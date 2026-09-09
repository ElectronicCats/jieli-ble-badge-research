# OTA over BLE — OEM→custom & custom→custom

How to flash and update firmware on the JieLi AC707N / BR35 e-badge **entirely over BLE**
(no USB, no opening the case), using the `qix` client in [`tools/qix-ble`](../tools/qix-ble).

> ⚠️ **Security research / educational use only** — on hardware you own. See
> [DISCLAIMER.md](../DISCLAIMER.md). A failed OTA can require USB-ISP recovery.

The badge is reflashed **directly over the OEM app-only BLE OTA** (the path the ZRun app uses).
The custom firmware is packaged **fw-custom** — your `app.bin` spliced into an OEM `.ufw`,
which keeps the **OEM uboot** byte-identical — so a single `qix flash --oem` hands the image to the
resident OEM uboot, which rewrites the CODE partition and boots the custom:

```
OEM ──► custom firmware     ( qix flash --oem  ·  Qix FD00, opcode 0xC0  ·  fw-custom )
```

All tooling addresses the badge **by MAC** (constant, lives in the preserved `key_mac`); only the
advertised **name** changes `OEM → custom firmware`.

> **Note — the STAGER chain is legacy / not used here.** An older `qix provision` / `deploy-fw`
> path installed a minimal loader ("STAGER" bridge) exposing raw-flash `0xD0–0xD3` + apply `0xD4`.
> It is **not used** for this build: the custom firmware embeds its resources in the code image,
> so the direct `qix flash` path is sufficient (the loader firmware is no longer shipped). The
> stager subcommands remain in the client only as a legacy option for a custom that must write the
> separate `ui_res` / `virfat` resource partitions (which the app-only `0xC0` path cannot reach).

---

## 1. Prerequisites (read before any flash)

