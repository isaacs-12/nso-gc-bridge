# NSO GameCube Controller Driver

Driver for the Nintendo Switch Online GameCube Controller adapter on macOS/Linux.

Demo with custom GUI:
![Kapture 2026-01-25 at 12 02 22](https://github.com/user-attachments/assets/95aead76-7f64-4b5e-b547-c8ae1f0fb74d)


## Quick Start

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Run the driver:**
   ```bash
   python3 driver_final.py
   ```

3. **With GUI (optional):**
   ```bash
   python3 driver_final.py --gui
   ```

4. **Log data (optional):**
   ```bash
   python3 driver_final.py --log stick_data.jsonl
   ```

## Usage

- **Terminal mode**: Shows real-time stick positions and button presses
- **GUI mode**: Visual interface showing stick positions, buttons, and triggers
- **Logging**: Saves controller data to a JSON Lines file for analysis

Press `Ctrl+C` to stop the driver.

## Technical Details

### Input Decoding

The controller uses the Switch HID protocol with **12-bit nibble-packed stick values**.

#### Stick Data Format

Each stick axis is 12 bits (0-4095), packed into 3 bytes per 2 axes:

**Main Stick (bytes 6-8):**
- **X axis**: `byte6` (low 8 bits) | `(byte7 & 0x0F) << 8` (high 4 bits)
- **Y axis**: `(byte7 >> 4)` (low 4 bits) | `byte8 << 4` (high 8 bits)

**C-Stick (bytes 9-11):**
- **X axis**: `byte9` (low 8 bits) | `(byte10 & 0x0F) << 8` (high 4 bits)
- **Y axis**: `(byte10 >> 4)` (low 4 bits) | `byte11 << 4` (high 8 bits)

#### Calibration

At startup, the driver assumes the controller is in a neutral position and samples 10 readings to determine the center point for each axis (typically around 2048 = 2^11). All subsequent readings are offset by subtracting this center value, resulting in signed integers ranging from approximately -2048 to +2047.

#### Button Mapping

Buttons are bit-packed in bytes 3-5:
- **Byte 3**: B, A, Y, X, R, Z, Start
- **Byte 4**: D-pad directions, L, ZL
- **Byte 5**: Home, Capture

#### Triggers

Analog triggers are 8-bit values (0-255) at bytes 13-14.

### Protocol Initialization

The driver requires USB initialization before HID reading:
1. Send default report (16 bytes) to USB endpoint
2. Send LED report (16 bytes) to USB endpoint
3. Open HID device for continuous input reading

## Requirements

- Python 3.7+
- `hidapi` (for HID communication)
- `pyusb` (for USB initialization)
- Optional: `PyQt5` or `tkinter` for GUI mode

