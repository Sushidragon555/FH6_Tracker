"""Auto-detect the CarOrdinal byte offset for your version of Forza.

Forza's "Data Out" packet layout can shift between titles (Motorsport vs Horizon,
and between Horizon versions). If the tracker shows every car as "Unknown Vehicle"
or never detects a car, it's reading the CarOrdinal from the wrong byte offset.

The CarOrdinal is the one value that stays CONSTANT while you drive a single car but
is DIFFERENT for a different car, so we sample two cars and look for the byte offset
that matches that pattern.

IMPORTANT: Forza pauses/zeroes its telemetry when you tab away from the game, so you
must be DRIVING (game focused) while it captures. This tool uses a countdown instead
of asking you to press Enter, so you can click back into Forza and just drive.

HOW TO USE
  1. In Forza: Settings -> HUD and Gameplay -> Data Out = On, IP 127.0.0.1, Port 9999.
  2. Close the tracker/GUI first (only one program can use the port at a time).
  3. Run:  python find_car_offset.py
  4. When it counts down, click back into Forza and DRIVE (keep moving) until it says done.
  5. Do it again for a DIFFERENT car.
  6. It prints the number to set as CAR_ORDINAL_OFFSET in car_lookup.py.
"""

import math
import socket
import struct
import time

UDP_IP = "0.0.0.0"
UDP_PORT = 9999

# How many in-gameplay packets to sample per car, and how long to wait for them.
SAMPLES = 120
CAPTURE_TIMEOUT = 15.0
COUNTDOWN_SECONDS = 25

# Plausible range for a real CarOrdinal (0 = no car loaded). Kept wide because some
# Forza titles use large ordinals; physics floats are filtered out separately below.
MIN_ORDINAL = 1
MAX_ORDINAL = 50_000_000

# CurrentEngineRpm is a float at byte 16; > 0 means the engine is running (real gameplay
# frame) rather than a zeroed "paused / tabbed-out / in menu" frame.
RPM_OFFSET = 16


def flush(sock):
    sock.setblocking(False)
    try:
        while True:
            sock.recvfrom(2048)
    except (BlockingIOError, OSError):
        pass
    sock.setblocking(True)


def capture(sock, label):
    print(f"\n>>> {label}")
    for remaining in range(COUNTDOWN_SECONDS, 0, -1):
        print(f"    Click into Forza and START DRIVING — capturing in {remaining}s ...", end="\r")
        time.sleep(1)
    print("\n    Capturing now — keep driving!                          ")
    flush(sock)
    packets = []
    sock.settimeout(2.0)
    deadline = time.time() + CAPTURE_TIMEOUT
    while len(packets) < SAMPLES and time.time() < deadline:
        try:
            data, _ = sock.recvfrom(2048)
        except socket.timeout:
            continue
        if len(data) < 232:
            continue
        rpm = struct.unpack_from("<f", data, RPM_OFFSET)[0]
        if rpm > 0:  # skip zeroed/paused frames
            packets.append(data)
    print(f"    Done ({len(packets)} in-gameplay packets).                 ")
    return packets


def looks_like_int_field(raw4):
    """Return the int value if these 4 bytes are a genuine integer field (a plausible
    ordinal), else None. A real small integer, reinterpreted as a float, is subnormal
    (magnitude ~0), whereas true float fields (RPM, physics) have normal magnitudes."""
    value = struct.unpack("<i", raw4)[0]
    if not (MIN_ORDINAL <= value <= MAX_ORDINAL):
        return None
    as_float = struct.unpack("<f", raw4)[0]
    if math.isnan(as_float) or math.isinf(as_float) or abs(as_float) > 1e-10:
        return None
    return value


def constant_int_offsets(packets):
    """Offsets holding the same integer-field value across every captured packet."""
    length = min(len(p) for p in packets)
    result = {}
    # Forza's telemetry fields are 4-byte aligned from the start of the packet, so only
    # multiples of 4 are real fields; stepping by 4 avoids misaligned-read false positives.
    for off in range(0, length - 3, 4):
        values = {p[off:off + 4] for p in packets}
        if len(values) != 1:
            continue
        value = looks_like_int_field(next(iter(values)))
        if value is not None:
            result[off] = value
    return result


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT))
    print(f"Listening on 127.0.0.1:{UDP_PORT} — make sure Forza's Data Out is On.")

    first_packets = capture(sock, "STEP 1 of 2: your FIRST car")
    second_packets = capture(sock, "STEP 2 of 2: a DIFFERENT car")

    if not first_packets or not second_packets:
        print("\nNo gameplay packets captured. Make sure you're DRIVING (not paused/in a menu)")
        print("and that Data Out is On (127.0.0.1:9999), then run it again.")
        return

    first = constant_int_offsets(first_packets)
    second = constant_int_offsets(second_packets)

    # CarOrdinal is constant within each car but changes between the two cars.
    candidates = sorted(o for o in first if o in second and first[o] != second[o])

    print("\n==================== RESULT ====================")
    if not candidates:
        print("Could not identify it automatically. Copy these two lines to Devin:")
        print(" car1 constants:", first)
        print(" car2 constants:", second)
        return

    for off in candidates:
        print(f"  offset {off}:  car1 = {first[off]}   car2 = {second[off]}")

    # The CarOrdinal is usually the largest such value (CarClass is 0-7, PI is 100-999).
    best = max(candidates, key=lambda o: max(first[o], second[o]))
    print(f"\nMost likely CAR_ORDINAL_OFFSET = {best}")
    print("Open car_lookup.py, set CAR_ORDINAL_OFFSET to that number, then restart the tracker.")
    print("If cars still show as Unknown, try the other offsets listed above.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
