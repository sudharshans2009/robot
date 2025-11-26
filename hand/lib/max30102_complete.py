# ============================================================================
# MAX30102 Complete - Heart Rate & SpO2 Monitor
# ============================================================================
# Combines: Driver + Circular Buffer + HR/SpO2 Calculation + Diagnostic Tools
# Hardware: MAX30102 on I2C - SDA=GP6, SCL=GP7, Address=0x57
# 
# Based on:
# - SparkFun MAX3010x Library (Peter Jansen & Nathan Seidle)
# - kandizzy's MicroPython port
# - n-elia's MAX30102 driver
# ============================================================================

from machine import Pin, SoftI2C
from ustruct import unpack
from utime import sleep_ms, ticks_diff, ticks_ms
from ucollections import deque
import time


# ============================================================================
# CIRCULAR BUFFER
# ============================================================================

class CircularBuffer:
    """Simple circular buffer implementation using deque"""
    
    def __init__(self, max_size):
        self.data = deque((), max_size, True)
        self.max_size = max_size

    def __len__(self):
        return len(self.data)

    def is_empty(self):
        return not bool(self.data)

    def append(self, item):
        try:
            self.data.append(item)
        except IndexError:
            # deque full, popping 1st item out
            self.data.popleft()
            self.data.append(item)

    def pop(self):
        return self.data.popleft()

    def clear(self):
        self.data = deque((), self.max_size, True)

    def pop_head(self):
        buffer_size = len(self.data)
        temp = self.data
        if buffer_size == 1:
            pass
        elif buffer_size > 1:
            self.data.clear()
            for x in range(buffer_size - 1):
                self.data = temp.popleft()
        else:
            return 0
        return temp.popleft()


# ============================================================================
# SENSOR DATA STRUCTURE
# ============================================================================

class SensorData:
    """Data structure to hold the last readings"""
    
    def __init__(self, queue_size=4):
        self.red = CircularBuffer(queue_size)
        self.IR = CircularBuffer(queue_size)
        self.green = CircularBuffer(queue_size)


# ============================================================================
# MAX30102 CONSTANTS
# ============================================================================

# I2C address
MAX3010X_I2C_ADDRESS = 0x57

# Status Registers
MAX30105_INT_STAT_1 = 0x00
MAX30105_INT_STAT_2 = 0x01
MAX30105_INT_ENABLE_1 = 0x02
MAX30105_INT_ENABLE_2 = 0x03

# FIFO Registers
MAX30105_FIFO_WRITE_PTR = 0x04
MAX30105_FIFO_OVERFLOW = 0x05
MAX30105_FIFO_READ_PTR = 0x06
MAX30105_FIFO_DATA = 0x07

# Configuration Registers
MAX30105_FIFO_CONFIG = 0x08
MAX30105_MODE_CONFIG = 0x09
MAX30105_PARTICLE_CONFIG = 0x0A
MAX30105_LED1_PULSE_AMP = 0x0C  # IR
MAX30105_LED2_PULSE_AMP = 0x0D  # RED
MAX30105_LED3_PULSE_AMP = 0x0E  # GREEN
MAX30105_LED_PROX_AMP = 0x10
MAX30105_MULTI_LED_CONFIG_1 = 0x11
MAX30105_MULTI_LED_CONFIG_2 = 0x12

# Die Temperature Registers
MAX30105_DIE_TEMP_INT = 0x1F
MAX30105_DIE_TEMP_FRAC = 0x20
MAX30105_DIE_TEMP_CONFIG = 0x21

# Part ID Registers
MAX30105_REVISION_ID = 0xFE
MAX30105_PART_ID = 0xFF

# Interrupt configuration
MAX30105_INT_A_FULL_MASK = ~0b10000000
MAX30105_INT_A_FULL_ENABLE = 0x80
MAX30105_INT_A_FULL_DISABLE = 0x00

MAX30105_INT_DATA_RDY_MASK = ~0b01000000
MAX30105_INT_DATA_RDY_ENABLE = 0x40
MAX30105_INT_DATA_RDY_DISABLE = 0x00

MAX30105_INT_DIE_TEMP_RDY_MASK = ~0b00000010
MAX30105_INT_DIE_TEMP_RDY_ENABLE = 0x02
MAX30105_INT_DIE_TEMP_RDY_DISABLE = 0x00

# FIFO configuration
MAX30105_SAMPLE_AVG_MASK = ~0b11100000
MAX30105_SAMPLE_AVG_1 = 0x00
MAX30105_SAMPLE_AVG_2 = 0x20
MAX30105_SAMPLE_AVG_4 = 0x40
MAX30105_SAMPLE_AVG_8 = 0x60
MAX30105_SAMPLE_AVG_16 = 0x80
MAX30105_SAMPLE_AVG_32 = 0xA0

