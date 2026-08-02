import csv
import ctypes
import ctypes.wintypes
import os
import socket
import struct
import sys
import threading
import time
from datetime import datetime, timezone

import car_lookup

# The tracker logs unicode status glyphs (✓ ⚠️ 🏁 …). When the GUI launches it,
# stdout is redirected to a file and Windows defaults the stream to cp1252,
# which can't encode those chars — a single print would crash the hotkey thread.
# Force UTF-8 (with lossy fallback) so logging never takes the process down.
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None:
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
del _stream

# ==========================================
# GLOBAL HOTKEYS via Win32 RegisterHotKey
# Works without admin privileges, unlike the keyboard library.
# ==========================================

WM_HOTKEY = 0x0312
HOTKEY_ID_RECORD = 2

# Virtual key codes
VK_F6 = 0x75

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

# On 64-bit Windows, WPARAM/LPARAM/LRESULT are pointer-sized (64-bit).
# ctypes.wintypes defines them as 32-bit, so the window proc would overflow
# when forwarding 64-bit lParam values to DefWindowProcW (and during
# CreateWindowExW), crashing the hotkey thread at startup on x64.
_PTR_SIZE = ctypes.sizeof(ctypes.c_void_p)
WPARAM = ctypes.c_uint64 if _PTR_SIZE == 8 else ctypes.c_uint32
LPARAM = ctypes.c_int64 if _PTR_SIZE == 8 else ctypes.c_int32
LRESULT = ctypes.c_int64 if _PTR_SIZE == 8 else ctypes.c_int32

WNDPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.wintypes.HWND, ctypes.c_uint, WPARAM, LPARAM)


class _WNDCLASS(ctypes.Structure):
    """Win32 WNDCLASS structure (removed from ctypes.wintypes in Python 3.14)."""
    _fields_ = [
        ("style", ctypes.c_uint),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", ctypes.wintypes.HINSTANCE),
        ("hIcon", ctypes.wintypes.HANDLE),
        ("hCursor", ctypes.wintypes.HANDLE),
        ("hbrBackground", ctypes.wintypes.HANDLE),
        ("lpszMenuName", ctypes.wintypes.LPCWSTR),
        ("lpszClassName", ctypes.wintypes.LPCWSTR),
        ("hIconSm", ctypes.wintypes.HANDLE),
    ]


_user32.DefWindowProcW.restype = LRESULT
_user32.DefWindowProcW.argtypes = [ctypes.wintypes.HWND, ctypes.c_uint, WPARAM, LPARAM]

_user32.RegisterClassW.restype = ctypes.c_ushort
_user32.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASS)]

_user32.CreateWindowExW.restype = ctypes.wintypes.HWND
_user32.CreateWindowExW.argtypes = [
    ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.wintypes.HWND, ctypes.wintypes.HMENU, ctypes.wintypes.HINSTANCE,
    ctypes.c_void_p,
]

_user32.RegisterHotKey.restype = ctypes.c_int
_user32.RegisterHotKey.argtypes = [ctypes.wintypes.HWND, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]

_user32.GetMessageW.restype = ctypes.c_int
_user32.GetMessageW.argtypes = [ctypes.POINTER(ctypes.wintypes.MSG), ctypes.wintypes.HWND, ctypes.c_uint, ctypes.c_uint]


def _wndproc(hwnd, msg, wparam, lparam):
    if msg == WM_HOTKEY:
        if wparam == HOTKEY_ID_RECORD:
            threading.Thread(target=toggle_race_recording, daemon=True).start()
        return 0
    return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

# Keep a reference to the callback so it doesn't get garbage collected
_pwndproc = WNDPROC(_wndproc)
_hotkey_hwnd = None

