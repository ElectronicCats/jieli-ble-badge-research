# pi-pico-jl-dongle — Pi Pico DIY Vector B dongle for JieLi BR35/AC707N

**Status:** 2026-05-18 initial setup. Foundation (0x16EF handshake + ACK detect) under construction. Final bridge approach (MSC pass-through vs USB switch IC vs manual swap) **TBD post-validation Phase 3**.

**Target HW:** Badge E87 (PCB qx7613_v1.3), SoC AC707N BR35 QFN-32, exposed DP/DM pads.
**Host PC tool:** `kagaimiq/jl-uboot-tool` (Python) — cloned in `tools/community-re/jl-uboot-tool/`.

## Architecture — bridge approach (provisional)

```
PC ←─ USB ─→ [Pi Pico] ←─ 4 soldered wires ─→ Badge BR35
              │
              ├─ USB device (native TinyUSB DCD RP2040) ────→ PC
              │     ↑↓ bridge state machine (~200-500 lines)
              └─ USB host (TinyUSB + pico-pio-usb HCD via PIO GP2/GP3) ────→ Badge
```

Pre-handshake: GP2/GP3 under raw control of the PIO state machine, emitting the custom 0x16EF.
Post-ACK: PIO reconfigured as pico-pio-usb HCD, badge enumerates as MSC `BR35 UBOOT1.00 1.00`.

## Executable plan (summary — see TaskList in session)

| Phase | Status | Notes |
|---|---|---|
| 0. Research pico-pio-usb compat | ✅ done | v0.7.2 (may 2025), RP2040 supported, FS+LS, GP0/GP1 default (configurable) |
| 1. Setup dev env (pico-sdk + Picoprobe) | 🟡 in progress | gcc-arm 13.2 + cmake 3.28 already; cloning pico-sdk + Pico-PIO-USB |
| 2. PIO state machine bit-bang 0x16EF | pending | **Critical path** — without this nothing works. Loopback test with LA before touching the badge |
| 3. ACK detector + retry loop | pending | **Critical path** — pullups TBD per validation |
| **DECISION POINT** | — | After Phase 3, decide bridge MSC vs USB switch IC vs manual swap |
| 4. USB device side (Pico ↔ PC, TinyUSB MSC) | pending | Only if "bridge MSC" chosen |
| 5. USB host side (Pico ↔ Badge, pico-pio-usb HCD) | pending | Only if "bridge MSC" chosen |
| 6. Mechanical: solder 3 wires DP/DM/GND to the badge | pending | No VBUS (badge uses internal battery) |
| 7. End-to-end integration test | pending | Requires Phases 4-5 OK |
| 8. jl-uboot-tool BR35 chip entry + first dump | pending | Patch `data/chips.yaml` |
| 9. Logbook + upstream PR | pending | Match feedback_upstream_contributions |

## Pi Pico pinout (provisional)

| Physical pin | GPIO | Function |
|---|---|---|
| Pin 4 | GP2 | Badge D+ (bit-bang pre-ACK / USB host post-ACK) |
| Pin 5 | GP3 | Badge D- (bit-bang pre-ACK / USB host post-ACK) |
| Pin 3 | GND | Common ground |
| Pin 36 | 3V3 OUT | For external pullups (if added) |
| USB connector | — | To PC host (native TinyUSB DCD) |

GP2/GP3 chosen because they must be an adjacent pair (pico-pio-usb HCD requirement).
Pico-PIO-USB's default GP0/GP1 are reserved by convention for UART debug — we use GP2/GP3 to avoid conflict.

## Current minimum BOM

| Item | Have? | Notes |
|---|---|---|
| Pi Pico RP2040 | ✅ | Original (no W, no 2) |
| Picoprobe (second Pico) | ✅ | For SWD debug + flash |
| Logic Analyzer | ✅ | Validation of the bit-bang before the badge |
| Multimeter + magnifier | ✅ | Continuity + inspect pads |
| Wires AWG30/32 + flux + iron | (assumed ✅) | Solder to the PCB qx7613_v1.3 |
| External 10kΩ pullups | ❌ TBD | If Phase 3 validation fails with builtin |
| USB switch IC TS3USB221E | ❌ optional | If we choose the "switch IC" approach vs "bridge" |
| 22Ω series resistors | ❌ optional | Recommended by pico-pio-usb docs for signal integrity |

