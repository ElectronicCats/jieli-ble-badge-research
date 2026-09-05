#include <stdio.h>
#include "handshake.h"
#include "pico/stdlib.h"
#include "hardware/sync.h"
#include "jl_protocol.h"
#include "pico_pin_map.h"

// Helper: busy wait approximation with sub-us precision.
// busy_wait_us handles the integer us part; cycle nops handle remainder.
// @ 125 MHz: 1 cycle = 8 ns.

static inline void wait_kramer_clock_low(float scale) {
    float total_us = KRAMER_CLK_LOW_US * scale;
    uint32_t whole_us = (uint32_t)total_us;
    float frac_us = total_us - whole_us;
    uint32_t cycles = (uint32_t)(frac_us * 125.0f);
    if (whole_us > 0) busy_wait_us(whole_us);
    if (cycles > 0) busy_wait_at_least_cycles(cycles);
}

static inline void wait_kramer_clock_high(float scale) {
    float total_us = KRAMER_CLK_HIGH_US * scale;
    uint32_t whole_us = (uint32_t)total_us;
    float frac_us = total_us - whole_us;
    uint32_t cycles = (uint32_t)(frac_us * 125.0f);
    if (whole_us > 0) busy_wait_us(whole_us);
    if (cycles > 0) busy_wait_at_least_cycles(cycles);
}

static inline void wait_pad_high(float scale) {
    float total_us = KRAMER_PAD_HIGH_US * scale;
    uint32_t whole_us = (uint32_t)total_us;
    float frac_us = total_us - whole_us;
    uint32_t cycles = (uint32_t)(frac_us * 125.0f);
    if (whole_us > 0) busy_wait_us(whole_us);
    if (cycles > 0) busy_wait_at_least_cycles(cycles);
}

static inline void wait_pad_low(float scale) {
    uint32_t whole_us = (uint32_t)(KRAMER_PAD_LOW_US * scale);
    if (whole_us > 0) busy_wait_us(whole_us);
}

void handshake_init(void) {
    gpio_init(PIN_TX_DPLUS);
    gpio_init(PIN_TX_DMINUS);
    // Pullups for ACK detection. Stay active even when driven OUT;
    // gpio_put overrides for transmit, pullup pulls high when IN and chip idle.
    gpio_pull_up(PIN_TX_DPLUS);
    gpio_pull_up(PIN_TX_DMINUS);
    gpio_set_dir(PIN_TX_DPLUS, GPIO_OUT);
    gpio_set_dir(PIN_TX_DMINUS, GPIO_OUT);
    gpio_put(PIN_TX_DPLUS, 1);
    gpio_put(PIN_TX_DMINUS, 1);   // idle high so first falling edge of MSB is real
}

void handshake_send_0x16EF(float scale_factor) {
    // Caller (handshake_attempt) wraps with save_and_disable_interrupts
    // so TX + direction switch happen atomically.
    gpio_put(PIN_TX_DMINUS, 1);   // ensure idle high before first falling edge

    for (int bit_idx = JL_KEY_BITS - 1; bit_idx >= 0; bit_idx--) {
        int val = (JL_KEY >> bit_idx) & 1;
        gpio_put(PIN_TX_DPLUS, val);    // D+ changes first (~8 ns ahead)
        gpio_put(PIN_TX_DMINUS, 0);     // D- falling edge
        wait_kramer_clock_low(scale_factor);
        gpio_put(PIN_TX_DMINUS, 1);     // D- rising edge -> chip samples here
        wait_kramer_clock_high(scale_factor);
    }

    // Inter-frame pad: D- high ~11.21 us, then low ~7 us
    gpio_put(PIN_TX_DMINUS, 1);
    wait_pad_high(scale_factor);
    gpio_put(PIN_TX_DMINUS, 0);
    wait_pad_low(scale_factor);
}

ack_result_t handshake_wait_for_ack(uint32_t timeout_us) {
    uint32_t low_streak = 0;
    absolute_time_t start = get_absolute_time();

    while ((uint32_t)absolute_time_diff_us(start, get_absolute_time()) < timeout_us) {
        if (gpio_get(PIN_TX_DPLUS) == 0 && gpio_get(PIN_TX_DMINUS) == 0) {
            low_streak++;
            if (low_streak >= ACK_MIN_LOW_US) return ACK_OK;
        } else {
            low_streak = 0;
        }
        busy_wait_us(1);
    }
    return ACK_TIMEOUT;
}

bool handshake_attempt(float scale_factor) {
    for (int attempt_idx = 0; attempt_idx < MAX_RETRIES; attempt_idx++) {
        // Atomic block: TX + direction switch under disabled interrupts
        // to avoid glitches on the line when handing off OUT->IN.
        gpio_set_dir(PIN_TX_DPLUS, GPIO_OUT);
        gpio_set_dir(PIN_TX_DMINUS, GPIO_OUT);
        gpio_pull_up(PIN_TX_DPLUS);
        gpio_pull_up(PIN_TX_DMINUS);

        uint32_t saved = save_and_disable_interrupts();
        handshake_send_0x16EF(scale_factor);
        gpio_set_dir(PIN_TX_DPLUS, GPIO_IN);
        gpio_set_dir(PIN_TX_DMINUS, GPIO_IN);
        restore_interrupts(saved);

        if (handshake_wait_for_ack(ACK_TIMEOUT_US) == ACK_OK) {
            return true;
        }
        // No long gap between retries — kagaimiq spec is "send continuously".
        // The inter-frame pad inside handshake_send_0x16EF (~18 us) is the only
        // separation. Original sleep_ms(20) made effective frame rate ~45 Hz,
        // too slow to catch the ROM's boot-window check pattern.
    }
    return false;
}

void handshake_sweep(float min, float max, float step, int attempts_per_step) {
    printf("[SWEEP] start range [%.2f..%.2f] step %.2f attempts/step %d\r\n",
           min, max, step, attempts_per_step);

    for (float scale = min; scale <= max + 0.001f; scale += step) {
        int ok = 0, fail = 0;
        for (int i = 0; i < attempts_per_step; i++) {
            if (handshake_attempt(scale)) ok++; else fail++;
            sleep_ms(20);
        }
        printf("[SWEEP] scale=%.2f ok=%d/%d\r\n", scale, ok, attempts_per_step);
    }
    printf("[SWEEP] complete\r\n");
}
