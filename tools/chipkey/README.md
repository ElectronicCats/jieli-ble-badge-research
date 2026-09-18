# Chipkey files for `isd_download -key` (anti-brick / Vector B recovery)

When an AC707N / BR35 chip has its chipkey burned into the eFuse,
`isd_download.exe` refuses to flash over USB MaskROM:

```
ERROR: Key has been burned with a key, please use the "-key <key file>" parameter to specify the key
```

The `-key` file required here is **not** a JieLi-signed L3KEY PEM (that was an
earlier wrong conclusion in our notes). It is the community-reversed
**chipkeyfile** format — a 72-char string: AES-ECB-wrapped 16-bit chipkey +
CRC32, with the marker `val2 == 0xa000` ("plain chipkey").

## Files

| File | Chipkey | Notes |
|------|---------|-------|
| `chipkey_9847.bin` | `0x9847` | PID 1558 badge (E87, Chip Version C, 4M flash). **HW-validated 2026-06-05**: passes the `-key` gate, isd_download flashes via USB MaskROM. |
| — | `0xB165` | **DG01 wristband** (`5A:73:53:3F:97:C6`, 4M flash). No key *file* is shipped: `gen_chipkey_keyfile.py` needs the `jltech.chipkeyfile` module from the un-vendored `jl-misctools` mirror, and the 8-hex-char trailer of the format is a CRC we have not reversed, so the file cannot be synthesized from `chipkey_9847.bin` either. It is only needed for the **USB** `isd_download -key` gate. The **BLE OTA** path does not need it — `scripts/build-badge-fw.sh --board dg01 --ota` packs with whatever key file isd_download finds and then rewrites the chipkey blob *and* re-scrambles the app area with `scripts/inject_chipkey.py --chipkey 0xB165`. |

## Recovering a chipkey from a flash dump

`0xB165` was recovered without USB access, by brute-forcing all 65 536 values against
`firmware/oem_DG01.bin` and scoring the descrambled result by byte entropy — the SFC
scramble is a 16-bit-keyed XOR keystream, so the right key stands out immediately
(entropy 6.94 vs 7.66 for the runner-up, against a 7.95 baseline). Confirm a candidate by
descrambling and looking for strings:

```sh
python3 tools/jl-flash-decrypt/jl_sfc_decrypt.py firmware/oem_DG01.bin 0xB165 plain.bin
strings -n 6 plain.bin | grep -iE 'AC707N|tp_cst816d|lcd_init'
```

Note the cipher has a 2-fold alias (`0xB165` and `0x417A` score identically); the value that
matches the unit's OEM `isd_config.ini` is the real one.

## Provenance of the value `0x9847`

The 16-bit chipkey is **not** invented — it is decoded from the OEM PID 1558
firmware. The `isd_config.ini` inside the factory UFW carries a 32-byte
`chipkey-bin` blob; `chipkeybin_decode()`
(`tools/community-re/jl-misctools/firmware/jltech/chipkeybin.py`,
also used by `scripts/inject_chipkey.py`) decodes it to `0x9847`. It is the
per-PID chipkey burned in the badge eFuse.

## Usage

```sh
isd_download.exe ... -key chipkey_9847.bin
```

## Regenerating / other chipkeys

Deterministic — same chipkey produces byte-identical output:

```sh
python3 -m venv .venv && .venv/bin/pip install pycryptodomex crcmod
.venv/bin/python scripts/gen_chipkey_keyfile.py 0x9847 -o tools/chipkey/chipkey_9847.bin
```

The embedded AES key is fixed for reproducibility; its value is cosmetic
because it is stored inside the file and `isd_download` reads it back to
decrypt. Any valid encoding of `0x9847` is accepted equally.

See also: `scripts/gen_chipkey_keyfile.py`, `scripts/inject_chipkey.py`,
memory `reference_chipkey_injection_app_area_rescramble`.
