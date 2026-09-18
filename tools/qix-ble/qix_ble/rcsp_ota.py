"""JieLi RCSP BLE OTA (service AE00, post-auth) — phone/master side.

Port of the JieLi SDK master-side OTA logic (rcsp_update_master.c) so we can drive
a *native RCSP* OTA against a JieLi watch/badge firmware (e.g. the e_badge_707_sdk_200
build that advertises "EC-BADGE"). This is distinct from the Qix FD00 path in
update_manager.py — RCSP runs over AE01(write)/AE02(notify) with the FE-DC-BA framing
(RcspFrame) and the E1-E8 opcodes.

Wire framing (verified in rcsp_frame.py / rcsp_session.py):
  outer:  [0xFE 0xDC 0xBA][flag][cmd][len BE16][body][0xEF]
  inner command  (flag 0xc0): body = [op_code_sn][payload]
  inner response (flag 0x00): body = [status][op_code_sn][payload]

OTA opcodes (rcsp_define.h 0xE1-0xE8) and the **pull** model (the DEVICE drives the
data transfer — it asks the phone for byte ranges of the .ufw, the phone answers):

  E1 GET_FILE_INFO_OFFSET   phone→dev (empty)  → dev resp [offset u32 BE][len u16 BE]
  E2 INQUIRE_CAN_UPDATE     phone→dev (mark)   → dev resp [can_update u8]
  E3 ENTER_UPDATE_MODE      phone→dev (empty)  → dev resp [status u8]
  E5 SEND_FW_UPDATE_BLOCK   dev→phone [off u32 BE][len u16 BE]  → phone resp [raw file bytes]
                            (off==0 && len==0 is the "transfer done, query status" signal)
  E6 GET_REFRESH_FW_STATUS  phone→dev (empty)  → dev resp [result u8]
  E7 SET_DEVICE_REBOOT      phone→dev [0x00]   → dev resp [status u8]
  E8 NOTIFY_CONTENT_SIZE    dev→phone [size u32 BE]  → phone resp (empty)

The "mark" data for E2 is read straight out of the .ufw at the [offset,len] the device
returns in E1 — we never need to know the UFW internals, the device tells us where to look.

Flow:
  probe():  E1 → E2 → stop (ZERO flash write — safe validation that the device accepts
            the file: auth OK, version/authkey/crc parsed, can_update reported).
  flash():  E1 → E2 → E3 → (device-driven E5/E8 loop, phone serves .ufw) → E6 → E7.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from qix_ble.errors import BadgeRejected, TimeoutError as QixTimeoutError
from qix_ble.rcsp_frame import RcspFrame
from qix_ble.rcsp_session import (
    _parse_inner,
    _build_response_body,
    FLAG_PHONE_REQ,     # 0xc0 — phone command, expects response
    FLAG_DEVICE_RESP,   # 0x00 — response flag (phone→dev reply too)
)

log = logging.getLogger("qix_ble.rcsp_ota")

# ── OTA opcodes (rcsp_define.h) ──────────────────────────────────────────────
OP_GET_FILE_INFO_OFFSET: int = 0xE1
OP_INQUIRE_CAN_UPDATE:   int = 0xE2
OP_ENTER_UPDATE_MODE:    int = 0xE3
OP_EXIT_UPDATE_MODE:     int = 0xE4
OP_SEND_FW_UPDATE_BLOCK: int = 0xE5
OP_GET_REFRESH_FW_STATUS: int = 0xE6
OP_SET_DEVICE_REBOOT:    int = 0xE7
OP_NOTIFY_CONTENT_SIZE:  int = 0xE8
# Not an OTA opcode, but load-bearing for the two-pass relink: JieLi's reference master
# (rcsp_update_master.c `_rcsp_switch_device_commucation_mode_to_ble`, and the SuperBand
# app's `NotifyCommunicationWayCmd`) sends this the moment E6 returns 0x80, on the STILL
# OPEN pass-1 link, and then does NOT disconnect — the device acks, drops the link itself
# and reboots into the loader ~1 s later.
OP_SWITCH_DEVICE:        int = 0x0B   # payload {way, reconnect}: 0x00 = BLE, 0x01 = reconnect-by-adv

# ── status code tables (for human-readable diagnostics) ──────────────────────
# Values are the firmware's UPDATE_FLAG_* enum (rcsp_update.c:89-98), NOT the old
# guessed mapping. 0x01 = LOW_POWER is the common one: the badge refuses OTA on a low
# battery (level <= low_battery_level, default 3, when not charging) to avoid a
# brown-out brick mid-apply — CHARGE THE BADGE and retry.
E2_CAN_UPDATE: dict[int, str] = {
    0x00: "OK — device accepts the update",
    0x01: "LOW BATTERY — badge refuses OTA below level 3 (charge it, then retry)",
    0x02: "firmware-info error (bad/incompatible .ufw header)",
    0x03: "same version (device already on this fw)",
    0x04: "TWS not connected",
    0x05: "TWS not in charge-store",
    0x06: "an update is already in progress",
    0x07: "multiple BT devices connected — not allowed",
}
E6_RESULT: dict[int, str] = {
    0x00: "success — update applied, ready to reboot",
    0x80: "loader downloaded (dual-bank: relink + retransfer needed)",
    0x01: "verify failed",
    0x02: "update failed",
    0x03: "key mismatch",
    0x04: "bad update file",
    0x05: "uboot mismatch",
    0x06: "length error",
    0x07: "flash read/write failed",
    0x08: "command timeout",
}


@dataclass(frozen=True)
class ProbeResult:
    can_update: int          # E2 status byte
    accepted: bool           # can_update == 0x00
    info_offset: int         # E1 offset into the .ufw where the mark/id block lives
    info_len: int            # E1 length of that block
    mark: bytes              # the bytes we sent in E2 (from the file)
    dt_ms: float


class RcspOtaUpdater:
    """Drives a native RCSP OTA over an already-authenticated transport.

    Pre-condition: AuthSession.do_handshake() must have run (the device gates OTA
    commands behind auth unless the firmware was built with BT_CONNECTION_VERIFY=1).
    """

    def __init__(self, transport):
        self.transport = transport
        self._seq: int = 0
        # Set from the 0x0B ack: 1 = the device will come back on a DIFFERENT address.
        self.relink_flag: int | None = None

    # ── inner framing helpers ────────────────────────────────────────────────
    def _next_seq(self) -> int:
        s = self._seq
        self._seq = (self._seq + 1) & 0xFF
        return s

    def _send_command(self, opcode: int, payload: bytes = b"") -> int:
        sn = self._next_seq()
        body = bytes([sn]) + payload
        log.debug("TX cmd 0x%02x sn=0x%02x len=%d", opcode, sn, len(payload))
        self.transport.send_rcsp_frame(
            RcspFrame(flag=FLAG_PHONE_REQ, cmd=opcode, payload=body)
        )
        return sn

    def _send_response(self, opcode: int, sn: int, status: int = 0x00,
                       payload: bytes = b"") -> None:
        body = _build_response_body(cmd=opcode, op_code_sn=sn, status=status,
                                    payload=payload)
        log.debug("TX resp 0x%02x sn=0x%02x status=0x%02x len=%d",
                  opcode, sn, status, len(payload))
        self.transport.send_rcsp_frame(
            RcspFrame(flag=FLAG_DEVICE_RESP, cmd=opcode, payload=body)
        )

    def _wait_response(self, opcode: int, sn: int, timeout: float):
        """Wait for the device's response to a phone-issued command (skips any
        interleaved device-initiated commands, which shouldn't happen pre-E3)."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise QixTimeoutError(
                    f"no RCSP response to cmd 0x{opcode:02x} sn=0x{sn:02x} in {timeout}s"
                )
            frame = self.transport.recv_rcsp_frame(timeout=remaining)
            inner = _parse_inner(frame)
            if not inner.is_command and frame.cmd == opcode:
                if inner.op_code_sn != sn:
                    log.warning("cmd 0x%02x: response sn=0x%02x != request sn=0x%02x "
                                "(continuing)", opcode, inner.op_code_sn, sn)
                return inner
            log.warning("waiting resp 0x%02x: skipping frame flag=0x%02x cmd=0x%02x",
                        opcode, frame.flag, frame.cmd)

    # ── high-level steps ─────────────────────────────────────────────────────
    def _e1_get_offset(self, timeout: float) -> tuple[int, int]:
        sn = self._send_command(OP_GET_FILE_INFO_OFFSET)
        inner = self._wait_response(OP_GET_FILE_INFO_OFFSET, sn, timeout)
        if inner.status not in (None, 0x00):
            raise BadgeRejected(f"E1 rejected: rcsp status=0x{inner.status:02x}")
        p = inner.payload
        if len(p) < 6:
            raise BadgeRejected(f"E1 response too short: {p.hex()}")
        offset = int.from_bytes(p[0:4], "big")
        length = int.from_bytes(p[4:6], "big")
        log.info("E1: file-info offset=0x%x len=%d", offset, length)
        return offset, length

    def _e2_inquire(self, mark: bytes, timeout: float) -> int:
        sn = self._send_command(OP_INQUIRE_CAN_UPDATE, mark)
        inner = self._wait_response(OP_INQUIRE_CAN_UPDATE, sn, timeout)
        if inner.status not in (None, 0x00):
            raise BadgeRejected(f"E2 rejected: rcsp status=0x{inner.status:02x}")
        can_update = inner.payload[0] if inner.payload else 0xFF
        log.info("E2: can_update=0x%02x (%s)", can_update,
                 E2_CAN_UPDATE.get(can_update, "unknown"))
        return can_update

    def _read_mark(self, ufw: bytes, offset: int, length: int) -> bytes:
        """The mark/id block the phone echoes in E2. When the device reports
        offset==0 && len==0 (this firmware disables the file-info block, see
        rcsp_update.c DEV_UPDATE_FILE_INFO_OFFEST/LEN = 0), the reference master
        sends a single 0x00 byte instead of nothing — the device's E2 handler
        needs len>0 to reply. Otherwise read [offset:offset+len] from the .ufw."""
        if offset == 0 and length == 0:
            return b"\x00"
        if offset + length > len(ufw):
            raise BadgeRejected(
                f"E1 wants file[{offset}:{offset + length}] but .ufw is only "
                f"{len(ufw)} bytes — wrong update file?"
            )
        return ufw[offset:offset + length]

    def _e3_enter(self, timeout: float) -> int:
        sn = self._send_command(OP_ENTER_UPDATE_MODE)
        inner = self._wait_response(OP_ENTER_UPDATE_MODE, sn, timeout)
        status = inner.payload[0] if inner.payload else 0xFF
        log.info("E3: enter-update status=0x%02x", status)
        return status

    def _e6_query_status(self, timeout: float) -> int:
        sn = self._send_command(OP_GET_REFRESH_FW_STATUS)
        inner = self._wait_response(OP_GET_REFRESH_FW_STATUS, sn, timeout)
        result = inner.payload[0] if inner.payload else 0xFF
        log.info("E6: refresh-status=0x%02x (%s)", result,
                 E6_RESULT.get(result, "unknown"))
        return result

    def _e7_reboot(self, timeout: float) -> None:
        sn = self._send_command(OP_SET_DEVICE_REBOOT, b"\x00")
        try:
            self._wait_response(OP_SET_DEVICE_REBOOT, sn, timeout)
        except QixTimeoutError:
            # The device often just reboots without acking — that's fine.
            log.info("E7: no reboot ack (device likely rebooted) — OK")

    # ── public API ───────────────────────────────────────────────────────────
    def probe(self, ufw: bytes, timeout: float = 8.0) -> ProbeResult:
        """E1 + E2 only. ZERO flash write. Validates that the device accepts the
        file (auth passed, the mark block parsed, version/authkey/crc checked)."""
        t0 = time.monotonic()
        offset, length = self._e1_get_offset(timeout)
        mark = self._read_mark(ufw, offset, length)
        can_update = self._e2_inquire(mark, timeout)
        return ProbeResult(
            can_update=can_update,
            accepted=(can_update == 0x00),
            info_offset=offset,
            info_len=length,
            mark=mark,
            dt_ms=(time.monotonic() - t0) * 1000.0,
        )

    def flash(self, ufw: bytes, on_progress=None, timeout: float = 8.0,
              loop_timeout: float = 15.0) -> int:
        """Full OTA: E1 → E2 → E3 → device-driven E5/E8 loop → E6 → E7.

        Returns the final E6 result code (0x00 = success). Raises BadgeRejected on
        a non-recoverable error. The transfer is DEVICE-DRIVEN: after E3 we become a
        passive responder, answering each E5 pull with the requested .ufw bytes.
        """
        offset, length = self._e1_get_offset(timeout)
        mark = self._read_mark(ufw, offset, length)
        can_update = self._e2_inquire(mark, timeout)
        if can_update != 0x00:
            raise BadgeRejected(
                f"device won't update: E2=0x{can_update:02x} "
                f"({E2_CAN_UPDATE.get(can_update, 'unknown')})"
            )

        # E3: tell the device to enter update mode. NOTE: this firmware does NOT
        # send a clean E3 response first — it immediately starts pulling data (E5)
        # and may push other notifications (e.g. 0xD1). So send E3 and go straight
        # into the device-driven loop, handling the E3 ack if/when it shows up.
        self._send_command(OP_ENTER_UPDATE_MODE)
        log.info("E3 sent — entering device-driven transfer loop (%d bytes to serve)", len(ufw))
        return self._serve_loop(ufw, on_progress, timeout, loop_timeout)

    def _serve_loop(self, ufw: bytes, on_progress, timeout: float,
                    loop_timeout: float) -> int:
        """Answer the device's E5 pulls until it signals done, then query E6.

        Shared by pass 1 (after E3) and pass 2 (after the relink), which is the whole
        point of splitting it out — pass 2 must NOT re-send E1/E2."""
        total = len(ufw)
        high_water = 0

        while True:
            try:
                frame = self.transport.recv_rcsp_frame(timeout=loop_timeout)
            except QixTimeoutError:
                # No frame for a while — the device may be done; poll status once.
                log.info("no frame in %ss — polling E6 status", loop_timeout)
                result = self._e6_query_status(timeout)
                return self._finish(result, timeout)
            inner = _parse_inner(frame)

            if inner.is_command and frame.cmd == OP_SEND_FW_UPDATE_BLOCK:
                off = int.from_bytes(inner.payload[0:4], "big") if len(inner.payload) >= 4 else 0
                ln = int.from_bytes(inner.payload[4:6], "big") if len(inner.payload) >= 6 else 0
                if off == 0 and ln == 0:
                    # "transfer done — query status" signal.
                    log.info("E5 [0,0] done-signal → ack + query E6")
                    self._send_response(OP_SEND_FW_UPDATE_BLOCK, inner.op_code_sn, 0x00, b"")
                    result = self._e6_query_status(timeout)
                    return self._finish(result, timeout)
                chunk = ufw[off:off + ln]
                self._send_response(OP_SEND_FW_UPDATE_BLOCK, inner.op_code_sn, 0x00, chunk)
                end = off + len(chunk)
                if end > high_water:
                    high_water = end
                    if on_progress and total:
                        on_progress(min(1.0, high_water / total))

            elif inner.is_command and frame.cmd == OP_NOTIFY_CONTENT_SIZE:
                size = int.from_bytes(inner.payload[0:4], "big") if len(inner.payload) >= 4 else 0
                log.info("E8 notify-size=%d", size)
                self._send_response(OP_NOTIFY_CONTENT_SIZE, inner.op_code_sn, 0x00, b"")

            elif not inner.is_command and frame.cmd == OP_ENTER_UPDATE_MODE:
                st = inner.payload[0] if inner.payload else 0xFF
                log.info("E3 ack: status=0x%02x", st)

            elif not inner.is_command and frame.cmd == OP_GET_REFRESH_FW_STATUS:
                # Device pushed a status unsolicited.
                result = inner.payload[0] if inner.payload else 0xFF
                log.info("E6 pushed: 0x%02x (%s)", result,
                         E6_RESULT.get(result, "unknown"))
                return self._finish(result, timeout)

            else:
                # Other device pushes (e.g. 0xD1 notifications) — ignore, keep serving.
                log.debug("transfer loop: ignoring frame flag=0x%02x cmd=0x%02x payload=%s",
                          frame.flag, frame.cmd, inner.payload.hex())

    # ── two-pass relink (pass 1 -> loader -> pass 2) ─────────────────────────
    def switch_communication_way(self, timeout: float = 6.0) -> int | None:
        """0x0B {0x00, 0x01} on the still-open pass-1 link. Returns the ack byte.

        The ack matters: JieLi's SDK reads it as `setReconnectUseADV(flag == 1)` — the
        device telling us whether it will come back under a DIFFERENT BLE address (to be
        matched via a `JLOTA` manufacturer advert) or the same one."""
        sn = self._send_command(OP_SWITCH_DEVICE, bytes([0x00, 0x01]))
        try:
            inner = self._wait_response(OP_SWITCH_DEVICE, sn, timeout)
        except QixTimeoutError:
            log.warning("0x0B: no ack in %.0fs — continuing anyway", timeout)
            return None
        flag = inner.payload[0] if inner.payload else None
        log.info("0x0B switch-communication-way ack: flag=%s%s", flag,
                 " (device will return on a DIFFERENT address)" if flag == 1 else "")
        return flag

    def wait_for_peripheral_disconnect(self, timeout: float = 20.0) -> bool:
        """Wait for the DEVICE to drop the link. Do NOT disconnect ourselves.

        The reference master polls at 1 s logging "Waiting for ble disconnect...". Our
        old code disconnected here, which skipped the device-side sequencing."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            client = getattr(self.transport, "_client", None)
            if client is None or not getattr(client, "is_connected", False):
                log.info("peripheral dropped the link — it is rebooting into the loader")
                return True
            time.sleep(1.0)
            log.debug("waiting for ble disconnect...")
        log.warning("peripheral still connected after %.0fs", timeout)
        return False

    def looks_like_loader(self, timeout: float = 6.0) -> bool:
        """0x03 GetTargetInfo, then check attr 9 byte0 (mandatory_upgrade_flag).

        This is the SDK's own pass-2 gate: `TargetInfoResponse.isMandatoryUpgrade()`.
        On the running app it is 0; on the loader it is 1."""
        sn = self._send_command(0x03, bytes.fromhex("ffffffff") + b"\x00")
        inner = self._wait_response(0x03, sn, timeout)
        tlv = inner.payload
        i = 0
        while i + 2 <= len(tlv):
            ln = tlv[i]
            if ln == 0:
                break
            ty = tlv[i + 1]
            data = tlv[i + 2:i + 1 + ln]
            if ty == 9:
                flag = data[0] if data else 0
                log.info("attr 9 mandatory_upgrade_flag = %d (%s)", flag,
                         "LOADER" if flag == 1 else "normal app")
                return flag == 1
            i += 1 + ln
        log.warning("attr 9 not present in target info — cannot confirm loader")
        return False

    def flash_pass2(self, ufw: bytes, on_progress=None, timeout: float = 8.0,
                    loop_timeout: float = 15.0) -> int:
        """Pass 2 against the loader: E3 only, then the device-driven loop.

        E1/E2 are deliberately NOT re-sent — the reference master skips them once its
        `isRelink` latch is set, and re-running them against the loader is the most
        likely source of the E6=0x04/0x05 rejections we saw."""
        self._send_command(OP_ENTER_UPDATE_MODE)
        log.info("pass 2: E3 sent (E1/E2 skipped) — serving %d bytes", len(ufw))
        return self._serve_loop(ufw, on_progress, timeout, loop_timeout)

    def _finish(self, result: int, timeout: float) -> int:
        desc = E6_RESULT.get(result, "unknown")
        if result == 0x00:
            log.info("OTA success — sending reboot (E7)")
            self._e7_reboot(timeout)
            return result
        if result == 0x80:
            # Pass 1 done: the loader is staged. What follows is the relink, and it is
            # NOT "disconnect and reconnect" — that is what this client used to do, and
            # it is why an OTA left the unit half-updated and needing a wired restore.
            #
            # The reference master (rcsp_update_master.c) and the SuperBand app both do:
            #   1. send 0x0B {0x00, 0x01} on the STILL-OPEN link
            #   2. do NOT disconnect — poll until the DEVICE drops the link
            #   3. the device then waits ~1 s and reboots into the loader
            # Step 1 is also what populates UPDATA_PARM (exif pointer, BTIF MAC backup,
            # reserved-area opt-out) for the loader to pick up.
            log.info("E6=0x80: loader staged — sending 0x0B and waiting for the device "
                     "to drop the link (NOT disconnecting ourselves)")
            self.relink_flag = self.switch_communication_way(timeout=6.0)
            self.wait_for_peripheral_disconnect(timeout=20.0)
            return result
        raise BadgeRejected(f"OTA failed: E6=0x{result:02x} ({desc})")