def _start_hotkey_listener():
    """Create a hidden message-only window and register F4/F6 hotkeys."""
    global _hotkey_hwnd
    className = "FH6TrackerHotkeys"
    wc = _WNDCLASS()
    wc.lpfnWndProc = _pwndproc
    wc.lpszClassName = className
    wc.hInstance = _kernel32.GetModuleHandleW(None)
    atom = _user32.RegisterClassW(ctypes.byref(wc))
    if not atom:
        print(" [⚠️] Could not register hotkey window class.")
        return

    _hotkey_hwnd = _user32.CreateWindowExW(
        0, className, "FH6Hotkeys", 0, 0, 0, 0, 0,
        None, None, wc.hInstance, None
    )
    if not _hotkey_hwnd:
        print(" [⚠️] Could not create hotkey window.")
        return

    # Register hotkey: 0 = no modifier
    if not _user32.RegisterHotKey(_hotkey_hwnd, HOTKEY_ID_RECORD, 0, VK_F6):
        _err = _kernel32.GetLastError()
        print(f" [⚠️] Could not register F6 hotkey (Win32 error {_err}).")
    else:
        print(" [✓] F6 hotkey registered — works in-game without admin.")

    # Message loop
    msg = ctypes.wintypes.MSG()
    while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        _user32.TranslateMessage(ctypes.byref(msg))
        _user32.DispatchMessageW(ctypes.byref(msg))

# ==========================================
# CONFIGURATION
# ==========================================
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UDP_IP = "0.0.0.0"
UDP_PORT = 9999
OWNED_FILE = car_lookup.OWNED_FILE
LOG_FILE = os.path.join(BASE_DIR, "telemetry_log.csv")
RACES_DIR = os.path.join(BASE_DIR, "races")
LIVE_RACE_FILE = os.path.join(RACES_DIR, ".live_race.json")
LIVE_CHART_FILE = os.path.join(RACES_DIR, ".live_chart.json")
TEST_MODE = os.environ.get("FH6_TEST_MODE", "0") == "1"

# Race telemetry capture settings. Forza sends ~60 packets/sec. We sample every
# RACE_SAMPLE_EVERY-th packet to get ~20Hz capture, keeping file sizes manageable
# (~300KB per 3-minute race) while capturing enough detail for useful analysis.
RACE_SAMPLE_EVERY = 3
RACE_MIN_DURATION = 5.0

# Global state trackers
current_mapped_car_name = "Unknown Vehicle"
active_car_id = "0"

# ==========================================
# FILE INITIALIZATION
# ==========================================
sock = None
if not TEST_MODE:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((UDP_IP, UDP_PORT))
    except OSError as exc:
        print(f" [⚠️] Could not bind to UDP port {UDP_PORT}: {exc}")
        print(" [⚠️] Telemetry logging unavailable (another instance may be running).")
        sock = None


# Load reference map (ordinal -> canonical name) and the master-name index once.
id_reference = car_lookup.load_reference()
canonical_index = car_lookup.build_canonical_index()

# Race detection state
race_in_progress = False
race_buffer = []
# Rolling telemetry window the GUI overlay polls while driving (not just while
# recording), so the overlay shows live data at all times.
live_recent = []
live_sample_tick = 0
session_start_mono = time.monotonic()
race_start_time_mono = 0.0
race_start_timestamp = ""
race_car_name = "Unknown Vehicle"
race_car_id = 0
race_packet_count = 0
race_manual_override = False  # True when user manually triggered recording
last_live_write_time = 0.0    # throttle for .live_race.json writes
last_live_chart_write_time = 0.0  # throttle for .live_chart.json writes
last_signal_check = 0.0       # throttle for .record_start/.record_stop polling
last_packet_mono = 0.0        # monotonic time of the last successfully parsed packet
last_notice_write = 0.0       # throttle for .tracker_notice warnings

# Serialises all recording state transitions. F6 presses and GUI signal files can
# arrive from different threads; without this, two near-simultaneous events can
# double-start (wiping the buffer) or double-stop, producing "race too short"
# discards for races that were actually recorded in full.
race_lock = threading.RLock()

# Signal file path for GUI <-> subprocess communication
RECORD_START_FILE = os.path.join(RACES_DIR, ".record_start")
RECORD_STOP_FILE = os.path.join(RACES_DIR, ".record_stop")

# Ensure races directory exists
os.makedirs(RACES_DIR, exist_ok=True)


def save_owned_car(car_name):
    return car_lookup.add_owned_car(car_name)


