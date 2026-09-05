#pragma once
#include <stdint.h>
#include <stdbool.h>

typedef enum {
    ACK_OK,
    ACK_TIMEOUT,
} ack_result_t;

// Initialize GPIOs for bit-bang (D+/D- as outputs).
void handshake_init(void);

// Drive the full 0x16EF sequence + inter-frame pad once.
// Interrupts disabled during the critical section (~310 us).
// scale_factor: 1.00 = baseline Kramer timing. Used for sweep.
void handshake_send_0x16EF(float scale_factor);

// Wait up to timeout_us for D+/D- to BOTH be LOW for sustained >= ACK_MIN_LOW_US.
// Sets D+/D- to INPUT before polling; caller can re-init for next TX.
ack_result_t handshake_wait_for_ack(uint32_t timeout_us);

// Full attempt: send + wait for ACK. Returns true on ACK, false on all retries timing out.
bool handshake_attempt(float scale_factor);

// Sweep timing scale from `min` to `max` step `step`. For each scale, do `attempts_per_step`
// handshakes. Print results via printf. Stops at first failing range or completes full sweep.
void handshake_sweep(float min, float max, float step, int attempts_per_step);
