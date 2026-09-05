#pragma once
#include <stdint.h>
#include <stdbool.h>

// Pure-logic API (HW-independent — used in native tests)
typedef struct {
    uint16_t shift_reg;
    uint8_t  bit_count;
} decoder_state_t;

void decoder_reset(decoder_state_t* s);

// Push a single bit (D+ sample at D- rising edge).
// Returns true if accumulated value just hit 0x16EF (MATCH event).
bool decoder_push_bit(decoder_state_t* s, uint8_t bit);

// Reset decoder state if too much time elapsed without a clock edge.
// Call after a poll cycle with us_since_last_edge = how long since last D- rising.
void decoder_watchdog_check(decoder_state_t* s, uint32_t us_since_last_edge);

// HW-side API (GPIO-backed, RP2040-only — excluded from native test build)
void decoder_init_gpio(void);

// Poll RX pins once. Returns true if a MATCH happened this call.
// Caller should pulse_ack on true.
bool decoder_poll_once(decoder_state_t* s);

// Pulse D+ and D- LOW (output) for 2 ms, then back to input.
void decoder_pulse_ack(void);
