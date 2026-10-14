# Automated Houseplant Microclimate Monitoring System

An end-to-end IoT monitoring system deployed on a Raspberry Pi using the pi-top [4] platform. The system continuously samples soil moisture, ambient light, temperature, and relative humidity to evaluate environmental metrics for sensitive tropical houseplants. 

It features local analog-to-digital signal conversion over I2C, visual alert indicators driven by software pulse-width modulation (PWM), non-blocking multithreading, and live telemetry streaming over MQTT to an Adafruit IO cloud dashboard.

---

## Hardware & Interface Engineering

All hardware runs on a shared 3.3V rail to prevent GPIO damage:

- **PCF8591 ADC/DAC Module:** Communicates via I2C at address `0x48` on Bus 1. Converts analog sensor voltages into digital 8-bit values (0–255) to provide analog read capabilities for the Raspberry Pi.
- **Soil Moisture Sensor Probe:** Wired to analog channel `AIN0` on the PCF8591. Measures electrical conductivity across the substrate, calibrated in software from a baseline of `24` (dry air, 0% moisture) to `158` (water submersion, 100% moisture).
- **Photocell (LDR) Light Module:** Wired to analog channel `AIN1` on the PCF8591. Averages five consecutive readings to eliminate analog jitter, mapping raw values from `195` (0 Lux) to `245` (10,000 Lux) in discrete 25-Lux steps.
- **DHT11 Climate Sensor:** Connected to digital GPIO 26 (`board.D26`). Decodes pulse durations over a 1-wire digital bus to capture relative humidity percentage and temperature, which is converted to Fahrenheit in software.
- **Common Anode RGB LED Module:** Controlled by GPIO 17 (Green), GPIO 27 (Red), and GPIO 22 (Blue). Driven active-LOW using software PWM at 100 Hz to handle color blending and balance diode brightness without hardware bias.

---

## Software Architecture

The Python engine (`main.py`) runs an asynchronous, multithreaded architecture that separates hardware polling from cloud communications and LED cycles:

- **Main Coordination Thread:** Handles boot sequence, loads API credentials from `config.yml`, initializes hardware drivers, establishes the MQTT client loop, and traps `KeyboardInterrupt` to ensure `gpio.cleanup()`, PWM stop commands, and I2C bus closures execute safely on exit.
- **Telemetry Publisher Thread:** A daemon thread running on a 10-second polling cycle. It reads all sensor channels, evaluates metrics against the botanical status matrix, logs color-coded ANSI terminal feedback, and publishes payloads over MQTT.
- **LED Blinker Worker Thread:** Runs on an independent 0.2-second cycle. It reads the global system state variable and executes solid or blinking duty cycle patterns without blocking or slowing the sensor sampling loop.

---

## Microclimate Evaluation Matrix

Sensor data is categorized into five distinct health states based on moisture and light thresholds:

- **Optimal Microclimate (Solid Green):** Soil moisture between 30% and 80%, ambient light between 1,800 and 3,000 Lux.
- **Light Warning (Blinking Green):** Soil moisture is optimal (30%–80%), but light is insufficient (< 1,800 Lux) or excessive (> 3,000 Lux).
- **Needs Water (Solid Yellow):** Soil moisture drops below 30%, while light levels remain optimal.
- **Compound Alert (Blinking Yellow):** Soil moisture drops below 30% and light levels fall outside the target window.
- **Critical Overwatered Alert (Blinking Red):** Soil moisture exceeds 80%, signaling a critical risk of root rot.

---

## Cloud Integration

Telemetry payloads are transmitted over MQTT to five discrete Adafruit IO feeds every 10 seconds:
- `temperature`
- `humidity`
- `soil-moisture`
- `light-lux`
- `led-status`

The cloud environment provides remote visualization through real-time gauges, status text, and historical trend charts. It also supports webhook triggers to send instant mobile and desktop notifications when critical states occur.

---

## Setup & Execution

### 1. Enable Hardware Interfaces
Ensure I2C is enabled on the Raspberry Pi:

sudo raspi-config
# Navigate to Interface Options -> I2C -> Enable

Verify the PCF8591 module is detected at address `0x48`:

sudo apt-get install -y i2c-tools
i2cdetect -y 1

### 2. Environment Setup
Install dependencies inside a virtual environment:

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

Create a `config.yml` file in the root directory:

adafruit:
  username: "YOUR_ADAFRUIT_IO_USERNAME"
  key: "YOUR_ADAFRUIT_IO_KEY"

### 3. Run
Start the telemetry engine:

python main.py

Press `Ctrl+C` to terminate the process and safely release all GPIO pins. 