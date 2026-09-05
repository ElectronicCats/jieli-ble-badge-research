"""CLI argparse para el cliente Qix BLE."""
from __future__ import annotations

import argparse
import logging
import struct
import sys
import time
from pathlib import Path

from qix_ble.errors import (
    QixError, BleConnectionError, BadgeRejected, UfwInvalid,
    TimeoutError as QixTimeoutError,
)
from qix_ble.frame import QixFrame
from qix_ble.opcodes import CMD_NAMES
from qix_ble.transport import QixTransport
from qix_ble.update_manager import QixUpdater, validate_qix_wrapper


def setup_logging(verbose: int, logfile: Path | None):
    level = logging.WARNING - 10 * min(verbose, 2)
    fmt = "%(asctime)s.%(msecs)03d %(levelname)-7s %(name)s: %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if logfile:
        handlers.append(logging.FileHandler(logfile))
    logging.basicConfig(level=level, format=fmt, datefmt="%H:%M:%S", handlers=handlers)


def fmt_frame(frame: QixFrame) -> str:
    name = CMD_NAMES.get(frame.cmd, "?")
    return (f"cmd=0x{frame.cmd:02X} ({name}) flags=0x{frame.flags:02X} "
            f"len={len(frame.payload)} payload={frame.payload.hex()}")


# ─── subcomandos ────────────────────────────────────────────────────────────

def cmd_scan(args) -> int:
    devices = QixTransport.scan(timeout=args.timeout, all_devices=args.all)
    if not devices:
        print("(no devices)")
        return 0
    for d in devices:
        print(f"{d.address}  {d.name or '<no name>'}  RSSI={getattr(d, 'rssi', '?')}")
    return 0


def cmd_info(args) -> int:
    with QixTransport(args.mac) as t:
        resp = t.send_command(0xC6, expect_cmd=0xC7, timeout=args.timeout)
        print(fmt_frame(resp))
    return 0


def cmd_battery(args) -> int:
    """Query battery — default vía device push post-bootstrap (E87 OEM path).

    Per community PROTOCOL.md §6: el FW push cmd 0x27 spontaneamente
    después de bootstrap Phase 5. Auth + bootstrap son requeridos.

    `--sysinfo` usa attr 0 (deprecated — siempre fake 0x00 en este FW).
    `--legacy` usa cmd 0x27 query standalone (NO funciona en este FW).
    """
    import json as _json
    from qix_ble.auth import AuthSession
    from qix_ble.battery import (
        query_battery, query_battery_legacy, query_battery_via_sysinfo,
    )
    from qix_ble.bootstrap import run_bootstrap
    from qix_ble.rcsp_session import RcspSession

    with QixTransport(args.mac) as t:
        if args.legacy:
            if args.bootstrap:
                AuthSession(t, step_timeout=args.timeout).do_handshake()
                run_bootstrap(t)
                t.drain()
                t.drain_ae02_raw()
            status = query_battery_legacy(t, timeout=args.timeout)
        elif args.sysinfo:
            AuthSession(t, step_timeout=args.timeout).do_handshake()
            if args.bootstrap:
                run_bootstrap(t)
                t.drain()
                t.drain_ae02_raw()
            status = query_battery_via_sysinfo(RcspSession(t), timeout=args.timeout)
        else:
            # Default: device push path (correct para E87 OEM)
            AuthSession(t, step_timeout=args.timeout).do_handshake()
            run_bootstrap(t)
            t.drain_ae02_raw()
            # NO drain Qix queue — el push 0x27 puede venir durante bootstrap final
            status = query_battery(t, listen_timeout=args.timeout)

    if args.json:
        print(_json.dumps({"mode": status.mode, "percent": status.percent}))
    else:
        mode_name = "discharging" if status.mode == 0 else "charging/other"
        print(f"battery={status.percent}%  mode=0x{status.mode:02x} ({mode_name})")
    return 0


def cmd_bootstrap(args) -> int:
    """Run Phase 1-5 community bootstrap aislado (debug / probe).

    Útil para validar contra HW si el badge acepta SESSION_OPEN después.
    """
    import json as _json
    from qix_ble.auth import AuthSession
    from qix_ble.bootstrap import run_bootstrap

    with QixTransport(args.mac) as t:
        AuthSession(t, step_timeout=args.timeout).do_handshake()
        results = run_bootstrap(t)

    if args.json:
        print(_json.dumps(results))
    else:
        print("bootstrap phase acks:")
        for k, v in results.items():
            mark = "ack ✓" if v else "(timeout — continuing)"
            print(f"  {k:14} {mark}")
        all_ack = all(results.values())
        print(f"  result: {'all phases acked' if all_ack else 'partial (best-effort)'}")
    return 0


def cmd_push_image(args) -> int:
    """Push single image al badge: prepare → upload via RcspUploader."""
    from qix_ble.auth import AuthSession
    from qix_ble.image import prepare_image
    from qix_ble.upload import RcspUploader

    img_bytes = prepare_image(args.path, target_size=(args.width, args.height),
                              fit=args.fit, zoom=args.zoom)
    print(f"prepared {len(img_bytes)} bytes JPEG", file=sys.stderr)

    with QixTransport(args.mac) as t:
        AuthSession(t, step_timeout=args.timeout).do_handshake()
        result = RcspUploader(t).upload(img_bytes, name=args.name, mode="image")

    if result.success:
        print(f"upload OK: {result.bytes_sent} bytes, chunk={result.chunk_size}, "
              f"dt={result.duration_s:.2f}s")
        return 0
    else:
        print(f"upload FAILED: {result.error}", file=sys.stderr)
        return 3


def cmd_push_video(args) -> int:
    """Push animated image (GIF/APNG/WebP) o AVI al badge."""
    from pathlib import Path as _P
    from qix_ble.auth import AuthSession
    from qix_ble.image import prepare_video
    from qix_ble.upload import RcspUploader

    p = _P(args.path)
    if p.suffix.lower() in (".avi", ".mjpeg"):
        avi_bytes = p.read_bytes()
    else:
        avi_bytes = prepare_video(args.path, target_size=(args.width, args.height),
                                   fps=args.fps, duration=args.duration)
    print(f"prepared {len(avi_bytes)} bytes AVI", file=sys.stderr)

    with QixTransport(args.mac) as t:
        AuthSession(t, step_timeout=args.timeout).do_handshake()
        result = RcspUploader(t).upload(avi_bytes, name=args.name, mode="avi")

    print(f"upload {'OK' if result.success else 'FAILED'}: "
          f"{result.bytes_sent} bytes, dt={result.duration_s:.2f}s"
          + (f" — {result.error}" if result.error else ""))
    return 0 if result.success else 3


def cmd_push_text(args) -> int:
    """Generate text animation AVI + upload."""
    from qix_ble.auth import AuthSession
    from qix_ble.patterns import prepare_text
    from qix_ble.upload import RcspUploader

    avi = prepare_text(args.text, mode=args.mode,
                       target_size=(args.width, args.height),
                       fps=args.fps, duration=args.duration,
                       font_size=args.font_size, bold=args.bold,
                       font_path=args.font)
    print(f"prepared {len(avi)} bytes AVI", file=sys.stderr)

    with QixTransport(args.mac) as t:
        AuthSession(t, step_timeout=args.timeout).do_handshake()
        result = RcspUploader(t).upload(avi, name=args.name, mode="avi")

    print(f"text push {'OK' if result.success else 'FAILED'}: "
          f"{result.bytes_sent} bytes, dt={result.duration_s:.2f}s"
          + (f" — {result.error}" if result.error else ""))
    return 0 if result.success else 3


def cmd_push_pattern(args) -> int:
    """Generate pattern animation AVI + upload."""
    from qix_ble.auth import AuthSession
    from qix_ble.patterns import prepare_pattern
    from qix_ble.upload import RcspUploader

    avi = prepare_pattern(args.pattern,
                          target_size=(args.width, args.height),
                          fps=args.fps, frame_count=args.frames)
    print(f"prepared {len(avi)} bytes AVI", file=sys.stderr)

    with QixTransport(args.mac) as t:
        AuthSession(t, step_timeout=args.timeout).do_handshake()
        result = RcspUploader(t).upload(avi, name=args.name, mode="avi")

    print(f"pattern push {'OK' if result.success else 'FAILED'}: "
          f"{result.bytes_sent} bytes, dt={result.duration_s:.2f}s"
          + (f" — {result.error}" if result.error else ""))
    return 0 if result.success else 3


def cmd_push_bootanim(args) -> int:
    """EXPERIMENTAL — push boot animation (RGB565 raw, no JPEG).

    Wire spec del smali `DialTool.sendBootAniFile`: blob = [27B file_hdr type=0x0B]
    + [8B BM hdr] + [RGB565 LE raw pixels]. Upload vía RcspUploader.upload(mode="bootanim").

    Caveat: NO validado contra HW. Use --query-only primero para verificar feature
    support en tu badge antes de cualquier push.
    """
    from qix_ble.auth import AuthSession
    from qix_ble.bootanim import prepare_bootanim, query_boot_ani_info
    from qix_ble.upload import RcspUploader

    if args.query_only:
        with QixTransport(args.mac) as t:
            AuthSession(t, step_timeout=args.timeout).do_handshake()
            info = query_boot_ani_info(t, timeout=args.timeout)
        print(f"boot ani info: width={info.width} height={info.height} "
              f"fmt_type=0x{info.fmt_type:02x}")
        return 0

    blob = prepare_bootanim(args.path,
                            target_size=(args.width, args.height),
                            fit=args.fit)
    print(f"prepared {len(blob)} bytes bootanim blob "
          f"({args.width}x{args.height} RGB565)", file=sys.stderr)
    if len(blob) > 200_000:
        print(f"⚠️  size {len(blob)}B excede 200KB threshold seguro — "
              f"badge puede rechazar. Considerá 240x240 (~115KB).",
              file=sys.stderr)

    with QixTransport(args.mac) as t:
        AuthSession(t, step_timeout=args.timeout).do_handshake()
        result = RcspUploader(t).upload(blob, name=args.name, mode="bootanim")

    print(f"upload {'OK' if result.success else 'FAILED'}: "
          f"{result.bytes_sent} bytes, dt={result.duration_s:.2f}s"
          + (f" — {result.error}" if result.error else ""))
    return 0 if result.success else 3


