# NSO GameCube Controller Driver

## Usage

### Download

Download the latest release from the [Releases](https://github.com/isaacs-12/nso-gc-bridge/releases) page. Get `nso-gc-bridge-X.X.X.zip`, unzip it, and you'll see the executable Application.

<img width="672" height="700" alt="Screenshot 2026-02-04 at 9 42 09 PM" src="https://github.com/user-attachments/assets/cbd7b3ce-a17f-4ea6-b6ef-2cd4bd6d7589" />

### Quick start

1. **Start the launcher** — Double-click `NSO GC Bridge` after downloading the latest release. A window opens with connection and option checkboxes.
2. **Connect the controller** — USB: plug in the cable to the controller and computer. BLE: put the controller in pairing mode (hold the pair button until LEDs blink), then select BLE in the launcher and click **Start Driver**. You should hold for ~8 seconds, or until the connection is established. BLE addresses are stable per device; use **Manage saved controllers** to save addresses with names and connect directly next time.
3. **Configure Dolphin** — Open Dolphin → Controllers → Enable Background Input

<img width="780" height="988" alt="Screenshot 2026-02-01 at 12 14 11 PM" src="https://github.com/user-attachments/assets/7f0e7ba8-d26e-4028-b227-143b0c01fe97" />

Configure the **DSU Client**. Use `127.0.0.1` and port **26760**

<img width="650" height="767" alt="Screenshot 2026-02-01 at 12 15 24 PM" src="https://github.com/user-attachments/assets/af55311a-f424-40e2-b415-6593857b0af4" />

→ Configure and map buttons.

<img width="780" height="988" alt="Screenshot 2026-02-01 at 12 17 43 PM" src="https://github.com/user-attachments/assets/c1e2a8c4-2c8f-4cf3-84d2-56c4982f8d01" />

Be sure to map each button as desired

<img width="914" height="723" alt="Screenshot 2026-02-01 at 12 18 43 PM" src="https://github.com/user-attachments/assets/b9977797-f1d7-4fe6-a23f-0e66a402f648" />


4. **Verify connection** — With the driver running and Dolphin open, the launcher log will show "✓ Dolphin connected" when Dolphin has successfully linked to the DSU server.

### For Using Multiple NSO GC Controllers

1. Select **Multi-controller** in the launcher

<img width="551" height="246" alt="Screenshot 2026-02-04 at 9 43 15 PM" src="https://github.com/user-attachments/assets/93e97545-4b6f-47db-8f87-ccf879e38c3f" />

2. Pick the slots/ports you want to use (this maps through Dolphin)
3. Select the connection method (USB/BLE)
4. If you have saved controllers, select them and assign to the slots. (If you haven't saved controllers, you may need to save them first, as I write this I realize I didn't test scanning here)
5. Click **Start Driver**. This time in the logs, you should see multiple controllers connecting, with a line like `✓ Dolphin connected` for each one.
6. In Dolphin, when configuring the controller, you should see them in devices (might need to refresh) as `DSUClient/<slot>/<What you named it>`

<img width="914" height="719" alt="Screenshot 2026-02-04 at 9 49 24 PM" src="https://github.com/user-attachments/assets/ea1b011f-c0d6-4688-8d5e-8b2e3e09dc3c" />


### Dolphin button mapping

- **A, B, X, Y** → face buttons (Cross, Circle, Square, Triangle)
- **Main Stick** → left stick · **C-Stick** → right stick
- **L, R, Z, ZL** → shoulder buttons / triggers
- **Start** → Options · **D-pad** → D-pad

DSU is on by default. Use **Stop** in the launcher (or Ctrl+C in the terminal) to quit.

---

If using the command line:

**One-time setup** (Python 3.7+ required):

```bash
cd nso-gc-bridge-X.X.X
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

**To run:** Double-click `NSO GC Bridge` (macOS) or run `python3 launcher.py`. You may need to go into System Settings to whitelist this app:
<img width="704" height="617" alt="Screenshot 2026-02-01 at 11 49 51 PM" src="https://github.com/user-attachments/assets/3a3ca188-a8f9-4934-b3a8-be7463c20b8a" />

This opens the **launcher** — a GUI with checkboxes for USB/BLE, DSU, controller GUI, and log options, plus a log view. Use **Start Driver** to run, **Stop** to quit.

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

A lot of exploration was done in the Bluetooth [implementation](https://github.com/isaacs-12/nso-gc-bridge/pull/1), if you're looking for more details.

### Troubleshooting

- **"Address already in use" (DSU port 26760):** A previous instance may still be running. The driver will try ports 26761, 26762, etc.; if it uses a fallback, configure Dolphin's DSU client to match. To free the port: click **Free orphaned port** in the launcher, or run `python main.py --free-dsu-port`.
- **Connects to wrong device (not your controller):** The driver filters by Nintendo-like names and HID service. If it still connects to the wrong device, use `--ble-scan` to list addresses, then `--ble --address <ADDR>` to target a specific controller.
- **Won't connect after restart:** Remove the controller from **System Settings → Bluetooth**, then put it in pairing mode and start the driver first (hold pair button when the script scans).
- **Multiple controllers:** Use **Manage saved controllers** → Add to save each with a name, then pick from the dropdown. Or use `--ble-scan` to get addresses, then `--ble --address <ADDR>`.

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
| Free orphaned port| `python3 main.py --free-dsu-port` |

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

## Demo

Driver for the Nintendo Switch Online GameCube Controller on macOS/Linux. Use it over **USB** or **BLE** with Dolphin (DSU) or the built-in GUI.

Demo within Dolphin (DSU server is on by default):
![Kapture 2026-01-25 at 19 02 18](https://github.com/user-attachments/assets/95334808-5a85-41f0-8a47-1e66ec156a3f)

Demo with custom GUI (run with `--gui` or the GUI option in the launcher):
![Kapture 2026-01-25 at 12 02 22](https://github.com/user-attachments/assets/95aead76-7f64-4b5e-b547-c8ae1f0fb74d)