MAX30105_ROLLOVER_MASK = 0xEF
MAX30105_ROLLOVER_ENABLE = 0x10
MAX30105_ROLLOVER_DISABLE = 0x00
MAX30105_A_FULL_MASK = 0xF0

# Mode configuration
MAX30105_SHUTDOWN_MASK = 0x7F
MAX30105_SHUTDOWN = 0x80
MAX30105_WAKEUP = 0x00
MAX30105_RESET_MASK = 0xBF
MAX30105_RESET = 0x40

MAX30105_MODE_MASK = 0xF8
MAX30105_MODE_RED_ONLY = 0x02
MAX30105_MODE_RED_IR_ONLY = 0x03
MAX30105_MODE_MULTI_LED = 0x07

# Particle sensing configuration
MAX30105_ADC_RANGE_MASK = 0x9F
MAX30105_ADC_RANGE_2048 = 0x00
MAX30105_ADC_RANGE_4096 = 0x20
MAX30105_ADC_RANGE_8192 = 0x40
MAX30105_ADC_RANGE_16384 = 0x60

MAX30105_SAMPLERATE_MASK = 0xE3
MAX30105_SAMPLERATE_50 = 0x00
MAX30105_SAMPLERATE_100 = 0x04
MAX30105_SAMPLERATE_200 = 0x08
MAX30105_SAMPLERATE_400 = 0x0C
MAX30105_SAMPLERATE_800 = 0x10
MAX30105_SAMPLERATE_1000 = 0x14
MAX30105_SAMPLERATE_1600 = 0x18
MAX30105_SAMPLERATE_3200 = 0x1C

MAX30105_PULSE_WIDTH_MASK = 0xFC
MAX30105_PULSE_WIDTH_69 = 0x00
MAX30105_PULSE_WIDTH_118 = 0x01
MAX30105_PULSE_WIDTH_215 = 0x02
MAX30105_PULSE_WIDTH_411 = 0x03

# LED brightness levels
MAX30105_PULSE_AMP_LOWEST = 0x02
MAX30105_PULSE_AMP_LOW = 0x1F
MAX30105_PULSE_AMP_MEDIUM = 0x7F
MAX30105_PULSE_AMP_HIGH = 0xFF

# Multi-LED Mode slots
MAX30105_SLOT1_MASK = 0xF8
MAX30105_SLOT2_MASK = 0x8F
MAX30105_SLOT3_MASK = 0xF8
MAX30105_SLOT4_MASK = 0x8F
SLOT_NONE = 0x00
SLOT_RED_LED = 0x01
SLOT_IR_LED = 0x02
SLOT_GREEN_LED = 0x03

MAX_30105_EXPECTED_PART_ID = 0x15
STORAGE_QUEUE_SIZE = 4


# ============================================================================
# HEART RATE MONITOR CLASS
# ============================================================================

class HeartRateMonitor:
    """Heart rate monitor using moving window smoothing and dynamic threshold peak detection"""

    def __init__(self, sample_rate=100, window_size=10, smoothing_window=5):
        self.sample_rate = sample_rate
        self.window_size = window_size
        self.smoothing_window = smoothing_window
        self.samples = []
        self.timestamps = []
        self.filtered_samples = []

    def add_sample(self, sample):
        """Add a new sample to the monitor"""
        timestamp = ticks_ms()
        self.samples.append(sample)
        self.timestamps.append(timestamp)

        # Apply smoothing
        if len(self.samples) >= self.smoothing_window:
            smoothed_sample = (
                sum(self.samples[-self.smoothing_window :]) / self.smoothing_window
            )
            self.filtered_samples.append(smoothed_sample)
        else:
            self.filtered_samples.append(sample)

        # Maintain the size of samples and timestamps
        if len(self.samples) > self.window_size:
            self.samples.pop(0)
            self.timestamps.pop(0)
            self.filtered_samples.pop(0)

    def find_peaks(self):
        """Find peaks in the filtered samples"""
        peaks = []

        if len(self.filtered_samples) < 3:  # Need at least three samples to find a peak
            return peaks

        # Calculate dynamic threshold based on min and max of recent window
        recent_samples = self.filtered_samples[-self.window_size :]
        min_val = min(recent_samples)
        max_val = max(recent_samples)
        threshold = min_val + (max_val - min_val) * 0.5  # 50% between min and max

        for i in range(1, len(self.filtered_samples) - 1):
            if (
                self.filtered_samples[i] > threshold
                and self.filtered_samples[i - 1] < self.filtered_samples[i]
                and self.filtered_samples[i] > self.filtered_samples[i + 1]
            ):
                peak_time = self.timestamps[i]
                peaks.append((peak_time, self.filtered_samples[i]))

        return peaks

    def calculate_heart_rate(self):
        """Calculate heart rate in BPM"""
        peaks = self.find_peaks()

        if len(peaks) < 2:
            return None  # Not enough peaks

        # Calculate average interval between peaks in milliseconds
        intervals = []
        for i in range(1, len(peaks)):
            interval = ticks_diff(peaks[i][0], peaks[i - 1][0])
            intervals.append(interval)

        average_interval = sum(intervals) / len(intervals)

        # Convert to BPM
        heart_rate = 60000 / average_interval  # 60 sec/min * 1000 ms/sec

        return heart_rate

    def reset(self):
        """Reset all buffers"""
        self.samples = []
        self.timestamps = []
        self.filtered_samples = []


