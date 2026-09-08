# Vector B — USB capture scripts

Scripts to investigate the USB behavior of the E87 badge (BR35 / AC707N) when
connecting the `DP` / `DM` / `5V` / `GND` pads of the `qx7613_v1.3` PCB to a USB host.

Current status (2026-05-15): the OEM firmware enumerates **automatically** on
connect (no `0x16EF` handshake, no dongle), exposes a USB Composite Device
(Mass-Storage + USB Audio + HID), and disconnects on its own after ~8.5 s, reconnecting
in a continuous cycle every ~10-11 s. See
internal research notes (not published).

## Scripts

### `capture.sh`
Passive monitor based on `udevadm monitor` that reacts to USB `add` events
filtering by VID `3654` (Jieli). Useful if the device stays up for several seconds.
**Limitation**: on some kernels `udevadm` without sudo does not receive events.

### `poll.sh`
Polling monitor of `/sys/bus/usb/devices/*/idVendor` every 20 ms. More
aggressive than `capture.sh` — captures even if the device lasts < 100 ms. Does not
require sudo. Dumps sysfs + attempts `lsusb -v` and `xxd` of descriptors.

### `auto-dump.sh`
Captures the storage. Polls `/dev/sda` every 50 ms; when it appears, validates that it is
the badge (vendor=BR35 / model contains UDISK / size=4672 sectors), and runs
**in parallel**:
- `dd` of the raw to `udisk-raw.bin` + `sha256sum`
- `mount -o ro,noatime` + `ls -laR` + `cp -a` of the FAT tree to `fs/`

Requires sudo (for `dd /dev/sda` and `mount`). It stays in a loop waiting for new
reconnections — capturing several at once into different timestamp directories.

Output: `<script_dir>/dumps/<ts>/` with `dd.log`, `udisk-raw.bin`,
`udisk-raw.sha256`, `sda-info.txt`, `mount.log`, `cp.log`, `ls-laR.txt`, `fs/`.

## Key findings (session 2026-05-15)

- VID `0x3654` (Jieli) / PID `0x4b55` ("UK" ASCII LE — OEM-customized chip id).
- SCSI INQUIRY: `BR35 UDISK 1.00` → **chip family confirmed as BR35** (= AC707N).
- USB serial: `4150353336373419` → ASCII `"AP533674"` + `0x19` — probable chip
  unique factory ID.
- UDISK = 2.28 MiB FAT12 with a single directory `BAG/` (empty). The
  firmware + UI assets live in another partition not exposed as mass-storage.
- 5 identical dumps (sha256 `1ff66317…`) confirm that the FS does not change between
  reconnects.
