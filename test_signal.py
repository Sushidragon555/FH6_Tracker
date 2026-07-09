import socket
import struct
import json
import os

# 0.0.0.0 tells Python to listen on BOTH 127.0.0.1 and your real network IP address
UDP_IP = "127.0.0.1"
UDP_PORT = 9999

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))

print("==========================================================")
print(" LIVE FORZA LOGGER ACTIVE: Listening on all network paths...")
print("==========================================================\n")

with open("fh6_master_list.json", "r", encoding="utf-8") as f:
    master_db = list(json.load(f).keys())

try:
    while True:
        data, addr = sock.recvfrom(1024)
        
        if len(data) < 176:
            continue
            
        car_ordinal = struct.unpack('i', data[172:176])[0]
        
        if car_ordinal > 0:
            if car_ordinal < len(master_db):
                detected_car = master_db[car_ordinal]
                
                with open("owned_cars.json", "r+", encoding="utf-8") as f:
                    owned_data = json.load(f)
                    if detected_car not in owned_data["owned"]:
                        owned_data["owned"].append(detected_car)
                        f.seek(0)
                        json.dump(owned_data, f, indent=4)
                        f.truncate()
                        print(f" [✓] Automatically Added: {detected_car}")
except KeyboardInterrupt:
    print("\nStopping logger...")