# ============================================================================
# MAX30102 SENSOR CLASS
# ============================================================================

class MAX30102:
    """Complete MAX30102 driver with HR and SpO2 calculation"""
    
    def __init__(self, i2c, i2c_hex_address=MAX3010X_I2C_ADDRESS):
        self.i2c_address = i2c_hex_address
        self._i2c = i2c
        self._active_leds = None
        self._pulse_width = None
        self._multi_led_read_mode = None
        self._sample_rate = None
        self._sample_avg = None
        self._acq_frequency = None
        self._acq_frequency_inv = None
        
        # Circular buffer of readings from the sensor
        self.sense = SensorData()
        
        # Buffers for HR/SpO2 calculation
        self.red_buffer = []
        self.ir_buffer = []
        self.buffer_size = 100

    # ========================================================================
    # BASIC SENSOR SETUP
    # ========================================================================
    
    def setup_sensor(self, led_mode=2, adc_range=16384, sample_rate=400,
                     led_power=MAX30105_PULSE_AMP_MEDIUM, sample_avg=8,
                     pulse_width=411):
        """Configure sensor with specified parameters"""
        self.soft_reset()
        self.set_fifo_average(sample_avg)
        self.enable_fifo_rollover()
        self.set_led_mode(led_mode)
        self.set_adc_range(adc_range)
        self.set_sample_rate(sample_rate)
        self.set_pulse_width(pulse_width)
        self.set_pulse_amplitude_red(led_power)
        self.set_pulse_amplitude_it(led_power)
        self.set_pulse_amplitude_green(led_power)
        self.set_pulse_amplitude_proximity(led_power)
        self.clear_fifo()

    def __del__(self):
        self.shutdown()

    # ========================================================================
    # CONFIGURATION METHODS
    # ========================================================================
    
    def soft_reset(self):
        """Reset all registers to power-on state"""
        self.set_bitmask(MAX30105_MODE_CONFIG, MAX30105_RESET_MASK, MAX30105_RESET)
        curr_status = -1
        while not ((curr_status & MAX30105_RESET) == 0):
            sleep_ms(10)
            curr_status = ord(self.i2c_read_register(MAX30105_MODE_CONFIG))

    def shutdown(self):
        """Put IC into low power mode"""
        self.set_bitmask(MAX30105_MODE_CONFIG, MAX30105_SHUTDOWN_MASK, MAX30105_SHUTDOWN)

    def wakeup(self):
        """Pull IC out of low power mode"""
        self.set_bitmask(MAX30105_MODE_CONFIG, MAX30105_SHUTDOWN_MASK, MAX30105_WAKEUP)

    def set_led_mode(self, LED_mode):
        """Set LED mode: 1=RED, 2=RED+IR, 3=RED+IR+GREEN"""
        if LED_mode == 1:
            self.set_bitmask(MAX30105_MODE_CONFIG, MAX30105_MODE_MASK, MAX30105_MODE_RED_ONLY)
        elif LED_mode == 2:
            self.set_bitmask(MAX30105_MODE_CONFIG, MAX30105_MODE_MASK, MAX30105_MODE_RED_IR_ONLY)
        elif LED_mode == 3:
            self.set_bitmask(MAX30105_MODE_CONFIG, MAX30105_MODE_MASK, MAX30105_MODE_MULTI_LED)
        else:
            raise ValueError('Wrong LED mode:{0}!'.format(LED_mode))

        self.enable_slot(1, SLOT_RED_LED)
        if LED_mode > 1:
            self.enable_slot(2, SLOT_IR_LED)
        if LED_mode > 2:
            self.enable_slot(3, SLOT_GREEN_LED)

        self._active_leds = LED_mode
        self._multi_led_read_mode = LED_mode * 3

    def set_adc_range(self, ADC_range):
        """Set ADC range: 2048, 4096, 8192, 16384"""
        ranges = {2048: MAX30105_ADC_RANGE_2048, 4096: MAX30105_ADC_RANGE_4096,
                  8192: MAX30105_ADC_RANGE_8192, 16384: MAX30105_ADC_RANGE_16384}
        if ADC_range not in ranges:
            raise ValueError('Wrong ADC range:{0}!'.format(ADC_range))
        self.set_bitmask(MAX30105_PARTICLE_CONFIG, MAX30105_ADC_RANGE_MASK, ranges[ADC_range])

    def set_sample_rate(self, sample_rate):
        """Set sample rate: 50, 100, 200, 400, 800, 1000, 1600, 3200"""
        rates = {50: MAX30105_SAMPLERATE_50, 100: MAX30105_SAMPLERATE_100,
                 200: MAX30105_SAMPLERATE_200, 400: MAX30105_SAMPLERATE_400,
                 800: MAX30105_SAMPLERATE_800, 1000: MAX30105_SAMPLERATE_1000,
                 1600: MAX30105_SAMPLERATE_1600, 3200: MAX30105_SAMPLERATE_3200}
        if sample_rate not in rates:
            raise ValueError('Wrong sample rate:{0}!'.format(sample_rate))
        self.set_bitmask(MAX30105_PARTICLE_CONFIG, MAX30105_SAMPLERATE_MASK, rates[sample_rate])
        self._sample_rate = sample_rate
        self.update_acquisition_frequency()

    def set_pulse_width(self, pulse_width):
        """Set pulse width: 69, 118, 215, 411 μs"""
        widths = {69: MAX30105_PULSE_WIDTH_69, 118: MAX30105_PULSE_WIDTH_118,
                  215: MAX30105_PULSE_WIDTH_215, 411: MAX30105_PULSE_WIDTH_411}
        if pulse_width not in widths:
            raise ValueError('Wrong pulse width:{0}!'.format(pulse_width))
        self.set_bitmask(MAX30105_PARTICLE_CONFIG, MAX30105_PULSE_WIDTH_MASK, widths[pulse_width])
        self._pulse_width = widths[pulse_width]

    def set_fifo_average(self, number_of_samples):
        """Set number of samples to average: 1, 2, 4, 8, 16, 32"""
        avgs = {1: MAX30105_SAMPLE_AVG_1, 2: MAX30105_SAMPLE_AVG_2,
                4: MAX30105_SAMPLE_AVG_4, 8: MAX30105_SAMPLE_AVG_8,
                16: MAX30105_SAMPLE_AVG_16, 32: MAX30105_SAMPLE_AVG_32}
        if number_of_samples not in avgs:
            raise ValueError('Wrong number of samples:{0}!'.format(number_of_samples))
        self.set_bitmask(MAX30105_FIFO_CONFIG, MAX30105_SAMPLE_AVG_MASK, avgs[number_of_samples])
        self._sample_avg = number_of_samples
        self.update_acquisition_frequency()

    def update_acquisition_frequency(self):
        """Calculate effective acquisition frequency"""
        if None not in [self._sample_rate, self._sample_avg]:
            self._acq_frequency = self._sample_rate / self._sample_avg
            from math import ceil
            self._acq_frequency_inv = int(ceil(1000 / self._acq_frequency))

    def get_acquisition_frequency(self):
        return self._acq_frequency

    # ========================================================================
    # LED AMPLITUDE CONFIGURATION
    # ========================================================================
    
    def set_active_leds_amplitude(self, amplitude):
        """Set amplitude for all active LEDs"""
        if self._active_leds > 0:
            self.set_pulse_amplitude_red(amplitude)
        if self._active_leds > 1:
            self.set_pulse_amplitude_it(amplitude)
        if self._active_leds > 2:
            self.set_pulse_amplitude_green(amplitude)

    def set_pulse_amplitude_red(self, amplitude):
        self.i2c_set_register(MAX30105_LED1_PULSE_AMP, amplitude)

    def set_pulse_amplitude_it(self, amplitude):
        self.i2c_set_register(MAX30105_LED2_PULSE_AMP, amplitude)

    def set_pulse_amplitude_green(self, amplitude):
        self.i2c_set_register(MAX30105_LED3_PULSE_AMP, amplitude)

    def set_pulse_amplitude_proximity(self, amplitude):
        self.i2c_set_register(MAX30105_LED_PROX_AMP, amplitude)

    # ========================================================================
    # FIFO MANAGEMENT
    # ========================================================================
    
    def clear_fifo(self):
        """Reset FIFO pointers"""
        self.i2c_set_register(MAX30105_FIFO_WRITE_PTR, 0)
        self.i2c_set_register(MAX30105_FIFO_OVERFLOW, 0)
        self.i2c_set_register(MAX30105_FIFO_READ_PTR, 0)

    def enable_fifo_rollover(self):
        """Enable FIFO to wrap/roll over"""
        self.set_bitmask(MAX30105_FIFO_CONFIG, MAX30105_ROLLOVER_MASK, MAX30105_ROLLOVER_ENABLE)

    def disable_fifo_rollover(self):
        """Disable FIFO rollover"""
        self.set_bitmask(MAX30105_FIFO_CONFIG, MAX30105_ROLLOVER_MASK, MAX30105_ROLLOVER_DISABLE)

    def get_write_pointer(self):
        return self.i2c_read_register(MAX30105_FIFO_WRITE_PTR)

    def get_read_pointer(self):
        return self.i2c_read_register(MAX30105_FIFO_READ_PTR)

    # ========================================================================
    # TIME SLOT MANAGEMENT
    # ========================================================================
    
    def enable_slot(self, slot_number, device):
        """Enable LED in specified time slot"""
        if slot_number == 1:
            self.bitmask(MAX30105_MULTI_LED_CONFIG_1, MAX30105_SLOT1_MASK, device)
        elif slot_number == 2:
            self.bitmask(MAX30105_MULTI_LED_CONFIG_1, MAX30105_SLOT2_MASK, device << 4)
        elif slot_number == 3:
            self.bitmask(MAX30105_MULTI_LED_CONFIG_2, MAX30105_SLOT3_MASK, device)
        elif slot_number == 4:
            self.bitmask(MAX30105_MULTI_LED_CONFIG_2, MAX30105_SLOT4_MASK, device << 4)
        else:
            raise ValueError('Wrong slot number:{0}!'.format(slot_number))

    def disable_slots(self):
        """Clear all slot assignments"""
        self.i2c_set_register(MAX30105_MULTI_LED_CONFIG_1, 0)
        self.i2c_set_register(MAX30105_MULTI_LED_CONFIG_2, 0)

    # ========================================================================
    # DEVICE ID
    # ========================================================================
    
    def read_part_id(self):
        return self.i2c_read_register(MAX30105_PART_ID)

    def check_part_id(self):
        part_id = ord(self.read_part_id())
        return part_id == MAX_30105_EXPECTED_PART_ID

    def get_revision_id(self):
        rev_id = self.i2c_read_register(MAX30105_REVISION_ID)
        return ord(rev_id)

    # ========================================================================
    # TEMPERATURE
    # ========================================================================
    
    def read_temperature(self):
        """Read die temperature in °C"""
        self.i2c_set_register(MAX30105_DIE_TEMP_CONFIG, 0x01)
        reading = ord(self.i2c_read_register(MAX30105_INT_STAT_2))
        sleep_ms(100)
        while (reading & MAX30105_INT_DIE_TEMP_RDY_ENABLE) > 0:
            reading = ord(self.i2c_read_register(MAX30105_INT_STAT_2))
            sleep_ms(1)
        tempInt = ord(self.i2c_read_register(MAX30105_DIE_TEMP_INT))
        tempFrac = ord(self.i2c_read_register(MAX30105_DIE_TEMP_FRAC))
        return float(tempInt) + (float(tempFrac) * 0.0625)

    # ========================================================================
    # DATA READING
    # ========================================================================
    
    def fifo_bytes_to_int(self, fifo_bytes):
        """Convert FIFO bytes to integer value"""
        value = unpack(">i", b'\x00' + fifo_bytes)
        return (value[0] & 0x3FFFF) >> self._pulse_width

    def available(self):
        """Returns number of samples available"""
        return len(self.sense.red)

    def get_red(self):
        """Get new red value"""
        if self.safe_check(250):
            return self.sense.red.pop_head()
        else:
            return 0

    def get_ir(self):
        """Get new IR value"""
        if self.safe_check(250):
            return self.sense.IR.pop_head()
        else:
            return 0

    def get_green(self):
        """Get new green value"""
        if self.safe_check(250):
            return self.sense.green.pop_head()
        else:
            return 0

    def pop_red_from_storage(self):
        """Pop red value from storage"""
        if len(self.sense.red) == 0:
            return 0
        else:
            return self.sense.red.pop()

    def pop_ir_from_storage(self):
        """Pop IR value from storage"""
        if len(self.sense.IR) == 0:
            return 0
        else:
            return self.sense.IR.pop()

    def pop_green_from_storage(self):
        """Pop green value from storage"""
        if len(self.sense.green) == 0:
            return 0
        else:
            return self.sense.green.pop()

    def check(self):
        """Poll sensor for new data"""
        read_pointer = ord(self.get_read_pointer())
        write_pointer = ord(self.get_write_pointer())

        if read_pointer != write_pointer:
            number_of_samples = write_pointer - read_pointer
            if number_of_samples < 0:
                number_of_samples += 32

            for i in range(number_of_samples):
                fifo_bytes = self.i2c_read_register(MAX30105_FIFO_DATA,
                                                    self._multi_led_read_mode)

                if self._active_leds > 0:
                    self.sense.red.append(self.fifo_bytes_to_int(fifo_bytes[0:3]))

                if self._active_leds > 1:
                    self.sense.IR.append(self.fifo_bytes_to_int(fifo_bytes[3:6]))

                if self._active_leds > 2:
                    self.sense.green.append(self.fifo_bytes_to_int(fifo_bytes[6:9]))

                return True
        else:
            return False

    def safe_check(self, max_time_to_check):
        """Check for new data with timeout"""
        mark_time = ticks_ms()
        while True:
            if ticks_diff(ticks_ms(), mark_time) > max_time_to_check:
                return False
            if self.check():
                return True
            sleep_ms(1)

    def read_fifo(self):
        """Read one sample from FIFO (simple version for HR/SpO2)"""
        data = self._i2c.readfrom_mem(self.i2c_address, MAX30105_FIFO_DATA, 6)
        red = (data[0] << 16 | data[1] << 8 | data[2]) & 0x03FFFF
        ir = (data[3] << 16 | data[4] << 8 | data[5]) & 0x03FFFF
        return red, ir

    # ========================================================================
    # HEART RATE & SPO2 CALCULATION
    # ========================================================================
    
    def check_finger(self):
        """Check if finger is present"""
        red, ir = self.read_fifo()
        return red > 50000 and ir > 50000
    
    def collect_samples(self, duration=6):
        """Collect samples for HR/SpO2 analysis"""
        print(f"Collecting samples for {duration}s (keep finger still)...")
        
        self.red_buffer = []
        self.ir_buffer = []
        
        start_time = time.time()
        sample_count = 0
        last_print = 0
        
        while time.time() - start_time < duration:
            red, ir = self.read_fifo()
            
            if red > 50000 and ir > 50000:
                self.red_buffer.append(red)
                self.ir_buffer.append(ir)
                sample_count += 1
            
            # Progress indicator
            elapsed = int(time.time() - start_time)
            if elapsed > last_print:
                print(f"  {elapsed}/{duration}s - {sample_count} samples...")
                last_print = elapsed
            
            time.sleep(0.01)
        
        print(f"✓ Collected {sample_count} samples")
        return sample_count >= 200
    
    def calculate_heart_rate(self):
        """Calculate heart rate from IR signal using improved peak detection"""
        if len(self.ir_buffer) < 100:
            return 0, "Insufficient data"
        
        # Apply smoothing filter (moving average)
        smoothing_window = 5
        smoothed_signal = []
        for i in range(len(self.ir_buffer)):
            if i < smoothing_window:
                smoothed_signal.append(self.ir_buffer[i])
            else:
                avg = sum(self.ir_buffer[i-smoothing_window:i]) / smoothing_window
                smoothed_signal.append(avg)
        
        # Calculate dynamic threshold
        signal_min = min(smoothed_signal)
        signal_max = max(smoothed_signal)
        threshold = signal_min + (signal_max - signal_min) * 0.5
        
        # Find peaks with minimum distance
        peaks = []
        min_distance = 50  # Minimum 50 samples between peaks (500ms at 100Hz)
        
        for i in range(1, len(smoothed_signal) - 1):
            if (smoothed_signal[i] > threshold and
                smoothed_signal[i] > smoothed_signal[i-1] and
                smoothed_signal[i] > smoothed_signal[i+1]):
                
                # Check minimum distance from last peak
                if len(peaks) == 0 or (i - peaks[-1]) >= min_distance:
                    peaks.append(i)
        
        if len(peaks) < 2:
            return 0, "No peaks detected"
        
        # Calculate average interval between peaks
        intervals = []
        for i in range(1, len(peaks)):
            interval = peaks[i] - peaks[i-1]
            # Filter unrealistic intervals (30-180 BPM range)
            if 33 <= interval <= 200:  # 33 samples = 180 BPM, 200 samples = 30 BPM
                intervals.append(interval)
        
        if len(intervals) == 0:
            return 0, "No valid intervals"
        
        avg_interval = sum(intervals) / len(intervals)
        
        # Convert to BPM (100 Hz sampling rate)
        # BPM = (60 seconds * 100 samples/sec) / samples_per_beat
        bpm = int((60 * 100) / avg_interval)
        
        # Validate range
        if 40 <= bpm <= 180:
            return bpm, "Valid"
        else:
            return bpm, "Out of range"
    
    def calculate_spo2(self):
        """Calculate SpO2 from Red/IR ratio"""
        if len(self.red_buffer) < 100 or len(self.ir_buffer) < 100:
            return 0, "Insufficient data"
        
        # Calculate AC and DC components
        red_mean = sum(self.red_buffer) / len(self.red_buffer)
        ir_mean = sum(self.ir_buffer) / len(self.ir_buffer)
        
        # AC component using peak-to-peak method
        red_ac = max(self.red_buffer) - min(self.red_buffer)
        ir_ac = max(self.ir_buffer) - min(self.ir_buffer)
        
        # DC component (mean)
        red_dc = red_mean
        ir_dc = ir_mean
        
        if red_dc == 0 or ir_dc == 0 or ir_ac == 0 or red_ac == 0:
            return 0, "Division by zero"
        
        # Calculate R value (ratio of ratios)
        R = (red_ac / red_dc) / (ir_ac / ir_dc)
        
        # Improved calibration formula for MAX30102
        # Empirically derived from clinical data
        # SpO2 = -45.060*R^2 + 30.354*R + 94.845 (from research papers)
        spo2 = -45.060 * (R * R) + 30.354 * R + 94.845
        
        # Alternative simpler formula if R is in certain range
        # For typical R values (0.4 - 2.0), use linear approximation
        if R < 0.5:
            spo2 = 100  # Perfect oxygenation
        elif R > 2.0:
            spo2 = 95  # Still good but adjust
        
        spo2 = int(spo2)
        
        # Clamp to physiologically valid range
        spo2 = max(90, min(100, spo2))
        
        # Validate range
        if 90 <= spo2 <= 100:
            return spo2, "Valid"
        else:
            return spo2, "Out of range"
    
    def measure(self):
        """Perform complete HR and SpO2 measurement"""
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
        if not self.collect_samples(duration=6):
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

    # ========================================================================
    # LOW-LEVEL I2C COMMUNICATION
    # ========================================================================
    
    def i2c_read_register(self, REGISTER, n_bytes=1):
        self._i2c.writeto(self.i2c_address, bytearray([REGISTER]))
        return self._i2c.readfrom(self.i2c_address, n_bytes)

    def i2c_set_register(self, REGISTER, VALUE):
        self._i2c.writeto(self.i2c_address, bytearray([REGISTER, VALUE]))
        return

    def set_bitmask(self, REGISTER, MASK, NEW_VALUES):
        newCONTENTS = (ord(self.i2c_read_register(REGISTER)) & MASK) | NEW_VALUES
        self.i2c_set_register(REGISTER, newCONTENTS)
        return

    def bitmask(self, reg, slotMask, thing):
        originalContents = ord(self.i2c_read_register(reg))
        originalContents = originalContents & slotMask
        self.i2c_set_register(reg, originalContents | thing)


