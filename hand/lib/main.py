# Integrated Hand System for Raspberry Pi Pico WH
# Combines: Flex Sensor, Emergency Alert, and MAX30102 Heart Rate Sensor
# Command-line interface for all functions
#
# Hardware Configuration:
# - Flex Sensor: GP27 (ADC1)
# - Emergency Button: GP18, Alert LED: GP17
# - MAX30102: SDA=GP6, SCL=GP7

from machine import Pin, ADC, SoftI2C
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
# MAX30102 Heart Rate Sensor Driver
# =========================
class MAX30102:
    """Driver for MAX30102 heart rate and SpO2 sensor"""
    
    ADDRESS = 0x57
    REG_INTR_STATUS_1 = 0x00
    REG_FIFO_WR_PTR = 0x04
    REG_FIFO_RD_PTR = 0x06
    REG_FIFO_DATA = 0x07
    REG_FIFO_CONFIG = 0x08
    REG_MODE_CONFIG = 0x09
    REG_SPO2_CONFIG = 0x0A
    REG_LED1_PA = 0x0C
    REG_LED2_PA = 0x0D
    REG_TEMP_INTR = 0x1F
    REG_TEMP_FRAC = 0x20
    REG_TEMP_CONFIG = 0x21
    REG_PART_ID = 0xFF
    
    def __init__(self, i2c, address=ADDRESS):
        self.i2c = i2c
        self.address = address
        
        try:
            part_id = self.i2c.readfrom_mem(self.address, self.REG_PART_ID, 1)[0]
            if part_id == 0x15:
                print(f"✓ MAX30102 found at 0x{address:02x}")
            else:
                print(f"⚠ Unexpected part ID: 0x{part_id:02x}")
        except OSError:
            raise RuntimeError(f"✗ MAX30102 not found at 0x{address:02x}")
        
        self.reset()
        self.setup()
    
    def reset(self):
        self.i2c.writeto_mem(self.address, self.REG_MODE_CONFIG, bytes([0x40]))
        time.sleep(0.1)
    
    def setup(self):
        # Reset FIFO pointers
        self.i2c.writeto_mem(self.address, self.REG_FIFO_WR_PTR, bytes([0x00]))
        self.i2c.writeto_mem(self.address, self.REG_FIFO_RD_PTR, bytes([0x00]))
        
        # FIFO config: Sample averaging = 4, rollover enabled
        self.i2c.writeto_mem(self.address, self.REG_FIFO_CONFIG, bytes([0x4F]))
        
        # Mode: SpO2 mode (Red + IR LEDs)
        self.i2c.writeto_mem(self.address, self.REG_MODE_CONFIG, bytes([0x03]))
        
        # SpO2 config: 100 Hz, 411μs pulse width, ADC range 4096
        self.i2c.writeto_mem(self.address, self.REG_SPO2_CONFIG, bytes([0x27]))
        
        # LED brightness
        self.i2c.writeto_mem(self.address, self.REG_LED1_PA, bytes([0x24]))  # Red
        self.i2c.writeto_mem(self.address, self.REG_LED2_PA, bytes([0x24]))  # IR
        
        print("✓ MAX30102 configured")
    
    def read_fifo(self):
        data = self.i2c.readfrom_mem(self.address, self.REG_FIFO_DATA, 6)
        red = (data[0] << 16 | data[1] << 8 | data[2]) & 0x03FFFF
        ir = (data[3] << 16 | data[4] << 8 | data[5]) & 0x03FFFF
        return red, ir
    
    def check_finger(self):
        red, ir = self.read_fifo()
        return red > 50000 and ir > 50000
    
    def estimate_heart_rate(self, duration=10):
        print(f"Measuring heart rate for {duration}s (place finger on sensor)...")
        
        samples = []
        start_time = time.time()
        
        while time.time() - start_time < duration:
            red, ir = self.read_fifo()
            if ir > 50000:
                samples.append(ir)
            time.sleep(0.01)
        
        if len(samples) < 100:
            return 0, "No finger detected"
        
        # Peak detection
        peaks = 0
        threshold = sum(samples) // len(samples)
        
        for i in range(1, len(samples) - 1):
            if samples[i] > threshold and samples[i] > samples[i-1] and samples[i] > samples[i+1]:
                peaks += 1
        
        bpm = int((peaks * 60) / duration)
        
        if 40 <= bpm <= 200:
            return bpm, "Valid"
        else:
            return bpm, "Check placement"
    
    def read_temperature(self):
        self.i2c.writeto_mem(self.address, self.REG_TEMP_CONFIG, bytes([0x01]))
        time.sleep(0.1)
        
        temp_int = self.i2c.readfrom_mem(self.address, self.REG_TEMP_INTR, 1)[0]
        temp_frac = self.i2c.readfrom_mem(self.address, self.REG_TEMP_FRAC, 1)[0]
        
        if temp_int > 127:
            temp_int -= 256
        
        return temp_int + (temp_frac * 0.0625)
    
    def print_reading(self):
        red, ir = self.read_fifo()
        finger = "YES" if red > 50000 and ir > 50000 else "NO"
        print(f"MAX30102: Red={red:6d}, IR={ir:6d}, Finger={finger}")

