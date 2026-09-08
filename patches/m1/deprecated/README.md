# Deprecated M1 patches

Patches that were applied in M1 v1 (2026-05-19/20) but bricked the badge when
flashed. Moved here as a historical reference; **do NOT apply in future builds**.

## sdk-ui-disable.patch
Sets `TCFG_UI_ENABLE=0` in `SDK/apps/watch/board/br35/sdk_config.h`.
- **Reason for the drop**: it removes the init paths that start the application FW's BLE adv.
  Combined with `m1-rcsp-force-ble.patch` it is not enough — RCSP links the symbols
  but does not guarantee BLE adv without UI init.
- **Empirical evidence**: an internal post-flash test log (not published) —
  badge with no BLE adv, no button wake, no backlight post-flash.

## sdk-makefile-single-board.patch
Drops `board_ac7074_demo.c` and `board_ac707n_csc_demo.c` from `c_SRC_FILES`.
- **Reason for the drop**: byte-identical housekeeping (the removed .c files were
  `#ifdef CONFIG_BOARD_JL{xxx}_DEMO` undefined, they emitted 0 bytes anyway).
  No real value; it complicates the M1 v1 vs vanilla SDK diff without reducing risk.

## Replacement

M1 v2 (2026-05-21) uses `e_badge_707_sdk_200/board_ac707n_csc_demo` with
`TCFG_UI_ENABLE=1` and selects ST77916 explicitly. See
`docs/superpowers/specs/2026-05-21-m1-v2-safe-build-design.md`.
