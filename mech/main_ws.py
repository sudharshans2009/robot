# WebSocket-Only Mech Controller for Raspberry Pi Pico WH
# Built-in WebSocket implementation (no external libraries needed)
# Automatically connects to WebSocket server and streams sensor data
# Listens for flex sensor commands to control servos
#
# Hardware Configuration:
# - Gas Sensor (MQ6): GP26 (ADC), GP18 (Alert LED)
# - Servos (5x): GP10, GP11, GP12, GP14, GP15
# - Temperature (DS18B20): GP22 (OneWire)
# - Ultrasonic (HC-SR04): GP20 (Trigger), GP19 (Echo)

from machine import Pin, PWM, ADC
import onewire, ds18x20
import time
import network
import ujson as json
import usocket as socket
import ubinascii
import urandom
import ustruct

# ==================== CONFIGURATION ====================
# WIFI_SSID = "B's Galaxy F12"
# WIFI_PASSWORD = "pyxf8869"
# WS_SERVER = "10.239.13.143"
# WS_PORT = 8081
WIFI_SSID = "Karthikeyan G"
WIFI_PASSWORD = "9842969931"
WS_SERVER = "10.222.185.143"
WS_PORT = 8081

# ==================== WEBSOCKET CLIENT ====================

class WebSocketClient:
    def __init__(self, url):
        self.url = url
        self.sock = None
    
    def connect(self):
        # Parse URL
        if self.url.startswith('ws://'):
            url = self.url[5:]
        else:
            url = self.url
        
        if '/' in url:
            host_port, path = url.split('/', 1)
            path = '/' + path
        else:
            host_port = url
            path = '/'
        
        if ':' in host_port:
            host, port = host_port.split(':')
            port = int(port)
        else:
            host = host_port
            port = 80
        
        print(f"Connecting to {host}:{port}{path}")
        
        # Create socket
        addr = socket.getaddrinfo(host, port)[0][-1]
        self.sock = socket.socket()
        self.sock.connect(addr)
        
        # WebSocket handshake
        key = ubinascii.b2a_base64(bytes([urandom.getrandbits(8) for _ in range(16)]))[:-1]
        
        handshake = (
            'GET {} HTTP/1.1\r\n'
            'Host: {}:{}\r\n'
            'Upgrade: websocket\r\n'
            'Connection: Upgrade\r\n'
            'Sec-WebSocket-Key: {}\r\n'
            'Sec-WebSocket-Version: 13\r\n'
            '\r\n'
        ).format(path, host, port, key.decode())
        
        print("Sending handshake...")
        self.sock.send(handshake.encode())
        
        # Read response
        print("Reading response...")
        response = b''
        timeout = 0
        while b'\r\n\r\n' not in response:
            chunk = self.sock.recv(1)
            if not chunk:
                timeout += 1
                if timeout > 1000:
                    break
                time.sleep(0.001)
                continue
            response += chunk
        
        response_text = response.decode()
        print("Response:", response_text[:200])
        
        if b'101' not in response:
            raise Exception('WebSocket handshake failed: ' + response_text.split('\r\n')[0])
        
        print("Handshake successful!")
        
        # Set non-blocking
        self.sock.setblocking(False)
        return self
    
    def send(self, data):
        if isinstance(data, str):
            data = data.encode()
        
        length = len(data)
        
        # Frame header
        frame = bytearray()
        frame.append(0x81)  # FIN + text frame
        
        # Payload length
        if length < 126:
            frame.append(0x80 | length)
        elif length < 65536:
            frame.append(0x80 | 126)
            frame.extend(ustruct.pack('>H', length))
        else:
            frame.append(0x80 | 127)
            frame.extend(ustruct.pack('>Q', length))
        
        # Masking key
        mask = bytes([urandom.getrandbits(8) for _ in range(4)])
        frame.extend(mask)
        
        # Masked payload
        masked_data = bytearray(data)
        for i in range(length):
            masked_data[i] ^= mask[i % 4]
        frame.extend(masked_data)
        
        self.sock.send(frame)
    
    def recv(self):
        try:
            # Read frame header (non-blocking)
            header = self.sock.recv(2)
            if not header or len(header) < 2:
                return None
            
            opcode = header[0] & 0x0F
            masked = header[1] & 0x80
            length = header[1] & 0x7F
            
            # Extended length
            if length == 126:
                ext_len = b''
                while len(ext_len) < 2:
                    try:
                        chunk = self.sock.recv(2 - len(ext_len))
                        if chunk:
                            ext_len += chunk
                        else:
                            return None
                    except OSError:
                        return None
                length = ustruct.unpack('>H', ext_len)[0]
            elif length == 127:
                ext_len = b''
                while len(ext_len) < 8:
                    try:
                        chunk = self.sock.recv(8 - len(ext_len))
                        if chunk:
                            ext_len += chunk
                        else:
                            return None
                    except OSError:
                        return None
                length = ustruct.unpack('>Q', ext_len)[0]
            
            # Mask (servers don't mask)
            if masked:
                mask = b''
                while len(mask) < 4:
                    try:
                        chunk = self.sock.recv(4 - len(mask))
                        if chunk:
                            mask += chunk
                        else:
                            return None
                    except OSError:
                        return None
            
            # Payload - read all data
            payload = bytearray()
            attempts = 0
            max_attempts = 100
            while len(payload) < length and attempts < max_attempts:
                try:
                    chunk = self.sock.recv(length - len(payload))
                    if chunk:
                        payload.extend(chunk)
                        attempts = 0  # Reset on successful read
                    else:
                        attempts += 1
                        time.sleep(0.001)  # Small delay before retry
                except OSError:
                    attempts += 1
                    time.sleep(0.001)
            
            if len(payload) < length:
                return None
            
            # Unmask if needed
            if masked:
                for i in range(len(payload)):
                    payload[i] ^= mask[i % 4]
            
            # Handle opcodes
            if opcode == 0x1 or opcode == 0x2:  # Text or binary
                return payload.decode() if payload else None
            elif opcode == 0x9:  # Ping
                self.send_pong(payload)
                return None
            elif opcode == 0x8:  # Close
                return None
            
            return None
        except OSError:
            return None
    
    def send_pong(self, data):
        frame = bytearray()
        frame.append(0x8A)  # FIN + pong
        frame.append(len(data))
        frame.extend(data)
        self.sock.send(frame)
    
    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except:
                pass

