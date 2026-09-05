"""Tests de RcspSession.browse() — request encoding + push collection + entry parse.

KAT vectors EXACTOS del btsnoop Day 1 (`hw-sessions/2026-05-14/zrun/btsnoop.pcap`):

Packet 590 (Phone → AE01, StartFileBrowse request):
  fedcba c0 0c 000f 76 00 0a 0001 00000002 0400 00000000 ef
                    ^^ sn=0x76 | type=0x00 readNum=0x0a startIdx=0x0001 BE
                    devHandler=0x00000002 BE pathLen=0x0004 LE path=[0x00000000]

Packet 596 (Device → AE02, ack response):
  fedcba 00 0c 0002 00 76 ef
                    ^^ status=0x00 sn=0x76 (echoes request sn)

Packet 597 (Device → AE02, push entry):
  fedcba 80 01 000d 01 0c 0a 00 00 00 02 00 01 03 42 41 47 ef
                    ^^ sn=0x01 | xm_op=0x0c | entry data:
                    header=0x0a (is_folder, ascii, dev_index=2=SD1)
                    cluster=0x00000002 BE
                    file_num=0x0001 BE
                    name_len=3, name="BAG"

Packet 598 (Device → AE02, StopFileBrowse command):
  fedcba c0 0d 0002 02 01 ef
                    ^^ sn=0x02, payload=[0x01]
"""
import pytest
from tests.conftest import MockTransport
from qix_ble.rcsp_frame import RcspFrame
from qix_ble.rcsp_session import (
    RcspSession, FileEntry,
    DEV_HANDLER_SD1, DEV_HANDLER_FLASH,
    CMD_START_FILE_BROWSE, CMD_STOP_FILE_BROWSE, CMD_DATA_PUSH,
    FLAG_PHONE_REQ, FLAG_DEVICE_RESP, FLAG_DEVICE_PUSH, FLAG_DEVICE_CMD,
    _parse_file_entries, _parse_inner,
)


def _auth_done_mock() -> MockTransport:
    m = MockTransport()
    m._auth_done = True
    return m


def test_browse_request_body_byte_exact_vs_packet_590():
    """browse(type=0, read_num=10, start_index=1, dev_handler=SD1, clusters=[0])
    produce EXACTAMENTE el wire del packet 590 del btsnoop Day 1."""
    t = _auth_done_mock()
    # Script ack (status=0, sn=0x76) → device StopBrowse (sn=0x02 payload=01)
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_RESP, cmd=CMD_START_FILE_BROWSE,
                                     payload=bytes.fromhex("0076")))
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_CMD, cmd=CMD_STOP_FILE_BROWSE,
                                     payload=bytes.fromhex("0201")))

    sess = RcspSession(t)
    sess._seq = 0x76  # Match packet 590 sn exact
    sess.browse(type=0, read_num=10, start_index=1, dev_handler=DEV_HANDLER_SD1, clusters=[0])

    # Verify TX[0] byte-exact contra packet 590
    expected_request = bytes.fromhex("fedcba" "c0" "0c" "000f"
                                      "76" "00" "0a" "0001" "00000002" "0400" "00000000"
                                      "ef")
    assert t.ae01_sent[0] == expected_request

    # Verify TX[1] = ack del StopFileBrowse (status=0, sn=0x02)
    expected_stop_ack = bytes.fromhex("fedcba" "00" "0d" "0002" "00" "02" "ef")
    assert t.ae01_sent[1] == expected_stop_ack


def test_browse_seq_increments_per_call():
    """Cada browse() incrementa el sn (offset 7 en wire frame)."""
    t = _auth_done_mock()
    # 2 calls — para cada una: ack + StopBrowse
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_RESP, cmd=CMD_START_FILE_BROWSE,
                                     payload=bytes.fromhex("0000")))
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_CMD, cmd=CMD_STOP_FILE_BROWSE,
                                     payload=bytes.fromhex("00")))
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_RESP, cmd=CMD_START_FILE_BROWSE,
                                     payload=bytes.fromhex("0001")))
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_CMD, cmd=CMD_STOP_FILE_BROWSE,
                                     payload=bytes.fromhex("01")))
    sess = RcspSession(t)
    sess.browse(dev_handler=DEV_HANDLER_FLASH)
    sess.browse(dev_handler=DEV_HANDLER_FLASH)

    # Browse TX is ae01_sent[0] and ae01_sent[2] (1 and 3 are StopBrowse acks).
    # Wire offset 7 = inner sn
    assert t.ae01_sent[0][7] == 0x00
    assert t.ae01_sent[2][7] == 0x01


