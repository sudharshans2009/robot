# ============================================================================
# Hand WebSocket Controller - Complete System
# ============================================================================
# Combines: MAX30102 Heart Rate/SpO2 + Emergency Button + WebSocket Client
# Connects to server and sends biometric data and emergency alerts
# ============================================================================

from machine import Pin, SoftI2C
import network
import usocket
import ujson
import ubinascii
import urandom
import ustruct
import time
from utime import sleep_ms, ticks_ms, ticks_diff

# ============================================================================
# CONFIGURATION
# ============================================================================

WIFI_SSID = "Galaxy M12 7715"
WIFI_PASSWORD = "mikk5685"
SERVER_IP = "10.86.81.143"
SERVER_PORT = 8081

# Pin Configuration
MAX30102_SDA = 6
MAX30102_SCL = 7
EMERGENCY_BUTTON = 21
EMERGENCY_LED = 20

# ============================================================================
# MAX30102 SENSOR (Simplified for WebSocket)
# ============================================================================

class MAX30102:
    """Simplified MAX30102 driver for HR and SpO2"""
    
    ADDRESS = 0x57
    REG_FIFO_WRITE_PTR = 0x04
    REG_FIFO_OVERFLOW = 0x05
    REG_FIFO_READ_PTR = 0x06
    REG_FIFO_DATA = 0x07
    REG_FIFO_CONFIG = 0x08
    REG_MODE_CONFIG = 0x09
    REG_SPO2_CONFIG = 0x0A
    REG_LED1_PA = 0x0C
    REG_LED2_PA = 0x0D
    REG_PART_ID = 0xFF
    
    def __init__(self, i2c):
        self.i2c = i2c
        self.address = self.ADDRESS
        
        # Check device
        try:
            part_id = self.i2c.readfrom_mem(self.address, self.REG_PART_ID, 1)[0]
            if part_id == 0x15:
                print("✓ MAX30102 detected")
            else:
                print("⚠ Unexpected part ID: 0x" + "{:02x}".format(part_id))
        except OSError:
            raise RuntimeError("✗ MAX30102 not found")
        
        self.reset()
        self.setup()
        
    def reset(self):
        """Soft reset"""
        self.i2c.writeto_mem(self.address, self.REG_MODE_CONFIG, bytes([0x40]))
        time.sleep(0.1)
        
    def clear_fifo(self):
        """Reset FIFO pointers"""
        self.i2c.writeto_mem(self.address, self.REG_FIFO_WRITE_PTR, bytes([0]))
        self.i2c.writeto_mem(self.address, self.REG_FIFO_OVERFLOW, bytes([0]))
        self.i2c.writeto_mem(self.address, self.REG_FIFO_READ_PTR, bytes([0]))
        
    def setup(self):
        """Configure sensor"""
        # FIFO configuration (sample averaging = 8, rollover enabled)
        self.i2c.writeto_mem(self.address, self.REG_FIFO_CONFIG, bytes([0x4F]))
        # SpO2 mode (Red + IR)
        self.i2c.writeto_mem(self.address, self.REG_MODE_CONFIG, bytes([0x03]))
        # 100 Hz, 411μs pulse width, ADC range 4096
        self.i2c.writeto_mem(self.address, self.REG_SPO2_CONFIG, bytes([0x27]))
        # LED brightness (medium-high for better signal)
        self.i2c.writeto_mem(self.address, self.REG_LED1_PA, bytes([0x7F]))  # IR
        self.i2c.writeto_mem(self.address, self.REG_LED2_PA, bytes([0x7F]))  # Red
        # Clear FIFO
        self.clear_fifo()
        time.sleep(0.1)
        
    def read_fifo(self):
        """Read one sample from FIFO"""
        data = self.i2c.readfrom_mem(self.address, self.REG_FIFO_DATA, 6)
        red = (data[0] << 16 | data[1] << 8 | data[2]) & 0x03FFFF
        ir = (data[3] << 16 | data[4] << 8 | data[5]) & 0x03FFFF
        return red, ir
    
    def check_finger(self):
        """Check if finger is present"""
        # Take multiple readings to verify
        readings = 0
        for _ in range(3):
            red, ir = self.read_fifo()
            if red > 50000 and ir > 50000:
                readings += 1
            time.sleep(0.01)
        return readings >= 2
    
    def measure(self, duration=6):
        """Perform HR and SpO2 measurement"""
        if not self.check_finger():
            return {'heart_rate': 0, 'spo2': 0, 'status': 'No finger', 'red': 0, 'ir': 0}
        
        print("  Collecting {}s samples...".format(duration))
        
        # Clear FIFO before collection
        self.clear_fifo()
        time.sleep(0.1)
        
        red_buffer = []
        ir_buffer = []
        
        start_time = time.time()
        while time.time() - start_time < duration:
            red, ir = self.read_fifo()
            if red > 50000 and ir > 50000:
                red_buffer.append(red)
                ir_buffer.append(ir)
            time.sleep(0.01)
        
        print("  Collected {} samples".format(len(red_buffer)))
        
        if len(red_buffer) < 200:
            return {'heart_rate': 0, 'spo2': 0, 'status': 'Insufficient data', 'red': 0, 'ir': 0}
        
        # Calculate HR
        hr = self._calculate_hr(ir_buffer)
        # Calculate SpO2
        spo2 = self._calculate_spo2(red_buffer, ir_buffer)
        
        red, ir = self.read_fifo()
        
        return {
            'heart_rate': hr,
            'spo2': spo2,
            'status': 'Valid' if hr > 0 and spo2 > 0 else 'Invalid',
            'red': red,
            'ir': ir
        }
    
    def _calculate_hr(self, ir_buffer):
        """Calculate heart rate from IR signal"""
        # Apply smoothing
        smoothed = []
        window = 5
        for i in range(len(ir_buffer)):
            if i < window:
                smoothed.append(ir_buffer[i])
            else:
                avg = sum(ir_buffer[i-window:i]) / window
                smoothed.append(avg)
        
        # Find peaks
        signal_min = min(smoothed)
        signal_max = max(smoothed)
        threshold = signal_min + (signal_max - signal_min) * 0.5
        
        peaks = []
        min_distance = 50  # 500ms at 100Hz
        
        for i in range(1, len(smoothed) - 1):
            if (smoothed[i] > threshold and
                smoothed[i] > smoothed[i-1] and
                smoothed[i] > smoothed[i+1]):
                if len(peaks) == 0 or (i - peaks[-1]) >= min_distance:
                    peaks.append(i)
        
        if len(peaks) < 2:
            return 0
        
        # Calculate BPM
        intervals = []
        for i in range(1, len(peaks)):
            interval = peaks[i] - peaks[i-1]
            if 33 <= interval <= 200:
                intervals.append(interval)
        
        if len(intervals) == 0:
            return 0
        
        avg_interval = sum(intervals) / len(intervals)
        bpm = int((60 * 100) / avg_interval)
        
        return bpm if 40 <= bpm <= 180 else 0
    
    def _calculate_spo2(self, red_buffer, ir_buffer):
        """Calculate SpO2 from Red/IR ratio"""
        red_mean = sum(red_buffer) / len(red_buffer)
        ir_mean = sum(ir_buffer) / len(ir_buffer)
        
        red_ac = max(red_buffer) - min(red_buffer)
        ir_ac = max(ir_buffer) - min(ir_buffer)
        
        if red_mean == 0 or ir_mean == 0 or ir_ac == 0 or red_ac == 0:
            return 0
        
        R = (red_ac / red_mean) / (ir_ac / ir_mean)
        
        # Empirical formula
        spo2 = -45.060 * (R * R) + 30.354 * R + 94.845
        
        if R < 0.5:
            spo2 = 100
        elif R > 2.0:
            spo2 = 95
        
        spo2 = int(spo2)
        spo2 = max(90, min(100, spo2))
        
        return spo2 if 90 <= spo2 <= 100 else 0


