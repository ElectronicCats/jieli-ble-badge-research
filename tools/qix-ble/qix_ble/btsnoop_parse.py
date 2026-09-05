"""Parser de btsnoop_hci.log Android → frames Qix decoded.

BTSnoop file format (NetSnoop variant, Android usa esto):
  Header: 16B
    [0:8]   magic "btsnoop\0"
    [8:12]  version (BE32) = 1
    [12:16] datalink type (BE32) = 1002 (HCI UART H4)
  Records (variable):
    [0:4]   original length (BE32)
    [4:8]   included length (BE32)
    [8:12]  packet flags (BE32)
    [12:16] cumulative drops (BE32)
    [16:24] timestamp microseconds since 0 AD (BE64)
    [24:]   HCI packet (included_length bytes)

Packet flags:
  bit 0: 1 = received (controller→host), 0 = sent (host→controller)
  bit 1: 1 = command/event, 0 = ACL data

HCI ACL packet (post-flags):
  [0]     HCI packet type (0x02 = ACL)
  [1:3]   ACL handle + flags (LE16)
  [3:5]   ACL data length (LE16)
  [5:]    L2CAP PDU
    L2CAP:
      [0:2] length (LE16)
      [2:4] CID (LE16)
      [4:]  L2CAP payload (= ATT for CID 0x0004)

ATT operations relevantes para Qix OTA:
  0x12 Write Request  (writes a CHAR_FD02_WRITE)
  0x52 Write Command  (writes a CHAR_FD02_WRITE, no response)
  0x1B Handle Value Notification (from CHAR_FD01_NOTIFY / CHAR_FD03_CTRL)
  0x1D Handle Value Indication

El parser:
  1. Walk through records
  2. Decode HCI ACL → L2CAP → ATT
  3. Reassemble fragmented PDUs (ACL flag PB bits)
  4. For writes y notifications: capture the value bytes
  5. Try to decode como QixFrame (magic 0x9E) y filter por opcodes OTA

Uso CLI:
  python -m qix_ble.btsnoop_parse <btsnoop_hci.log>  # human-readable summary
  python -m qix_ble.btsnoop_parse <log> --json > flow.json
  python -m qix_ble.btsnoop_parse <log> --ota-only   # solo frames 0xC0..0xC5
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

from qix_ble.frame import QixFrame, QIX_MAGIC


BTSNOOP_MAGIC = b"btsnoop\x00"
ATT_CID = 0x0004
HCI_ACL_TYPE = 0x02

ATT_WRITE_REQ = 0x12
ATT_WRITE_CMD = 0x52
ATT_WRITE_RSP = 0x13
ATT_NOTIFICATION = 0x1B
ATT_INDICATION = 0x1D
ATT_HANDLE_VALUE_CONFIRMATION = 0x1E

ATT_OP_NAMES = {
    0x01: "ERROR_RSP",
    0x02: "EXCHANGE_MTU_REQ",
    0x03: "EXCHANGE_MTU_RSP",
    0x04: "FIND_INFO_REQ",
    0x05: "FIND_INFO_RSP",
    0x08: "READ_BY_TYPE_REQ",
    0x09: "READ_BY_TYPE_RSP",
    0x0A: "READ_REQ",
    0x0B: "READ_RSP",
    0x10: "READ_BY_GROUP_TYPE_REQ",
    0x11: "READ_BY_GROUP_TYPE_RSP",
    0x12: "WRITE_REQ",
    0x13: "WRITE_RSP",
    0x16: "PREPARE_WRITE_REQ",
    0x17: "PREPARE_WRITE_RSP",
    0x18: "EXECUTE_WRITE_REQ",
    0x19: "EXECUTE_WRITE_RSP",
    0x1B: "NOTIFICATION",
    0x1D: "INDICATION",
    0x1E: "CONFIRMATION",
    0x52: "WRITE_CMD",
}

OTA_OPCODES = {
    0xC0: "REQ_UPDATE",
    0xC1: "RET_UPDATE",
    0xC2: "SEND_UPDATE_DATA",
    0xC3: "RET_UPDATE_DATA",
    0xC4: "REQ_UPDATE_CON",
    0xC5: "RET_UPDATE_RESULT",
}


@dataclass
class AttPdu:
    ts_us: int
    direction: str  # "tx" (host→ctrl, app→badge) or "rx" (ctrl→host, badge→app)
    handle: int
    op: int
    value: bytes
    op_name: str = ""
    qix: dict | None = None


@dataclass
class FragReassembler:
    """Por (handle, direction) reassembling L2CAP from fragmented ACL packets."""

    buffer: bytearray = field(default_factory=bytearray)
    expected_l2cap_len: int = 0  # full L2CAP PDU length including 4B header

    def feed(self, pb: int, payload: bytes) -> bytes | None:
        """pb = packet boundary flag (00=continuation, 01=start-non-AC, 10=start-A, 11=control).
        Returns full L2CAP PDU if complete, else None."""
        if pb in (0b10, 0b00):  # start (10 = start auto-flushable BLE)
            # start of a new L2CAP PDU
            self.buffer = bytearray(payload)
            if len(self.buffer) >= 4:
                l2cap_payload_len = int.from_bytes(self.buffer[0:2], "little")
                self.expected_l2cap_len = 4 + l2cap_payload_len
            else:
                self.expected_l2cap_len = 0
        elif pb == 0b01:  # continuation
            self.buffer.extend(payload)
        else:
            return None

        if self.expected_l2cap_len and len(self.buffer) >= self.expected_l2cap_len:
            full = bytes(self.buffer[: self.expected_l2cap_len])
            self.buffer = self.buffer[self.expected_l2cap_len :]
            self.expected_l2cap_len = 0
            return full
        return None


def parse_btsnoop(path: Path):
    """Generator de AttPdu desde btsnoop file."""
    data = path.read_bytes()
    if data[:8] != BTSNOOP_MAGIC:
        raise ValueError(f"magic incorrecto: {data[:8]!r}")
    version = int.from_bytes(data[8:12], "big")
    datalink = int.from_bytes(data[12:16], "big")
    if version != 1 or datalink not in (1001, 1002):
        sys.stderr.write(
            f"warn: version={version} datalink={datalink} (esperaba 1,1002)\n"
        )

    pos = 16
    reassemblers: dict[tuple[int, str], FragReassembler] = {}

    while pos < len(data):
        if pos + 24 > len(data):
            break
        orig_len = int.from_bytes(data[pos : pos + 4], "big")
        incl_len = int.from_bytes(data[pos + 4 : pos + 8], "big")
        flags = int.from_bytes(data[pos + 8 : pos + 12], "big")
        # drops = int.from_bytes(data[pos+12:pos+16], "big")
        ts_us = int.from_bytes(data[pos + 16 : pos + 24], "big")
        pkt = data[pos + 24 : pos + 24 + incl_len]
        pos += 24 + incl_len

        if incl_len == 0:
            continue

        # Direction (bit 0 of flags)
        direction = "rx" if (flags & 1) else "tx"
        is_data = (flags & 2) == 0  # bit 1: 0 = ACL data

        if not is_data:
            continue  # commands/events out of scope

        if pkt[0] != HCI_ACL_TYPE:
            continue

        if len(pkt) < 5:
            continue
        handle_word = int.from_bytes(pkt[1:3], "little")
        handle = handle_word & 0x0FFF
        pb = (handle_word >> 12) & 0b11
        bc = (handle_word >> 14) & 0b11  # noqa
        acl_len = int.from_bytes(pkt[3:5], "little")
        acl_payload = pkt[5 : 5 + acl_len]

        # Reassembler per (handle, direction)
        key = (handle, direction)
        if key not in reassemblers:
            reassemblers[key] = FragReassembler()
        full_l2cap = reassemblers[key].feed(pb, acl_payload)
        if full_l2cap is None:
            continue

        if len(full_l2cap) < 4:
            continue
        cid = int.from_bytes(full_l2cap[2:4], "little")
        if cid != ATT_CID:
            continue

        att = full_l2cap[4:]
        if not att:
            continue
        op = att[0]
        op_name = ATT_OP_NAMES.get(op, f"OP_0x{op:02x}")

        pdu = AttPdu(
            ts_us=ts_us, direction=direction, handle=handle,
            op=op, value=b"", op_name=op_name,
        )

        if op == ATT_WRITE_REQ or op == ATT_WRITE_CMD:
            if len(att) >= 3:
                pdu.handle = int.from_bytes(att[1:3], "little")
                pdu.value = att[3:]
        elif op == ATT_NOTIFICATION or op == ATT_INDICATION:
            if len(att) >= 3:
                pdu.handle = int.from_bytes(att[1:3], "little")
                pdu.value = att[3:]
        else:
            pdu.value = att[1:]

        # Try Qix decode
        if pdu.value and pdu.value[0] == QIX_MAGIC:
            try:
                qf = QixFrame.decode(pdu.value)
                pdu.qix = {
                    "flags": qf.flags,
                    "cmd": qf.cmd,
                    "cmd_hex": f"0x{qf.cmd:02x}",
                    "cmd_name": OTA_OPCODES.get(qf.cmd, ""),
                    "payload_hex": qf.payload.hex(),
                    "payload_len": len(qf.payload),
                }
            except Exception as e:
                pdu.qix = {"decode_error": str(e), "raw_hex": pdu.value.hex()}

        yield pdu


def format_pdu(pdu: AttPdu) -> str:
    arrow = "TX→" if pdu.direction == "tx" else "←RX"
    ts_s = pdu.ts_us / 1_000_000
    line = f"[{ts_s:13.6f}] {arrow} handle=0x{pdu.handle:04x} {pdu.op_name:<20s}"
    if pdu.qix:
        if "cmd_name" in pdu.qix:
            line += f"  Qix cmd=0x{pdu.qix['cmd']:02x} ({pdu.qix['cmd_name']:<20s}) flags=0x{pdu.qix['flags']:02x} len={pdu.qix['payload_len']:>4d}"
            if pdu.qix["payload_len"] <= 32:
                line += f" payload={pdu.qix['payload_hex']}"
        else:
            line += f"  Qix-decode-fail raw={pdu.qix.get('raw_hex', '')[:80]}"
    elif pdu.value:
        line += f"  value({len(pdu.value)})={pdu.value.hex()[:80]}"
    return line


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", type=Path, help="btsnoop_hci.log path")
    ap.add_argument("--json", action="store_true", help="dump como JSON lines")
    ap.add_argument(
        "--ota-only",
        action="store_true",
        help="solo PDUs con cmd 0xC0..0xC5",
    )
    ap.add_argument(
        "--qix-only",
        action="store_true",
        help="solo PDUs cuyo value es un frame Qix válido",
    )
    args = ap.parse_args()

    count_total = 0
    count_qix = 0
    count_ota = 0
    by_cmd: dict[int, int] = {}

    for pdu in parse_btsnoop(args.path):
        count_total += 1
        if pdu.qix and "cmd" in pdu.qix:
            count_qix += 1
            cmd = pdu.qix["cmd"]
            by_cmd[cmd] = by_cmd.get(cmd, 0) + 1
            if cmd in OTA_OPCODES:
                count_ota += 1

        if args.qix_only and not (pdu.qix and "cmd" in pdu.qix):
            continue
        if args.ota_only and not (
            pdu.qix and "cmd" in pdu.qix and pdu.qix["cmd"] in OTA_OPCODES
        ):
            continue

        if args.json:
            d = asdict(pdu)
            d["value_hex"] = pdu.value.hex()
            d.pop("value", None)
            print(json.dumps(d))
        else:
            print(format_pdu(pdu))

    sys.stderr.write(
        f"\n=== summary === total_pdus={count_total} qix_frames={count_qix} ota_frames={count_ota}\n"
    )
    for cmd, n in sorted(by_cmd.items()):
        name = OTA_OPCODES.get(cmd, ATT_OP_NAMES.get(cmd, ""))
        sys.stderr.write(f"  cmd 0x{cmd:02x} ({name:<20s}): {n}\n")


if __name__ == "__main__":
    main()