- **Host:** the `qix` client is built on [`bleak`](https://github.com/hbldh/bleak), so the BLE
  transport works on **Linux (BlueZ), macOS (CoreBluetooth) and Windows (WinRT)**; every
  BlueZ/DBus helper in it is gated behind `sys.platform == "linux"` and is a no-op elsewhere.
  **The recipes below are the Linux ones and are what has been validated on hardware** — the
  `rfkill` / `hciconfig` / `qix cleanup` / `QIX_CONNECT_VIA_DBUS=1` steps are BlueZ workarounds
  and are neither needed nor available on macOS/Windows, where bleak's native backend manages
  connection state itself. Install the client: `pip install -e tools/qix-ble`.
- **Third-party crypto (`jltech`):** vendored as pure Python in `tools/ufw-repack/jltech/` —
  nothing to obtain, no `crcmod`. Only `scripts/gen_chipkey_keyfile.py` still needs the external
  `jl-misctools` mirror. See [README](../README.md#dependencies).
- **Chipkey:** these images embed the device flashing key `0x9847` (E87, PID 1558). A different
  PID needs a different chipkey/`isd_config.ini` — do **not** reuse `0x9847`.
- **Power:** badge battery **≥ 30 %**, or USB connected for the **whole** flash window. A
  brown-out while the code region is being rewritten can brick the unit → USB-ISP recovery.
- **Single connection slot:** the badge accepts **one** BLE connection. Turn the phone's
  Bluetooth **fully off** (closing the ZRun app is not enough).
- **TEST_MODE gate:** do **not** run `qix dump --auth` since the last power-cycle — it sets a
  TEST_MODE flag that makes the badge **silently reject** `REQ_UPDATE`. Power-cycle to clear it.
- **Address by MAC**, never by name (`qix scan` to find it). The name changes across stages; the
  MAC does not.
- **BLE-HID pairing / stale bond:** the badge is a BLE-HID device. Clear any stale host bond
  before flashing (see §3) and let `qix flash` pair fresh (`QIX_FLASH_PAIR=1`).

### The firmware image in [`firmware/`](../firmware)

| File | Size | Role |
|---|---|---|
| `firmware/custom-fw-ac707n.ufw` | 1 079 363 B | **The custom firmware** — the custom firmware badge-menu build (LVGL UI, `BADGE-FWMARK 016`). Serves the **Qix FD00** OTA server on its Update screen. Qix-wrapped **fw-custom** (OEM uboot + app). The **destination** of every leg; **validated end-to-end over custom→custom**. |

It carries the 27-byte Qix wrapper (magic `bcaf`). The custom is packaged **fw-custom**
(`tools/ufw-repack/swap_app.py`: your `app.bin` spliced into an OEM `.ufw`, which preserves the OEM
uboot byte-identically — the resident OEM uboot is what performs the CODE rewrite on apply). Do
**not** flash a raw SDK `make` image (native uboot) as the OTA payload / recovery — the native uboot
cannot do the OTA CODE-write and the badge bricks silently at apply.

### Rebuilding the image from a fresh `app.bin`

`firmware/custom-fw-ac707n.ufw` is the packaged OTA image; the SDK build only emits a raw
`app.bin` (its own `update.ufw` carries the native uboot and is **not** OTA-flashable). To roll a
new build into the OTA image, splice your freshly-built `app.bin` into **an existing copy of
`firmware/custom-fw-ac707n.ufw`** (reused as the base — it already carries the OEM uboot, flash
geometry, and the `bcaf` Qix wrapper). `swap_app.py` does a code-only replace and recomputes every
CRC (JLFS `app.bin`, `app_area_head`, `flash.bin`, the list/header, and the Qix wrapper), so the
output is flash-ready — no separate `wrap_qix.py` step:

The crypto/CRC helpers `swap_app.py` needs are **vendored** in `tools/ufw-repack/jltech/` (pure
Python — no `jl-misctools` checkout and no `crcmod`), so it runs from this repo with nothing extra
on the `PYTHONPATH`. Just point `APP` at your freshly-built `app.bin` (after a submodule build it is
at `tools/community-re/jieli-sdks/e_badge_707_sdk_200/SDK/cpu/br35/tools/app.bin`), then run from
the repo root:

```sh
APP=/path/to/SDK/cpu/br35/tools/app.bin

python3 tools/ufw-repack/swap_app.py \
  firmware/custom-fw-ac707n.ufw \
  "$APP" \
  firmware/custom-fw-ac707n.ufw
```

`swap_app.py <base.ufw> <app.bin> <out.ufw>`: it reads `firmware/custom-fw-ac707n.ufw` as the
OEM-geometry **base**, splices in your **`$APP`**, and writes the packaged image back to
`firmware/custom-fw-ac707n.ufw` (**the deliverable**). The base is read fully before the output is
written, so reusing the same path (in-place update) is safe — pass a different third argument if you
want to keep the previous image. (No inline `#` comments inside the command: an inline comment after
a `\` breaks the line continuation and the shell then tries to run the `.bin`/`.ufw` as a command.)

`swap_app.py` prints the OEM app slot size (~995 KB) and 0xFF-pads the tail; your `app.bin` must
fit it. The output size stays `1 079 363 B`. Two builds of the same source differ only in the
embedded `__TIME__`/`__LINE__` bytes plus the CRCs that cover them — a byte-different `.ufw` with
the same size is expected (don't compare by `sha256`). Then flash it per §2 / §3.

---

## 2. OEM → custom

The badge currently runs the **stock OEM** firmware (powered on, advertising). This host's BlueZ
needs a clean adapter + the deterministic connect — the **bare `qix flash` hangs**.

```sh
# 1. Adapter up + clear any zombie connection. A hung/failed attempt leaves a stale link that
#    blocks the next (errors: "Operation already in progress" / "le-connection-abort-by-local"):
sudo rfkill unblock bluetooth && sudo hciconfig hci0 up
qix cleanup <MAC> --remove
qix scan | grep -i <MAC>              # confirm it advertises; caches it for the DBus connect

# 2. Flash. Go STRAIGHT to the real flash — a --probe-only that returns accepted leaves the badge
#    in update-mode (it won't re-advertise cleanly), so don't probe-then-flash. Takes minutes.
QIX_CONNECT_VIA_DBUS=1 QIX_FLASH_PAIR=1 QIX_CONNECT_ATTEMPTS=1 QIX_CONNECT_TIMEOUT=60 \
  qix flash <MAC> firmware/custom-fw-ac707n.ufw --oem --req-timeout 20
```

If the connect still fails or hangs, **power-cycle the badge** (single-slot; it wedges after a
failed or half-open connect) and retry from step 1.

- Opcode `0xC0` (Qix FD00). `--oem` runs auth + bootstrap and the slower (~15 s) `REQ_UPDATE`
  timeout the stock firmware needs.
- The fw-custom keeps the OEM uboot, which rewrites the CODE and reboots into the custom
  (advertised name → `EC-BADGE`).
- This delivers the full EC-BADGE build (its UI/resources are embedded in the code image), so no
  separate resource-writing step is needed.

---

## 3. custom → custom

The badge already runs the EC-BADGE custom. Put it on **Ajustes → Actualizar** (advertising
`EC-BADGE`, serving the Qix FD00 OTA), then:

```sh
# 1. Adapter up (do NOT use btmgmt on hosts where it hangs; it is not needed):
sudo rfkill unblock bluetooth && sudo hciconfig hci0 up

# 2. Clear any stale host bond, then re-cache the badge with an LE scan.
#    The Update screen serves the OTA cleartext WITHOUT bonding, so a leftover BlueZ LTK makes
#    the badge drop the link on the first write (UART: `LE disconnect reason=0x13`). Use
#    `bluetoothctl remove`, NOT QIX_FORGET_BOND (that wipes the cache -> "Not Connected").
bluetoothctl remove <MAC>
bluetoothctl --timeout 15 scan le | grep -i <MAC>

# 3. Flash (deterministic DBus connect + fresh HID pair). The ~1 MB transfer takes SEVERAL
#    MINUTES — do NOT interrupt it or wrap it in a short timeout.
QIX_CONNECT_VIA_DBUS=1 QIX_FLASH_PAIR=1 QIX_CONNECT_ATTEMPTS=4 QIX_CONNECT_TIMEOUT=40 \
  qix flash <MAC> firmware/custom-fw-ac707n.ufw --oem --req-timeout 20
```

This is the exact path validated end-to-end this session. Resume after a link drop works without
re-issuing `0xC0`.

> **The receiver must carry the OEM uboot** (which the fw-custom packaging guarantees, and
> which is preserved across every OTA). A badge that was USB-ISP'd with a raw SDK `make` image
> (native uboot) cannot do the OTA CODE-write and bricks silently at apply — recover by USB-ISP'ing
> the fw-custom `.ufw` code-slice.

---

## 4. Verify the result

```sh
qix scan                       # advertised name changed to "EC-BADGE"
```
- Visual: the badge boots the new build — press a button to wake and see the lock image, then
  swipe to the LVGL menu.
- The badge disconnects and reboots on its own 5–30 s after the transfer.
- On UART (`/dev/ttyUSB0` @ 115200): `OTA fully received` → `apply VERIFY OK` → `cpu_reset` →
  `[VM UPDATE RECOVER]` (OEM uboot rewrites CODE) → `update module init ok` → boot banner
  `>>>>> BADGE-FWMARK 016 <<<<<` → `menu: build`.

---

## 5. Connection env vars & troubleshooting

Env vars (see [`tools/qix-ble/README.md`](../tools/qix-ble/README.md)):

- `QIX_CONNECT_VIA_DBUS=1` — raw BlueZ `Device1.Connect()` (skips bleak's flaky scan; needs the
  device cached by an LE scan first, §3 step 2).
- `QIX_FLASH_PAIR=1` — fresh Just-Works pair on `qix flash` (the badge is BLE-HID; without it the
  link can tear down on the first data write).
- `QIX_CONNECT_ATTEMPTS` (default 4) — connection retry rounds.
- `QIX_BREDR_OFF=1` — pre-flight `btmgmt bredr off` + tighter conn params. **Skip it on hosts
  where `btmgmt` hangs** (recover a wedged adapter with `sudo rfkill unblock bluetooth`); it is
  usually unnecessary.

| Symptom | Cause | Fix |
|---|---|---|
| `REQ_UPDATE` silently rejected / `state=0` | TEST_MODE flag set by a prior `qix dump --auth` | Power-cycle the badge, don't dump before flashing |
| Link drops on first data write, `LE disconnect reason=0x13` | BLE-HID, stale host LTK vs the cleartext Update screen | `bluetoothctl remove <MAC>` + re-scan (§3), let it pair fresh |
| `connect failed … Device1 not found` | device not cached / not advertising | ensure badge on the Update screen, then re-scan (§3) |
| `flash OK` + `apply VERIFY OK` + `cpu_reset`, then **silent brick** | receiver carried the SDK-native uboot (bad USB-ISP image) | recover by USB-ISP'ing the fw-custom `.ufw` code-slice (OEM uboot) — **not** a battery issue |
| `UfwInvalid` from `qix flash` | `.ufw` lacks the 27-byte Qix wrapper | `wrap_qix.py wrap` it |
| Custom needs its own `ui_res`/`virfat` resources | the `0xC0` path is code-only | (legacy) use the STAGER `provision`/`deploy-fw --resources` path |

---

## 6. Notes

- **No shell script performs the OTA** — flashing is entirely `qix` (this doc). The `scripts/` are
  build / repack / chipkey / setup helpers (see [`scripts/README.md`](../scripts/README.md)); they
  produce the `.ufw`, they do not flash it.
- The Qix OTA (`0xC0`) apply ends in the **resident OEM uboot** rewriting the CODE partition
  (`[0, 0x17E000)` is write-protected otherwise). The MAC (`key_mac`) is preserved across it.
- Building the firmware is out of scope here — see
  [build-m1-firmware.md](build-m1-firmware.md) and [jieli-sdk-build.md](jieli-sdk-build.md).
