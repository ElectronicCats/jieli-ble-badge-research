#!/bin/bash
# Aggressive polling for JieLi USB device. Polls sysfs every 20 ms looking
# for a VID 3654 device, then dumps everything we can read without sudo.
set -u

OUTDIR="$(cd "$(dirname "$0")" && pwd)/captures"
mkdir -p "$OUTDIR"

declare -A seen_path

dump_device() {
    local devpath="$1"
    local ts="$(date +%Y%m%d-%H%M%S)"
    local out="$OUTDIR/$ts-$(basename "$devpath")"
    mkdir -p "$out"

    {
        echo "=== capture $ts ==="
        echo "sysfs path: $devpath"
        for f in idVendor idProduct manufacturer product serial bcdDevice \
                 bDeviceClass bDeviceSubClass bDeviceProtocol \
                 bNumConfigurations bMaxPacketSize0 \
                 speed busnum devnum version; do
            v="$(cat "$devpath/$f" 2>/dev/null)"
            [ -n "$v" ] && echo "$f: $v"
        done
        echo "---"
        echo "INTERFACES:"
        for iface in "$devpath"/*:*; do
            [ -d "$iface" ] || continue
            base="$(basename "$iface")"
            cls="$(cat "$iface/bInterfaceClass" 2>/dev/null)"
            sub="$(cat "$iface/bInterfaceSubClass" 2>/dev/null)"
            pro="$(cat "$iface/bInterfaceProtocol" 2>/dev/null)"
            nam="$(cat "$iface/interface" 2>/dev/null)"
            drv="$(basename "$(readlink "$iface/driver" 2>/dev/null)" 2>/dev/null)"
            echo "  $base  class=$cls sub=$sub proto=$pro  name=$nam  driver=$drv"
            for ep in "$iface"/ep_*; do
                [ -d "$ep" ] || continue
                t="$(cat "$ep/type" 2>/dev/null)"
                d="$(cat "$ep/direction" 2>/dev/null)"
                w="$(cat "$ep/wMaxPacketSize" 2>/dev/null)"
                i="$(cat "$ep/bInterval" 2>/dev/null)"
                echo "    $(basename "$ep")  type=$t dir=$d  wMaxPacket=$w  bInterval=$i"
            done
        done
        echo "---"
        echo "RAW DESCRIPTORS (hex):"
        if [ -r "$devpath/descriptors" ]; then
            xxd "$devpath/descriptors" 2>/dev/null
        else
            echo "(descriptors file not readable - need sudo for full lsusb -v)"
        fi
    } | tee "$out/dump.txt"

    lsusb 2>/dev/null | grep -i "3654:" > "$out/lsusb.txt"

    local idv idp
    idv="$(cat "$devpath/idVendor" 2>/dev/null)"
    idp="$(cat "$devpath/idProduct" 2>/dev/null)"
    [ -n "$idv" ] && [ -n "$idp" ] && \
        lsusb -v -d "$idv:$idp" >"$out/lsusb-v.txt" 2>&1

    echo ""
    echo "[$(date +%H:%M:%S.%3N)] Saved to: $out"
    echo ""
}

echo "Aggressive polling started (every 20 ms). Looking for VID 3654."
echo "Outputs go to: $OUTDIR/<ts>-<sysfs-name>/"
echo "Ctrl-C to stop."
echo ""

while true; do
    for vid_file in /sys/bus/usb/devices/*/idVendor; do
        [ -r "$vid_file" ] || continue
        if [ "$(cat "$vid_file" 2>/dev/null)" = "3654" ]; then
            devpath="$(dirname "$vid_file")"
            key="$(realpath "$devpath" 2>/dev/null)"
            if [ -z "${seen_path[$key]:-}" ]; then
                seen_path[$key]=1
                echo "[$(date +%H:%M:%S.%3N)] DETECTED at $devpath"
                dump_device "$devpath"
            fi
        fi
    done
    for k in "${!seen_path[@]}"; do
        [ -d "$k" ] || unset seen_path[$k]
    done
    sleep 0.02
done