def cmd_dump_health(args) -> int:
    """Dump histórico steps/sleep/hr/pressure/oxygen/battery.

    State machine: TX cmd 0x29 [mask] → RX cmd 0x21 SYNC_START → loop RX
    cmd 0x22-0x27 frames (auto-ack via cmd 0xFF) → RX cmd 0x21 SYNC_END.

    Requires BIND post-bootstrap. Auth NOT required per smali.
    """
    import json as _json
    from qix_ble.auth import AuthSession
    from qix_ble.bind import bind as _bind
    from qix_ble.health_dump import dump_health, DATA_TYPE_MASK
    from dataclasses import asdict

    if args.types == "all":
        types = None  # → mask 0xBF
    else:
        types = [t.strip() for t in args.types.split(",") if t.strip()]
        invalid = [t for t in types if t not in DATA_TYPE_MASK]
        if invalid:
            print(f"unknown types: {invalid}. Valid: {list(DATA_TYPE_MASK)}",
                  file=sys.stderr)
            return 2

    with QixTransport(args.mac) as t:
        if args.bootstrap:
            from qix_ble.bootstrap import run_bootstrap
            AuthSession(t, step_timeout=args.timeout).do_handshake()
            run_bootstrap(t)
            t.drain()
            t.drain_ae02_raw()
        else:
            # BIND-only path per app real (mínimo). Pre-bind opcional para que
            # el badge populate firmware_version etc.
            try:
                _bind(t, timeout=args.timeout)
            except Exception as e:
                print(f"warning: bind failed ({e}), continuing anyway",
                      file=sys.stderr)
        result = dump_health(t, types=types,
                             timeout_per_frame=args.timeout,
                             total_timeout=args.total_timeout)

    if args.json:
        out = asdict(result)
        print(_json.dumps(out, indent=2))
    else:
        print(f"frames RX: {result.raw_frames}")
        print(f"  steps:    {len(result.steps)} entries")
        print(f"  sleep:    {len(result.sleep)} entries")
        print(f"  hr:       {len(result.hr)} entries")
        print(f"  pressure: {len(result.pressure)} entries")
        print(f"  oxygen:   {len(result.oxygen)} entries")
        if result.battery:
            print(f"  battery:  charge_mode={result.battery['charge_mode']} "
                  f"percent={result.battery['percent']}%")
        # show first few entries of each type
        for key in ("steps", "sleep", "hr", "pressure", "oxygen"):
            entries = getattr(result, key)
            if entries:
                print(f"\n  {key} first {min(3, len(entries))}:")
                for e in entries[:3]:
                    print(f"    {e}")
    return 0


def cmd_bind(args) -> int:
    """Bind cmd 0x60 + parse response 0x61. Memory dice gated → status=0x02 en E87.

    Default --bootstrap True para max chance de aceptación.
    """
    import json as _json
    from qix_ble.auth import AuthSession
    from qix_ble.bind import bind
    from qix_ble.bootstrap import run_bootstrap

    with QixTransport(args.mac) as t:
        if args.bootstrap:
            AuthSession(t, step_timeout=args.timeout).do_handshake()
            run_bootstrap(t)
            t.drain()
            t.drain_ae02_raw()
        result = bind(t, lang=args.lang, hour12=args.hour12,
                      device_id=args.device_id, timeout=args.timeout)

    if args.json:
        print(_json.dumps({
            "state": result.state,
            "pact_version": result.pact_version,
            "firmwa_version": result.firmwa_version,
            "platform": result.platform,
            "serial_number": result.serial_number,
            "function_config": result.function_config,
            "function_config1": result.function_config1,
            "function_config2": result.function_config2,
            "ui_version": result.ui_version,
            "function_bytes": result.function_bytes.hex(),
        }))
    else:
        print(f"state={result.state}  pact={result.pact_version}  "
              f"fw={result.firmwa_version}  ui={result.ui_version}")
        print(f"platform=0x{result.platform:08x}  serial={result.serial_number}")
        print(f"func_config=0x{result.function_config:08x} / "
              f"0x{result.function_config1:08x} / 0x{result.function_config2:016x}")
        if result.function_bytes:
            print(f"function_bytes={result.function_bytes.hex()}")
    return 0


def cmd_unbind(args) -> int:
    """Unbind cmd 0x62 — fire-and-forget. Bootstrap opcional (probablemente no requerido)."""
    from qix_ble.auth import AuthSession
    from qix_ble.bind import unbind
    from qix_ble.bootstrap import run_bootstrap

    with QixTransport(args.mac) as t:
        if args.bootstrap:
            AuthSession(t, step_timeout=args.timeout).do_handshake()
            run_bootstrap(t)
            t.drain()
            t.drain_ae02_raw()
        unbind(t)
    print("unbind frame sent (no response expected)")
    return 0


def cmd_sysinfo(args) -> int:
    """Query RCSP target_info (cmd 0x03) + sys_info (cmd 0x07). Requires auth."""
    import json as _json
    from qix_ble.auth import AuthSession
    from qix_ble.rcsp_session import RcspSession
    from qix_ble.sysinfo import get_target_info, get_sys_info

    with QixTransport(args.mac) as t:
        AuthSession(t, step_timeout=args.timeout).do_handshake()
        sess = RcspSession(t)
        target = get_target_info(sess, timeout=args.timeout * 2)
        sys_ = None
        if args.kind in ("sys", "both"):
            try:
                sys_ = get_sys_info(sess, timeout=args.timeout * 2)
            except Exception as e:
                if args.kind == "sys":
                    raise
                print(f"warning: get_sys_info failed: {e}", file=sys.stderr)

    if args.json:
        out: dict = {}
        if args.kind in ("target", "both"):
            out["target_info"] = {
                "raw": target.raw.hex(),
                "attrs": [
                    {"type": a.type, "name": a.name, "data": a.data.hex(),
                     "decoded": a.decoded}
                    for a in target.attrs
                ],
            }
        if sys_ is not None:
            out["sys_info"] = {
                "function": sys_.function,
                "raw": sys_.raw.hex(),
                "attrs": [
                    {"type": a.type, "name": a.name, "data": a.data.hex(),
                     "decoded": a.decoded}
                    for a in sys_.attrs
                ],
            }
        print(_json.dumps(out, indent=2))
    else:
        if args.kind in ("target", "both"):
            print(f"── target_info ({len(target.attrs)} attrs) ──")
            for a in target.attrs:
                decoded = f" → {a.decoded}" if a.decoded else ""
                print(f"  [0x{a.type:02x}] {a.name:25} = {a.data.hex()}{decoded}")
        if sys_ is not None:
            print(f"── sys_info function=0x{sys_.function:02x} ({len(sys_.attrs)} attrs) ──")
            for a in sys_.attrs:
                decoded = f" → {a.decoded}" if a.decoded else ""
                print(f"  [0x{a.type:02x}] {a.name:25} = {a.data.hex()}{decoded}")
    return 0


def cmd_raw(args) -> int:
    cmd = int(args.cmd_hex, 16)
    payload = bytes.fromhex(args.payload_hex) if args.payload_hex else b""
    with QixTransport(args.mac) as t:
        resp = t.send_command(cmd, payload, timeout=args.timeout)
        print(fmt_frame(resp))
    return 0


def cmd_dump(args) -> int:
    cmd_map = {"flash": 0xAA, "memory": 0xA9, "ram": 0xAE}
    cmd = cmd_map[args.type]
    out_path = Path(args.output)
    end_addr = args.addr + args.length

    with QixTransport(args.mac) as t:
        if args.auth:
            from qix_ble.auth import AuthSession
            AuthSession(t, step_timeout=args.timeout).do_handshake()
            print("auth OK — handshake completed (6 steps)")
        if args.bootstrap:
            from qix_ble.bootstrap import run_bootstrap
            run_bootstrap(t)
            t.drain()
            t.drain_ae02_raw()
            print("bootstrap OK — phases 1-5 completed")

        # Activar test mode
        t.send_command(0xA0, b"\x01", timeout=args.timeout)
        try:
            with out_path.open("wb") as fh:
                offset = args.addr
                total = args.length
                done = 0
                while offset < end_addr:
                    chunk_len = min(args.chunk_size, end_addr - offset)
                    payload = struct.pack("<II", offset, chunk_len)
                    resp = t.send_command(cmd, payload, timeout=args.chunk_timeout)
                    if resp.cmd != cmd:
                        print(f"\n⚠ unexpected response cmd=0x{resp.cmd:02X} "
                              f"payload={resp.payload.hex()} — aborting", flush=True)
                        return 3
                    received = len(resp.payload)
                    fh.write(resp.payload)
                    offset += chunk_len
                    done += received
                    pct = 100 * done / total if total else 100
                    print(f"\r  {done}/{total} bytes ({pct:.1f}%)", end="", flush=True)
                print()
        finally:
            try:
                t.send_command(0xA6, b"", timeout=2.0)  # TEST_CLOSE
            except QixError:
                pass

    print(f"saved {out_path} ({out_path.stat().st_size} bytes)")
    return 0


