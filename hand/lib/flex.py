# Flex Sensor System for Raspberry Pi Pico
# Hardware:
# - ADS1115/ADS1015 ADC: SDA=GP14, SCL=GP15 (4 flex sensors on A0-A3)
# - Direct Flex Sensor: GP26 (ADC0)

from machine import Pin, ADC, I2C
import time

# =========================
# ADS1115/ADS1015 ADC Driver
# =========================
class ADS1x15:
    """Driver for ADS1115/ADS1015 16-bit/12-bit ADC"""
    
    # I2C address
    ADDRESS = 0x48
    
    # Register addresses
    REG_CONVERSION = 0x00
    REG_CONFIG = 0x01
    
    # Configuration bits
    OS_SINGLE = 0x8000      # Single conversion
    MUX_AIN0 = 0x4000       # Channel A0
    MUX_AIN1 = 0x5000       # Channel A1
    MUX_AIN2 = 0x6000       # Channel A2
    MUX_AIN3 = 0x7000       # Channel A3
    PGA_4_096V = 0x0200     # +/- 4.096V range
    MODE_SINGLE = 0x0100    # Single-shot mode
    DR_128SPS = 0x0080      # 128 samples per second
    COMP_QUE_DIS = 0x0003   # Disable comparator
    
    def __init__(self, i2c, address=ADDRESS):
        """Initialize ADS1x15"""
        self.i2c = i2c
        self.address = address
        self.channels = [self.MUX_AIN0, self.MUX_AIN1, self.MUX_AIN2, self.MUX_AIN3]
        
        # Test connection
        try:
            self.i2c.readfrom(self.address, 1)
            print(f"✓ ADS1x15 found at address 0x{address:02x}")
        except OSError:
            raise RuntimeError(f"✗ ADS1x15 not found at address 0x{address:02x}")
    
    def read_channel(self, channel):
        """Read raw value from specified channel (0-3)"""
        if not (0 <= channel <= 3):
            raise ValueError("Channel must be 0-3")
        
        # Configure for single-shot read on specified channel
        config = (self.OS_SINGLE | 
                  self.channels[channel] |
                  self.PGA_4_096V |
                  self.MODE_SINGLE |
                  self.DR_128SPS |
                  self.COMP_QUE_DIS)
        
        # Write config register
        config_bytes = bytes([(config >> 8) & 0xFF, config & 0xFF])
        self.i2c.writeto_mem(self.address, self.REG_CONFIG, config_bytes)
        
        # Wait for conversion (at 128 SPS, ~8ms)
        time.sleep(0.01)
        
        # Read conversion result
        result = self.i2c.readfrom_mem(self.address, self.REG_CONVERSION, 2)
        value = (result[0] << 8) | result[1]
        
        # Convert to signed 16-bit
        if value > 32767:
            value -= 65536
        
        return value
    
    def read_voltage(self, channel):
        """Read voltage from specified channel"""
        raw = self.read_channel(channel)
        # With PGA at 4.096V, LSB = 0.125mV
        voltage = raw * 0.000125
        return voltage