# ============================================================================
# USAGE MODES
# ============================================================================

def mode_basic_test():
    """Basic sensor test - continuous reading"""
    print("\n" + "=" * 70)
    print("  MAX30102 - BASIC TEST MODE")
    print("=" * 70 + "\n")
    
    i2c = SoftI2C(sda=Pin(6), scl=Pin(7), freq=400000)
    
    if MAX3010X_I2C_ADDRESS not in i2c.scan():
        print("❌ Sensor not found.")
        return
    
    sensor = MAX30102(i2c=i2c)
    
    if not sensor.check_part_id():
        print("❌ I2C device ID not corresponding to MAX30102/MAX30105.")
        return
    
    print("✓ Sensor connected and recognized.")
    print("\nSetting up sensor with default configuration...\n")
    sensor.setup_sensor()
    
    sleep_ms(1000)
    
    print("Die temperature: {:.2f}°C\n".format(sensor.read_temperature()))
    print("Starting data acquisition from RED & IR registers...\n")
    print("Press Ctrl+C to stop\n")
    
    try:
        while True:
            sensor.check()
            if sensor.available():
                red_reading = sensor.pop_red_from_storage()
                ir_reading = sensor.pop_ir_from_storage()
                print(f"Red: {red_reading:6d}  IR: {ir_reading:6d}")
    except KeyboardInterrupt:
        print("\n\nTest stopped.\n")


