class GasSensor:
    def __init__(self, adc_pin=26, alert_pin=18, threshold=30000):
        """Initialize MQ6 gas sensor"""
        self.adc = ADC(Pin(adc_pin))  # GP26 (A0)
        self.alert_led = Pin(alert_pin, Pin.OUT)  # GP18 for alert
        self.threshold = threshold  # Gas level threshold (0-65535)
        self.alert_led.value(0)  # Start with LED off
        self.monitoring = False
        self.last_reading = 0
        print(f"MQ6 Gas Sensor initialized on GP{adc_pin}")
        print(f"Alert LED on GP{alert_pin}")
        print(f"Threshold: {threshold}\n")
    
    def read_raw(self):
        """Read raw ADC value (0-65535)"""
        self.last_reading = self.adc.read_u16()
        return self.last_reading
    
    def read_percentage(self):
        """Read gas level as percentage (0-100%)"""
        raw = self.read_raw()
        return (raw / 65535) * 100
    
    def is_gas_detected(self):
        """Check if gas level exceeds threshold"""
        return self.read_raw() > self.threshold
    
    def check_alert(self):
        """Check and update alert LED"""
        if self.is_gas_detected():
            self.alert_led.value(1)  # Turn on alert
            return True
        else:
            self.alert_led.value(0)  # Turn off alert
            return False
    
    def print_reading(self):
        """Print current gas sensor reading"""
        raw = self.read_raw()
        percentage = (raw / 65535) * 100
        alert = "⚠️ ALERT!" if raw > self.threshold else "✓ Normal"
        print(f"Gas Level: {raw:5d} ({percentage:5.2f}%) - {alert}")
        return raw
    
    def set_threshold(self, threshold):
        """Set new threshold value"""
        if 0 <= threshold <= 65535:
            self.threshold = threshold
            print(f"Threshold set to: {threshold}")
            return True
        else:
            print("Error: Threshold must be between 0 and 65535")
            return False
    
    def cleanup(self):
        """Clean up gas sensor"""
        self.alert_led.value(0)
        print("Gas sensor cleanup complete.")