def cmd_auth(args) -> int:
    """Run 6-step JieLi RCSP handshake aislado. Útil para sanity check antes
    de operaciones gated."""
    from qix_ble.auth import AuthSession
    with QixTransport(args.mac) as t:
        AuthSession(t, step_timeout=args.timeout).do_handshake()
    print("auth OK — handshake completed (6 steps)")
    return 0


def cmd_fs_ls(args) -> int:
    """Listar archivos del FS del badge (browse_all o handler específico)."""
    import json
    from qix_ble.auth import AuthSession
    from qix_ble.rcsp_session import (
        RcspSession,
        DEV_HANDLER_USB, DEV_HANDLER_SD0, DEV_HANDLER_SD1, DEV_HANDLER_FLASH,
    )
    handler_map = {
        "USB": DEV_HANDLER_USB, "SD0": DEV_HANDLER_SD0,
        "SD1": DEV_HANDLER_SD1, "FLASH": DEV_HANDLER_FLASH,
    }

    with QixTransport(args.mac) as t:
        AuthSession(t, step_timeout=args.timeout).do_handshake()
        sess = RcspSession(t)
        if args.dev == "ALL":
            entries = sess.browse_all(type=args.type, read_num=args.read_num)
        else:
            handler = handler_map[args.dev]
            entries = sess.browse(
                type=args.type, read_num=args.read_num,
                start_index=0, dev_handler=handler, clusters=[],
            )

    if args.json:
        out = [
            {
                "type": e.type,
                "type_name": e.type_name,
                "id": e.id,
                "size": e.size,
                "cluster": e.cluster,
                "name": e.name,
            }
            for e in entries
        ]
        print(json.dumps(out, indent=2))
    else:
        if not entries:
            print("(no entries)")
        else:
            print(f"{'TYPE':6} {'CLUSTER':>10} {'SIZE':>10}  NAME")
            for e in entries:
                cluster_s = f"0x{e.cluster:x}" if e.cluster is not None else "-"
                name_s = e.name if e.name else "(unparsed)"
                print(f"{e.type_name:6} {cluster_s:>10} {e.size:>10}  {name_s}")
    return 0


def cmd_fs_get(args) -> int:
    """Download archivo del FS del badge por ID. Auto small vs large."""
    from qix_ble.auth import AuthSession
    from qix_ble.rcsp_session import RcspSession, FileEntry

    target_id = int(args.id_hex, 16)
    out_path = Path(args.output)
    handler_map = {"USB": 0, "SD0": 1, "SD1": 2, "FLASH": 3}
    handler = handler_map[args.dev]

    with QixTransport(args.mac) as t:
        AuthSession(t, step_timeout=args.timeout).do_handshake()
        sess = RcspSession(t)

        # Browse the specified handler para encontrar el entry
        entries = sess.browse(
            type=args.type, read_num=255, start_index=0,
            dev_handler=handler, clusters=[],
        )
        match = next((e for e in entries if e.id == target_id or e.cluster == target_id), None)
        if match is None:
            print(f"error: file id 0x{target_id:x} not found in {args.dev}",
                  file=sys.stderr)
            return 4

        if match.size <= 0xFFFF:
            data, crc = sess.read_small_file(match)
            out_path.write_bytes(data)
            print(f"saved {out_path} ({len(data)} bytes, crc16=0x{crc:04x})")
        else:
            try:
                sess.read_large_file(match, out_path)
            except NotImplementedError as e:
                print(f"error: large file read not yet implemented ({match.size} bytes). "
                      f"Run probe phase first. See plan T11. ({e})",
                      file=sys.stderr)
                return 5
            print(f"saved {out_path} ({match.size} bytes)")
    return 0


def cmd_raw_rcsp(args) -> int:
    """Send arbitrary RCSP frame to AE01, print response(s) from AE02.

    Útil para probe / debug. Por default hace auth handshake antes (sin auth
    la mayoría de los cmds dan rejection). --no-auth skip eso.
    """
    from qix_ble.auth import AuthSession
    from qix_ble.rcsp_frame import RcspFrame
    flag = int(args.flag_hex, 16)
    cmd = int(args.cmd_hex, 16)
    payload = bytes.fromhex(args.payload_hex.replace(" ", "")) if args.payload_hex else b""

    frame = RcspFrame(flag=flag, cmd=cmd, payload=payload)
    print(f"TX: {frame.encode().hex()}")

    with QixTransport(args.mac) as t:
        if not args.no_auth:
            AuthSession(t, step_timeout=args.timeout).do_handshake()
            print("auth OK")
        t.send_rcsp_frame(frame)
        # Loop: collect responses until timeout (1 or more notifications possible)
        n_responses = 0
        try:
            while True:
                resp = t.recv_rcsp_frame(timeout=args.timeout)
                print(f"RX: flag=0x{resp.flag:02x} cmd=0x{resp.cmd:02x} "
                      f"len={len(resp.payload)} payload={resp.payload.hex()}")
                n_responses += 1
        except (QixTimeoutError, BleConnectionError):
            pass  # No más responses — exit
        if n_responses == 0:
            print("(no responses)")
    return 0


def cmd_flash(args) -> int:
    ufw_path = Path(args.wrapped)
    ufw = ufw_path.read_bytes()
    if not args.no_validate:
        validate_qix_wrapper(ufw)
        print(f"wrapper Qix válido ({len(ufw)} bytes total, payload {len(ufw) - 27})",
              file=sys.stderr)

    # HID-badge pairing (opt-in via QIX_FLASH_PAIR=1). cmd_flash historically skipped
    # prepare_fresh_pairing (unlike rcsp-flash/deploy-fw): fine for the OEM E87 leg
    # whose OTA chars are cleartext, but the CUSTOM "Actualizar" screen is a BLE-HID
    # device and this host's BlueZ tears the link down on the first FD02 write when it
    # tries (and fails) to bond on the HID service. Bringing up a Just-Works agent +
    # pairing fresh on the OTA connection (QIX_PAIR=1) reaches the encrypted link and
    # stops the drop — the same mechanism the other flash-family commands use. Left
    # off by default so the proven OEM→custom path is byte-for-byte unchanged.
    import os as _os
    _pair_stop = (lambda: None)
    if _os.environ.get("QIX_FLASH_PAIR", "0") != "0":
        from qix_ble.bond import prepare_fresh_pairing
        _pair_stop = prepare_fresh_pairing(args.mac)
        print("QIX_FLASH_PAIR: agente Just-Works arriba + QIX_PAIR=1 (link HID)",
              file=sys.stderr)

    if args.probe_only:
        # Probe path: REQ_UPDATE only, structured JSON output. NUNCA chunk send.
        # --oem implies bootstrap + the slow (~15 s) REQ_UPDATE timeout, same as
        # the real-flash path below — the stock firmware needs auth+bootstrap and
        # reacts to REQ_UPDATE in ~6 s, so a 5 s probe false-times-out.
        import json
        from qix_ble.auth import AuthSession
        from qix_ble.bootstrap import run_bootstrap
        do_bootstrap = args.bootstrap or args.oem
        probe_timeout = (args.req_timeout if args.req_timeout is not None
                         else (15.0 if args.oem else args.timeout))
        with QixTransport(args.mac) as t:
            if do_bootstrap:
                AuthSession(t, step_timeout=args.timeout).do_handshake()
                run_bootstrap(t)
                t.drain()
                t.drain_ae02_raw()
                print("auth + bootstrap OK", file=sys.stderr)
            print(f"REQ_UPDATE timeout: {probe_timeout:.0f}s", file=sys.stderr)
            result = QixUpdater(t).probe(ufw, timeout=probe_timeout)
        out = {
            "ufw": ufw_path.name,
            "bootstrap": do_bootstrap,
            "state": result.state,
            "allow_len": result.allow_len,
            "offset": result.offset,
            "dt_ms": round(result.dt_ms, 1),
            "accepted": result.accepted,
        }
        print(json.dumps(out))
        _pair_stop()
        return 0 if result.accepted else 3

    last_pct = [-1]

    def progress(p: float):
        pct = int(p * 100)
        if pct != last_pct[0]:
            print(f"\r  flashing… {pct}%", end="", flush=True)
            last_pct[0] = pct

    # --oem: the stock OEM firmware reacts slowly to REQ_UPDATE (~6 s, shows
    # "actualizando") and needs auth+bootstrap. Bundle the sane defaults so a first
    # flash onto a stock badge doesn't false-timeout. (Our own stager firmware
    # replies fast — plain `flash` is fine once the badge runs STAGER.)
    bootstrap = args.bootstrap or args.oem
    req_timeout = args.req_timeout if args.req_timeout is not None else (15.0 if args.oem else None)

    with QixTransport(args.mac) as t:
        if bootstrap:
            from qix_ble.auth import AuthSession
            from qix_ble.bootstrap import run_bootstrap
            AuthSession(t, step_timeout=args.timeout).do_handshake()
            run_bootstrap(t)
            t.drain()
            t.drain_ae02_raw()
            print("auth + bootstrap OK", file=sys.stderr)
        if req_timeout:
            print(f"REQ_UPDATE timeout: {req_timeout:.0f}s (OEM reacts slowly)", file=sys.stderr)

        # Resume a mid-transfer link drop (the classic sustained-~1MB PC BlueZ drop
        # near the end). The update_manager resume CONTINUES the SEND_DATA loop from
        # the badge's preserved qix_ota_offset WITHOUT re-issuing REQ_UPDATE (0xC0
        # would re-erase the staging span → restart from 0 → same drop forever). The
        # FD02 data path is cleartext, so no re-auth/re-bootstrap is needed on the
        # fresh link — on_reconnect just re-establishes the LINK and drains the stale
        # RX frames from the dropped session (else the next ret_data read picks up a
        # leftover frame). This is the path custom→custom uses (custom "Actualizar"
        # only advertises + serves the OTA cleartext once auth+bootstrap ran the FIRST
        # time; that state persists in badge RAM across the BLE disconnect).
        def _link_reconnect():
            import time as _time
            from qix_ble.bluez_cleanup import force_disconnect
            # The badge is single-link: after a mid-transfer drop it keeps holding the
            # (now dead) link until ITS supervision timeout fires; reconnecting before
            # that leaves the badge refusing the new link (and any write fails at once).
            # Force a clean disconnect on both sides, then wait long enough for the
            # badge to notice, free the slot, and re-advertise before we reconnect.
            try:
                force_disconnect(t.mac)
            except Exception as e:
                print(f"  (force_disconnect in reconnect ignored: {e})", file=sys.stderr)
            print("  esperando 10s a que el badge libere el enlace y re-anuncie…",
                  file=sys.stderr)
            _time.sleep(10.0)
            t.reconnect()
            t.drain()
            t.drain_ae02_raw()

        QixUpdater(t).flash(ufw, on_progress=progress, timeout_per_chunk=args.timeout,
                            timeout_req=req_timeout,
                            on_reconnect=_link_reconnect)
    print("\nflash OK")
    _pair_stop()
    return 0