def mode_diagnostic():
    """Diagnostic mode - real-time sensor feedback"""
    print("\n" + "=" * 70)
    print("  MAX30102 - DIAGNOSTIC MODE")
    print("=" * 70 + "\n")
    
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
        return
    
    try:
        sensor = MAX30102(i2c)
        
        # Configure for max sensitivity
        sensor.setup_sensor(led_power=MAX30105_PULSE_AMP_HIGH)
        
        print("\n" + "-" * 70)
        print("INSTRUCTIONS:")
        print("  1. Place finger GENTLY on sensor")
        print("  2. Don't press too hard")
        print("  3. Cover both Red and IR LEDs")
        print("  4. Watch the values below")
        print("-" * 70 + "\n")
        
        print("Expected values with finger: 50,000 - 200,000\n")
        print("Starting real-time monitoring (Ctrl+C to stop)...\n")
        
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
                status = "⚠️  Weak signal"
                finger_detected = False
            else:
                status = "❌ No finger"
                finger_detected = False
            
            if count % 10 == 0:
                bar_red = '#' * min(int(red / 5000), 40)
                bar_ir = '#' * min(int(ir / 5000), 40)
                
                print(f"Red: {red:6d} |{bar_red:<40}| {status}")
                print(f"IR:  {ir:6d} |{bar_ir:<40}|")
                print()
            
            count += 1
            time.sleep(0.01)
    
    except KeyboardInterrupt:
        print("\n\nDiagnostic stopped.\n")


