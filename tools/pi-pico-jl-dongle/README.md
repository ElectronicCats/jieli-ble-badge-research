# pi-pico-jl-dongle — Pi Pico DIY Vector B dongle para JieLi BR35/AC707N

**Estado:** 2026-05-18 setup inicial. Foundation (handshake 0x16EF + ACK detect) en construcción. Approach final del bridge (MSC pass-through vs USB switch IC vs swap manual) **TBD post-validation Fase 3**.

**Target HW:** Badge E87 (PCB qx7613_v1.3), SoC AC707N BR35 QFN-32, pads DP/DM expuestos.
**Host PC tool:** `kagaimiq/jl-uboot-tool` (Python) — clonado en `tools/community-re/jl-uboot-tool/`.

## Arquitectura — bridge approach (provisional)

```
PC ←─ USB ─→ [Pi Pico] ←─ 4 wires soldados ─→ Badge BR35
              │
              ├─ USB device (TinyUSB DCD nativo RP2040) ────→ PC
              │     ↑↓ bridge state machine (~200-500 líneas)
              └─ USB host (TinyUSB + pico-pio-usb HCD via PIO GP2/GP3) ────→ Badge
```

Pre-handshake: GP2/GP3 bajo control raw del PIO state machine, emiten 0x16EF custom.
Post-ACK: PIO reconfigurado como pico-pio-usb HCD, badge enumera como MSC `BR35 UBOOT1.00 1.00`.

## Plan ejecutable (resumen — ver TaskList en sesión)

| Fase | Status | Notas |
|---|---|---|
| 0. Research pico-pio-usb compat | ✅ done | v0.7.2 (may 2025), RP2040 supported, FS+LS, GP0/GP1 default (configurable) |
| 1. Setup dev env (pico-sdk + Picoprobe) | 🟡 in progress | gcc-arm 13.2 + cmake 3.28 ya; clonando pico-sdk + Pico-PIO-USB |
| 2. PIO state machine bit-bang 0x16EF | pending | **Critical path** — sin esto nada funciona. Loopback test con LA antes de tocar badge |
| 3. ACK detector + retry loop | pending | **Critical path** — pullups TBD según validation |
| **DECISION POINT** | — | After Fase 3, decidir bridge MSC vs USB switch IC vs swap manual |
| 4. USB device side (Pico ↔ PC, TinyUSB MSC) | pending | Solo si "bridge MSC" elegido |
| 5. USB host side (Pico ↔ Badge, pico-pio-usb HCD) | pending | Solo si "bridge MSC" elegido |
| 6. Mecánico: soldar 3 wires DP/DM/GND al badge | pending | Sin VBUS (badge usa batería interna) |
| 7. Integration test end-to-end | pending | Requiere Fases 4-5 OK |
| 8. jl-uboot-tool BR35 chip entry + first dump | pending | Patch `data/chips.yaml` |
| 9. Bitácora + upstream PR | pending | Match feedback_upstream_contributions |

## Pinout Pi Pico (provisional)

| Pin físico | GPIO | Función |
|---|---|---|
| Pin 4 | GP2 | D+ del badge (bit-bang pre-ACK / USB host post-ACK) |
| Pin 5 | GP3 | D- del badge (bit-bang pre-ACK / USB host post-ACK) |
| Pin 3 | GND | Common ground |
| Pin 36 | 3V3 OUT | Para pullups externos (si se agregan) |
| USB connector | — | Hacia PC host (TinyUSB DCD nativo) |

GP2/GP3 elegidos porque deben ser un par adyacente (requisito pico-pio-usb HCD).
GP0/GP1 default de Pico-PIO-USB están reservados por convención para UART debug — usamos GP2/GP3 para evitar conflict.

## BOM mínimo actual

