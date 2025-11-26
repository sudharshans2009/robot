# ADS1115 Test - MicroPython Version
# Reads all 4 analog channels and displays values

from machine import Pin, I2C
import utime

# =========================
# ADS1115 Driver
# =========================
class ADS1115:
    """ADS1115 16-bit ADC driver"""
    
    def __init__(self, i2c, address=0x48):
        """Initialize ADS1115"""
        self.i2c = i2c
        self.address = address
        
        # Test connection
        try:
            devices = self.i2c.scan()
            if address in devices:
                print(f"ADS1115 found at address 0x{address:02x}")
            else:
                print(f"Warning: Address 0x{address:02x} not found")
                print(f"Available devices: {[hex(d) for d in devices]}")
        except OSError as e:
            raise RuntimeError(f"I2C error: {e}")
    
    def read_config(self):
        """Read configuration register"""
        self.i2c.writeto(self.address, bytearray([1]))
        result = self.i2c.readfrom(self.address, 2)
        return result[0] << 8 | result[1]
    
    def read_adc(self, channel):
        """
        Read raw ADC value from specified channel (0-3)
        Returns: signed 16-bit integer
        """
        if not (0 <= channel <= 3):
            raise ValueError("Channel must be 0-3")
        
        # Read current value
        self.i2c.writeto(self.address, bytearray([0]))
        result = self.i2c.readfrom(self.address, 2)
        
        # Configure for next read
        config = self.read_config()
        config &= ~(7 << 12) & ~(7 << 9)
        config |= (channel << 12) | (1 << 9) | (1 << 15)
        config_bytes = [int(config >> i & 0xff) for i in (8, 0)]
        self.i2c.writeto(self.address, bytearray([1] + config_bytes))
        
        # Convert to signed value
        value = result[0] << 8 | result[1]
        if value > 32767:
            value -= 65536
        
        return value
    
    def to_voltage(self, val, max_val=26100, voltage_ref=3.3):
        """Convert ADC value to voltage"""
        return val / max_val * voltage_ref

# =========================
# Main Program
# =========================
def setup():
    """Setup function (Arduino-style)"""
    print("Hello!")
    print("ADS1115 MicroPython Test")
    print("ADS1X15_LIB_VERSION: 1.0.0")
    
    # Initialize I2C
    # Using I2C(1): SDA=GP14, SCL=GP15
    i2c = I2C(1, freq=400000, scl=Pin(15), sda=Pin(14))
    
    # Scan I2C bus
    devices = i2c.scan()
    print(f"I2C devices found: {[hex(d) for d in devices]}")
    
    # Initialize ADS1115
    ads = ADS1115(i2c, 0x48)
    
    # Read and display config
    config = ads.read_config()
    print(f"Config: {bin(config)}")
    
    return ads

def loop(ads):
    """Loop function (Arduino-style)"""
    val_0 = ads.read_adc(0)
    val_1 = ads.read_adc(1)
    val_2 = ads.read_adc(2)
    val_3 = ads.read_adc(3)
    
    v_0 = ads.to_voltage(val_0)
    v_1 = ads.to_voltage(val_1)
    v_2 = ads.to_voltage(val_2)
    v_3 = ads.to_voltage(val_3)
    
    print(f"\tADC0: {val_0}\t{v_0:.3f} V")
    print(f"\tADC1: {val_1}\t{v_1:.3f} V")
    print(f"\tADC2: {val_2}\t{v_2:.3f} V")
    print(f"\tADC3: {val_3}\t{v_3:.3f} V")
    print()
    
    utime.sleep(0.5)

# =========================
# Main Entry Point
# =========================
def main():
    """Main function"""
    ads = setup()
    
    print("\nStarting continuous reading...")
    print("Press Ctrl+C to stop\n")
    
    try:
        while True:
            loop(ads)
    except KeyboardInterrupt:
        print("\nStopped.")

if __name__ == "__main__":
    main()