def cmd_rawflash(args) -> int:
    """Stager raw-flash: write a file to ANY flash partition via the custom
    0xD0..0xD3 commands (needs the STAGER firmware loaded). This is how you update
    the UI resources / virfat etc. that the OEM app-only Qix OTA can't reach.

    --addr accepts an absolute hex address (0x17E000) or a partition name
    (ui_res / virfat / data). The running CODE region (<0x17E000) is rejected.
    """
    from qix_ble.stager import StagerClient, StagerError, PARTITIONS
    from qix_ble.bond import prepare_fresh_pairing
    prepare_fresh_pairing(args.mac)   # HID badge → pair fresh on the connection (QIX_PAIR)

    data = Path(args.file).read_bytes()
    addr = PARTITIONS[args.addr] if args.addr in PARTITIONS else int(args.addr, 0)
    print(f"raw-flash {args.file} ({len(data)} bytes) -> 0x{addr:x}"
          f"{' ['+args.addr+']' if args.addr in PARTITIONS else ''}", file=sys.stderr)

    last = [-1]

    def progress(p: float):
        pct = int(p * 100)
        if pct != last[0]:
            print(f"\r  raw-flashing… {pct}%", end="", flush=True)
            last[0] = pct

    try:
        with QixTransport(args.mac) as t:
            sc = StagerClient(t, timeout=args.timeout)
            sc.flash_region(addr, data, chunk=args.chunk, erase=not args.no_erase,
                            verify=args.verify, on_progress=progress)
            print("\nraw-flash OK" + (" (verified)" if args.verify else ""))
            if args.reboot:
                print("rebooting badge…", file=sys.stderr)
                sc.reboot()
    except StagerError as e:
        print(f"\nraw-flash FAILED: {e}", file=sys.stderr)
        return 3
    return 0


def cmd_stager_read(args) -> int:
    """Read flash via the stager RAW_READ (0xD2) service — non-destructive.
    Confirms the 0xD0..0xD3 service is alive and lets you inspect any partition.
    --addr accepts an absolute hex addr or a partition name (ui_res/virfat/data)."""
    from qix_ble.stager import StagerClient, PARTITIONS
    from qix_ble.bond import prepare_fresh_pairing
    prepare_fresh_pairing(args.mac)   # HID badge → pair fresh on the connection (QIX_PAIR)

    addr = PARTITIONS[args.addr] if args.addr in PARTITIONS else int(args.addr, 0)
    total = args.length
    print(f"stager-read 0x{addr:x}"
          f"{' ['+args.addr+']' if args.addr in PARTITIONS else ''} len={total}",
          file=sys.stderr)
    with QixTransport(args.mac) as t:
        sc = StagerClient(t, timeout=args.timeout)
        out = bytearray()
        while len(out) < total:
            n = min(16, total - len(out))
            out += sc.read(addr + len(out), n)
    for i in range(0, len(out), 16):
        chunk = out[i:i + 16]
        hexs = " ".join(f"{b:02x}" for b in chunk)
        asci = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print(f"{addr + i:08x}  {hexs:<47}  {asci}")
    return 0


def cmd_stager_apply(args) -> int:
    """Apply a staged code .ufw (0xD4) — the stager-loader hands off to uboot,
    which writes the CODE partition and reboots into the new firmware.

    Full BLE-loader flow to push a custom firmware (no USB):
      1. qix rawflash <mac> flash2.bin     --addr ui_res        (resources)
      2. qix rawflash <mac> code_inner.ufw --addr 0x2F0000      (stage code)
      3. qix stager-apply <mac> 0x2F0000 <len>                  (apply -> reboot)
    code_inner.ufw = the JieLi .ufw WITHOUT the 27-byte Qix wrapper."""
    from qix_ble.stager import StagerClient
    from qix_ble.bond import prepare_fresh_pairing
    prepare_fresh_pairing(args.mac)   # HID badge → pair fresh on the connection (QIX_PAIR)

    addr = int(args.addr, 0)
    length = int(args.length, 0)
    print(f"stager-apply staged .ufw @0x{addr:x} len={length}", file=sys.stderr)
    with QixTransport(args.mac) as t:
        sc = StagerClient(t, timeout=args.timeout)
        st = sc.apply(addr, length)
    if st == 0:
        print("apply accepted — device verifying + resetting into uboot")
        return 0
    print(f"apply REJECTED st={st} "
          f"({'verify failed' if st == 2 else 'lc-update disabled' if st == 1 else st})",
          file=sys.stderr)
    return 3


def cmd_deploy_fw(args) -> int:
    """One-shot full-firmware BLE deploy via the stager-loader (no USB):
      1. (optional) raw-flash resources -> ui_res (0x17E000)
      2. raw-flash the code .ufw       -> staging (default 0x2F0000)
      3. apply (0xD4) -> the loader hands off to uboot, which writes the CODE
         partition and reboots into the new firmware.
    The code .ufw may be the wrapped UFW or the inner one — a 27-byte Qix wrapper
    is auto-stripped. Then wait for the RGB reboot and `qix scan` to confirm."""
    from qix_ble.stager import StagerClient, STAGING_ADDR, PARTITIONS
    from qix_ble.bond import prepare_fresh_pairing
    prepare_fresh_pairing(args.mac)   # HID badge → pair fresh on the connection (QIX_PAIR)

    code = Path(args.code).read_bytes()
    if code[:2] == b"\xbc\xaf":
        code = code[27:]
        print("  (stripped 27-byte Qix wrapper from code .ufw)", file=sys.stderr)
    res = Path(args.resources).read_bytes() if args.resources else None
    staging = int(args.staging_addr, 0)
    print(f"deploy-fw: code={len(code)}B staging=0x{staging:x}"
          + (f"  resources={len(res)}B -> ui_res" if res else "  (no resources)"),
          file=sys.stderr)

    pct = [-1]

    def progress(label):
        def cb(p):
            v = int(p * 100)
            if v != pct[0]:
                print(f"\r  {label}… {v}%", end="", flush=True)
                pct[0] = v
        return cb

    with QixTransport(args.mac) as t:
        sc = StagerClient(t, timeout=args.timeout)
        if res is not None:
            pct[0] = -1
            sc.flash_region(PARTITIONS["ui_res"], res, chunk=args.chunk,
                            erase=True, verify=args.verify, on_progress=progress("resources"))
            print(" ok")
        pct[0] = -1
        sc.flash_region(staging, code, chunk=args.chunk,
                        erase=True, verify=args.verify, on_progress=progress("staging code"))
        print(" ok")
        st = sc.apply(staging, len(code))

    if st == 0:
        print("apply accepted — device verifying + resetting into uboot")
        print("→ wait for the RGB reboot, then `qix scan` to confirm the new firmware")
        return 0
    print(f"apply REJECTED st={st}", file=sys.stderr)
    return 3


def _wait_for_device(mac: str, timeout: float, scan_window: float = 6.0):
    """Scan by MAC until the badge re-advertises after a reboot. Robust to variable
    boot time (vs a blind fixed delay). Returns the BLEDevice (has .name/.address)
    once seen, or None on timeout. The MAC is constant across OEM/stager/custom
    (it lives in the preserved key_mac@0x3ff000), so this works even as the
    advertised NAME changes E87 -> STAGER -> custom."""
    target = mac.lower()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            devs = QixTransport.scan(timeout=scan_window, all_devices=True)
        except Exception:
            devs = []
        for d in devs:
            if (getattr(d, "address", "") or "").lower() == target:
                return d
        print(f"\r  waiting for {mac} to come back…", end="", flush=True)
    return None


