"""Minimal, self-contained JieLi crypto/CRC helpers used by ufw-repack.

Vendored (pure Python, no external deps) so `swap_app.py` / `repack_ufw.py` run from this
repo alone — no `jl-misctools` checkout and no `crcmod` needed. The RE'd algorithms match the
upstream `jltech` package (and the standalone cipher copy in `tools/jl-flash-decrypt`).
"""
