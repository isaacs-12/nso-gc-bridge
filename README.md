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

<img width="632" height="397" alt="Screenshot 2026-02-01 at 12 11 18 PM" src="https://github.com/user-attachments/assets/b8acf15e-8648-41af-b0be-69450ccb9f2a" />

---

### Option B: BLE (wireless)

[Technical details in the PR where I added it](https://github.com/isaacs-12/nso-gc-bridge/pull/1)

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
<img width="670" height="381" alt="Screenshot 2026-02-01 at 12 12 17 PM" src="https://github.com/user-attachments/assets/f54b634d-de10-4e00-88b1-abae9a00c477" />

---

## Part 2: Dolphin configuration

Use these steps to use the controller in Dolphin via the DSU client.

1. **Start the driver** (DSU is on by default):
   - USB: `python3 main.py` (or `python3 main.py --usb`)
   - BLE: `python3 main.py --ble`
2. **Open Dolphin** → **Controllers** (or **Options** → **Controller Settings**).

<img width="912" height="740" alt="Screenshot 2026-02-01 at 12 12 58 PM" src="https://github.com/user-attachments/assets/3746a890-800c-46d4-a724-4001f138b7e8" />

<img width="780" height="988" alt="Screenshot 2026-02-01 at 12 14 11 PM" src="https://github.com/user-attachments/assets/61b884c0-116d-4e73-b9c6-4093c57d74f4" />


3. **Configure the port** you want (e.g. Port 1). Set **Device** to **DSU Client**.

<img width="650" height="767" alt="Screenshot 2026-02-01 at 12 15 24 PM" src="https://github.com/user-attachments/assets/5d0cce35-5447-44e7-97a3-3d1e7db29784" />

4. **Click Configure** for that port. Map the controller inputs to Dolphin’s buttons:

<img width="780" height="988" alt="Screenshot 2026-02-01 at 12 17 43 PM" src="https://github.com/user-attachments/assets/df0f4339-eb7f-43c1-8b27-fe537ca49fd8" />

<img width="914" height="723" alt="Screenshot 2026-02-01 at 12 18 43 PM" src="https://github.com/user-attachments/assets/1fde9556-3c79-4f0b-bfeb-1ef8226633ab" />

   - **A, B, X, Y** → face buttons (e.g. Cross, Circle, Square, Triangle)
   - **Main Stick** → left stick
   - **C-Stick** → right stick
   - **L, R, Z, ZL** → shoulder buttons / triggers
   - **Start** → Start / Options
   - **D-pad** → D-pad


6. **Port:** The script’s DSU server uses **UDP port 26760**. If Dolphin asks for a port or server address, use `127.0.0.1` (or localhost) and port **26760**.

<img width="650" height="767" alt="Screenshot 2026-02-01 at 12 15 24 PM" src="https://github.com/user-attachments/assets/725c955c-fa4c-43d3-83fe-c2e57b48773b" />


7. Click **OK** and start a game. The NSO controller should work as the configured DSU client.

---

## Quick reference

| Goal              | Command                          |
|-------------------|-----------------------------------|
| USB + Dolphin     | `python3 main.py` or `python3 main.py --usb` |
| USB + GUI         | `python3 main.py --gui`          |
| BLE (auto pair)   | `python3 main.py --ble`           |
| BLE + specific MAC| `python3 main.py --ble --address ADDR` |
| Find BLE address  | `python3 main.py --ble-scan`      |
| Log + latency stats | `python3 main.py --log path/to/file.jsonl` (USB or BLE) |
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

### Latency (USB vs BLE)

The driver can measure **inter-arrival time (IAT)** between input reports and print stats every ~100 reports: `[Latency] Avg: Xms | Jitter: Xms | Range: [min–max]`. This helps you compare USB vs BLE and understand input lag (e.g. for dash dancing or short hops in Melee). **Latency stats are off by default.** To see them (and log input data to a file), run with `--log <path>`, e.g. `python3 main.py --ble --log latency.jsonl` or `python3 main.py --usb --log latency.jsonl`.

| Connection | Typical avg | Why |
|------------|-------------|-----|
| **USB**    | ~4 ms       | Host polls at 250 Hz; controller sends as fast as we read. |
| **BLE**    | ~30 ms      | Limited by BLE **connection interval** (~33 Hz). Controller/OS decide the interval; our code cannot make BLE send faster than the stack allows. |

**Rough guide (avg IAT):** 8–10 ms = excellent; 12–16 ms = okay (occasional missed flicks); >20 ms = poor for tight timing (it get's really hard to perform L-cancels, short hops, wavedash, anything that needs high latency).

**What we do in code:**

- **USB:** Poll HID in a tight loop; no extra delay.
- **BLE:** We request standard full reports at max rate (Set Input Mode subcommand), keep the notification callback lightweight (calibration, file logging, and latency print run off the hot path), and print latency stats so you can confirm numbers.
- **Linux only:** Before connecting over BLE, we try to request a **shorter connection interval** (7.5–15 ms) via `/sys/kernel/debug/bluetooth/hci0/conn_min_interval` (and `conn_max_interval`). This can improve BLE toward ~7–15 ms if the controller and stack accept it. It requires **debugfs** and often **root** (e.g. `sudo python3 main.py --ble`). On macOS and Windows there is no such knob; BLE stays at the stack default (~30 ms).

**TLDR:** For lowest latency use **USB** (~4 ms). For wireless, BLE ~30 ms is normal; on Linux you can try `sudo python3 main.py --ble` to request a shorter interval and see if latency drops. 33HZ, for what it's worth, is almost unnoticeable in games like Mario Kart, and only shows when tight frame perfet windows are needed.

### DSU mapping (Cemuhook/DSU protocol, UDP 26760)

- Main Stick → left analog; C-Stick → right analog
- A, B, X, Y → Cross, Circle, Square, Triangle
- L, R → L1, R1; Z → R3; ZL → L2Btn
- Start → Options; Home → PS Button; triggers → L2/R2 analog
