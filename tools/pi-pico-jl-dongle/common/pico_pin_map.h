#pragma once
// Pin assignments for both Pi Pico firmwares.
// Pico A (handshaker) drives D+/D- to badge; Pico B (chip-emulator) reads them.

// Pi Pico A (handshaker)
#define PIN_TX_DPLUS    2    // GPIO 2 -> D+ (data line)
#define PIN_TX_DMINUS   3    // GPIO 3 -> D- (clock line)

// Pi Pico B (chip emulator)
#define PIN_RX_DMINUS   6    // GPIO 6 <- D- input (clock from Pico A's GP3)
#define PIN_RX_DPLUS    7    // GPIO 7 <- D+ input (data from Pico A's GP2)
// Cable mapping: GP2 -> GP7, GP3 -> GP6, GND -> GND