# ==================== SENSOR CLASSES ====================

class GasSensor:
    def __init__(self, adc_pin=26, alert_pin=18, threshold=30000):
        self.adc = ADC(Pin(adc_pin))
        self.alert_led = Pin(alert_pin, Pin.OUT)
        self.threshold = threshold
        self.alert_led.value(0)
        print(f"✓ Gas Sensor on GP{adc_pin}")
    
    def read(self):
        raw = self.adc.read_u16()
        percent = (raw / 65535) * 100
        alert = raw > self.threshold
        if alert:
            self.alert_led.value(1)
        else:
            self.alert_led.value(0)
        return {"raw": raw, "percent": round(percent, 2), "alert": alert}

class Servo:
    def __init__(self, pin, min_us=500, max_us=2500, freq=50):
        self.pwm = PWM(Pin(pin))
        self.pwm.freq(freq)
        self.min_us = min_us
        self.max_us = max_us
        self.current_angle = 90
        self.set_angle(90)
    
    def set_angle(self, degrees):
        degrees = max(0, min(180, degrees))
        pulse_width = self.min_us + (self.max_us - self.min_us) * degrees / 180
        duty = int(pulse_width * 65535 / 20000)
        self.pwm.duty_u16(duty)
        self.current_angle = degrees
        return degrees

class ServoController:
    def __init__(self, pins):
        self.servos = {}
        for i, pin in enumerate(pins, 1):
            self.servos[f'servo_{i}'] = Servo(pin)
        print(f"✓ {len(self.servos)} Servos on {pins}")
    
    def _apply_servo_mapping(self, servo_name, angle):
        """Apply servo-specific angle mappings (inversions, offsets, etc.)"""
        # Servo 4 is inverted: 90° -> 0°, 0° -> 90°
        if angle > 90:
            angle = 10 - angle
        if servo_name == 'servo_2':
            return 90 - angle
        elif servo_name == 'servo_1':
            return 90 + angle
        return angle
    
    def set_angle(self, servo_name, angle):
        if servo_name in self.servos:
            mapped_angle = self._apply_servo_mapping(servo_name, angle)
            return self.servos[servo_name].set_angle(mapped_angle)
        return None
    
    def set_all_angles(self, angle):
        for servo_name, servo in self.servos.items():
            mapped_angle = self._apply_servo_mapping(servo_name, angle)
            servo.set_angle(mapped_angle)
    
    def set_angles_from_dict(self, servo_angles):
        """Set multiple servo angles from dictionary
        Args:
            servo_angles: dict like {servo_1: 90, servo_2: 0, servo_3: 45, ...}
        """
        for servo_name, angle in servo_angles.items():
            if servo_name in self.servos:
                mapped_angle = self._apply_servo_mapping(servo_name, angle)
                self.servos[servo_name].set_angle(mapped_angle)
    
    def get_status(self):
        return {name: servo.current_angle for name, servo in self.servos.items()}

