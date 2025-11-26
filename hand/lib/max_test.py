# MAX30102 Diagnostic Tool
# Tests sensor connection and displays real-time readings

from machine import Pin, SoftI2C
import time

class MAX30102:
    ADDRESS = 0x57
    REG_FIFO_DATA = 0x07
    REG_FIFO_CONFIG = 0x08
    REG_MODE_CONFIG = 0x09
    REG_SPO2_CONFIG = 0x0A
    REG_LED1_PA = 0x0C
    REG_LED2_PA = 0x0D
    REG_PART_ID = 0xFF
    
    def __init__(self, i2c, address=ADDRESS):
        self.i2c = i2c
        self.address = address
        
        try:
            part_id = self.i2c.readfrom_mem(self.address, self.REG_PART_ID, 1)[0]
            print(f"✓ MAX30102 found (Part ID: 0x{part_id:02x})")
        except OSError:
            raise RuntimeError(f"✗ MAX30102 not found at 0x{address:02x}")
        
        self.reset()
        self.setup()
        
    def reset(self):
        self.i2c.writeto_mem(self.address, self.REG_MODE_CONFIG, bytes([0x40]))
        time.sleep(0.1)
        
    def setup(self):
        # FIFO configuration
        self.i2c.writeto_mem(self.address, self.REG_FIFO_CONFIG, bytes([0x4F]))
        # SpO2 mode
        self.i2c.writeto_mem(self.address, self.REG_MODE_CONFIG, bytes([0x03]))
        # 100 Hz, 411μs pulse width
        self.i2c.writeto_mem(self.address, self.REG_SPO2_CONFIG, bytes([0x27]))
        # LED brightness (start with higher values)
        self.i2c.writeto_mem(self.address, self.REG_LED1_PA, bytes([0x7F]))  # Red - Max
        self.i2c.writeto_mem(self.address, self.REG_LED2_PA, bytes([0x7F]))  # IR - Max
        print("✓ Sensor configured (LEDs at max brightness)")
        
    def read_fifo(self):
        data = self.i2c.readfrom_mem(self.address, self.REG_FIFO_DATA, 6)
        red = (data[0] << 16 | data[1] << 8 | data[2]) & 0x03FFFF
        ir = (data[3] << 16 | data[4] << 8 | data[5]) & 0x03FFFF
        return red, ir

print("\n" + "=" * 70)
print("  MAX30102 DIAGNOSTIC TOOL")
print("=" * 70 + "\n")

# Initialize I2C
i2c = SoftI2C(sda=Pin(6), scl=Pin(7), freq=400000)

print("Scanning I2C bus...")
devices = i2c.scan()
print(f"Found devices: {[hex(d) for d in devices]}\n")

if not devices:
    print("❌ No I2C devices found!")
    print("\nCheck wiring:")
    print("  VCC  → 3.3V")
    print("  GND  → GND")
    print("  SDA  → GP6")
    print("  SCL  → GP7")
    print("\nMake sure sensor has power (LED should be visible)")
else:
    try:
        sensor = MAX30102(i2c)
        
        print("\n" + "-" * 70)
        print("INSTRUCTIONS:")
        print("  1. Place your finger GENTLY on the sensor")
        print("  2. Don't press too hard (blocks blood flow)")
        print("  3. Cover both the Red and IR LEDs completely")
        print("  4. Watch the values below")
        print("-" * 70 + "\n")
        
        print("Starting real-time monitoring (Ctrl+C to stop)...\n")
        print("Expected values with finger:")
        print("  Red: 50,000 - 200,000")
        print("  IR:  50,000 - 200,000")
        print("\nCurrent readings:\n")
        
        count = 0
        finger_detected = False
        
        while True:
            red, ir = sensor.read_fifo()
            
            # Determine status
            if red > 50000 and ir > 50000:
                status = "✅ FINGER DETECTED"
                if not finger_detected:
                    print("\n🎉 Finger detected!\n")
                    finger_detected = True
            elif red > 10000 or ir > 10000:
                status = "⚠️  Weak signal (adjust finger)"
                finger_detected = False
            else:
                status = "❌ No finger"
                finger_detected = False
            
            # Display every 10 samples (10 times per second at 100Hz)
            if count % 10 == 0:
                bar_red = '#' * min(int(red / 5000), 40)
                bar_ir = '#' * min(int(ir / 5000), 40)
                
                print(f"Red: {red:6d} |{bar_red:<40}| {status}")
                print(f"IR:  {ir:6d} |{bar_ir:<40}|")
                print()
            
            count += 1
            time.sleep(0.01)
    
    except KeyboardInterrupt:
        print("\n\nDiagnostic stopped.")
        print("\n" + "=" * 70)
        
        if finger_detected:
            print("✅ Sensor is working! You can now use max_hr.py")
        else:
            print("❌ Sensor didn't detect finger properly")
            print("\nTroubleshooting:")
            print("  • Make sure LEDs are glowing (visible in dark)")
            print("  • Place finger on TOP of sensor, not edge")
            print("  • Don't press too hard")
            print("  • Try different finger (index usually works best)")
            print("  • Ensure finger covers BOTH LEDs")
            print("  • Clean sensor surface")
        
        print("=" * 70 + "\n")
    
    except RuntimeError as e:
        print(f"Error: {e}")
