# Vector B — USB capture scripts

Scripts para investigar el comportamiento USB del badge E87 (BR35 / AC707N) al
conectar los pads `DP` / `DM` / `5V` / `GND` del PCB `qx7613_v1.3` a un USB host.

Estado actual (2026-05-15): el firmware OEM enumera **automáticamente** al
conectar (sin handshake `0x16EF`, sin dongle), expone una USB Composite Device
(Mass-Storage + USB Audio + HID), y desconecta solo a los ~8.5 s, reconectando
en ciclo continuo cada ~10-11 s. Ver
internal research notes (not published).

## Scripts

### `capture.sh`
Monitor pasivo basado en `udevadm monitor` que reacciona a USB `add` events
filtrando por VID `3654` (Jieli). Útil si el device dura varios segundos.
**Limitación**: en algunos kernels `udevadm` sin sudo no recibe eventos.

### `poll.sh`
Monitor por polling de `/sys/bus/usb/devices/*/idVendor` cada 20 ms. Más
agresivo que `capture.sh` — captura aunque el device dure < 100 ms. No
requiere sudo. Dumpea sysfs + intenta `lsusb -v` y `xxd` de descriptors.

### `auto-dump.sh`
Captura el storage. Polea `/dev/sda` cada 50 ms; cuando aparece, valida que es
el badge (vendor=BR35 / model contiene UDISK / size=4672 sectores), y ejecuta
**en paralelo**:
- `dd` del raw a `udisk-raw.bin` + `sha256sum`
- `mount -o ro,noatime` + `ls -laR` + `cp -a` del tree FAT a `fs/`

Requiere sudo (para `dd /dev/sda` y `mount`). Se queda en loop esperando nuevas
reconexiones — captura varias a la vez en directorios timestamp distintos.

Output: `<script_dir>/dumps/<ts>/` con `dd.log`, `udisk-raw.bin`,
`udisk-raw.sha256`, `sda-info.txt`, `mount.log`, `cp.log`, `ls-laR.txt`, `fs/`.

## Hallazgos clave (sesión 2026-05-15)

- VID `0x3654` (Jieli) / PID `0x4b55` ("UK" ASCII LE — chip id customizado OEM).
- SCSI INQUIRY: `BR35 UDISK 1.00` → **chip family confirmada como BR35** (= AC707N).
- USB serial: `4150353336373419` → ASCII `"AP533674"` + `0x19` — probable chip
  unique factory ID.
- UDISK = FAT12 de 2.28 MiB con un único directorio `BAG/` (vacío). El
  firmware + UI assets están en otra partición no expuesta como mass-storage.
- 5 dumps idénticos (sha256 `1ff66317…`) confirman que el FS no cambia entre
  reconnects.