## Empirical timing of the 0x16EF bit-bang (RESOLVED 2026-05-18)

Source: `christian-kramer/JieLi-AC690X-Familiarization` (scope-validated on AC6905A/BR17).

| Param | Value |
|---|---|
| Bit period (D- full cycle) | **19.3 μs** (~51.8 kHz clock) |
| D- low half | 9.625 μs |
| D- high half | 9.667 μs |
| D+ skew before D- falling | 125 ns |
| Sample edge | D- rising |
| Bit order | MSB first (0x16EF = `0001 0110 1110 1111`) |
| Inter-frame pad | 11.21 μs high + 7 μs low |
| ACK signature | D+ AND D- to GND ≥1-2 ms |
| Retry | loop forever (no protocol-side timeout) |

**Targets for the PIO program:**
- Sysclk 125 MHz → 9.625 μs = 1203 cycles
- clkdiv ≥ 8 + ~150 cycles per half-period
- D+ must change 125 ns before the D- falling edge (sub-μs precision)

Confidence: high for BR17/21 (empirical). Medium-high for BR35 (architectural argument — ROM-side USB_KEY check, same mechanism cross-family).

## Remaining critical open questions

2. **ACK detection threshold**: the Pico's builtin pullups (~50kΩ) are probably weak. External 10kΩ pullups are better but still borderline (1:1 divider with the chip's 10k pulldown → 1.65V during ACK). A 4.7kΩ pullup is ideal. Firmware fallback: sample the D+ pullup pulse pattern.

3. **pico-pio-usb SOF generation jitter**: BR23+ does 3 SOF checks post-handshake. No precedent of Pico-PIO-USB vs JieLi specifically. Debug with scope during the integration test.

4. **`pio_sm_set_enabled(false)` + reload PIO program**: clean switch between the custom bit-bang and the pico-pio-usb HCD. Technically feasible but no public precedent — risk of quirks in cleanup.

5. **Bridge MSC complexity**: ~200-500 lines of C for the bridge state machine if the "bridge approach" is kept. Alternative USB switch IC ($1-2) reduces the firmware to ~50 lines. Decision post-Phase 3.

## Build & flash

```bash
# One-time setup
export PICO_SDK_PATH=~/pico-sdk

# Build both firmwares
cd tools/pi-pico-jl-dongle
mkdir -p build && cd build
cmake .. && make -j$(nproc)

# Flash via BOOTSEL + drag:
# - build/src/handshaker/handshaker.uf2     → Pi Pico A (bench)
# - build/src/chip-emulator/chip_emulator.uf2 → Pi Pico B (ex-Picoprobe)
```

## Bench testing

See internal notes (not published) for wiring. Two CDC terminals:
- `/dev/ttyACM0` = Pico A (handshaker)
- `/dev/ttyACM1` = Pico B (chip emulator)

Run sweep: `python3 test/ack_sweep.py` (needs `pip install pyserial`; by default
it talks to `--port /dev/ttyACM0`, the handshaker).

## Native tests

```bash
cd test/native
mkdir -p build && cd build
cmake .. && make && ctest --output-on-failure
```

Expected: 6 Unity tests pass (decoder pure logic + watchdog).

## Cross-repo references

- `tools/community-re/jl-uboot-tool/internal notes (not published) — 0x16EF waveform spec
- `tools/community-re/jl-uboot-tool/internal notes (not published) — opcodes FB/FC/FD post-handshake
- `tools/community-re/jl-uboot-tool/data/chips.yaml` — BR35 entry TBD
- `tools/community-re/jieli-sdks/e_badge_707_sdk_200/SDK/cpu/br35/tools/br35loader.bin` — loader binary 27 KB
- `tools/community-re/jieli-sdks/e_badge_707_sdk_200/SDK/cpu/br35/tools/rom.image` — MaskROM dump 32 KB
- Memory: `reference_jieli_uboot_protocol.md` — full protocol
- Memory: `reference_jieli_boot_mode_pins.md` — DP/DM pads teardown
- Memory: `reference_jieli_maskrom_abi_validated.md` — `_UBOOT_LOADER_RAM_START=0x102600` + chips.yaml entry inferred
- Origin logbook: internal research notes (not published)