def append_telemetry_row(rpm, speed_mph, car_id, car_name):
    file_exists = os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if not file_exists or os.path.getsize(LOG_FILE) == 0:
            writer.writerow(["timestamp", "rpm", "speed_mph", "car_id", "car_name"])
        writer.writerow([datetime.now(timezone.utc).isoformat(), rpm, speed_mph, car_id, car_name])


def save_race(buffer, car_name, car_id, start_time, end_time, duration):
    """Write the race telemetry buffer to a JSON file in the races directory."""
    import json as _json
    ts = start_time.replace(":", "-").replace("T", "_")[:19]
    filename = f"race_{ts}.json"
    filepath = os.path.join(RACES_DIR, filename)
    race_data = {
        "car_name": car_name,
        "car_id": car_id,
        "start_time": start_time,
        "end_time": end_time,
        "duration_seconds": round(duration, 1),
        "samples": buffer,
    }
    with open(filepath, "w", encoding="utf-8") as handle:
        _json.dump(race_data, handle)
    print(f"\n [🏁] Race saved: {filename} ({len(buffer)} samples, {duration:.1f}s)")


def _write_live_race():
    """Write a compact live snapshot the GUI overlay polls continuously.

    Unlike before, this runs while driving as well as while recording, so the
    overlay stays live between races. Throttled by the caller to ~2x/sec.
    """
    if not live_recent:
        return
    try:
        import json as _json
        duration = (time.monotonic() - race_start_time_mono) if race_in_progress else 0.0
        data = {
            "recording": bool(race_in_progress),
            "car_name": (race_car_name if race_in_progress else current_mapped_car_name)
                        or "Unknown Vehicle",
            "elapsed": round(duration, 1),
            "sample": live_recent[-1],
            "recent": live_recent[-90:],
        }
        with open(LIVE_RACE_FILE, "w", encoding="utf-8") as fh:
            _json.dump(data, fh)
    except (OSError, TypeError, ValueError):
        pass


def _decimate(samples, max_points=2400):
    """Evenly reduce a sample list to at most ``max_points`` for live charts."""
    if len(samples) <= max_points:
        return samples
    step = len(samples) / max_points
    return [samples[int(i * step)] for i in range(max_points)]


def _write_live_chart():
    """Write the full race-so-far sample set (decimated) so the GUI can render
    live charts in the Race Analysis tab while the race is still being recorded.
    Throttled by the caller to ~1x/sec; separate from .live_race.json so the
    tiny overlay snapshot stays cheap to poll."""
    if not race_in_progress:
        return
    try:
        import json as _json
        data = {
            "recording": True,
            "car_name": race_car_name or "Unknown Vehicle",
            "start_time": race_start_timestamp,
            "duration_seconds": round(time.monotonic() - race_start_time_mono, 1),
            "samples": _decimate(race_buffer, 2400),
        }
        with open(LIVE_CHART_FILE, "w", encoding="utf-8") as fh:
            _json.dump(data, fh)
    except (OSError, TypeError, ValueError):
        pass


def _clear_live_race():
    """Remove the live snapshots so the GUI overlay hides."""
    try:
        if os.path.exists(LIVE_RACE_FILE):
            os.remove(LIVE_RACE_FILE)
        if os.path.exists(LIVE_CHART_FILE):
            os.remove(LIVE_CHART_FILE)
    except OSError:
        pass


def start_race(parsed, now_mono, timestamp_str):
    """Begin recording a new race. Caller must hold ``race_lock``."""
    global race_in_progress, race_buffer, race_start_time_mono, race_start_timestamp
    global race_car_name, race_car_id, race_packet_count
    if race_in_progress:
        return
    race_in_progress = True
    race_buffer = []
    race_start_time_mono = now_mono
    race_start_timestamp = timestamp_str
    race_car_id = parsed["car_ordinal"]
    car_id_str = str(race_car_id)
    race_car_name = id_reference.get(car_id_str, "Unknown Vehicle")
    race_car_name = car_lookup.resolve_canonical_name(race_car_name, canonical_index)
    race_packet_count = 0
    _clear_live_race()
    _write_tracker_status(True)
    print(f"\n [🏁] Race started! Recording telemetry for {race_car_name}...")


