# btsnoop-parser

Mini herramienta para extraer tráfico ATT (writes + notifications) de un capture btsnoop_hci.log de Android. Útil para RE de protocolos BLE custom (ej. extraer el handshake de auth entre app oficial y badge).

## Dependencias

`parse_att.py` usa **scapy** (para leer el pcap):

```bash
pip install scapy
```

`editcap` (paso 4 del pipeline) viene con `wireshark-common` (`apt install wireshark-common`).

## Pipeline

1. **Habilitar HCI snoop en el phone Android:** Settings → Developer Options → "Enable Bluetooth HCI snoop log" → ON. Reiniciar Bluetooth.
2. **Generar tráfico** que quieras capturar (pairing app↔device).
3. **Extraer el log** del phone (sin root):
   ```bash
   adb bugreport bugreport.zip
   unzip -j bugreport.zip 'FS/data/misc/bluetooth/logs/btsnoop_hci.log' -d .
   ```
4. **Convertir btsnoop → pcap** (`editcap` viene con wireshark-common):
   ```bash
   editcap btsnoop_hci.log capture.pcap
   ```
5. **Parsear ATT:**
   ```bash
   python3 parse_att.py capture.pcap --only-data --hide-discovery
   ```

## Output

Por línea: índice de packet, timestamp, op ATT, length, hex de la data (sin el op byte).

Filtros disponibles:
- `--only-data` — solo writes + notifications (oculta READs, ERRs, descubrimiento)
- `--hide-discovery` — oculta GATT discovery (READ_BY_GROUP/TYPE/FIND_INFO) y MTU/ERR

## Tips de RE

Para identificar **TX (phone→device)** vs **RX (device→phone)**:
- `WRITE_REQ (0x12)` y `WRITE_CMD (0x52)` son **siempre** TX
- `HANDLE_NOTIF (0x1B)` y `HANDLE_INDIC (0x1D)` son **siempre** RX
- `WRITE_RESP (0x13)` y `HANDLE_CONFIRM (0x1E)` son ACKs del peer

El primer byte de `data` en writes/notifs es típicamente el handle (LE u16) del característico. Después viene el payload del protocolo de aplicación.

## Caveats

- El script no extrae el connection handle BLE (necesario si hay múltiples devices en el snoop) — asume 1 device.
- Para captures multi-device, filtrar packets por `BTHCI_ACL.handle` antes de imprimir.

## Sesión de creación

Creado en `2026-05-14` durante Day 1 del e-badge E87 (ZRun). Permitió validar byte-a-byte el handshake JieLi RCSP de 6 pasos descrito en el repo comunitario `hybridherbst/web-bluetooth-e87`.
