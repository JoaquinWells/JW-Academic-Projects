'''
=======================================================
Joaquin Wells  |  jwells32@student.cnm.edu
CIST1605 - IoT 
Final Project
Automated Houseplant Microclimate Monitoring System
======================================================= 
'''

import RPi.GPIO as gpio
import time
import threading
import yaml
import smbus2
import board
import adafruit_dht
from Adafruit_IO import MQTTClient

# ANSI COLOR constants for terminal output
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
CYAN = "\033[36m"
RESET = "\033[0m"


# ===============================================
# LOAD ADAFRUIT IO LOGIN CREDENTIALS
# ===============================================

try:
    with open('config.yml', 'r') as fp:
        config = yaml.safe_load(fp)
    ada_username = config['adafruit']['username']
    ada_key = config['adafruit']['key']
except Exception as e:
    print(f"{RED}ERROR: Failed to load config.yml: {e}{RESET}")
    exit(1)


# ===============================================
# HARDWARE & GPIO PIN CONFIGURATION
# ===============================================

# RGB LED Pins
GREEN_PIN = 17
RED_PIN   = 27
BLUE_PIN  = 22

# Direct GPIO Digital Inputs
DHT_PIN = board.D26  # DHT11 Data line (GPIO 26)

# PCF8591 ADC Configuration (I2C)
I2C_ADDRESS = 0x48
BUS_NUMBER = 1
AIN0_MOISTURE_CMD = 0x40  # Soil Moisture on AIN0
AIN1_LIGHT_CMD    = 0x41  # Photocell on AIN1

# Soil Moisture Sensor Calibration Boundaries
AIR_RAW = 24         # Dry air baseline -> 0%
WATER_RAW = 158      # Full submersion floor -> 100%

# Photocell Calibration Boundaries
DARK_RAW = 195      # Covered/Dark baseline -> 0 Lux
BRIGHT_RAW = 245    # Direct light ceiling -> 10,000 Lux

gpio.setwarnings(False)
gpio.setmode(gpio.BCM)

# Configure RGB LED with Pulse Width Modulation(PWM) - 100Hz
PINS = [GREEN_PIN, RED_PIN, BLUE_PIN]
for pin in PINS:
    gpio.setup(pin, gpio.OUT)
    gpio.output(pin, gpio.HIGH)  # Common Anode OFF = HIGH

pwm_red = gpio.PWM(RED_PIN, 100)
pwm_green = gpio.PWM(GREEN_PIN, 100)
pwm_blue = gpio.PWM(BLUE_PIN, 100)

# Start PWM with 100% Duty Cycle = OFF for Common Anode
pwm_red.start(100) 
pwm_green.start(100)
pwm_blue.start(100)

# Global thread state for non-blocking LED blinking
current_led_mode = "OFF"
system_running = True

# Initialize I2C Bus and DHT11 Sensor
bus = smbus2.SMBus(BUS_NUMBER)
dht_device = adafruit_dht.DHT11(DHT_PIN, use_pulseio=False)


# ===============================================
# ADAFRUIT IO FEEDS CONFIGURATION
# ===============================================

FEED_TEMP = "temperature"
FEED_HUMIDITY = "humidity"
FEED_SOIL = "soil-moisture"
FEED_LIGHT = "light-lux"
FEED_STATUS = "led-status"


# ===============================================
# HARDWARE HELPER & LED THREAD FUNCTIONS
# ===============================================

# RGB LED control with PWM for Common Anode configuration
def set_rgb_pwm(r_pct, g_pct, b_pct):
    """
    Controls Common Anode RGB LED intensity via PWM (0 - 100%).
    For Common Anode, Duty Cycle = (100 - desired_intensity).
    """
    pwm_red.ChangeDutyCycle(100.0 - r_pct)
    pwm_green.ChangeDutyCycle(100.0 - g_pct)
    pwm_blue.ChangeDutyCycle(100.0 - b_pct)


# RCB LED Blinker Thread for non-blocking blinking and solid states
def led_blinker_thread():
    """
    Dedicated background worker to handle active blinking and solid states
    without delaying the telemetry loop.
    """
    toggle = False
    while system_running:
        toggle = not toggle

        if current_led_mode == "SOLID_GREEN":
            set_rgb_pwm(r_pct=0, g_pct=100, b_pct=0)
        elif current_led_mode == "BLINK_GREEN":
            set_rgb_pwm(r_pct=0, g_pct=100 if toggle else 0, b_pct=0)
        elif current_led_mode == "SOLID_YELLOW":
            set_rgb_pwm(r_pct=80, g_pct=100, b_pct=0)
        elif current_led_mode == "BLINK_YELLOW":
            set_rgb_pwm(r_pct=80 if toggle else 0, g_pct=100 if toggle else 0, b_pct=0)
        elif current_led_mode == "BLINK_RED":
            set_rgb_pwm(r_pct=100 if toggle else 0, g_pct=0, b_pct=0)
        else:
            set_rgb_pwm(r_pct=0, g_pct=0, b_pct=0)

        time.sleep(0.2)  # 0.2s toggle rate for blinking


