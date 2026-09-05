# jl-flash-decrypt — JieLi AC70x (BR35) flash CODE decrypt/repack

Decrypts (and re-encrypts) the CODE/app region of a raw JieLi BR35 flash dump so
it can be disassembled, patched, and flashed back. This is the same cipher the SDK
(`isd_download`) applies when loading firmware onto the badge — reversed.

## Why it's needed

On AC70x the app is stored **XOR-scrambled** with the SFC (serial-flash-controller)
cipher, keyed by the chip's eFuse **chipkey**. The Boot ROM descrambles it on the fly
during XIP, so a raw flash read comes back at entropy ~8.0 (looks encrypted). To read
or patch the code you must apply the same cipher.

## The cipher (`jl_sfc_cipher`)

Per 32-byte block, XOR the block against the `jl_enc_cipher` LFSR keystream seeded with:

```
effective_key = chipkey ^ (((block_offset - base) >> 2) & 0xFFFF)
```

- `base = 0x1000`, and the app area starts at flash **0x1000** (uboot header sits below).
- `jl_enc_cipher` = CRC16 poly `0x1021` LFSR, XOR keystream (JieLi "ENC" cipher).
- **XOR-symmetric** and **per-block independent** → the same call re-encrypts, and a
  patch only rewrites the 32-byte block(s) it touches (so a 1-char string edit changes
  a single 4 KB flash sector).

Reconstructed from the docstring of `scripts/inject_chipkey.py:_rescramble_app_area`
(the vendored `jl-misctools` clone that defined `jl_sfc_cipher` is missing locally).

## Usage

```bash
# get the chipkey:  jluboottool.py --device /dev/sgN exit  → "Chip key: 0xXXXX"
python3 jl_sfc_decrypt.py <flash_dump.bin> <chipkey_hex> [out.bin]
python3 jl_sfc_decrypt.py --check          # XOR-symmetry self-test
```

Import form:

```python
import jl_sfc_decrypt as J
plain = J.decrypt_code(open('full_flash.bin','rb').read(), 0xB165)   # descramble app area
```

## Validated

- 2nd badge (chipkey **0xB165**): entropy 8.0 → 6.9 after descramble; `lcd_init` and other
  code strings appear. Round-trip reflash proven (see
  `dumps/newbadge_watch_W003/README.md` §Reprogramming proof).