# ============================================================================
# EMERGENCY BUTTON
# ============================================================================

class EmergencyButton:
    """Emergency button with LED indicator"""
    
    def __init__(self, button_pin=21, led_pin=20):
        self.button = Pin(button_pin, Pin.IN, Pin.PULL_DOWN)
        self.led = Pin(led_pin, Pin.OUT)
        self.led.off()
        
        self.emergency_active = False
        self.last_state = 0
        self.last_press_time = 0
        self.debounce_delay = 200
        
        print("✓ Emergency button: GP" + str(button_pin) + ", LED: GP" + str(led_pin))
    
    def check(self):
        """Check if button was pressed"""
        current_state = self.button.value()
        current_time = time.ticks_ms()
        
        # Rising edge detection
        if current_state == 1 and self.last_state == 0:
            if time.ticks_diff(current_time, self.last_press_time) > self.debounce_delay:
                self.last_press_time = current_time
                self.last_state = current_state
                return True
        
        self.last_state = current_state
        return False
    
    def toggle(self):
        """Toggle emergency state"""
        self.emergency_active = not self.emergency_active
        
        if self.emergency_active:
            self.led.on()
            print("\n🚨 EMERGENCY ACTIVATED 🚨")
        else:
            self.led.off()
            print("\n✓ Emergency cleared")
        
        return self.emergency_active


