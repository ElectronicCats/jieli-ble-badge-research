# Deprecated M1 patches

Patches que se aplicaron en M1 v1 (2026-05-19/20) pero brickearon el badge al
flashear. Movidos aquí como referencia histórica; **NO aplicar en builds futuros**.

## sdk-ui-disable.patch
Setea `TCFG_UI_ENABLE=0` en `SDK/apps/watch/board/br35/sdk_config.h`.
- **Razón del drop**: elimina los init paths que arrancan BLE adv del application FW.
  Combinado con `m1-rcsp-force-ble.patch` no es suficiente — RCSP linkea símbolos
  pero no garantiza BLE adv sin UI init.
- **Evidencia empírica**: an internal post-flash test log (not published) —
  badge sin BLE adv, sin button wake, sin backlight post-flash.

## sdk-makefile-single-board.patch
Dropa `board_ac7074_demo.c` y `board_ac707n_csc_demo.c` de `c_SRC_FILES`.
- **Razón del drop**: housekeeping byte-identical (los .c quitados estaban
  `#ifdef CONFIG_BOARD_JL{xxx}_DEMO` undefined, emitían 0 bytes igual).
  Sin valor real; complica el diff M1 v1 vs vanilla SDK sin reducir riesgo.

## Reemplazo

M1 v2 (2026-05-21) usa `e_badge_707_sdk_200/board_ac707n_csc_demo` con
`TCFG_UI_ENABLE=1` y selecciona ST77916 explícitamente. Ver
`docs/superpowers/specs/2026-05-21-m1-v2-safe-build-design.md`.
