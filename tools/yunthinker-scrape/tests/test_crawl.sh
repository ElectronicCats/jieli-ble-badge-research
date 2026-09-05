#!/usr/bin/env bash
# Lightweight bats-less test runner: each test_* function returns 0=pass non-zero=fail
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRAPE_DIR="$(cd "$HERE/.." && pwd)"
PASS=0; FAIL=0

assert_eq() {
  local actual="$1" expected="$2" name="$3"
  if [[ "$actual" == "$expected" ]]; then
    echo "PASS: $name"; PASS=$((PASS+1))
  else
    echo "FAIL: $name"; echo "  expected: $expected"; echo "  actual:   $actual"
    FAIL=$((FAIL+1))
  fi
}

assert_contains() {
  local haystack="$1" needle="$2" name="$3"
  if echo "$haystack" | grep -qF "$needle"; then
    echo "PASS: $name"; PASS=$((PASS+1))
  else
    echo "FAIL: $name (needle '$needle' not in output)"
    FAIL=$((FAIL+1))
  fi
}

test_extract_family_urls_from_category() {
  local out
  out="$(bash "$SCRAPE_DIR/crawl.sh" --extract-families "$HERE/fixtures/category_jlchip.html")"
  assert_contains "$out" "https://www.yunthinker.net/product_cat/jlbachip/" "family jlbachip"
  assert_contains "$out" "https://www.yunthinker.net/product_cat/jlbhc/"    "family jlbhc"
  assert_contains "$out" "https://www.yunthinker.net/product_cat/blechip/"  "family blechip"
  assert_contains "$out" "https://www.yunthinker.net/product_cat/audio/"    "family audio"
}

test_extract_pdfs_from_product() {
  local out
  out="$(bash "$SCRAPE_DIR/crawl.sh" --extract-pdfs "$HERE/fixtures/product_ac7069f.html")"
  assert_contains "$out" "AC7069F-Datasheet-V1.0.pdf"           "datasheet PDF"
  assert_contains "$out" "ac7069flanyatoudaierjibiaozhunyuanli" "schematic PDF"
}

test_extract_family_urls_from_category
test_extract_pdfs_from_product

echo ""
echo "Results: $PASS pass, $FAIL fail"
exit $FAIL
