"""Shared helpers for mapping Forza CarOrdinals to car names and tracking owned cars.

Both the telemetry listener (auto_log.py) and the GUI (fh6_gui.py) use this module so
that the reference map, the owned list, and the canonical car names stay consistent.
"""

import json
import os
import re
import struct

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REF_FILE = os.path.join(BASE_DIR, "fh6_id_reference.json")
MASTER_FILE = os.path.join(BASE_DIR, "fh6_master_list.json")
OWNED_FILE = os.path.join(BASE_DIR, "owned_cars.json")
SETTINGS_FILE = os.path.join(BASE_DIR, "gui_settings.json")

# Performance presets shared by the GUI and the tracker process. Higher intervals mean
# fewer screen grabs / disk writes / UI redraws per second, which frees up the CPU/GPU for
# the game (higher in-game FPS) at the cost of a slightly less frequently updated dashboard.
#   refresh_ms  - how often the GUI redraws its live data
#   ocr_seconds - how often the credit-balance OCR screen grab runs
#   log_seconds - how often the tracker writes a telemetry row to disk
PERFORMANCE_PRESETS = {
    "Quality": {"refresh_ms": 1000, "ocr_seconds": 3, "log_seconds": 1.0},
    "Balanced": {"refresh_ms": 2000, "ocr_seconds": 6, "log_seconds": 2.0},
    "Performance": {"refresh_ms": 4000, "ocr_seconds": 12, "log_seconds": 5.0},
}
DEFAULT_PERFORMANCE_MODE = "Balanced"

# Byte offset of the CarOrdinal field (a signed 32-bit int) inside a Forza Horizon
# "Data Out" UDP telemetry packet. It lives in the fixed "sled" section, so unlike the
# dashboard fields it is not affected by the +12 byte Horizon shift. If a future Forza
# version moves it, run scan_offsets.py in-game to find the new value.
CAR_ORDINAL_OFFSET = 212

# Byte offsets of the other telemetry fields we read. RPM lives in the fixed "sled"
# section; Speed lives in the dashboard section, which on Forza Horizon is shifted +12
# bytes versus Forza Motorsport (244 -> 256).
RPM_OFFSET = 16
SPEED_OFFSET = 256
MPS_TO_MPH = 2.23694

# A full Forza Horizon "Data Out" packet is 324 bytes; shorter packets are incomplete.
MIN_PACKET_SIZE = 324

# Plausible range for a real Forza CarOrdinal. Values outside this range mean we read the
# wrong bytes (e.g. reinterpreted float data) and should be ignored.
MIN_ORDINAL = 1
MAX_ORDINAL = 100000


def parse_packet(data):
    """Extract ``(rpm, speed_mph, car_ordinal)`` from a Forza telemetry packet.

    Returns ``None`` for packets that are too short to be a complete Horizon packet.
    """
    if data is None or len(data) < MIN_PACKET_SIZE:
        return None
    rpm = struct.unpack("f", data[RPM_OFFSET:RPM_OFFSET + 4])[0]
    speed_mps = struct.unpack("f", data[SPEED_OFFSET:SPEED_OFFSET + 4])[0]
    car_ordinal = struct.unpack("i", data[CAR_ORDINAL_OFFSET:CAR_ORDINAL_OFFSET + 4])[0]
    return {"rpm": rpm, "speed_mph": speed_mps * MPS_TO_MPH, "car_ordinal": car_ordinal}


def get_performance_preset(mode=None):
    """Return the performance preset dict for ``mode`` (or the saved/default mode)."""
    if mode is None:
        settings = load_json_file(SETTINGS_FILE, {})
        mode = settings.get("performance_mode", DEFAULT_PERFORMANCE_MODE)
    return PERFORMANCE_PRESETS.get(mode, PERFORMANCE_PRESETS[DEFAULT_PERFORMANCE_MODE])


def load_json_file(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            try:
                return json.load(handle)
            except json.JSONDecodeError:
                return default
    return default


def normalize_car_name(name):
    if not name:
        return ""
    lowered = str(name).strip().lower()
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    return " ".join(lowered.split())


def _looks_like_ordinal(value):
    return str(value).lstrip("-").isdigit()


def load_reference():
    """Return the reference map as ``{ordinal_str: car_name}``.

    The file historically shipped as ``{car_name: ordinal}`` while the code expected the
    opposite direction. This loader accepts either layout so both old and new files work.
    """
    raw = load_json_file(REF_FILE, {})
    mapping = {}
    for key, value in raw.items():
        key_str, value_str = str(key), str(value)
        key_is_ord = _looks_like_ordinal(key_str)
        value_is_ord = _looks_like_ordinal(value_str)
        if key_is_ord and not value_is_ord:
            mapping[key_str] = value_str
        elif value_is_ord and not key_is_ord:
            mapping[value_str] = key_str
        elif key_is_ord:
            # Both sides numeric (ambiguous); assume key is the ordinal.
            mapping[key_str] = value_str
    return mapping


def save_reference(mapping):
    """Write the reference map to disk in the canonical ``{ordinal_str: name}`` form."""
    with open(REF_FILE, "w", encoding="utf-8") as handle:
        json.dump(mapping, handle, indent=4, sort_keys=True)


def build_canonical_index(master=None):
    """Map normalized car names to the exact spelling used in the master list."""
    if master is None:
        master = load_json_file(MASTER_FILE, {})
    return {normalize_car_name(name): name for name in master}


def resolve_canonical_name(name, canonical_index=None):
    """Return the master-list spelling of ``name`` when it exists, else ``name``."""
    if canonical_index is None:
        canonical_index = build_canonical_index()
    return canonical_index.get(normalize_car_name(name), name)


def is_real_ordinal(ordinal):
    try:
        value = int(ordinal)
    except (TypeError, ValueError):
        return False
    return MIN_ORDINAL <= value <= MAX_ORDINAL


def lookup_car_name(ordinal, reference=None, canonical_index=None):
    """Return the canonical car name for ``ordinal``, or ``None`` if it is unmapped."""
    if reference is None:
        reference = load_reference()
    name = reference.get(str(ordinal))
    if not name:
        return None
    return resolve_canonical_name(name, canonical_index)


def save_mapping(ordinal, name):
    """Persist ``ordinal -> name`` in the reference file and return the stored name."""
    mapping = load_reference()
    canonical = resolve_canonical_name(name)
    mapping[str(ordinal)] = canonical
    save_reference(mapping)
    return canonical


def load_owned():
    data = load_json_file(OWNED_FILE, {"owned": []})
    if not isinstance(data, dict) or "owned" not in data:
        data = {"owned": []}
    return data


def add_owned_car(car_name):
    """Add ``car_name`` to the owned list. Return True only if it was newly added."""
    car_name = (car_name or "").strip()
    if not car_name:
        return False
    data = load_owned()
    if car_name in data["owned"]:
        return False
    data["owned"].append(car_name)
    with open(OWNED_FILE, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=4)
    return True