def _write_tracker_status(recording):
    """Write tracker recording status so the GUI can sync."""
    try:
        status_path = os.path.join(RACES_DIR, ".tracker_status")
        with open(status_path, "w", encoding="utf-8") as fh:
            fh.write("1" if recording else "0")
    except OSError:
        pass


def _write_race_event(event_type):
    """Write a race event file so the GUI knows a race was saved/discarded."""
    try:
        event_path = os.path.join(RACES_DIR, ".race_event")
        with open(event_path, "w", encoding="utf-8") as fh:
            fh.write(event_type)
    except OSError:
        pass


def _write_tracker_notice(message):
    """Write a one-shot notice the GUI surfaces to the user (e.g. no telemetry)."""
    global last_notice_write
    now = time.monotonic()
    if now - last_notice_write < 10.0:
        return
    last_notice_write = now
    try:
        notice_path = os.path.join(RACES_DIR, ".tracker_notice")
        with open(notice_path, "w", encoding="utf-8") as fh:
            fh.write(message)
    except OSError:
        pass


def end_race(now_mono, timestamp_str):
    """Finish the current race and save the telemetry data.

    Caller must hold ``race_lock``. Safe to call when nothing is recording —
    a late/second stop signal is ignored instead of starting a new race.
    """
    global race_in_progress, race_buffer, race_car_name, race_car_id, race_packet_count
    if not race_in_progress:
        return
    race_in_progress = False
    race_packet_count = 0
    _write_tracker_status(False)
    _clear_live_race()
    duration = now_mono - race_start_time_mono
    if duration < RACE_MIN_DURATION or len(race_buffer) < 10:
        if len(race_buffer) == 0:
            reason = "no_data"
        elif len(race_buffer) < 10:
            reason = "too_few_samples"
        else:
            reason = "too_short"
        print(f"\n [🏁] Race ended ({duration:.1f}s, {len(race_buffer)} samples) — discarded ({reason}).")
        _write_race_event(f"discarded:{len(race_buffer)}:{duration:.1f}:{reason}")
        race_buffer = []
        return
    save_race(race_buffer, race_car_name, race_car_id, race_start_timestamp, timestamp_str, duration)
    _write_race_event("saved")
    race_buffer = []


def toggle_race_recording():
    """Toggle manual race recording on/off via F6 hotkey."""
    global race_in_progress, race_manual_override
    with race_lock:
        now = time.monotonic()
        now_str = datetime.now(timezone.utc).isoformat()
        if not race_in_progress:
            race_manual_override = True
            start_race({"car_ordinal": int(active_car_id) if active_car_id.isdigit() else 0,
                         "rpm": 0, "speed_mph": 0,
                         "timestamp_ms": int(time.time() * 1000),
                         "engine_max_rpm": 0,
                         "throttle": 0, "brake": 0, "steering": 0,
                         "handbrake": 0, "gear": 0, "power": 0, "torque": 0, "boost": 0,
                         "is_race_on": 1}, now, now_str)
            print(f"\n [REC] Manual recording STARTED — press F6 or Stop Recording to finish.")
        else:
            race_manual_override = False
            end_race(now, now_str)
            print(f"\n [REC] Manual recording STOPPED.")