# Soil Moisture Sensor Reading and Conversion
def read_soil_moisture():
    """
    Reads AIN0 from PCF8591 ADC and converts to 0-100% moisture.
    """
    try:
        bus.write_byte(I2C_ADDRESS, AIN0_MOISTURE_CMD)
        bus.read_byte(I2C_ADDRESS)  # Discard stale conversion sample
        raw_val = bus.read_byte(I2C_ADDRESS)
        
        if raw_val <= AIR_RAW:
            return 0.0
        if raw_val >= WATER_RAW:
            return 100.0

        pct = ((raw_val - AIR_RAW) / (WATER_RAW - AIR_RAW)) * 100.0
        return round(pct, 1)
    except Exception as e:
        print(f"{RED}ERROR: ADC Soil Read Error: {e}{RESET}")
        return None


# Photocell Sensor Reading and Conversion to Lux
def read_photocell_lux():
    """
    Reads AIN1 from PCF8591 ADC, averages samples to reduce jitter,
    and maps voltage to Lux (0 - 10,000) rounded to the nearest 100 Lux.
    """
    try:
        samples = []
        for _ in range(5):
            bus.write_byte(I2C_ADDRESS, AIN1_LIGHT_CMD)
            bus.read_byte(I2C_ADDRESS)  # Discard stale read
            samples.append(bus.read_byte(I2C_ADDRESS))
            time.sleep(0.02)

        raw_val = sum(samples) / float(len(samples))

        if raw_val <= DARK_RAW:
            return 0
        if raw_val >= BRIGHT_RAW:
            return 10000

        # Linear mapping from 195-245 raw ADC to 0-10,000 Lux
        raw_lux = ((raw_val - DARK_RAW) / (BRIGHT_RAW - DARK_RAW)) * 10000.0
        
        # Calculate clean 100 Lux step integer
        clean_lux = int(round(raw_lux / 25.0) * 25)
        return clean_lux
    except Exception as e:
        print(f"{RED}ERROR: ADC Photocell Read Error: {e}{RESET}")
        return None


# Sensor Sampling Function for DHT11, Soil Moisture, and Photocell
def read_sensors():
    """
    Samples physical telemetry from all connected microclimate sensors.
    """
    temp_f = None
    humidity = None
    
    for _ in range(3):
        try:
            temp_c = dht_device.temperature
            hum = dht_device.humidity
            if temp_c is not None and hum is not None:
                temp_f = round((temp_c * 9/5) + 32, 1)
                humidity = round(float(hum), 1)
                break
        except RuntimeError:
            time.sleep(0.5)
        except Exception as e:
            print(f"{RED}ERROR: DHT11 Sensor Error: {e}{RESET}")
            break

    soil_moisture = read_soil_moisture()
    light_lux = read_photocell_lux()

    return {
        "temp": temp_f,
        "humidity": humidity,
        "soil": soil_moisture,
        "light": light_lux
    }


# LED Matrix Update Logic Based on Sensor Readings
def update_led_matrix(soil_moisture, light_lux):
    """
    Executes microclimate status matrix, updates current_led_mode for the 
    blinker thread, and returns string for Adafruit IO.
    """
    global current_led_mode

    if soil_moisture is None or light_lux is None:
        current_led_mode = "OFF"
        return "OFF: Sensor Data Initializing..."

    ideal_moisture = 30.0 <= soil_moisture <= 80.0
    ideal_light = 1800 <= light_lux <= 3000

    if light_lux < 1800:
        light_status = f"Insufficient Light ({light_lux} lux)"
    elif light_lux > 3000:
        light_status = f"Excessive Light ({light_lux} lux)"
    else:
        light_status = f"Ideal Light ({light_lux} lux)"

    # 5-State Alert Matrix Logic
    if soil_moisture > 80.0:
        current_led_mode = "BLINK_RED"
        return f"BLINKING RED: !!! CRITICAL ALERT: OVERWATERED ({soil_moisture}%) !!!"
    elif ideal_moisture and ideal_light:
        current_led_mode = "SOLID_GREEN"
        return "SOLID GREEN: Optimal Microclimate"
    elif ideal_moisture and not ideal_light:
        current_led_mode = "BLINK_GREEN"
        return f"BLINKING GREEN: Moisture Ideal | {light_status}"
    elif not ideal_moisture and ideal_light:
        current_led_mode = "SOLID_YELLOW"
        return "SOLID YELLOW: Needs Water"
    else:
        current_led_mode = "BLINK_YELLOW"
        return f"BLINKING YELLOW: Low Water & {light_status}"


