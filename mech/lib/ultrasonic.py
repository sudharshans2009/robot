# Ultrasonic Distance Sensor (HC-SR04) for Raspberry Pi Pico
# Hardware:
# - Trigger Pin: GP20
# - Echo Pin: GP19

from machine import Pin
import time

class UltrasonicSensor:
    """Driver for HC-SR04 Ultrasonic Distance Sensor"""
    
    def __init__(self, trigger_pin, echo_pin):
        """
        Initialize ultrasonic sensor
        
        Args:
            trigger_pin: GPIO pin number for trigger
            echo_pin: GPIO pin number for echo
        """
        self.trigger = Pin(trigger_pin, Pin.OUT)
        self.echo = Pin(echo_pin, Pin.IN)
        self.trigger.low()
    
    def measure_distance(self, timeout=30000):
        """
        Measure distance in centimeters
        
        Args:
            timeout: Maximum time to wait for echo (microseconds)
        
        Returns:
            float: Distance in centimeters, or -1 if timeout
        """
        # Send 10us pulse to trigger
        self.trigger.low()
        time.sleep_us(2)
        self.trigger.high()
        time.sleep_us(10)
        self.trigger.low()
        
        # Wait for echo to go high
        pulse_start = time.ticks_us()
        while self.echo.value() == 0:
            if time.ticks_diff(time.ticks_us(), pulse_start) > timeout:
                return -1
            pulse_start = time.ticks_us()
        
        # Wait for echo to go low
        pulse_end = time.ticks_us()
        while self.echo.value() == 1:
            if time.ticks_diff(time.ticks_us(), pulse_start) > timeout:
                return -1
            pulse_end = time.ticks_us()
        
        # Calculate distance
        # Speed of sound = 343 m/s = 0.0343 cm/us
        # Distance = (time * speed) / 2 (divide by 2 for round trip)
        pulse_duration = time.ticks_diff(pulse_end, pulse_start)
        distance = (pulse_duration * 0.0343) / 2
        
        return distance
    
    def measure_distance_inches(self):
        """Measure distance in inches"""
        cm = self.measure_distance()
        if cm < 0:
            return -1
        return cm / 2.54
    
    def get_multiple_readings(self, samples=5):
        """
        Get average of multiple readings for better accuracy
        
        Args:
            samples: Number of samples to average
        
        Returns:
            float: Average distance in cm
        """
        readings = []
        for _ in range(samples):
            dist = self.measure_distance()
            if dist > 0:
                readings.append(dist)
            time.sleep_ms(60)  # Wait between readings
        
        if len(readings) == 0:
            return -1
        
        return sum(readings) / len(readings)

# =========================
# Main Program
# =========================
def main():
    """Main program with continuous distance monitoring"""
    
    print("\n" + "=" * 60)
    print("  ULTRASONIC DISTANCE SENSOR - HC-SR04")
    print("=" * 60)
    print("Hardware:")
    print("  - Trigger: GP20")
    print("  - Echo: GP19")
    print("\nMeasuring distance...")
    print("Press Ctrl+C to stop\n")
    print("=" * 60 + "\n")
    
    # Initialize sensor
    sensor = UltrasonicSensor(trigger_pin=20, echo_pin=19)
    
    try:
        while True:
            # Get distance measurement
            distance_cm = sensor.measure_distance()
            
            if distance_cm < 0:
                print("Error: No echo received (timeout)")
            else:
                distance_in = distance_cm / 2.54
                
                # Create visual bar indicator (0-100cm range)
                bar_length = int(min(distance_cm, 100) / 5)  # 0-20 characters
                bar = "█" * bar_length + "░" * (20 - bar_length)
                
                # Distance status
                if distance_cm < 10:
                    status = "⚠ VERY CLOSE"
                elif distance_cm < 30:
                    status = "⚡ CLOSE"
                elif distance_cm < 100:
                    status = "✓ NORMAL"
                else:
                    status = "⚪ FAR"
                
                print(f"Distance: {distance_cm:6.2f} cm | {distance_in:6.2f} in | [{bar}] {status}")
            
            time.sleep(0.3)
    
    except KeyboardInterrupt:
        print("\n\n" + "=" * 60)
        print("  Measurement stopped. Goodbye!")
        print("=" * 60 + "\n")

# Alternative: Menu-based interface
def menu_interface():
    """Interactive menu for ultrasonic sensor"""
    
    print("\n" + "=" * 60)
    print("  ULTRASONIC SENSOR CONTROL")
    print("=" * 60 + "\n")
    
    sensor = UltrasonicSensor(trigger_pin=20, echo_pin=19)
    
    while True:
        print("\nOptions:")
        print("  1. Single measurement")
        print("  2. Continuous monitoring")
        print("  3. Average of 5 readings")
        print("  4. Distance alert (threshold)")
        print("  5. Exit")
        
        try:
            choice = input("\nSelect option (1-5): ").strip()
            
            if choice == '1':
                dist = sensor.measure_distance()
                if dist < 0:
                    print("\n⚠ Error: No echo received")
                else:
                    print(f"\n✓ Distance: {dist:.2f} cm ({dist/2.54:.2f} inches)\n")
            
            elif choice == '2':
                print("\nContinuous monitoring (Ctrl+C to stop)...\n")
                try:
                    while True:
                        dist = sensor.measure_distance()
                        if dist < 0:
                            print("Error: Timeout")
                        else:
                            print(f"Distance: {dist:6.2f} cm | {dist/2.54:6.2f} in")
                        time.sleep(0.3)
                except KeyboardInterrupt:
                    print("\nMonitoring stopped.\n")
            
            elif choice == '3':
                print("\nTaking 5 readings...")
                avg_dist = sensor.get_multiple_readings(samples=5)
                if avg_dist < 0:
                    print("⚠ Error: No valid readings\n")
                else:
                    print(f"✓ Average Distance: {avg_dist:.2f} cm ({avg_dist/2.54:.2f} inches)\n")
            
            elif choice == '4':
                threshold = input("Enter alert threshold in cm (default 20): ").strip()
                threshold = float(threshold) if threshold else 20.0
                
                print(f"\nMonitoring distance - Alert if < {threshold} cm")
                print("Press Ctrl+C to stop\n")
                
                try:
                    while True:
                        dist = sensor.measure_distance()
                        if dist < 0:
                            print("Error: Timeout")
                        elif dist < threshold:
                            print(f"⚠⚠⚠ ALERT! Distance: {dist:.2f} cm ⚠⚠⚠")
                        else:
                            print(f"✓ OK: {dist:.2f} cm")
                        time.sleep(0.3)
                except KeyboardInterrupt:
                    print("\nAlert monitoring stopped.\n")
            
            elif choice == '5':
                print("\n" + "=" * 60)
                print("  Goodbye!")
                print("=" * 60 + "\n")
                break
            
            else:
                print("\n⚠ Invalid option. Please select 1-5.")
        
        except KeyboardInterrupt:
            print("\n\n" + "=" * 60)
            print("  Goodbye!")
            print("=" * 60 + "\n")
            break
        except Exception as e:
            print(f"\n⚠ Error: {e}\n")

if __name__ == "__main__":
    # Choose which interface to run:
    main()  # Continuous monitoring
    # menu_interface()  # Menu-based interface (uncomment to use)
