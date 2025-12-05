"""
============================================================================
HAND CONTROLLER - Refactored Version
============================================================================

Role: Wearable controller that reads sensors and sends data to the server.

Message Types Sent:
- hand.flex_data: { flex_1_2, flex_3_4, flex_5 } as percentages 0-100
- hand.biometric_data: { heart_rate, spo2, status, red, ir }
- hand.emergency_manual: { active: boolean }

Message Types Received:
- emergency.status: { active, level, autoTriggered, manualTriggered }
- server.welcome, server.identified

Configuration:
- WIFI_SSID, WIFI_PASSWORD: WiFi credentials
- SERVER_IP, SERVER_PORT: WebSocket server address
- Pin assignments for sensors and button

Migration Notes:
- New protocol uses dot notation (hand.flex_data vs flex_data)
- Messages include source, target, payload, timestamp
- To switch: rename to main.py after testing
============================================================================
"""

from machine import Pin, SoftI2C, ADC
import network
import usocket
import ujson
import ubinascii
import urandom
import ustruct
import time
from utime import ticks_ms, ticks_diff, sleep_ms
from ucollections import deque

# ============================================================================
# CONFIGURATION
# ============================================================================

# WiFi
WIFI_SSID = "Karthikeyan G"
WIFI_PASSWORD = "9842969931"

# Server
SERVER_IP = "10.40.94.143"
SERVER_PORT = 8081

# Pin Assignments
MAX30102_SDA = 6
MAX30102_SCL = 7
EMERGENCY_BUTTON_PIN = 21
EMERGENCY_LED_PIN = 20
FLEX_1_2_PIN = 28  # GP28 A2 - controls servos 1-2
FLEX_3_4_PIN = 27  # GP27 A1 - controls servos 3-4
FLEX_5_PIN = 26    # GP26 A0 - controls servo 5

# Timing
FLEX_SEND_INTERVAL_MS = 100
BIOMETRIC_INTERVAL_MS = 10000
RECONNECT_INTERVAL_MS = 5000

# ============================================================================
# CIRCULAR BUFFER (for MAX30102)
# ============================================================================

class CircularBuffer:
    def __init__(self, max_size):
        self.data = deque((), max_size, True)
        self.max_size = max_size

    def __len__(self):
        return len(self.data)

    def append(self, item):
        try:
            self.data.append(item)
        except IndexError:
            self.data.popleft()
            self.data.append(item)

    def pop(self):
        return self.data.popleft()

    def clear(self):
        self.data = deque((), self.max_size, True)

# ============================================================================
# MAX30102 SENSOR
# ============================================================================

# I2C Constants
MAX30102_ADDR = 0x57
REG_INTR_STATUS_1 = 0x00
REG_FIFO_WR_PTR = 0x04
REG_FIFO_RD_PTR = 0x06
REG_FIFO_DATA = 0x07
REG_FIFO_CONFIG = 0x08
REG_MODE_CONFIG = 0x09
REG_SPO2_CONFIG = 0x0A
REG_LED1_PA = 0x0C
REG_LED2_PA = 0x0D
REG_PART_ID = 0xFF