# ============================================================================
# WEBSOCKET CLIENT
# ============================================================================

class WebSocketClient:
    """Simple WebSocket client for MicroPython"""
    
    def __init__(self, url):
        self.url = url
        self.sock = None
        self.connected = False
    
    def connect(self):
        """Connect to WebSocket server"""
        try:
            # Parse URL
            url_parts = self.url.replace('ws://', '').split(':')
            host = url_parts[0]
            port = int(url_parts[1]) if len(url_parts) > 1 else 80
            
            # Create socket
            addr = usocket.getaddrinfo(host, port)[0][-1]
            self.sock = usocket.socket()
            self.sock.connect(addr)
            
            # WebSocket handshake
            key = ubinascii.b2a_base64(bytes(urandom.getrandbits(8) for _ in range(16)))[:-1]
            
            handshake = (
                "GET / HTTP/1.1\r\n"
                "Host: " + host + ":" + str(port) + "\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                "Sec-WebSocket-Key: " + key.decode() + "\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                "\r\n"
            )
            
            self.sock.send(handshake.encode())
            
            # Read response
            response = self.sock.recv(1024).decode()
            
            if '101' in response.split('\r\n')[0]:
                self.connected = True
                print("✓ WebSocket connected")
                return True
            else:
                print("✗ WebSocket handshake failed")
                return False
                
        except Exception as e:
            print("✗ Connection error: " + str(e))
            self.connected = False
            return False
    
    def send(self, data):
        """Send JSON data over WebSocket"""
        if not self.connected:
            return False
        
        try:
            message = ujson.dumps(data)
            payload = message.encode()
            
            # Create frame
            frame = bytearray()
            frame.append(0x81)  # Text frame, FIN bit set
            
            length = len(payload)
            if length < 126:
                frame.append(0x80 | length)
            elif length < 65536:
                frame.append(0x80 | 126)
                frame.extend(ustruct.pack('!H', length))
            else:
                frame.append(0x80 | 127)
                frame.extend(ustruct.pack('!Q', length))
            
            # Masking key
            mask = bytes(urandom.getrandbits(8) for _ in range(4))
            frame.extend(mask)
            
            # Masked payload
            masked = bytearray(payload[i] ^ mask[i % 4] for i in range(length))
            frame.extend(masked)
            
            self.sock.send(frame)
            return True
            
        except Exception as e:
            print("✗ Send error: " + str(e))
            self.connected = False
            return False
    
    def recv(self):
        """Receive and parse WebSocket message"""
        try:
            self.sock.setblocking(False)
            data = self.sock.recv(1024)
            
            if len(data) < 2:
                return None
            
            # Parse frame
            payload_len = data[1] & 0x7F
            mask_start = 2
            
            if payload_len == 126:
                payload_len = ustruct.unpack('!H', data[2:4])[0]
                mask_start = 4
            elif payload_len == 127:
                payload_len = ustruct.unpack('!Q', data[2:10])[0]
                mask_start = 10
            
            payload_start = mask_start
            payload = data[payload_start:payload_start + payload_len]
            
            message = payload.decode()
            return ujson.loads(message)
            
        except OSError:
            return None
        except Exception as e:
            return None
    
    def close(self):
        """Close WebSocket connection"""
        if self.sock:
            self.sock.close()
        self.connected = False


# ============================================================================
# HAND CONTROLLER
# ============================================================================