# =========================
# Flex Sensor Class
# =========================
class FlexSensor:
    """Individual flex sensor with calibration"""
    
    def __init__(self, name, read_func, vcc=3.3):
        """
        Initialize flex sensor
        
        Args:
            name: Sensor name (e.g., "Thumb", "Index")
            read_func: Function to read raw ADC value
            vcc: Supply voltage
        """
        self.name = name
        self.read_func = read_func
        self.vcc = vcc
        
        # Calibration values
        self.min_value = 0      # Fully straight
        self.max_value = 65535  # Fully bent
        self.calibrated = False
    
    def read_raw(self):
        """Read raw ADC value"""
        return self.read_func()
    
    def read_voltage(self):
        """Read voltage value"""
        raw = self.read_raw()
        return (raw / 65535) * self.vcc
    
    def read_percentage(self):
        """Read flex as percentage (0-100%)"""
        raw = self.read_raw()
        
        if self.calibrated:
            if self.max_value == self.min_value:
                return 0
            percentage = ((raw - self.min_value) / (self.max_value - self.min_value)) * 100
            return max(0, min(100, percentage))
        else:
            return (raw / 65535) * 100
    
    def read_angle(self, max_angle=90):
        """
        Read flex sensor as angle (0 to max_angle degrees)
        0° = straight, max_angle° = fully bent
        """
        percentage = self.read_percentage()
        return (percentage / 100) * max_angle
    
    def calibrate(self, samples=10):
        """Calibrate sensor by reading straight and bent positions"""
        print(f"\nCalibrating {self.name}...")
        
        # Read straight position
        input(f"Straighten {self.name} and press Enter...")
        straight_readings = []
        for _ in range(samples):
            straight_readings.append(self.read_raw())
            time.sleep(0.1)
        self.min_value = sum(straight_readings) // len(straight_readings)
        print(f"  Straight: {self.min_value}")
        
        # Read bent position
        input(f"Bend {self.name} fully and press Enter...")
        bent_readings = []
        for _ in range(samples):
            bent_readings.append(self.read_raw())
            time.sleep(0.1)
        self.max_value = sum(bent_readings) // len(bent_readings)
        print(f"  Bent: {self.max_value}")
        
        self.calibrated = True
        print(f"✓ {self.name} calibrated!\n")
        
        return (self.min_value, self.max_value)
    
    def print_reading(self):
        """Print current sensor reading"""
        raw = self.read_raw()
        voltage = self.read_voltage()
        percentage = self.read_percentage()
        angle = self.read_angle()
        
        status = "[CAL]" if self.calibrated else "[RAW]"
        print(f"{self.name:10s} {status}: Raw={raw:5d} | V={voltage:.3f}V | " +
              f"Flex={percentage:5.1f}% | Angle={angle:5.1f}°")

# =========================
# Flex Sensor Manager
# =========================
class FlexSensorSystem:
    """Manage all flex sensors"""
    
    def __init__(self):
        """Initialize flex sensor system"""
        print("\n" + "=" * 60)
        print("  FLEX SENSOR SYSTEM - INITIALIZATION")
        print("=" * 60 + "\n")
        
        self.sensors = {}
        self.ads = None
        self.direct_adc = None
        
        # Initialize I2C for ADS1x15
        print("Initializing ADS1x15 (4 flex sensors)...")
        try:
            self.i2c = I2C(0, scl=Pin(15), sda=Pin(14), freq=400000)
            print(f"  I2C: SDA=GP14, SCL=GP15")
            
            # Scan I2C bus
            devices = self.i2c.scan()
            print(f"  I2C devices found: {[hex(d) for d in devices]}")
            
            # Initialize ADS1x15
            self.ads = ADS1x15(self.i2c)
            
            # Create sensor objects for ADS1x15 channels (A0-A3)
            sensor_names = ['Thumb', 'Index', 'Middle', 'Ring']
            for i, name in enumerate(sensor_names):
                self.sensors[name.lower()] = FlexSensor(
                    name,
                    lambda ch=i: self._read_ads_normalized(ch)
                )
            
            print(f"✓ 4 flex sensors initialized on ADS1x15\n")
        except Exception as e:
            print(f"✗ ADS1x15 initialization failed: {e}\n")
        
        # Initialize direct ADC on GP26
        print("Initializing Direct ADC flex sensor...")
        try:
            self.direct_adc = ADC(Pin(26))
            self.sensors['pinky'] = FlexSensor(
                "Pinky",
                lambda: self.direct_adc.read_u16()
            )
            print(f"✓ Direct flex sensor initialized on GP26\n")
        except Exception as e:
            print(f"✗ Direct ADC initialization failed: {e}\n")
        
        # Summary
        print("=" * 60)
        print(f"SYSTEM READY: {len(self.sensors)} flex sensors initialized")
        for name in self.sensors.keys():
            print(f"  - {name[0].upper() + name[1:] if name else name}")
        print("=" * 60 + "\n")
    
    def _read_ads_normalized(self, channel):
        """Read ADS1x15 channel and normalize to 0-65535 range"""
        if self.ads is None:
            return 0
        
        try:
            # ADS1115 returns -32768 to 32767
            raw = self.ads.read_channel(channel)
            # Normalize to 0-65535 range
            normalized = int((raw + 32768) * 2)
            return max(0, min(65535, normalized))
        except:
            return 0
    
    def read_all(self):
        """Read all sensors"""
        readings = {}
        for name, sensor in self.sensors.items():
            readings[name] = {
                'raw': sensor.read_raw(),
                'voltage': sensor.read_voltage(),
                'percentage': sensor.read_percentage(),
                'angle': sensor.read_angle()
            }
        return readings
    
    def print_all(self):
        """Print readings from all sensors"""
        if len(self.sensors) == 0:
            print("Error: No sensors available")
            return
        
        print("\n" + "┌─ FLEX SENSOR READINGS " + "─" * 35 + "┐")
        for sensor in self.sensors.values():
            sensor.print_reading()
        print("└─" + "─" * 59 + "┘\n")
    
    def calibrate_all(self):
        """Calibrate all sensors"""
        if len(self.sensors) == 0:
            print("Error: No sensors available")
            return
        
        print("\n" + "=" * 60)
        print("  FLEX SENSOR CALIBRATION")
        print("=" * 60)
        
        for sensor in self.sensors.values():
            sensor.calibrate()
        
        print("=" * 60)
        print("✓ All sensors calibrated!")
        print("=" * 60 + "\n")
    
    def monitor(self, duration=10, interval=1):
        """Monitor sensors for specified duration"""
        if len(self.sensors) == 0:
            print("Error: No sensors available")
            return
        
        print(f"\nMonitoring flex sensors for {duration} seconds...")
        print("Press Ctrl+C to stop\n")
        
        start_time = time.time()
        
        try:
            while time.time() - start_time < duration:
                self.print_all()
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\nMonitoring stopped.\n")
    
    def detect_gesture(self, threshold=50):
        """Detect hand gesture based on flex sensors"""
        if len(self.sensors) == 0:
            return "No sensors available"
        
        readings = self.read_all()
        
        bent = []
        straight = []
        
        for name, data in readings.items():
            if data['percentage'] > threshold:
                bent.append(name)
            else:
                straight.append(name)
        
        # Gesture detection
        if len(bent) == 0:
            return "✋ Open Hand"
        elif len(bent) == len(self.sensors):
            return "✊ Closed Fist"
        elif len(straight) == 2 and 'index' in straight and 'middle' in straight:
            return "✌ Peace Sign"
        elif len(straight) == 1 and 'index' in straight:
            return "☝ Pointing"
        elif len(straight) == 1 and 'thumb' in straight:
            return "👍 Thumbs Up"
        elif 'pinky' in bent and 'thumb' in bent and len(straight) >= 2:
            return "🤘 Rock Sign"
        else:
            return f"Custom ({len(bent)} bent, {len(straight)} straight)"

