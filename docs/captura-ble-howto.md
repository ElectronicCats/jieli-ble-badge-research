# Captura BLE en vivo — receta operativa

> Receta para usar cuando lleguen los e-badges (~mediados mayo 2026). **Aún no validada** contra superband/zrun (sin HW). FeralRF tampoco re-validado en esta sesión: T5 del Plan 1 quedó ⏸ pendiente del CatSniffer físico.

## Pre-requisitos

| Tool | Estado |
|---|---|
| CatSniffer V3+ | ⏸ pendiente — no disponible en esta máquina al cierre Gate 1 |
| FeralRF instalado | ⏸ pendiente de validación |
| Sniffle como fallback | ⏸ pendiente |
| `bluetoothctl` (BlueZ) | ✅ disponible (Linux 6.8) |
| Wireshark con btatt decoder | (a instalar al recibir HW) |

> **Bloqueo:** Step 1 del plan original ("Re-validar FeralRF") requiere conectar el CatSniffer y correr `python python/examples/ble_sniffer.py`. No ejecutado en esta sesión. Documentado para realizar al recibir el HW del CatSniffer.

## Setup base (al recibir CatSniffer)

> `FeralRF` y `CatSniffer-Tools` son repos externos (Electronic Cats), clonados
> fuera de este proyecto donde prefieras. Abajo se usan las rutas placeholder
> `~/FeralRF` y `~/CatSniffer-Tools`; ajústalas a donde los tengas clonados.

```bash
# 1. Verificar que FeralRF está clonado y con .venv
cd ~/FeralRF
ls .venv/bin/python || python -m venv .venv
source .venv/bin/activate
pip install -e .

# 2. Conectar CatSniffer al USB y confirmar device
ls /dev/ttyACM*  # esperado: /dev/ttyACM0

# 3. Si firmware no es FeralRF reciente, flashear con Catnip
cd ~/CatSniffer-Tools
python catnip.py -f feralrf  # o sniffle si se prefiere

# 4. Test smoke con FeralRF
cd ~/FeralRF
source .venv/bin/activate
python python/examples/ble_sniffer.py --channel 37
# Esperado: paquetes de advertising de algún device BLE cercano (teléfono o cualquier wearable)
```

Si FeralRF responde con paquetes en pantalla → ✅ ready. Si silencia o errores → fallback a Sniffle (paso siguiente).

## Captura de advertising — descubrir el badge

Antes de conectar la app oficial. El badge debe estar encendido pero NO conectado a un teléfono.

```bash
# Channel 37
python python/examples/ble_sniffer.py --channel 37 --duration 30 --output captures/2026-05-XX-superband-adv-37.pcap

# Channel 38
python python/examples/ble_sniffer.py --channel 38 --duration 30 --output captures/2026-05-XX-superband-adv-38.pcap

# Channel 39
python python/examples/ble_sniffer.py --channel 39 --duration 30 --output captures/2026-05-XX-superband-adv-39.pcap
```

Abrir el `.pcap` en Wireshark, filtrar por `btle.advertising_address` y identificar la MAC del badge. Anotar.

**Validaciones contra hipótesis:**

| Hipótesis | Cómo validar |
|---|---|
| SuperBand advertise como `DG01` | Buscar `btle.advertising_data.flags` y campo `Local Name` con valor "DG01" o variante |
| SuperBand expone NUS `6e400001-…dcca9d` | Una vez conectado, listar services con `bluetoothctl info <MAC>` |
| ZRun advertise como `ZRun` o un BT name | Buscar Local Name en advertising |
| ZRun expone service `C2E6FD00-E966-1000-8000-BEF9C223DF6A` | `bluetoothctl info <MAC>` listará UUIDs |
| Manufacturer Specific Data con company ID | Buscar `btle.advertising_data.manufacturer_specific_data` |

## Captura de connection — tráfico app↔badge

Una vez identificada la MAC:

```bash
# Apagar Bluetooth del teléfono Android (que tiene la app oficial)
# Iniciar follow desde FeralRF
python python/examples/ble_sniffer.py --follow <MAC-del-badge> --output captures/2026-05-XX-superband-connection.pcap

# Encender Bluetooth del teléfono y abrir app SuperBand/ZRun
# Operar la app, una acción a la vez:
#   1. Conexión inicial
#   2. Subir un watchface
#   3. Cambiar brillo
#   4. Sincronizar tiempo
#   5. (ZRun) Mediciones de salud
#   6. (ZRun) Probar AI conversacional (mic)

# Cada acción produce paquetes — anotar en bitácora qué acción correspondió a qué timestamp
```

