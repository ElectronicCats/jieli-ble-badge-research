#include "decoder.h"
#include "jl_protocol.h"

void decoder_reset(decoder_state_t* s) {
    s->shift_reg = 0;
    s->bit_count = 0;
}

bool decoder_push_bit(decoder_state_t* s, uint8_t bit) {
    s->shift_reg = (s->shift_reg << 1) | (bit & 1);
    s->bit_count++;
    if (s->bit_count == JL_KEY_BITS) {
        bool match = (s->shift_reg == JL_KEY);
        decoder_reset(s);
        return match;
    }
    return false;
}

void decoder_watchdog_check(decoder_state_t* s, uint32_t us_since_last_edge) {
    if (s->bit_count > 0 && us_since_last_edge > DECODER_WATCHDOG_US) {
        decoder_reset(s);
    }
}

#ifndef NATIVE_TEST
#include "pico/stdlib.h"
#include "pico_pin_map.h"

static int s_last_d_minus = 1;
static absolute_time_t s_last_edge_time;

void decoder_init_gpio(void) {
    gpio_init(PIN_RX_DMINUS);
    gpio_init(PIN_RX_DPLUS);
    gpio_set_dir(PIN_RX_DMINUS, GPIO_IN);
    gpio_set_dir(PIN_RX_DPLUS, GPIO_IN);
    s_last_d_minus = 1;
    s_last_edge_time = get_absolute_time();
}

bool decoder_poll_once(decoder_state_t* s) {
    int curr = gpio_get(PIN_RX_DMINUS);
    bool match = false;
    if (curr == 1 && s_last_d_minus == 0) {
        // Rising edge — sample D+
        int bit = gpio_get(PIN_RX_DPLUS);
        match = decoder_push_bit(s, bit);
        s_last_edge_time = get_absolute_time();
    }
    s_last_d_minus = curr;
    uint32_t us = (uint32_t)absolute_time_diff_us(s_last_edge_time, get_absolute_time());
    decoder_watchdog_check(s, us);
    return match;
}

void decoder_pulse_ack(void) {
    gpio_set_dir(PIN_RX_DMINUS, GPIO_OUT);
    gpio_set_dir(PIN_RX_DPLUS, GPIO_OUT);
    gpio_put(PIN_RX_DMINUS, 0);
    gpio_put(PIN_RX_DPLUS, 0);
    sleep_ms(2);
    gpio_set_dir(PIN_RX_DMINUS, GPIO_IN);
    gpio_set_dir(PIN_RX_DPLUS, GPIO_IN);
}
#endif  // NATIVE_TEST