class TemperatureSensor:
    def __init__(self, pin=27):
        self.ds_pin = Pin(pin)
        try:
            self.ds_sensor = ds18x20.DS18X20(onewire.OneWire(self.ds_pin))
            self.roms = self.ds_sensor.scan()
            if len(self.roms) > 0:
                print(f"✓ Temperature Sensor on GP{pin}")
                print(f"  Found DS18B20 devices: {len(self.roms)}")
                for i, rom in enumerate(self.roms):
                    print(f"  Device {i+1}: {rom}")
            else:
                print(f"⚠ No DS18B20 found on GP{pin}")
                print(f"  Check wiring: Data->GP{pin}, VCC->3.3V, GND->GND")
                print(f"  Don't forget 4.7kΩ pull-up resistor between Data and VCC")
        except Exception as e:
            print(f"✗ DS18B20 initialization error on GP{pin}: {e}")
            self.roms = []
    
    def read(self):
        if len(self.roms) == 0:
            return None
        try:
            self.ds_sensor.convert_temp()
            time.sleep_ms(750)
            temp_c = self.ds_sensor.read_temp(self.roms[0])
            return round(temp_c, 2)
        except Exception as e:
            print(f"Temperature read error: {e}")
            return None

class UltrasonicSensor:
    def __init__(self, trigger_pin=20, echo_pin=19):
        self.trigger = Pin(trigger_pin, Pin.OUT)
        self.echo = Pin(echo_pin, Pin.IN)
        self.trigger.low()
        print(f"✓ Ultrasonic on GP{trigger_pin}/{echo_pin}")
    
    def measure(self, timeout=30000):
        self.trigger.low()
        time.sleep_us(2)
        self.trigger.high()
        time.sleep_us(10)
        self.trigger.low()
        
        pulse_start = time.ticks_us()
        while self.echo.value() == 0:
            if time.ticks_diff(time.ticks_us(), pulse_start) > timeout:
                return -1
            pulse_start = time.ticks_us()
        
        pulse_end = time.ticks_us()
        while self.echo.value() == 1:
            if time.ticks_diff(time.ticks_us(), pulse_start) > timeout:
                return -1
            pulse_end = time.ticks_us()
        
        pulse_duration = time.ticks_diff(pulse_end, pulse_start)
        distance = (pulse_duration * 0.0343) / 2
        return round(distance, 2) if distance > 0 else -1
    
    def read(self, samples=3):
        readings = []
        for _ in range(samples):
            dist = self.measure()
            if dist > 0:
                readings.append(dist)
            time.sleep_ms(60)
        
        if len(readings) == 0:
            return -1
        return round(sum(readings) / len(readings), 2)

# ==================== WIFI CONNECTION ====================

def connect_wifi(ssid, password, timeout=15):
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    
    if wlan.isconnected():
        print(f'✓ WiFi already connected: {wlan.ifconfig()[0]}')
        return wlan
    
    print(f'Connecting to WiFi: {ssid}...')
    wlan.connect(ssid, password)
    
    start = time.time()
    while not wlan.isconnected():
        if time.time() - start > timeout:
            raise RuntimeError('WiFi connection timeout')
        time.sleep(1)
        print('.', end='')
    
    print(f'\n✓ WiFi connected: {wlan.ifconfig()[0]}')
    return wlan

# ==================== MAIN APPLICATION ====================

