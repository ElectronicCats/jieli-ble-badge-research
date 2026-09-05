#include "unity.h"
#include "decoder.h"
#include "jl_protocol.h"

void setUp(void) {}
void tearDown(void) {}

void test_decoder_reset_zeroes_state(void) {
    decoder_state_t s = { .shift_reg = 0xDEAD, .bit_count = 7 };
    decoder_reset(&s);
    TEST_ASSERT_EQUAL_HEX16(0, s.shift_reg);
    TEST_ASSERT_EQUAL_UINT8(0, s.bit_count);
}

void test_decoder_match_after_exact_0x16EF(void) {
    decoder_state_t s;
    decoder_reset(&s);

    uint16_t pattern = JL_KEY;
    bool last_result = false;
    for (int i = 15; i >= 0; i--) {
        last_result = decoder_push_bit(&s, (pattern >> i) & 1);
    }
    TEST_ASSERT_TRUE(last_result);
    TEST_ASSERT_EQUAL_UINT8(0, s.bit_count);
}

void test_decoder_no_match_on_wrong_pattern(void) {
    decoder_state_t s;
    decoder_reset(&s);
    bool last_result = false;
    for (int i = 0; i < 16; i++) {
        last_result = decoder_push_bit(&s, 0);
    }
    TEST_ASSERT_FALSE(last_result);
}

void test_decoder_partial_bits_no_match(void) {
    decoder_state_t s;
    decoder_reset(&s);
    uint16_t pattern = JL_KEY;
    for (int i = 15; i > 0; i--) {
        bool r = decoder_push_bit(&s, (pattern >> i) & 1);
        TEST_ASSERT_FALSE(r);
    }
}

void test_decoder_watchdog_resets_state(void) {
    decoder_state_t s;
    decoder_reset(&s);
    decoder_push_bit(&s, 1);
    decoder_push_bit(&s, 0);
    decoder_push_bit(&s, 1);
    decoder_push_bit(&s, 1);
    decoder_push_bit(&s, 0);
    TEST_ASSERT_EQUAL_UINT8(5, s.bit_count);

    decoder_watchdog_check(&s, /*us_since_last_edge=*/100);
    TEST_ASSERT_EQUAL_UINT8(0, s.bit_count);
    TEST_ASSERT_EQUAL_HEX16(0, s.shift_reg);
}

void test_decoder_watchdog_no_reset_within_window(void) {
    decoder_state_t s;
    decoder_reset(&s);
    decoder_push_bit(&s, 1);
    decoder_watchdog_check(&s, /*us_since_last_edge=*/20);
    TEST_ASSERT_EQUAL_UINT8(1, s.bit_count);
}

int main(void) {
    UNITY_BEGIN();
    RUN_TEST(test_decoder_reset_zeroes_state);
    RUN_TEST(test_decoder_match_after_exact_0x16EF);
    RUN_TEST(test_decoder_no_match_on_wrong_pattern);
    RUN_TEST(test_decoder_partial_bits_no_match);
    RUN_TEST(test_decoder_watchdog_resets_state);
    RUN_TEST(test_decoder_watchdog_no_reset_within_window);
    return UNITY_END();
}