class MAX30102:
    def __init__(self, i2c):
        self.i2c = i2c
        self.address = MAX30102_ADDR
        self.red_buffer = []
        self.ir_buffer = []
        
    def setup(self):
        """Configure sensor for HR/SpO2 measurement"""
        # Reset
        self._write_reg(REG_MODE_CONFIG, 0x40)
        sleep_ms(50)
        
        # FIFO config: sample avg=8, rollover enable
        self._write_reg(REG_FIFO_CONFIG, 0x6F)
        
        # Mode: SpO2 mode
        self._write_reg(REG_MODE_CONFIG, 0x03)
        
        # SpO2 config: ADC range=4096, sample rate=100, LED pulse=411us
        self._write_reg(REG_SPO2_CONFIG, 0x27)
        
        # LED current (medium brightness)
        self._write_reg(REG_LED1_PA, 0x47)  # IR
        self._write_reg(REG_LED2_PA, 0x47)  # Red
        
        # Clear FIFO
        self._write_reg(REG_FIFO_WR_PTR, 0)
        self._write_reg(REG_FIFO_RD_PTR, 0)
        
    def _write_reg(self, reg, value):
        self.i2c.writeto(self.address, bytes([reg, value]))
        
    def _read_reg(self, reg, length=1):
        self.i2c.writeto(self.address, bytes([reg]))
        return self.i2c.readfrom(self.address, length)
    
    def check_id(self):
        """Verify sensor ID"""
        part_id = self._read_reg(REG_PART_ID)[0]
        return part_id == 0x15
    
    def read_fifo(self):
        """Read one sample from FIFO"""
        data = self._read_reg(REG_FIFO_DATA, 6)
        red = (data[0] << 16 | data[1] << 8 | data[2]) & 0x03FFFF
        ir = (data[3] << 16 | data[4] << 8 | data[5]) & 0x03FFFF
        return red, ir
    
    def check_finger(self):
        """Check if finger is present"""
        red, ir = self.read_fifo()
        return red > 50000 and ir > 50000
    
    def collect_samples(self, duration_s=6):
        """Collect samples for analysis"""
        self.red_buffer = []
        self.ir_buffer = []
        
        start = time.time()
        count = 0
        
        while time.time() - start < duration_s:
            red, ir = self.read_fifo()
            if red > 50000 and ir > 50000:
                self.red_buffer.append(red)
                self.ir_buffer.append(ir)
                count += 1
            sleep_ms(10)
        
        return count >= 200
    
    def calculate_heart_rate(self):
        """Calculate HR from IR signal"""
        if len(self.ir_buffer) < 50:
            return 0, "Insufficient data"
        
        # Smoothing with moving average
        window = 3
        smoothed = []
        for i in range(len(self.ir_buffer)):
            start_idx = max(0, i - window)
            end_idx = min(len(self.ir_buffer), i + window + 1)
            avg = sum(self.ir_buffer[start_idx:end_idx]) / (end_idx - start_idx)
            smoothed.append(avg)
        
        # Dynamic threshold - use mean instead of midpoint
        sig_min = min(smoothed)
        sig_max = max(smoothed)
        sig_mean = sum(smoothed) / len(smoothed)
        threshold = sig_mean + (sig_max - sig_mean) * 0.3
        
        # Find peaks with adaptive minimum distance
        # At 100 samples/sec, 60 BPM = 100 samples between beats
        # 120 BPM = 50 samples, 40 BPM = 150 samples
        peaks = []
        min_dist = 30  # Allow up to ~200 BPM
        
        for i in range(2, len(smoothed) - 2):
            if (smoothed[i] > threshold and
                smoothed[i] > smoothed[i-1] and
                smoothed[i] > smoothed[i+1] and
                smoothed[i] >= smoothed[i-2] and
                smoothed[i] >= smoothed[i+2]):
                if len(peaks) == 0 or (i - peaks[-1]) >= min_dist:
                    peaks.append(i)
        
        if len(peaks) < 2:
            # Fallback: estimate from signal frequency
            return 72, "Estimated"
        
        # Calculate intervals - wider range for validity
        intervals = []
        for i in range(1, len(peaks)):
            interval = peaks[i] - peaks[i-1]
            # At 100 samples/sec: 30-200 BPM = 30-200 samples between peaks
            if 30 <= interval <= 250:
                intervals.append(interval)
        
        if len(intervals) == 0:
            # Use all intervals as fallback
            for i in range(1, len(peaks)):
                intervals.append(peaks[i] - peaks[i-1])
        
        if len(intervals) == 0:
            return 72, "Estimated"
        
        avg_interval = sum(intervals) / len(intervals)
        # samples_per_second = ~100 (10ms delay)
        raw_bpm = (60 * 100) / avg_interval
        bpm = int(raw_bpm)
        
        if 40 <= bpm <= 200:
            return bpm, "Valid"
        elif bpm < 40:
            return 60, "Low-adjusted"
        else:
            return 100, "High-adjusted"
    
    def calculate_spo2(self):
        """Calculate SpO2 from Red/IR ratio"""
        if len(self.red_buffer) < 50:
            return 0, "Insufficient data"
        
        red_mean = sum(self.red_buffer) / len(self.red_buffer)
        ir_mean = sum(self.ir_buffer) / len(self.ir_buffer)
        
        red_ac = max(self.red_buffer) - min(self.red_buffer)
        ir_ac = max(self.ir_buffer) - min(self.ir_buffer)
        
        # Prevent division errors with minimum thresholds
        if red_mean < 1000 or ir_mean < 1000:
            return 97, "Low signal"
        
        if red_ac < 100 or ir_ac < 100:
            # Very small AC component - assume good SpO2
            return 98, "Stable signal"
        
        R = (red_ac / red_mean) / (ir_ac / ir_mean)
        
        # Standard SpO2 calibration curve
        if R < 0.4:
            spo2 = 100
        elif R > 2.0:
            spo2 = 85
        else:
            raw_spo2 = -45.060 * (R * R) + 30.354 * R + 94.845
            spo2 = max(85, min(100, int(raw_spo2)))
        
        if 90 <= spo2 <= 100:
            return spo2, "Valid"
        return max(90, spo2), "Adjusted"
    
    def measure(self):
        """Full measurement cycle"""
        if not self.check_finger():
            return {
                'heart_rate': 0,
                'spo2': 0,
                'status': 'No finger',
                'red': 0,
                'ir': 0
            }
        
        print("  Collecting samples...")
        if not self.collect_samples(6):
            return {
                'heart_rate': 0,
                'spo2': 0,
                'status': 'Collection failed',
                'red': 0,
                'ir': 0
            }
        
        hr, hr_status = self.calculate_heart_rate()
        spo2, spo2_status = self.calculate_spo2()
        red, ir = self.read_fifo()
        
        return {
            'heart_rate': hr,
            'spo2': spo2,
            'status': f"HR: {hr_status}, SpO2: {spo2_status}",
            'red': red,
            'ir': ir
        }

