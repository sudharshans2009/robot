"""
============================================================================
MECH CONTROLLER - Refactored Version
============================================================================

Role: Mechanical arm controller that receives servo commands and sends sensor data.

Message Types Sent:
- mech.sensor_data: { servos: [...], gas: {...}, temperature, distance }

Message Types Received:
- server.control_servo: { servo_id, angle }
- server.control_servos: { angles: [...] }
- emergency.status: { active, level }

Configuration:
- WIFI_SSID, WIFI_PASSWORD: WiFi credentials
- SERVER_IP, SERVER_PORT: WebSocket server address
- Servo pin assignments

Migration Notes:
- New protocol uses dot notation
- Servos lock to 90° during emergency
- To switch: rename to main.py after testing
============================================================================
"""

from machine import Pin, PWM, ADC
import network
import usocket
import ujson
import ubinascii
import urandom
import ustruct
import time
from utime import ticks_ms, ticks_diff, sleep_ms, ticks_us, sleep_us
import time
import onewire
import ds18x20

# ============================================================================
# CONFIGURATION
# ============================================================================

# WiFi
# WiFi
WIFI_SSID = "Karthikeyan G"
WIFI_PASSWORD = "9842969931"

# Server
SERVER_IP = "10.40.94.143"
SERVER_PORT = 8081

# Pin Assignments
SERVO_PINS = [10, 11, 12, 14, 15]  # GP10, GP11, GP12, GP14, GP15
GAS_PIN = 26
TEMP_PIN = 27
ULTRASONIC_TRIG = 20
ULTRASONIC_ECHO = 19

# Servo Settings
SERVO_FREQ = 50
SERVO_MIN_DUTY = 1640    # 0 degrees
SERVO_MAX_DUTY = 8190    # 180 degrees
SERVO_DEFAULT = 90

# Timing
SENSOR_SEND_INTERVAL_MS = 1000
RECONNECT_INTERVAL_MS = 5000

# ============================================================================
# GAS SENSOR
# ============================================================================

class GasSensor:
    def __init__(self, pin):
        self.adc = ADC(Pin(pin))
        self.threshold_ppm = 150
        # Calibration: read baseline on startup
        self.baseline = self.adc.read_u16()
        print(f"  Gas baseline: {self.baseline}")
        
    def read_raw(self):
        """Read raw ADC value"""
        return self.adc.read_u16()
    
    def read_ppm(self):
        """Read as approximated PPM"""
        raw = self.read_raw()
        
        # Calculate percentage based on raw value
        # Most MQ sensors: low ADC = clean air, high ADC = gas detected
        # Scale 0-65535 to 0-100%
        percent = (raw / 65535.0) * 100
        
        # If baseline is high (>50000), sensor outputs HIGH in clean air
        # So we need to invert
        if self.baseline > 50000:
            percent = 100.0 - percent
        
        # Clamp to 0-100
        percent = max(0, min(100, percent))
        
        # Convert to PPM (rough approximation)
        ppm = int(percent * 100)  # 0-10000 PPM range
        return ppm, percent
    
    def read(self):
        """Read comprehensive gas data"""
        raw = self.read_raw()
        ppm, percent = self.read_ppm()
        
        return {
            'ppm': ppm,
            'percent': percent,
            'raw': raw,
            'isHigh': percent > 30  # Alert if > 30%
        }

# ============================================================================
# TEMPERATURE SENSOR
# ============================================================================

class TemperatureSensor:
    def __init__(self, pin):
        self.pin = Pin(pin)
        self.ow = None
        self.ds = None
        self.rom = None
        self.last_reading = 25.0
        
        self._init_sensor()
    
    def _init_sensor(self):
        """Initialize OneWire and DS18B20"""
        try:
            self.ow = onewire.OneWire(self.pin)
            self.ds = ds18x20.DS18X20(self.ow)
            
            roms = self.ds.scan()
            if roms:
                self.rom = roms[0]
                print(f"✓ DS18B20 found")
            else:
                print("⚠ No DS18B20 found")
        except Exception as e:
            print(f"✗ DS18B20 error: {e}")
    
    def read(self):
        """Read temperature in Celsius"""
        if not self.ds or not self.rom:
            return self.last_reading
        
        try:
            self.ds.convert_temp()
            sleep_ms(100)
            temp = self.ds.read_temp(self.rom)
            
            if -40 <= temp <= 85:
                self.last_reading = temp
                return round(temp, 1)
            return self.last_reading
        except Exception:
            return self.last_reading

# ============================================================================
# ULTRASONIC SENSOR
# ============================================================================

