#!/usr/bin/env python3
"""Check if firmware OEM (and others) implement the SCSI custom hook
that calls go_mask_usb_updata(). Multiple evidence vectors."""
import struct
from pathlib import Path

# Repo root, anchored on this file (scripts/maskrom-analysis/ → parents[2]).
REPO = Path(__file__).resolve().parents[2]

TARGETS = {
    "OEM_pid1558_V1.0.3":  REPO / "hw-sessions/2026-05-17/pid1558-firmware-rev/app.bin",
    "OEM_pid1581_Alipay":  REPO / "hw-sessions/2026-05-16/libjl_ota_auth-reverse/ufw-unpacked/cloud_inner/files/app.bin",
    "SDK_self_built":      REPO / "hw-sessions/2026-05-16/libjl_ota_auth-reverse/ufw-unpacked/self_built/files/app.bin",
}

# Strings únicos que aparecen si el SCSI custom hook está compilado
STRING_PROBES = [
    b"goto mask pc mode",       # log_d en private_scsi_cmd
    b"go_mask_usb_updata",      # symbol name si hay debug info
    b"goto mask",                # variante shorter
    b"mask pc mode",
    b"UPGRADE_USB_SOFT",
    b"PC_UPDATA",
    b"USB_UPDATA",
    b"usb updata",              # log printf de dev_update.c
    b"private_scsi_cmd",
    b"msd_upgrade",
    b"usb_g_hold",              # otro symbol del path
    b"ram_protect_close",
]

# Direcciones MaskROM clave del path go_mask_usb_updata
MASKROM_ADDRS = {
    0xffe04: "nvram_set_boot_state",   # ← LA crítica, set NVRAM flag
    0xffe08: "chip_reset",              # alternativa a cpu_reset
    0xffe30: "usb_slave_mode",          # USB slave entry
    0xffdfc: "mask_init",
}

# Constantes que aparecen si UPGRADE_USB_SOFT_KEY o UPDATA_MAGIC están usados
CONSTANTS = {
    0x5A00: "UPDATA_MAGIC / USB_UPDATA",
    0x5A03: "PC_UPDATA",
    0x5A06: "BLE_APP_UPDATA",
}

def scan(path):
    blob = Path(path).read_bytes()
    print(f"\n{'='*70}\n  {Path(path).parent.parent.name}/{Path(path).name}  ({len(blob)} bytes)\n{'='*70}")

    print(f"\n[Strings probes — evidence del SCSI hook compilado]")
    found = False
    for s in STRING_PROBES:
        c = blob.count(s)
        if c > 0:
            found = True
            i = blob.find(s)
            print(f"  HIT  '{s.decode():28s}'  count={c}  first @ 0x{i:06x}")
    if not found:
        print(f"  (no hits — SCSI hook strings NOT compiled in)")

    print(f"\n[MaskROM address xrefs (literal pool 4-byte LE)]")
    for addr, name in MASKROM_ADDRS.items():
        needle = struct.pack("<I", addr)
        c = blob.count(needle)
        marker = "**" if c > 0 else "  "
        print(f"  {marker} 0x{addr:06x}  {name:25s}  count={c}")

    print(f"\n[Magic constants from update.h enum (4-byte LE)]")
    for v, name in CONSTANTS.items():
        # u16 LE
        n2 = struct.pack("<H", v)
        c2 = blob.count(n2)
        # u32 LE
        n4 = struct.pack("<I", v)
        c4 = blob.count(n4)
        print(f"  0x{v:04x}  {name:30s}  u16_LE={c2}  u32_LE={c4}")

for path in TARGETS.values():
    scan(path)
