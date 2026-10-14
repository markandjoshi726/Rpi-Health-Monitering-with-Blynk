# Raspberry Pi Health Monitoring Project with Honeywell SPI and Blynk

Python 3 project for Raspberry Pi 3B+. Based on your report and uploaded MAX30100 files. This is a revised implementation, not a claim
about which hardware or cloud service was used in the original project.

## What works in this implementation

- MAX30100: continuous I2C red/IR acquisition and experimental pulse estimation.
- Honeywell HSCDANN600MGSA3: SPI gauge pressure converted to cuff pressure in mmHg.
- KY-038: digital sound-threshold detection, not calibrated decibels.
- SW-420: digital vibration/movement detection, not posture or fall diagnosis.
- Blynk: HTTPS datastream updates through the Pi's Wi-Fi internet connection.
- Independent acquisition threads, invalid/stale reading handling, retry after
  sensor/network errors, event latching, console output and clean shutdown.

**Important limits:** This is an educational prototype, not a clinical monitor.
Cuff pressure is not systolic/diastolic blood pressure. Producing those values
requires a cuff inflation/controlled-deflation system, a validated oscillometric
algorithm and reference testing. No pump or valve is driven by this program.
SpO2 is unavailable by default because the supplied files have no validated calibration. Pulse estimates
also need comparison against a reference and can be wrong with motion or poor contact.

## Files

- `main.py`: threads, state handling, GPIO events, Blynk uploads and entry point.
- `sensors.py`: new MAX30100 and Honeywell drivers.
- `processing.py`: waveform filtering, pulse estimation, optional calibrated SpO2.
- `config.json`: BCM pins, sensor range and processing settings.
- `requirements.txt`: Python dependencies.
- `tests/test_project.py`: desktop tests; no Raspberry Pi required.

Your original files are left unchanged. The uploaded `rpi.zip` contains a visual
project stub, rather than a functioning complete monitoring application.

## Wiring

Disconnect power while wiring. The GPIO numbers below are BCM numbers.
Physical pin numbers refer to the Pi's 40-pin header. These are proposed wiring
assignments, not a reconstruction of unspecified original wiring.

| Module signal | Pi BCM | Physical pin | Notes |
|---|---:|---:|---|
| MAX30100 SDA | 2 | 3 | I2C1, address 0x57 |
| MAX30100 SCL | 3 | 5 | I2C1 |
| Honeywell SCLK | 11 | 23 | SPI0 clock |
| Honeywell MISO | 9 | 21 | Sensor data output |
| Honeywell SS/CS | 25 | 22 | Manual active-low chip select; NOT CE0 |
| KY-038 DO | 17 | 11 | Threshold signal, configurable polarity |
| SW-420 DO | 27 | 13 | Threshold signal, configurable polarity |
| Common ground | GND | 6 | All module grounds |
| 3.3V supply | 3.3V | 1 or 17 | Only where supported by module |

Honeywell MOSI is unused. CE0 (BCM8) is not connected to the sensor. The code
opens `/dev/spidev0.0` with hardware CS disabled and controls BCM25 to satisfy
Honeywell's minimum 2.5 microsecond select-to-clock interval.

The assumed pressure part is **HSCDANN600MGSA3**: 3.3V, SPI, 0 to 600 mbar gauge,
transfer function A. Confirm the complete part marking and its package pinout
before wiring. Do not apply this SPI driver to the report's analog
SSCMRRN005PGAA5. The analog sensor needs an ADC; this SPI replacement does not
need an external ADC. The pressure sensor's internal electronics digitize it.

Use an appropriate MAX30100 breakout with its required regulators and level
translation. The bare IC requires different supply rails and must not simply
be connected as a generic 3.3V module. Verify that SDA/SCL pull-ups are compatible
with the Pi. All GPIO input signals must remain at 3.3V logic levels; use level
translation for a 5V module output. KY-038 and SW-420 boards vary in supply and
DO polarity. Check yours; config defaults are active-low. Add a 3.3V pull-up if
required by your comparator output. Leave analog AO outputs unconnected.

## Install on Raspberry Pi OS

1. Connect the Pi to Wi-Fi using the desktop network menu or `sudo nmtui`.
   Wi-Fi credentials belong in Raspberry Pi OS, not in this Python script.
2. Run `sudo raspi-config`, enable I2C and SPI in Interface Options, then reboot.
3. In the extracted project directory, run:

```bash
sudo apt update
sudo apt install -y python3-venv python3-dev python3-numpy python3-scipy python3-gpiozero python3-lgpio i2c-tools build-essential
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -r requirements.txt
sudo usermod -aG gpio,i2c,spi "$USER"
```

Log out and back in after changing groups. Package availability varies by
Raspberry Pi OS release.
Run `i2cdetect -y 1`: the MAX30100 should appear at `57`.
Check that `/dev/spidev0.0` exists. Do not run this alongside another program
using the same sensors or GPIOs.

## Set up the Blynk app

Create a Blynk template for this monitor (hardware Raspberry Pi if available,
otherwise Other; connection Wi-Fi). Create a device from that template. In
Device Info, copy its **device auth token**, not a template ID.
Create the following Virtual Pin datastreams in that template. The API only
needs the device token and cloud hostname, not a legacy Blynk Python library.

