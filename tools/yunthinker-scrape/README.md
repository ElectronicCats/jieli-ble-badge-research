# yunthinker-scrape

Idempotent crawler that mirrors JieLi chip datasheet PDFs from `yunthinker.net`
into the repo's reference tree.

## What it does

Walks `yunthinker.net`'s JL-chip catalog: fetches the top `product_cat/jlchip/`
page, extracts the 4 top-level families (`jlbachip`, `jlbhc`, `blechip`,
`audio`), walks each family's category pages (following pagination + nested
sub-categories), collects `/product/<model>/` pages, and downloads every
`wp-content/uploads/*.pdf` into
`manuales/jieli-reference/yunthinker/<family>/<model>/`. Already-downloaded
files are skipped; downloads are logged to `.fetch-log` and paced with a 1s
sleep to stay polite.

## Usage

Run from the repo root (output paths are relative):

```
tools/yunthinker-scrape/crawl.sh                        # full crawl (fetch all PDFs)
```

Helper modes that just extract URLs from a saved HTML file (used by the tests):

```
tools/yunthinker-scrape/crawl.sh --extract-families <file.html>
tools/yunthinker-scrape/crawl.sh --extract-products <file.html>
tools/yunthinker-scrape/crawl.sh --extract-pdfs     <file.html>
```

Tests: `tools/yunthinker-scrape/tests/test_crawl.sh` (bats-less runner, uses the
HTML fixtures under `tests/fixtures/`).

## Dependencies

- `bash`, `curl`, plus coreutils (`grep -oE`, `sed`, `mktemp`, `md5sum`,
  `mapfile`). No Python, no third-party libraries.

## Status

reference — a one-off documentation-gathering utility (not in the repo's main
tools table; feeds the `manuales/` reference tree).