## Análisis post-captura

```bash
# Convertir a JSON para análisis programático
tshark -r captures/2026-05-XX-superband-connection.pcap -T json > captures/2026-05-XX-superband.json

# Filtrar writes a la TX char
tshark -r captures/.pcap -Y 'btatt.opcode == 0x12 || btatt.opcode == 0x52' -T fields \
  -e frame.time_relative -e btatt.handle -e btatt.value
```

**Para validar el wire-format Baji de SuperBand:**

```bash
# Buscar bytes de inicio 0xCD en payloads
tshark -r captures/.pcap -Y 'btatt.value matches "^cd"' -T fields -e btatt.value | head -10
```

**Para validar el wire-format Qix de ZRun:**

```bash
# Buscar bytes de inicio 0x9E en payloads
tshark -r captures/.pcap -Y 'btatt.value matches "^9e"' -T fields -e btatt.value | head -10
```

## Fallback a Sniffle (si FeralRF pierde paquetes)

```bash
# Flashear CatSniffer con Sniffle
cd ~/CatSniffer-Tools
python catnip.py -f sniffle

# Capturar
cd ~/<repo-Sniffle>
python sniff_receiver.py -m <MAC-del-badge> | wireshark -k -i -
```

## Validación cruzada (deseable)

Capturar 30s con FeralRF, luego mismo escenario 30s con Sniffle. Comparar:
- Conteo de paquetes
- MACs visibles
- Diferencia en payloads (deberían ser idénticos byte-a-byte)

Si difiere mucho, probable issue en uno de los dos firmwares — reportar a FeralRF si es el que pierde.

## Validaciones específicas para Track HW

### Para SuperBand (validar wire-format Baji)

1. ¿Frame inicia con `0xCD`? Sí → confirma framing del doc protocol-superband.md
2. ¿Tras `0xCD` viene length BE16, luego `0x25`? Sí → confirma magic Baji
3. ¿`MEDIA_LIST_REQUEST` (módulo 0x02 cmd 0x00) responde con lista? Validar comportamiento del badge
4. ¿FILE_TRANSFER opcodes producen el flow esperado?

### Para ZRun (validar wire-format Qix)

1. ¿Frame inicia con `0x9E`? Sí → confirma framing del doc protocol-zrun.md
2. ¿Tras `0x9E` viene checksum (suma simple bytes 2..N), luego flagStatus, cmd, len LE16? Validar parser
3. **Vector A — TEST_GET_FLASH:** mandar `0x9E [chk] [flags] 0xAA [len LE16] [address LE32] [length LE32]` (formato exacto a validar) y esperar respuesta con bytes de flash
4. ¿`UpdateManager.init(isFirmwareUpdate=true)` + header malformado provoca rechazo o brick? — **probar solo con backup hecho**
5. ¿Audio capture en mic produce paquetes en `C2E6FD01` notify? (cmds AI_RECORD)

## Output esperado

Por cada acción operada en la app:
- `captures/2026-MM-DD-<badge>-<accion>.pcap`
- Anotación en bitácora del día con timestamp y acción
- Análisis con tshark + comparación contra hipótesis del doc protocol-*.md
- Si discrepancia: actualizar el doc protocol-*.md con findings reales

## Errores comunes (de experiencia con CatSniffer)

| Síntoma | Causa | Mitigación |
|---|---|---|
| FeralRF muestra "no devices" | Firmware no es FeralRF o canal incorrecto | Re-flashear con Catnip; probar canales 37/38/39 |
| Pierde la conexión a mid-capture | Channel hopping mal seguido | Aumentar prioridad del proceso; correr en máquina dedicada |
| Wireshark no muestra btatt | Versión vieja de Wireshark | Actualizar a 4.x+ |
| Paquetes corruptos | Antena dañada o ruido RF | Probar otro CatSniffer; mover a entorno con menos WiFi |

## Bloqueos al cierre Gate 1

- T5 del Plan 1 (validar FeralRF) **no completado**: requiere CatSniffer físico
- Esta receta **no probada end-to-end** — se prueba al recibir badges
- Plan 2 (Track HW) debe incluir como T1: re-correr esta receta con setup completo y actualizar las secciones que requieran corrección
