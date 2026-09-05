#!/usr/bin/env python3
"""Parse ATT (Attribute Protocol) packets from a btsnoop or pcap capture.

Use case: extract phone↔badge GATT writes/notifications from Android
btsnoop_hci.log captures, to RE BLE protocols.

Pre-req: btsnoop must be converted to pcap with `editcap input.btsnoop output.pcap`
(editcap ships with wireshark-common on Debian/Ubuntu).
"""

import argparse
import sys
from pathlib import Path

try:
    from scapy.all import rdpcap
    from scapy.layers.bluetooth import L2CAP_Hdr
except ImportError:
    sys.exit("scapy not installed — pip install scapy")

ATT_OPS = {
    0x01: "ERR_RESP",
    0x02: "MTU_REQ",
    0x03: "MTU_RESP",
    0x04: "FIND_INFO_REQ",
    0x05: "FIND_INFO_RESP",
    0x06: "FIND_BY_TYPE_REQ",
    0x07: "FIND_BY_TYPE_RESP",
    0x08: "READ_BY_TYPE_REQ",
    0x09: "READ_BY_TYPE_RESP",
    0x0A: "READ_REQ",
    0x0B: "READ_RESP",
    0x0C: "READ_BLOB_REQ",
    0x0D: "READ_BLOB_RESP",
    0x10: "READ_BY_GROUP_REQ",
    0x11: "READ_BY_GROUP_RESP",
    0x12: "WRITE_REQ",
    0x13: "WRITE_RESP",
    0x16: "PREPARE_WRITE_REQ",
    0x17: "PREPARE_WRITE_RESP",
    0x18: "EXECUTE_WRITE_REQ",
    0x19: "EXECUTE_WRITE_RESP",
    0x1B: "HANDLE_NOTIF",
    0x1D: "HANDLE_INDIC",
    0x1E: "HANDLE_CONFIRM",
    0x52: "WRITE_CMD",
    0xD2: "SIGNED_WRITE_CMD",
}

# ATT ops we want to highlight (data exchange between phone and device)
DATA_OPS = {0x12, 0x13, 0x52, 0x1B, 0x1D}


def parse(pcap_path: Path, only_data: bool = False, hide_discovery: bool = False) -> int:
    pkts = rdpcap(str(pcap_path))
    n_total = len(pkts)
    n_att = 0
    n_shown = 0
    for i, pkt in enumerate(pkts):
        if not pkt.haslayer(L2CAP_Hdr):
            continue
        l2 = pkt[L2CAP_Hdr]
        if l2.cid != 4:
            continue
        n_att += 1
        b = bytes(l2.payload)
        if not b:
            continue
        op = b[0]
        op_name = ATT_OPS.get(op, f"0x{op:02X}?")
        if only_data and op not in DATA_OPS:
            continue
        if hide_discovery and op_name in (
            "READ_BY_GROUP_REQ", "READ_BY_GROUP_RESP",
            "READ_BY_TYPE_REQ", "READ_BY_TYPE_RESP",
            "FIND_INFO_REQ", "FIND_INFO_RESP",
            "ERR_RESP",
            "MTU_REQ", "MTU_RESP",
        ):
            continue
        ts = float(pkt.time)
        rest = b[1:]
        print(f"[{i:4d}] t={ts:.3f}  ATT {op_name:18s}  {len(b):3d}B  data={rest.hex()}")
        n_shown += 1

    sys.stderr.write(f"\n# Total packets: {n_total}, ATT: {n_att}, shown: {n_shown}\n")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("pcap", type=Path, help="pcap converted from btsnoop")
    p.add_argument("--only-data", action="store_true",
                   help="only show data exchange (writes + notifications)")
    p.add_argument("--hide-discovery", action="store_true",
                   help="hide GATT discovery / MTU / error packets")
    args = p.parse_args()
    if not args.pcap.exists():
        sys.exit(f"file not found: {args.pcap}")
    sys.exit(parse(args.pcap, only_data=args.only_data,
                   hide_discovery=args.hide_discovery))


if __name__ == "__main__":
    main()