class HandController:
    """Main hand controller with sensors and WebSocket"""
    
    def __init__(self):
        print("\n" + "=" * 60)
        print("  HAND CONTROLLER - Starting...")
        print("=" * 60 + "\n")
        
        # Initialize I2C for MAX30102
        self.i2c = SoftI2C(sda=Pin(MAX30102_SDA), scl=Pin(MAX30102_SCL), freq=400000)
        
        # Initialize sensors
        try:
            self.max30102 = MAX30102(self.i2c)
        except RuntimeError as e:
            print("✗ MAX30102 error: " + str(e))
            self.max30102 = None
        
        self.emergency = EmergencyButton(EMERGENCY_BUTTON, EMERGENCY_LED)
        
        # WiFi
        self.wlan = network.WLAN(network.STA_IF)
        
        # WebSocket
        self.ws = None
        
        # Measurement interval
        self.last_measurement = 0
        self.measurement_interval = 10000  # 10 seconds
    
    def connect_wifi(self):
        """Connect to WiFi"""
        print("Connecting to WiFi...")
        self.wlan.active(True)
        self.wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        
        timeout = 10
        while timeout > 0:
            if self.wlan.isconnected():
                print("✓ WiFi connected: " + self.wlan.ifconfig()[0])
                return True
            time.sleep(1)
            timeout -= 1
            print("  Waiting... " + str(timeout) + "s")
        
        print("✗ WiFi connection failed")
        return False
    
    def connect_websocket(self):
        """Connect to WebSocket server"""
        print("Connecting to server: ws://" + SERVER_IP + ":" + str(SERVER_PORT))
        
        self.ws = WebSocketClient("ws://" + SERVER_IP + ":" + str(SERVER_PORT))
        
        if self.ws.connect():
            # Send identification
            self.ws.send({
                'type': 'identify',
                'client': 'hand'
            })
            print("✓ Identified as hand\n")
            return True
        
        return False
    
    def get_biometric_data(self):
        """Get heart rate and SpO2 data"""
        if not self.max30102:
            return None
        
        try:
            result = self.max30102.measure(duration=6)
            return result
        except Exception as e:
            print("✗ Measurement error: " + str(e))
            return None
    
    def send_biometric_data(self, data):
        """Send biometric data to server"""
        if not self.ws or not self.ws.connected:
            return False
        
        message = {
            'type': 'max30102_data',
            'payload': {
                'heart_rate': data['heart_rate'],
                'spo2': data['spo2'],
                'status': data['status'],
                'red': data['red'],
                'ir': data['ir']
            }
        }
        
        return self.ws.send(message)
    
    def send_emergency_alert(self, active):
        """Send emergency alert to server"""
        if not self.ws or not self.ws.connected:
            return False
        
        message = {
            'type': 'emergency',
            'payload': {
                'active': active,
                'timestamp': time.time()
            }
        }
        
        return self.ws.send(message)
    
    def run(self):
        """Main control loop"""
        # Connect to WiFi
        if not self.connect_wifi():
            print("Cannot continue without WiFi")
            return
        
        # Connect to WebSocket
        if not self.connect_websocket():
            print("Cannot continue without WebSocket")
            return
        
        print("=" * 60)
        print("  HAND CONTROLLER - RUNNING")
        print("=" * 60)
        print("  • Press emergency button to trigger alert")
        print("  • Heart rate/SpO2 measured every 10s")
        print("  • Press Ctrl+C to exit")
        print("=" * 60 + "\n")
        
        try:
            while True:
                current_time = ticks_ms()
                
                # Check emergency button
                if self.emergency.check():
                    is_active = self.emergency.toggle()
                    self.send_emergency_alert(is_active)
                
                # Periodic biometric measurement
                if ticks_diff(current_time, self.last_measurement) > self.measurement_interval:
                    self.last_measurement = current_time
                    
                    print("\n📊 Taking measurement...")
                    data = self.get_biometric_data()
                    
                    if data and data.get('heart_rate', 0) > 0:
                        print("  ❤️  HR: " + str(data['heart_rate']) + " BPM")
                        print("  🩸 SpO2: " + str(data['spo2']) + "%")
                        print("  Status: " + data['status'])
                        
                        if self.send_biometric_data(data):
                            print("  ✓ Data sent to server")
                        else:
                            print("  ✗ Failed to send data")
                    elif data:
                        print("  ⚠ " + data['status'])
                    else:
                        print("  ✗ Measurement error")
                
                # Check for incoming messages
                msg = self.ws.recv() if self.ws else None
                if msg:
                    print("📨 Received: " + str(msg))
                
                # Blink LED if emergency active
                if self.emergency.emergency_active:
                    self.emergency.led.toggle()
                    time.sleep_ms(200)
                else:
                    time.sleep_ms(100)
                
        except KeyboardInterrupt:
            print("\n\n" + "=" * 60)
            print("  Shutting down...")
            print("=" * 60)
            
            if self.ws:
                self.ws.close()
            
            self.emergency.led.off()
            
            print("  ✓ Hand controller stopped")
            print("=" * 60 + "\n")


# ============================================================================
# MAIN
# ============================================================================

def main():
    controller = HandController()
    controller.run()

if __name__ == "__main__":
    main()