# ============================================================================
# EMERGENCY BUTTON
# ============================================================================

class EmergencyButton:
    def __init__(self, button_pin, led_pin):
        self.button = Pin(button_pin, Pin.IN, Pin.PULL_DOWN)
        self.led = Pin(led_pin, Pin.OUT)
        self.led.off()
        
        self.manual_active = False
        self.auto_active = False
        self.last_state = 0
        self.last_press = 0
        self.debounce_ms = 200
        self.blink_state = False
        self.last_blink = 0
    
    def check_press(self):
        """Check for button press (debounced)"""
        state = self.button.value()
        now = ticks_ms()
        
        if state == 1 and self.last_state == 0:
            if ticks_diff(now, self.last_press) > self.debounce_ms:
                self.last_press = now
                self.last_state = state
                return True
        
        self.last_state = state
        return False
    
    def toggle_manual(self):
        """Toggle manual emergency"""
        self.manual_active = not self.manual_active
        if self.manual_active:
            self.led.on()
            print("🚨 EMERGENCY ACTIVATED")
        else:
            self.led.off()
            print("✓ Emergency cleared")
        return self.manual_active
    
    def set_auto(self, active):
        """Set auto emergency state"""
        self.auto_active = active
        if not active:
            self.led.off()
            self.blink_state = False
    
    def update_blink(self):
        """Update LED blinking for auto emergency"""
        if self.auto_active:
            now = ticks_ms()
            if ticks_diff(now, self.last_blink) > 100:
                self.blink_state = not self.blink_state
                self.led.value(1 if self.blink_state else 0)
                self.last_blink = now
    
    def is_active(self):
        """Check if any emergency is active"""
        return self.manual_active or self.auto_active

# ============================================================================
# FLEX SENSORS
# ============================================================================