def _check_signal_files():
    """Check for GUI signal files to start/stop recording."""
    global race_manual_override
    if os.path.exists(RECORD_START_FILE):
        try:
            os.remove(RECORD_START_FILE)
        except OSError:
            pass
        with race_lock:
            if not race_in_progress:
                now = time.monotonic()
                now_str = datetime.now(timezone.utc).isoformat()
                race_manual_override = True
                start_race({"car_ordinal": int(active_car_id) if active_car_id.isdigit() else 0,
                             "rpm": 0, "speed_mph": 0,
                             "timestamp_ms": int(time.time() * 1000),
                             "engine_max_rpm": 0,
                             "throttle": 0, "brake": 0, "steering": 0,
                             "handbrake": 0, "gear": 0, "power": 0, "torque": 0, "boost": 0,
                             "is_race_on": 1}, now, now_str)
    if os.path.exists(RECORD_STOP_FILE):
        try:
            os.remove(RECORD_STOP_FILE)
        except OSError:
            pass
        with race_lock:
            if race_in_progress and race_manual_override:
                now = time.monotonic()
                now_str = datetime.now(timezone.utc).isoformat()
                race_manual_override = False
                end_race(now, now_str)

    # Warn the user while a manual race is active but no telemetry is arriving.
    # A silent "race too short to analyze" at the end tells them nothing; this
    # surfaces the real problem (Data Out off, wrong port, dead socket) mid-race.
    if race_in_progress and race_manual_override and last_packet_mono:
        idle_for = time.monotonic() - last_packet_mono
        if idle_for > 8.0:
            _write_tracker_notice(
                f"Recording, but no Forza data received for {int(idle_for)}s — "
                f"check that Data Out is enabled on UDP port {UDP_PORT} and "
                f"that no other tracker instance is running."
            )


# Start global hotkey listener (F6 for recording) in a background thread
_hotkey_thread = threading.Thread(target=_start_hotkey_listener, daemon=True)
_hotkey_thread.start()

print("==========================================================")
print(" VISUAL TELEMETRY LOGGER & RACE RECORDER RUNNING")
print(" Open Forza and drive around to verify connection!")
print(" Press F6 in-game or click Start Recording in the GUI")
print(" to record a race (auto-detection removed for FH6).")
print("==========================================================\n")
if sys.stdout is not None:
    try:
        sys.stdout.flush()
    except OSError:
        pass

last_id = None
last_log_time = 0.0
# Forza streams ~60 packets/sec. Writing every one thrashes the disk and grows
# telemetry_log.csv without bound, which is the main cause of in-game stutter while
# the tracker runs. We log at most once per interval (plus immediately on a car change),
# which keeps the GUI's live view fresh while cutting disk writes drastically. The
# interval follows the Performance setting chosen in the GUI (read once at startup).
LOG_INTERVAL_SECONDS = car_lookup.get_performance_preset()["log_seconds"]

# ==========================================
# MAIN TELEMETRY LOOP
# ==========================================
if TEST_MODE:
    print(" [TEST] Running owned-car update test mode. Saving a sample car name.")
    sample_car = "Test Car"
    if save_owned_car(sample_car):
        print(f" [TEST] Added '{sample_car}' to the owned cars file.")
    else:
        print(f" [TEST] '{sample_car}' is already present in the owned cars file.")
    print(" [TEST] Done. Check owned_cars.json for the new entry.")
