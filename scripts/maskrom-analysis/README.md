# MaskROM analysis scripts

Produced in session 2026-05-17 to validate the viability of Vector B (USB ISP MaskROM) on the AC707N (BR35) and to rule out that the OEM firmware has the soft-trigger path compiled in.

Source log: `../../internal research notes (not published)

## `maskrom_xref.py`

For each address declared in the e_badge SDK's `maskrom_stubs.ld`, it searches for 4-byte LE occurrences in the target `app.bin` binaries. Each hit = a MaskROM function referenced as a pointer (callback/struct member/literal pool).

Output table: 17 stubs effectively live in OEM/SDK + comparative matrix.

Caveat: it does NOT catch a direct `call rel32` (relative offset in the instruction). The result is a floor, not a ceiling.

```bash
python3 maskrom_xref.py
```

## `check_scsi_hook.py`

Checks whether `private_scsi_cmd()` + `go_mask_usb_updata()` are compiled into the firmwares — 3 pillars of evidence: strings, MaskROM xrefs of the boot path, UPDATA_TYPE enum constants as u32 literals.

Conclusion 2026-05-17: OEM PID 1558 V1.0.3 does NOT have the hook, confidence ~95% (caveat: log strip + literal pool scan not exhaustive). Definitive 30s empirical test pending (sg_raw FB/FC/FD to the enumerated UDISK).

```bash
python3 check_scsi_hook.py
```

## Hardcoded paths (reproducibility)

Both scripts assume:
- LD: `tools/community-re/jieli-sdks/e_badge_707_sdk_200/SDK/cpu/br35/maskrom_stubs.ld`
- Targets:
  - `hw-sessions/2026-05-17/pid1558-firmware-rev/app.bin` (real OEM PID 1558)
  - `hw-sessions/2026-05-16/libjl_ota_auth-reverse/ufw-unpacked/cloud_inner/files/app.bin` (OEM PID 1581)
  - `hw-sessions/2026-05-16/libjl_ota_auth-reverse/ufw-unpacked/self_built/files/app.bin` (SDK template)

If paths change, edit the constants at the top of each script.