class MechController:
    def __init__(self):
        print("\n" + "=" * 70)
        print("  MECH CONTROLLER - WebSocket Mode")
        print("=" * 70 + "\n")
        
        # Initialize sensors
        try:
            self.gas = GasSensor(adc_pin=26, alert_pin=18)
        except Exception as e:
            print(f"⚠ Gas sensor error: {e}")
            self.gas = None
        
        try:
            self.servos = ServoController(pins=[10, 11, 12, 14, 15])
        except Exception as e:
            print(f"⚠ Servo error: {e}")
            self.servos = None
        
        try:
            self.temp = TemperatureSensor(pin=27)
        except Exception as e:
            print(f"⚠ Temp sensor error: {e}")
            self.temp = None
        
        try:
            self.ultrasonic = UltrasonicSensor(trigger_pin=20, echo_pin=19)
        except Exception as e:
            print(f"⚠ Ultrasonic error: {e}")
            self.ultrasonic = None
        
        print("\n" + "=" * 70)
        print("  INITIALIZATION COMPLETE")
        print("=" * 70 + "\n")
        
        # Emergency lock state
        self.emergency_lock = False
    
    def get_sensor_data(self):
        """Collect all sensor readings"""
        data = {}
        
        if self.gas:
            try:
                data['gas'] = self.gas.read()
            except:
                pass
        
        if self.temp:
            try:
                temp = self.temp.read()
                if temp is not None:
                    data['temperature_c'] = temp
            except:
                pass
        
        if self.ultrasonic:
            try:
                dist = self.ultrasonic.read()
                if dist > 0:
                    data['distance_cm'] = dist
            except:
                pass
        
        if self.servos:
            try:
                data['servos'] = self.servos.get_status()
            except:
                pass
        
        return data
    
    def handle_message(self, msg):
        """Handle incoming WebSocket messages"""
        try:
            data = json.loads(msg)
            msg_type = data.get('type')
            
            # REMOVED: flex_data handler - flex sensors should NOT control mech hand
            # Only control_servo, control_servos, and emergency_alert are handled
            
            if msg_type == 'control_servo':
                emergency_override = data.get('emergencyOverride', False)
                if self.emergency_lock and not emergency_override:
                    print("⚠ Manual servo control blocked - Emergency active")
                    return
                
                servo = data.get('servo')
                angle = data.get('angle')
                if servo and angle is not None and self.servos:
                    self.servos.set_angle(servo, int(angle))
                    override_msg = " [OVERRIDE]" if emergency_override else ""
                    print(f"Manual control: {servo} → {angle}°{override_msg}")
            
            elif msg_type == 'control_servos':
                emergency_override = data.get('emergencyOverride', False)
                if self.emergency_lock and not emergency_override:
                    print("⚠ Bulk servo control blocked - Emergency active")
                    return
                
                servos = data.get('servos')
                if servos and self.servos:
                    # Convert angles to integers and apply all at once
                    servo_angles = {k: int(v) for k, v in servos.items()}
                    self.servos.set_angles_from_dict(servo_angles)
                    override_msg = " [OVERRIDE]" if emergency_override else ""
                    print(f"Bulk control: {servo_angles}{override_msg}")
            
            elif msg_type == 'emergency_alert':
                active = data.get('active')
                
                if active:
                    # Emergency activated - stop all servos
                    self.emergency_lock = True
                    if self.servos:
                        self.servos.set_all_angles(90)
                    print("\n" + "="*50)
                    print("🚨 EMERGENCY ACTIVATED - SERVOS LOCKED AT 90° 🚨")
                    print("="*50 + "\n")
                else:
                    # Emergency deactivated - resume normal operation
                    self.emergency_lock = False
                    print("\n" + "="*50)
                    print("✓ EMERGENCY CLEARED - SERVOS UNLOCKED")
                    print("="*50 + "\n")
        
        except Exception as e:
            print(f"Message handler error: {e}")
    
    def run(self):
        """Main WebSocket loop"""
        # Connect to WiFi
        try:
            connect_wifi(WIFI_SSID, WIFI_PASSWORD)
        except Exception as e:
            print(f"WiFi error: {e}")
            return
        
        # Connect to WebSocket server
        ws_url = f"ws://{WS_SERVER}:{WS_PORT}"
        print(f"\nConnecting to {ws_url}...")
        
        while True:
            try:
                ws = WebSocketClient(ws_url).connect()
                print("✓ WebSocket connected\n")
                
                # Identify as mech client
                ws.send(json.dumps({'type': 'identify', 'client': 'mech'}))
                print("→ Identified as 'mech'\n")
                
                # Main loop
                last_send = 0
                
                while True:
                    current_time = time.time()
                    
                    # Send sensor data every second
                    if current_time - last_send >= 1.0:
                        try:
                            sensor_data = self.get_sensor_data()
                            ws.send(json.dumps({
                                'type': 'sensor_data',
                                'payload': sensor_data
                            }))
                            print(f"→ {sensor_data}")
                            last_send = current_time
                        except Exception as e:
                            print(f"Send error: {e}")
                            raise
                    
                    # Check for incoming messages
                    try:
                        msg = ws.recv()
                        if msg:
                            print(f"← {msg}")
                            self.handle_message(msg)
                    except Exception as e:
                        # No message or error - just continue
                        pass
                    
                    time.sleep(0.01)  # 10ms delay for faster servo response
            
            except KeyboardInterrupt:
                print("\n\nShutting down...")
                if self.servos:
                    self.servos.set_all_angles(90)
                if self.gas:
                    self.gas.alert_led.value(0)
                ws.close()
                break
            
            except Exception as e:
                print(f"\nWebSocket error: {e}")
                print("Reconnecting in 5 seconds...")
                try:
                    ws.close()
                except:
                    pass
                time.sleep(5)

# ==================== ENTRY POINT ====================

if __name__ == "__main__":
    controller = MechController()
    controller.run()
