# ufw-repack

Pure-Python pack/parse/patch for JieLi OEM UFW firmware (BR35 / AC707N "E87").
Replaces the wine + `isd_download` repack path.

## What it does

Parses the layered UFW container (Qix wrapper → UFW entry table → chipkey-
encrypted `flash.bin` / JLFS), lets you patch bytes inside JLFS files, and
recomputes **every** CRC the bootloader's `dual_bank_update_verify` checks
(JLFS data + header CRCs, parent CRCs, UFW listcrc/hdrcrc, Qix wrapper CRC).
The chipkey is read from the firmware's own `isd_config.ini`. All JieLi ciphers
are stream-XOR, so CRC fields are patched in-place without full re-encryption.

## Scripts & usage

All paths below are relative to the repo root.

**`repack_ufw.py`** — the core parser/repacker (Qix-wrapped OEM UFW):
```
python3 tools/ufw-repack/repack_ufw.py info      <oem.ufw>
python3 tools/ufw-repack/repack_ufw.py roundtrip <oem.ufw> <out.ufw>   # must be byte-exact
python3 tools/ufw-repack/repack_ufw.py patch     <oem.ufw> <out.ufw> \
        --file app.bin --offset 0xNN --bytes DEADBEEF        # flags repeat in parallel
```

**`swap_app.py`** — replace the whole `app.bin` (code) inside a working OEM UFW
with your own, preserving all OEM flash geometry (0xFF-padded to the slot size):
```
python3 tools/ufw-repack/swap_app.py <base.ufw> <new_app.bin> <out.ufw>
```

**`transplant_ota.py`** — swap the `ota.bin` updater (UFW entry type 100) in a
Qix-wrapped UFW in place, resizing that one entry and recomputing all CRCs:
```
python3 tools/ufw-repack/transplant_ota.py <stager.ufw> <oem_ota.bin> <out.ufw>
```

**`parse_outer.py`** — dump just the outer UFW entry table (works with or
without the Qix wrapper; auto-detects the magic):
```
python3 tools/ufw-repack/parse_outer.py <file.ufw> [more.ufw ...]
```

**`make_app_only_ufw.py`** — strip `flash2.bin` (the dual-bank duplicate copy,
or any named entries) from a **vanilla** (non-Qix) UFW to make an app-only
single-copy image:
```
python3 tools/ufw-repack/make_app_only_ufw.py <in.ufw> <out.ufw> [entry_name ...]
```
(defaults to dropping `flash2.bin`).

**`dump2ufw.py`** — **`dump.bin` → loader-download `.ufw`** (the custom→OEM OTA
leg for `qix rcsp-flash`). Wraps the CODE region of a full 4 MB dump
(`dump[0:0xFC000]`, verbatim) into a VANILLA RCSP UFW whose loader rewrites CODE
`[0,0x17E000)` **in place** — it never stages over the resource partitions
(SDFILE `0x17E000` / VIRFAT `0x33E000`), unlike the `0xC0`/`0xD4` staging paths
which cannibalize that span and corrupt resources. The loader bundle (`ota.bin`,
incl. `lcflash_ota.bin`) can't be derived from a raw dump, so it ships here as
`loaderdl-base.ufw` (its flash.bin zeroed → code-free, ~172 KB packed in git).
So the user needs **only a dump** — no `.ufw` to supply:
```
python3 tools/ufw-repack/dump2ufw.py <dump.bin> <out.ufw>
```
Recomputes the flash.bin dcrc + listcrc + hdrcrc; uses the vendored `jltech/`
(no external checkout). Maintainer-only, to refresh the loader from a newer SDK
build: `dump2ufw.py --make-base <native_update.ufw>` (regenerates the base).

## Dependencies

- `jltech` from `tools/community-re/jl-misctools/firmware` — the canonical JieLi
  crypto: `jl_enc_cipher`, `jl_sfc_cipher`, `jl_crc16`, `chipkeybin_decode`.
  Every script imports crypto from there (the JieLi CRC-CCITT-false for the Qix
  wrapper is implemented locally).
- Python standard library otherwise.

## Status

active — the canonical, in-use repack tooling (repo tools table lists it
**active**; the legacy wine path is `scripts/repack-jieli-ufw.sh`).

> Note: `make_app_only_ufw.py` still hardcodes an absolute `sys.path` to
> `jl-misctools` (`/home/heikki/Documents/jl-misctools/firmware`) instead of the
> repo-relative `tools/community-re/jl-misctools/firmware` that the other scripts
> use — edit that line for your environment if the strip-flash2 path is needed.
