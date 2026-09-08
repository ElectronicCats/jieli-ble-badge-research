# Live BLE capture — operational recipe

> Recipe to use once the e-badges arrive (~mid-May 2026). **Not yet validated** against superband/zrun (no HW). FeralRF also not re-validated in this session: T5 of Plan 1 was left ⏸ pending the physical CatSniffer.

## Prerequisites

| Tool | Status |
|---|---|
| CatSniffer V3+ | ⏸ pending — not available on this machine at Gate 1 close |
| FeralRF installed | ⏸ pending validation |
| Sniffle as fallback | ⏸ pending |
| `bluetoothctl` (BlueZ) | ✅ available (Linux 6.8) |
| Wireshark with btatt decoder | (to install on receiving HW) |

> **Blocker:** Step 1 of the original plan ("Re-validate FeralRF") requires connecting the CatSniffer and running `python python/examples/ble_sniffer.py`. Not executed in this session. Documented to be done on receiving the CatSniffer HW.

## Base setup (on receiving the CatSniffer)

> `FeralRF` and `CatSniffer-Tools` are external repos (Electronic Cats), cloned
> outside this project wherever you prefer. Below, the placeholder paths
> `~/FeralRF` and `~/CatSniffer-Tools` are used; adjust them to wherever you have them cloned.

```bash
# 1. Verify FeralRF is cloned and has a .venv
cd ~/FeralRF
ls .venv/bin/python || python -m venv .venv
source .venv/bin/activate
pip install -e .

# 2. Connect the CatSniffer to USB and confirm the device
ls /dev/ttyACM*  # expected: /dev/ttyACM0

# 3. If the firmware is not recent FeralRF, flash it with Catnip
cd ~/CatSniffer-Tools
python catnip.py -f feralrf  # or sniffle if preferred

# 4. Smoke test with FeralRF
cd ~/FeralRF
source .venv/bin/activate
python python/examples/ble_sniffer.py --channel 37
# Expected: advertising packets from some nearby BLE device (phone or any wearable)
```

If FeralRF responds with packets on screen → ✅ ready. If silent or errors → fall back to Sniffle (next step).

## Advertising capture — discover the badge

Before connecting the official app. The badge must be powered on but NOT connected to a phone.

```bash
# Channel 37
python python/examples/ble_sniffer.py --channel 37 --duration 30 --output captures/2026-05-XX-superband-adv-37.pcap

# Channel 38
python python/examples/ble_sniffer.py --channel 38 --duration 30 --output captures/2026-05-XX-superband-adv-38.pcap

# Channel 39
python python/examples/ble_sniffer.py --channel 39 --duration 30 --output captures/2026-05-XX-superband-adv-39.pcap
```

Open the `.pcap` in Wireshark, filter by `btle.advertising_address` and identify the badge MAC. Note it down.

**Validations against hypotheses:**

| Hypothesis | How to validate |
|---|---|
| SuperBand advertises as `DG01` | Look for `btle.advertising_data.flags` and the `Local Name` field with value "DG01" or a variant |
| SuperBand exposes NUS `6e400001-…dcca9d` | Once connected, list services with `bluetoothctl info <MAC>` |
| ZRun advertises as `ZRun` or a BT name | Look for the Local Name in advertising |
| ZRun exposes service `C2E6FD00-E966-1000-8000-BEF9C223DF6A` | `bluetoothctl info <MAC>` will list the UUIDs |
| Manufacturer Specific Data with a company ID | Look for `btle.advertising_data.manufacturer_specific_data` |

## Connection capture — app↔badge traffic

Once the MAC is identified:

```bash
# Turn off the Bluetooth of the Android phone (which has the official app)
# Start the follow from FeralRF
python python/examples/ble_sniffer.py --follow <MAC-del-badge> --output captures/2026-05-XX-superband-connection.pcap

# Turn on the phone Bluetooth and open the SuperBand/ZRun app
# Operate the app, one action at a time:
#   1. Initial connection
#   2. Upload a watchface
#   3. Change brightness
#   4. Sync time
#   5. (ZRun) Health measurements
#   6. (ZRun) Test conversational AI (mic)

# Each action produces packets — note in the log which action corresponded to which timestamp
```

