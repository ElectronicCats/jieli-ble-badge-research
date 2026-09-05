#include <stdio.h>
#include "pico/stdlib.h"
#include "pico/stdio_usb.h"
#include "pico/cyw43_arch.h"
#include "decoder.h"

static inline void led_set(bool on) {
    cyw43_arch_gpio_put(CYW43_WL_GPIO_LED_PIN, on);
}

int main(void) {
    stdio_init_all();
    cyw43_arch_init();
    decoder_init_gpio();

    absolute_time_t start = get_absolute_time();
    while (!stdio_usb_connected() &&
           absolute_time_diff_us(start, get_absolute_time()) < 3000000) {
        sleep_ms(100);
    }

    printf("[INIT] chip-emulator v0.2 — decode mode\r\n");

    decoder_state_t state;
    decoder_reset(&state);
    uint32_t match_count = 0;

    while (1) {
        if (decoder_poll_once(&state)) {
            printf("[MATCH] 0x16EF detected — match #%lu\r\n", ++match_count);
            decoder_pulse_ack();
            printf("[ACK] pulsed 2 ms\r\n");
            led_set(true);
        } else {
            led_set(false);
        }
    }
}