| Item | Have? | Notas |
|---|---|---|
| Pi Pico RP2040 | ✅ | Original (no W, no 2) |
| Picoprobe (segundo Pico) | ✅ | Para SWD debug + flash |
| Logic Analyzer | ✅ | Validation del bit-bang antes del badge |
| Multimetro + lupa | ✅ | Continuity + inspect pads |
| Wires AWG30/32 + flux + iron | (assumed ✅) | Soldar al PCB qx7613_v1.3 |
| Pullups 10kΩ externos | ❌ TBD | Si validation Fase 3 falla con builtin |
| USB switch IC TS3USB221E | ❌ optional | Si elegimos approach "switch IC" vs "bridge" |
| Resistors 22Ω series | ❌ optional | Recomendados por pico-pio-usb docs para señal integrity |

## Timing empírico del bit-bang 0x16EF (RESUELTO 2026-05-18)

Fuente: `christian-kramer/JieLi-AC690X-Familiarization` (scope-validated en AC6905A/BR17).

| Param | Valor |
|---|---|
| Bit period (D- full cycle) | **19.3 μs** (~51.8 kHz clock) |
| D- low half | 9.625 μs |
| D- high half | 9.667 μs |
| D+ skew antes de D- falling | 125 ns |
| Sample edge | D- rising |
| Bit order | MSB first (0x16EF = `0001 0110 1110 1111`) |
| Inter-frame pad | 11.21 μs high + 7 μs low |
| ACK signature | D+ AND D- a GND ≥1-2 ms |
| Retry | loop forever (sin timeout protocol-side) |

**Targets para PIO program:**
- Sysclk 125 MHz → 9.625 μs = 1203 cycles
- clkdiv ≥ 8 + ~150 cycles per half-period
- D+ debe cambiar 125 ns antes de D- falling edge (precision sub-μs)

Confidence: alta para BR17/21 (empírico). Media-alta para BR35 (argumento arquitectural — ROM-side USB_KEY check mismo mecanismo cross-familia).

## Open questions críticos restantes

2. **ACK detection threshold**: pullups builtin del Pico (~50kΩ) probablemente débiles. Pullups externos 10kΩ mejor pero todavía borderline (divisor 1:1 con pulldown 10k del chip → 1.65V durante ACK). Pullup 4.7kΩ ideal. Fallback firmware: sample pattern del D+ pullup pulse.

3. **SOF generation jitter de pico-pio-usb**: BR23+ hace 3 SOF checks post-handshake. Sin precedente de Pico-PIO-USB vs JieLi específicamente. Debug con scope durante integration test.

4. **`pio_sm_set_enabled(false)` + reload PIO program**: switch limpio entre bit-bang custom y pico-pio-usb HCD. Factible técnicamente pero sin precedente público — riesgo de quirks en cleanup.

5. **Bridge MSC complexity**: ~200-500 líneas C de bridge state machine si se mantiene "bridge approach". Alternative USB switch IC ($1-2) reduce el firmware a ~50 líneas. Decisión post-Fase 3.

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

Run sweep: `python3 test/ack_sweep.py` (necesita `pip install pyserial`; por default
habla con `--port /dev/ttyACM0`, el handshaker).

## Native tests

```bash
cd test/native
mkdir -p build && cd build
cmake .. && make && ctest --output-on-failure
```

Expected: 6 Unity tests pass (decoder pure logic + watchdog).

## Referencias cross-repo

- `tools/community-re/jl-uboot-tool/internal notes (not published) — spec waveform 0x16EF
- `tools/community-re/jl-uboot-tool/internal notes (not published) — opcodes FB/FC/FD post-handshake
- `tools/community-re/jl-uboot-tool/data/chips.yaml` — BR35 entry TBD
- `tools/community-re/jieli-sdks/e_badge_707_sdk_200/SDK/cpu/br35/tools/br35loader.bin` — loader binary 27 KB
- `tools/community-re/jieli-sdks/e_badge_707_sdk_200/SDK/cpu/br35/tools/rom.image` — MaskROM dump 32 KB
- Memoria: `reference_jieli_uboot_protocol.md` — protocolo completo
- Memoria: `reference_jieli_boot_mode_pins.md` — pads DP/DM teardown
- Memoria: `reference_jieli_maskrom_abi_validated.md` — `_UBOOT_LOADER_RAM_START=0x102600` + chips.yaml entry inferred
- Bitácora origen: internal research notes (not published)