def test_browse_collects_push_entry_and_parses_BAG():
    """KAT real packet 597: push entry → parse decodes folder "BAG" cluster=2 dev_index=SD1."""
    t = _auth_done_mock()
    sess = RcspSession(t)
    sess._seq = 0x76
    # Ack initial (status=0, sn=0x76)
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_RESP, cmd=CMD_START_FILE_BROWSE,
                                     payload=bytes.fromhex("0076")))
    # Push entry exacto del packet 597: sn=0x01 xm_op=0x0c entry="BAG"
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_PUSH, cmd=CMD_DATA_PUSH,
                                     payload=bytes.fromhex("010c0a00000002000103424147")))
    # Device StopFileBrowse (real packet 598: sn=0x02 payload=01)
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_CMD, cmd=CMD_STOP_FILE_BROWSE,
                                     payload=bytes.fromhex("0201")))

    entries = sess.browse(type=0, read_num=10, start_index=1,
                          dev_handler=DEV_HANDLER_SD1, clusters=[0])

    assert len(entries) == 1
    e = entries[0]
    assert e.name == "BAG"
    assert e.type_name == "folder"   # header bit0=0
    assert e.cluster == 2
    assert e.id == 1                  # file_num
    # type echoed del query (0=folder)
    assert e.type == 0

    # Verify phone sent: request + push ACK + StopBrowse ACK = 3 TX
    assert len(t.ae01_sent) == 3
    # Push ACK structure: response 0x00 cmd=0x01 body=[status=0][sn=0x01][xm_op=0x0c]
    push_ack = t.ae01_sent[1]
    expected_push_ack = bytes.fromhex("fedcba" "00" "01" "0003" "00" "01" "0c" "ef")
    assert push_ack == expected_push_ack


def test_browse_status_error_raises_badge_rejected():
    """Ack con status=0xff → BadgeRejected con state."""
    from qix_ble.errors import BadgeRejected
    t = _auth_done_mock()
    # status=0xff (error) sn=0x00
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_RESP, cmd=CMD_START_FILE_BROWSE,
                                     payload=bytes.fromhex("ff00")))
    sess = RcspSession(t)
    with pytest.raises(BadgeRejected, match="status"):
        sess.browse()


def test_browse_default_args_use_flash_handler_empty_path():
    """Defaults: type=0, read_num=10, start_index=0, dev=FLASH, clusters=[]."""
    t = _auth_done_mock()
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_RESP, cmd=CMD_START_FILE_BROWSE,
                                     payload=bytes.fromhex("0000")))
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_CMD, cmd=CMD_STOP_FILE_BROWSE,
                                     payload=bytes.fromhex("00")))
    sess = RcspSession(t)
    sess.browse()

    # Body: sn(0x00) type(0) readNum(10) startIndex(0000) devHandler(00000003=FLASH)
    # pathLen(0000) path=()
    expected = bytes.fromhex("fedcba" "c0" "0c" "000b"
                              "00" "00" "0a" "0000" "00000003" "0000"
                              "ef")
    assert t.ae01_sent[0] == expected


def test_browse_all_iterates_4_handlers():
    """browse_all() prueba USB, SD0, SD1, Flash en orden, retorna entries unidos.
    Cada handler: ack + (optional pushes) + StopFileBrowse cmd."""
    t = _auth_done_mock()
    # USB → done sin entries (ack + StopBrowse)
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_RESP, cmd=CMD_START_FILE_BROWSE,
                                     payload=bytes.fromhex("0000")))
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_CMD, cmd=CMD_STOP_FILE_BROWSE,
                                     payload=bytes.fromhex("00")))
    # SD0 → done sin entries
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_RESP, cmd=CMD_START_FILE_BROWSE,
                                     payload=bytes.fromhex("0001")))
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_CMD, cmd=CMD_STOP_FILE_BROWSE,
                                     payload=bytes.fromhex("01")))
    # SD1 → 1 entry BAG, then StopBrowse
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_RESP, cmd=CMD_START_FILE_BROWSE,
                                     payload=bytes.fromhex("0002")))
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_PUSH, cmd=CMD_DATA_PUSH,
                                     payload=bytes.fromhex("010c0a00000002000103424147")))
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_CMD, cmd=CMD_STOP_FILE_BROWSE,
                                     payload=bytes.fromhex("02")))
    # Flash → done sin entries
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_RESP, cmd=CMD_START_FILE_BROWSE,
                                     payload=bytes.fromhex("0003")))
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_CMD, cmd=CMD_STOP_FILE_BROWSE,
                                     payload=bytes.fromhex("03")))

    sess = RcspSession(t)
    entries = sess.browse_all()
    assert len(entries) == 1
    assert entries[0].name == "BAG"


