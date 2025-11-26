# Integrated Sensor & Actuator System for Raspberry Pi Pico WH
# Combines: Gas Sensor, Servos, Temperature Sensor, and Ultrasonic Sensor
# Command-line interface for all functions
#
# Hardware Configuration:
# - Gas Sensor (MQ6): GP26 (ADC), GP18 (Alert LED)
# - Servos (5x): GP10, GP11, GP12, GP14, GP15
# - Temperature (DS18B20): GP22 (OneWire)
# - Ultrasonic (HC-SR04): GP20 (Trigger), GP19 (Echo)

from machine import Pin, PWM, ADC
import onewire, ds18x20
import time

# Networking / WebSocket (MicroPython)
import network
import usocket as socket
import ujson as json
try:
    import uwebsockets.client as websocket
except Exception:
    websocket = None
try:
    import _thread
except Exception:
    _thread = None

# --- Configuration: set your WiFi SSID/PASSWORD and WebSocket server host/port ---
WS_SSID = 'B\'s Galaxy F12'
WS_PASSWORD = 'pyxf8869'
# Point this to the machine running the Node.js server (replace with its LAN IP)
WS_SERVER = '10.239.13.143'
WS_PORT = 8080


def connect_wifi(ssid, password, timeout=15):
    wlan = network.WLAN(network.STA_IF)
    if not wlan.active():
        wlan.active(True)
    if wlan.isconnected():
        print('WiFi: already connected')
        return wlan

    print(f"WiFi: connecting to {ssid}...")
    wlan.connect(ssid, password)
    start = time.time()
    while not wlan.isconnected():
        if time.time() - start > timeout:
            raise RuntimeError('WiFi connection timeout')
        time.sleep(1)
    print('WiFi: connected, IP =', wlan.ifconfig()[0])
    return wlan

# =========================
# Gas Sensor (MQ6)
# =========================
class GasSensor:
    """MQ6 Gas Sensor with alert LED"""
    
    def __init__(self, adc_pin=26, alert_pin=18, threshold=30000):
        self.adc = ADC(Pin(adc_pin))
        self.alert_led = Pin(alert_pin, Pin.OUT)
        self.threshold = threshold
        self.alert_led.value(0)
        print(f"✓ Gas Sensor: GP{adc_pin}, Alert: GP{alert_pin}")
    
    def read_raw(self):
        return self.adc.read_u16()
    
    def read_percentage(self):
        return (self.read_raw() / 65535) * 100
    
    def is_gas_detected(self):
        return self.read_raw() > self.threshold
    
    def check_alert(self):
        if self.is_gas_detected():
            self.alert_led.value(1)
            return True
        else:
            self.alert_led.value(0)
            return False
    
    def print_reading(self):
        raw = self.read_raw()
        percentage = (raw / 65535) * 100
        alert = "⚠ ALERT!" if raw > self.threshold else "✓ Normal"
        print(f"Gas: {raw:5d} ({percentage:5.2f}%) - {alert}")
        return raw
    
    def set_threshold(self, threshold):
        if 0 <= threshold <= 65535:
            self.threshold = threshold
            print(f"✓ Threshold set to: {threshold}")
            return True
        return False
    
    def cleanup(self):
        self.alert_led.value(0)

# =========================
# Servo Controller
# =========================
class Servo:
    """Individual servo motor driver"""
    
    def __init__(self, pin, min_us=500, max_us=2500, freq=50):
        self.pwm = PWM(Pin(pin))
        self.pwm.freq(freq)
        self.min_us = min_us
        self.max_us = max_us
        self.current_angle = 90
        self.angle(90)
    
    def angle(self, degrees):
        degrees = max(0, min(180, degrees))
        pulse_width = self.min_us + (self.max_us - self.min_us) * degrees / 180
        duty = int(pulse_width * 65535 / 20000)
        self.pwm.duty_u16(duty)
        self.current_angle = degrees
    
    def get_angle(self):
        return self.current_angle
    
    def deinit(self):
        self.pwm.deinit()

