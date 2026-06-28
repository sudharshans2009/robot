# Flex Sensor Reader - Direct ADC Only
# GP26 (A0) - Flex Sensor 1
# GP27 (A1) - Flex Sensor 2
# Total: 2 flex sensors

from machine import Pin, ADC
import utime

# =========================
# Flex Sensor System
# =========================
class FlexSensorSystem:
    """Manage 2 flex sensors via direct ADC pins"""
    
    def __init__(self):
        """Initialize flex sensor system"""
        print("\n" + "=" * 60)
        print("  FLEX SENSOR SYSTEM - DIRECT ADC")
        print("=" * 60)
        
        self.adc_a0 = None
        self.adc_a1 = None
        
        # Initialize Direct ADC on GP26 (A0)
        print("\nInitializing GP26 (A0)...")
        try:
            self.adc_a0 = ADC(Pin(26))
            print("  ✓ GP26 (A0) initialized - Sensor 0")
        except Exception as e:
            print(f"  ✗ GP26 (A0) initialization failed: {e}")
        
        # Initialize Direct ADC on GP27 (A1)
        print("\nInitializing GP27 (A1)...")
        try:
            self.adc_a1 = ADC(Pin(27))
            print("  ✓ GP27 (A1) initialized - Sensor 1")
        except Exception as e:
            print(f"  ✗ GP27 (A1) initialization failed: {e}")
        
        # Sensor names (2 total)
        self.sensor_names = [
            "Flex_1",  # GP26 A0
            "Flex_2"   # GP27 A1
        ]
        
        print("\n" + "=" * 60)
        total_sensors = sum([1 for adc in [self.adc_a0, self.adc_a1] if adc is not None])
        print(f"  SYSTEM READY: {total_sensors} flex sensors available")
        
        if self.adc_a0:
            print(f"    ✓ Sensor 0: Flex_1   [GP26 A0]")
        else:
            print(f"    ✗ Sensor 0: Flex_1   [GP26 A0]")
        
        if self.adc_a1:
            print(f"    ✓ Sensor 1: Flex_2   [GP27 A1]")
        else:
            print(f"    ✗ Sensor 1: Flex_2   [GP27 A1]")
        
        print("=" * 60 + "\n")
    
    def read_sensor(self, sensor_num):
        """
        Read specific sensor (0-1)
        Returns: dict with raw, voltage, percentage
        """
        if not (0 <= sensor_num <= 1):
            raise ValueError("Sensor number must be 0-1")
        
        try:
            # Direct ADC GP26 (sensor 0)
            if sensor_num == 0:
                if self.adc_a0 is None:
                    return {'raw': 0, 'voltage': 0, 'percentage': 0, 'error': 'GP26 not available'}
                
                raw = self.adc_a0.read_u16()
                voltage = (raw / 65535.0) * 3.3
                percentage = (raw / 65535.0) * 100.0
            
            # Direct ADC GP27 (sensor 1)
            else:  # sensor_num == 1
                if self.adc_a1 is None:
                    return {'raw': 0, 'voltage': 0, 'percentage': 0, 'error': 'GP27 not available'}
                
                raw = self.adc_a1.read_u16()
                voltage = (raw / 65535.0) * 3.3
                percentage = (raw / 65535.0) * 100.0
            
            percentage = max(0, min(100, percentage))
            
            return {
                'raw': raw,
                'voltage': voltage,
                'percentage': percentage,
                'name': self.sensor_names[sensor_num]
            }
        except Exception as e:
            return {'raw': 0, 'voltage': 0, 'percentage': 0, 'error': str(e)}
    
    def read_all(self):
        """Read all 2 sensors"""
        readings = {}
        for i in range(2):
            readings[self.sensor_names[i].lower()] = self.read_sensor(i)
        return readings
    
    def read_all_list(self):
        """Read all sensors and return as list"""
        return [self.read_sensor(i) for i in range(2)]
    
    def print_reading(self, sensor_num):
        """Print single sensor reading"""
        data = self.read_sensor(sensor_num)
        name = self.sensor_names[sensor_num]
        
        if sensor_num == 0:
            source = "GP26 A0"
        else:
            source = "GP27 A1"
        
        if 'error' in data:
            print(f"  [{sensor_num}] {name:8s} [{source}] ERROR: {data['error']}")
        else:
            # Highlight maxed values (likely disconnected)
            warning = " ⚠ MAXED" if data['raw'] > 60000 else ""
            print(f"  [{sensor_num}] {name:8s} [{source}] " +
                  f"Raw:{data['raw']:6d} | V:{data['voltage']:5.3f} | " +
                  f"Flex:{data['percentage']:5.1f}%{warning}")
    
    def print_all(self):
        """Print all sensor readings"""
        print("\n┌─ FLEX SENSOR READINGS " + "─" * 37 + "┐")
        for i in range(2):
            self.print_reading(i)
        print("└─" + "─" * 60 + "┘\n")
    
    def monitor(self, duration=10, interval=0.5):
        """Monitor all sensors for specified duration"""
        print(f"\nMonitoring {len(self.sensor_names)} flex sensors for {duration} seconds...")
        print("Press Ctrl+C to stop\n")
        
        start_time = utime.time()
        
        try:
            while utime.time() - start_time < duration:
                self.print_all()
                utime.sleep(interval)
        except KeyboardInterrupt:
            print("\nMonitoring stopped.\n")
    
    def get_servo_angles(self):
        """
        Get servo control angles for 2 servos
        Maps flex sensors to servo angles (0-180°)
        """
        readings = self.read_all_list()
        
        # Map 2 sensors to first 2 servos
        servo_angles = {}
        for i in range(2):
            if i < len(readings) and 'percentage' in readings[i]:
                # Convert percentage to servo angle (0% = 0°, 100% = 180°)
                angle = int((readings[i]['percentage'] / 100.0) * 180.0)
                angle = max(0, min(180, angle))
                servo_angles[f'servo_{i+1}'] = angle
            else:
                servo_angles[f'servo_{i+1}'] = 90  # Default middle position
        
        return servo_angles

# =========================
# Main Program
# =========================
def main():
    """Main program with menu interface"""
    
    # Initialize sensor system
    system = FlexSensorSystem()
    
    # Main menu loop
    while True:
        print("\n" + "─" * 60)
        print("  FLEX SENSOR MENU")
        print("─" * 60)
        print("  1. Read all flex sensors once")
        print("  2. Continuous monitoring")
        print("  3. Monitor for X seconds")
        print("  4. Get servo angles")
        print("  5. Read specific sensor")
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
                        utime.sleep(0.5)
                except KeyboardInterrupt:
                    print("\nMonitoring stopped.\n")
            
            elif choice == '3':
                duration = input("Enter duration in seconds (default 10): ").strip()
                duration = int(duration) if duration else 10
                system.monitor(duration)
            
            elif choice == '4':
                angles = system.get_servo_angles()
                print("\n" + "=" * 60)
                print("  SERVO CONTROL ANGLES")
                print("=" * 60)
                for servo, angle in angles.items():
                    print(f"  {servo}: {angle}°")
                print("=" * 60 + "\n")
            
            elif choice == '5':
                sensor_num = input("Enter sensor number (0-1): ").strip()
                sensor_num = int(sensor_num)
                if 0 <= sensor_num <= 1:
                    print()
                    system.print_reading(sensor_num)
                else:
                    print("⚠ Invalid sensor number. Must be 0-1.")
            
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