def cmd_provision(args) -> int:
    """Full from-OEM provision in ONE command (the real workflow, no USB):
      Phase 1 (--loader): OEM badge -> loader firmware via OEM lcflash (Qix
        REQ_UPDATE + chunks). The badge reboots into the loader (wait reboot-delay).
      Phase 2 (via the loader's 0xD0-0xD4):
        --resources flash2.bin   -> raw-flash to ui_res (0x17E000)
        --content FILE@ADDR ...  -> raw-flash each blob (ADDR = hex or partition name)
        --fw code.ufw            -> stage + apply (0xD4) -> reboot into the custom fw
        (no --fw: just reboot, to boot the content that was written).
    Example (cat firmware): provision <mac> --loader gato_in_jack.ufw
                                        --content gatito.bin@0x200000"""
    from qix_ble.auth import AuthSession
    from qix_ble.bootstrap import run_bootstrap
    from qix_ble.stager import StagerClient, PARTITIONS

    pct = [-1]

    def progress(label):
        def cb(p):
            v = int(p * 100)
            if v != pct[0]:
                print(f"\r  {label}… {v}%", end="", flush=True)
                pct[0] = v
        return cb

    # ── Phase 1: OEM -> loader (lcflash) ─────────────────────────────────────
    if args.loader:
        loader = Path(args.loader).read_bytes()
        validate_qix_wrapper(loader)
        print("=== Phase 1: OEM → loader (lcflash) ===", file=sys.stderr)
        with QixTransport(args.mac) as t:
            AuthSession(t, step_timeout=args.timeout).do_handshake()
            run_bootstrap(t)
            t.drain()
            t.drain_ae02_raw()
            print("auth + bootstrap OK", file=sys.stderr)
            pct[0] = -1
            QixUpdater(t).flash(loader, on_progress=progress("flashing loader"),
                                timeout_per_chunk=args.timeout, timeout_req=15.0)
        print(f"\nloader flashed — waiting for {args.mac} to reboot into the loader "
              f"(reboot #1)…", file=sys.stderr)
        if _wait_for_device(args.mac, args.reboot_timeout) is None:
            print(f"\ntimed out after {args.reboot_timeout:.0f}s — the badge didn't come "
                  f"back; it may still be booting (retry Phase 2 with `deploy-fw`)",
                  file=sys.stderr)
            return 4
        print(" back up — settling…", file=sys.stderr)
        time.sleep(args.settle)

    # ── Phase 2: deploy content + custom fw via the loader ───────────────────
    print("=== Phase 2: deploy via loader ===", file=sys.stderr)
    with QixTransport(args.mac) as t:
        sc = StagerClient(t, timeout=args.timeout)
        if args.resources:
            pct[0] = -1
            sc.flash_region(PARTITIONS["ui_res"], Path(args.resources).read_bytes(),
                            chunk=args.chunk, erase=True, on_progress=progress("resources"))
            print(" ok")
        for spec in (args.content or []):
            path, sep, addr_s = spec.rpartition("@")
            if not sep:
                print(f"bad --content {spec!r}; use FILE@ADDR", file=sys.stderr)
                return 2
            addr = PARTITIONS[addr_s] if addr_s in PARTITIONS else int(addr_s, 0)
            pct[0] = -1
            sc.flash_region(addr, Path(path).read_bytes(), chunk=args.chunk,
                            erase=True, on_progress=progress(f"content→0x{addr:x}"))
            print(" ok")
        if args.fw:
            code = Path(args.fw).read_bytes()
            if code[:2] == b"\xbc\xaf":
                code = code[27:]
            staging = int(args.staging_addr, 0)
            pct[0] = -1
            sc.flash_region(staging, code, chunk=args.chunk, erase=True,
                            on_progress=progress("staging code"))
            print(" ok")
            st = sc.apply(staging, len(code))
            if st != 0:
                print(f"apply REJECTED st={st}", file=sys.stderr)
                return 3
            print("apply accepted — device resetting into the custom firmware", file=sys.stderr)
        else:
            sc.reboot()
            print("rebooting to load the written content", file=sys.stderr)

    # ── reboot #2 (loader -> custom): wait for it to come back and confirm ───
    print(f"waiting for {args.mac} to reboot into the new firmware (reboot #2)…",
          file=sys.stderr)
    dev = _wait_for_device(args.mac, args.reboot_timeout)
    if dev is None:
        print(f"\nbadge not seen after {args.reboot_timeout:.0f}s (may still be booting; "
              f"`qix scan` to check)", file=sys.stderr)
        return 0
    print(f"\n✓ badge back up as '{dev.name or '?'}'")

    # The content/staging writes land in the resource (SDFILE) region, so the firmware
    # reconciles the FS on THIS first boot — which can leave the screen blank. One extra
    # clean reboot settles it so the first boot the user sees is already good.
    # (disable with --no-final-reboot)
    if args.final_reboot:
        print("settling + clean reboot so the screen comes up right…", file=sys.stderr)
        time.sleep(args.settle)
        try:
            with QixTransport(args.mac) as t:
                StagerClient(t, timeout=args.timeout).reboot()
        except Exception:
            pass  # device resets and drops the link — expected
        _wait_for_device(args.mac, args.reboot_timeout)
    print("✓ provision complete")
    return 0


def cmd_patchflash(args) -> int:
    """Drive the binary-PATCH raw-flash service (option A): write a file to ANY
    flash region via the injected 0xD0/0xD1 handler (fire-and-forget). Needs the
    jack_rawflash.ufw patch applied (badge advertising 'Jack')."""
    from qix_ble.patchflash import PatchClient

    data = Path(args.file).read_bytes()
    addr = int(args.addr, 0)
    print(f"patch-flash {args.file} ({len(data)} B) -> 0x{addr:x}", file=sys.stderr)
    last = [-1]

    def progress(p: float):
        pct = int(p * 100)
        if pct != last[0]:
            print(f"\r  writing… {pct}%", end="", flush=True)
            last[0] = pct

    with QixTransport(args.mac) as t:
        if not args.no_bootstrap:
            from qix_ble.auth import AuthSession
            from qix_ble.bootstrap import run_bootstrap
            AuthSession(t, step_timeout=args.timeout).do_handshake()
            run_bootstrap(t)
            t.drain(); t.drain_ae02_raw()
            print("auth + bootstrap OK", file=sys.stderr)
        PatchClient(t, settle=args.settle).flash_region(
            addr, data, chunk=args.chunk, erase=not args.no_erase, on_progress=progress)
    print("\npatch-flash sent (fire-and-forget) — verify via USB dump")
    return 0


def cmd_rcsp_flash(args) -> int:
    """Native JieLi RCSP OTA over AE00 (NOT Qix/FD00). For firmwares that do RCSP
    OTA (e.g. the e_badge_707_sdk_200 "EC-BADGE" build).

    --probe-only: E1+E2 only — ZERO flash write. Safe validation that the device
    accepts the .ufw (auth ok, version/authkey/crc parsed, can_update reported).
    Default (no --probe-only): full transfer + reboot.
    """
    import json as _json
    import os as _os
    from qix_ble.auth import AuthSession
    from qix_ble.rcsp_ota import RcspOtaUpdater, E2_CAN_UPDATE, E6_RESULT

    # The badge is BLE-HID → BlueZ pairs on connect, and the badge only encrypts after
    # pairing. Its firmware clears its own key on each new connection, so a STORED bond
    # makes the reconnect fail (PIN_KEY_MISS). Fix: forget any stored bond, bring up a
    # Just-Works agent, and pair FRESH on the OTA connection itself (QIX_PAIR=1) so the
    # OTA runs on that same encrypted link — no reconnect. Opt out with QIX_NO_PAIR=1.
    _stop_agent = (lambda: None)
    if sys.platform.startswith("linux") and not _os.environ.get("QIX_NO_PAIR"):
        try:
            from qix_ble.bluez_cleanup import remove_device
            from qix_ble.bond import start_persistent_agent
            remove_device(args.mac)               # forget stale bond → pair fresh
            # Opt-in pre-flight (QIX_BREDR_OFF=1): BR/EDR off + tight LE conn interval so
            # the HID badge stops page-timing-out over classic and starving the LE OTA.
            if _os.environ.get("QIX_BREDR_OFF"):
                from qix_ble.bluez_cleanup import set_bredr_off, set_le_conn_interval
                set_bredr_off()
                set_le_conn_interval()
            _stop_agent = start_persistent_agent()
            _os.environ["QIX_PAIR"] = "1"
        except Exception as e:
            print(f"pairing-agent setup skipped: {e}", file=sys.stderr)

    ufw_path = Path(args.ufw)
    ufw = ufw_path.read_bytes()
    print(f"update file: {ufw_path.name} ({len(ufw)} bytes)", file=sys.stderr)

    # ── probe-only: single connection, ZERO flash write ─────────────────────
    if args.probe_only:
        with QixTransport(args.mac) as t:
            if not args.no_auth:
                AuthSession(t, step_timeout=args.timeout).do_handshake()
                print("auth OK (6-step JieLi handshake)", file=sys.stderr)
            r = RcspOtaUpdater(t).probe(ufw, timeout=args.timeout)
        desc = E2_CAN_UPDATE.get(r.can_update, "unknown")
        if args.json:
            print(_json.dumps({
                "ufw": ufw_path.name, "info_offset": r.info_offset,
                "info_len": r.info_len, "mark": r.mark.hex(),
                "can_update": r.can_update, "can_update_desc": desc,
                "accepted": r.accepted, "dt_ms": round(r.dt_ms, 1),
            }))
        else:
            print(f"E1 file-info: offset=0x{r.info_offset:x} len={r.info_len}")
            print(f"E1 mark block: {r.mark.hex()}")
            print(f"E2 can_update: 0x{r.can_update:02x} ({desc})")
            print(f"probe {'ACCEPTED ✓' if r.accepted else 'rejected'} "
                  f"({r.dt_ms:.0f} ms) — zero flash write")
        return 0 if r.accepted else 3

    # ── full flash: this firmware's OTA is TWO passes (single-bank + loader) ──
    #   pass 1: the running app pulls the LOADER, verifies it, reboots into it (E6=0x80)
    #   pass 2: the loader re-advertises and pulls the real firmware (E6=0x00)
    # The device reboots between passes (dropping the link), so we reconnect.
    MAX_PASSES = 5
    for attempt in range(1, MAX_PASSES + 1):
        last = [-1]

        def progress(p: float, _a=attempt):
            pct = int(p * 100)
            if pct != last[0]:
                print(f"\r  [pass {_a}] flashing… {pct}%", end="", flush=True)
                last[0] = pct

        try:
            with QixTransport(args.mac) as t:
                if not args.no_auth:
                    AuthSession(t, step_timeout=args.timeout).do_handshake()
                    print(f"[pass {attempt}] auth OK", file=sys.stderr)
                result = RcspOtaUpdater(t).flash(ufw, on_progress=progress, timeout=args.timeout)
        except (BleConnectionError, QixTimeoutError) as e:
            print()
            if attempt == 1:
                raise  # a pass-1 failure is a real error — let main() report it
            print(f"[pass {attempt}] reconnect/transfer hiccup ({e}) — the device may "
                  f"still be rebooting; retrying in {args.relink_delay:.0f}s…", file=sys.stderr)
            time.sleep(args.relink_delay)
            continue

        print()
        desc = E6_RESULT.get(result, "unknown")
        print(f"[pass {attempt}] result: E6=0x{result:02x} ({desc})")
        if result == 0x00:
            print("OTA SUCCESS ✓ — the device will reboot into the new firmware 🎉")
            return 0
        if result == 0x80:
            # Pass 1 done: the app staged the loader and (on our disconnect in
            # RcspOtaUpdater._finish) armed + rebooted INTO the loader. The loader's GATT
            # is AE00 only and it has NO SMP — so pass 2 must connect PLAIN: forget the
            # bond and stop pairing (a stale LTK / a Pair() attempt makes the loader drop
            # the link → "failed to discover services"). Auth IS still required (the loader
            # runs the same 6-step handshake). This switch is what makes the apply persist.
            print(f"loader staged — switching to PLAIN (no-pair) reconnect and waiting "
                  f"{args.relink_delay:.0f}s for the loader to boot…", file=sys.stderr)
            _stop_agent()
            _os.environ.pop("QIX_PAIR", None)
            _os.environ["QIX_NO_PAIR"] = "1"
            if sys.platform.startswith("linux"):
                try:
                    from qix_ble.bluez_cleanup import remove_device
                    remove_device(args.mac)
                except Exception as e:
                    print(f"bond forget skipped: {e}", file=sys.stderr)
            time.sleep(args.relink_delay)
            continue
        print(f"OTA FAILED at E6=0x{result:02x} ({desc})", file=sys.stderr)
        return 3

    print(f"reached MAX_PASSES={MAX_PASSES} without an 0x00 success", file=sys.stderr)
    return 3


