# blimit-re

Codec for JieLi `blimit.bin` — the 144-byte licence/limit blob encrypted with
JieLi's `cd03_crc_encode` 16-bit LFSR XOR stream cipher.

## What it does

Recovers the 16-bit LFSR key from known plaintext (the `JLDGAUTH` magic + the
known size field), then decrypts the 144-byte `blimit.bin`, parses its struct
(magic, sizes, CRCs, chip info, timestamp, payload), and can re-encrypt /
round-trip it. The cipher is symmetric (encrypt == decrypt); reversed from
`isd_download_br35.exe` (`FUN_004a6560`).

## Usage

`decrypt_blimit.py` is a single-file `argparse` script:

```
python3 decrypt_blimit.py decrypt <blimit.bin>     # recover key, hexdump + parse fields
python3 decrypt_blimit.py roundtrip <blimit.bin>   # verify decrypt→encrypt→ct is byte-exact
```

`decrypt` prints the recovered key state and every parsed field (with the
timestamp rendered as UTC). `roundtrip` exits 0 on match, 1 on mismatch.
The `encrypt_blimit()` / `cd03_crc_encode()` functions are importable for reuse.

## Dependencies

Python standard library only (`struct`, `argparse`, `datetime`, `pathlib`).
No third-party or in-repo imports.

## Status

experimental — a standalone RE codec; listed as **experimental** in the repo's
tools table. Input must be exactly 144 bytes.
