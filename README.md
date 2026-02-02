# NSO GameCube Controller Driver

Driver for the Nintendo Switch Online GameCube Controller on macOS/Linux. Use it over **USB** or **BLE** with Dolphin (DSU) or the built-in GUI.

Demo within Dolphin (DSU server is on by default):
![Kapture 2026-01-25 at 19 02 18](https://github.com/user-attachments/assets/95334808-5a85-41f0-8a47-1e66ec156a3f)

Demo with custom GUI (run with `--gui`):
![Kapture 2026-01-25 at 12 02 22](https://github.com/user-attachments/assets/95aead76-7f64-4b5e-b547-c8ae1f0fb74d)

---

## Usage

### Download

Download the latest release from the [Releases](https://github.com/isaacs-12/nso-gc-bridge/releases) page.

- **macOS:** Double-click `run.command` to start the launcher (a Terminal window will open). If you don't have a release yet, clone the repo and double-click `run.command` in the project folder.
- **Linux:** Run from the project folder (see Developing).

### Quick start

1. **Start the driver** — Double-click `run.command` (or `NSO GC Bridge.app` on macOS), or run `python3 launcher.py` from the project folder. The launcher opens with checkboxes for USB/BLE, DSU, GUI, and other options.
2. **Connect the controller** — USB: plug in the cable. BLE: put the controller in pairing mode (hold the pair button until LEDs blink), then select BLE in the launcher and click **Start Driver**.
3. **Configure Dolphin** — Open Dolphin → Controllers → set the port to **DSU Client** → Configure and map buttons. Use `127.0.0.1` and port **26760** if prompted.

### Dolphin button mapping

- **A, B, X, Y** → face buttons (Cross, Circle, Square, Triangle)
- **Main Stick** → left stick · **C-Stick** → right stick
- **L, R, Z, ZL** → shoulder buttons / triggers
- **Start** → Options · **D-pad** → D-pad

DSU is on by default. Use **Stop** in the launcher (or Ctrl+C in the terminal) to quit.

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

The driver can measure **inter-arrival time (IAT)** between input reports and print stats every ~100 reports: `[Latency] Avg: Xms | Jitter: Xms | Range: [min–max]`. **Latency stats are off by default.** To see them, run with `--log <path>`, e.g. `python3 main.py --ble --log latency.jsonl`.

| Connection | Typical avg | Why |
|------------|-------------|-----|
| **USB**    | ~4 ms       | Host polls at 250 Hz; controller sends as fast as we read. |
| **BLE**    | ~30 ms      | Limited by BLE **connection interval** (~33 Hz). |

**Rough guide (avg IAT):** 8–10 ms = excellent; 12–16 ms = okay; >20 ms = poor for tight timing (L-cancels, short hops, wavedash).

- **Linux only:** Before connecting over BLE, we try to request a **shorter connection interval** (7.5–15 ms) via `/sys/kernel/debug/bluetooth/hci0/conn_min_interval`. Requires debugfs and often **root** (e.g. `sudo python3 main.py --ble`). On macOS and Windows there is no such knob.

**TLDR:** For lowest latency use **USB** (~4 ms). For wireless, BLE ~30 ms is normal.

### DSU mapping (Cemuhook/DSU protocol, UDP 26760)

- Main Stick → left analog; C-Stick → right analog
- A, B, X, Y → Cross, Circle, Square, Triangle
- L, R → L1, R1; Z → R3; ZL → L2Btn
- Start → Options; Home → PS Button; triggers → L2/R2 analog

---

## Developing

### Setup

```bash
git clone https://github.com/isaacs-12/nso-gc-bridge.git
cd nso-gc-bridge
python3 -m venv venv
source venv/bin/activate   # or: venv\Scripts\activate on Windows
pip install -r requirements.txt
```

**macOS:** For BLE, allow **Bluetooth** for Terminal in **System Settings → Privacy & Security → Bluetooth** when prompted.

### Running

| Goal              | Command                          |
|-------------------|-----------------------------------|
| Launcher UI       | `make run` or `python3 launcher.py` |
| USB + Dolphin     | `python3 main.py` or `python3 main.py --usb` |
| USB + GUI         | `python3 main.py --gui`          |
| BLE (auto pair)   | `python3 main.py --ble`           |
| BLE + address     | `python3 main.py --ble --address ADDR` |
| Find BLE address  | `python3 main.py --ble-scan`      |
| Log + latency     | `python3 main.py --log path/to/file.jsonl` |
| Disable DSU       | `python3 main.py --no-dsu`        |

### Building

| Target       | Command          | Description |
|--------------|------------------|-------------|
| Run launcher | `make run`       | Run the launcher UI |
| Double-click | `run.command`    | Run launcher without terminal (no .app needed) |
| Build .app   | `make build`     | Build `dist/NSO GC Bridge.app` (requires `make install` first) |
| Open app     | `make open`      | Open the built .app |
| Clean        | `make clean`     | Remove build artifacts |

```bash
make install    # One-time: install deps + py2app
make build      # Build dist/NSO GC Bridge.app
make open       # Open the app
```

The launcher provides checkboxes for USB/BLE, DSU, GUI, debug (BLE only), and log options, plus a log view. Use **Start Driver** to run, **Stop** to quit. To find your BLE address, run `python3 main.py --ble-scan` in a terminal (put controller in pairing mode first).

### Releasing

```bash
make release                    # Create release/nso-gc-bridge-1.0.0.zip
make release-publish VERSION=1.0.0   # Create GitHub release + upload (requires gh)
```

Install the GitHub CLI first: `brew install gh` then `gh auth login`. The zip includes `run.command` for double-click launch.
