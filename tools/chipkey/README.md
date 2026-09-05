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
