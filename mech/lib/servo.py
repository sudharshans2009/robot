# Servo Motor Control for Raspberry Pi Pico
# Hardware:
# - Servo Motors on GP10, GP11, GP12, GP14, GP15

from machine import Pin, PWM
import time

class Servo:
    """Driver for hobby servo motors (SG90, MG90S, etc.)"""
    
    def __init__(self, pin, min_us=500, max_us=2500, freq=50):
        """
        Initialize servo motor
        
        Args:
            pin: GPIO pin number
            min_us: Minimum pulse width in microseconds (0 degrees)
            max_us: Maximum pulse width in microseconds (180 degrees)
            freq: PWM frequency in Hz (default 50Hz for servos)
        """
        self.pwm = PWM(Pin(pin))
        self.pwm.freq(freq)
        self.min_us = min_us
        self.max_us = max_us
        self.current_angle = 90
        self.angle(90)  # Start at center position
    
    def angle(self, degrees):
        """
        Set servo angle
        
        Args:
            degrees: Angle in degrees (0-180)
        """
        # Constrain to 0-180 degrees
        degrees = max(0, min(180, degrees))
        
        # Calculate pulse width
        pulse_width = self.min_us + (self.max_us - self.min_us) * degrees / 180
        
        # Convert to duty cycle (16-bit: 0-65535)
        # duty = (pulse_width / 20000) * 65535
        duty = int(pulse_width * 65535 / 20000)
        
        self.pwm.duty_u16(duty)
        self.current_angle = degrees
    
    def get_angle(self):
        """Get current servo angle"""
        return self.current_angle
    
    def sweep(self, start=0, end=180, step=5, delay=0.05):
        """
        Sweep servo between two angles
        
        Args:
            start: Starting angle
            end: Ending angle
            step: Step size
            delay: Delay between steps in seconds
        """
        if start < end:
            for angle in range(start, end + 1, step):
                self.angle(angle)
                time.sleep(delay)
        else:
            for angle in range(start, end - 1, -step):
                self.angle(angle)
                time.sleep(delay)
    
    def deinit(self):
        """Turn off PWM"""
        self.pwm.deinit()

class ServoController:
    """Control multiple servo motors"""
    
    def __init__(self, pins):
        """
        Initialize multiple servos
        
        Args:
            pins: List of GPIO pin numbers
        """
        self.servos = {}
        for i, pin in enumerate(pins):
            self.servos[f'servo_{i+1}'] = Servo(pin)
        
        print(f"✓ Initialized {len(self.servos)} servos on pins: {pins}")
    
    def set_angle(self, servo_name, angle):
        """Set angle for specific servo"""
        if servo_name in self.servos:
            self.servos[servo_name].angle(angle)
        else:
            print(f"Error: Servo '{servo_name}' not found")
    
    def set_all_angles(self, angle):
        """Set all servos to same angle"""
        for servo in self.servos.values():
            servo.angle(angle)
    
    def set_angles(self, angles):
        """
        Set angles for all servos
        
        Args:
            angles: List of angles (must match number of servos)
        """
        if len(angles) != len(self.servos):
            print(f"Error: Expected {len(self.servos)} angles, got {len(angles)}")
            return
        
        for i, (name, servo) in enumerate(self.servos.items()):
            servo.angle(angles[i])
    
    def get_angles(self):
        """Get current angles of all servos"""
        return {name: servo.get_angle() for name, servo in self.servos.items()}
    
    def sweep_all(self, start=0, end=180, step=5, delay=0.05):
        """Sweep all servos together"""
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
    
    def wave_motion(self, delay=0.1):
        """Create a wave motion across servos"""
        for i in range(len(self.servos)):
            for j, servo in enumerate(self.servos.values()):
                if j == i:
                    servo.angle(180)
                else:
                    servo.angle(0)
            time.sleep(delay)
    
    def deinit_all(self):
        """Turn off all servos"""
        for servo in self.servos.values():
            servo.deinit()

