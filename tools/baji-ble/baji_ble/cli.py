"""CLI argparse para baji-ble. Subcomandos: scan, info, find, query, dial-dims,
upload-dial, battery-watch, ae00-probe.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

from bleak import BleakClient

from baji_ble.ae00_probe import probe_ae00
from baji_ble.sysinfo_probe import probe_sysinfo
from baji_ble.commands.dial import build_dial_dims_query
from baji_ble.commands.query import build_query
from baji_ble.commands.settings import build_find_me_on
from baji_ble.commands.upload import upload_dial
from baji_ble.device_info import read_battery_level, read_device_info
from baji_ble.errors import BajiError
from baji_ble.frame import parse_dial_clock_info
from baji_ble.opcodes import CMD_DIAL_NOTIFY, CMD_QUERY, query_key_label
from baji_ble.session import BajiSession
from baji_ble.service import CHAR_BATTERY_LEVEL
from baji_ble.transport import BajiTransport


def setup_logging(verbose: int):
    level = logging.WARNING - 10 * min(verbose, 2)
    logging.basicConfig(
        level=level,
        format="%(asctime)s.%(msecs)03d %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_scan(args) -> int:
    devices = BajiTransport.scan(timeout=args.timeout, name_filter=None if args.all else "DG01")
    if not devices:
        print("(no devices)")
        return 0
    for d in devices:
        print(f"{d.address}  {d.name or '<no name>'}  RSSI={getattr(d, 'rssi', '?')}")
    return 0


def cmd_info(args) -> int:
    async def _do():
        async with BleakClient(args.mac) as client:
            info = read_device_info(client)
            pct = read_battery_level(client)
        return info, pct
    info, pct = asyncio.run(_do())
    print(f"Manufacturer:  {info.manufacturer}")
    print(f"Model:         {info.model}")
    print(f"Serial:        {info.serial}")
    print(f"Firmware:      {info.firmware_revision}")
    print(f"Hardware:      {info.hardware_revision}")
    print(f"Software:      {info.software_revision}")
    print(f"IEEE Cert:     {info.ieee_cert}")
    print(f"Battery:       {pct}%")
    return 0


def cmd_find(args) -> int:
    with BajiTransport(args.mac) as t:
        t.send_raw_to_nus(build_find_me_on())
        print("find-me sent (cmd 18 sub 11 value 1)")
    return 0


def cmd_query(args) -> int:
    with BajiTransport(args.mac) as t:
        sess = BajiSession(t)
        resp = sess.send_and_wait(
            build_query(args.key), expect_cmd=CMD_QUERY, expect_sub=args.key, timeout=args.timeout,
        )
        label = query_key_label(args.key) or "?"
        print(f"key={args.key} ({label}) raw={resp.hex()}")
    return 0


def cmd_dial_dims(args) -> int:
    with BajiTransport(args.mac) as t:
        sess = BajiSession(t)
        resp = sess.send_and_wait(
            build_dial_dims_query(), expect_cmd=CMD_DIAL_NOTIFY, expect_sub=2, timeout=args.timeout,
        )
        info = parse_dial_clock_info(resp)
        if info is None:
            print(f"unparseable: {resp.hex()}")
            return 1
        print(f"screen_type={info.screen_type} grade={info.grade} "
              f"width={info.width} height={info.height} config={info.config}")
        print(f"RGB565 body size = {info.width * info.height * 2} bytes")
    return 0


def cmd_upload_dial(args) -> int:
    file = Path(args.file).read_bytes()
    with BajiTransport(args.mac) as t:
        upload_dial(
            t, file=file, chunk_size=args.chunk,
            font_position=args.font_position, custom=args.custom,
            r=args.r, g=args.g, b=args.b, timeout=args.timeout,
        )
    print(f"uploaded {len(file)} bytes OK")
    return 0


def cmd_battery_watch(args) -> int:
    async def _do():
        async with BleakClient(args.mac) as client:
            start = time.monotonic()
            while time.monotonic() - start < args.secs:
                raw = await client.read_gatt_char(CHAR_BATTERY_LEVEL)
                print(f"[{time.strftime('%H:%M:%S')}] battery {raw[0] if raw else 0}%")
                await asyncio.sleep(args.interval)
    asyncio.run(_do())
    return 0


def cmd_ae00_probe(args) -> int:
    result = probe_ae00(args.mac, timeout=args.timeout)
    if result.success:
        print(f"OK — {result.message}")
        return 0
    print(f"FAIL — {result.message}")
    return 1


def cmd_sysinfo(args) -> int:
    result = probe_sysinfo(args.mac, handshake_timeout=args.timeout, rcsp_timeout=args.timeout)
    print(f"Handshake: {'OK' if result.handshake_ok else 'FAIL'}")
    if result.target_info is not None:
        print(f"\n=== Target Info (cmd 0x03, {len(result.target_info.attrs)} attrs) ===")
        print(f"raw: {result.target_info.raw.hex()}")
        for attr in sorted(result.target_info.attrs, key=lambda a: a.type):
            decoded = attr.decoded if attr.decoded is not None else attr.data.hex()
            print(f"  [{attr.type:3d}] {attr.name:<32} = {decoded}")
    if result.sys_info is not None:
        print(f"\n=== Sys Info (cmd 0x07, {len(result.sys_info.attrs)} attrs) ===")
        print(f"raw: {result.sys_info.raw.hex()}")
        for attr in sorted(result.sys_info.attrs, key=lambda a: a.type):
            decoded = attr.decoded if attr.decoded is not None else attr.data.hex()
            print(f"  [{attr.type:3d}] {attr.name:<32} = {decoded}")
    if not result.success:
        print(f"\nFAIL — {result.message}")
        return 1
    print(f"\nOK — {result.message}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="baji", description="Cliente BLE para badge DG01 (path Baji NUS + AE00 probe)")
    p.add_argument("-v", "--verbose", action="count", default=0)
    sub = p.add_subparsers(dest="subcmd", required=True)

    s = sub.add_parser("scan"); s.add_argument("--timeout", type=float, default=10.0); s.add_argument("--all", action="store_true"); s.set_defaults(func=cmd_scan)
    s = sub.add_parser("info"); s.add_argument("--mac", required=True); s.set_defaults(func=cmd_info)
    s = sub.add_parser("find"); s.add_argument("--mac", required=True); s.set_defaults(func=cmd_find)
    s = sub.add_parser("query"); s.add_argument("--mac", required=True); s.add_argument("--key", type=int, required=True); s.add_argument("--timeout", type=float, default=5.0); s.set_defaults(func=cmd_query)
    s = sub.add_parser("dial-dims"); s.add_argument("--mac", required=True); s.add_argument("--timeout", type=float, default=5.0); s.set_defaults(func=cmd_dial_dims)
    s = sub.add_parser("upload-dial")
    s.add_argument("--mac", required=True); s.add_argument("--file", required=True)
    s.add_argument("--chunk", type=int, default=200)
    s.add_argument("--font-position", type=int, default=0)
    s.add_argument("--custom", type=int, default=0)
    s.add_argument("--r", type=int, default=255); s.add_argument("--g", type=int, default=255); s.add_argument("--b", type=int, default=255)
    s.add_argument("--timeout", type=float, default=10.0)
    s.set_defaults(func=cmd_upload_dial)
    s = sub.add_parser("battery-watch"); s.add_argument("--mac", required=True); s.add_argument("--secs", type=float, default=60.0); s.add_argument("--interval", type=float, default=5.0); s.set_defaults(func=cmd_battery_watch)
    s = sub.add_parser("ae00-probe"); s.add_argument("--mac", required=True); s.add_argument("--timeout", type=float, default=5.0); s.set_defaults(func=cmd_ae00_probe)
    s = sub.add_parser("sysinfo"); s.add_argument("--mac", required=True); s.add_argument("--timeout", type=float, default=10.0); s.set_defaults(func=cmd_sysinfo)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)
    try:
        return args.func(args)
    except BajiError as e:
        print(f"baji error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