class UltrasonicSensor:
    def __init__(self, trig_pin, echo_pin):
        self.trigger = Pin(trig_pin, Pin.OUT)
        self.echo = Pin(echo_pin, Pin.IN)
        self.trigger.off()
        self.last_reading = 100.0
    
    def read(self):
        """Read distance in centimeters"""
        try:
            # Ensure trigger is low
            self.trigger.off()
            sleep_us(2)
            
            # Send 10us pulse
            self.trigger.on()
            sleep_us(10)
            self.trigger.off()
            
            # Wait for echo to go high (timeout 30ms = 30000us)
            timeout_start = ticks_us()
            while self.echo.value() == 0:
                if ticks_diff(ticks_us(), timeout_start) > 30000:
                    return self.last_reading
            pulse_start = ticks_us()
            
            # Wait for echo to go low
            while self.echo.value() == 1:
                if ticks_diff(ticks_us(), pulse_start) > 30000:
                    return self.last_reading
            pulse_end = ticks_us()
            
            # Calculate distance
            # Speed of sound = 343 m/s = 0.0343 cm/us
            # Distance = (time * speed) / 2 (round trip)
            duration = ticks_diff(pulse_end, pulse_start)
            distance = (duration * 0.0343) / 2
            
            if 2 <= distance <= 400:
                self.last_reading = round(distance, 1)
                return self.last_reading
            
            return self.last_reading
        except Exception:
            return self.last_reading

# ============================================================================
# SERVO CONTROLLER
# ============================================================================

class Servo:
    def __init__(self, pin, servo_id):
        self.pwm = PWM(Pin(pin))
        self.pwm.freq(SERVO_FREQ)
        self.id = servo_id
        self.angle = SERVO_DEFAULT
        self.set_angle(SERVO_DEFAULT)
    
    def set_angle(self, angle):
        """Set servo angle (0-180)"""
        angle = max(0, min(180, angle))
        duty = int(SERVO_MIN_DUTY + (angle / 180) * (SERVO_MAX_DUTY - SERVO_MIN_DUTY))
        self.pwm.duty_u16(duty)
        self.angle = angle
    
    def get_angle(self):
        """Get current angle"""
        return self.angle
    
    def stop(self):
        """Stop PWM"""
        self.pwm.deinit()


class ServoController:
    def __init__(self, pins):
        self.servos = []
        for i, pin in enumerate(pins):
            try:
                servo = Servo(pin, i + 1)
                self.servos.append(servo)
                print(f"  ✓ Servo {i+1} on GP{pin}")
            except Exception as e:
                print(f"  ✗ Servo {i+1} error: {e}")
        
        self.locked = False
        self.lock_angle = 90
    
    def set_angle(self, servo_id, angle):
        """Set individual servo angle"""
        if self.locked:
            return False
        
        if 1 <= servo_id <= len(self.servos):
            self.servos[servo_id - 1].set_angle(angle)
            return True
        return False
    
    def set_all(self, angles):
        """Set all servo angles from list"""
        if self.locked:
            return False
        
        for i, angle in enumerate(angles):
            if i < len(self.servos):
                self.servos[i].set_angle(angle)
        return True
    
    def lock(self, angle=90):
        """Lock all servos to angle"""
        self.lock_angle = angle
        self.locked = True
        for servo in self.servos:
            servo.set_angle(angle)
        print(f"🔒 Servos locked at {angle}°")
    
    def unlock(self):
        """Unlock servos"""
        self.locked = False
        print("🔓 Servos unlocked")
    
    def get_angles(self):
        """Get all angles"""
        return [s.get_angle() for s in self.servos]
    
    def stop_all(self):
        """Stop all servos"""
        for servo in self.servos:
            try:
                servo.stop()
            except:
                pass

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
        """Connect and handshake"""
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
                if e.args[0] == 115:
                    sleep_ms(100)
                else:
                    raise
            
            # Handshake
            key = ubinascii.b2a_base64(bytes(urandom.getrandbits(8) for _ in range(16)))[:-1]
            handshake = (
                f"GET / HTTP/1.1\r\n"
                f"Host: {self.host}:{self.port}\r\n"
                f"Upgrade: websocket\r\n"
                f"Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key.decode()}\r\n"
                f"Sec-WebSocket-Version: 13\r\n"
                f"\r\n"
            )
            
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
            frame.append(0x81)
            
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
                if e.args[0] in (11, 35, 115):
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
            
            if opcode == 0x1:
                return payload.decode() if payload else None
            elif opcode == 0x8:
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
    """Create message with standard envelope"""
    return {
        'type': msg_type,
        'source': 'mech',
        'target': 'server',
        'payload': payload,
        'timestamp': time.time()
    }

# ============================================================================
# MAIN CONTROLLER
# ============================================================================

