#!/usr/bin/env python3
"""
Construye un `.ufw` con wrapper Qix de 27 bytes a partir de un JieLi UFW vanilla.

Formato del wrapper (verificado byte-exacto contra 5 .ufw OEM ZRun, 2026-05-06):

  [0:2]   bc af              MAGIC CONSTANTE Qix (NO crc)
  [2]     01                 type = firmware update
  [3:13]  ASCII version 10B  null-padded (e.g. "10.1.1.1\0\0")
  [13:17] payload_size LE32  (= file_size - 27)
  [17:25] 00 00 ... 00       reserved/padding (8B de ceros)
  [25:27] CRC LE16           CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF) sobre payload[27:end]
  [27:end] <JieLi UFW vanilla con magic "JLUFW" en footer>

Output queda listo para enviar al badge ZRun via UpdateManager.startUpdate(bytes).

También provee `--unwrap` para extraer el payload JieLi de un .ufw OEM y `--verify` para
validar wrapper de un .ufw existente contra el algoritmo derivado.
"""
import argparse
import binascii
import struct
import sys
from pathlib import Path

QIX_MAGIC = b"\xbc\xaf"
QIX_TYPE_FIRMWARE = 0x01
WRAPPER_LEN = 27


def crc16_ccitt_false(data: bytes) -> int:
    """CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, no refin/refout, no xorout."""
    return binascii.crc_hqx(data, 0xFFFF) & 0xFFFF


def build_wrapper(version: str, payload: bytes) -> bytes:
    if len(version.encode("ascii")) > 10:
        raise ValueError(f"version '{version}' excede 10 bytes ASCII")
    ver_bytes = version.encode("ascii").ljust(10, b"\x00")
    size_le = struct.pack("<I", len(payload))
    reserved = b"\x00" * 8
    crc = crc16_ccitt_false(payload)
    crc_le = struct.pack("<H", crc)
    wrapper = QIX_MAGIC + bytes([QIX_TYPE_FIRMWARE]) + ver_bytes + size_le + reserved + crc_le
    assert len(wrapper) == WRAPPER_LEN, f"wrapper {len(wrapper)} != 27"
    return wrapper


def parse_wrapper(data: bytes) -> dict:
    if len(data) < WRAPPER_LEN:
        raise ValueError(f"archivo {len(data)}B < 27B wrapper")
    return {
        "magic": data[0:2],
        "type": data[2],
        "version": data[3:13].rstrip(b"\x00").decode("ascii", errors="replace"),
        "payload_size": struct.unpack("<I", data[13:17])[0],
        "reserved": data[17:25],
        "crc": struct.unpack("<H", data[25:27])[0],
        "payload": data[27:],
    }


def wrap(payload_path: Path, version: str, out_path: Path) -> Path:
    payload = payload_path.read_bytes()
    wrapper = build_wrapper(version, payload)
    out = wrapper + payload
    out_path.write_bytes(out)
    print(f"[+] {payload_path} ({len(payload)} bytes) + wrapper Qix v={version!r} → {out_path} ({len(out)} bytes)")
    print(f"    crc16 ccitt-false = 0x{crc16_ccitt_false(payload):04x}")
    return out_path


def unwrap(wrapped_path: Path, out_path: Path) -> Path:
    data = wrapped_path.read_bytes()
    info = parse_wrapper(data)
    out_path.write_bytes(info["payload"])
    print(f"[+] {wrapped_path}: magic={info['magic'].hex()} type={info['type']:#x} "
          f"ver={info['version']!r} size={info['payload_size']} crc={info['crc']:#06x}")
    print(f"    → payload {out_path} ({len(info['payload'])} bytes)")
    return out_path


def verify(wrapped_path: Path) -> bool:
    data = wrapped_path.read_bytes()
    info = parse_wrapper(data)
    expected_crc = crc16_ccitt_false(info["payload"])
    expected_size = len(info["payload"])

    ok_magic = info["magic"] == QIX_MAGIC
    ok_type = info["type"] == QIX_TYPE_FIRMWARE
    ok_size = info["payload_size"] == expected_size
    ok_crc = info["crc"] == expected_crc
    ok_jlufw = b"JLUFW" in info["payload"][-32:]

    print(f"=== {wrapped_path} ===")
    print(f"  magic         {'OK' if ok_magic else 'FAIL'}: {info['magic'].hex()} == bcaf")
    print(f"  type          {'OK' if ok_type else 'FAIL'}: {info['type']:#x} == 0x01")
    print(f"  version       {info['version']!r}")
    print(f"  payload_size  {'OK' if ok_size else 'FAIL'}: {info['payload_size']} == {expected_size}")
    print(f"  crc16         {'OK' if ok_crc else 'FAIL'}: {info['crc']:#06x} == {expected_crc:#06x}")
    print(f"  JLUFW footer  {'OK' if ok_jlufw else 'FAIL'}: presente={ok_jlufw}")
    return ok_magic and ok_type and ok_size and ok_crc and ok_jlufw


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("wrap", help="JieLi vanilla .ufw → .ufw con wrapper Qix")
    sp.add_argument("payload", type=Path, help="path al .ufw vanilla (input)")
    sp.add_argument("--version", default="0.0.0.1", help="version string (max 10 ASCII)")
    sp.add_argument("-o", "--output", type=Path, required=True, help="output .ufw wrapped")

    sp = sub.add_parser("unwrap", help=".ufw con wrapper Qix → JieLi vanilla")
    sp.add_argument("wrapped", type=Path)
    sp.add_argument("-o", "--output", type=Path, required=True)

    sp = sub.add_parser("verify", help="valida wrapper Qix de un .ufw existente")
    sp.add_argument("wrapped", type=Path, nargs="+")

    args = ap.parse_args()

    if args.cmd == "wrap":
        wrap(args.payload, args.version, args.output)
    elif args.cmd == "unwrap":
        unwrap(args.wrapped, args.output)
    elif args.cmd == "verify":
        all_ok = True
        for path in args.wrapped:
            if not verify(path):
                all_ok = False
            print()
        sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
