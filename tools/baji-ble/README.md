# baji-ble

BLE client for JieLi **Baji**-protocol smartwatch badges (advertised name `DG01`,
"Legend Smartwatch" family). Talks the vendor NUS command path plus an AE00/RCSP
probe path.

## What it does

Connects to a DG01 badge over BLE (via `bleak`, with an optional `dbus_fast`
BlueZ transport for the AE00 path) and drives the Baji protocol: scan/discover,
read Device Information + battery, send framed NUS commands (query keys, "find me"
buzzer), fetch the watch-face ("dial") dimensions, and upload a custom dial image.
It also has two probes: `ae00-probe` (AE00 GATT reachability) and `sysinfo`
(RCSP handshake → target-info / sys-info attribute dump, reusing `qix-ble`).

## Usage

Installed as a console script `baji` (`baji_ble.cli:main`); or run
`python -m baji_ble.cli <subcmd>`. All device subcommands take `--mac`.

```
baji scan [--timeout 10] [--all]          # discover (default filters to name "DG01")
baji info --mac <MAC>                      # manufacturer/model/serial/fw + battery %
baji find --mac <MAC>                      # trigger "find me" buzzer (cmd 18 sub 11)
baji query --mac <MAC> --key <N> [--timeout 5]
baji dial-dims --mac <MAC> [--timeout 5]   # screen type/grade/width/height + RGB565 size
baji upload-dial --mac <MAC> --file <img.bin> \
      [--chunk 200] [--font-position 0] [--custom 0] [--r 255 --g 255 --b 255] [--timeout 10]
baji battery-watch --mac <MAC> [--secs 60] [--interval 5]
baji ae00-probe --mac <MAC> [--timeout 5]
baji sysinfo --mac <MAC> [--timeout 10]    # RCSP handshake + target/sys info dump
```

`-v` / `-vv` raise log verbosity.

## Dependencies

- `bleak>=0.21` (BLE central)
- `dbus_fast` (BlueZ D-Bus AE00 transport)
- `qix-ble` — sibling repo tool at `tools/qix-ble` (RCSP session, auth, sysinfo);
  wired as an editable dep in `pyproject.toml`
- `pytest` (dev, for `tests/`)

## Status

active — a working, tested (`tests/`) client; listed as **active** in the repo's
tools table.