# =========================
# Main Program
# =========================
def main():
    """Main program with menu interface"""
    
    # Initialize sensor system
    system = FlexSensorSystem()
    
    if len(system.sensors) == 0:
        print("Error: No sensors available!")
        return
    
    # Main menu loop
    while True:
        print("\n" + "─" * 60)
        print("  FLEX SENSOR MENU")
        print("─" * 60)
        print("  1. Read all flex sensors once")
        print("  2. Continuous monitoring")
        print("  3. Monitor for X seconds")
        print("  4. Calibrate all sensors")
        print("  5. Detect hand gesture")
        print("  6. Exit")
        print("─" * 60)
        
        try:
            choice = input("\nSelect option (1-6): ").strip()
            
            if choice == '1':
                system.print_all()
            
            elif choice == '2':
                print("\nContinuous monitoring (Ctrl+C to stop)...\n")
                try:
                    while True:
                        system.print_all()
                        time.sleep(1)
                except KeyboardInterrupt:
                    print("\nMonitoring stopped.\n")
            
            elif choice == '3':
                duration = input("Enter duration in seconds (default 10): ").strip()
                duration = int(duration) if duration else 10
                system.monitor(duration)
            
            elif choice == '4':
                system.calibrate_all()
            
            elif choice == '5':
                gesture = system.detect_gesture()
                print(f"\n{'=' * 60}")
                print(f"  Detected Gesture: {gesture}")
                print('=' * 60)
                system.print_all()
            
            elif choice == '6':
                print("\n" + "=" * 60)
                print("  Goodbye!")
                print("=" * 60 + "\n")
                break
            
            else:
                print("⚠ Invalid option. Please select 1-6.")
        
        except KeyboardInterrupt:
            print("\n\n" + "=" * 60)
            print("  Goodbye!")
            print("=" * 60 + "\n")
            break
        except Exception as e:
            print(f"\n⚠ Error: {e}")

if __name__ == "__main__":
    main()
