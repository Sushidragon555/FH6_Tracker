import socket
import struct
UDP_IP = "0.0.0.0"
UDP_PORT = 9999
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))
print("====================================================")
print(" SCANNING SECTOR TWO... Drive around in game!")
print("====================================================\n")
try:
    while True:
        data, addr = sock.recvfrom(1024)
        if len(data) >= 324:
            p180 = struct.unpack('i', data[180:184])[0]
            p184 = struct.unpack('i', data[184:188])[0]
            p188 = struct.unpack('i', data[188:192])[0]
            p192 = struct.unpack('i', data[192:196])[0]
            print(f"At 180: {p180} | At 184: {p184} | At 188: {p188} | At 192: {p192}          ", end='\r')
except KeyboardInterrupt:
    print("\nStopping scan...")