class MechController:
    def __init__(self):
        print("\n" + "=" * 50)
        print("  MECH CONTROLLER (Refactored)")
        print("=" * 50 + "\n")
        
        # Initialize servos
        print("Initializing servos...")
        self.servo_ctrl = ServoController(SERVO_PINS)
        
        # Initialize gas sensor
        print("Initializing gas sensor...")
        try:
            self.gas = GasSensor(GAS_PIN)
            print("✓ Gas sensor ready")
        except Exception as e:
            print(f"✗ Gas sensor error: {e}")
            self.gas = None
        
        # Initialize temperature sensor
        print("Initializing temperature sensor...")
        try:
            self.temp = TemperatureSensor(TEMP_PIN)
        except Exception as e:
            print(f"✗ Temperature error: {e}")
            self.temp = None
        
        # Initialize ultrasonic sensor
        print("Initializing ultrasonic sensor...")
        try:
            self.ultrasonic = UltrasonicSensor(ULTRASONIC_TRIG, ULTRASONIC_ECHO)
            print("✓ Ultrasonic sensor ready")
        except Exception as e:
            print(f"✗ Ultrasonic error: {e}")
            self.ultrasonic = None
        
        # Network
        self.wlan = network.WLAN(network.STA_IF)
        self.ws = None
        
        # State
        self.emergency_active = False
        
        # Timing
        self.last_sensor_send = 0
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
                self.ws.send({'type': 'identify', 'client': 'mech'})
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
            
            # Handle servo control (single)
            if msg_type in ('server.control_servo', 'control_servo', 'servo_control'):
                if not self.emergency_active:
                    # Get servo identifier (handle both 'servo_id', 'id', and 'servo')
                    servo_id = payload.get('servo_id', payload.get('id'))
                    if servo_id is None:
                        servo = payload.get('servo')
                        if servo:
                            # Handle string format like 'servo_1' -> 1
                            if isinstance(servo, str) and servo.startswith('servo_'):
                                try:
                                    servo_id = int(servo.split('_')[1])
                                except:
                                    servo_id = 0
                            else:
                                servo_id = 0
                        else:
                            servo_id = 0
                    
                    angle = payload.get('angle', 90)
                    
                    if self.servo_ctrl.set_angle(servo_id, angle):
                        print(f"  Servo {servo_id} → {angle}°")
                    else:
                        print(f"  Failed: Servo {servo_id} → {angle}°")
            
            # Handle servos control (bulk)
            elif msg_type in ('server.control_servos', 'control_servos', 'flex_data', 'hand.flex_data'):
                if not self.emergency_active:
                    angles = payload.get('angles', [])
                    
                    # Handle servos object format: {servo_1: 90, servo_2: 90, ...}
                    if not angles:
                        servos_dict = payload.get('servos', {})
                        if servos_dict and isinstance(servos_dict, dict):
                            # Convert dict to list of angles
                            angles = []
                            for i in range(1, 6):  # servo_1 to servo_5
                                servo_key = f'servo_{i}'
                                if servo_key in servos_dict:
                                    angles.append(servos_dict[servo_key])
                                else:
                                    # Keep current angle if not specified
                                    angles.append(self.servo_ctrl.servos[i-1].get_angle() if i <= len(self.servo_ctrl.servos) else 90)
                    
                    # Handle flex_data conversion (legacy support)
                    if not angles and ('flex_1_2' in payload or 'flex1_2' in payload):
                        flex_1_2 = payload.get('flex_1_2', payload.get('flex1_2', 50))
                        flex_3_4 = payload.get('flex_3_4', payload.get('flex3_4', 50))
                        flex_5 = payload.get('flex_5', 50)
                        
                        # Convert flex percentage to servo angle
                        angle_1_2 = int((flex_1_2 / 100) * 180)
                        angle_3_4 = int((flex_3_4 / 100) * 180)
                        angle_5 = int((flex_5 / 100) * 180)
                        
                        angles = [angle_1_2, angle_1_2, angle_3_4, angle_3_4, angle_5]
                    
                    if angles:
                        self.servo_ctrl.set_all(angles)
                        print(f"  Servos → {angles}")
            
            # Handle emergency
            elif msg_type in ('emergency.status', 'emergency_status'):
                active = payload.get('active', False)
                
                if active and not self.emergency_active:
                    print("\n🚨 EMERGENCY - LOCKING SERVOS")
                    self.emergency_active = True
                    self.servo_ctrl.lock(90)
                elif not active and self.emergency_active:
                    print("✓ Emergency cleared - UNLOCKING")
                    self.emergency_active = False
                    self.servo_ctrl.unlock()
                    
        except Exception as e:
            print(f"⚠ Message parse error: {e}")
    
    def send_sensor_data(self):
        """Send all sensor data"""
        if not self.ws or not self.ws.connected:
            return
        
        # Collect data
        payload = {
            'servos': self.servo_ctrl.get_angles()
        }
        
        if self.gas:
            gas_data = self.gas.read()
            payload['gas'] = {
                'percent': round(gas_data['percent'], 1),
                'ppm': gas_data['ppm'],
                'raw': gas_data['raw'],
                'alert': gas_data['isHigh']
            }
        
        if self.temp:
            payload['temperature_c'] = self.temp.read()
        
        if self.ultrasonic:
            payload['distance_cm'] = self.ultrasonic.read()
        
        # Send
        msg = create_message('mech.sensor_data', payload)
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
        print("  • Receiving servo commands")
        print("  • Sensor data every 1s")
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
                
                # Send sensor data
                if ticks_diff(now, self.last_sensor_send) > SENSOR_SEND_INTERVAL_MS:
                    self.last_sensor_send = now
                    self.send_sensor_data()
                
                sleep_ms(10)
                
        except KeyboardInterrupt:
            print("\n\nShutting down...")
            if self.ws:
                self.ws.close()
            self.servo_ctrl.stop_all()
            print("✓ Stopped")

# ============================================================================
# ENTRY POINT
# ============================================================================

def main():
    controller = MechController()
    controller.run()

if __name__ == "__main__":
    main()
