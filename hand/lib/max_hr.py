# MAX30102 Heart Rate and SpO2 Monitor
# Displays heart rate (BPM) and blood oxygen saturation (SpO2%)
# Hardware: MAX30102 on I2C - SDA=GP6, SCL=GP7

from machine import Pin, SoftI2C
import time
import math

class MAX30102:
    """Enhanced MAX30102 driver with HR and SpO2 calculation"""
    
    ADDRESS = 0x57
    
    # Registers
    REG_INTR_STATUS_1 = 0x00
    REG_INTR_STATUS_2 = 0x01
    REG_INTR_ENABLE_1 = 0x02
    REG_INTR_ENABLE_2 = 0x03
    REG_FIFO_WR_PTR = 0x04
    REG_OVF_COUNTER = 0x05
    REG_FIFO_RD_PTR = 0x06
    REG_FIFO_DATA = 0x07
    REG_FIFO_CONFIG = 0x08
    REG_MODE_CONFIG = 0x09
    REG_SPO2_CONFIG = 0x0A
    REG_LED1_PA = 0x0C
    REG_LED2_PA = 0x0D
    REG_PILOT_PA = 0x10
    REG_MULTI_LED_CTRL1 = 0x11
    REG_MULTI_LED_CTRL2 = 0x12
    REG_TEMP_INTR = 0x1F
    REG_TEMP_FRAC = 0x20
    REG_TEMP_CONFIG = 0x21
    REG_PART_ID = 0xFF
    
    def __init__(self, i2c, address=ADDRESS):
        self.i2c = i2c
        self.address = address
        
        # Check device ID
        try:
            part_id = self.i2c.readfrom_mem(self.address, self.REG_PART_ID, 1)[0]
            if part_id == 0x15:
                print(f"✓ MAX30102 detected (Part ID: 0x{part_id:02x})")
            else:
                print(f"⚠ Unexpected part ID: 0x{part_id:02x}")
        except OSError:
            raise RuntimeError(f"✗ MAX30102 not found at 0x{address:02x}")
        
        self.reset()
        self.setup()
        
        # Buffers for calculation
        self.red_buffer = []
        self.ir_buffer = []
        self.buffer_size = 100
        
    def reset(self):
        """Soft reset"""
        self.i2c.writeto_mem(self.address, self.REG_MODE_CONFIG, bytes([0x40]))
        time.sleep(0.1)
        
    def setup(self):
        """Configure sensor for HR and SpO2 measurement"""
        # FIFO configuration: sample averaging = 4, rollover enabled
        self.i2c.writeto_mem(self.address, self.REG_FIFO_CONFIG, bytes([0x4F]))
        
        # Mode configuration: SpO2 mode (Red + IR)
        self.i2c.writeto_mem(self.address, self.REG_MODE_CONFIG, bytes([0x03]))
        
        # SpO2 configuration: 100 Hz, 411μs pulse width, 4096 ADC range
        self.i2c.writeto_mem(self.address, self.REG_SPO2_CONFIG, bytes([0x27]))
        
        # LED pulse amplitude (brightness)
        self.i2c.writeto_mem(self.address, self.REG_LED1_PA, bytes([0x24]))  # Red LED
        self.i2c.writeto_mem(self.address, self.REG_LED2_PA, bytes([0x24]))  # IR LED
        
        print("✓ MAX30102 configured for HR and SpO2")
        
    def read_fifo(self):
        """Read one sample from FIFO"""
        data = self.i2c.readfrom_mem(self.address, self.REG_FIFO_DATA, 6)
        red = (data[0] << 16 | data[1] << 8 | data[2]) & 0x03FFFF
        ir = (data[3] << 16 | data[4] << 8 | data[5]) & 0x03FFFF
        return red, ir
    
    def check_finger(self):
        """Check if finger is present"""
        red, ir = self.read_fifo()
        return red > 50000 and ir > 50000
    
    def collect_samples(self, duration=4):
        """Collect samples for analysis"""
        print(f"Collecting samples for {duration}s (keep finger still)...")
        
        self.red_buffer = []
        self.ir_buffer = []
        
        start_time = time.time()
        sample_count = 0
        
        while time.time() - start_time < duration:
            red, ir = self.read_fifo()
            
            # Only collect if finger is present
            if red > 50000 and ir > 50000:
                self.red_buffer.append(red)
                self.ir_buffer.append(ir)
                sample_count += 1
            
            time.sleep(0.01)  # 100 Hz sampling
        
        print(f"Collected {sample_count} samples")
        return sample_count >= 100
    
    def calculate_heart_rate(self):
        """Calculate heart rate from IR signal using peak detection"""
        if len(self.ir_buffer) < 100:
            return 0, "Insufficient data"
        
        # Normalize IR signal
        ir_mean = sum(self.ir_buffer) / len(self.ir_buffer)
        ir_normalized = [x - ir_mean for x in self.ir_buffer]
        
        # Find peaks
        peaks = []
        for i in range(1, len(ir_normalized) - 1):
            if (ir_normalized[i] > ir_normalized[i-1] and 
                ir_normalized[i] > ir_normalized[i+1] and 
                ir_normalized[i] > ir_mean * 0.1):
                peaks.append(i)
        
        if len(peaks) < 2:
            return 0, "No peaks detected"
        
        # Calculate average interval between peaks
        intervals = []
        for i in range(1, len(peaks)):
            intervals.append(peaks[i] - peaks[i-1])
        
        if len(intervals) == 0:
            return 0, "No intervals"
        
        avg_interval = sum(intervals) / len(intervals)
        
        # Convert to BPM (assuming 100 Hz sampling)
        bpm = int(6000 / avg_interval) if avg_interval > 0 else 0
        
        # Validate range
        if 40 <= bpm <= 200:
            return bpm, "Valid"
        else:
            return bpm, "Out of range"
    
    def calculate_spo2(self):
        """Calculate SpO2 from Red/IR ratio"""
        if len(self.red_buffer) < 100 or len(self.ir_buffer) < 100:
            return 0, "Insufficient data"
        
        # Calculate AC and DC components
        red_ac = max(self.red_buffer) - min(self.red_buffer)
        red_dc = sum(self.red_buffer) / len(self.red_buffer)
        
        ir_ac = max(self.ir_buffer) - min(self.ir_buffer)
        ir_dc = sum(self.ir_buffer) / len(self.ir_buffer)
        
        if red_dc == 0 or ir_dc == 0 or ir_ac == 0:
            return 0, "Division by zero"
        
        # Calculate R value
        R = (red_ac / red_dc) / (ir_ac / ir_dc)
        
        # Empirical formula for SpO2
        # SpO2 = -45.060*R^2 + 30.354*R + 94.845
        spo2 = -45.060 * R * R + 30.354 * R + 94.845
        spo2 = int(spo2)
        
        # Validate range
        if 70 <= spo2 <= 100:
            return spo2, "Valid"
        else:
            return spo2, "Out of range"
    
    def measure(self):
        """Perform complete measurement"""
        # Check if finger is present
        if not self.check_finger():
            return {
                'heart_rate': 0,
                'spo2': 0,
                'status': 'No finger detected',
                'red': 0,
                'ir': 0
            }
        
        # Collect samples
        if not self.collect_samples(duration=4):
            return {
                'heart_rate': 0,
                'spo2': 0,
                'status': 'Collection failed',
                'red': 0,
                'ir': 0
            }
        
        # Calculate metrics
        hr, hr_status = self.calculate_heart_rate()
        spo2, spo2_status = self.calculate_spo2()
        
        # Get current raw values
        red, ir = self.read_fifo()
        
        return {
            'heart_rate': hr,
            'spo2': spo2,
            'status': f"HR: {hr_status}, SpO2: {spo2_status}",
            'red': red,
            'ir': ir
        }