class ServoController:
    """Control multiple servo motors"""
    
    def __init__(self, pins):
        self.servos = {}
        self.pins = pins
        for i, pin in enumerate(pins):
            self.servos[f'servo_{i+1}'] = Servo(pin)
        print(f"✓ Servos: {len(self.servos)} initialized on {pins}")
    
    def set_angle(self, servo_name, angle):
        if servo_name in self.servos:
            self.servos[servo_name].angle(angle)
    
    def set_all_angles(self, angle):
        for servo in self.servos.values():
            servo.angle(angle)
    
    def set_angles(self, angles):
        if len(angles) != len(self.servos):
            print(f"Error: Need {len(self.servos)} angles, got {len(angles)}")
            return
        for i, (name, servo) in enumerate(self.servos.items()):
            servo.angle(angles[i])
    
    def get_angles(self):
        return {name: servo.get_angle() for name, servo in self.servos.items()}
    
    def sweep_all(self, start=0, end=180, step=5, delay=0.05):
        if start < end:
            for angle in range(start, end + 1, step):
                for servo in self.servos.values():
                    servo.angle(angle)
                time.sleep(delay)
        else:
            for angle in range(start, end - 1, -step):
                for servo in self.servos.values():
                    servo.angle(angle)
                time.sleep(delay)

# =========================
# Temperature Sensor (DS18B20)
# =========================
class TemperatureSensor:
    """DS18B20 Temperature Sensor"""
    
    def __init__(self, pin=22):
        self.ds_pin = Pin(pin)
        self.ds_sensor = ds18x20.DS18X20(onewire.OneWire(self.ds_pin))
        self.roms = self.ds_sensor.scan()
        
        if len(self.roms) > 0:
            print(f"✓ Temperature Sensor: GP{pin}, Found {len(self.roms)} device(s)")
        else:
            print(f"⚠ Temperature Sensor: No DS18B20 found on GP{pin}")
    
    def read_temperature(self):
        """Read temperature in Celsius"""
        if len(self.roms) == 0:
            return None
        
        self.ds_sensor.convert_temp()
        time.sleep_ms(750)
        return self.ds_sensor.read_temp(self.roms[0])
    
    def read_all_temperatures(self):
        """Read all connected sensors"""
        if len(self.roms) == 0:
            return []
        
        self.ds_sensor.convert_temp()
        time.sleep_ms(750)
        temps = []
        for rom in self.roms:
            temps.append(self.ds_sensor.read_temp(rom))
        return temps
    
    def print_reading(self):
        """Print temperature reading"""
        temp_c = self.read_temperature()
        if temp_c is not None:
            temp_f = temp_c * (9/5) + 32
            print(f"Temperature: {temp_c:.2f}°C ({temp_f:.2f}°F)")
            return temp_c
        else:
            print("Temperature: No sensor found")
            return None

# =========================
# Ultrasonic Sensor (HC-SR04)
# =========================
class UltrasonicSensor:
    """HC-SR04 Ultrasonic Distance Sensor"""
    
    def __init__(self, trigger_pin=20, echo_pin=19):
        self.trigger = Pin(trigger_pin, Pin.OUT)
        self.echo = Pin(echo_pin, Pin.IN)
        self.trigger.low()
        print(f"✓ Ultrasonic: Trig=GP{trigger_pin}, Echo=GP{echo_pin}")
    
    def measure_distance(self, timeout=30000):
        """Measure distance in centimeters"""
        # Send pulse
        self.trigger.low()
        time.sleep_us(2)
        self.trigger.high()
        time.sleep_us(10)
        self.trigger.low()
        
        # Wait for echo
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
        
        # Calculate distance
        pulse_duration = time.ticks_diff(pulse_end, pulse_start)
        distance = (pulse_duration * 0.0343) / 2
        return distance
    
    def print_reading(self):
        """Print distance reading"""
        dist_cm = self.measure_distance()
        if dist_cm < 0:
            print("Distance: Timeout/Error")
            return None
        else:
            dist_in = dist_cm / 2.54
            print(f"Distance: {dist_cm:6.2f} cm ({dist_in:6.2f} in)")
            return dist_cm
    
    def get_multiple_readings(self, samples=5):
        """Get average of multiple readings"""
        readings = []
        for _ in range(samples):
            dist = self.measure_distance()
            if dist > 0:
                readings.append(dist)
            time.sleep_ms(60)
        
        if len(readings) == 0:
            return -1
        return sum(readings) / len(readings)

