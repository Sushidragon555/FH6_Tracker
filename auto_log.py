import csv
import io
import os
import socket
import sys
import time
from datetime import datetime, timezone

import car_lookup

# Voice logging and recording libraries
try:
    import keyboard
    import numpy as np
    import sounddevice as sd
    import soundfile as sf
    import speech_recognition as sr
except Exception as exc:  # pragma: no cover - depends on local environment
    keyboard = None
    np = None
    sd = None
    sf = None
    sr = None
    OPTIONAL_IMPORT_ERROR = str(exc)
else:
    OPTIONAL_IMPORT_ERROR = None

# ==========================================
# CONFIGURATION
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UDP_IP = "0.0.0.0"
UDP_PORT = 9999
OWNED_FILE = car_lookup.OWNED_FILE
LOG_FILE = os.path.join(BASE_DIR, "telemetry_log.csv")

# Hotkey set to F4
HOTKEY = "f4"
TEST_MODE = os.environ.get("FH6_TEST_MODE", "0") == "1"

# Global state trackers
current_mapped_car_name = "Unknown Vehicle"
active_car_id = "0"
voice_override_active = False

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


def save_owned_car(car_name):
    return car_lookup.add_owned_car(car_name)


def append_telemetry_row(rpm, speed_mph, car_id, car_name):
    file_exists = os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if not file_exists or os.path.getsize(LOG_FILE) == 0:
            writer.writerow(["timestamp", "rpm", "speed_mph", "car_id", "car_name"])
        writer.writerow([datetime.now(timezone.utc).isoformat(), rpm, speed_mph, car_id, car_name])


# ==========================================
# HARDWARE-LEVEL RAW VOICE LOGGING FUNCTION
# ==========================================
def log_car_voice():
    """Records voice and links the currently detected car ID to a human-readable name."""
    global current_mapped_car_name, active_car_id, voice_override_active

    if sr is None or sd is None or sf is None or np is None:
        print(" [⚠️] Voice logging is unavailable because the audio dependencies are not installed.")
        return

    # Block the telemetry loop from overwriting our text screen state
    voice_override_active = True

    print(f"\n[🎙️] Logging current Raw ID ({active_car_id})... Speak car name now!")

    sample_rate = 16000
    duration = 4.0  # 4 second recording window

    try:
        audio_chunks = []

        def callback(indata, frames, time, status):
            audio_chunks.append(bytes(indata))

        with sd.RawInputStream(samplerate=sample_rate, channels=1, dtype="int16", callback=callback):
            time.sleep(duration)

        raw_bytes = b"".join(audio_chunks)
        recording = np.frombuffer(raw_bytes, dtype=np.int16)

        byte_io = io.BytesIO()
        sf.write(byte_io, recording, sample_rate, format="WAV", subtype="PCM_16")
        byte_io.seek(0)

        recognizer = sr.Recognizer()
        with sr.AudioFile(byte_io) as source:
            audio = recognizer.record(source)

        car_name = recognizer.recognize_google(audio).strip()

        if car_name:
            canonical = car_lookup.save_mapping(active_car_id, car_name)
            current_mapped_car_name = canonical
            id_reference[active_car_id] = canonical
            print(f" [✅] Heard: {car_name}")
            print(f" [💾] Linked Raw ID {active_car_id} -> '{canonical}' in reference database.")

            if save_owned_car(canonical):
                print(f" [💾] Added '{canonical}' to owned garage list!")
            else:
                print(f" [ℹ️] '{canonical}' is already marked as owned.")

    except sr.UnknownValueError:
        print(" [❌] Voice capture failure: speech wasn't clearly understood.")
    except Exception as exc:
        print(f" [⚠️] Voice service warning: {exc}")

    voice_override_active = False
    print("\n Resuming live telemetry display tracking...\n")


# Register the shortcut hook safely before running the loop
if keyboard is not None:
    keyboard.add_hotkey(HOTKEY, log_car_voice)
else:
    print(" [⚠️] Hotkey support disabled because the keyboard package is unavailable.")

print("==========================================================")
print(" VISUAL TELEMETRY LOGGER & VOICE GARAGE TRACKER RUNNING")
print(" Open Forza and drive around to verify connection!")
print(f" Press '{HOTKEY}' in-game to manually name an unknown car.")
print("==========================================================\n")

if OPTIONAL_IMPORT_ERROR:
    print(f" Optional dependency warning: {OPTIONAL_IMPORT_ERROR}")

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
        print(" [⚠️] Telemetry logging skipped — socket could not be opened.")
        sys.exit(0)
    try:
        while True:
            data, _ = sock.recvfrom(1024)

            parsed = car_lookup.parse_packet(data)
            if parsed is None:
                continue

            current_rpm = parsed["rpm"]
            speed_mph = parsed["speed_mph"]
            car_ordinal = parsed["car_ordinal"]
            car_id_str = str(car_ordinal)

            if not car_lookup.is_real_ordinal(car_ordinal):
                print(" Waiting for gameplay to start (In Menus/Loading)...       ", end="\r")
                continue

            active_car_id = car_id_str

            car_changed = last_id != car_id_str
            if car_changed:
                last_id = car_id_str
                if not voice_override_active:
                    current_mapped_car_name = "Unknown Vehicle"

            if not voice_override_active:
                mapped_name = id_reference.get(car_id_str)
                if mapped_name:
                    current_mapped_car_name = car_lookup.resolve_canonical_name(mapped_name, canonical_index)

                    # The owned list only changes when a new car appears, so touch it on
                    # car changes instead of on every packet (avoids re-reading the JSON 60x/sec).
                    if car_changed and save_owned_car(current_mapped_car_name):
                        print(f"\n [✓] Automatically Added from ID Map: {current_mapped_car_name}")
                else:
                    current_mapped_car_name = "Unknown Vehicle"

            now = time.monotonic()
            if car_changed or (now - last_log_time) >= LOG_INTERVAL_SECONDS:
                last_log_time = now
                append_telemetry_row(int(current_rpm), int(speed_mph), car_id_str, current_mapped_car_name)
            print(f" [LIVE] RPM: {int(current_rpm):<5} | Speed: {int(speed_mph):<3} MPH | Raw ID: {car_id_str:<10} | Name: {current_mapped_car_name:<30}", end="\r")

    except KeyboardInterrupt:
        print("\n\nLogger stopped safely.")