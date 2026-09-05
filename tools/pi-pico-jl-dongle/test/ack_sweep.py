#!/usr/bin/env python3
"""
Drive the Pi Pico handshaker via USB CDC to run a SWEEP across timing scales
and collect results in a table.
"""
import argparse
import re
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("install pyserial: pip install pyserial")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", default="/dev/ttyACM0")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--min", type=float, default=0.80)
    p.add_argument("--max", type=float, default=1.20)
    p.add_argument("--step", type=float, default=0.05)
    p.add_argument("--attempts", type=int, default=10)
    p.add_argument("--timeout", type=float, default=30.0)
    args = p.parse_args()

    with serial.Serial(args.port, args.baud, timeout=1.0) as s:
        time.sleep(0.5)
        s.reset_input_buffer()
        cmd = f"SWEEP {args.min:.2f} {args.max:.2f} {args.step:.2f} {args.attempts}\r\n"
        print(f"→ {cmd.strip()}")
        s.write(cmd.encode())

        results = []
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            line = s.readline().decode(errors="replace").strip()
            if not line:
                continue
            print(line)
            m = re.match(r"\[SWEEP\] scale=([\d.]+) ok=(\d+)/(\d+)", line)
            if m:
                scale = float(m.group(1))
                ok = int(m.group(2))
                total = int(m.group(3))
                results.append((scale, ok, total))
            if "[SWEEP] complete" in line:
                break

        print("\n=== Summary ===")
        print(f"{'scale':>6}  {'ok/total':>10}  {'rate':>6}")
        for scale, ok, total in results:
            rate = ok / total if total else 0
            print(f"{scale:>6.2f}  {ok:>4}/{total:<4}  {rate*100:>5.1f}%")

        accepting = [s for s, o, t in results if t and o == t]
        if accepting:
            print(f"\nAccepting range: {min(accepting):.2f} .. {max(accepting):.2f}")
            width = (max(accepting) - min(accepting)) / 2
            print(f"Margin: ±{width*100:.1f}%")
        else:
            print("\nNo fully-passing scale found")


if __name__ == "__main__":
    main()