# =========================
# Integrated System Controller
# =========================
class IntegratedSystem:
    """Main system controller for all sensors and actuators"""
    
    def __init__(self):
        print("\n" + "=" * 70)
        print("  INTEGRATED SENSOR & ACTUATOR SYSTEM")
        print("=" * 70 + "\n")
        
        # Initialize all components
        try:
            self.gas_sensor = GasSensor(adc_pin=26, alert_pin=18)
        except Exception as e:
            print(f"⚠ Gas Sensor Error: {e}")
            self.gas_sensor = None
        
        try:
            self.servos = ServoController(pins=[10, 11, 12, 14, 15])
        except Exception as e:
            print(f"⚠ Servo Error: {e}")
            self.servos = None
        
        try:
            self.temp_sensor = TemperatureSensor(pin=22)
        except Exception as e:
            print(f"⚠ Temperature Sensor Error: {e}")
            self.temp_sensor = None
        
        try:
            self.ultrasonic = UltrasonicSensor(trigger_pin=20, echo_pin=19)
        except Exception as e:
            print(f"⚠ Ultrasonic Error: {e}")
            self.ultrasonic = None
        
        print("\n" + "=" * 70)
        print("  SYSTEM READY")
        print("=" * 70 + "\n")
        
        # Start websocket client automatically if WIFI configured
        try:
            if WS_SSID != 'YOUR_SSID' and websocket is not None and _thread is not None:
                _thread.start_new_thread(self._start_ws_client, ())
                print("WebSocket client thread started...")
        except Exception as e:
            print(f"WebSocket thread error: {e}")
    
    def read_all_sensors(self):
        """Read all sensors and display"""
        print("\n" + "┌─ ALL SENSOR READINGS " + "─" * 46 + "┐")
        
        if self.gas_sensor:
            self.gas_sensor.print_reading()
        
        if self.temp_sensor:
            self.temp_sensor.print_reading()
        
        if self.ultrasonic:
            self.ultrasonic.print_reading()
        
        if self.servos:
            angles = self.servos.get_angles()
            servo_str = ", ".join([f"S{i+1}:{a}°" for i, a in enumerate(angles.values())])
            print(f"Servos: {servo_str}")
        
        print("└─" + "─" * 68 + "┘\n")

    # ------------ WebSocket helpers ------------
    def get_sensor_payload(self):
        payload = {}
        try:
            if self.gas_sensor:
                payload['gas_raw'] = self.gas_sensor.read_raw()
                payload['gas_percent'] = self.gas_sensor.read_percentage()
        except Exception:
            pass

        try:
            if self.temp_sensor:
                payload['temperature_c'] = self.temp_sensor.read_temperature()
        except Exception:
            pass

        try:
            if self.ultrasonic:
                payload['distance_cm'] = self.ultrasonic.get_multiple_readings(3)
        except Exception:
            pass

        try:
            if self.servos:
                payload['servos'] = self.servos.get_angles()
        except Exception:
            pass

        return payload

    def handle_ws_message(self, msg):
        """Handle incoming websocket JSON messages."""
        try:
            m = json.loads(msg)
        except Exception:
            print("⚠ Received non-JSON WS message")
            return

        t = m.get('type')
        if t == 'control_servo':
            # Example payload: {type:'control_servo', servo:'servo_1', angle:45}
            servo = m.get('servo')
            angle = m.get('angle')
            if servo and angle is not None and self.servos:
                try:
                    self.servos.set_angle(servo, int(angle))
                    print(f"WS: Set {servo} -> {angle}°")
                except Exception as e:
                    print(f"WS servo error: {e}")

        elif t == 'emergency_alert':
            active = m.get('active')
            print(f"WS: Emergency alert received: {active}")
            # React: center servos as a simple safe position
            if active and self.servos:
                try:
                    self.servos.set_all_angles(90)
                except Exception:
                    pass

    def _start_ws_client(self):
        """Background WebSocket client thread."""
        try:
            connect_wifi(WS_SSID, WS_PASSWORD)
        except Exception as e:
            print(f"WS: WiFi error: {e}")
            return

        if websocket is None:
            print("WS: websocket client library not available")
            return

        url = 'ws://' + WS_SERVER + ':' + str(WS_PORT)
        print(f"WS: Connecting to {url} ...")

        try:
            ws = websocket.connect(url)
            # set socket timeout for non-blocking recv
            try:
                ws.sock.settimeout(0.5)
            except Exception:
                pass

            # Identify
            ws.send(json.dumps({'type': 'identify', 'client': 'mech'}))

            while True:
                # Send sensor payload
                payload = self.get_sensor_payload()
                ws.send(json.dumps({'type': 'sensor_data', 'payload': payload}))

                # Try to receive messages (non-blocking)
                try:
                    msg = ws.recv()
                    if msg:
                        self.handle_ws_message(msg)
                except Exception:
                    # timeout or no data
                    pass

                time.sleep(1)

        except Exception as e:
            print(f"WS client error: {e}")
            return
    
    def monitor_all(self, duration=10, interval=1):
        """Monitor all sensors continuously"""
        print(f"\nMonitoring all sensors for {duration} seconds...")
        print("Press Ctrl+C to stop\n")
        
        start_time = time.time()
        try:
            while time.time() - start_time < duration:
                self.read_all_sensors()
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\nMonitoring stopped.\n")

