#!/usr/bin/env python3
"""Strip an LVGL 8 fmt_txt font (.c, bpp4, FORMAT0_TINY + sparse cmaps) down
to the glyphs we actually need, and emit a new .c.

Usage:
    python3 tools/strip_lvgl_font.py <input.c> <output.c> [keep_spec]

    keep_spec: a python expression that evaluates to a set of codepoints,
               default "set(range(32, 91))"  (space .. 'Z': digits, %, A-Z).

The generated font keeps the same cmap slot layout (chars 32..126, glyph ids
1..95) but non-kept glyphs are blanked (zero box, zero bitmap).  The sparse
symbol cmap + kerning tables are dropped entirely (kern_classes = 0).
The script also decodes the output and renders kept glyphs as ASCII art for
visual verification.
"""
import re
import sys

def parse(text):
    m = re.search(r'glyph_bitmap\[\] = \{(.*?)\n\};', text, re.S)
    assert m, "glyph_bitmap not found"
    bitmap_bytes = [int(x, 16) for x in re.findall(r'0x[0-9a-fA-F]+', m.group(1))]
    m2 = re.search(r'glyph_dsc\[\] = \{(.*?)\n\};', text, re.S)
    assert m2, "glyph_dsc not found"
    entries = []
    for line in m2.group(1).splitlines():
        e = re.search(
            r'\{\.bitmap_index = (\d+), \.adv_w = (-?\d+), \.box_w = (\d+), '
            r'\.box_h = (\d+), \.ofs_x = (-?\d+), \.ofs_y = (-?\d+)\}', line)
        if e:
            entries.append({k: int(v) for k, v in zip(
                ('bitmap_index', 'adv_w', 'box_w', 'box_h', 'ofs_x', 'ofs_y'),
                e.groups())})
    return bitmap_bytes, entries

def glyph_size(e, bpp=4):
    return (e['box_w'] * e['box_h'] * bpp + 7) // 8

def check_consistency(bitmap_bytes, entries, bpp=4):
    for i in range(len(entries) - 1):
        sz = glyph_size(entries[i], bpp)
        assert entries[i + 1]['bitmap_index'] - entries[i]['bitmap_index'] == sz, \
            f"size mismatch at entry {i}: {sz} vs " \
            f"{entries[i+1]['bitmap_index'] - entries[i]['bitmap_index']}"
    last = entries[-1]
    assert last['bitmap_index'] + glyph_size(last, bpp) == len(bitmap_bytes), \
        "trailing bitmap bytes mismatch"

def emit_c(dst, header, bitmap_body, dsc_body, mid, tail):
    with open(dst, 'w') as f:
        f.write(header)
        f.write(bitmap_body)
        f.write(dsc_body)
        f.write(mid)
        f.write(tail)