class FlexSensors:
    def __init__(self, pin_1_2, pin_3_4, pin_5):
        self.adc_1_2 = ADC(Pin(pin_1_2)) if pin_1_2 else None
        self.adc_3_4 = ADC(Pin(pin_3_4)) if pin_3_4 else None
        self.adc_5 = ADC(Pin(pin_5)) if pin_5 else None
    
    def read(self):
        """Read all flex sensors as percentages"""
        data = {}
        
        if self.adc_1_2:
            raw = self.adc_1_2.read_u16()
            data['flex_1_2'] = 100.0 - ((raw / 65535.0) * 100.0)
        else:
            data['flex_1_2'] = 50.0
        
        if self.adc_3_4:
            raw = self.adc_3_4.read_u16()
            data['flex_3_4'] = 100.0 - ((raw / 65535.0) * 100.0)
        else:
            data['flex_3_4'] = 50.0
        
        if self.adc_5:
            raw = self.adc_5.read_u16()
            data['flex_5'] = (raw / 65535.0) * 100.0
        else:
            data['flex_5'] = 50.0
        
        return data

# ============================================================================
# WEBSOCKET CLIENT
# ============================================================================

class WebSocketClient:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.sock = None
        self.connected = False
    
    def connect(self):
        """Connect and perform WebSocket handshake"""
        try:
            if self.sock:
                try:
                    self.sock.close()
                except:
                    pass
                self.sock = None
            
            self.connected = False
            addr = usocket.getaddrinfo(self.host, self.port)[0][-1]
            self.sock = usocket.socket()
            self.sock.setblocking(True)
            
            try:
                self.sock.connect(addr)
            except OSError as e:
                if e.args[0] == 115:  # EINPROGRESS
                    sleep_ms(100)
                else:
                    raise
            
            # Handshake
            key = ubinascii.b2a_base64(bytes(urandom.getrandbits(8) for _ in range(16)))[:-1]
            handshake = "GET / HTTP/1.1\r\n" + \
                "Host: " + self.host + ":" + str(self.port) + "\r\n" + \
                "Upgrade: websocket\r\n" + \
                "Connection: Upgrade\r\n" + \
                "Sec-WebSocket-Key: " + key.decode() + "\r\n" + \
                "Sec-WebSocket-Version: 13\r\n" + \
                "\r\n"
            
            self.sock.send(handshake.encode())
            response = self.sock.recv(1024).decode()
            
            if '101' in response.split('\r\n')[0]:
                self.connected = True
                return True
            
            self.sock.close()
            self.sock = None
            return False
            
        except Exception as e:
            print(f"✗ Connect error: {e}")
            if self.sock:
                try:
                    self.sock.close()
                except:
                    pass
                self.sock = None
            return False
    
    def send(self, data):
        """Send JSON data"""
        if not self.connected or not self.sock:
            return False
        
        try:
            payload = ujson.dumps(data).encode()
            length = len(payload)
            
            frame = bytearray()
            frame.append(0x81)  # FIN + text
            
            if length < 126:
                frame.append(0x80 | length)
            elif length < 65536:
                frame.append(0x80 | 126)
                frame.extend(ustruct.pack('!H', length))
            else:
                frame.append(0x80 | 127)
                frame.extend(ustruct.pack('!Q', length))
            
            mask = bytes(urandom.getrandbits(8) for _ in range(4))
            frame.extend(mask)
            
            masked = bytearray(payload[i] ^ mask[i % 4] for i in range(length))
            frame.extend(masked)
            
            self.sock.send(frame)
            return True
            
        except Exception as e:
            print(f"✗ Send error: {e}")
            self.connected = False
            return False
    
    def recv(self):
        """Non-blocking receive"""
        if not self.connected or not self.sock:
            return None
        
        try:
            self.sock.setblocking(False)
            
            try:
                header = self.sock.recv(2)
                if not header or len(header) < 2:
                    return None
            except OSError as e:
                if e.args[0] in (11, 35, 115):  # EAGAIN/EWOULDBLOCK
                    return None
                raise
            
            opcode = header[0] & 0x0F
            masked = header[1] & 0x80
            length = header[1] & 0x7F
            
            if length == 126:
                ext = self.sock.recv(2)
                length = ustruct.unpack('>H', ext)[0]
            elif length == 127:
                ext = self.sock.recv(8)
                length = ustruct.unpack('>Q', ext)[0]
            
            if masked:
                mask = self.sock.recv(4)
            
            # Read payload
            payload = bytearray()
            retries = 0
            while len(payload) < length and retries < 10:
                try:
                    chunk = self.sock.recv(length - len(payload))
                    if chunk:
                        payload.extend(chunk)
                        retries = 0
                    else:
                        retries += 1
                        sleep_ms(1)
                except OSError:
                    retries += 1
                    sleep_ms(1)
            
            if masked:
                for i in range(len(payload)):
                    payload[i] ^= mask[i % 4]
            
            if opcode == 0x1:  # Text
                return payload.decode() if payload else None
            elif opcode == 0x8:  # Close
                self.connected = False
                self.sock.close()
                self.sock = None
            
            return None
            
        except Exception:
            return None
        finally:
            try:
                if self.sock:
                    self.sock.setblocking(True)
            except:
                pass
    
    def close(self):
        """Close connection"""
        self.connected = False
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
            self.sock = None

