#!/usr/bin/env python3
"""One-shot: parsea CommandCode.java decompiled y emite qix_ble/opcodes.py.

Usage:
    python tools/qix-ble/scripts/extract_opcodes.py

Lee:  decompiled/zrun/sources/com/qix/library/command/CommandCode.java
Escribe: tools/qix-ble/qix_ble/opcodes.py
"""
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC = REPO_ROOT / "decompiled/zrun/sources/com/qix/library/command/CommandCode.java"
DST = REPO_ROOT / "tools/qix-ble/qix_ble/opcodes.py"

# Match: public static final byte COMMAND_FOO = 123;  o  public static final byte COMMAND_FOO = -86;
PATTERN = re.compile(r"public static final byte (COMMAND_\w+)\s*=\s*(-?\d+)\s*;")


def main():
    if not SRC.exists():
        sys.exit(f"ERROR: no encontrado {SRC}")

    text = SRC.read_text(encoding="utf-8")
    matches = PATTERN.findall(text)
    if not matches:
        sys.exit("ERROR: no se encontraron constantes en el .java")

    # Convertir signed bytes a unsigned (Java byte es signed -128..127, queremos 0..255)
    entries = []
    for name, value_str in matches:
        v = int(value_str)
        if v < 0:
            v += 256
        # Strip "COMMAND_" prefix → "CMD_" para concisión
        py_name = "CMD_" + name[len("COMMAND_"):]
        entries.append((py_name, v))

    entries.sort(key=lambda e: e[1])  # ordenar por opcode value

    out = [
        '"""Constantes de opcodes Qix (com.qix.library.command.CommandCode).',
        "",
        f"Auto-generado por scripts/extract_opcodes.py desde",
        f"{SRC.relative_to(REPO_ROOT)}",
        f"Total: {len(entries)} opcodes.",
        '"""',
        "",
    ]
    for name, value in entries:
        out.append(f"{name} = 0x{value:02X}")

    out.append("")
    out.append("CMD_NAMES: dict[int, str] = {")
    for name, value in entries:
        out.append(f"    0x{value:02X}: {name!r},")
    out.append("}")
    out.append("")

    DST.write_text("\n".join(out), encoding="utf-8")
    print(f"escrito {DST} con {len(entries)} opcodes")


if __name__ == "__main__":
    main()
