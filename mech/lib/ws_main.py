# WebSocket-Only Mech Controller for Raspberry Pi Pico WH
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

# ==================== CONFIGURATION ====================
WIFI_SSID = "B's Galaxy F12"
WIFI_PASSWORD = "pyxf8869"
WS_SERVER = "10.239.13.143"
WS_PORT = 8080

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
    
    def set_angle(self, servo_name, angle):
        if servo_name in self.servos:
            return self.servos[servo_name].set_angle(angle)
        return None
    
    def set_all_angles(self, angle):
        for servo in self.servos.values():
            servo.set_angle(angle)
    
    def get_status(self):
        return {name: servo.current_angle for name, servo in self.servos.items()}

class TemperatureSensor:
    def __init__(self, pin=22):
        self.ds_pin = Pin(pin)
        self.ds_sensor = ds18x20.DS18X20(onewire.OneWire(self.ds_pin))
        self.roms = self.ds_sensor.scan()
        if len(self.roms) > 0:
            print(f"✓ Temperature Sensor on GP{pin}")
        else:
            print(f"⚠ No DS18B20 found on GP{pin}")
    
    def read(self):
        if len(self.roms) == 0:
            return None
        self.ds_sensor.convert_temp()
        time.sleep_ms(750)
        temp_c = self.ds_sensor.read_temp(self.roms[0])
        return round(temp_c, 2)

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
            self.temp = TemperatureSensor(pin=22)
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
    
    def handle_flex_command(self, flex_data):
        """Handle flex sensor input to control servos"""
        if not self.servos:
            return
        
        try:
            # flex_data contains: {raw, voltage, percentage, angle}
            angle = flex_data.get('angle', 90)
            
            # Map flex angle to servo angles
            # You can customize this mapping as needed
            servo_angle = max(0, min(180, int(angle)))
            
            # Control all servos or specific ones based on flex percentage
            percentage = flex_data.get('percentage', 0)
            
            if percentage < 20:
                # Fully open
                self.servos.set_all_angles(0)
            elif percentage > 80:
                # Fully closed
                self.servos.set_all_angles(180)
            else:
                # Proportional control
                self.servos.set_all_angles(servo_angle)
            
            print(f"Flex: {percentage:.1f}% → Servos: {servo_angle}°")
        except Exception as e:
            print(f"Flex command error: {e}")
    
    def handle_message(self, msg):
        """Handle incoming WebSocket messages"""
        try:
            data = json.loads(msg)
            msg_type = data.get('type')
            
            if msg_type == 'flex_data':
                flex = data.get('flex', {})
                self.handle_flex_command(flex)
            
            elif msg_type == 'control_servo':
                servo = data.get('servo')
                angle = data.get('angle')
                if servo and angle is not None and self.servos:
                    self.servos.set_angle(servo, int(angle))
                    print(f"Manual control: {servo} → {angle}°")
            
            elif msg_type == 'emergency_alert':
                active = data.get('active')
                if active and self.servos:
                    self.servos.set_all_angles(90)
                    print("EMERGENCY: Servos centered")
        
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
                ws = ws_connect(ws_url)
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
                        sensor_data = self.get_sensor_data()
                        ws.send(json.dumps({
                            'type': 'sensor_data',
                            'payload': sensor_data
                        }))
                        print(f"→ Sent: {sensor_data}")
                        last_send = current_time
                    
                    # Check for incoming messages (non-blocking)
                    try:
                        # Set a short timeout for recv
                        msg = ws.recv()
                        if msg:
                            print(f"← Received: {msg}")
                            self.handle_message(msg)
                    except:
                        # No message available
                        pass
                    
                    time.sleep(0.1)
            
            except KeyboardInterrupt:
                print("\n\nShutting down...")
                if self.servos:
                    self.servos.set_all_angles(90)
                if self.gas:
                    self.gas.alert_led.value(0)
                break
            
            except Exception as e:
                print(f"\nWebSocket error: {e}")
                print("Reconnecting in 5 seconds...")
                time.sleep(5)

# ==================== ENTRY POINT ====================

if __name__ == "__main__":
    controller = MechController()
    controller.run()
