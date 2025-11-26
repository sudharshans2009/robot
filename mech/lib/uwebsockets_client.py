# WebSocket client implementation for MicroPython
# Based on github.com/danni/uwebsockets

import usocket as socket
import ubinascii
import urandom

def connect(uri):
    """Connect to a WebSocket server"""
    from uwebsockets_protocol import Websocket
    
    # Parse URI
    if uri.startswith("ws://"):
        uri = uri[5:]
    elif uri.startswith("wss://"):
        raise NotImplementedError("wss:// not supported")
    
    if "/" in uri:
        host, path = uri.split("/", 1)
        path = "/" + path
    else:
        host = uri
        path = "/"
    
    if ":" in host:
        host, port = host.split(":")
        port = int(port)
    else:
        port = 80
    
    # Connect socket
    addr = socket.getaddrinfo(host, port)[0][-1]
    sock = socket.socket()
    sock.connect(addr)
    
    # WebSocket handshake
    key = ubinascii.b2a_base64(bytes([urandom.getrandbits(8) for _ in range(16)]))[:-1]
    
    handshake = (
        "GET {} HTTP/1.1\r\n"
        "Host: {}\r\n"
        "Connection: Upgrade\r\n"
        "Upgrade: websocket\r\n"
        "Sec-WebSocket-Key: {}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    ).format(path, host, key.decode())
    
    sock.send(handshake.encode())
    
    # Read response headers
    header = b''
    while True:
        chunk = sock.recv(1)
        if not chunk:
            break
        header += chunk
        if header.endswith(b'\r\n\r\n'):
            break
    
    # Check response
    header = header.decode()
    if "101" not in header.split('\r\n')[0]:
        raise Exception("WebSocket handshake failed: " + header.split('\r\n')[0])
    
    # Set non-blocking mode
    sock.setblocking(False)
    
    return Websocket(sock)
