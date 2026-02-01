# NSO GameCube Controller Driver

Driver for the Nintendo Switch Online GameCube Controller on macOS/Linux. Use it over **USB** or **BLE** with Dolphin (DSU) or the built-in GUI.

Demo within Dolphin (DSU server is on by default):
![Kapture 2026-01-25 at 19 02 18](https://github.com/user-attachments/assets/95334808-5a85-41f0-8a47-1e66ec156a3f)


Demo with custom GUI (using the included GUI, run with `--gui`):
![Kapture 2026-01-25 at 12 02 22](https://github.com/user-attachments/assets/95aead76-7f64-4b5e-b547-c8ae1f0fb74d)


---

## Download the tool and open Terminal

Do this once before setting up the controller.

### 1. Download the tool to your computer (if you're not familiar with `git` this will help you at least use this tool)

- **Option A (easiest):** On this GitHub page, click the green **Code** button → **Download ZIP**. Unzip the folder (e.g. double‑click it). You’ll get a folder named something like `nso-gc-bridge-main`.
- **Option B:** If you use Git, run: `git clone https://github.com/YOUR_USERNAME/nso-gc-bridge.git` (replace with the real repo URL) and `cd nso-gc-bridge`.

Put the folder somewhere you can find it (e.g. **Downloads** or **Documents**). Remember the full path (e.g. `~/Downloads/nso-gc-bridge-main`).

### 2. Open Terminal (the place where you’ll run the commands)

- **macOS:** Press **Cmd + Space**, type **Terminal**, press Enter. Or open **Applications → Utilities → Terminal**.
- **Linux:** Open your app menu and search for **Terminal** (or **Konsole**, **GNOME Terminal**, etc.).

A window will open with a prompt (e.g. `yourname@computer ~ %`). All the commands below are typed here and run when you press **Enter**.

### 3. Go into the tool’s folder

In Terminal, type this (replace the path with **your** folder path if it’s different):

```bash
cd ~/Downloads/nso-gc-bridge-main
```

If you put the folder somewhere else (e.g. Desktop), use that path instead, for example:

```bash
cd ~/Desktop/nso-gc-bridge-main
```

After you press Enter, you’re “inside” the folder. Every time you open a **new** Terminal window, you’ll need to run this `cd` command again before running the driver.

### 4. Check Python (optional)

The driver needs **Python 3**. To see if it’s installed, run:

```bash
python3 --version
```

You should see something like `Python 3.10.x` or `Python 3.11.x`. If you get “command not found”, install Python from [python.org](https://www.python.org/downloads/) or your system’s package manager first.

---

## Part 1: Script & controller setup

Choose **USB** or **BLE** (for using over Bluetooth) depending on how you connect the controller.

**Run all commands in Terminal, from inside the tool’s folder** (the `cd` step above).

### Prerequisites

- **Python 3.7+** and **pip** (see “Check Python” above if needed).
- **macOS:** For BLE, allow **Bluetooth** for Terminal in **System Settings → Privacy & Security → Bluetooth** when prompted.

---

### Option A: USB

1. **Plug in** the controller with a USB cable.
2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
3. **Run the driver** (DSU for Dolphin is on by default):
   ```bash
   python3 main.py
   ```
   Or explicitly: `python3 main.py --usb`. With GUI: `python3 main.py --gui`
4. You should see `✓ USB device found` and `✓ Driver started successfully!` Keep this terminal open while you use the controller.

*(TODO Add screenshot: Terminal showing successful USB startup if desired.)*

---

### Option B: BLE (wireless)

1. **Install dependencies** (includes `bleak` for BLE):
   ```bash
   pip install -r requirements.txt
   ```
2. **Put the controller in pairing mode** (hold the **pair** button until the LEDs blink).
3. **Run the driver:**
   ```bash
   python3 main.py --ble
   ```
   The script will **scan** for the controller and connect automatically. Keep the pair button held (or put it in pairing mode) when you start the script.
4. You should see `Scanning for controller...`, then `Found controller at ...` and `✓ Connected!` Keep this terminal open.

**Optional:** To use a specific controller address (e.g. after running `python3 main.py --ble-scan`):
   ```bash
   python3 main.py --ble --address <YOUR_ADDRESS>
   ```

*(TODO Add screenshot: Terminal showing BLE scan and connect if desired.)*

---

## Part 2: Dolphin configuration

Use these steps to use the controller in Dolphin via the DSU client.

1. **Start the driver** (DSU is on by default):
   - USB: `python3 main.py` (or `python3 main.py --usb`)
   - BLE: `python3 main.py --ble`
2. **Open Dolphin** → **Controllers** (or **Options** → **Controller Settings**).

   *(TODO Add screenshot: Dolphin main menu or Controllers entry point.)*

3. **Configure the port** you want (e.g. Port 1). Set **Device** to **DSU Client**.

   *(TODO Add screenshot: Device dropdown with "DSU Client" selected.)*

4. **Click Configure** for that port. Map the controller inputs to Dolphin’s buttons:
   - **A, B, X, Y** → face buttons (e.g. Cross, Circle, Square, Triangle)
   - **Main Stick** → left stick
   - **C-Stick** → right stick
   - **L, R, Z, ZL** → shoulder buttons / triggers
   - **Start** → Start / Options
   - **D-pad** → D-pad

   *(TODO Add screenshot: Dolphin button mapping window with DSU client.)*

5. **Port:** The script’s DSU server uses **UDP port 26760**. If Dolphin asks for a port or server address, use `127.0.0.1` (or localhost) and port **26760**.

   *(TODO Add screenshot: Dolphin DSU/network settings if your build shows port or host.)*

6. Click **OK** and start a game. The NSO controller should work as the configured DSU client.

---

## Quick reference

| Goal              | Command                          |
|-------------------|-----------------------------------|
| USB + Dolphin     | `python3 main.py` or `python3 main.py --usb` |
| USB + GUI         | `python3 main.py --gui`          |
| BLE (auto pair)   | `python3 main.py --ble`           |
| BLE + specific MAC| `python3 main.py --ble --address ADDR` |
| Find BLE address  | `python3 main.py --ble-scan`      |
| Disable DSU       | `python3 main.py --no-dsu` (or `--ble --no-dsu`) |
| Stop              | `Ctrl+C` in the terminal          |

DSU (Dolphin) is **on by default** for both USB and BLE. Use `--no-dsu` to disable it. Press **Ctrl+C** in the terminal to stop the driver.

---

## Technical details

### Input decoding

The controller uses the Switch HID protocol with **12-bit nibble-packed stick values**. Buttons are bit-packed in bytes 2–4 (BLE 63-byte) or 3–5 (USB); sticks in bytes 5–7 (main) and 8–10 (C-stick) for BLE 63-byte, or 6–8 and 9–11 for USB. The driver calibrates stick center from the first reports (BLE: median of 50 samples after a short delay).

### Requirements

- Python 3.7+
- `hidapi` (HID), `pyusb` (USB init)
- **BLE:** `bleak`
- **GUI:** PyQt5 or tkinter

### DSU mapping (Cemuhook/DSU protocol, UDP 26760)

- Main Stick → left analog; C-Stick → right analog
- A, B, X, Y → Cross, Circle, Square, Triangle
- L, R → L1, R1; Z → R3; ZL → L2Btn
- Start → Options; Home → PS Button; triggers → L2/R2 analog
