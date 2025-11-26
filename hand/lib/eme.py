# Emergency Alert System for Raspberry Pi Pico
# Hardware:
# - Button: GP21 (with internal pull-down resistor)
# - LED: GP20 (Alert indicator)

from machine import Pin
import time

class EmergencyAlert:
    """Emergency Alert System with toggle button"""
    
    def __init__(self, button_pin=21, led_pin=20):
        """
        Initialize emergency alert system
        
        Args:
            button_pin: GPIO pin for emergency button
            led_pin: GPIO pin for alert LED
        """
        # Initialize button with pull-down resistor (active high)
        self.button = Pin(button_pin, Pin.IN, Pin.PULL_DOWN)
        
        # Initialize LED
        self.led = Pin(led_pin, Pin.OUT)
        self.led.off()
        
        # Alert state
        self.alert_active = False
        self.last_button_state = 0
        self.last_press_time = 0
        self.debounce_delay = 200  # milliseconds
        
        print(f"✓ Emergency Alert System initialized")
        print(f"  - Button: GP{button_pin}")
        print(f"  - LED: GP{led_pin}")
    
    def check_button_press(self):
        """Check if button was pressed (with debouncing)"""
        current_state = self.button.value()
        current_time = time.ticks_ms()
        
        # Detect rising edge (button pressed - active high)
        if current_state == 1 and self.last_button_state == 0:
            # Check debounce
            if time.ticks_diff(current_time, self.last_press_time) > self.debounce_delay:
                self.last_press_time = current_time
                self.last_button_state = current_state
                return True
        
        self.last_button_state = current_state
        return False
    
    def toggle_alert(self):
        """Toggle alert state"""
        self.alert_active = not self.alert_active
        
        if self.alert_active:
            print("\n🚨 EMERGENCY ALERT ACTIVATED 🚨")
        else:
            print("\n✓ Alert deactivated")
            self.led.off()
    
    def blink_led(self, on_time=200, off_time=200):
        """
        Blink LED once
        
        Args:
            on_time: LED on duration in milliseconds
            off_time: LED off duration in milliseconds
        """
        self.led.on()
        time.sleep_ms(on_time)
        self.led.off()
        time.sleep_ms(off_time)
    
    def run(self):
        """Main loop - continuously check button and blink LED if alert active"""
        print("\nEmergency Alert System Ready")
        print("Press button to activate/deactivate alert\n")
        
        try:
            while True:
                # Check for button press
                if self.check_button_press():
                    self.toggle_alert()
                
                # Blink LED if alert is active
                if self.alert_active:
                    self.blink_led(on_time=200, off_time=200)
                else:
                    time.sleep_ms(10)  # Small delay to reduce CPU usage
        
        except KeyboardInterrupt:
            print("\n\nShutting down Emergency Alert System...")
            self.led.off()
            print("✓ LED turned off. Goodbye!\n")

# =========================
# Alternative: Configurable Alert Patterns
# =========================
class AdvancedEmergencyAlert(EmergencyAlert):
    """Emergency Alert with multiple blink patterns"""
    
    def __init__(self, button_pin=21, led_pin=20):
        super().__init__(button_pin, led_pin)
        self.blink_patterns = {
            'fast': (100, 100),      # Fast blink
            'slow': (500, 500),      # Slow blink
            'sos': 'sos',            # SOS morse code
            'strobe': (50, 50)       # Strobe effect
        }
        self.current_pattern = 'fast'
    
    def blink_sos(self):
        """Blink SOS pattern in morse code (... --- ...)"""
        # S (3 short)
        for _ in range(3):
            self.led.on()
            time.sleep_ms(150)
            self.led.off()
            time.sleep_ms(150)
        
        time.sleep_ms(200)
        
        # O (3 long)
        for _ in range(3):
            self.led.on()
            time.sleep_ms(450)
            self.led.off()
            time.sleep_ms(150)
        
        time.sleep_ms(200)
        
        # S (3 short)
        for _ in range(3):
            self.led.on()
            time.sleep_ms(150)
            self.led.off()
            time.sleep_ms(150)
        
        time.sleep_ms(500)
    
    def run_advanced(self, pattern='fast'):
        """
        Run with specified blink pattern
        
        Args:
            pattern: 'fast', 'slow', 'sos', or 'strobe'
        """
        self.current_pattern = pattern
        
        print(f"\nEmergency Alert System Ready - Pattern: {pattern.upper()}")
        print("Press button to activate/deactivate alert\n")
        
        try:
            while True:
                # Check for button press
                if self.check_button_press():
                    self.toggle_alert()
                
                # Execute pattern if alert is active
                if self.alert_active:
                    if self.current_pattern == 'sos':
                        self.blink_sos()
                    else:
                        on_time, off_time = self.blink_patterns[self.current_pattern]
                        self.blink_led(on_time, off_time)
                else:
                    time.sleep_ms(10)
        
        except KeyboardInterrupt:
            print("\n\nShutting down Emergency Alert System...")
            self.led.off()
            print("✓ LED turned off. Goodbye!\n")

# =========================
# Main Program
# =========================
def main():
    """Main program with menu"""
    
    print("\n" + "=" * 60)
    print("  EMERGENCY ALERT SYSTEM")
    print("=" * 60)
    print("Hardware:")
    print("  - Emergency Button: GP21")
    print("  - Alert LED: GP20")
    print("=" * 60 + "\n")
    
    print("Select Alert Mode:")
    print("  1. Standard Alert (Fast Blink)")
    print("  2. Slow Blink")
    print("  3. SOS Pattern")
    print("  4. Strobe Effect")
    print("  5. Simple Mode (Basic Toggle)\n")
    
    try:
        choice = input("Select mode (1-5, default 1): ").strip()
        
        if choice == '2':
            alert = AdvancedEmergencyAlert()
            alert.run_advanced(pattern='slow')
        elif choice == '3':
            alert = AdvancedEmergencyAlert()
            alert.run_advanced(pattern='sos')
        elif choice == '4':
            alert = AdvancedEmergencyAlert()
            alert.run_advanced(pattern='strobe')
        elif choice == '5':
            alert = EmergencyAlert()
            alert.run()
        else:  # Default or '1'
            alert = AdvancedEmergencyAlert()
            alert.run_advanced(pattern='fast')
    
    except Exception as e:
        print(f"\nError: {e}")

if __name__ == "__main__":
    main()
