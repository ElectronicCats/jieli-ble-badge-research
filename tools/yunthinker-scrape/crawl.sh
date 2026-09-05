#!/usr/bin/env bash
# yunthinker.net JL chip PDF crawler. Idempotent.
# Usage:
#   crawl.sh                              # full crawl: fetch all PDFs to manuales/jieli-reference/yunthinker/
#   crawl.sh --extract-families <file>    # print family category URLs found in an HTML file
#   crawl.sh --extract-products <file>    # print product page URLs found in an HTML file
#   crawl.sh --extract-pdfs <file>        # print PDF URLs found in an HTML file
set -euo pipefail

UA="Mozilla/5.0"
BASE="https://www.yunthinker.net"
OUT_ROOT="manuales/jieli-reference/yunthinker"
FETCH_LOG="$OUT_ROOT/.fetch-log"

extract_families() {
  # Family category URLs live in the nav menu as /product_cat/<slug>/ where slug
  # is one of jlbachip|jlbhc|blechip|audio (the 4 top-level families).
  grep -oE 'href="https://www\.yunthinker\.net/product_cat/(jlbachip|jlbhc|blechip|audio)/"' "$1" \
    | sed 's/^href="//;s/"$//' | sort -u
}

extract_products() {
  # Product URLs are /product/<slug>/
  grep -oE 'href="https://www\.yunthinker\.net/product/[^"]+/"' "$1" \
    | sed 's/^href="//;s/"$//' | sort -u
}

extract_pdfs() {
  grep -oE 'href="https://www\.yunthinker\.net/wp-content/uploads/[^"]+\.pdf"' "$1" \
    | sed 's/^href="//;s/"$//' | sort -u
}

case "${1:-}" in
  --extract-families) extract_families "$2"; exit 0 ;;
  --extract-products) extract_products "$2"; exit 0 ;;
  --extract-pdfs)     extract_pdfs     "$2"; exit 0 ;;
esac

# ---- Full crawl ----
mkdir -p "$OUT_ROOT"
touch "$FETCH_LOG"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

log() { echo "[crawl] $*" >&2; }

fetch_html() {
  local url="$1" out="$2"
  curl -sL -A "$UA" "$url" -o "$out"
}

download_pdf() {
  local url="$1" family="$2" model="$3"
  local rel="$OUT_ROOT/$family/$model"
  mkdir -p "$rel"
  local fname
  fname="$(basename "$url")"
  local dest="$rel/$fname"
  if [[ -f "$dest" ]]; then
    log "  skip (exists): $dest"
    return 0
  fi
  log "  GET $url"
  if curl -sLf -A "$UA" "$url" -o "$dest"; then
    echo "$(date -u +%FT%TZ) $url -> $dest" >> "$FETCH_LOG"
  else
    log "  FAIL $url"
    rm -f "$dest"
    return 1
  fi
  sleep 1  # be polite
}

# Step A: fetch top-level JL chip category, extract the 4 family URLs.
top_html="$tmp_dir/top.html"
fetch_html "$BASE/product_cat/jlchip/" "$top_html"
mapfile -t families < <(extract_families "$top_html")
log "found ${#families[@]} families"

# Step B: per family, fetch its category page(s) (including pagination) and
# extract product URLs. Each family page may also expose subfamily category
# URLs (the menu nests them); we treat all /product_cat/<chip-slug>/ URLs as
# additional category pages to walk, then collect /product/<model>/ leaves.
declare -A seen_cat=()
declare -A seen_product=()
declare -A product_family=()  # product URL -> family slug

walk_category() {
  local cat_url="$1" family_slug="$2"
  [[ -n "${seen_cat[$cat_url]:-}" ]] && return 0
  seen_cat[$cat_url]=1
  local page=1
  while :; do
    local url="$cat_url"
    [[ $page -gt 1 ]] && url="${cat_url}page/$page/"
    local html="$tmp_dir/cat-$(echo -n "$url" | md5sum | cut -d' ' -f1).html"
    if ! fetch_html "$url" "$html"; then
      break
    fi
    # If pagination overflowed, the response will redirect/404; bail when we see
    # zero products on the page (most reliable signal across themes).
    local before_products
    before_products="${#seen_product[@]}"
    while read -r p; do
      [[ -z "$p" ]] && continue
      if [[ -z "${seen_product[$p]:-}" ]]; then
        seen_product[$p]=1
        product_family[$p]="$family_slug"
      fi
    done < <(extract_products "$html")
    # Also discover nested sub-category URLs (subfamilies).
    while read -r c; do
      [[ -z "$c" ]] && continue
      # only walk sub-categories whose slug is NOT one of the top-level families
      case "$c" in
        */product_cat/jlchip/) continue ;;
      esac
      walk_category "$c" "$family_slug"
    done < <(grep -oE 'href="https://www\.yunthinker\.net/product_cat/[^"]+/"' "$html" \
              | sed 's/^href="//;s/"$//' | sort -u)
    local after_products="${#seen_product[@]}"
    [[ "$after_products" == "$before_products" ]] && break
    page=$((page+1))
    [[ $page -gt 20 ]] && break  # hard cap, sanity
    sleep 1
  done
}

for fam in "${families[@]}"; do
  fam_slug="$(echo "$fam" | sed -E 's#.*/product_cat/([^/]+)/#\1#')"
  log "walking family: $fam_slug"
  walk_category "$fam" "$fam_slug"
done

log "discovered ${#seen_product[@]} product pages"

# Step C: for each product page, fetch and grab PDFs.
ok=0; failed=0
for product_url in "${!seen_product[@]}"; do
  model_slug="$(echo "$product_url" | sed -E 's#.*/product/([^/]+)/#\1#')"
  family_slug="${product_family[$product_url]}"
  log "product: $family_slug/$model_slug"
  prod_html="$tmp_dir/prod-${model_slug}.html"
  if ! fetch_html "$product_url" "$prod_html"; then
    log "  fetch fail"
    failed=$((failed+1))
    continue
  fi
  pdf_count=0
  while read -r pdf; do
    [[ -z "$pdf" ]] && continue
    download_pdf "$pdf" "$family_slug" "$model_slug" && pdf_count=$((pdf_count+1)) || true
  done < <(extract_pdfs "$prod_html")
  log "  $pdf_count PDFs"
  ok=$((ok+1))
done

log "DONE: $ok products processed, $failed fetch failures"