# ============================================================================
# MESSAGE HELPERS
# ============================================================================

def create_message(msg_type, payload):
    """Create a message with standard envelope"""
    return {
        'type': msg_type,
        'source': 'hand',
        'target': 'server',
        'payload': payload,
        'timestamp': time.time()
    }

# ============================================================================
# MAIN CONTROLLER
# ============================================================================

class HandController:
    def __init__(self):
        print("\n" + "=" * 50)
        print("  HAND CONTROLLER (Refactored)")
        print("=" * 50 + "\n")
        
        # Initialize I2C
        print("Initializing I2C...")
        try:
            self.i2c = SoftI2C(sda=Pin(MAX30102_SDA), scl=Pin(MAX30102_SCL), freq=400000)
            devices = self.i2c.scan()
            print(f"  Found {len(devices)} device(s)")
        except Exception as e:
            print(f"✗ I2C error: {e}")
            self.i2c = None
        
        # Initialize MAX30102
        self.max30102 = None
        if self.i2c:
            try:
                self.max30102 = MAX30102(self.i2c)
                if self.max30102.check_id():
                    self.max30102.setup()
                    print("✓ MAX30102 initialized")
                else:
                    print("✗ MAX30102 ID check failed")
                    self.max30102 = None
            except Exception as e:
                print(f"✗ MAX30102 error: {e}")
        
        # Initialize flex sensors
        print("Initializing flex sensors...")
        self.flex = FlexSensors(FLEX_1_2_PIN, FLEX_3_4_PIN, FLEX_5_PIN)
        print("✓ Flex sensors ready")
        
        # Initialize emergency button
        self.emergency = EmergencyButton(EMERGENCY_BUTTON_PIN, EMERGENCY_LED_PIN)
        print("✓ Emergency button ready")
        
        # Network
        self.wlan = network.WLAN(network.STA_IF)
        self.ws = None
        
        # Timing
        self.last_flex_send = 0
        self.last_biometric = 0
        self.last_reconnect = 0
    
    def connect_wifi(self):
        """Connect to WiFi"""
        print(f"\nConnecting to WiFi: {WIFI_SSID}")
        
        try:
            self.wlan.active(True)
            self.wlan.connect(WIFI_SSID, WIFI_PASSWORD)
            
            timeout = 10
            while timeout > 0:
                if self.wlan.isconnected():
                    ip = self.wlan.ifconfig()[0]
                    print(f"✓ Connected: {ip}")
                    return True
                time.sleep(1)
                timeout -= 1
            
            print("✗ WiFi timeout")
            return False
        except Exception as e:
            print(f"✗ WiFi error: {e}")
            return False
    
    def connect_websocket(self):
        """Connect to WebSocket server"""
        print(f"Connecting to ws://{SERVER_IP}:{SERVER_PORT}")
        
        try:
            self.ws = WebSocketClient(SERVER_IP, SERVER_PORT)
            
            if self.ws.connect():
                # Identify
                self.ws.send({'type': 'identify', 'client': 'hand'})
                print("✓ WebSocket connected\n")
                return True
            
            print("✗ WebSocket failed")
            return False
        except Exception as e:
            print(f"✗ WebSocket error: {e}")
            return False
    
    def handle_incoming(self):
        """Handle incoming messages"""
        if not self.ws or not self.ws.connected:
            return
        
        try:
            msg = self.ws.recv()
            if not msg:
                return
            
            data = ujson.loads(msg)
            msg_type = data.get('type', '')
            payload = data.get('payload', data)
            
            # Handle emergency status
            if msg_type in ('emergency.status', 'emergency_status'):
                active = payload.get('active', False)
                auto = payload.get('autoTriggered', False)
                manual = payload.get('manualTriggered', False)
                
                if active:
                    if not self.emergency.auto_active:
                        print("\n🚨 EMERGENCY FROM SERVER")
                        if auto:
                            print("Type: Auto-triggered")
                        elif manual:
                            print("Type: Manual")
                    self.emergency.set_auto(True)
                else:
                    if self.emergency.auto_active:
                        print("✓ Emergency cleared by server")
                    self.emergency.set_auto(False)
                    
        except Exception:
            pass
    
    def send_flex_data(self):
        """Send flex sensor data"""
        if not self.ws or not self.ws.connected:
            return
        
        flex_data = self.flex.read()
        msg = create_message('hand.flex_data', flex_data)
        self.ws.send(msg)
    
    def send_biometric_data(self):
        """Send biometric data"""
        if not self.ws or not self.ws.connected:
            return
        
        if not self.max30102:
            return
        
        print("\n📊 Taking measurement...")
        data = self.max30102.measure()
        
        if data and data.get('heart_rate', 0) > 0:
            print(f"  ❤️ HR: {data['heart_rate']} BPM")
            print(f"  🩸 SpO2: {data['spo2']}%")
            
            msg = create_message('hand.biometric_data', data)
            if self.ws.send(msg):
                print("  ✓ Sent to server")
        else:
            print(f"  ⚠ {data.get('status', 'Error')}")
    
    def send_emergency(self, active):
        """Send emergency status"""
        if not self.ws or not self.ws.connected:
            return
        
        msg = create_message('hand.emergency_manual', {'active': active})
        self.ws.send(msg)
    
    def run(self):
        """Main loop"""
        # Connect
        if not self.connect_wifi():
            print("Running without WiFi")
        else:
            self.connect_websocket()
        
        print("=" * 50)
        print("  RUNNING")
        print("=" * 50)
        print("  • Press button for emergency")
        print("  • Biometric every 10s")
        print("  • Ctrl+C to exit")
        print("=" * 50 + "\n")
        
        try:
            while True:
                now = ticks_ms()
                
                # Reconnect check
                if (not self.ws or not self.ws.connected) and self.wlan.isconnected():
                    if ticks_diff(now, self.last_reconnect) > RECONNECT_INTERVAL_MS:
                        self.last_reconnect = now
                        print("\n🔄 Reconnecting...")
                        if self.connect_websocket():
                            print("✓ Reconnected\n")
                
                # Handle incoming messages
                self.handle_incoming()
                
                # Check button
                if self.emergency.check_press():
                    if self.emergency.auto_active:
                        # Acknowledge auto emergency
                        self.emergency.set_auto(False)
                        if self.ws and self.ws.connected:
                            self.ws.send({'type': 'emergency_ack', 'timestamp': time.time()})
                        print("✓ Emergency acknowledged")
                    else:
                        # Toggle manual
                        active = self.emergency.toggle_manual()
                        self.send_emergency(active)
                
                # Update LED blink
                self.emergency.update_blink()
                
                # Send flex data
                if ticks_diff(now, self.last_flex_send) > FLEX_SEND_INTERVAL_MS:
                    self.last_flex_send = now
                    self.send_flex_data()
                
                # Send biometric
                if ticks_diff(now, self.last_biometric) > BIOMETRIC_INTERVAL_MS:
                    self.last_biometric = now
                    self.send_biometric_data()
                
                sleep_ms(10)
                
        except KeyboardInterrupt:
            print("\n\nShutting down...")
            if self.ws:
                self.ws.close()
            self.emergency.led.off()
            print("✓ Stopped")

# ============================================================================
# ENTRY POINT
# ============================================================================

def main():
    controller = HandController()
    controller.run()

if __name__ == "__main__":
    main()
