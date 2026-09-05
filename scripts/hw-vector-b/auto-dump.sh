#!/bin/bash
# Auto-dump /dev/sda when it appears.
#
# Usage:
#   sudo ./auto-dump.sh
#
# Polls every 50 ms for /dev/sda matching BR35 UDISK. When detected,
# (1) read raw blocks via dd to udisk-raw-<ts>.bin,
# (2) parallel mount RO and copy fs tree to udisk-fs-<ts>/.
# Stop when /dev/sda disappears or after dd completes.
set -u

OUTDIR="$(cd "$(dirname "$0")" && pwd)/dumps"
mkdir -p "$OUTDIR"

is_target_sda() {
    local sda="/dev/sda"
    [ -b "$sda" ] || return 1
    # Match by vendor=BR35 OR model containing UDISK (case-insensitive),
    # trimmed of padding. Belt-and-suspenders: also accept rev=1.00 + size matches
    # the 4672-block UDISK we saw in dmesg.
    local vendor model size
    vendor="$(cat /sys/block/sda/device/vendor 2>/dev/null | xargs)"   # xargs trims
    model="$(cat  /sys/block/sda/device/model  2>/dev/null | xargs)"
    size="$(cat   /sys/block/sda/size           2>/dev/null)"
    echo "  vendor='$vendor' model='$model' size=$size" >&2

    case "$vendor" in BR35|*BR35*) return 0;; esac
    case "$model"  in UDISK|*UDISK*) return 0;; esac
    [ "$size" = "4672" ] && return 0
    return 1
}

echo "Waiting for BR35 UDISK on /dev/sda..."
echo "Output dir: $OUTDIR"
echo "Reconnect the badge USB now."
echo ""

while true; do
    if is_target_sda; then
        ts="$(date +%Y%m%d-%H%M%S)"
        out="$OUTDIR/$ts"
        mkdir -p "$out"

        echo "[$(date +%H:%M:%S)] /dev/sda is BR35 UDISK — starting dump"

        # snapshot relevant kernel info
        {
            echo "--- /sys/block/sda info ---"
            for f in /sys/block/sda/{size,ro,removable} /sys/block/sda/device/{vendor,model,rev,scsi_level}; do
                [ -r "$f" ] && echo "$(basename "$(dirname "$f")")/$(basename "$f"): $(cat "$f")"
            done
            echo
            echo "--- partition table (sfdisk dump) ---"
            sfdisk -d /dev/sda 2>&1
        } > "$out/sda-info.txt"

        # Background: dd raw image (with dd_rescue-style options for resilience)
        (
            dd if=/dev/sda of="$out/udisk-raw.bin" bs=4096 \
               conv=noerror,sync iflag=fullblock status=progress 2>&1 \
               | tee "$out/dd.log"
            sha256sum "$out/udisk-raw.bin" > "$out/udisk-raw.sha256"
        ) &
        DD_PID=$!

        # Background: mount RO + cp -a tree
        (
            mkdir -p "$out/mnt"
            if mount -o ro,noatime /dev/sda "$out/mnt" 2>"$out/mount.log"; then
                ls -laR "$out/mnt" > "$out/ls-laR.txt" 2>&1
                # copy tree to outside the mount (preserve attrs)
                mkdir -p "$out/fs"
                cp -a "$out/mnt"/. "$out/fs/" 2>"$out/cp.log" || true
                umount "$out/mnt" 2>>"$out/mount.log" || true
                rmdir "$out/mnt" 2>/dev/null
            else
                echo "(mount failed - dd will still capture raw)"
            fi
        ) &
        MOUNT_PID=$!

        # Wait for dd to finish or device disappear
        while kill -0 "$DD_PID" 2>/dev/null; do
            if ! [ -b /dev/sda ]; then
                echo "[$(date +%H:%M:%S)] /dev/sda disappeared (USB disconnect)"
                # Let dd exit on its own (it will error out on next read)
                sleep 1
                break
            fi
            sleep 0.1
        done

        wait "$DD_PID" 2>/dev/null
        wait "$MOUNT_PID" 2>/dev/null

        echo "[$(date +%H:%M:%S)] dump done → $out"
        echo ""
        echo "Contents summary:"
        ls -la "$out"
        echo ""
        echo "Disconnect & reconnect to capture again, or Ctrl-C to stop."
        echo ""

        # Wait for sda to go away before looking again (otherwise we'd loop)
        while [ -b /dev/sda ]; do sleep 0.2; done
    fi
    sleep 0.05
done
