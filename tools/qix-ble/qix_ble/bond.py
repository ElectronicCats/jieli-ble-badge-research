"""Establish a PERSISTENT BlueZ bond with the badge so the OTA client reconnects
encrypted.

Why this exists
---------------
The badge is a BLE HID device: its HID report characteristics are flagged
encryption-required (att-db 0x6112 / 0x610a), so BlueZ bonds the moment it discovers
the HID service — even though the OTA chars (AE02 / FD01 / FD03) themselves need no
encryption (their CCCDs are 0x010a). The badge's SM does a two-connection dance: a
FRESH pairing reports SM_EVENT_PAIR_SUB_PAIR_FAIL (0x03) but exchanges keys, and only
a RECONNECT (SM_EVENT_PAIR_SUB_RECONNECT_START, 0x01) actually reaches
`encryption change status=0`. A single-shot `BleakClient.connect()` hits the failing
first pairing and dies.

So we drive the dance here, once, with a Just-Works (NoInputNoOutput) agent, and mark
the device Trusted so BlueZ stores the LTK. After that the badge is bonded and every
qix connection is a fast sub=01 reconnect that encrypts — which is the state in which
`rcsp-flash` works. The bond persists across sessions, so this is a one-time step.

Linux/BlueZ only (uses dbus-fast, same as bluez_cleanup).
"""
from __future__ import annotations

import asyncio
import logging
import sys

if sys.platform.startswith("linux"):
    try:
        from dbus_fast import BusType, Variant
        from dbus_fast.aio import MessageBus
        from dbus_fast.service import ServiceInterface, method
        _DBUS_AVAILABLE = True
    except ImportError:
        _DBUS_AVAILABLE = False
else:
    _DBUS_AVAILABLE = False

from qix_ble.bluez_cleanup import BLUEZ, ADAPTER, _device_path, _adapter_path

log = logging.getLogger("qix_ble.bond")

AGENT_PATH = "/org/bluez/qix_ble_agent"


if _DBUS_AVAILABLE:
    class _JustWorksAgent(ServiceInterface):
        """NoInputNoOutput agent: auto-accepts every request (Just Works, no passkey).

        The badge SMP is NO_INPUT_NO_OUTPUT | BONDING | SECURE_CONNECTION, no MITM, so
        the pairing method is always Just Works — this agent simply confirms it."""

        def __init__(self):
            super().__init__("org.bluez.Agent1")

        @method()
        def Release(self):  # noqa: N802
            pass

        @method()
        def RequestPinCode(self, device: 'o') -> 's':  # noqa: N802,F821
            return "0000"

        @method()
        def RequestPasskey(self, device: 'o') -> 'u':  # noqa: N802,F821
            return 0

        @method()
        def DisplayPasskey(self, device: 'o', passkey: 'u', entered: 'q'):  # noqa: N802,F821
            pass

        @method()
        def DisplayPinCode(self, device: 'o', pincode: 's'):  # noqa: N802,F821
            pass

        @method()
        def RequestConfirmation(self, device: 'o', passkey: 'u'):  # noqa: N802,F821
            return  # auto-accept

        @method()
        def RequestAuthorization(self, device: 'o'):  # noqa: N802,F821
            return  # auto-accept

        @method()
        def AuthorizeService(self, device: 'o', uuid: 's'):  # noqa: N802,F821
            return  # auto-accept

        @method()
        def Cancel(self):  # noqa: N802
            pass


async def _get_device_iface(bus, mac, adapter, timeout):
    dev_path = _device_path(mac, adapter)
    intro = await asyncio.wait_for(bus.introspect(BLUEZ, dev_path), timeout=timeout)
    proxy = bus.get_proxy_object(BLUEZ, dev_path, intro)
    return proxy.get_interface("org.bluez.Device1"), proxy.get_interface(
        "org.freedesktop.DBus.Properties"
    )


async def _discover(bus, mac, adapter, timeout):
    """Make sure BlueZ has a Device1 object for `mac`. Use bleak's scanner (same as the
    transport) — it reliably creates+keeps the Device1 object, unlike a raw
    StartDiscovery/StopDiscovery which can drop it before we use it."""
    from bleak import BleakScanner
    dev = await BleakScanner.find_device_by_address(mac, timeout=timeout)
    if dev is None:
        log.warning("bleak scan didn't see %s", mac)


async def _bond_async(mac, adapter, retries, timeout):
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    agent = _JustWorksAgent()
    bus.export(AGENT_PATH, agent)
    # Register + make default so BlueZ routes Just-Works confirmations to us.
    am_intro = await bus.introspect(BLUEZ, "/org/bluez")
    am = bus.get_proxy_object(BLUEZ, "/org/bluez", am_intro).get_interface(
        "org.bluez.AgentManager1"
    )
    await am.call_register_agent(AGENT_PATH, "NoInputNoOutput")
    try:
        await am.call_request_default_agent(AGENT_PATH)
    except Exception as e:
        log.debug("RequestDefaultAgent: %s", e)

    try:
        await _discover(bus, mac, adapter, timeout)
        try:
            dev, props = await _get_device_iface(bus, mac, adapter, timeout)
        except Exception as e:
            log.error("badge %s not found by BlueZ (advertising?): %s", mac, e)
            return False

        async def paired() -> bool:
            try:
                v = await props.call_get("org.bluez.Device1", "Paired")
                return bool(v.value)
            except Exception:
                return False

        # Two-connection dance: attempt Pair; on failure disconnect + reconnect and
        # retry, giving the badge's SM the reconnect it needs to reach encryption.
        for attempt in range(1, retries + 1):
            if await paired():
                break
            log.info("bond attempt %d/%d", attempt, retries)
            try:
                await asyncio.wait_for(dev.call_connect(), timeout=timeout)
            except Exception as e:
                log.debug("Connect: %s", e)
            try:
                await asyncio.wait_for(dev.call_pair(), timeout=timeout)
                log.info("Pair() returned")
            except Exception as e:
                # PAIR_FAIL (sub=03) surfaces here as AuthenticationFailed; the keys may
                # still have been exchanged, so a reconnect next round can encrypt.
                log.info("Pair() attempt %d: %s", attempt, e)
            if await paired():
                break
            try:
                await asyncio.wait_for(dev.call_disconnect(), timeout=timeout)
            except Exception:
                pass
            await asyncio.sleep(2.0)

        ok = await paired()
        if ok:
            try:
                await props.call_set(
                    "org.bluez.Device1", "Trusted", Variant("b", True)
                )
                log.info("marked Trusted (bond persisted)")
            except Exception as e:
                log.debug("set Trusted: %s", e)
            # Leave the link free for the OTA client.
            try:
                await asyncio.wait_for(dev.call_disconnect(), timeout=timeout)
            except Exception:
                pass
        return ok
    finally:
        try:
            await am.call_unregister_agent(AGENT_PATH)
        except Exception:
            pass
        bus.unexport(AGENT_PATH)
        bus.disconnect()