| Pin | Name | Type | Range / unit |
|---|---|---|---|
| V2 | Experimental pulse | Double | 0 to 250, bpm |
| V3 | Experimental SpO2 | Double | 0 to 100, %; unavailable until calibrated |
| V4 | Cuff pressure | Double | 0 to 500, mmHg; NOT systolic/diastolic |
| V5 | Sound detected | Integer | 0 to 1 |
| V6 | Movement detected | Integer | 0 to 1 |
| V7 | Sensor status | String | Text |
| V8 | Last update Unix time | Integer | 0 to 2147483647 |
| V9 | Optical AC/DC ratio | Double | 0 to 20 |
| V10 | Pulse valid | Integer | 0 to 1 |
| V11 | SpO2 available | Integer | 0 to 1 |
| V12 | Pressure valid | Integer | 0 to 1 |

In the mobile app, add value displays/charts for V2 to V4, indicators for V5/V6
and V10 to V12, and a text display for V7. Keep V8 visible to detect stale cloud
data. Add V9 only if you want to inspect the optical ratio. Widget names and
availability depend on the Blynk plan. This project uses 11 datastreams; check
your account's datastream/message allowances. Increase `publish_seconds` to
reduce traffic. A batch request still updates multiple datastreams.

The program sends only valid numeric readings. **A Blynk value widget may retain
its previous number after a sensor fails. Always view the matching validity
indicator, status and last-update time.** If Wi-Fi stops, even those indicators
remain at their last cloud values until the next successful upload. This is not
an emergency alert system. HTTP updates do not establish a continuous Blynk
hardware connection; use the update timestamp to assess freshness.

The remaining pin numbers are preserved. If updating an existing dashboard,
remove the old temperature, humidity and DHT validity widgets/datastreams
(V0, V1 and V13). These pins are no longer uploaded.

## Run

```bash
source .venv/bin/activate
python main.py --no-cloud --seconds 30
```

Then enter your Blynk device token without placing it in shell history:

```bash
read -rsp 'Blynk device token: ' BLYNK_AUTH_TOKEN
export BLYNK_AUTH_TOKEN
export BLYNK_SERVER='blynk.cloud'
python main.py
```

If your device uses a regional endpoint, set `BLYNK_SERVER` to that exact
hostname from Blynk, for example `fra1.blynk.cloud`. Do not include `https://`
or a URL path. Wrong-region redirects are not followed. The script does not
print the token or request URL. Ctrl+C stops the program. `--no-cloud` still
uses real sensors; it is not simulated data.

Data path: sensor -> Pi Python -> Wi-Fi router/internet -> Blynk Cloud -> app.
This also works over Ethernet if the Pi routes internet traffic over Ethernet.
No USB sensor connection is used in this revised design.

## How processing works

Pressure: reject non-normal status bits, decode the 14-bit reading, then use:

```text
pressure_mbar = (raw - 1638) * 600 / (14745 - 1638)
pressure_mmhg = pressure_mbar * 0.750061683
```

The pressure loop samples at roughly 50Hz and publishes the latest value.
Out-of-calibration readings are invalid, not silently clamped.

MAX30100: configure 100Hz acquisition, read the FIFO frequently, and retain a
12-second window. On FIFO overflow or long scheduling gaps, discard the window.
The pulse algorithm bandpass-filters IR and red, checks amplitude/correlation,
finds IR peaks and calculates `60 / median(peak-to-peak interval in seconds)`.
Expect roughly 8 to 12 seconds to warm up with a stationary finger. Thresholds
in config are starting points, not calibrated finger-detection specifications.

The original test's `IR / 100` and `red / 100` cannot produce bpm and SpO2.
The original driver also passed literal `1600` into an 8-bit configuration
register and did not apply its sample-rate argument. This version uses explicit
register codes and checks the device ID.

SpO2 needs calibrated mapping from:

```text
R = (red_AC_RMS / red_DC_mean) / (IR_AC_RMS / IR_DC_mean)
SpO2_estimate = a + b * R
```

`spo2_calibration` stays `null` by default. After obtaining suitable calibration
for this optical assembly and this RMS processing method, replace null with an
object containing numeric `a`, `b`, `ratio_min`, and `ratio_max`. The code rejects
ratios outside that calibration domain and outputs outside 0 to 100. Arbitrary
coefficients do not make this a reliable oximeter; enabling them does not validate
the instrument. No invented oxygen-saturation values are uploaded by default.

KY-038/SW-420 edges are latched between upload intervals, with active states
also checked at each upload. Events survive failed uploads, but multiple events
merge into one flag. They are indications, not event counts. Adjust the physical
module potentiometers and active-high configuration while testing.

## Verification and remaining work

```bash
python -m unittest discover -s tests -v
```

Automated checks cover pressure scaling/status rejection, MAX30100 register
configuration and FIFO handling, synthetic pulse recovery, flat-signal rejection,
and mocked Blynk behavior. No real Pi, sensor, Wi-Fi connection or Blynk account
was available during generation. Bench-check wiring, voltage levels, signal
polarity, pressure against a reference and pulse against a reference before
interpreting measurements. Actual systolic/diastolic measurement and clinically
validated SpO2 are outside the implemented functionality.

## Primary references

- Honeywell HSC series and model documentation:
  https://automation.honeywell.com/us/en/products/sensing-solutions/sensors/pressure-sensors/board-mount-pressure-amplified/trustability-hsc-series-board-mount-pressure-sensor
- Honeywell SPI communication technical note:
  https://prod-edam.honeywell.com/content/dam/honeywell-edam/sps/siot/ja/products/sensors/pressure-sensors/common/documents/sps-siot-spi-comms-digital-ouptu-pressure-sensors-tn-008202-3-en-ciid-45843.pdf
- MAX30100 datasheet:
  https://www.analog.com/media/en/technical-documentation/data-sheets/MAX30100.pdf
- Blynk batch datastream update API:
  https://docs.blynk.io/en/blynk.cloud/device-https-api/update-multiple-datastreams-api
