import csv
import io
import json
import os
import socket
import struct
import time
from datetime import datetime, timezone

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
REF_FILE = os.path.join(BASE_DIR, "fh6_id_reference.json")
OWNED_FILE = os.path.join(BASE_DIR, "owned_cars.json")
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
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT))


def load_json_file(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            try:
                return json.load(handle)
            except json.JSONDecodeError:
                return default
    return default


# Load reference map safely
id_reference = load_json_file(REF_FILE, {})

# Load or create owned garage tracker
owned_garage = load_json_file(OWNED_FILE, {"owned": []})
if "owned" not in owned_garage:
    owned_garage["owned"] = []


def save_owned_car(car_name):
    if car_name not in owned_garage["owned"]:
        owned_garage["owned"].append(car_name)
        with open(OWNED_FILE, "w", encoding="utf-8") as handle:
            json.dump(owned_garage, handle, indent=4)
        return True
    return False


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
            print(f" [✅] Heard: {car_name}")
            current_mapped_car_name = car_name

            id_reference[active_car_id] = car_name
            with open(REF_FILE, "w", encoding="utf-8") as handle:
                json.dump(id_reference, handle, indent=4)
            print(f" [💾] Linked Raw ID {active_car_id} -> '{car_name}' in reference database.")

            if save_owned_car(car_name):
                print(f" [💾] Added '{car_name}' to owned garage list!")
            else:
                print(f" [ℹ️] '{car_name}' is already marked as owned.")

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
    try:
        while True:
            data, _ = sock.recvfrom(1024)

            if len(data) < 324:
                continue

            current_rpm = struct.unpack("f", data[16:20])[0]
            speed_mps = struct.unpack("f", data[256:260])[0]
            speed_mph = speed_mps * 2.23694

            car_ordinal = struct.unpack("i", data[192:196])[0]
            car_id_str = str(car_ordinal)

            if car_ordinal == 0:
                print(" Waiting for gameplay to start (In Menus/Loading)...       ", end="\r")
                continue

            active_car_id = car_id_str

            if last_id != car_id_str:
                last_id = car_id_str
                if not voice_override_active:
                    current_mapped_car_name = "Unknown Vehicle"

            if not voice_override_active:
                if car_id_str in id_reference:
                    current_mapped_car_name = id_reference[car_id_str]

                    if save_owned_car(current_mapped_car_name):
                        print(f"\n [✓] Automatically Added from ID Map: {current_mapped_car_name}")
                else:
                    current_mapped_car_name = "Unknown Vehicle"

            append_telemetry_row(int(current_rpm), int(speed_mph), car_id_str, current_mapped_car_name)
            print(f" [LIVE] RPM: {int(current_rpm):<5} | Speed: {int(speed_mph):<3} MPH | Raw ID: {car_id_str:<10} | Name: {current_mapped_car_name:<30}", end="\r")

    except KeyboardInterrupt:
        print("\n\nLogger stopped safely.")