"""Auto-detect the CarOrdinal byte offset for your version of Forza.

Forza's "Data Out" packet layout can shift between titles (Motorsport vs Horizon,
and between Horizon versions). If the tracker shows every car as "Unknown Vehicle"
or never detects a car, it's reading the CarOrdinal from the wrong byte offset.

This tool finds the right offset automatically. The CarOrdinal is the one value that
stays CONSTANT while you drive a single car but is DIFFERENT for a different car, so
we capture two cars and look for the byte offset that matches that pattern.

HOW TO USE
  1. In Forza: Settings -> HUD and Gameplay -> Data Out = On, IP 127.0.0.1, Port 9999.
  2. Close the tracker/GUI first (only one program can use the port at a time).
  3. Run:  python find_car_offset.py
  4. Follow the prompts: drive one car, then switch to a different car.
  5. It prints the number to set as CAR_ORDINAL_OFFSET in car_lookup.py.
"""

import socket
import struct

UDP_IP = "0.0.0.0"
UDP_PORT = 9999

# Number of packets to sample per car (~2-3 seconds at 60 Hz).
SAMPLES = 150

# Plausible range for a real CarOrdinal. Physics fields read as ints fall far outside
# this (huge or negative), so this filters out almost all of the noise immediately.
MIN_ORDINAL = 1
MAX_ORDINAL = 50000


def capture(sock, label):
    input(f"\n>>> {label}\n    Get in the car and drive a little, then press Enter to capture...")
    print("    Capturing — keep driving...")
    packets = []
    while len(packets) < SAMPLES:
        data, _ = sock.recvfrom(2048)
        if len(data) >= 232:
            packets.append(data)
    print(f"    Done ({len(packets)} packets).")
    return packets


def constant_int_offsets(packets):
    """Offsets whose little-endian int32 value is identical across all packets and in range."""
    length = min(len(p) for p in packets)
    result = {}
    for off in range(0, length - 3):
        values = {struct.unpack_from("<i", p, off)[0] for p in packets}
        if len(values) == 1:
            value = next(iter(values))
            if MIN_ORDINAL <= value <= MAX_ORDINAL:
                result[off] = value
    return result


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT))
    print(f"Listening on 127.0.0.1:{UDP_PORT} — make sure Forza's Data Out is On.")

    first = constant_int_offsets(capture(sock, "STEP 1 of 2: your FIRST car"))
    second = constant_int_offsets(capture(sock, "STEP 2 of 2: a DIFFERENT car"))

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