def decode_glyph(bitmap, e, bpp=4):
    w, h = e['box_w'], e['box_h']
    idx = e['bitmap_index']
    rows = []
    ramp = ' .:-=+*#%@'
    for y in range(h):
        line = ''
        for x in range(w):
            bit_pos = (y * w + x) * bpp
            byte = bitmap[idx + bit_pos // 8]
            v = (byte >> (bit_pos % 8)) & (2 ** bpp - 1)
            line += ramp[(v * 10) // (2 ** bpp)]
        rows.append(line)
    return rows

def main():
    src, dst = sys.argv[1], sys.argv[2]
    keep = eval(sys.argv[3]) if len(sys.argv) > 3 else set(range(32, 91))
    text = open(src).read()

    bitmap_bytes, entries = parse(text)
    check_consistency(bitmap_bytes, entries)
    print(f"input: {len(bitmap_bytes)} bitmap bytes, {len(entries)} dsc entries")

    header = text.split(
        'static LV_ATTRIBUTE_LARGE_CONST const uint8_t glyph_bitmap[] = {')[0] + \
        'static LV_ATTRIBUTE_LARGE_CONST const uint8_t glyph_bitmap[] = {\n'
    tail = text.split('/*Initialize a public general font descriptor*/')[1]

    new_bitmap = []
    new_dsc = []
    running = 0
    kept = []
    for i, e in enumerate(entries[:96]):          # ids 0..95 (reserved + 32..126)
        if i == 0:                                # reserved id 0
            new_dsc.append(dict(bitmap_index=0, adv_w=0, box_w=0, box_h=0,
                                ofs_x=0, ofs_y=0))
            continue
        ch = 31 + i                               # id 1 -> U+0020
        size = glyph_size(e)
        if ch in keep and size > 0:
            payload = bitmap_bytes[e['bitmap_index']:e['bitmap_index'] + size]
            new_dsc.append(dict(e, bitmap_index=running))
            new_bitmap += payload
            kept.append((ch, e, running))
            running += size
        else:
            new_dsc.append(dict(bitmap_index=running, adv_w=e['adv_w'],
                                box_w=0, box_h=0, ofs_x=0, ofs_y=0))

    body = []
    for ch, e, ofs in kept:
        label = chr(ch)
        if label == ' ':
            label = ' '
        body.append(f'    /* U+{ch:04X} "{label}" */')
        payload = new_bitmap[ofs:ofs + glyph_size(e)]
        for j in range(0, len(payload), 12):
            body.append('    ' + ', '.join(f'0x{b:02x}' for b in payload[j:j + 12]) + ',')
    body.append('};')
    bitmap_body = '\n'.join(body) + '\n\n'

    dsc_lines = ['static const lv_font_fmt_txt_glyph_dsc_t glyph_dsc[] = {']
    for i, e in enumerate(new_dsc):
        comment = ' /* id = 0 reserved */' if i == 0 else ''
        dsc_lines.append(
            f"    {{.bitmap_index = {e['bitmap_index']}, .adv_w = {e['adv_w']}, "
            f".box_w = {e['box_w']}, .box_h = {e['box_h']}, "
            f".ofs_x = {e['ofs_x']}, .ofs_y = {e['ofs_y']}}}{comment},")
    dsc_lines.append('};')
    dsc_body = '\n'.join(dsc_lines) + '\n\n'

    mid = """/*---------------------
 *  CHARACTER MAPPING
 *--------------------*/

/*Collect the unicode lists and glyph_id offsets*/
static const lv_font_fmt_txt_cmap_t cmaps[] = {
    {
        .range_start = 32, .range_length = 95, .glyph_id_start = 1,
        .unicode_list = NULL, .glyph_id_ofs_list = NULL, .list_length = 0, .type = LV_FONT_FMT_TXT_CMAP_FORMAT0_TINY
    }
};

/*--------------------
 *  ALL CUSTOM DATA
 *--------------------*/

#if LV_VERSION_CHECK(8, 0, 0)
/*Store all the custom data of the font*/
static  lv_font_fmt_txt_glyph_cache_t cache;
static const lv_font_fmt_txt_dsc_t font_dsc = {
#else
static lv_font_fmt_txt_dsc_t font_dsc = {
#endif
    .glyph_bitmap = glyph_bitmap,
    .glyph_dsc = glyph_dsc,
    .cmaps = cmaps,
    .kern_dsc = NULL,
    .kern_scale = 16,
    .cmap_num = 1,
    .bpp = 4,
    .kern_classes = 0,
    .bitmap_format = 0,
#if LV_VERSION_CHECK(8, 0, 0)
    .cache = &cache
#endif
};

"""

    emit_c(dst, header, bitmap_body, dsc_body, mid, tail)
    print(f"output: {len(new_bitmap)} bitmap bytes, {len(new_dsc)} dsc entries, "
          f"{len(kept)} glyphs kept")

    # --- verify: re-parse the emitted file and render kept glyphs ---
    out_text = open(dst).read()
    out_bitmap, out_entries = parse(out_text)
    check_consistency(out_bitmap, out_entries)
    render = [ord('0') + i for i in range(10)] + [ord('%')] + \
             [ord(c) for c in 'CHARGING']
    seen = set()
    for ch in render:
        if ch in seen:
            continue
        seen.add(ch)
        i = ch - 31
        e = out_entries[i]
        if e['box_w'] == 0:
            print(f"U+{ch:04X}: BLANK")
            continue
        rows = decode_glyph(out_bitmap, e)
        print(f"U+{ch:04X} '{chr(ch)}' ({e['box_w']}x{e['box_h']}):")
        for r in rows:
            print('    ' + r)

if __name__ == '__main__':
    main()
