"""Diagnostic tool: dump the integer values around the expected CarOrdinal offset.

Run this while driving different cars in Forza. The column whose value stays constant
per car (and changes when you switch cars) is the real CarOrdinal offset. It should be
212 for Forza Horizon; if a future game version differs, update CAR_ORDINAL_OFFSET in
car_lookup.py to the offset you find here.
"""

import socket
import struct

from car_lookup import CAR_ORDINAL_OFFSET

UDP_IP = "0.0.0.0"
UDP_PORT = 9999
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))

offsets = [CAR_ORDINAL_OFFSET - 4, CAR_ORDINAL_OFFSET, CAR_ORDINAL_OFFSET + 4, CAR_ORDINAL_OFFSET + 8]

print("====================================================")
print(" SCANNING FOR CarOrdinal... Drive around in game!")
print(f" Expected offset: {CAR_ORDINAL_OFFSET}")
print("====================================================\n")
try:
    while True:
        data, addr = sock.recvfrom(1024)
        if len(data) >= offsets[-1] + 4:
            values = [struct.unpack("i", data[o:o + 4])[0] for o in offsets]
            cells = " | ".join(f"@{o}: {v}" for o, v in zip(offsets, values))
            print(f"{cells}          ", end="\r")
except KeyboardInterrupt:
    print("\nStopping scan...")