def cmd_listen(args) -> int:
    with QixTransport(args.mac) as t:
        end = time.time() + args.timeout
        while time.time() < end:
            frame = t.recv_frame(timeout=1.0)
            if frame:
                print(fmt_frame(frame))
    return 0


def cmd_cleanup(args) -> int:
    """Limpia state zombie de BlueZ para `mac`. No conecta al badge.

    Por default solo llama Device1.Disconnect() (idempotente). Con --remove
    además invoca Adapter1.RemoveDevice() — BlueZ olvida el device, próxima
    sesión necesita re-descubrir."""
    from qix_ble.bluez_cleanup import force_disconnect, remove_device
    disc = force_disconnect(args.mac, timeout=args.timeout)
    print(f"Device1.Disconnect({args.mac}): {'desconectado' if disc else 'ya estaba limpio'}")
    if args.remove:
        rem = remove_device(args.mac, timeout=args.timeout)
        print(f"Adapter1.RemoveDevice({args.mac}): {'removido' if rem else 'no presente'}")
    return 0


def cmd_bond(args) -> int:
    """Establece un bond PERSISTENTE y trusted con el badge (Just Works, baile 2-conn).

    Se corre UNA vez: el badge es HID, BlueZ empareja al descubrirlo, y su SM solo
    encripta en una RECONEXIÓN sobre un bond existente. Este comando crea+persiste ese
    bond; después `rcsp-flash` reconecta encriptado (sub=01) y el OTA funciona. Requiere
    el badge anunciando (pantalla Actualizar). Linux/BlueZ."""
    from qix_ble.bond import bond
    ok = bond(args.mac, retries=args.retries, timeout=args.timeout)
    if ok:
        print(f"✓ bond persistente establecido con {args.mac} — ya podés correr rcsp-flash")
        return 0
    print(f"✗ no se pudo bondear {args.mac} (¿anunciando? ¿batería ok?)", file=sys.stderr)
    return 3


def cmd_reset(args) -> int:
    """Trigger badge soft-reset.

    Mecanismos disponibles:
      1. cmd 0xA8 TEST_RESTART (default) — Qix test command, fire-and-forget.
         ⚠️ Empíricamente GATED en E87 OEM production firmware "11.1.0.4"
         (2026-05-16): badge ACK-ea con `0xFF a8 00` pero NO ejecuta restart.
         Probable requiere `cmd 0xA0 SET_TEST_MODE` previo, que también es gated.
         Mantenido por si funciona en otros badges JieLi (DG01, futuras builds).

      2. --via-ota: REQ_UPDATE handshake + disconnect → badge timeout-reset.
         ✅ Validado FUNCIONAR en E87 OEM 2026-05-16. Necesita UFW con wrapper
         Qix válido + auth+bootstrap. Reset ocurre ~5-30s tras disconnect del
         laptop (badge sale del estado "waiting OTA chunks" via timeout).
         Zero flash write garantizado (NUNCA envía 0xC2 SEND_UPDATE_DATA).
    """
    from qix_ble.transport import QixTransport
    from qix_ble.frame import QixFrame

    if args.via_ota:
        if args.ufw is None:
            print("ERROR: --via-ota requires --ufw <path-to-valid-Qix-wrapped-UFW>", file=sys.stderr)
            return 2
        # Reuso del path flash --probe-only --bootstrap. Más simple invocar
        # directo update_manager logic.
        from qix_ble.auth import AuthSession
        from qix_ble.bootstrap import run_bootstrap
        from qix_ble.update_manager import QixUpdater, validate_qix_wrapper
        ufw = args.ufw.read_bytes()
        if not args.no_validate:
            validate_qix_wrapper(ufw)
        with QixTransport(args.mac) as t:
            if args.bootstrap:
                AuthSession(t, step_timeout=args.timeout).do_handshake()
                run_bootstrap(t)
                t.drain()
                t.drain_ae02_raw()
            try:
                result = QixUpdater(t).probe(ufw, timeout=args.timeout)
                print(f"REQ_UPDATE handshake: state={result.state} "
                      f"allow_len={result.allow_len} offset={result.offset} "
                      f"accepted={result.accepted}")
            except Exception as e:
                print(f"REQ_UPDATE handshake error: {e}")
        print("disconnected — badge debería rebootear por timeout OTA en ~5-30s")
        return 0

    # Default: cmd 0xA8 TEST_RESTART
    CMD_TEST_RESTART = 0xA8
    with QixTransport(args.mac) as t:
        frame = QixFrame(flags=0x00, cmd=CMD_TEST_RESTART, payload=b"")
        print(f"TX cmd=0xA8 TEST_RESTART (fire-and-forget): {frame.encode().hex()}")
        t.send_raw(frame)
        # Wait briefly for any acknowledgement (badge may push 0xFF ack o disconnect directly)
        time.sleep(0.5)
        try:
            while True:
                f = t.recv_frame(timeout=1.0)
                if f is None:
                    break
                print(f"  RX {fmt_frame(f)}")
        except Exception:
            pass
    print("disconnected — badge debería rebootear inmediato (TEST_RESTART) "
          "o en ~5-30s si esa cmd está gated en este FW")
    return 0