# =========================
# Emergency Alert System
# =========================
class EmergencyAlert:
    """Emergency Alert System with toggle button"""
    
    def __init__(self, button_pin=18, led_pin=17):
        self.button = Pin(button_pin, Pin.IN, Pin.PULL_UP)
        self.led = Pin(led_pin, Pin.OUT)
        self.led.off()
        
        self.alert_active = False
        self.last_button_state = 1
        self.last_press_time = 0
        self.debounce_delay = 200
        
        print(f"✓ Emergency Alert: Button=GP{button_pin}, LED=GP{led_pin}")
    
    def check_button_press(self):
        current_state = self.button.value()
        current_time = time.ticks_ms()
        
        if current_state == 0 and self.last_button_state == 1:
            if time.ticks_diff(current_time, self.last_press_time) > self.debounce_delay:
                self.last_press_time = current_time
                self.last_button_state = current_state
                return True
        
        self.last_button_state = current_state
        return False
    
    def toggle_alert(self):
        self.alert_active = not self.alert_active
        if self.alert_active:
            print("\n🚨 EMERGENCY ALERT ACTIVATED 🚨")
        else:
            print("\n✓ Alert deactivated")
            self.led.off()
    
    def update(self):
        """Check button and update LED (non-blocking)"""
        if self.check_button_press():
            self.toggle_alert()
        
        if self.alert_active:
            # Toggle LED
            self.led.value(not self.led.value())
    
    def cleanup(self):
        self.led.off()
        self.alert_active = False

# =========================
# Flex Sensor
# =========================
class FlexSensor:
    """Flex sensor with calibration"""
    
    def __init__(self, name, read_func, vcc=3.3):
        self.name = name
        self.read_func = read_func
        self.vcc = vcc
        
        self.min_value = 0
        self.max_value = 65535
        self.calibrated = False
    
    def read_raw(self):
        return self.read_func()
    
    def read_voltage(self):
        raw = self.read_raw()
        return (raw / 65535) * self.vcc
    
    def read_percentage(self):
        raw = self.read_raw()
        
        if self.calibrated:
            if self.max_value == self.min_value:
                return 0
            percentage = ((raw - self.min_value) / (self.max_value - self.min_value)) * 100
            return max(0, min(100, percentage))
        else:
            return (raw / 65535) * 100
    
    def read_angle(self, max_angle=90):
        percentage = self.read_percentage()
        return (percentage / 100) * max_angle
    
    def calibrate(self, samples=10):
        print(f"\nCalibrating {self.name}...")
        
        input(f"Straighten sensor and press Enter...")
        straight_readings = []
        for _ in range(samples):
            straight_readings.append(self.read_raw())
            time.sleep(0.1)
        self.min_value = sum(straight_readings) // len(straight_readings)
        print(f"  Straight: {self.min_value}")
        
        input(f"Bend sensor fully and press Enter...")
        bent_readings = []
        for _ in range(samples):
            bent_readings.append(self.read_raw())
            time.sleep(0.1)
        self.max_value = sum(bent_readings) // len(bent_readings)
        print(f"  Bent: {self.max_value}")
        
        self.calibrated = True
        print(f"✓ {self.name} calibrated!\n")
    
    def print_reading(self):
        raw = self.read_raw()
        voltage = self.read_voltage()
        percentage = self.read_percentage()
        angle = self.read_angle()
        
        status = "[CAL]" if self.calibrated else "[RAW]"
        print(f"{self.name} {status}: Raw={raw:5d} | V={voltage:.3f}V | " +
              f"Flex={percentage:5.1f}% | Angle={angle:5.1f}°")

