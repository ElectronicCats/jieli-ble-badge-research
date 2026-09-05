#include "pico/stdlib.h"
#include "pico/cyw43_arch.h"
#include "handshake.h"
#include "jl_protocol.h"

// v0.8 — no USB CDC, immediate bit-bang at boot.
// Total time from power-on to first 0x16EF frame: ~5 ms (vs ~3000 ms before).
// LED feedback deferred until after hunt (CYW43 init takes ~200-500 ms).
//
// LED states (CYW43 onboard):
//   solid ON     = ACK detected (badge in UBOOT1.00 ROM state)
//   slow blink   = hunt timeout (no ACK in 60 s)
//   dark         = currently hunting (CYW43 not yet initialized)
//
// To retry: unplug + replug the Pico to power-cycle.

#define HUNT_TIMEOUT_MS  60000
#define BLINK_PERIOD_MS  500

static inline void led_set(bool on) {
    cyw43_arch_gpio_put(CYW43_WL_GPIO_LED_PIN, on);
}

int main(void) {
    // STEP 1 — minimal GPIO init for bit-bang. ~1 ms.
    handshake_init();

    // STEP 2 — bit-bang in tight hunt loop IMMEDIATELY.
    // We are now ~5 ms post power-on; the badge's boot ROM key-check window
    // should still be open if user power-cycles the badge after this.
    bool got_ack = false;
    absolute_time_t hunt_start = get_absolute_time();

    while (true) {
        int64_t elapsed_us = absolute_time_diff_us(hunt_start, get_absolute_time());
        if (elapsed_us >= (int64_t)HUNT_TIMEOUT_MS * 1000) {
            break;
        }

        if (handshake_attempt(1.0f)) {
            got_ack = true;
            break;
        }
    }

    // STEP 3 — hunt finished. Now init CYW43 for LED feedback.
    cyw43_arch_init();

    // STEP 4 — indicate result forever (until power-cycle).
    if (got_ack) {
        led_set(true);   // solid = success
        while (1) {
            sleep_ms(1000);
        }
    } else {
        // slow blink = timeout
        bool state = false;
        while (1) {
            state = !state;
            led_set(state);
            sleep_ms(BLINK_PERIOD_MS);
        }
    }
}
