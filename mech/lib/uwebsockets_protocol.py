# Minimal WebSocket protocol implementation for MicroPython
# Based on github.com/danni/uwebsockets

import usocket as socket
import ubinascii
import urandom
import ustruct

# WebSocket opcodes
OP_CONT = 0x0
OP_TEXT = 0x1
OP_BYTES = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xa

class Websocket:
    def __init__(self, sock):
        self.sock = sock
        self.open = True

    def write_frame(self, opcode, data=b''):
        fin = 0x80
        mask_bit = 0x80
        
        length = len(data)
        
        # Frame header
        header = bytearray()
        header.append(fin | opcode)
        
        # Payload length
        if length < 126:
            header.append(mask_bit | length)
        elif length < 65536:
            header.append(mask_bit | 126)
            header.extend(ustruct.pack('>H', length))
        else:
            header.append(mask_bit | 127)
            header.extend(ustruct.pack('>Q', length))
        
        # Masking key
        mask = bytes([urandom.getrandbits(8) for _ in range(4)])
        header.extend(mask)
        
        # Send header
        self.sock.send(header)
        
        # Send masked payload
        if length > 0:
            masked_data = bytearray(data)
            for i in range(length):
                masked_data[i] ^= mask[i % 4]
            self.sock.send(masked_data)

    def read_frame(self):
        # Read first 2 bytes
        try:
            header = self.sock.recv(2)
            if not header or len(header) < 2:
                return None, None
        except:
            return None, None
        
        fin = header[0] & 0x80
        opcode = header[0] & 0x0f
        masked = header[1] & 0x80
        length = header[1] & 0x7f
        
        # Extended length
        if length == 126:
            length_data = self.sock.recv(2)
            length = ustruct.unpack('>H', length_data)[0]
        elif length == 127:
            length_data = self.sock.recv(8)
            length = ustruct.unpack('>Q', length_data)[0]
        
        # Mask (server doesn't mask)
        if masked:
            mask = self.sock.recv(4)
        
        # Payload
        payload = bytearray()
        while len(payload) < length:
            chunk = self.sock.recv(length - len(payload))
            if not chunk:
                break
            payload.extend(chunk)
        
        # Unmask if needed
        if masked:
            for i in range(len(payload)):
                payload[i] ^= mask[i % 4]
        
        return opcode, bytes(payload)

    def send(self, data):
        if isinstance(data, str):
            data = data.encode()
        self.write_frame(OP_TEXT, data)

    def recv(self):
        opcode, data = self.read_frame()
        if opcode == OP_TEXT or opcode == OP_BYTES:
            return data.decode() if data else None
        elif opcode == OP_CLOSE:
            self.open = False
            return None
        elif opcode == OP_PING:
            self.write_frame(OP_PONG, data)
            return None
        return None

    def close(self):
        self.write_frame(OP_CLOSE)
        self.sock.close()
        self.open = False