# ==================== MAIN PROGRAM ====================

def main():
    print("\n" + "=" * 60)
    print("  MAX30102 Heart Rate & SpO2 Monitor")
    print("=" * 60 + "\n")
    
    # Initialize I2C and sensor
    i2c = SoftI2C(sda=Pin(6), scl=Pin(7), freq=400000)
    
    print("Scanning I2C bus...")
    devices = i2c.scan()
    print(f"Found devices: {[hex(d) for d in devices]}\n")
    
    try:
        sensor = MAX30102(i2c)
    except RuntimeError as e:
        print(f"Error: {e}")
        print("\nCheck wiring:")
        print("  VCC  → 3.3V")
        print("  GND  → GND")
        print("  SDA  → GP6")
        print("  SCL  → GP7")
        return
    
    print("\n" + "-" * 60)
    print("Instructions:")
    print("  1. Place finger GENTLY on sensor")
    print("  2. Keep finger still during measurement")
    print("  3. Wait for results (4-5 seconds)")
    print("  4. Press Ctrl+C to exit")
    print("-" * 60 + "\n")
    
    try:
        while True:
            print("\nPress Enter to start measurement...")
            input()
            
            print("\n📊 Measuring...")
            result = sensor.measure()
            
            print("\n" + "=" * 60)
            print("  RESULTS")
            print("=" * 60)
            print(f"  ❤️  Heart Rate:  {result['heart_rate']} BPM")
            print(f"  🩸 Blood Oxygen: {result['spo2']}%")
            print(f"  📈 Red LED:      {result['red']}")
            print(f"  📉 IR LED:       {result['ir']}")
            print(f"  ℹ️  Status:       {result['status']}")
            print("=" * 60)
            
            # Health assessment
            if result['heart_rate'] > 0:
                if result['heart_rate'] < 60:
                    print("  ⚠️  Heart rate is low (bradycardia)")
                elif result['heart_rate'] > 100:
                    print("  ⚠️  Heart rate is high (tachycardia)")
                else:
                    print("  ✅ Heart rate is normal")
            
            if result['spo2'] > 0:
                if result['spo2'] >= 95:
                    print("  ✅ Blood oxygen is normal")
                elif result['spo2'] >= 90:
                    print("  ⚠️  Blood oxygen is slightly low")
                else:
                    print("  🚨 Blood oxygen is critically low!")
            
            print()
    
    except KeyboardInterrupt:
        print("\n\nExiting...")
        print("=" * 60)
        print("  Goodbye!")
        print("=" * 60 + "\n")

if __name__ == "__main__":
    main()