# =========================
# Integrated System
# =========================
class IntegratedHandSystem:
    """Main system controller"""
    
    def __init__(self):
        print("\n" + "=" * 70)
        print("  INTEGRATED HAND SYSTEM - Raspberry Pi Pico WH")
        print("=" * 70 + "\n")
        
        # Initialize Flex Sensor
        print("Initializing Flex Sensor...")
        try:
            self.flex_adc = ADC(Pin(27))
            self.flex_sensor = FlexSensor("Flex", lambda: self.flex_adc.read_u16())
            print("✓ Flex sensor on GP27\n")
        except Exception as e:
            print(f"✗ Flex sensor error: {e}\n")
            self.flex_sensor = None
        
        # Initialize Emergency Alert
        print("Initializing Emergency Alert...")
        try:
            self.emergency = EmergencyAlert(button_pin=18, led_pin=17)
            print()
        except Exception as e:
            print(f"✗ Emergency alert error: {e}\n")
            self.emergency = None
        
        # Initialize MAX30102
        print("Initializing MAX30102...")
        try:
            self.i2c = SoftI2C(sda=Pin(6), scl=Pin(7), freq=400000)
            devices = self.i2c.scan()
            print(f"  I2C devices: {[hex(d) for d in devices]}")
            
            self.max30102 = MAX30102(self.i2c)
            print()
        except Exception as e:
            print(f"✗ MAX30102 error: {e}\n")
            self.max30102 = None
        
        print("=" * 70)
        print("SYSTEM READY")
        print("=" * 70 + "\n")

        # Start websocket client automatically if WIFI configured
        try:
            if WS_SSID != 'YOUR_SSID' and websocket is not None and _thread is not None:
                _thread.start_new_thread(self._start_ws_client, ())
                print("WebSocket client thread started...")
        except Exception as e:
            print(f"WebSocket thread error: {e}")
    
    def read_all_sensors(self):
        """Read all sensors"""
        print("\n" + "┌─ ALL SENSOR READINGS " + "─" * 46 + "┐")
        
        if self.flex_sensor:
            self.flex_sensor.print_reading()
        
        if self.emergency:
            alert_status = "ACTIVE" if self.emergency.alert_active else "Inactive"
            print(f"Emergency Alert: {alert_status}")
        
        if self.max30102:
            self.max30102.print_reading()
        
        print("└─" + "─" * 68 + "┘\n")
    
    def monitor_all(self, duration=10):
        """Monitor all sensors"""
        print(f"\nMonitoring for {duration} seconds (Ctrl+C to stop)...")
        start_time = time.time()
        
        try:
            while time.time() - start_time < duration:
                self.read_all_sensors()
                
                # Update emergency alert (non-blocking)
                if self.emergency:
                    self.emergency.update()
                
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nMonitoring stopped.\n")

    # ------------ WebSocket helpers ------------
    def get_flex_payload(self):
        payload = {}
        try:
            if self.flex_sensor:
                payload['raw'] = self.flex_sensor.read_raw()
                payload['voltage'] = self.flex_sensor.read_voltage()
                payload['percentage'] = self.flex_sensor.read_percentage()
                payload['angle'] = self.flex_sensor.read_angle()
        except Exception:
            pass
        return payload

    def get_max30102_payload(self):
        payload = {}
        try:
            if self.max30102:
                red, ir = self.max30102.read_fifo()
                payload['red'] = red
                payload['ir'] = ir
                payload['finger'] = (red > 50000 and ir > 50000)
        except Exception:
            pass
        return payload

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
            ws.send(json.dumps({'type': 'identify', 'client': 'hand'}))

            while True:
                # Send flex data
                flex_payload = self.get_flex_payload()
                if flex_payload:
                    ws.send(json.dumps({'type': 'flex_data', 'flex': flex_payload}))

                # Send emergency alert if active
                if self.emergency:
                    if self.emergency.alert_active:
                        ws.send(json.dumps({'type': 'emergency', 'active': True}))

                # Send MAX30102 data
                max_payload = self.get_max30102_payload()
                if max_payload:
                    ws.send(json.dumps({'type': 'max30102_data', 'max30102': max_payload}))

                # Try to receive messages (non-blocking)
                try:
                    msg = ws.recv()
                    if msg:
                        # Hand can receive commands if needed (not required by spec but for completeness)
                        pass
                except Exception:
                    # timeout or no data
                    pass

                time.sleep(1)

        except Exception as e:
            print(f"WS client error: {e}")
            return

