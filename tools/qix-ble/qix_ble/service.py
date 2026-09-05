"""UUIDs de los services BLE del badge E87 (firmware JieLi).

Dos services coexisten en la misma conexión GATT:

- FD00 (custom 128-bit) — control sideband Qix (`com.qix.library`). Sin auth.
- AE00 (alias JieLi standard 16-bit) — RCSP / OTA / filesystem. Auth gate (6-step handshake).

Confirmados Day 1 contra HW E87 + btsnoop oficial de la app ZRun.
Detalles en internal notes (not published) sección "Servicios BLE".
"""

# ── Service Qix (FD00) ────────────────────────────────────────────────────────
SERVICE_UUID: str = "C2E6FD00-E966-1000-8000-BEF9C223DF6A"
CHAR_FD01_NOTIFY: str = "C2E6FD01-E966-1000-8000-BEF9C223DF6A"
CHAR_FD02_WRITE: str = "C2E6FD02-E966-1000-8000-BEF9C223DF6A"
CHAR_FD03_CTRL: str = "C2E6FD03-E966-1000-8000-BEF9C223DF6A"
# FD04/FD05 — listadas por community pero NO caracterizadas:
#   - web-bluetooth-e87/README.md las menciona con descripción idéntica a
#     FD02/FD01 ("9E-prefixed control writes/notifications"), tratadas como
#     duplicates redundantes en el primary control path.
#   - ebadge-python-cli/ebadge_cli/rcsp_transfer.py subscribe a FD05 durante
#     transfers RCSP (junto con AE02/FD01/FD03) — sugiere que FD05 SE activa
#     solo durante operaciones data-heavy (audio Opus, AI chat, file ops).
#   - ebadge-python-cli/internal notes (not published) lista ambas sin descripción
#     ("FD04: -", "FD05: FD05 通知").
# Probe nuestro 2026-05-16 18:54 sin auth: FD04 acepta writes (no rechaza)
# pero FD05 NO emite notifies espontáneos ni post-stimulus con cmd Qix
# estándar (0xC6). Consistente con la hipótesis "channel secundario para
# transferencias data-heavy", se activa solo post-auth + durante RCSP ops.
# Validación más profunda: capturar btsnoop_hci.log durante AI chat /
# audio recording / navigation en la app real, filtrar por handles de
# FD04/FD05, decode del wire format específico.
CHAR_FD04_WRITE2: str = "C2E6FD04-E966-1000-8000-BEF9C223DF6A"
CHAR_FD05_NOTIFY2: str = "C2E6FD05-E966-1000-8000-BEF9C223DF6A"

# NOTA 2026-05-16 night: FD05 NO incluido en subscribe set default —
# añadirlo rompe el OTA flow en E87 production firmware "11.1.0.4" (badge
# entra en silent reject del REQ_UPDATE). Probable que el FW Huazhen
# verifique el subscribe set y rechace clientes con chars "non-standard"
# subscribed. Si necesitás FD05 para investigación (audio Opus, AI chat),
# pasalo explícito como override, NO lo agregues al tuple default.
NOTIFY_CHARS: tuple[str, ...] = (CHAR_FD01_NOTIFY, CHAR_FD03_CTRL)

# ── Service RCSP JieLi (AE00) ─────────────────────────────────────────────────
RCSP_SERVICE_UUID: str = "0000AE00-0000-1000-8000-00805F9B34FB"
CHAR_AE01_WRITE: str = "0000AE01-0000-1000-8000-00805F9B34FB"
CHAR_AE02_NOTIFY: str = "0000AE02-0000-1000-8000-00805F9B34FB"

RCSP_NOTIFY_CHARS: tuple[str, ...] = (CHAR_AE02_NOTIFY,)

# ── Conjunto unificado: usado por transport para subscribirse en _connect() ──
ALL_NOTIFY_CHARS: tuple[str, ...] = NOTIFY_CHARS + RCSP_NOTIFY_CHARS