# =========================
# Main Menu Program
# =========================
def main():
    """Main program with command-line menu"""
    
    # Initialize system
    system = IntegratedSystem()
    
    while True:
        print("\n" + "─" * 70)
        print("  MAIN MENU")
        print("─" * 70)
        print("  SENSORS:")
        print("    1. Read All Sensors")
        print("    2. Monitor All Sensors")
        print("    3. Gas Sensor Menu")
        print("    4. Temperature Sensor Menu")
        print("    5. Ultrasonic Sensor Menu")
        print("\n  ACTUATORS:")
        print("    6. Servo Control Menu")
        print("\n  SYSTEM:")
        print("    7. System Status")
        print("    8. Exit")
        print("─" * 70)
        
        try:
            choice = input("\nSelect option (1-8): ").strip()
            
            # Read All Sensors
            if choice == '1':
                system.read_all_sensors()
            
            # Monitor All Sensors
            elif choice == '2':
                duration = input("Duration in seconds (default 10): ").strip()
                duration = int(duration) if duration else 10
                interval = input("Update interval in seconds (default 1): ").strip()
                interval = float(interval) if interval else 1
                system.monitor_all(duration, interval)
            
            # Gas Sensor Menu
            elif choice == '3':
                gas_menu(system.gas_sensor)
            
            # Temperature Sensor Menu
            elif choice == '4':
                temp_menu(system.temp_sensor)
            
            # Ultrasonic Sensor Menu
            elif choice == '5':
                ultrasonic_menu(system.ultrasonic)
            
            # Servo Control Menu
            elif choice == '6':
                servo_menu(system.servos)
            
            # System Status
            elif choice == '7':
                print("\n" + "=" * 70)
                print("  SYSTEM STATUS")
                print("=" * 70)
                print(f"  Gas Sensor: {'✓ Ready' if system.gas_sensor else '✗ Not Available'}")
                print(f"  Temperature: {'✓ Ready' if system.temp_sensor else '✗ Not Available'}")
                print(f"  Ultrasonic: {'✓ Ready' if system.ultrasonic else '✗ Not Available'}")
                print(f"  Servos: {'✓ Ready' if system.servos else '✗ Not Available'}")
                print("=" * 70 + "\n")
            
            # Exit
            elif choice == '8':
                print("\nShutting down system...")
                if system.gas_sensor:
                    system.gas_sensor.cleanup()
                if system.servos:
                    system.servos.set_all_angles(90)
                print("\n" + "=" * 70)
                print("  Goodbye!")
                print("=" * 70 + "\n")
                break
            
            else:
                print("⚠ Invalid option. Please select 1-8.")
        
        except KeyboardInterrupt:
            print("\n\nShutting down system...")
            if system.gas_sensor:
                system.gas_sensor.cleanup()
            if system.servos:
                system.servos.set_all_angles(90)
            print("\n" + "=" * 70)
            print("  Goodbye!")
            print("=" * 70 + "\n")
            break
        except Exception as e:
            print(f"\n⚠ Error: {e}")

# =========================
# Submenus
# =========================
def gas_menu(gas_sensor):
    """Gas sensor submenu"""
    if not gas_sensor:
        print("\n⚠ Gas sensor not available\n")
        return
    
    while True:
        print("\n  GAS SENSOR MENU")
        print("  ─" * 30)
        print("  1. Read gas level")
        print("  2. Monitor continuously")
        print("  3. Set threshold")
        print("  4. Back to main menu")
        
        choice = input("\n  Select (1-4): ").strip()
        
        if choice == '1':
            gas_sensor.print_reading()
        elif choice == '2':
            print("\n  Monitoring (Ctrl+C to stop)...\n")
            try:
                while True:
                    gas_sensor.print_reading()
                    gas_sensor.check_alert()
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\n  Monitoring stopped.\n")
        elif choice == '3':
            thresh = input("  Enter threshold (0-65535): ").strip()
            try:
                gas_sensor.set_threshold(int(thresh))
            except ValueError:
                print("  ⚠ Invalid threshold")
        elif choice == '4':
            break
        else:
            print("  ⚠ Invalid option")

