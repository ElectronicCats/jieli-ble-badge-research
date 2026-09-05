#!/bin/bash
# Monitor JieLi USB device (VID 3654) and dump descriptor info on connect.
# No sudo required for sysfs reads; for lsusb -v full descriptor a sudo
# helper is invoked if available. Output to ./captures/<ts>/

set -u

OUTDIR="$(cd "$(dirname "$0")" && pwd)/captures"
mkdir -p "$OUTDIR"

dump_device() {
    local devpath="$1"
    local ts="$(date +%Y%m%d-%H%M%S-%N)"
    local out="$OUTDIR/$ts"
    mkdir -p "$out"

    {
        echo "=== capture at $ts ==="
        echo "devpath: $devpath"
        echo "---"
        echo "idVendor:  $(cat "$devpath/idVendor"   2>/dev/null)"
        echo "idProduct: $(cat "$devpath/idProduct"  2>/dev/null)"
        echo "manufact:  $(cat "$devpath/manufacturer" 2>/dev/null)"
        echo "product:   $(cat "$devpath/product"   2>/dev/null)"
        echo "serial:    $(cat "$devpath/serial"    2>/dev/null)"
        echo "bcdDevice: $(cat "$devpath/bcdDevice" 2>/dev/null)"
        echo "bDeviceClass: $(cat "$devpath/bDeviceClass" 2>/dev/null)"
        echo "bDeviceSub:   $(cat "$devpath/bDeviceSubClass" 2>/dev/null)"
        echo "bDeviceProto: $(cat "$devpath/bDeviceProtocol" 2>/dev/null)"
        echo "bNumConfigurations: $(cat "$devpath/bNumConfigurations" 2>/dev/null)"
        echo "speed: $(cat "$devpath/speed" 2>/dev/null) Mbps"
        echo "---"
        echo "INTERFACES:"
        for iface in "$devpath"/*:*; do
            [ -d "$iface" ] || continue
            echo "  $(basename "$iface"):"
            echo "    class=$(cat "$iface/bInterfaceClass" 2>/dev/null)"
            echo "    subclass=$(cat "$iface/bInterfaceSubClass" 2>/dev/null)"
            echo "    proto=$(cat "$iface/bInterfaceProtocol" 2>/dev/null)"
            echo "    iface=$(cat "$iface/interface" 2>/dev/null)"
            echo "    driver=$(basename "$(readlink "$iface/driver" 2>/dev/null)" 2>/dev/null)"
            # endpoints
            for ep in "$iface"/ep_*; do
                [ -d "$ep" ] || continue
                echo "    ep $(basename "$ep"):"
                echo "      type=$(cat "$ep/type" 2>/dev/null)"
                echo "      direction=$(cat "$ep/direction" 2>/dev/null)"
                echo "      wMaxPacketSize=$(cat "$ep/wMaxPacketSize" 2>/dev/null)"
            done
        done
        echo "---"
        echo "RAW DESCRIPTORS (hex):"
        if [ -r "$devpath/descriptors" ]; then
            xxd "$devpath/descriptors" 2>/dev/null
        else
            echo "  (not readable without sudo)"
        fi
    } | tee "$out/sysfs-dump.txt"

    # also try full lsusb -v if device still present
    local idv idp
    idv="$(cat "$devpath/idVendor"  2>/dev/null)"
    idp="$(cat "$devpath/idProduct" 2>/dev/null)"
    if [ -n "$idv" ] && [ -n "$idp" ]; then
        lsusb -v -d "$idv:$idp" >"$out/lsusb-v.txt" 2>&1 || true
    fi

    echo "Saved to: $out"
}

# main loop: udevadm monitor for kernel USB add events
echo "Watching for JieLi USB device (VID 3654)..."
echo "Outputs: $OUTDIR/<timestamp>/"
echo "Ctrl-C to stop."
echo ""

udevadm monitor --kernel --subsystem-match=usb 2>/dev/null | \
while IFS= read -r line; do
    if [[ "$line" =~ ^KERNEL ]] && [[ "$line" =~ "(usb)" ]] && [[ "$line" == *" add "* ]]; then
        sysfs_path="/sys${line##* }"
        for _ in 1 2 3 4 5 6 7 8 9 10; do
            if [ -r "$sysfs_path/idVendor" ]; then
                idv="$(cat "$sysfs_path/idVendor" 2>/dev/null)"
                if [ "$idv" = "3654" ]; then
                    echo ""
                    echo "[$(date +%H:%M:%S)] Got JieLi device at $sysfs_path"
                    dump_device "$sysfs_path"
                    break
                fi
                break
            fi
            sleep 0.05
        done
    fi
done
