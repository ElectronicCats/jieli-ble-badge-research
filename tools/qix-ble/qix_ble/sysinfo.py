"""RCSP target info / sys info queries (post-auth, service AE00).

Port de:
- web-bluetooth-e87/web/src/lib/e87-protocol.ts:
  * getTargetInfoE87 (cmd 0x03) — protocol/firmware/MAC/vid_pid/etc.
  * getSysInfoE87 (cmd 0x07) — battery/volume/mode/light/eq/etc.
  * parseTargetInfoAttrs / parseSysInfoAttrs (TLV decoder)
  * TARGET_INFO_ATTR_NAMES / PUBLIC_SYSINFO_ATTR_NAMES

Wire-format del request (ambos cmds):
  body = [seq 1B][param_data...]
  - cmd 0x03 param = [mask BE32][platform 1B]   (5 bytes)
  - cmd 0x07 param = [function 1B][mask BE32]   (5 bytes)

Wire-format del response (ambos cmds):
  body = [status 1B][seq_echo 1B][payload...]
  - cmd 0x03 payload = TLV directly
  - cmd 0x07 payload = [function 1B][TLV]

TLV layout (same para target + sys):
  [attrLen 1B][type 1B][data attrLen-1 bytes]  repeated.

Status 0x00 = success; cualquier otro = badge rejected.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from qix_ble.errors import BadgeRejected
from qix_ble.rcsp_frame import RcspFrame

log = logging.getLogger("qix_ble.sysinfo")

CMD_GET_TARGET_INFO: int = 0x03
CMD_GET_SYS_INFO: int = 0x07

FLAG_PHONE_REQ: int = 0xc0  # is_command + has_response

# ── Attribute name tables (port byte-exact del TS) ───────────────────────────
TARGET_INFO_ATTR_NAMES: dict[int, str] = {
    0: "protocol_version",
    1: "power_up_sys_info",
    2: "edr_addr",
    3: "platform",
    4: "function_info",
    5: "firmware_info",
    6: "sdk_type",
    7: "uboot_version",
    8: "support_double_backup",
    9: "mandatory_upgrade_flag",
    10: "vid_pid",
    11: "auth_key",
    12: "project_code",
    13: "fw_version",  # per community PROTOCOL.md tabla L191: "uboot/fw 版本號"
                       # NOTA: web-bluetooth-e87 lo decoda como "protocol_mtu" (interpreta
                       # bytes 0-1 como BE16 = MTU). Empíricamente en FW E87 OEM 2026
                       # bytes son `01 10 02 1c` → fw version 1.16.2.28 (interpretation
                       # community PROTOCOL.md). Ambiguo entre community projects.
    14: "allow_connect",
    16: "name",
    17: "connect_ble_only",
    18: "peripherals_support",
    19: "dev_support_func",
    20: "recode_file_transfer",
    21: "file_transfer",
    31: "custom_ver",
}

PUBLIC_SYSINFO_ATTR_NAMES: dict[int, str] = {
    0: "battery",
    1: "volume",
    2: "music_dev_status",
    3: "err",
    4: "eq",
    5: "file_type",
    6: "cur_mode_type",
    7: "light",
    8: "fm_tx",
    9: "emitter_mode",
    10: "emitter_connect_status",
    11: "high_bass",
    12: "eq_preset_value",
    13: "current_noise_mode",
    14: "all_noise_mode",
    15: "phone_status",
    16: "fixed_len_data_fun",
    17: "sound_card_eq_freq",
    18: "sound_card_eq_gain",
    19: "sound_card",
}


@dataclass(frozen=True)
class Attr:
    """TLV attribute. `decoded` es human-readable best-effort."""
    type: int
    name: str
    data: bytes
    decoded: Optional[str]


@dataclass(frozen=True)
class TargetInfoResult:
    request_mask: int
    request_platform: int
    raw: bytes
    attrs: list[Attr]


@dataclass(frozen=True)
class SysInfoResult:
    function: int
    raw: bytes
    attrs: list[Attr]


# ── TLV parsing (shared para target + sys) ───────────────────────────────────

def _try_ascii(data: bytes) -> Optional[str]:
    if not data:
        return None
    if any(b < 0x20 or b > 0x7e for b in data):
        return None
    return data.decode("ascii")


def _decode_attr_value(type_: int, data: bytes) -> Optional[str]:
    """Generic decoder usado por sysinfo + targetInfo fallback."""
    if not data:
        return None
    if type_ == 0 and len(data) == 1:
        return f"{data[0]}%"
    ascii_val = _try_ascii(data)
    if ascii_val:
        return ascii_val
    if len(data) == 1:
        return str(data[0])
    if len(data) == 2:
        return str(int.from_bytes(data, "big"))
    if len(data) == 4:
        return str(int.from_bytes(data, "big"))
    return None


def _decode_target_info_attr(type_: int, data: bytes) -> Optional[str]:
    """Specialized decoder para target_info attrs. Falls back a _decode_attr_value."""
    if not data:
        return None
    if type_ == 2 and len(data) == 6:  # edr_addr (MAC)
        return ":".join(f"{b:02x}" for b in data)
    if type_ == 10 and len(data) >= 4:  # vid_pid
        vid = int.from_bytes(data[0:2], "big")
        pid = int.from_bytes(data[2:4], "big")
        return f"vid=0x{vid:04x}, pid=0x{pid:04x}"
    if type_ == 13 and len(data) >= 4:  # fw_version (4B) o protocol_mtu (2B) — ambiguo
        # Mostrar AMBAS interpretaciones para no perder info
        mtu = int.from_bytes(data[0:2], "big")
        fw = ".".join(str(b) for b in data[0:4])
        return f"fw={fw} (mtu_alt={mtu})"
    if type_ in (11, 12, 16, 31):  # auth_key, project_code, name, custom_ver — ascii
        ascii_val = _try_ascii(data)
        if ascii_val:
            return ascii_val
    return _decode_attr_value(type_, data)


def _parse_tlv(payload: bytes, attr_names: dict[int, str],
               decoder=None) -> list[Attr]:
    """Parse [attrLen 1B][type 1B][data attrLen-1B] repetido.

    attrLen counts: type byte + data bytes (NOT including itself).
    """
    out: list[Attr] = []
    i = 0
    decoder = decoder or _decode_attr_value
    while i < len(payload):
        attr_len = payload[i]
        if attr_len < 1:
            break
        type_idx = i + 1
        data_start = i + 2
        data_len = attr_len - 1
        data_end = data_start + data_len
        if data_end > len(payload):
            break
        type_ = payload[type_idx]
        data = bytes(payload[data_start:data_end])
        out.append(Attr(
            type=type_,
            name=attr_names.get(type_, f"attr_{type_}"),
            data=data,
            decoded=decoder(type_, data),
        ))
        i = data_end
    return out


# ── Request senders ──────────────────────────────────────────────────────────

def get_target_info(rcsp_session, mask: int = 0xFFFFFFFF, platform: int = 0,
                    timeout: float = 10.0) -> TargetInfoResult:
    """RCSP cmd 0x03 — TLV con protocol/firmware/MAC/vid_pid/etc.

    Requiere session post-auth. `rcsp_session` debe exponer `.transport`,
    `._check_auth()`, `._next_seq()`.
    """
    rcsp_session._check_auth()
    seq = rcsp_session._next_seq()
    param = (
        bytes([seq])
        + (mask & 0xFFFFFFFF).to_bytes(4, "big")
        + bytes([platform & 0xFF])
    )
    frame = RcspFrame(flag=FLAG_PHONE_REQ, cmd=CMD_GET_TARGET_INFO, payload=param)
    log.debug("get_target_info: TX seq=0x%02x mask=0x%08x platform=%d", seq, mask, platform)
    rcsp_session.transport.send_rcsp_frame(frame)

    resp = rcsp_session.transport.recv_rcsp_frame(timeout=timeout)
    if resp.cmd != CMD_GET_TARGET_INFO:
        raise BadgeRejected(
            f"target_info: unexpected cmd 0x{resp.cmd:02x} (expected 0x03)",
            cmd=CMD_GET_TARGET_INFO,
        )
    if len(resp.payload) < 2:
        raise BadgeRejected(
            f"target_info: response payload corto ({len(resp.payload)}B)",
            cmd=CMD_GET_TARGET_INFO,
        )
    status = resp.payload[0]
    if status != 0x00:
        raise BadgeRejected(
            f"target_info: status=0x{status:02x}",
            cmd=CMD_GET_TARGET_INFO, state=status,
        )
    tlv_data = bytes(resp.payload[2:])
    attrs = _parse_tlv(tlv_data, TARGET_INFO_ATTR_NAMES, _decode_target_info_attr)
    log.info("get_target_info: %d attrs parsed", len(attrs))
    return TargetInfoResult(
        request_mask=mask & 0xFFFFFFFF,
        request_platform=platform & 0xFF,
        raw=tlv_data,
        attrs=attrs,
    )


def get_sys_info(rcsp_session, function: int = 0xFF, mask: int = 0xFFFFFFFF,
                 timeout: float = 10.0) -> SysInfoResult:
    """RCSP cmd 0x07 — TLV con battery/volume/mode/light/etc.

    Requiere session post-auth.
    """
    rcsp_session._check_auth()
    seq = rcsp_session._next_seq()
    param = (
        bytes([seq, function & 0xFF])
        + (mask & 0xFFFFFFFF).to_bytes(4, "big")
    )
    frame = RcspFrame(flag=FLAG_PHONE_REQ, cmd=CMD_GET_SYS_INFO, payload=param)
    log.debug("get_sys_info: TX seq=0x%02x function=0x%02x mask=0x%08x",
              seq, function, mask)
    rcsp_session.transport.send_rcsp_frame(frame)

    resp = rcsp_session.transport.recv_rcsp_frame(timeout=timeout)
    if resp.cmd != CMD_GET_SYS_INFO:
        raise BadgeRejected(
            f"sys_info: unexpected cmd 0x{resp.cmd:02x} (expected 0x07)",
            cmd=CMD_GET_SYS_INFO,
        )
    if len(resp.payload) < 2:
        raise BadgeRejected(
            f"sys_info: response payload corto ({len(resp.payload)}B)",
            cmd=CMD_GET_SYS_INFO,
        )
    status = resp.payload[0]
    if status != 0x00:
        raise BadgeRejected(
            f"sys_info: status=0x{status:02x}",
            cmd=CMD_GET_SYS_INFO, state=status,
        )
    body = bytes(resp.payload[2:])
    if not body:
        return SysInfoResult(function=function & 0xFF, raw=b"", attrs=[])
    function_id = body[0]
    tlv_data = body[1:]
    attrs = _parse_tlv(tlv_data, PUBLIC_SYSINFO_ATTR_NAMES, _decode_attr_value)
    log.info("get_sys_info: function=0x%02x %d attrs parsed", function_id, len(attrs))
    return SysInfoResult(function=function_id, raw=body, attrs=attrs)
