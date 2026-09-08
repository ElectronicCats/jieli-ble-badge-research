# btsnoop-parser

Mini tool to extract ATT traffic (writes + notifications) from an Android btsnoop_hci.log capture. Useful for RE of custom BLE protocols (e.g. extracting the auth handshake between the official app and the badge).

## Dependencies

`parse_att.py` uses **scapy** (to read the pcap):

```bash
pip install scapy
```

`editcap` (step 4 of the pipeline) comes with `wireshark-common` (`apt install wireshark-common`).

## Pipeline

1. **Enable HCI snoop on the Android phone:** Settings → Developer Options → "Enable Bluetooth HCI snoop log" → ON. Restart Bluetooth.
2. **Generate the traffic** you want to capture (pairing app↔device).
3. **Extract the log** from the phone (no root):
   ```bash
   adb bugreport bugreport.zip
   unzip -j bugreport.zip 'FS/data/misc/bluetooth/logs/btsnoop_hci.log' -d .
   ```
4. **Convert btsnoop → pcap** (`editcap` comes with wireshark-common):
   ```bash
   editcap btsnoop_hci.log capture.pcap
   ```
5. **Parse ATT:**
   ```bash
   python3 parse_att.py capture.pcap --only-data --hide-discovery
   ```

## Output

Per line: packet index, timestamp, ATT op, length, hex of the data (without the op byte).

Available filters:
- `--only-data` — only writes + notifications (hides READs, ERRs, discovery)
- `--hide-discovery` — hides GATT discovery (READ_BY_GROUP/TYPE/FIND_INFO) and MTU/ERR

## RE tips

To identify **TX (phone→device)** vs **RX (device→phone)**:
- `WRITE_REQ (0x12)` and `WRITE_CMD (0x52)` are **always** TX
- `HANDLE_NOTIF (0x1B)` and `HANDLE_INDIC (0x1D)` are **always** RX
- `WRITE_RESP (0x13)` and `HANDLE_CONFIRM (0x1E)` are ACKs from the peer

The first byte of `data` in writes/notifs is typically the characteristic's handle (LE u16). After it comes the application protocol payload.

## Caveats

- The script does not extract the BLE connection handle (needed if there are multiple devices in the snoop) — it assumes 1 device.
- For multi-device captures, filter packets by `BTHCI_ACL.handle` before printing.

## Creation session

Created on `2026-05-14` during Day 1 of the e-badge E87 (ZRun). It allowed byte-by-byte validation of the 6-step JieLi RCSP handshake described in the community repo `hybridherbst/web-bluetooth-e87`.
