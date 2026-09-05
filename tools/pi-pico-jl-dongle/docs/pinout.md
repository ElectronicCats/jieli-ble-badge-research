# Wiring — Pi Pico A ↔ Pi Pico B (Fase 2-3 fixture)

## Cable

3 wires Dupont M-M, 5-10 cm. No protoboard required.

| Pico A side | wire | Pico B side |
|---|---|---|
| GP2 (pin 4) — D+ tx | →→→→→ | GP7 (pin 10) — D+ rx |
| GP3 (pin 5) — D- tx | →→→→→ | GP6 (pin 9)  — D- rx |
| GND (pin 3 or any)  | →→→→→ | GND (any GND pin) |

## Logic Analyzer probes (for T2 spec verification)

| LA channel | connects to |
|---|---|
| CH0 | Pico A GP2 (pin 4) |
| CH1 | Pico A GP3 (pin 5) |
| GND | Pico A GND |

Trigger: GP3 falling edge. Sample rate ≥ 1 MHz (10 MHz preferred for skew measurement).

## Expected waveform (per Kramer baseline)

- GP3 (D-) clock low half: 9.625 μs ±5%
- GP3 (D-) clock high half: 9.667 μs ±5%
- 16 clock cycles per frame
- GP2 (D+) changes ~125 ns before each GP3 falling edge
- Decoded GP2 sequence at GP3 rising edges (MSB first) = `0x16EF` = `0001 0110 1110 1111`
- Inter-frame pad: D- high ~11.21 μs + low ~7 μs

## Implementation note: CPU bit-bang, not PIO

Fase 2-3 fixture uses CPU bit-bang on RP2040 @ 125 MHz (interrupts disabled
during the ~310 μs critical section). PIO is reserved for Fase 5+ where
pico-pio-usb HCD would take over GP2/GP3 post-ACK to enumerate the badge
as MSC. The CPU approach is simpler for the handshake-only validation and
allows runtime-mutable scale factor for sweep tests.