# =========================
# Main Program
# =========================
def main():
    """Main program with interactive servo control"""
    
    print("\n" + "=" * 60)
    print("  SERVO MOTOR CONTROLLER")
    print("=" * 60)
    print("Hardware:")
    print("  - 5 Servos on GP10, GP11, GP12, GP14, GP15")
    print("=" * 60 + "\n")
    
    # Initialize servos
    servo_pins = [10, 11, 12, 14, 15]
    controller = ServoController(servo_pins)
    
    # Main menu loop
    while True:
        print("\n" + "-" * 60)
        print("  MENU")
        print("-" * 60)
        print("  1. Set all servos to same angle")
        print("  2. Set individual servo angle")
        print("  3. Set custom angles for all servos")
        print("  4. Sweep all servos (0° → 180°)")
        print("  5. Sweep all servos (180° → 0°)")
        print("  6. Wave motion")
        print("  7. Center all servos (90°)")
        print("  8. Show current angles")
        print("  9. Preset positions")
        print(" 10. Exit")
        print("-" * 60)
        
        try:
            choice = input("\nSelect option (1-10): ").strip()
            
            if choice == '1':
                angle = input("Enter angle (0-180): ").strip()
                try:
                    angle = int(angle)
                    controller.set_all_angles(angle)
                    print(f"✓ All servos set to {angle}°")
                except ValueError:
                    print("⚠ Invalid angle")
            
            elif choice == '2':
                servo_num = input("Enter servo number (1-5): ").strip()
                angle = input("Enter angle (0-180): ").strip()
                try:
                    servo_num = int(servo_num)
                    angle = int(angle)
                    if 1 <= servo_num <= 5:
                        controller.set_angle(f'servo_{servo_num}', angle)
                        print(f"✓ Servo {servo_num} set to {angle}°")
                    else:
                        print("⚠ Servo number must be 1-5")
                except ValueError:
                    print("⚠ Invalid input")
            
            elif choice == '3':
                angles_str = input("Enter 5 angles separated by spaces (e.g., 0 45 90 135 180): ").strip()
                try:
                    angles = [int(a) for a in angles_str.split()]
                    controller.set_angles(angles)
                    print(f"✓ Servos set to: {angles}")
                except ValueError:
                    print("⚠ Invalid angles")
            
            elif choice == '4':
                print("\nSweeping all servos 0° → 180°...")
                controller.sweep_all(0, 180, step=5, delay=0.03)
                print("✓ Sweep complete")
            
            elif choice == '5':
                print("\nSweeping all servos 180° → 0°...")
                controller.sweep_all(180, 0, step=5, delay=0.03)
                print("✓ Sweep complete")
            
            elif choice == '6':
                print("\nExecuting wave motion...")
                for _ in range(3):
                    controller.wave_motion(delay=0.15)
                controller.set_all_angles(90)
                print("✓ Wave complete")
            
            elif choice == '7':
                controller.set_all_angles(90)
                print("✓ All servos centered at 90°")
            
            elif choice == '8':
                angles = controller.get_angles()
                print("\nCurrent Servo Angles:")
                for i, (name, angle) in enumerate(angles.items(), 1):
                    print(f"  Servo {i} (GP{servo_pins[i-1]}): {angle}°")
            
            elif choice == '9':
                print("\nPreset Positions:")
                print("  1. All Extended (180°)")
                print("  2. All Retracted (0°)")
                print("  3. Center (90°)")
                print("  4. Alternating (0°, 180°, 0°, 180°, 0°)")
                print("  5. Gradient (0°, 45°, 90°, 135°, 180°)")
                
                preset = input("\nSelect preset (1-5): ").strip()
                
                if preset == '1':
                    controller.set_all_angles(180)
                    print("✓ All servos extended to 180°")
                elif preset == '2':
                    controller.set_all_angles(0)
                    print("✓ All servos retracted to 0°")
                elif preset == '3':
                    controller.set_all_angles(90)
                    print("✓ All servos centered at 90°")
                elif preset == '4':
                    controller.set_angles([0, 180, 0, 180, 0])
                    print("✓ Alternating pattern set")
                elif preset == '5':
                    controller.set_angles([0, 45, 90, 135, 180])
                    print("✓ Gradient pattern set")
                else:
                    print("⚠ Invalid preset")
            
            elif choice == '10':
                print("\nCentering servos before exit...")
                controller.set_all_angles(90)
                time.sleep(0.5)
                print("✓ Servos centered")
                print("\n" + "=" * 60)
                print("  Goodbye!")
                print("=" * 60 + "\n")
                break
            
            else:
                print("⚠ Invalid option. Please select 1-10.")
        
        except KeyboardInterrupt:
            print("\n\nCentering servos before exit...")
            controller.set_all_angles(90)
            time.sleep(0.5)
            print("\n" + "=" * 60)
            print("  Goodbye!")
            print("=" * 60 + "\n")
            break
        except Exception as e:
            print(f"\n⚠ Error: {e}")

if __name__ == "__main__":
    main()