def temp_menu(temp_sensor):
    """Temperature sensor submenu"""
    if not temp_sensor:
        print("\n⚠ Temperature sensor not available\n")
        return
    
    while True:
        print("\n  TEMPERATURE SENSOR MENU")
        print("  ─" * 30)
        print("  1. Read temperature")
        print("  2. Monitor continuously")
        print("  3. Back to main menu")
        
        choice = input("\n  Select (1-3): ").strip()
        
        if choice == '1':
            temp_sensor.print_reading()
        elif choice == '2':
            print("\n  Monitoring (Ctrl+C to stop)...\n")
            try:
                while True:
                    temp_sensor.print_reading()
                    time.sleep(2)
            except KeyboardInterrupt:
                print("\n  Monitoring stopped.\n")
        elif choice == '3':
            break
        else:
            print("  ⚠ Invalid option")

def ultrasonic_menu(ultrasonic):
    """Ultrasonic sensor submenu"""
    if not ultrasonic:
        print("\n⚠ Ultrasonic sensor not available\n")
        return
    
    while True:
        print("\n  ULTRASONIC SENSOR MENU")
        print("  ─" * 30)
        print("  1. Single measurement")
        print("  2. Monitor continuously")
        print("  3. Average of 5 readings")
        print("  4. Back to main menu")
        
        choice = input("\n  Select (1-4): ").strip()
        
        if choice == '1':
            ultrasonic.print_reading()
        elif choice == '2':
            print("\n  Monitoring (Ctrl+C to stop)...\n")
            try:
                while True:
                    ultrasonic.print_reading()
                    time.sleep(0.5)
            except KeyboardInterrupt:
                print("\n  Monitoring stopped.\n")
        elif choice == '3':
            print("\n  Taking 5 readings...")
            avg = ultrasonic.get_multiple_readings(5)
            if avg > 0:
                print(f"  Average: {avg:.2f} cm ({avg/2.54:.2f} in)\n")
            else:
                print("  ⚠ Error reading sensor\n")
        elif choice == '4':
            break
        else:
            print("  ⚠ Invalid option")

def servo_menu(servos):
    """Servo control submenu"""
    if not servos:
        print("\n⚠ Servos not available\n")
        return
    
    while True:
        print("\n  SERVO CONTROL MENU")
        print("  ─" * 30)
        print("  1. Set all servos")
        print("  2. Set individual servo")
        print("  3. Sweep all (0° → 180°)")
        print("  4. Sweep all (180° → 0°)")
        print("  5. Center all (90°)")
        print("  6. Show current angles")
        print("  7. Presets")
        print("  8. Back to main menu")
        
        choice = input("\n  Select (1-8): ").strip()
        
        if choice == '1':
            angle = input("  Angle (0-180): ").strip()
            try:
                servos.set_all_angles(int(angle))
                print(f"  ✓ All servos set to {angle}°")
            except ValueError:
                print("  ⚠ Invalid angle")
        
        elif choice == '2':
            servo_num = input("  Servo number (1-5): ").strip()
            angle = input("  Angle (0-180): ").strip()
            try:
                servos.set_angle(f'servo_{servo_num}', int(angle))
                print(f"  ✓ Servo {servo_num} set to {angle}°")
            except (ValueError, KeyError):
                print("  ⚠ Invalid input")
        
        elif choice == '3':
            print("\n  Sweeping 0° → 180°...")
            servos.sweep_all(0, 180, step=5, delay=0.03)
            print("  ✓ Complete\n")
        
        elif choice == '4':
            print("\n  Sweeping 180° → 0°...")
            servos.sweep_all(180, 0, step=5, delay=0.03)
            print("  ✓ Complete\n")
        
        elif choice == '5':
            servos.set_all_angles(90)
            print("  ✓ All centered at 90°")
        
        elif choice == '6':
            angles = servos.get_angles()
            print("\n  Current Angles:")
            for i, (name, angle) in enumerate(angles.items(), 1):
                print(f"    Servo {i}: {angle}°")
            print()
        
        elif choice == '7':
            print("\n  PRESETS:")
            print("    1. Extended (180°)")
            print("    2. Retracted (0°)")
            print("    3. Gradient (0°,45°,90°,135°,180°)")
            preset = input("\n  Select preset (1-3): ").strip()
            
            if preset == '1':
                servos.set_all_angles(180)
                print("  ✓ Extended")
            elif preset == '2':
                servos.set_all_angles(0)
                print("  ✓ Retracted")
            elif preset == '3':
                servos.set_angles([0, 45, 90, 135, 180])
                print("  ✓ Gradient set")
            else:
                print("  ⚠ Invalid preset")
        
        elif choice == '8':
            break
        else:
            print("  ⚠ Invalid option")

if __name__ == "__main__":
    main()