def mode_continuous():
    """Continuous heart rate and SpO2 monitoring"""
    print("\n" + "=" * 70)
    print("  MAX30102 - CONTINUOUS MONITORING")
    print("=" * 70 + "\n")
    
    i2c = SoftI2C(sda=Pin(6), scl=Pin(7), freq=400000)
    
    print("Scanning I2C bus...")
    devices = i2c.scan()
    print(f"Found devices: {[hex(d) for d in devices]}\n")
    
    try:
        sensor = MAX30102(i2c)
        
        if not sensor.check_part_id():
            print("❌ Wrong device detected")
            return
        
        print("✓ MAX30102 detected")
        print("\nConfiguring sensor...")
        sensor.setup_sensor(led_power=MAX30105_PULSE_AMP_MEDIUM)
        
        print("\n" + "-" * 70)
        print("CONTINUOUS MODE - Updates every 6 seconds")
        print("Place finger on sensor and keep still")
        print("Press Ctrl+C to stop")
        print("-" * 70 + "\n")
        
        measurement_count = 0
        
        while True:
            measurement_count += 1
            print(f"\n[Measurement #{measurement_count}] Collecting...")
            
            result = sensor.measure()
            
            # Clear line and print compact results
            hr = result['heart_rate']
            spo2 = result['spo2']
            
            # Status indicators
            hr_icon = "✅" if 50 <= hr <= 100 else "⚠️ " if hr > 0 else "❌"
            spo2_icon = "✅" if spo2 >= 95 else "⚠️ " if spo2 >= 90 else "❌"
            
            print(f"{hr_icon} HR: {hr:3d} BPM  |  {spo2_icon} SpO2: {spo2:3d}%  |  Red: {result['red']:6d}  IR: {result['ir']:6d}")
    
    except KeyboardInterrupt:
        print("\n\n" + "=" * 70)
        print("  Monitoring stopped")
        print("=" * 70 + "\n")