# ─── argparse ───────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="qix", description="Cliente BLE Qix (badges com.qix.library)")
    p.add_argument("-v", "--verbose", action="count", default=0, help="verbose: -v=INFO, -vv=DEBUG")
    p.add_argument("--log", type=Path, default=None, help="tee logs to file")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("scan", help="scan BLE devices con SERVICE_UUID Qix")
    sp.add_argument("--timeout", type=float, default=10.0)
    sp.add_argument("--all", action="store_true", help="no filtrar por SERVICE_UUID")
    sp.set_defaults(func=cmd_scan)

    sp = sub.add_parser("info", help="REQ_BADGE_INFO 0xC6")
    sp.add_argument("mac")
    sp.add_argument("--timeout", type=float, default=5.0)
    sp.set_defaults(func=cmd_info)

    sp = sub.add_parser("battery", help="query battery via device push post-bootstrap (E87 OEM)")
    sp.add_argument("mac")
    sp.add_argument("--timeout", type=float, default=5.0,
                    help="listen seconds for device push")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--sysinfo", action="store_true",
                    help="use RCSP sys_info attr 0 (DEPRECATED — fake 0x00 en E87 OEM)")
    sp.add_argument("--legacy", action="store_true",
                    help="use Qix cmd 0x27 standalone (ROTO en E87 OEM, dejado para SDKs antiguos)")
    sp.add_argument("--no-bootstrap", dest="bootstrap", action="store_false",
                    help="skip bootstrap (solo aplica a --legacy)")
    sp.set_defaults(func=cmd_battery, bootstrap=True)

    sp = sub.add_parser("bootstrap",
                        help="run Phase 1-5 community bootstrap (post-auth, pre-upload)")
    sp.add_argument("mac")
    sp.add_argument("--timeout", type=float, default=5.0)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_bootstrap)

    # ── push ─── upload de imagen/video/text/pattern al badge ─────────────
    sp = sub.add_parser("push", help="upload content al badge")
    sub_push = sp.add_subparsers(dest="push_cmd", required=True)

    def _add_common_push_args(parser):
        parser.add_argument("mac")
        parser.add_argument("--width", type=int, default=360)
        parser.add_argument("--height", type=int, default=360)
        parser.add_argument("--name", default="qix_upload",
                            help="display filename del upload")
        parser.add_argument("--timeout", type=float, default=5.0)

    sp_img = sub_push.add_parser("image", help="push single image (JPEG/PNG)")
    _add_common_push_args(sp_img)
    sp_img.add_argument("path")
    sp_img.add_argument("--fit", choices=["cover", "contain", "stretch"], default="cover")
    sp_img.add_argument("--zoom", type=float, default=1.0)
    sp_img.set_defaults(func=cmd_push_image)

    sp_vid = sub_push.add_parser("video", help="push GIF/APNG/WebP/AVI animation")
    _add_common_push_args(sp_vid)
    sp_vid.add_argument("path")
    sp_vid.add_argument("--fps", type=int, default=12)
    sp_vid.add_argument("--duration", type=float, default=None,
                        help="cap segundos del animation")
    sp_vid.set_defaults(func=cmd_push_video)

    sp_txt = sub_push.add_parser("text", help="push text animation")
    _add_common_push_args(sp_txt)
    sp_txt.add_argument("text")
    sp_txt.add_argument("--mode", choices=["scroll", "static", "pulse", "wave"],
                        default="scroll")
    sp_txt.add_argument("--fps", type=int, default=12)
    sp_txt.add_argument("--duration", type=float, default=5.0)
    sp_txt.add_argument("--font-size", type=int, default=64)
    sp_txt.add_argument("--bold", action="store_true")
    sp_txt.add_argument("--font", help="path al .ttf/.ttc font file")
    sp_txt.set_defaults(func=cmd_push_text)

    sp_pat = sub_push.add_parser("pattern", help="push procedural pattern animation")
    _add_common_push_args(sp_pat)
    sp_pat.add_argument("pattern",
                        choices=["gradient", "pulse", "checker", "rainbow",
                                 "wave", "plasma_waves", "concentric_waves"])
    sp_pat.add_argument("--fps", type=int, default=12)
    sp_pat.add_argument("--frames", type=int, default=60)
    sp_pat.set_defaults(func=cmd_push_pattern)

    sp_ba = sub_push.add_parser("bootanim",
        help="EXPERIMENTAL — push boot animation (RGB565 raw, no JPEG)")
    _add_common_push_args(sp_ba)
    sp_ba.add_argument("path", help="imagen (PNG/JPEG) para usar como splash boot")
    sp_ba.add_argument("--fit", choices=["cover", "contain", "stretch"], default="cover")
    sp_ba.add_argument("--query-only", action="store_true",
        help="solo query badge metadata (cmd 0x8A read-only, no escribe nada)")
    sp_ba.set_defaults(func=cmd_push_bootanim)

    # ── dump-health ─── dump histórico steps/sleep/hr/oxygen/pressure/battery
    sp_dh = sub.add_parser("dump-health",
        help="dump histórico de health data (cmd 0x29 → 0x21-0x27 state machine)")
    sp_dh.add_argument("mac")
    sp_dh.add_argument("--types", default="all",
        help="csv de tipos: steps,sleep,hr,pressure,oxygen,battery (o 'all' = mask 0xBF)")
    sp_dh.add_argument("--bootstrap", action="store_true", default=False,
        help="hacer auth + bootstrap Phase 1-5 antes (default solo BIND)")
    sp_dh.add_argument("--json", action="store_true",
        help="output como JSON estructurado")
    sp_dh.add_argument("--timeout", type=float, default=10.0,
        help="timeout per-frame entre RX consecutivos")
    sp_dh.add_argument("--total-timeout", type=float, default=60.0,
        help="timeout total del dump")
    sp_dh.set_defaults(func=cmd_dump_health)

    sp = sub.add_parser("bind", help="bind phone-to-badge (cmd 0x60), parse fw/serial/ui")
    sp.add_argument("mac")
    sp.add_argument("--lang", choices=["zh", "en"], default="en")
    sp.add_argument("--hour12", action="store_true",
                    help="usar 12h format (default 24h)")
    sp.add_argument("--device-id", type=lambda s: int(s, 0), default=None,
                    help="device_id custom (default: hash de platform.uname)")
    sp.add_argument("--timeout", type=float, default=5.0)
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--no-bootstrap", dest="bootstrap", action="store_false",
                    help="skip auth+bootstrap (default: run; bind suele estar gated sin bootstrap)")
    sp.set_defaults(func=cmd_bind, bootstrap=True)

    sp = sub.add_parser("unbind", help="unbind cmd 0x62 (fire-and-forget)")
    sp.add_argument("mac")
    sp.add_argument("--timeout", type=float, default=5.0)
    sp.add_argument("--bootstrap", action="store_true",
                    help="run auth+bootstrap before (default off, unbind no parece requerirlo)")
    sp.set_defaults(func=cmd_unbind, bootstrap=False)

    sp = sub.add_parser("sysinfo",
                        help="RCSP target_info (cmd 0x03) + sys_info (cmd 0x07), post-auth")
    sp.add_argument("mac")
    sp.add_argument("--kind", choices=["target", "sys", "both"], default="both")
    sp.add_argument("--timeout", type=float, default=5.0)
    sp.add_argument("--json", action="store_true", help="output JSON")
    sp.set_defaults(func=cmd_sysinfo)

    sp = sub.add_parser("raw", help="send arbitrary command")
    sp.add_argument("mac")
    sp.add_argument("cmd_hex", help="opcode hex (e.g. 0xC6 o C6)")
    sp.add_argument("payload_hex", nargs="?", default="", help="payload hex (optional)")
    sp.add_argument("--timeout", type=float, default=5.0)
    sp.set_defaults(func=cmd_raw)

    sp = sub.add_parser("dump", help="dump via TEST_GET_FLASH/MEMORY/RAM")
    sp.add_argument("mac")
    sp.add_argument("--type", choices=["flash", "memory", "ram"], required=True)
    sp.add_argument("--addr", type=lambda s: int(s, 0), default=0)
    sp.add_argument("--length", type=lambda s: int(s, 0), required=True, help="bytes to dump")
    sp.add_argument("--output", required=True)
    sp.add_argument("--chunk-size", type=int, default=256)
    sp.add_argument("--timeout", type=float, default=5.0)
    sp.add_argument("--chunk-timeout", type=float, default=10.0)
    sp.add_argument("--auth", action="store_true",
                    help="hacer JieLi RCSP handshake antes de SET_TEST_MODE (firmware 1.x+)")
    sp.add_argument("--bootstrap", action="store_true",
                    help="run bootstrap Phase 1-5 después de auth — empíricamente "
                         "necesario en E87 production para desbloquear TEST_GET_* "
                         "(per feedback_vector_a1_gated_post_bootstrap)")
    sp.set_defaults(func=cmd_dump)

    sp = sub.add_parser("auth", help="run JieLi RCSP 6-step handshake aislado (debug)")
    sp.add_argument("mac")
    sp.add_argument("--timeout", type=float, default=5.0, help="per-step timeout")
    sp.set_defaults(func=cmd_auth)

    sp = sub.add_parser("raw-rcsp",
                        help="send arbitrary RCSP frame (debug / probe)")
    sp.add_argument("mac")
    sp.add_argument("flag_hex", help="RCSP flag byte hex (e.g. 0xc0 o c0)")
    sp.add_argument("cmd_hex", help="RCSP cmd byte hex")
    sp.add_argument("payload_hex", nargs="?", default="",
                    help="payload hex (optional, espacios ignorados)")
    sp.add_argument("--no-auth", action="store_true",
                    help="skip JieLi handshake before sending (default: ensure auth)")
    sp.add_argument("--timeout", type=float, default=5.0)
    sp.set_defaults(func=cmd_raw_rcsp)

    # ── fs ─── browse + download files post-auth via RCSP service AE00 ──────
    sp = sub.add_parser("fs", help="filesystem ops (post-auth)")
    sub_fs = sp.add_subparsers(dest="fs_cmd", required=True)

    sp_ls = sub_fs.add_parser("ls", help="list files del FS del badge")
    sp_ls.add_argument("mac")
    sp_ls.add_argument("--dev", choices=["USB", "SD0", "SD1", "FLASH", "ALL"], default="ALL")
    sp_ls.add_argument("--type", type=int, default=0, help="0=folders, 1=files")
    sp_ls.add_argument("--read-num", type=int, default=10)
    sp_ls.add_argument("--json", action="store_true", help="output JSON")
    sp_ls.add_argument("--timeout", type=float, default=5.0)
    sp_ls.set_defaults(func=cmd_fs_ls)

    sp_get = sub_fs.add_parser("get", help="download archivo por ID")
    sp_get.add_argument("mac")
    sp_get.add_argument("id_hex", help="file ID hex (e.g., 0x000a)")
    sp_get.add_argument("--dev", choices=["USB", "SD0", "SD1", "FLASH"], required=True)
    sp_get.add_argument("--type", type=int, default=1, help="0=folder, 1=file (default file)")
    sp_get.add_argument("--output", "-o", required=True)
    sp_get.add_argument("--timeout", type=float, default=5.0)
    sp_get.set_defaults(func=cmd_fs_get)

    sp = sub.add_parser("flash", help="OTA flash via UpdateManager")
    sp.add_argument("mac")
    sp.add_argument("wrapped", help="path al .ufw con wrapper Qix")
    sp.add_argument("--no-validate", action="store_true")
    sp.add_argument("--probe-only", action="store_true",
                    help="ZERO flash write — solo REQ_UPDATE handshake, exit 0 si accepted, 3 si rejected")
    sp.add_argument("--bootstrap", action="store_true",
                    help="run auth+bootstrap Phase 1-5 antes (probe-only). Test si bootstrap desbloquea Vector A1 que era GATED")
    sp.add_argument("--oem", action="store_true",
                    help="OEM-compat: auth+bootstrap + longer REQ_UPDATE timeout (the stock "
                         "firmware reacts to REQ_UPDATE in ~6s). Use for the first flash onto a stock badge.")
    sp.add_argument("--req-timeout", type=float, default=None,
                    help="seconds to wait for RET_UPDATE after REQ_UPDATE (default: --timeout, or 15s with --oem)")
    sp.add_argument("--timeout", type=float, default=5.0)
    sp.set_defaults(func=cmd_flash, bootstrap=False)

    sp = sub.add_parser("rawflash",
                        help="stager raw-flash: write ANY partition (ui_res/virfat/…) via the "
                             "custom 0xD0..0xD3 commands (needs STAGER firmware loaded)")
    sp.add_argument("mac")
    sp.add_argument("file", help="binary to write")
    sp.add_argument("--addr", required=True,
                    help="absolute flash addr (0x17E000) or partition name: ui_res / virfat / data")
    sp.add_argument("--chunk", type=int, default=224, help="bytes per RAW_WRITE (capped to MTU)")
    sp.add_argument("--no-erase", action="store_true", help="skip erase (region already erased)")
    sp.add_argument("--verify", action="store_true", help="sample-verify via RAW_READ after write")
    sp.add_argument("--reboot", action="store_true", help="reboot the badge when done")
    sp.add_argument("--timeout", type=float, default=6.0)
    sp.set_defaults(func=cmd_rawflash)

    sp = sub.add_parser("stager-read",
                        help="read flash via stager RAW_READ 0xD2 (non-destructive; "
                             "needs STAGER firmware loaded)")
    sp.add_argument("mac")
    sp.add_argument("addr", help="absolute hex addr (0x17E000) or partition name: "
                                 "ui_res / virfat / data")
    sp.add_argument("length", type=lambda s: int(s, 0), help="bytes to read")
    sp.add_argument("--timeout", type=float, default=6.0)
    sp.set_defaults(func=cmd_stager_read)

    sp = sub.add_parser("provision",
                        help="full from-OEM provision in one command: OEM->loader (lcflash) "
                             "then deploy content/custom-fw via the loader")
    sp.add_argument("mac")
    sp.add_argument("--loader", help="loader .ufw (Qix-wrapped) to flash via OEM lcflash (Phase 1)")
    sp.add_argument("--fw", help="custom firmware code .ufw to stage+apply via the loader (Phase 2)")
    sp.add_argument("--content", action="append", metavar="FILE@ADDR",
                    help="content blob to raw-flash (repeatable); ADDR = hex or partition "
                         "name (ui_res/virfat/data), e.g. gatito.bin@0x200000")
    sp.add_argument("--resources", help="flash2.bin -> ui_res (0x17E000)")
    sp.add_argument("--staging-addr", default="0x2F0000")
    sp.add_argument("--reboot-timeout", type=float, default=90.0,
                    help="max seconds to wait for the badge to re-advertise after each "
                         "reboot (scans by MAC; default 90)")
    sp.add_argument("--settle", type=float, default=3.0,
                    help="seconds to let GATT stabilise after the badge reappears (default 3)")
    sp.add_argument("--no-final-reboot", dest="final_reboot", action="store_false",
                    help="skip the automatic clean reboot at the end (the first boot after a "
                         "flash reconciles the resource FS and can show a blank screen)")
    sp.add_argument("--chunk", type=int, default=224)
    sp.add_argument("--timeout", type=float, default=8.0)
    sp.set_defaults(func=cmd_provision, final_reboot=True)

    sp = sub.add_parser("deploy-fw",
                        help="one-shot BLE full-firmware deploy via stager-loader: "
                             "resources -> ui_res, stage code, apply (0xD4) -> reboot")
    sp.add_argument("mac")
    sp.add_argument("code", help="code .ufw (wrapped or inner; Qix wrapper auto-stripped)")
    sp.add_argument("--resources", help="flash2.bin resources image -> ui_res (0x17E000)")
    sp.add_argument("--staging-addr", default="0x2F0000",
                    help="scratch addr for the staged code .ufw (default 0x2F0000)")
    sp.add_argument("--chunk", type=int, default=224)
    sp.add_argument("--verify", action="store_true", help="sample-verify each raw write")
    sp.add_argument("--timeout", type=float, default=8.0)
    sp.set_defaults(func=cmd_deploy_fw)

    sp = sub.add_parser("stager-apply",
                        help="apply a staged code .ufw (0xD4): stager-loader hands off to "
                             "uboot to write CODE + reboot into new firmware")
    sp.add_argument("mac")
    sp.add_argument("addr", help="flash addr where the code .ufw was staged (e.g. 0x2F0000)")
    sp.add_argument("length", help="length of the staged .ufw in bytes")
    sp.add_argument("--timeout", type=float, default=8.0)
    sp.set_defaults(func=cmd_stager_apply)

    sp = sub.add_parser("patchflash",
                        help="binary-patch raw-flash (option A): write ANY region via the "
                             "injected 0xD0/0xD1 handler (fire-and-forget; needs jack_rawflash.ufw applied)")
    sp.add_argument("mac")
    sp.add_argument("file")
    sp.add_argument("--addr", required=True, help="absolute flash addr, e.g. 0x300000")
    sp.add_argument("--chunk", type=int, default=160, help="bytes per RAW_WRITE")
    sp.add_argument("--no-erase", action="store_true")
    sp.add_argument("--no-bootstrap", action="store_true", help="skip auth+bootstrap")
    sp.add_argument("--settle", type=float, default=0.04, help="delay after each op (s)")
    sp.add_argument("--timeout", type=float, default=8.0)
    sp.set_defaults(func=cmd_patchflash)

    sp = sub.add_parser("rcsp-flash",
                        help="native JieLi RCSP OTA over AE00 (for EC-BADGE / SDK builds, NOT Qix/FD00)")
    sp.add_argument("mac")
    sp.add_argument("ufw", help="path al update.ufw (RCSP/JieLi, no Qix wrapper)")
    sp.add_argument("--probe-only", action="store_true",
                    help="E1+E2 only — ZERO flash write; exit 0 if device accepts, 3 if not")
    sp.add_argument("--no-auth", action="store_true",
                    help="skip the JieLi handshake (only if firmware built with BT_CONNECTION_VERIFY=1)")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--timeout", type=float, default=8.0)
    sp.add_argument("--relink-delay", type=float, default=10.0,
                    help="seconds to wait for the device to reboot into the loader "
                         "between OTA passes (single-bank + loader is a 2-pass flow)")
    sp.set_defaults(func=cmd_rcsp_flash)

    sp = sub.add_parser("cleanup",
                        help="limpia state zombie de BlueZ (Device1.Disconnect) sin conectar")
    sp.add_argument("mac")
    sp.add_argument("--remove", action="store_true",
                    help="además llama Adapter1.RemoveDevice() — nuclear, BlueZ olvida el device")
    sp.add_argument("--timeout", type=float, default=1.5,
                    help="timeout DBus call (default 1.5s)")
    sp.set_defaults(func=cmd_cleanup)

    sp = sub.add_parser("bond",
                        help="establece un bond persistente+trusted (correr 1 vez antes de rcsp-flash)")
    sp.add_argument("mac")
    sp.add_argument("--retries", type=int, default=5,
                    help="intentos del baile de 2 conexiones (default 5)")
    sp.add_argument("--timeout", type=float, default=20.0,
                    help="timeout por operación DBus (default 20s)")
    sp.set_defaults(func=cmd_bond)

    sp = sub.add_parser("listen", help="passive: print every frame received")
    sp.add_argument("mac")
    sp.add_argument("--timeout", type=float, default=60.0)
    sp.set_defaults(func=cmd_listen)

    # soft-reset (reboot del SoC, NO factory reset — no toca user data/pairing/flash)
    for name in ("soft-reset", "reset"):  # reset = deprecated alias
        sp = sub.add_parser(name,
                            help="soft-reset (reboot SoC, no toca flash/pairing/config)")
        sp.add_argument("mac")
        sp.add_argument("--timeout", type=float, default=5.0)
        sp.add_argument("--via-ota", action="store_true",
                        help="✅ RECOMENDADO en E87 OEM production: REQ_UPDATE handshake + "
                             "disconnect → badge timeout-reset ~5-30s. Necesita --ufw con wrapper "
                             "Qix válido. Zero flash write.")
        sp.add_argument("--ufw", type=Path,
                        help="UFW path para --via-ota")
        sp.add_argument("--no-validate", action="store_true",
                        help="skip wrapper validation (solo --via-ota)")
        sp.add_argument("--bootstrap", action="store_true", default=True,
                        help="run auth+bootstrap antes (solo --via-ota)")
        sp.add_argument("--no-bootstrap", dest="bootstrap", action="store_false")
        sp.set_defaults(func=cmd_reset)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(args.verbose, args.log)
    try:
        return args.func(args)
    except UfwInvalid as e:
        print(f"ufw inválido: {e}", file=sys.stderr)
        return 4
    except (BleConnectionError, QixTimeoutError) as e:
        print(f"BLE/timeout error: {e}", file=sys.stderr)
        return 2
    except BadgeRejected as e:
        print(f"badge rechazó: {e}", file=sys.stderr)
        return 3
    except QixError as e:
        print(f"qix error: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
