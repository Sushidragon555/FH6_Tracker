import importlib.util
import json
import struct
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "car_lookup.py"
spec = importlib.util.spec_from_file_location("car_lookup", MODULE_PATH)
car_lookup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(car_lookup)


def make_packet(ordinal, rpm=5000.0, speed_mps=30.0):
    buffer = bytearray(car_lookup.MIN_PACKET_SIZE)
    struct.pack_into("f", buffer, car_lookup.RPM_OFFSET, rpm)
    struct.pack_into("f", buffer, car_lookup.SPEED_OFFSET, speed_mps)
    struct.pack_into("i", buffer, car_lookup.CAR_ORDINAL_OFFSET, ordinal)
    # Set a known throttle value (U8 at ACCEL_OFFSET) for input tests
    buffer[car_lookup.ACCEL_OFFSET] = 200  # ~78.4%
    buffer[car_lookup.BRAKE_OFFSET] = 0
    buffer[car_lookup.GEAR_OFFSET] = 3
    buffer[car_lookup.STEERING_OFFSET] = 0  # centre
    return bytes(buffer)


def test_car_ordinal_offset_is_212():
    assert car_lookup.CAR_ORDINAL_OFFSET == 212


def test_parse_packet_reads_car_ordinal_at_correct_offset():
    packet = make_packet(348, rpm=4200.0, speed_mps=40.0)
    parsed = car_lookup.parse_packet(packet)
    assert parsed["car_ordinal"] == 348
    assert round(parsed["rpm"]) == 4200
    assert round(parsed["speed_mph"]) == round(40.0 * car_lookup.MPS_TO_MPH)


def test_parse_packet_old_offset_192_would_be_wrong():
    # Regression: the previous code read the ordinal at byte 192 (tire-slip float),
    # which never matches the real ordinal written at byte 212.
    packet = make_packet(348)
    wrong = struct.unpack("i", packet[192:196])[0]
    assert wrong != 348


def test_parse_packet_rejects_short_packets():
    assert car_lookup.parse_packet(b"\x00" * 100) is None
    assert car_lookup.parse_packet(None) is None


def test_parse_packet_normalises_u8_inputs():
    """FH6 player inputs are U8/S8; parse_packet must normalise to 0-1 / -1..1."""
    buffer = bytearray(car_lookup.MIN_PACKET_SIZE)
    struct.pack_into("f", buffer, car_lookup.RPM_OFFSET, 3000.0)
    struct.pack_into("f", buffer, car_lookup.SPEED_OFFSET, 20.0)
    struct.pack_into("i", buffer, car_lookup.CAR_ORDINAL_OFFSET, 100)
    buffer[car_lookup.ACCEL_OFFSET] = 255   # full throttle
    buffer[car_lookup.BRAKE_OFFSET] = 128   # ~50% brake
    buffer[car_lookup.HANDBRAKE_OFFSET] = 0
    buffer[car_lookup.GEAR_OFFSET] = 4
    buffer[car_lookup.STEERING_OFFSET] = 64  # ~50% right
    parsed = car_lookup.parse_packet(bytes(buffer))
    assert parsed is not None
    assert abs(parsed["throttle"] - 1.0) < 0.01
    assert abs(parsed["brake"] - 128 / 255.0) < 0.01
    assert abs(parsed["steering"] - 64 / 127.0) < 0.01
    assert parsed["gear"] == 4


def test_parse_packet_steering_clamps():
    """Steering S8 value should clamp to -1..1 even if raw byte is -128."""
    buffer = bytearray(car_lookup.MIN_PACKET_SIZE)
    struct.pack_into("f", buffer, car_lookup.RPM_OFFSET, 3000.0)
    struct.pack_into("f", buffer, car_lookup.SPEED_OFFSET, 20.0)
    struct.pack_into("i", buffer, car_lookup.CAR_ORDINAL_OFFSET, 100)
    buffer[car_lookup.STEERING_OFFSET] = 255  # -1 as unsigned, = -1 as signed (S8)
    parsed = car_lookup.parse_packet(bytes(buffer))
    assert parsed is not None
    assert -1.0 <= parsed["steering"] <= 1.0


def test_is_real_ordinal():
    assert car_lookup.is_real_ordinal(348)
    assert car_lookup.is_real_ordinal("348")
    assert not car_lookup.is_real_ordinal(0)
    assert not car_lookup.is_real_ordinal(1003312267)  # garbage from the wrong offset
    assert not car_lookup.is_real_ordinal("not-a-number")


def test_load_reference_accepts_name_to_ordinal_direction(tmp_path, monkeypatch):
    ref = tmp_path / "ref.json"
    ref.write_text(json.dumps({"1969 Toyota 2000 GT": "247"}), encoding="utf-8")
    monkeypatch.setattr(car_lookup, "REF_FILE", str(ref))
    assert car_lookup.load_reference() == {"247": "1969 Toyota 2000 GT"}


def test_load_reference_accepts_ordinal_to_name_direction(tmp_path, monkeypatch):
    ref = tmp_path / "ref.json"
    ref.write_text(json.dumps({"247": "1969 Toyota 2000 GT"}), encoding="utf-8")
    monkeypatch.setattr(car_lookup, "REF_FILE", str(ref))
    assert car_lookup.load_reference() == {"247": "1969 Toyota 2000 GT"}


def test_resolve_canonical_name_matches_master_spelling():
    index = car_lookup.build_canonical_index({"2005 Ford GT": 300000})
    assert car_lookup.resolve_canonical_name("2005  ford  gt", index) == "2005 Ford GT"
    assert car_lookup.resolve_canonical_name("Unknown Car", index) == "Unknown Car"


def test_lookup_car_name_resolves_to_master(tmp_path, monkeypatch):
    ref = tmp_path / "ref.json"
    master = tmp_path / "master.json"
    ref.write_text(json.dumps({"348": "2005 ford gt"}), encoding="utf-8")
    master.write_text(json.dumps({"2005 Ford GT": 300000}), encoding="utf-8")
    monkeypatch.setattr(car_lookup, "REF_FILE", str(ref))
    monkeypatch.setattr(car_lookup, "MASTER_FILE", str(master))
    assert car_lookup.lookup_car_name(348) == "2005 Ford GT"
    assert car_lookup.lookup_car_name(999) is None


def test_add_owned_car_is_idempotent(tmp_path, monkeypatch):
    owned = tmp_path / "owned.json"
    owned.write_text(json.dumps({"owned": []}), encoding="utf-8")
    monkeypatch.setattr(car_lookup, "OWNED_FILE", str(owned))
    assert car_lookup.add_owned_car("2005 Ford GT") is True
    assert car_lookup.add_owned_car("2005 Ford GT") is False
    data = json.loads(owned.read_text(encoding="utf-8"))
    assert data["owned"] == ["2005 Ford GT"]


def test_save_mapping_writes_canonical_ordinal_form(tmp_path, monkeypatch):
    ref = tmp_path / "ref.json"
    master = tmp_path / "master.json"
    ref.write_text(json.dumps({}), encoding="utf-8")
    master.write_text(json.dumps({"2005 Ford GT": 300000}), encoding="utf-8")
    monkeypatch.setattr(car_lookup, "REF_FILE", str(ref))
    monkeypatch.setattr(car_lookup, "MASTER_FILE", str(master))
    stored = car_lookup.save_mapping(348, "2005 ford gt")
    assert stored == "2005 Ford GT"
    assert json.loads(ref.read_text(encoding="utf-8")) == {"348": "2005 Ford GT"}
