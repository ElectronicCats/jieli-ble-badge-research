#pragma once
// JieLi USB_KEY protocol constants (per Kramer scope measurements,
// AC6905A/BR17; same protocol assumed for BR35/AC707N per kagaimiq doc)

#define JL_KEY            0x16EF
#define JL_KEY_BITS       16

#define KRAMER_CLK_LOW_US     9.625
#define KRAMER_CLK_HIGH_US    9.667
#define KRAMER_BIT_PERIOD_US  19.292
#define KRAMER_SKEW_NS        125

#define KRAMER_PAD_HIGH_US    11.21
#define KRAMER_PAD_LOW_US     7.0

#define ACK_MIN_LOW_US        1000
#define ACK_TIMEOUT_US        2000

#define MAX_RETRIES           5

#define SWEEP_MIN_SCALE       0.80
#define SWEEP_MAX_SCALE       1.20
#define SWEEP_STEP            0.05

#define DECODER_WATCHDOG_US   50