def test_browse_all_skips_handlers_with_errors():
    """Si un handler responde status=0xff (e.g., USB no presente), skip + continúa."""
    t = _auth_done_mock()
    # USB → error → skip
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_RESP, cmd=CMD_START_FILE_BROWSE,
                                     payload=bytes.fromhex("ff00")))
    # SD0 → done sin entries
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_RESP, cmd=CMD_START_FILE_BROWSE,
                                     payload=bytes.fromhex("0001")))
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_CMD, cmd=CMD_STOP_FILE_BROWSE,
                                     payload=bytes.fromhex("01")))
    # SD1 → done sin entries
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_RESP, cmd=CMD_START_FILE_BROWSE,
                                     payload=bytes.fromhex("0002")))
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_CMD, cmd=CMD_STOP_FILE_BROWSE,
                                     payload=bytes.fromhex("02")))
    # Flash → done sin entries
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_RESP, cmd=CMD_START_FILE_BROWSE,
                                     payload=bytes.fromhex("0003")))
    t.queue_rcsp_response(RcspFrame(flag=FLAG_DEVICE_CMD, cmd=CMD_STOP_FILE_BROWSE,
                                     payload=bytes.fromhex("03")))

    sess = RcspSession(t)
    entries = sess.browse_all()
    assert entries == []


def test_parse_file_entries_decodes_BAG_from_packet_597_inner():
    """Unit test del _parse_file_entries con bytes del entry_data del packet 597.
    El inner del push body 010c0a00000002000103424147:
      sn=01 xm_op=0c entry_data=0a00000002000103424147 (11B)
    """
    entry_data = bytes.fromhex("0a00000002000103424147")
    entries = _parse_file_entries(entry_data, query_type=0)
    assert len(entries) == 1
    e = entries[0]
    assert e.name == "BAG"
    assert e.type_name == "folder"
    assert e.cluster == 2
    assert e.id == 1   # file_num
    # header byte 0x0a = 0b00001010:
    #   bit0 = 0 → is_folder
    #   bit1 = 1 → ascii (NOT unicode)
    #   bits 2-6 = (0x0a >> 2) & 0x1F = 2 → dev_index=SD1


def test_parse_inner_command_vs_response_byte_exact():
    """Verifica _parse_inner contra los 4 packet types del btsnoop."""
    # Packet 590 TX: command 0xc0 0x0c, body has sn=0x76 + FileBrowseParam
    f590 = RcspFrame(flag=0xc0, cmd=0x0c,
                     payload=bytes.fromhex("76000a000100000002040000000000"))
    i590 = _parse_inner(f590)
    assert i590.is_command is True
    assert i590.has_response is True
    assert i590.op_code_sn == 0x76
    assert i590.xm_op_code is None
    assert i590.status is None
    assert i590.payload == bytes.fromhex("000a000100000002040000000000")

    # Packet 596 RX: response 0x00 0x0c body=`0076` (status=0, sn=0x76)
    f596 = RcspFrame(flag=0x00, cmd=0x0c, payload=bytes.fromhex("0076"))
    i596 = _parse_inner(f596)
    assert i596.is_command is False
    assert i596.status == 0x00
    assert i596.op_code_sn == 0x76
    assert i596.payload == b""

    # Packet 597 RX: command-no-response 0x80 0x01 con xm_op=0x0c
    f597 = RcspFrame(flag=0x80, cmd=0x01,
                     payload=bytes.fromhex("010c0a00000002000103424147"))
    i597 = _parse_inner(f597)
    assert i597.is_command is True
    assert i597.has_response is False
    assert i597.op_code_sn == 0x01
    assert i597.xm_op_code == 0x0c
    assert i597.payload == bytes.fromhex("0a00000002000103424147")

    # Packet 598 RX: command-with-response 0xc0 0x0d (device-initiated)
    f598 = RcspFrame(flag=0xc0, cmd=0x0d, payload=bytes.fromhex("0201"))
    i598 = _parse_inner(f598)
    assert i598.is_command is True
    assert i598.op_code_sn == 0x02
    assert i598.xm_op_code is None
    assert i598.payload == bytes.fromhex("01")