else:
    if sock is None:
        for _bind_attempt in range(3):
            print(f" [⚠️] Could not bind UDP port {UDP_PORT}, retrying in 2s... ({_bind_attempt + 1}/3)")
            time.sleep(2)
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.bind((UDP_IP, UDP_PORT))
                print(f" [✓] Bound to UDP port {UDP_PORT} on retry.")
                break
            except OSError:
                sock = None
        if sock is None:
            print(" [⚠️] Telemetry logging skipped — socket could not be opened after retries.")
            _write_tracker_notice(
                f"Could not bind UDP port {UDP_PORT} — another tracker instance may be running. "
                f"Close the old one and restart."
            )
            sys.exit(0)
    try:
        sock.settimeout(1.0)
        while True:
            try:
                try:
                    data, _ = sock.recvfrom(1024)
                except socket.timeout:
                    _check_signal_files()
                    print(" Waiting for Forza telemetry...                     ", end="\r")
                    continue

                parsed = car_lookup.parse_packet(data)
                if parsed is None:
                    continue

                current_rpm = parsed["rpm"]
                speed_mph = parsed["speed_mph"]
                car_ordinal = parsed["car_ordinal"]
                car_id_str = str(car_ordinal)
                is_race_on = parsed["is_race_on"]

                now = time.monotonic()
                now_str = datetime.now(timezone.utc).isoformat()
                last_packet_mono = now

                # --- Check for GUI signal files (start/stop recording) ---
                # Time-based (not packet-count based): start/stop must be picked up
                # promptly even during menus/results screens, where the car ordinal
                # is invalid and the capture loop (which advances race_packet_count)
                # is paused.
                if now - last_signal_check >= 0.3:
                    last_signal_check = now
                    _check_signal_files()

                # --- Auto race detection DISABLED ---
                # In FH6, is_race_on is 1 whenever the player is actively
                # driving (including free roam), so it cannot distinguish
                # actual race events from normal gameplay.  Recording is now
                # manual-only: press F6 in-game or click Start Recording in
                # the GUI.

                # --- Race telemetry capture ---
                # Runs BEFORE the ordinal gate on purpose: a manually started race
                # must record telemetry even if this car's ordinal is 0/unknown,
                # otherwise such users would get "race too short" every time.
                sample = {
                    "t": round((now - race_start_time_mono) if race_in_progress
                               else (now - session_start_mono), 3),
                    "spd": round(speed_mph, 1),
                    "rpm": int(current_rpm),
                    "thr": round(parsed["throttle"], 3),
                    "brk": round(parsed["brake"], 3),
                    "str": round(parsed["steering"], 3),
                    "gear": parsed["gear"],
                    "pwr": int(parsed["power"]),
                    "trq": int(parsed["torque"]),
                    "hbrk": round(parsed["handbrake"], 3),
                }
                if race_in_progress:
                    race_packet_count += 1
                    if race_packet_count % RACE_SAMPLE_EVERY == 0:
                        race_buffer.append(sample)
                        live_recent.append(sample)
                        if len(live_recent) > 90:
                            del live_recent[:-90]
                        # Feed the GUI overlay a compact live snapshot (~2x/sec).
                        if now - last_live_write_time >= 0.5:
                            last_live_write_time = now
                            _write_live_race()
                        # Feed the GUI's live Race Analysis charts (~1x/sec).
                        if now - last_live_chart_write_time >= 1.0:
                            last_live_chart_write_time = now
                            _write_live_chart()

                # --- Ordinal gate: skip menus/results screens ---
                # Only gates car-name resolution and regular logging; manual-race
                # capture above deliberately runs regardless of ordinal validity.
                if not car_lookup.is_real_ordinal(car_ordinal):
                    print(" Waiting for gameplay to start (In Menus/Loading)...       ", end="\r")
                    continue

                active_car_id = car_id_str

                car_changed = last_id != car_id_str
                if car_changed:
                    last_id = car_id_str
                    current_mapped_car_name = "Unknown Vehicle"

                mapped_name = id_reference.get(car_id_str)
                if mapped_name:
                    current_mapped_car_name = car_lookup.resolve_canonical_name(mapped_name, canonical_index)

                    # The owned list only changes when a new car appears, so touch it on
                    # car changes instead of on every packet (avoids re-reading the JSON 60x/sec).
                    if car_changed and save_owned_car(current_mapped_car_name):
                        print(f"\n [✓] Automatically Added from ID Map: {current_mapped_car_name}")
                else:
                    current_mapped_car_name = "Unknown Vehicle"

                # --- Live overlay feed (always, while driving, ~20Hz sampling) ---
                if not race_in_progress:
                    if live_sample_tick % RACE_SAMPLE_EVERY == 0:
                        live_recent.append(sample)
                        if len(live_recent) > 90:
                            del live_recent[:-90]
                    live_sample_tick += 1
                    if now - last_live_write_time >= 0.5:
                        last_live_write_time = now
                        _write_live_race()

                # --- Regular telemetry logging ---
                if car_changed or (now - last_log_time) >= LOG_INTERVAL_SECONDS:
                    last_log_time = now
                    append_telemetry_row(int(current_rpm), int(speed_mph), car_id_str, current_mapped_car_name)
                race_flag = " [RACE]" if race_in_progress else ""
                print(f" [LIVE] RPM: {int(current_rpm):<5} | Speed: {int(speed_mph):<3} MPH | Raw ID: {car_id_str:<10} | Name: {current_mapped_car_name:<30}{race_flag}", end="\r")

            except Exception as exc:
                try:
                    print(f" [!] Telemetry loop error: {exc}")
                except Exception:
                    pass

    except KeyboardInterrupt:
        print("\n\nLogger stopped safely.")