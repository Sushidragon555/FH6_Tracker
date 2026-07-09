import socket

UDP_IP = "0.0.0.0"
UDP_PORT = 9999

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))

print("====================================================")
print(f" INVESTIGATING PORT {UDP_PORT}: Send incoming signals...")
print("====================================================\n")

try:
    while True:
        data, addr = sock.recvfrom(1024)
        print(f" -> Caught Packet! Size: {len(data)} bytes")
        if len(data) >= 176:
            # Print the raw slice where we expect the Car ID to be
            raw_slice = data[172:176]
            print(f"    Raw bytes at 172-176: {raw_slice}")
except KeyboardInterrupt:
    print("\nStopping investigation...")