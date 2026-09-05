# MaskROM analysis scripts

Producidos en sesión 2026-05-17 para validar viabilidad de Vector B (USB ISP MaskROM) en AC707N (BR35) y descartar que el firmware OEM tenga el path soft-trigger compilado.

Bitácora origen: `../../internal research notes (not published)

## `maskrom_xref.py`

Para cada address declarada en `maskrom_stubs.ld` del SDK e_badge, busca occurrencias 4-byte LE en `app.bin` binarios target. Cada hit = función MaskROM referenciada como puntero (callback/struct member/literal pool).

Tabla output: 17 stubs efectivamente vivos en OEM/SDK + matriz comparativa.

Caveat: NO captura `call rel32` directo (offset relativo en la instruction). Resultado es piso, no techo.

```bash
python3 maskrom_xref.py
```

## `check_scsi_hook.py`

Verifica si `private_scsi_cmd()` + `go_mask_usb_updata()` están compilados en firmwares — 3 pilares evidencia: strings, MaskROM xrefs del boot path, constantes UPDATA_TYPE enum como u32 literal.

Conclusión 2026-05-17: OEM PID 1558 V1.0.3 NO tiene el hook, confidence ~95% (caveat: log strip + literal pool scan no exhaustivo). Test empírico definitivo de 30s pendiente (sg_raw FB/FC/FD al UDISK enumerated).

```bash
python3 check_scsi_hook.py
```

## Paths hardcoded (reproducibilidad)

Ambos scripts asumen:
- LD: `tools/community-re/jieli-sdks/e_badge_707_sdk_200/SDK/cpu/br35/maskrom_stubs.ld`
- Targets:
  - `hw-sessions/2026-05-17/pid1558-firmware-rev/app.bin` (OEM real PID 1558)
  - `hw-sessions/2026-05-16/libjl_ota_auth-reverse/ufw-unpacked/cloud_inner/files/app.bin` (OEM PID 1581)
  - `hw-sessions/2026-05-16/libjl_ota_auth-reverse/ufw-unpacked/self_built/files/app.bin` (SDK template)

Si paths cambian, editar las constantes al inicio de cada script.