## Post-capture analysis

```bash
# Convert to JSON for programmatic analysis
tshark -r captures/2026-05-XX-superband-connection.pcap -T json > captures/2026-05-XX-superband.json

# Filter writes to the TX char
tshark -r captures/.pcap -Y 'btatt.opcode == 0x12 || btatt.opcode == 0x52' -T fields \
  -e frame.time_relative -e btatt.handle -e btatt.value
```

**To validate SuperBand's Baji wire-format:**

```bash
# Look for the 0xCD start byte in payloads
tshark -r captures/.pcap -Y 'btatt.value matches "^cd"' -T fields -e btatt.value | head -10
```

**To validate ZRun's Qix wire-format:**

```bash
# Look for the 0x9E start byte in payloads
tshark -r captures/.pcap -Y 'btatt.value matches "^9e"' -T fields -e btatt.value | head -10
```

## Fallback to Sniffle (if FeralRF drops packets)

```bash
# Flash the CatSniffer with Sniffle
cd ~/CatSniffer-Tools
python catnip.py -f sniffle

# Capture
cd ~/<repo-Sniffle>
python sniff_receiver.py -m <MAC-del-badge> | wireshark -k -i -
```

## Cross-validation (desirable)

Capture 30s with FeralRF, then the same scenario 30s with Sniffle. Compare:
- Packet count
- Visible MACs
- Difference in payloads (should be byte-for-byte identical)

If they differ significantly, likely an issue in one of the two firmwares — report it to FeralRF if it is the one dropping.

## Track-HW-specific validations

### For SuperBand (validate the Baji wire-format)

1. Does the frame start with `0xCD`? Yes → confirms the framing in protocol-superband.md
2. After `0xCD`, does a BE16 length come, then `0x25`? Yes → confirms the Baji magic
3. Does `MEDIA_LIST_REQUEST` (module 0x02 cmd 0x00) respond with a list? Validate the badge's behavior
4. Do the FILE_TRANSFER opcodes produce the expected flow?

### For ZRun (validate the Qix wire-format)

1. Does the frame start with `0x9E`? Yes → confirms the framing in protocol-zrun.md
2. After `0x9E`, does a checksum (simple sum of bytes 2..N) come, then flagStatus, cmd, len LE16? Validate the parser
3. **Vector A — TEST_GET_FLASH:** send `0x9E [chk] [flags] 0xAA [len LE16] [address LE32] [length LE32]` (exact format to be validated) and expect a response with flash bytes
4. Does `UpdateManager.init(isFirmwareUpdate=true)` + a malformed header cause a reject or a brick? — **test only with a backup made**
5. Does audio capture on the mic produce packets on the `C2E6FD01` notify? (AI_RECORD cmds)

## Expected output

For each action operated in the app:
- `captures/2026-MM-DD-<badge>-<accion>.pcap`
- An annotation in the day's log with timestamp and action
- Analysis with tshark + comparison against the hypotheses in protocol-*.md
- If a discrepancy: update protocol-*.md with the real findings

## Common errors (from CatSniffer experience)

| Symptom | Cause | Mitigation |
|---|---|---|
| FeralRF shows "no devices" | Firmware is not FeralRF or wrong channel | Re-flash with Catnip; try channels 37/38/39 |
| Loses the connection mid-capture | Channel hopping followed poorly | Raise the process priority; run on a dedicated machine |
| Wireshark does not show btatt | Old Wireshark version | Update to 4.x+ |
| Corrupt packets | Damaged antenna or RF noise | Try another CatSniffer; move to an environment with less WiFi |

## Blockers at Gate 1 close

- T5 of Plan 1 (validate FeralRF) **not completed**: requires the physical CatSniffer
- This recipe **not tested end-to-end** — it will be tested on receiving the badges
- Plan 2 (Track HW) should include as T1: re-run this recipe with the full setup and update the sections that need correction