def bond(mac: str, *, adapter: str = ADAPTER, retries: int = 5,
         timeout: float = 20.0) -> bool:
    """Establish a persistent, trusted bond with the badge (Just Works, 2-conn dance).

    Returns True if the device ends up Paired. Run ONCE; the bond persists so later
    `rcsp-flash` connections reconnect encrypted. Linux/BlueZ only."""
    if not _DBUS_AVAILABLE:
        log.error("bond requires Linux + dbus-fast")
        return False
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(
            asyncio.wait_for(
                _bond_async(mac, adapter, retries, timeout),
                timeout=retries * (timeout + 4) + 10,
            )
        )
    except Exception as e:
        log.error("bond(%s) failed: %s", mac, e)
        return False
    finally:
        loop.close()


def start_persistent_agent(adapter: str = ADAPTER):
    """Register a NoInputNoOutput default agent that stays alive on a daemon thread,
    so BlueZ routes Just-Works pairing confirmations to us while another thread (the
    OTA transport's bleak client) drives `Device1.Pair()`. Returns a stop() callable.

    Rationale: bleak's `client.pair()` fails with AuthenticationFailed when no agent is
    registered; with this agent up, the badge's fresh pairing reaches encryption in the
    SAME connection the OTA then runs on — so no reconnect, no PIN_KEY_MISS. No-op off
    Linux / without dbus-fast."""
    if not _DBUS_AVAILABLE:
        return lambda: None
    import threading
    ready = threading.Event()
    state = {"loop": None, "bus": None}

    async def _serve():
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        agent = _JustWorksAgent()
        bus.export(AGENT_PATH, agent)
        am_intro = await bus.introspect(BLUEZ, "/org/bluez")
        am = bus.get_proxy_object(BLUEZ, "/org/bluez", am_intro).get_interface(
            "org.bluez.AgentManager1"
        )
        try:
            await am.call_register_agent(AGENT_PATH, "NoInputNoOutput")
            await am.call_request_default_agent(AGENT_PATH)
        except Exception as e:
            log.debug("agent register: %s", e)
        state["bus"] = bus
        ready.set()
        await asyncio.get_event_loop().create_future()  # run until cancelled

    def _run():
        loop = asyncio.new_event_loop()
        state["loop"] = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_serve())
        except (asyncio.CancelledError, Exception):
            pass

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    ready.wait(timeout=8.0)

    def stop():
        loop = state["loop"]
        if loop and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
    return stop


def prepare_fresh_pairing(mac: str):
    """Forget any stale bond, bring up a Just-Works agent, and set QIX_PAIR=1 so the
    transport pairs FRESH on its connection. Shared by every command that must reach an
    encrypted link on this HID badge (rcsp-flash, deploy-fw, the stager). Returns a
    stop() to tear the agent down. No-op off Linux or with QIX_NO_PAIR set."""
    import os as _os
    if not sys.platform.startswith("linux") or _os.environ.get("QIX_NO_PAIR"):
        return lambda: None
    try:
        # DO NOT remove the bond here. Removing BlueZ's LTK while the badge keeps its own
        # (from the previous fresh pair) makes the badge do RECONNECT (sub=01) → PIN_KEY_MISS
        # (sub=02) → disconnect 0x05 in a tight loop that starves the OTA transfer. Keep the
        # bond so reconnects are clean; set QIX_FORGET_BOND=1 only to force a one-time re-pair
        # (e.g. after the host bond was wiped and the two sides genuinely diverged).
        if _os.environ.get("QIX_FORGET_BOND"):
            from qix_ble.bluez_cleanup import remove_device
            remove_device(mac)
        # Opt-in pre-flight (QIX_BREDR_OFF=1): stop BlueZ paging the HID appearance over
        # classic BR/EDR (page-timeout starves the LE OTA) + pin a tight LE conn interval.
        if _os.environ.get("QIX_BREDR_OFF"):
            from qix_ble.bluez_cleanup import set_bredr_off, set_le_conn_interval
            set_bredr_off()
            set_le_conn_interval()
        stop = start_persistent_agent()
        _os.environ["QIX_PAIR"] = "1"
        return stop
    except Exception as e:
        log.warning("pairing setup skipped: %s", e)
        return lambda: None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import sys as _sys
    _mac = _sys.argv[1] if len(_sys.argv) > 1 else "AA:BB:CC:DD:EE:FF"
    print("bonded" if bond(_mac) else "NOT bonded")