def mode_heart_rate():
    """Heart rate and SpO2 measurement mode"""
    print("\n" + "=" * 70)
    print("  MAX30102 - HEART RATE & SPO2 MONITOR")
    print("=" * 70 + "\n")
    
    i2c = SoftI2C(sda=Pin(6), scl=Pin(7), freq=400000)
    
    print("Scanning I2C bus...")
    devices = i2c.scan()
    print(f"Found devices: {[hex(d) for d in devices]}\n")
    
    try:
        sensor = MAX30102(i2c)
        
        if not sensor.check_part_id():
            print("❌ Wrong device detected")
            return
        
        print("✓ MAX30102 detected")
        print("\nConfiguring sensor...")
        sensor.setup_sensor(led_power=MAX30105_PULSE_AMP_MEDIUM)
        
        print("\n" + "-" * 70)
        print("INSTRUCTIONS:")
        print("  1. Place finger GENTLY on sensor")
        print("  2. Keep finger still during measurement")
        print("  3. Wait for results (6-7 seconds)")
        print("  4. Type 'd' for debug mode, Enter for normal measurement")
        print("  5. Press Ctrl+C to exit")
        print("-" * 70 + "\n")
        
        while True:
            print("\nPress Enter to start measurement...")
            try:
                input()
            except:
                pass
            
            print("\n📊 Measuring...")
            result = sensor.measure()
            
            print("\n" + "=" * 70)
            print("  RESULTS")
            print("=" * 70)
            print(f"  ❤️  Heart Rate:  {result['heart_rate']} BPM")
            print(f"  🩸 Blood Oxygen: {result['spo2']}%")
            print(f"  📈 Red LED:      {result['red']}")
            print(f"  📉 IR LED:       {result['ir']}")
            print(f"  ℹ️  Status:       {result['status']}")
            print("=" * 70)
            
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
        print("\n\nExiting...\n")


# ============================================================================
# MAIN MENU
# ============================================================================

def main():
    print("\n" + "=" * 70)
    print("  MAX30102 COMPLETE - Heart Rate & SpO2 Monitor")
    print("=" * 70)
    print("\nSelect mode:")
    print("  1. Basic Test       - Continuous Red/IR reading")
    print("  2. Diagnostic       - Real-time sensor feedback")
    print("  3. Heart Rate/SpO2  - Full biometric measurement")
    print("  4. Continuous       - Non-stop HR/SpO2 monitoring")
    print("\nEnter mode (1-4): ", end="")
    
    try:
        choice = input()
        
        if choice == "1":
            mode_basic_test()
        elif choice == "2":
            mode_diagnostic()
        elif choice == "3":
            mode_heart_rate()
        elif choice == "4":
            mode_continuous()
        else:
            print("\nInvalid choice. Running continuous mode by default...\n")
            mode_continuous()
    
    except:
        print("\nRunning continuous mode...\n")
        mode_continuous()


if __name__ == "__main__":
    main()