# =========================
# Main Menu
# =========================
def main():
    """Main program with command-line menu"""
    
    system = IntegratedHandSystem()
    
    while True:
        print("\n" + "─" * 70)
        print("  INTEGRATED HAND SYSTEM MENU")
        print("─" * 70)
        print("  ALL SENSORS:")
        print("    1. Read all sensors")
        print("    2. Monitor all sensors")
        print("\n  FLEX SENSOR:")
        print("    3. Read flex sensor")
        print("    4. Calibrate flex sensor")
        print("    5. Monitor flex sensor")
        print("\n  EMERGENCY ALERT:")
        print("    6. Toggle alert manually")
        print("    7. Monitor alert (auto mode)")
        print("\n  MAX30102:")
        print("    8. Read heart rate sensor")
        print("    9. Measure heart rate (10s)")
        print("   10. Read temperature")
        print("\n  SYSTEM:")
        print("   11. System status")
        print("   12. Exit")
        print("─" * 70)
        
        try:
            choice = input("\nSelect option (1-12): ").strip()
            
            # All Sensors
            if choice == '1':
                system.read_all_sensors()
            
            elif choice == '2':
                duration = input("Duration in seconds (default 10): ").strip()
                duration = int(duration) if duration else 10
                system.monitor_all(duration)
            
            # Flex Sensor
            elif choice == '3':
                if system.flex_sensor:
                    system.flex_sensor.print_reading()
                else:
                    print("⚠ Flex sensor not available")
            
            elif choice == '4':
                if system.flex_sensor:
                    system.flex_sensor.calibrate()
                else:
                    print("⚠ Flex sensor not available")
            
            elif choice == '5':
                if system.flex_sensor:
                    print("\nMonitoring flex sensor (Ctrl+C to stop)...\n")
                    try:
                        while True:
                            system.flex_sensor.print_reading()
                            time.sleep(1)
                    except KeyboardInterrupt:
                        print("\nStopped.\n")
                else:
                    print("⚠ Flex sensor not available")
            
            # Emergency Alert
            elif choice == '6':
                if system.emergency:
                    system.emergency.toggle_alert()
                else:
                    print("⚠ Emergency alert not available")
            
            elif choice == '7':
                if system.emergency:
                    print("\nMonitoring emergency button (Ctrl+C to stop)...")
                    print("Press button to toggle alert\n")
                    try:
                        while True:
                            system.emergency.update()
                            time.sleep(0.1)
                    except KeyboardInterrupt:
                        print("\nStopped.\n")
                        system.emergency.cleanup()
                else:
                    print("⚠ Emergency alert not available")
            
            # MAX30102
            elif choice == '8':
                if system.max30102:
                    system.max30102.print_reading()
                else:
                    print("⚠ MAX30102 not available")
            
            elif choice == '9':
                if system.max30102:
                    bpm, status = system.max30102.estimate_heart_rate(10)
                    print(f"\nHeart Rate: {bpm} BPM")
                    print(f"Status: {status}\n")
                else:
                    print("⚠ MAX30102 not available")
            
            elif choice == '10':
                if system.max30102:
                    temp = system.max30102.read_temperature()
                    print(f"\nMAX30102 Temperature: {temp:.2f}°C\n")
                else:
                    print("⚠ MAX30102 not available")
            
            # System
            elif choice == '11':
                print("\n" + "=" * 70)
                print("  SYSTEM STATUS")
                print("=" * 70)
                print(f"  Flex Sensor (GP27): {'✓ Ready' if system.flex_sensor else '✗ Not Available'}")
                if system.flex_sensor:
                    print(f"    Calibrated: {'Yes' if system.flex_sensor.calibrated else 'No'}")
                print(f"  Emergency Alert (GP18/17): {'✓ Ready' if system.emergency else '✗ Not Available'}")
                if system.emergency:
                    print(f"    Alert Status: {'ACTIVE' if system.emergency.alert_active else 'Inactive'}")
                print(f"  MAX30102 (GP6/7): {'✓ Ready' if system.max30102 else '✗ Not Available'}")
                print("=" * 70 + "\n")
            
            elif choice == '12':
                print("\nShutting down...")
                if system.emergency:
                    system.emergency.cleanup()
                print("\n" + "=" * 70)
                print("  Goodbye!")
                print("=" * 70 + "\n")
                break
            
            else:
                print("⚠ Invalid option. Please select 1-12.")
        
        except KeyboardInterrupt:
            print("\n\nShutting down...")
            if system.emergency:
                system.emergency.cleanup()
            print("\n" + "=" * 70)
            print("  Goodbye!")
            print("=" * 70 + "\n")
            break
        except Exception as e:
            print(f"\n⚠ Error: {e}")

if __name__ == "__main__":
    main()