# Color-Coded Status Text for Terminal Output
def get_status_color(status_text):
    """
    Assigns ANSI terminal color formatting based on current system status.
    """
    if "GREEN" in status_text:
        return GREEN
    if "RED" in status_text:
        return RED
    if "YELLOW" in status_text:
        return YELLOW
    return RESET


# ===============================================
# MQTT CALLBACKS
# ===============================================

def connected(client):
    print(f"{GREEN}[MQTT] Successfully connected to Adafruit IO Cloud!{RESET}")

def disconnected(client):
    print(f"{YELLOW}[MQTT] Disconnected from Adafruit IO Cloud.{RESET}")

def message(client, feed_id, payload):
    print(f"{BLUE}[MQTT] Downstream command received | Feed: {feed_id} --> {payload}{RESET}")


# ===============================================
# PUBLISHER THREAD
# ===============================================

def telemetry_publisher(client):
    """
    Continuously samples hardware metrics, manages LED state, 
    and publishes telemetry payloads to Adafruit IO.
    """
    print(f"{RESET}[ENGINE] Background telemetry publisher thread active.{RESET}")
    while system_running:
        data = read_sensors()
        
        if data["soil"] is not None and data["light"] is not None:
            status_text = update_led_matrix(data["soil"], data["light"])
            status_color = get_status_color(status_text)

            try:
                if data["temp"] is not None:
                    client.publish(FEED_TEMP, data["temp"])
                if data["humidity"] is not None:
                    client.publish(FEED_HUMIDITY, data["humidity"])
                    
                client.publish(FEED_SOIL, data["soil"])
                client.publish(FEED_LIGHT, data["light"])
                client.publish(FEED_STATUS, status_text)

                light_str = f"{int(data['light'])}" if data['light'] is not None else "N/A"
                temp_str = f"{data['temp']}°F" if data['temp'] is not None else "N/A"
                hum_str = f"{data['humidity']}%" if data['humidity'] is not None else "N/A"

                print(f"\n{CYAN}[TELEMETRY] Temp: {temp_str} | Hum: {hum_str} | Soil: {data['soil']}% | Light: {light_str} Lux{RESET}")

                print(f"    {status_color}└─►[SYSTEM STATUS] ─► {status_text}{RESET}\n")
            except Exception as e:
                print(f"{RED}ERROR: Failed publishing feed payload: {e}{RESET}")

        time.sleep(10)  # Publish cycle rate (seconds)


# ===============================================
# MAIN ENTRY POINT
# ===============================================

# Initialize MQTT Client, connect to Adafruit IO, and start background threads
def main():
    global system_running

    client = MQTTClient(ada_username, ada_key)
    client.on_connected = connected
    client.on_disconnected = disconnected
    client.on_message = message

    print(f"{GREEN}\n\n================================================{RESET}")
    print(f"{GREEN}---  HOUSEPLANT MICROCLIMATE MONITOR ENGINE  ---{RESET}")
    print(f"{GREEN}================================================\n{RESET}")
    print(f"{RESET}INITIALIZING: Connecting to Adafruit IO...{RESET}")
    
    try:
        client.connect()
        client.loop_background()
    except Exception as e:
        print(f"{RED}CRITICAL: Could not connect to Adafruit IO: {e}{RESET}")
        dht_device.exit()
        bus.close()
        gpio.cleanup()
        return

    # Start non-blocking LED blinking worker thread
    blinker = threading.Thread(target=led_blinker_thread, daemon=True)
    blinker.start()

    # Start non-blocking background publisher thread
    worker = threading.Thread(target=telemetry_publisher, args=(client,), daemon=True)
    worker.start()

    # Keep main thread alive to allow background threads to operate
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print(f"{YELLOW}\nSHUTDOWN: Terminating system...{RESET}")
    finally:
        system_running = False
        client.disconnect()
        dht_device.exit()
        bus.close()
        
        # Stop PWM & cleanup
        pwm_red.stop()
        pwm_green.stop()
        pwm_blue.stop()
        for pin in PINS:
            gpio.output(pin, gpio.HIGH)
        gpio.cleanup()
        print(f"{YELLOW}SHUTDOWN: GPIO state cleaned up. Process stopped.{RESET}\n\n\n\n")

if __name__ == "__main__":
    main()