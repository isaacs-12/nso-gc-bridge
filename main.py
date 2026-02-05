#!/usr/bin/env python3
"""
NSO GameCube Controller Driver - Based on Discovered Protocol

Uses the initialization sequence and HID format discovered by the community.
"""

import usb.core
import usb.util
import hid
import time
import sys
import threading
import asyncio
import queue
from collections import deque

# Optional BLE support (for wireless controller not visible as HID)
try:
    from bleak import BleakClient, BleakScanner
    BLE_AVAILABLE = True
except ImportError:
    BLE_AVAILABLE = False
    BleakClient = None
    BleakScanner = None

# Retry interval when waiting for controller to become connectable (seconds)
BLE_CONNECT_RETRY_SEC = 2.0
# How long to scan when using --ble-scan (seconds)
BLE_SCAN_DURATION_SEC = 15
# Shorter scan per leg when using --ble-scan-diff (seconds)
BLE_SCAN_DIFF_DURATION_SEC = 8
# Timeout per candidate when trying handshake in --ble-scan-diff (seconds). Keep short so pairing mode stays active.
BLE_HANDSHAKE_TRY_TIMEOUT_SEC = 1.5
# Scan duration when --ble is used without --address (auto-discover). Hold pair button during this.
BLE_SCAN_AUTO_SEC = 10
# BLE connection interval: units of 1.25ms. 6=7.5ms (min), 12=15ms. Request before connect on Linux to get USB-like latency.
BLE_CONN_MIN_INTERVAL_UNITS = 6   # 7.5ms
BLE_CONN_MAX_INTERVAL_UNITS = 12  # 15ms
# Import DSU server support
try:
    from dsu_server import DSUServer
    DSU_AVAILABLE = True
except ImportError:
    DSU_AVAILABLE = False
    DSUServer = None

# Try to import GUI libraries
GUI_AVAILABLE = False
GUI_TYPE = None

try:
    import tkinter as tk
    from tkinter import ttk
    GUI_AVAILABLE = True
    GUI_TYPE = 'tkinter'
except ImportError:
    try:
        # Try PyQt5 as alternative
        from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout, QFrame
        from PyQt5.QtCore import Qt, QTimer
        from PyQt5.QtGui import QPainter, QColor, QPen
        GUI_AVAILABLE = True
        GUI_TYPE = 'pyqt5'
    except ImportError:
        # Last resort: try to use system Python's tkinter
        # This is a workaround for venv Python that doesn't have tkinter
        import sys
        import subprocess
        if sys.platform == 'darwin':  # macOS
            try:
                # Check if system Python has tkinter
                result = subprocess.run(['/usr/bin/python3', '-c', 'import tkinter'], 
                                      capture_output=True, timeout=2)
                if result.returncode == 0:
                    # System Python has tkinter - we'll need to use it via subprocess
                    # For now, just note that it's available but not directly importable
                    GUI_AVAILABLE = False
                    GUI_TYPE = 'tkinter_system_python'
            except:
                pass
        GUI_AVAILABLE = False
        if GUI_TYPE is None:
            GUI_TYPE = None

VID = 0x057e
PID = 0x2073
INTERFACE_NUM = 1

# Nintendo BLE: standard HID Report characteristic (read = notifications, write = output/command)
HID_REPORT_UUID = "00002a4d-0000-1000-8000-00805f9b34fb"

# BlueRetro-style BLE handshake for Nintendo SW2/GC (PID 0x2073): READ_SPI command to wake/init.
# See BlueRetro main/bluetooth/hidp/sw2.c SW2_INIT_STATE_READ_INFO.
# First byte 0x02=CMD_READ_SPI, 0x91=REQ_TYPE_REQ, 0x01=REQ_INT_BLE, 0x04=SUBCMD_READ_SPI, then payload.
BLE_HANDSHAKE_READ_SPI = bytearray([
    0x02, 0x91, 0x01, 0x04,
    0x00, 0x08, 0x00, 0x00, 0x40, 0x7e, 0x00, 0x00, 0x00, 0x30, 0x01, 0x00
])

# Initialization data discovered by the community
DEFAULT_REPORT_DATA = [
    0x03, 0x91, 0x00, 0x0d, 0x00, 0x08,
    0x00, 0x00, 0x01, 0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF
]

SET_LED_DATA = [
    0x09, 0x91, 0x00, 0x07, 0x00, 0x08,
    0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00
]

# Subcommand 0x03: Set Input Mode — 0x30 = standard full reports (max report rate for dash dancing / short hops)
SET_INPUT_MODE = bytearray([0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x03, 0x30])


class NSODriver:
    """NSO GameCube Controller Driver."""
    
    def __init__(self, use_gui=False, log_file=None, use_dsu=False, debug=False, dsu_server=None,
                 dsu_pad_id=0, dsu_connection_type=0x01, device_index=0):
        self.usb_device = None
        self.hid_device = None
        self.running = False
        self.out_endpoint = None
        self.use_gui = use_gui and GUI_AVAILABLE
        self.current_state = None
        self.gui_window = None
        self.log_file = log_file
        self.debug = debug
        self.last_log_time = 0
        self.log_interval = 1.0  # Log every 1 second
        self.dsu_pad_id = dsu_pad_id
        self.dsu_connection_type = dsu_connection_type
        self.device_index = device_index
        
        # DSU server: use shared when provided, else create our own
        self._dsu_owned = False
        if dsu_server is not None:
            self.dsu_server = dsu_server
        elif use_dsu and DSU_AVAILABLE:
            self.dsu_server = DSUServer()
            self._dsu_owned = True
        else:
            self.dsu_server = None
        
        # Calibration offsets (assume controller starts in neutral position)
        self.calibration = {
            'main_x_center': None,
            'main_y_center': None,
            'c_x_center': None,
            'c_y_center': None,
            'calibrated': False
        }
        self._init_latency_monitor()

    def _init_latency_monitor(self):
        """Track inter-arrival time of input reports for latency comparison (USB vs BLE)."""
        self._last_packet_time = None
        self._iat_history = deque(maxlen=100)  # last 100 packets

    def _log_latency(self):
        """Log IAT stats every 100 packets. Avg 8–10ms = excellent; >20ms = dash dancing suffers."""
        current_time = time.perf_counter()
        if self._last_packet_time is not None:
            delta = (current_time - self._last_packet_time) * 1000
            self._iat_history.append(delta)
            if len(self._iat_history) == 100:
                avg = sum(self._iat_history) / 100
                max_val = max(self._iat_history)
                min_val = min(self._iat_history)
                jitter = (sum((x - avg) ** 2 for x in self._iat_history) / 100) ** 0.5
                msg = f"[Latency] Avg: {avg:.2f}ms | Jitter: {jitter:.2f}ms | Range: [{min_val:.1f}-{max_val:.1f}]"
                self._iat_history.clear()
                if self.log_file or getattr(self, 'debug', False):
                    threading.Thread(target=lambda m=msg: print(m, flush=True), daemon=True).start()
        self._last_packet_time = current_time

    def find_usb_device(self, device_index: int = 0):
        """Find USB device and get endpoints. device_index=0 for first, 1 for second, etc."""
        devices = list(usb.core.find(find_all=True, idVendor=VID, idProduct=PID))
        if device_index >= len(devices):
            return False
        self.usb_device = devices[device_index]
        
        try:
            if self.usb_device.is_kernel_driver_active(INTERFACE_NUM):
                self.usb_device.detach_kernel_driver(INTERFACE_NUM)
        except Exception:
            pass
        
        try:
            self.usb_device.set_configuration()
        except Exception:
            pass
        
        cfg = self.usb_device.get_active_configuration()
        intf = cfg[(INTERFACE_NUM, 0)]
        self.out_endpoint = None
        for ep in intf:
            if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_OUT:
                if (ep.bmAttributes & 0x03) == usb.util.ENDPOINT_TYPE_BULK:
                    self.out_endpoint = ep.bEndpointAddress
                    break
        
        if self.out_endpoint is None:
            print("✗ Could not find bulk OUT endpoint")
            return False
        return True
    
    def initialize_usb(self):
        """Initialize device via USB (required first step)."""
        print("Initializing device via USB...")
        
        # Send default report
        try:
            transferred = self.usb_device.write(
                self.out_endpoint,
                DEFAULT_REPORT_DATA,
                timeout=1000
            )
            print(f"  ✓ Default report sent ({transferred} bytes)")
        except Exception as e:
            print(f"  ✗ Error sending default report: {e}")
            return False 
        # Send LED report
        try:
            transferred = self.usb_device.write(
                self.out_endpoint,
                SET_LED_DATA,
                timeout=1000
            )
            print(f"  ✓ LED report sent ({transferred} bytes)")
        except Exception as e:
            print(f"  ✗ Error sending LED report: {e}")
            return False
        print("  ✓ USB initialization complete\n")
        return True
    
    def open_hid_device(self, device_index: int = 0):
        """Open HID device for reading. device_index=0 for first, 1 for second, etc."""
        try:
            devices = hid.enumerate(VID, PID)
            if device_index >= len(devices):
                return False
            path = devices[device_index]['path']
            self.hid_device = hid.device()
            self.hid_device.open_path(path)
            self.hid_device.set_nonblocking(True)
            print("✓ HID device opened")
            return True
        except Exception as e:
            print(f"✗ Failed to open HID device: {e}")
            return False
    
    def calibrate_sticks(self, num_samples=10):
        """Calibrate stick centers by reading initial values (assumes neutral position)."""
        if self.calibration['calibrated']:
            return True
        
        print("Calibrating sticks (assuming neutral position)...")
        samples = []
        
        # Collect samples
        for _ in range(num_samples):
            try:
                data = self.hid_device.read(64)
                if data and len(data) >= 12:
                    # Extract 12-bit values using nibble packing (same as parse_input)
                    samples.append({
                        'main_x': data[6] | ((data[7] & 0x0F) << 8),
                        'main_y': (data[7] >> 4) | (data[8] << 4),
                        'c_x': data[9] | ((data[10] & 0x0F) << 8),
                        'c_y': (data[10] >> 4) | (data[11] << 4),
                    })
            except:
                pass
        
        if len(samples) < 3:
            print("  ✗ Not enough samples for calibration")
            return False
        
        # Calculate average (median might be better, but average is simpler)
        self.calibration['main_x_center'] = int(sum(s['main_x'] for s in samples) / len(samples))
        self.calibration['main_y_center'] = int(sum(s['main_y'] for s in samples) / len(samples))
        self.calibration['c_x_center'] = int(sum(s['c_x'] for s in samples) / len(samples))
        self.calibration['c_y_center'] = int(sum(s['c_y'] for s in samples) / len(samples))
        self.calibration['calibrated'] = True
        
        print(f"  ✓ Calibration complete:")
        print(f"    Main stick center: X={self.calibration['main_x_center']}, Y={self.calibration['main_y_center']}")
        print(f"    C-stick center: X={self.calibration['c_x_center']}, Y={self.calibration['c_y_center']}")
        return True
    
    def parse_input(self, data, report_id_offset=0, ble_layout=False):
        """Parse HID input data based on discovered format.

        report_id_offset: If BLE has a leading byte, pass 1 so indices shift.
        ble_layout: If True, use Nintendo standard BLE report button bytes (3/4/5).
                   If False, use USB/discovered format (unchanged).
        """
        o = report_id_offset
        if len(data) < 12 + o:  # Need at least 12 bytes for sticks (bytes 6-11) after offset
            return None

        if ble_layout:
            # BLE only: Nintendo standard input report (dekuNukem bluetooth_hid_notes)
            # Byte 3: Y, X, B, A, R, ZR  |  Byte 4: Minus, Plus, Home, Capture  |  Byte 5: Dpad, L, ZL
            b3, b4, b5 = data[3 + o], data[4 + o], data[5 + o]
            buttons = {
                'Y': (b3 & 0x01) != 0,
                'X': (b3 & 0x02) != 0,
                'B': (b3 & 0x04) != 0,
                'A': (b3 & 0x08) != 0,
                'R': (b3 & 0x10) != 0,
                'Z': (b3 & 0x20) != 0,
                'Start': (b4 & 0x02) != 0,
                'Dpad_Down': (b5 & 0x01) != 0,
                'Dpad_Up': (b5 & 0x02) != 0,
                'Dpad_Right': (b5 & 0x04) != 0,
                'Dpad_Left': (b5 & 0x08) != 0,
                'L': (b5 & 0x40) != 0,
                'ZL': (b5 & 0x80) != 0,
                'Home': (b4 & 0x10) != 0,
                'Capture': (b4 & 0x20) != 0,
            }
        else:
            # USB: original discovered format (do not change)
            buttons = {
                'B': (data[3 + o] & 0x01) != 0,
                'A': (data[3 + o] & 0x02) != 0,
                'Y': (data[3 + o] & 0x04) != 0,
                'X': (data[3 + o] & 0x08) != 0,
                'R': (data[3 + o] & 0x10) != 0,
                'Z': (data[3 + o] & 0x20) != 0,
                'Start': (data[3 + o] & 0x40) != 0,
                'Dpad_Down': (data[4 + o] & 0x01) != 0,
                'Dpad_Right': (data[4 + o] & 0x02) != 0,
                'Dpad_Left': (data[4 + o] & 0x04) != 0,
                'Dpad_Up': (data[4 + o] & 0x08) != 0,
                'L': (data[4 + o] & 0x10) != 0,
                'ZL': (data[4 + o] & 0x20) != 0,
                'Home': (data[5 + o] & 0x01) != 0,
                'Capture': (data[5 + o] & 0x02) != 0,
            }

        # Analog triggers (bytes 13 and 14) - restore original working positions
        trigger_l = data[13 + o] if len(data) > 13 + o else 0
        trigger_r = data[14 + o] if len(data) > 14 + o else 0

        # Sticks - Switch HID protocol uses 12-bit nibble-packed values
        # Each stick axis is 12 bits (0-4095), packed into 3 bytes per 2 axes
        # Main stick: bytes 6-8 (X and Y packed)
        # C-stick: bytes 9-11 (X and Y packed)
        # Triggers: bytes 13-14 (working positions)
        #
        # 12-BIT NIBBLE PACKING FORMAT:
        # Main Stick X: byte6 (low 8 bits) | (byte7 & 0x0F) << 8 (high 4 bits)
        # Main Stick Y: (byte7 >> 4) (low 4 bits) | byte8 << 4 (high 8 bits)
        # C-Stick X: byte9 (low 8 bits) | (byte10 & 0x0F) << 8 (high 4 bits)
        # C-Stick Y: (byte10 >> 4) (low 4 bits) | byte11 << 4 (high 8 bits)
        #
        # MATH EXPLANATION:
        # 1. Extract 12-bit values from nibble-packed bytes (0-4095)
        # 2. Subtract calibration center (typically 2048 = 2^11) to get offset from neutral
        # 3. Result is signed integer (-2048 to +2047), no wrapping needed

        if len(data) >= 12 + o:
            # STEP 1: Extract 12-bit values from nibble-packed bytes
            # Main Stick (Left Stick) extraction
            # Byte 6: Lower 8 bits of X
            # Byte 7: Upper 4 bits of X (low nibble), Lower 4 bits of Y (high nibble)
            # Byte 8: Upper 8 bits of Y
            main_x_raw = data[6 + o] | ((data[7 + o] & 0x0F) << 8)
            main_y_raw = (data[7 + o] >> 4) | (data[8 + o] << 4)

            # C-Stick (Right Stick) extraction
            # Byte 9: Lower 8 bits of X
            # Byte 10: Upper 4 bits of X (low nibble), Lower 4 bits of Y (high nibble)
            # Byte 11: Upper 8 bits of Y
            c_x_raw = data[9 + o] | ((data[10 + o] & 0x0F) << 8)
            c_y_raw = (data[10 + o] >> 4) | (data[11 + o] << 4)

            # STEP 2: Apply calibration - subtract center to get offset from neutral
            if self.calibration['calibrated']:
                # Subtract measured center (12-bit value, typically around 2048)
                main_x = main_x_raw - self.calibration['main_x_center']
                main_y = main_y_raw - self.calibration['main_y_center']
                c_x = c_x_raw - self.calibration['c_x_center']
                c_y = c_y_raw - self.calibration['c_y_center']
            else:
                # Fallback: assume 2048 is center (2^11, middle of 12-bit range)
                main_x = main_x_raw - 2048
                main_y = main_y_raw - 2048
                c_x = c_x_raw - 2048
                c_y = c_y_raw - 2048

            # STEP 3: Final output (no Y inversion needed - controller already outputs correct direction)
            sticks = {
                # Main stick: Use calibrated values directly
                'main_x': main_x,
                'main_y': main_y,  # No inversion needed

                # C-stick: Use calibrated values directly
                'c_x': c_x,
                'c_y': c_y,  # No inversion needed

                # Store raw 12-bit values for debugging
                'main_x_raw': main_x_raw,
                'main_y_raw': main_y_raw,
                'c_x_raw': c_x_raw,
                'c_y_raw': c_y_raw,

                # Store calibrated offsets for debugging
                'main_x_offset': main_x,
                'main_y_offset': main_y,
                'c_x_offset': c_x,
                'c_y_offset': c_y,

                # Raw bytes for debugging
                'raw_bytes': {
                    'main': [data[6 + o], data[7 + o], data[8 + o]],
                    'c': [data[9 + o], data[10 + o], data[11 + o]],
                },
            }
        else:
            # Fallback
            sticks = {
                'main_x': 0, 'main_y': 0, 'c_x': 0, 'c_y': 0,
            }

        return {
            'buttons': buttons,
            'trigger_l': trigger_l,
            'trigger_r': trigger_r,
            'sticks': sticks,
            'raw': data
        }

    def _stick_12bit_from_bytes(self, b0, b1, b2):
        """Decode 12-bit nibble-packed stick axis from 3 bytes (Nintendo standard)."""
        x_raw = b0 | ((b1 & 0x0F) << 8)
        y_raw = (b1 >> 4) | (b2 << 4)
        return x_raw, y_raw

    def log_sample(self, data_list, parsed):
        """Log a sample to file with all interpretations."""
        import json
        from datetime import datetime
        
        if not self.log_file:
            return
        
        current_time = time.time()
        if current_time - self.last_log_time < self.log_interval:
            return
        
        self.last_log_time = current_time
        
        # Create comprehensive log entry
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'raw_bytes': {
                'all': data_list[:20],  # First 20 bytes
                'stick_region': {
                    'bytes_6_7': [data_list[6], data_list[7]],
                    'bytes_8_9': [data_list[8], data_list[9]],
                    'bytes_10_11': [data_list[10], data_list[11]],
                    'bytes_12_13': [data_list[12], data_list[13]],
                }
            },
            'interpretations': {
                'current': {
                    'main_x': parsed['sticks'].get('main_x', 0),
                    'main_y': parsed['sticks'].get('main_y', 0),
                    'c_x': parsed['sticks'].get('c_x', 0),
                    'c_y': parsed['sticks'].get('c_y', 0),
                },
                'alternative': {
                    'main_x_alt': parsed['sticks'].get('main_x_alt', 0),
                    'main_y_alt': parsed['sticks'].get('main_y_alt', 0),
                },
                '16bit_le': {
                    'main_x': parsed['sticks'].get('main_x_16_le', 0),
                    'main_y': parsed['sticks'].get('main_y_16_le', 0),
                    'c_x': parsed['sticks'].get('c_x_16_le', 0),
                    'c_y': parsed['sticks'].get('c_y_16_le', 0),
                }
            },
            'buttons': {k: v for k, v in parsed['buttons'].items() if v},
            'triggers': {
                'L': parsed['trigger_l'],
                'R': parsed['trigger_r']
            }
        }
        
        # Append to log file (JSON Lines format)
        try:
            with open(self.log_file, 'a') as f:
                f.write(json.dumps(log_entry) + '\n')
                f.flush()
        except Exception as e:
            print(f"Logging error: {e}")
    
    def read_loop(self):
        """Read input data from HID device."""
        last_data = None
        
        while self.running:
            try:
                data = self.hid_device.read(64)
                if data:
                    self._log_latency()
                    data_list = list(data)
                    
                    # Process all data for logging (even if unchanged)
                    parsed = self.parse_input(data_list)
                    if parsed:
                        self.current_state = parsed
                        
                        # Update DSU server if running - pass raw bytes for on-demand parsing
                        if self.dsu_server and self.dsu_server.running:
                            try:
                                # Store raw bytes for on-demand parsing (reduces latency)
                                raw_state = {'raw_bytes': data_list, 'parsed': parsed}
                                self.dsu_server.update(
                                    raw_state,
                                    pad_id=getattr(self, 'dsu_pad_id', 0),
                                    connection_type=getattr(self, 'dsu_connection_type', 0x01),
                                )
                            except Exception as e:
                                # Silently ignore DSU errors (client may not be connected)
                                pass
                        
                        # Log sample if logging enabled (every second, regardless of changes)
                        if self.log_file:
                            self.log_sample(data_list, parsed)
                        
                        # Only update display if data changed
                        if data_list != last_data:
                            if self.use_gui and self.gui_window:
                                # Update GUI
                                self.gui_window.update_state(parsed)
                        
                        last_data = data_list
            except Exception as e:
                if 'timeout' not in str(e).lower():
                    if not self.use_gui:
                        print(f"\nRead error: {e}")
            
            # Yield to OS without hitting timer interrupt floor (time.sleep(0) on some systems)
            # HID is non-blocking, so we can poll aggressively
            time.sleep(0)  # Yield to OS scheduler
    
    def start(self):
        """Start the driver."""
        print("NSO GameCube Controller Driver")
        print("="*70)
        
        # Step 1: Find and initialize USB device
        if not self.find_usb_device(self.device_index):
            print("✗ Failed to find USB device")
            return False
        
        print("✓ USB device found")
        
        if not self.initialize_usb():
            print("✗ USB initialization failed")
            return False
        
        # Step 2: Open HID device for reading
        if not self.open_hid_device(self.device_index):
            print("✗ Failed to open HID device")
            print("  Make sure the device is initialized via USB first")
            return False
        
        # Step 2.5: Calibrate sticks (assume neutral position at startup)
        self.calibrate_sticks()
        
        # Step 2.6: Start DSU server if requested (only when we own it)
        if self.dsu_server and self._dsu_owned:
            if not self.dsu_server.start():
                print("⚠️  DSU server failed to start", flush=True)
                self.dsu_server = None
        
        # Step 3: Start reading
        self.running = True
        if self.log_file:
            print("Latency stats ([Latency] Avg/Jitter/Range) printed here every ~100 reports.")

        if self.use_gui:
            # Start GUI in main thread
            try:
                print(f"Initializing GUI ({GUI_TYPE})...")
                if GUI_TYPE == 'tkinter':
                    self.gui_window = ControllerGUI(self)
                elif GUI_TYPE == 'pyqt5':
                    self.gui_window = ControllerGUIPyQt5(self)
                else:
                    raise ValueError(f"Unknown GUI type: {GUI_TYPE}")
                print("GUI initialized, starting main loop...")
                self.gui_window.run()
            except Exception as e:
                print(f"✗ GUI error: {e}")
                import traceback
                traceback.print_exc()
                print("\nFalling back to terminal mode...")
                # Fall back to terminal mode
                self.use_gui = False
                read_thread = threading.Thread(target=self.read_loop, daemon=True)
                read_thread.start()
                print("✓ Driver started successfully!")
                print("\nDriver is running. Press Ctrl+C to stop.\n")
                try:
                    while self.running:
                        time.sleep(0.1)
                except KeyboardInterrupt:
                    self.stop()
        else:
            # Start reading thread
            read_thread = threading.Thread(target=self.read_loop, daemon=True)
            read_thread.start()
            
            print("✓ Driver started successfully!")
            print("\nDriver is running. Press Ctrl+C to stop.\n")
            
            try:
                while self.running:
                    time.sleep(0.1)
            except KeyboardInterrupt:
                self.stop()
        
        return True
    
    def stop(self):
        """Stop the driver."""
        self.running = False
        
        # Stop DSU server only when we own it (single-controller mode)
        if self.dsu_server and self._dsu_owned:
            self.dsu_server.stop()
        
        if self.hid_device:
            self.hid_device.close()
        if self.usb_device:
            try:
                usb.util.release_interface(self.usb_device, INTERFACE_NUM)
                usb.util.dispose_resources(self.usb_device)
            except:
                pass
        print("\nDriver stopped")


class NSOWirelessDriver(NSODriver):
    """NSO GameCube Controller over BLE (wireless). Use when controller does not show as HID."""

    def __init__(self, mac_address, report_id_offset=0, ble_report_layout='auto', ble_debug=False, ble_discover=False, **kwargs):
        super().__init__(**kwargs)
        self.address = mac_address
        self.report_id_offset = report_id_offset
        self.ble_report_layout = ble_report_layout  # 'auto' | 'standard' | 'reordered' | '0x3f'
        self.ble_debug = ble_debug
        self.ble_discover = ble_discover
        self._ble_calibration_samples = []
        self._ble_calibration_skip = 5  # skip first N reports before collecting stick center (avoid connection jitter)
        self._ble_loop = None
        self._discover_lock = threading.Lock()
        self._discover_samples = []  # list of (phase, data_list); max 300
        self._discover_phase = None
        self._log_queue = queue.Queue()
        self._log_worker_started = False
        self._init_latency_monitor()  # uses base class implementation

    def _try_set_ble_connection_interval_linux(self):
        """On Linux, request shorter BLE connection interval (7.5–15ms) before connect so reports arrive ~4x faster (USB-like).
        Requires debugfs and often root. No-op on other platforms or on failure."""
        if sys.platform != 'linux':
            return
        base = '/sys/kernel/debug/bluetooth/hci0'
        for name, val in (('conn_min_interval', BLE_CONN_MIN_INTERVAL_UNITS), ('conn_max_interval', BLE_CONN_MAX_INTERVAL_UNITS)):
            path = f'{base}/{name}'
            try:
                with open(path, 'w') as f:
                    f.write(str(val))
                if not getattr(self, '_ble_interval_logged', False):
                    print("  Requested shorter BLE connection interval (Linux debugfs).")
                    self._ble_interval_logged = True
            except (OSError, IOError):
                pass

    def parse_ble_input(self, data):
        """Parse BLE input report. Handles Nintendo formats: 0x3F (simple), reordered (sticks then buttons), standard 0x30.
        BLE differs from USB: report type in byte 0, sometimes different field order; bytes 13+ are IMU not triggers.
        """
        if len(data) < 12:
            return None
        report_id = data[0]
        # BLE 0x30 uses bytes 13+ for IMU; we set triggers from L/ZL buttons after parsing

        # --- INPUT 0x3F (simple report: buttons 1-2, stick hat 3, left stick 4-7 as 16-bit, right 8-11) ---
        if report_id == 0x3F and (self.ble_report_layout in ('auto', '0x3f')):
            # dekuNukem: Byte 1 = Down, Right, Left, Up, SL, SR; Byte 2 = Minus, Plus, LStick, RStick, Home, Capture, L, ZR
            b1, b2 = data[1], data[2]
            buttons = {
                'Dpad_Down': (b1 & 0x01) != 0,
                'Dpad_Right': (b1 & 0x02) != 0,
                'Dpad_Left': (b1 & 0x04) != 0,
                'Dpad_Up': (b1 & 0x08) != 0,
                'Start': (b2 & 0x02) != 0,   # Plus
                'Home': (b2 & 0x10) != 0,
                'Capture': (b2 & 0x20) != 0,
                'L': (b2 & 0x40) != 0,
                'Z': (b2 & 0x80) != 0,
                'Y': False, 'X': False, 'B': False, 'A': False, 'R': False, 'ZL': False,  # 0x3F may not expose these
            }
            # Sticks: 16-bit per axis (data[0]|(data[1]<<8), data[2]|(data[3]<<8))
            main_x_raw = data[4] | (data[5] << 8)
            main_y_raw = data[6] | (data[7] << 8)
            c_x_raw = data[8] | (data[9] << 8)
            c_y_raw = data[10] | (data[11] << 8)
            center = 32768
            main_x = main_x_raw - center
            main_y = main_y_raw - center
            c_x = c_x_raw - center
            c_y = c_y_raw - center
            sticks = {
                'main_x': main_x, 'main_y': main_y, 'c_x': c_x, 'c_y': c_y,
                'main_x_raw': main_x_raw, 'main_y_raw': main_y_raw, 'c_x_raw': c_x_raw, 'c_y_raw': c_y_raw,
                'main_x_offset': main_x, 'main_y_offset': main_y, 'c_x_offset': c_x, 'c_y_offset': c_y,
                'raw_bytes': {'main': data[4:8], 'c': data[8:12]},
            }
            trigger_l = 255 if buttons.get('L') else 0
            trigger_r = 255 if buttons.get('Z') else 0
            return {'buttons': buttons, 'trigger_l': trigger_l, 'trigger_r': trigger_r, 'sticks': sticks, 'raw': data}

        # --- Reordered layout (sticks then buttons): left stick 3-5, right stick 6-8, buttons 9-11) ---
        if self.ble_report_layout == 'standard':
            pass  # fall through to standard block below
        elif self.ble_report_layout in ('auto', 'reordered') and len(data) >= 12:
            # Nintendo standard button bits on bytes 9,10,11
            b3, b4, b5 = data[9], data[10], data[11]
            buttons = {
                'Y': (b3 & 0x01) != 0, 'X': (b3 & 0x02) != 0, 'B': (b3 & 0x04) != 0, 'A': (b3 & 0x08) != 0,
                'R': (b3 & 0x10) != 0, 'Z': (b3 & 0x20) != 0,
                'Start': (b4 & 0x02) != 0, 'Dpad_Down': (b5 & 0x01) != 0, 'Dpad_Up': (b5 & 0x02) != 0,
                'Dpad_Right': (b5 & 0x04) != 0, 'Dpad_Left': (b5 & 0x08) != 0,
                'L': (b5 & 0x40) != 0, 'ZL': (b5 & 0x80) != 0,
                'Home': (b4 & 0x10) != 0, 'Capture': (b4 & 0x20) != 0,
            }
            main_x_raw, main_y_raw = self._stick_12bit_from_bytes(data[3], data[4], data[5])
            c_x_raw, c_y_raw = self._stick_12bit_from_bytes(data[6], data[7], data[8])
            if self.calibration['calibrated']:
                main_x = main_x_raw - self.calibration['main_x_center']
                main_y = main_y_raw - self.calibration['main_y_center']
                c_x = c_x_raw - self.calibration['c_x_center']
                c_y = c_y_raw - self.calibration['c_y_center']
            else:
                main_x, main_y = main_x_raw - 2048, main_y_raw - 2048
                c_x, c_y = c_x_raw - 2048, c_y_raw - 2048
            sticks = {
                'main_x': main_x, 'main_y': main_y, 'c_x': c_x, 'c_y': c_y,
                'main_x_raw': main_x_raw, 'main_y_raw': main_y_raw, 'c_x_raw': c_x_raw, 'c_y_raw': c_y_raw,
                'main_x_offset': main_x, 'main_y_offset': main_y, 'c_x_offset': c_x, 'c_y_offset': c_y,
                'raw_bytes': {'main': [data[3], data[4], data[5]], 'c': [data[6], data[7], data[8]]},
            }
            trigger_l = 255 if buttons.get('ZL') else 0
            trigger_r = 255 if buttons.get('Z') else 0
            return {'buttons': buttons, 'trigger_l': trigger_l, 'trigger_r': trigger_r, 'sticks': sticks, 'raw': data}

        # --- Standard 0x30 layout: buttons 3-5, left stick 6-8, right stick 9-11 ---
        o = self.report_id_offset
        if len(data) < 12 + o:
            return None
        b3, b4, b5 = data[3 + o], data[4 + o], data[5 + o]
        buttons = {
            'Y': (b3 & 0x01) != 0, 'X': (b3 & 0x02) != 0, 'B': (b3 & 0x04) != 0, 'A': (b3 & 0x08) != 0,
            'R': (b3 & 0x10) != 0, 'Z': (b3 & 0x20) != 0,
            'Start': (b4 & 0x02) != 0, 'Dpad_Down': (b5 & 0x01) != 0, 'Dpad_Up': (b5 & 0x02) != 0,
            'Dpad_Right': (b5 & 0x04) != 0, 'Dpad_Left': (b5 & 0x08) != 0,
            'L': (b5 & 0x40) != 0, 'ZL': (b5 & 0x80) != 0,
            'Home': (b4 & 0x10) != 0, 'Capture': (b4 & 0x20) != 0,
        }
        main_x_raw, main_y_raw = self._stick_12bit_from_bytes(data[6 + o], data[7 + o], data[8 + o])
        c_x_raw, c_y_raw = self._stick_12bit_from_bytes(data[9 + o], data[10 + o], data[11 + o])
        if self.calibration['calibrated']:
            main_x = main_x_raw - self.calibration['main_x_center']
            main_y = main_y_raw - self.calibration['main_y_center']
            c_x = c_x_raw - self.calibration['c_x_center']
            c_y = c_y_raw - self.calibration['c_y_center']
        else:
            main_x, main_y = main_x_raw - 2048, main_y_raw - 2048
            c_x, c_y = c_x_raw - 2048, c_y_raw - 2048
        sticks = {
            'main_x': main_x, 'main_y': main_y, 'c_x': c_x, 'c_y': c_y,
            'main_x_raw': main_x_raw, 'main_y_raw': main_y_raw, 'c_x_raw': c_x_raw, 'c_y_raw': c_y_raw,
            'main_x_offset': main_x, 'main_y_offset': main_y, 'c_x_offset': c_x, 'c_y_offset': c_y,
            'raw_bytes': {'main': [data[6 + o], data[7 + o], data[8 + o]], 'c': [data[9 + o], data[10 + o], data[11 + o]]},
        }
        trigger_l = 255 if buttons.get('ZL') else 0
        trigger_r = 255 if buttons.get('Z') else 0
        return {'buttons': buttons, 'trigger_l': trigger_l, 'trigger_r': trigger_r, 'sticks': sticks, 'raw': data}

    def read_loop(self):
        """No-op for BLE: data comes via notifications. Keeps GUI/thread layout unchanged."""
        while self.running:
            time.sleep(0.1)

    def _parse_ble_nso(self, data):
        """
        NSO BLE Parser. Detects layout from RAW: macOS often strips Report ID so we get
        [timer, battery, btn, btn, btn, left_stick_3, right_stick_3, ...] -> buttons 2,3,4; sticks 5-7, 8-10.
        If byte 0 == 0x30 then full report: buttons 3,4,5; sticks 6-8, 9-11.
        """
        if len(data) < 11:
            return None
        # Stripped report (byte 0 = timer 0-15): buttons at 2,3,4; left stick 5,6,7; right stick 8,9,10
        if data[0] != 0x30:
            if len(data) < 11:
                return None
            b3, b4, b5 = data[2], data[3], data[4]
            lx_raw = data[5] | ((data[6] & 0x0F) << 8)
            ly_raw = (data[6] >> 4) | (data[7] << 4)
            rx_raw = data[8] | ((data[9] & 0x0F) << 8)
            ry_raw = (data[9] >> 4) | (data[10] << 4)
            stick_bytes = {'main': [data[5], data[6], data[7]], 'c': [data[8], data[9], data[10]]}
            trigger_l = data[13] if len(data) > 13 else 0
            trigger_r = data[14] if len(data) > 14 else 0
        else:
            # Full report (byte 0 = 0x30): buttons 3,4,5; left stick 6,7,8; right stick 9,10,11
            if len(data) < 12:
                return None
            b3, b4, b5 = data[3], data[4], data[5]
            lx_raw = data[6] | ((data[7] & 0x0F) << 8)
            ly_raw = (data[7] >> 4) | (data[8] << 4)
            rx_raw = data[9] | ((data[10] & 0x0F) << 8)
            ry_raw = (data[10] >> 4) | (data[11] << 4)
            stick_bytes = {'main': [data[6], data[7], data[8]], 'c': [data[9], data[10], data[11]]}
            trigger_l = data[14] if len(data) > 14 else 0
            trigger_r = data[15] if len(data) > 15 else 0
        # Nintendo standard button bits: byte 3 = Y,X,B,A,R,ZR; byte 4 = Minus,Plus,Home,Capture; byte 5 = Dpad,L,ZL
        buttons = {
            'Y': (b3 & 0x01) != 0, 'X': (b3 & 0x02) != 0, 'B': (b3 & 0x04) != 0, 'A': (b3 & 0x08) != 0,
            'R': (b3 & 0x10) != 0, 'Z': (b3 & 0x20) != 0,
            'Start': (b4 & 0x02) != 0, 'Home': (b4 & 0x10) != 0, 'Capture': (b4 & 0x20) != 0,
            'Dpad_Down': (b5 & 0x01) != 0, 'Dpad_Up': (b5 & 0x02) != 0,
            'Dpad_Right': (b5 & 0x04) != 0, 'Dpad_Left': (b5 & 0x08) != 0,
            'L': (b5 & 0x40) != 0, 'ZL': (b5 & 0x80) != 0,
        }
        if self.calibration['calibrated']:
            main_x = lx_raw - self.calibration['main_x_center']
            main_y = ly_raw - self.calibration['main_y_center']
            c_x = rx_raw - self.calibration['c_x_center']
            c_y = ry_raw - self.calibration['c_y_center']
        else:
            main_x = lx_raw - 2048
            main_y = ly_raw - 2048
            c_x = rx_raw - 2048
            c_y = ry_raw - 2048
        sticks = {
            'main_x': main_x, 'main_y': main_y, 'c_x': c_x, 'c_y': c_y,
            'main_x_raw': lx_raw, 'main_y_raw': ly_raw, 'c_x_raw': rx_raw, 'c_y_raw': ry_raw,
            'main_x_offset': main_x, 'main_y_offset': main_y, 'c_x_offset': c_x, 'c_y_offset': c_y,
            'raw_bytes': stick_bytes,
        }
        if trigger_l == 0 and trigger_r == 0:
            trigger_l = 255 if buttons.get('ZL') else 0
            trigger_r = 255 if buttons.get('Z') else 0
        return {'buttons': buttons, 'trigger_l': trigger_l, 'trigger_r': trigger_r, 'sticks': sticks, 'raw': data}

    def _parse_ble_63_discovered(self, data):
        """Parse 63-byte BLE report from --ble-discover mapping.
        Buttons: byte2 (B,A,Y,X,R,Z,Start), byte3 (Dpad_Down,Right,Left,Up,L,ZL), byte4 (Home,Capture).
        Sticks: main 5-7 (12-bit nibble packed), c-stick 8-10. Triggers: 12,13 or digital from ZL/Z.
        """
        if len(data) < 11:
            return None
        b2, b3, b4 = data[2], data[3], data[4]
        buttons = {
            'B': (b2 & 0x01) != 0, 'A': (b2 & 0x02) != 0, 'Y': (b2 & 0x04) != 0, 'X': (b2 & 0x08) != 0,
            'R': (b2 & 0x10) != 0, 'Z': (b2 & 0x20) != 0, 'Start': (b2 & 0x40) != 0,
            'Dpad_Down': (b3 & 0x01) != 0, 'Dpad_Right': (b3 & 0x02) != 0, 'Dpad_Left': (b3 & 0x04) != 0,
            'Dpad_Up': (b3 & 0x08) != 0, 'L': (b3 & 0x10) != 0, 'ZL': (b3 & 0x20) != 0,
            'Home': (b4 & 0x01) != 0, 'Capture': (b4 & 0x02) != 0,
        }
        main_x_raw = data[5] | ((data[6] & 0x0F) << 8)
        main_y_raw = (data[6] >> 4) | (data[7] << 4)
        c_x_raw = data[8] | ((data[9] & 0x0F) << 8)
        c_y_raw = (data[9] >> 4) | (data[10] << 4)
        if self.calibration['calibrated']:
            main_x = main_x_raw - self.calibration['main_x_center']
            main_y = main_y_raw - self.calibration['main_y_center']
            c_x = c_x_raw - self.calibration['c_x_center']
            c_y = c_y_raw - self.calibration['c_y_center']
        else:
            main_x = main_x_raw - 2048
            main_y = main_y_raw - 2048
            c_x = c_x_raw - 2048
            c_y = c_y_raw - 2048
        sticks = {
            'main_x': main_x, 'main_y': main_y, 'c_x': c_x, 'c_y': c_y,
            'main_x_raw': main_x_raw, 'main_y_raw': main_y_raw, 'c_x_raw': c_x_raw, 'c_y_raw': c_y_raw,
            'main_x_offset': main_x, 'main_y_offset': main_y, 'c_x_offset': c_x, 'c_y_offset': c_y,
            'raw_bytes': {'main': [data[5], data[6], data[7]], 'c': [data[8], data[9], data[10]]},
        }
        trigger_l = data[12] if len(data) > 12 else 0
        trigger_r = data[13] if len(data) > 13 else 0
        if trigger_l == 0 and trigger_r == 0:
            trigger_l = 255 if buttons.get('ZL') else 0
            trigger_r = 255 if buttons.get('Z') else 0
        return {'buttons': buttons, 'trigger_l': trigger_l, 'trigger_r': trigger_r, 'sticks': sticks, 'raw': data}

    def _parse_ble_blueretro(self, data):
        """Parse BLE input using BlueRetro SW2 GC layout (main/adapter/wireless/sw2.c struct sw2_map).
        Layout: bytes 0-3 tbd, 4-7 buttons (uint32 LE), 8-9 tbd, 10-15 axes[6] (left 10-12, right 13-15), 16-59 tbd, 60-61 triggers.
        Button bits (sw2_gc_btns_mask): 8=L, 9=R, 10=D, 11=U, 16=B, 17=X, 18=A, 19=Y, 20=Plus, 21=C, 22=Home, 23=Capture, 25=ZL, 26=L, 29=ZR, 30=R.
        """
        if len(data) < 62:
            return None
        buttons_u32 = data[4] | (data[5] << 8) | (data[6] << 16) | (data[7] << 24)
        def bit(b): return (buttons_u32 >> b) & 1
        buttons = {
            'Dpad_Left': bit(8), 'Dpad_Right': bit(9), 'Dpad_Down': bit(10), 'Dpad_Up': bit(11),
            'B': bit(16), 'X': bit(17), 'A': bit(18), 'Y': bit(19),
            'Start': bit(20),  # Plus
            'Home': bit(22), 'Capture': bit(23),
            'ZL': bit(25), 'L': bit(26), 'Z': bit(29), 'R': bit(30),
        }
        # Sticks: bytes 10-12 (left), 13-15 (right), same 12-bit nibble packing
        main_x_raw = data[10] | ((data[11] & 0x0F) << 8)
        main_y_raw = (data[11] >> 4) | (data[12] << 4)
        c_x_raw = data[13] | ((data[14] & 0x0F) << 8)
        c_y_raw = (data[14] >> 4) | (data[15] << 4)
        if self.calibration['calibrated']:
            main_x = main_x_raw - self.calibration['main_x_center']
            main_y = main_y_raw - self.calibration['main_y_center']
            c_x = c_x_raw - self.calibration['c_x_center']
            c_y = c_y_raw - self.calibration['c_y_center']
        else:
            main_x = main_x_raw - 2048
            main_y = main_y_raw - 2048
            c_x = c_x_raw - 2048
            c_y = c_y_raw - 2048
        sticks = {
            'main_x': main_x, 'main_y': main_y, 'c_x': c_x, 'c_y': c_y,
            'main_x_raw': main_x_raw, 'main_y_raw': main_y_raw, 'c_x_raw': c_x_raw, 'c_y_raw': c_y_raw,
            'main_x_offset': main_x, 'main_y_offset': main_y, 'c_x_offset': c_x, 'c_y_offset': c_y,
            'raw_bytes': {'main': [data[10], data[11], data[12]], 'c': [data[13], data[14], data[15]]},
        }
        trigger_l = data[60] if len(data) > 60 else 0
        trigger_r = data[61] if len(data) > 61 else 0
        return {'buttons': buttons, 'trigger_l': trigger_l, 'trigger_r': trigger_r, 'sticks': sticks, 'raw': data}

    def _notification_handler(self, sender, data):
        """Handle BLE input report notifications. Native NSO (sliding-window) first; 63-byte = BlueRetro layout."""
        self._log_latency()
        data_list = list(data)
        if getattr(self, 'ble_discover', False) and getattr(self, '_discover_phase', None):
            with self._discover_lock:
                self._discover_samples.append((self._discover_phase, data_list))
                if len(self._discover_samples) > 300:
                    self._discover_samples.pop(0)
        # RAW dump for offset verification (--ble-debug): Neutral / Hold A / Hold Stick Left -> which index changed?
        if getattr(self, 'ble_debug', False):
            if not hasattr(self, '_ble_raw_count'):
                self._ble_raw_count = 0
            if self._ble_raw_count < 10:
                self._ble_raw_count += 1
                print(f"RAW: {list(data_list[:16])}")
                if self._ble_raw_count == 1:
                    print("  (Neutral note; Hold A -> which index changed? Hold Stick Left -> which 2-3 indices? That gives button byte and stick block.)")
        # 63-byte report = discovered layout (buttons 2,3,4; sticks 5-7, 8-10); 62-byte = BlueRetro; else NSO
        if len(data_list) == 63:
            parsed = self._parse_ble_63_discovered(data_list)
        elif len(data_list) >= 62:
            parsed = self._parse_ble_blueretro(data_list)
        else:
            parsed = self._parse_ble_nso(data_list)
        if not parsed:
            parsed = self.parse_input(data_list, report_id_offset=self.report_id_offset, ble_layout=False)
        if not parsed:
            return

        # Deferred calibration from parsed stick raw values (median over 50 samples, skip first few reports).
        # Run median computation in a background thread so the notification callback returns immediately.
        if not self.calibration['calibrated'] and 'sticks' in parsed and 'main_x_raw' in parsed['sticks']:
            if getattr(self, '_ble_calibration_skip', 0) > 0:
                self._ble_calibration_skip -= 1
            else:
                s = parsed['sticks']
                self._ble_calibration_samples.append({
                    'main_x': s['main_x_raw'], 'main_y': s['main_y_raw'],
                    'c_x': s['c_x_raw'], 'c_y': s['c_y_raw'],
                })
                if len(self._ble_calibration_samples) >= 50:
                    samples = list(self._ble_calibration_samples)
                    self._ble_calibration_samples.clear()

                    def _apply_calibration():
                        def median(vals):
                            srt = sorted(vals)
                            return srt[len(srt) // 2]
                        self.calibration['main_x_center'] = median(s['main_x'] for s in samples)
                        self.calibration['main_y_center'] = median(s['main_y'] for s in samples)
                        self.calibration['c_x_center'] = median(s['c_x'] for s in samples)
                        self.calibration['c_y_center'] = median(s['c_y'] for s in samples)
                        self.calibration['calibrated'] = True
                        print("  ✓ BLE stick calibration complete (median of 50 samples)")

                    threading.Thread(target=_apply_calibration, daemon=True).start()

        self.current_state = parsed

        if self.dsu_server and self.dsu_server.running:
            try:
                raw_state = {'raw_bytes': data_list, 'parsed': parsed}
                self.dsu_server.update(
                    raw_state,
                    pad_id=getattr(self, 'dsu_pad_id', 0),
                    connection_type=getattr(self, 'dsu_connection_type', 0x02),
                )
            except Exception:
                pass

        if self.log_file:
            try:
                self._log_queue.put_nowait((list(data_list), parsed))
            except queue.Full:
                pass

        if self.use_gui and self.gui_window:
            if hasattr(self.gui_window, 'root'):
                self.gui_window.root.after(0, lambda p=parsed: self.gui_window.update_state(p))
            # PyQt5 uses a timer that reads current_state in update_display(); no call needed

    def _discover_collect(self, phase, duration_sec=2.5):
        """Set discover phase, wait for samples, return list of raw data lists (and clear buffer)."""
        self._discover_phase = phase
        time.sleep(duration_sec)
        with self._discover_lock:
            samples = [d for _, d in self._discover_samples]
            self._discover_samples.clear()
        self._discover_phase = None
        return samples

    def run_discover_flow(self):
        """Interactive BLE calibration: prompt for each input, collect raw reports, print byte map.
        Run after BLE thread is started; blocks until flow is done.
        """
        print("\n" + "=" * 70)
        print("BLE DISCOVER MODE – we will map each button and stick to raw bytes.")
        print("Ignore stick drift: we only care which bytes change when you move a stick.")
        print("=" * 70)

        # Wait for connection and neutral baseline
        for attempt in range(30):
            print("\n1. Release ALL buttons and put both sticks at CENTER. Press Enter when ready...")
            input()
            samples = self._discover_collect("neutral", 2.5)
            if len(samples) < 5:
                print(f"   No data yet ({len(samples)} samples). Is the controller connected? Retry.")
                continue
            break
        else:
            print("   Could not get enough samples. Exiting discover.")
            return

        # Baseline: per-byte mode (most common value). Use longest report length.
        length = max(len(d) for d in samples)
        baseline = []
        for i in range(length):
            vals = [d[i] for d in samples if len(d) > i]
            if not vals:
                baseline.append(0)
                continue
            from collections import Counter
            baseline.append(Counter(vals).most_common(1)[0][0])
        print(f"   Baseline captured ({len(samples)} samples, report length {length} bytes).")
        print(f"   First 16 bytes (baseline): {list(baseline[:16])}\n")

        # Buttons: which byte+bit turns ON when we hold the button
        button_steps = [
            "A", "B", "X", "Y", "Start", "Dpad_Up", "Dpad_Down", "Dpad_Left", "Dpad_Right",
            "L", "R", "Z", "ZL", "Home", "Capture",
        ]
        button_results = []
        for name in button_steps:
            print(f"   Hold ONLY [{name}]. Press Enter when holding...")
            input()
            samples = self._discover_collect(name, 2.5)
            if len(samples) < 3:
                print(f"      (too few samples, skipping)")
                continue
            found = []
            for bi in range(1, min(length, 16)):
                base_byte = baseline[bi] if bi < len(baseline) else 0
                for bit in range(8):
                    mask = 1 << bit
                    base_set = (base_byte & mask) != 0
                    action_set_count = sum(1 for d in samples if len(d) > bi and (d[bi] & mask) != 0)
                    if not base_set and action_set_count >= max(2, len(samples) * 0.7):
                        button_results.append((name, bi, bit, mask))
                        found.append((bi, bit, mask))
            if found:
                for bi, bit, mask in found:
                    print(f"      -> byte {bi} bit {bit} (mask 0x{mask:02X})")
            else:
                for bi in range(1, min(length, 16)):
                    base_byte = baseline[bi] if bi < len(baseline) else 0
                    vals = [d[bi] for d in samples if len(d) > bi]
                    if vals and (min(vals) != max(vals) or vals[0] != base_byte):
                        print(f"      -> byte {bi} changed (baseline 0x{base_byte:02X}, saw {min(vals)}-{max(vals)})")
                        break
                else:
                    print(f"      -> no clear byte/bit change")

        # Sticks: which bytes change when we move each axis
        stick_steps = [
            ("Main stick LEFT", "main_l"),
            ("Main stick RIGHT", "main_r"),
            ("Main stick UP", "main_u"),
            ("Main stick DOWN", "main_d"),
            ("C-stick LEFT", "c_l"),
            ("C-stick RIGHT", "c_r"),
            ("C-stick UP", "c_u"),
            ("C-stick DOWN", "c_d"),
        ]
        stick_results = []
        for label, key in stick_steps:
            print(f"   Move {label} only (others center). Press Enter when holding...")
            input()
            samples = self._discover_collect(key, 2.5)
            if len(samples) < 3:
                print(f"      (too few samples, skipping)")
                continue
            # Which byte indices differ from baseline (beyond noise)? Use range.
            changed = []
            for bi in range(1, length):
                base_byte = baseline[bi] if bi < len(baseline) else 0
                vals = [d[bi] for d in samples if len(d) > bi]
                if not vals:
                    continue
                lo, hi = min(vals), max(vals)
                if hi - lo > 2 or (base_byte != (sum(vals) // len(vals)) and (max(abs(v - base_byte) for v in vals) > 2)):
                    changed.append((bi, base_byte, lo, hi, vals))
            if changed:
                s = ", ".join(f"byte{i}: base 0x{b:02X} range [{lo}-{hi}]" for (i, b, lo, hi, _) in changed)
                stick_results.append((key, changed))
                print(f"      -> {s}")
            else:
                print(f"      -> no clear byte change (stick may be in same byte as timer/drift)")

        # Summary
        print("\n" + "=" * 70)
        print("SUMMARY – Button map (byte index, bit mask):")
        for name, bi, bit, mask in button_results:
            print(f"  {name}: byte {bi} bit {bit} (0x{mask:02X})")
        print("\nStick bytes that changed per direction:")
        for key, changes in stick_results:
            indices = [c[0] for c in changes]
            print(f"  {key}: bytes {indices}")
        print("=" * 70)

    async def _try_handshake(self, addr):
        """Connect and try Nintendo BLE handshake on any writable characteristic; return True if one accepts."""
        try:
            async with BleakClient(addr, timeout=BLE_HANDSHAKE_TRY_TIMEOUT_SEC) as client:
                for svc in client.services:
                    for char in svc.characteristics:
                        props = getattr(char, "properties", []) or []
                        if "write" in props or "write-without-response" in props:
                            try:
                                await client.write_gatt_char(char.uuid, BLE_HANDSHAKE_READ_SPI)
                                return True
                            except Exception:
                                try:
                                    await client.write_gatt_char(char.uuid, bytearray([0x01, 0x01]))
                                    return True
                                except Exception:
                                    pass
                return False
        except Exception:
            return False

    async def _discover_controller_address(self):
        """Scan for BLE devices, try handshake on each; return address of first that accepts, or None."""
        if BleakScanner is None:
            return None
        devices = await BleakScanner.discover(timeout=BLE_SCAN_AUTO_SEC)
        if not devices:
            return None
        name_by_addr = {d.address: (d.name or "(no name)").strip() for d in devices}
        def sort_key(addr):
            name = (name_by_addr.get(addr, "") or "").lower()
            return (0 if name == "devicename" else 1, 0 if "nintendo" in name else 1, addr)
        ordered = sorted(devices, key=lambda d: sort_key(d.address))
        for d in ordered:
            if await self._try_handshake(d.address):
                return d.address
        return None

    async def _run_wireless_async(self):
        """Connect over BLE, discover notify/write characteristics, handshake, and receive input reports.
        Nintendo BLE (SW2) may not expose standard 0x2A4d; we discover from the device.
        When scanning (no address), we connect once to the first device that accepts handshake and stay
        connected so the controller is not bumped out of pairing mode by a connect-then-disconnect.
        """
        try:
            self._ble_loop = asyncio.get_event_loop()
            while self.running:
                try:
                    self._try_set_ble_connection_interval_linux()
                    if not self.address:
                        # Scan then connect once to first device that accepts handshake (no disconnect in between).
                        print("Scanning for controller... Hold the pair button.", flush=True)
                        print(f"  Scanning for {BLE_SCAN_AUTO_SEC} seconds...", flush=True)
                        if BleakScanner is None:
                            await asyncio.sleep(BLE_CONNECT_RETRY_SEC)
                            continue
                        # Single scan; use return_adv for RSSI so we try closest device first (likely the controller)
                        try:
                            discovered = await BleakScanner.discover(timeout=BLE_SCAN_AUTO_SEC, return_adv=True)
                        except TypeError:
                            discovered = {d.address: (d, None) for d in await BleakScanner.discover(timeout=BLE_SCAN_AUTO_SEC)}
                        devices = [d for d, _ in discovered.values()]
                        if not devices:
                            print("  No controller found. Hold the pair button and we'll retry.", flush=True)
                            await asyncio.sleep(BLE_CONNECT_RETRY_SEC)
                            continue
                        print(f"  Found {len(devices)} device(s), trying to connect (strongest signal first)...", flush=True)
                        name_by_addr = {d.address: (d.name or "(no name)").strip() for d in devices}
                        def _sort_key(d):
                            addr = d.address
                            name = (name_by_addr.get(addr, "") or "").lower()
                            rssi = -999
                            if addr in discovered:
                                _, adv = discovered[addr]
                                if adv is not None and hasattr(adv, 'rssi') and adv.rssi is not None:
                                    rssi = adv.rssi
                            # Strongest RSSI first, then devicename, then nintendo, then address
                            return (-rssi, 0 if name == "devicename" else 1, 0 if "nintendo" in name else 1, addr)
                        ordered = sorted(devices, key=_sort_key)
                        client = None
                        handshake_char = None
                        for d in ordered:
                            c = BleakClient(d.address, timeout=10.0)
                            try:
                                await c.connect()
                                for svc in c.services:
                                    for char in svc.characteristics:
                                        props = getattr(char, "properties", []) or []
                                        if "write" not in props and "write-without-response" not in props:
                                            continue
                                        try:
                                            await c.write_gatt_char(char.uuid, BLE_HANDSHAKE_READ_SPI)
                                            handshake_char = char
                                            break
                                        except Exception:
                                            try:
                                                await c.write_gatt_char(char.uuid, bytearray([0x01, 0x01]))
                                                handshake_char = char
                                                break
                                            except Exception:
                                                pass
                                    if handshake_char is not None:
                                        break
                                if handshake_char is None:
                                    await c.disconnect()
                                    continue
                                client = c
                                self.address = d.address
                                print(f"  Found controller at {self.address}", flush=True)
                                break
                            except Exception:
                                try:
                                    await c.disconnect()
                                except Exception:
                                    pass
                                continue
                        if client is None:
                            print("  No controller found. Hold the pair button and we'll retry.", flush=True)
                            await asyncio.sleep(BLE_CONNECT_RETRY_SEC)
                            continue
                        # Stay connected: subscribe, send LED/slot, then main loop (no second connect).
                        try:
                            print("✓ Connected! Discovering characteristics...", flush=True)
                            notify_chars = []
                            for svc in client.services:
                                for char in svc.characteristics:
                                    props = getattr(char, "properties", []) or []
                                    if "notify" in props or "indicate" in props:
                                        notify_chars.append(char)
                            if not notify_chars:
                                raise RuntimeError("No notify/indicate characteristic found")
                            print(f"  Subscribing to {len(notify_chars)} notify char(s)...", flush=True)
                            if self.log_file:
                                print("  Latency stats ([Latency] Avg/Jitter/Range) printed here every ~100 reports.")
                            for char in notify_chars:
                                await client.start_notify(char.uuid, self._notification_handler)
                            if handshake_char:
                                for data in (bytearray(DEFAULT_REPORT_DATA), bytearray(SET_LED_DATA)):
                                    try:
                                        await client.write_gatt_char(handshake_char.uuid, data)
                                    except Exception:
                                        pass
                                try:
                                    await client.write_gatt_char(handshake_char.uuid, SET_INPUT_MODE)
                                except Exception:
                                    pass
                                print("  ✓ Slot/LED report sent (controller may stop blinking)", flush=True)
                            try:
                                from controller_storage import set_last_connected
                                set_last_connected(self.address)
                            except Exception:
                                pass
                            while self.running:
                                await asyncio.sleep(0.1)
                        finally:
                            try:
                                await client.disconnect()
                            except Exception:
                                pass
                        break
                    else:
                        # Address already known (e.g. --address): connect as before.
                        print(f"Connecting to {self.address}...", flush=True)
                        async with BleakClient(self.address, timeout=10.0) as client:
                            print("✓ Connected! Discovering characteristics...", flush=True)
                            notify_chars = []
                            write_chars = []
                            for svc in client.services:
                                for char in svc.characteristics:
                                    props = getattr(char, "properties", []) or []
                                    if "notify" in props or "indicate" in props:
                                        notify_chars.append(char)
                                    if "write" in props or "write-without-response" in props:
                                        write_chars.append(char)
                            if not notify_chars:
                                raise RuntimeError("No notify/indicate characteristic found")
                            if not write_chars:
                                raise RuntimeError("No write characteristic found")
                            print(f"  Subscribing to {len(notify_chars)} notify char(s), trying handshake on {len(write_chars)} write char(s)...", flush=True)
                            if self.log_file:
                                print("  Latency stats ([Latency] Avg/Jitter/Range) printed here every ~100 reports.")
                            for char in notify_chars:
                                await client.start_notify(char.uuid, self._notification_handler)
                            handshake_done = False
                            handshake_char = None
                            for char in write_chars:
                                try:
                                    await client.write_gatt_char(char.uuid, BLE_HANDSHAKE_READ_SPI)
                                    handshake_done = True
                                    handshake_char = char
                                    break
                                except Exception:
                                    try:
                                        await client.write_gatt_char(char.uuid, bytearray([0x01, 0x01]))
                                        handshake_done = True
                                        handshake_char = char
                                        break
                                    except Exception:
                                        pass
                            if not handshake_done:
                                print("  (Handshake write failed on all write chars; continuing for input reports.)")
                            if handshake_char:
                                for data in (bytearray(DEFAULT_REPORT_DATA), bytearray(SET_LED_DATA)):
                                    try:
                                        await client.write_gatt_char(handshake_char.uuid, data)
                                    except Exception:
                                        pass
                                try:
                                    await client.write_gatt_char(handshake_char.uuid, SET_INPUT_MODE)
                                except Exception:
                                    pass
                                print("  ✓ Slot/LED report sent (controller may stop blinking)", flush=True)
                            try:
                                from controller_storage import set_last_connected
                                set_last_connected(self.address)
                            except Exception:
                                pass
                            while self.running:
                                await asyncio.sleep(0.1)
                        break
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    if not self.running:
                        break
                    print(f"  Not yet: {e}", flush=True)
                    print(f"  Retrying in {BLE_CONNECT_RETRY_SEC}s...", flush=True)
                    await asyncio.sleep(BLE_CONNECT_RETRY_SEC)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            if self.running:
                print(f"\nBLE error: {e}", flush=True)
        finally:
            self._ble_loop = None

    def start(self):
        """Start the wireless driver (BLE only, no USB/HID)."""
        print("NSO GameCube Controller Driver (BLE)")
        print("=" * 70)
        if self.address:
            print("Using controller address:", self.address)
        else:
            print("No address given: we'll scan for the controller. Hold the pair button when starting.")
        print("Controller not in system Bluetooth list? Start this script first, then hold pair.")
        print("Multiple controllers? Use --ble-scan to get addresses, then --ble --address <ADDR>.")
        print("Won't connect after restart? Remove from System Settings > Bluetooth, then pair again.")

        if not BLE_AVAILABLE:
            print("✗ bleak not installed. Run: pip install bleak")
            return False

        if self.dsu_server and getattr(self, '_dsu_owned', True) and not self.dsu_server.start():
            print("⚠️  DSU server failed to start", flush=True)
            self.dsu_server = None

        self.running = True

        def _log_worker():
            while self.running:
                try:
                    data_list, parsed = self._log_queue.get(timeout=0.25)
                    if self.log_file:
                        self.log_sample(data_list, parsed)
                except queue.Empty:
                    continue
                except Exception:
                    pass

        if not self._log_worker_started:
            self._log_worker_started = True
            threading.Thread(target=_log_worker, daemon=True).start()

        def run_ble():
            asyncio.run(self._run_wireless_async())

        ble_thread = threading.Thread(target=run_ble, daemon=True)
        ble_thread.start()

        if self.ble_discover:
            time.sleep(1)
            self.run_discover_flow()
            self.stop()
            return True

        if self.use_gui:
            try:
                print(f"Initializing GUI ({GUI_TYPE})...")
                if GUI_TYPE == 'tkinter':
                    self.gui_window = ControllerGUI(self)
                elif GUI_TYPE == 'pyqt5':
                    self.gui_window = ControllerGUIPyQt5(self)
                else:
                    raise ValueError(f"Unknown GUI type: {GUI_TYPE}")
                print("GUI initialized, starting main loop...")
                self.gui_window.run()
            except Exception as e:
                print(f"✗ GUI error: {e}")
                import traceback
                traceback.print_exc()
                print("\nFalling back to terminal mode...")
                self.use_gui = False
                print("✓ Driver started successfully!")
                print("\nDriver is running. Press Ctrl+C to stop.\n")
                try:
                    while self.running:
                        time.sleep(0.1)
                except KeyboardInterrupt:
                    self.stop()
        else:
            print("✓ Driver started successfully!")
            print("\nDriver is running. Press Ctrl+C to stop.\n")
            try:
                while self.running:
                    time.sleep(0.1)
            except KeyboardInterrupt:
                self.stop()

        return True

    def stop(self):
        """Stop the wireless driver."""
        self.running = False
        if self.dsu_server and getattr(self, '_dsu_owned', True):
            self.dsu_server.stop()
        if self.hid_device:
            self.hid_device.close()
        if self.usb_device:
            try:
                usb.util.release_interface(self.usb_device, INTERFACE_NUM)
                usb.util.dispose_resources(self.usb_device)
            except Exception:
                pass
        print("\nDriver stopped")


def count_usb_controllers() -> int:
    """Return number of NSO USB controllers connected."""
    try:
        return len(list(usb.core.find(find_all=True, idVendor=VID, idProduct=PID)))
    except Exception:
        return 0


def count_hid_controllers() -> int:
    """Return number of NSO HID devices (should match USB count)."""
    try:
        return len(hid.enumerate(VID, PID))
    except Exception:
        return 0


class MultiControllerDriver:
    """
    Coordinates multiple controllers (USB and/or BLE) sharing one DSU server.
    slots_config: list of dicts, each {slot: 0-3, type: 'usb'|'ble', address?: str for BLE}
    """

    def __init__(self, slots_config, use_dsu=True, use_gui=False, log_file=None, debug=False):
        self.slots_config = slots_config
        self.use_dsu = use_dsu and DSU_AVAILABLE
        self.use_gui = use_gui and GUI_AVAILABLE
        self.log_file = log_file
        self.debug = debug
        self.running = False
        self.dsu_server = DSUServer() if self.use_dsu else None
        self.drivers = []
        self._threads = []

    def _create_drivers(self):
        """Create driver instances from slots_config."""
        usb_index = 0
        for cfg in self.slots_config:
            slot = cfg.get('slot', 0)
            ctype = cfg.get('type', 'usb')
            if ctype == 'usb':
                driver = NSODriver(
                    use_gui=False,
                    log_file=self.log_file,
                    use_dsu=False,
                    debug=self.debug,
                    dsu_server=self.dsu_server,
                    dsu_pad_id=slot,
                    dsu_connection_type=0x01,
                    device_index=usb_index,
                )
                driver._dsu_owned = False
                usb_index += 1
                self.drivers.append(driver)
            elif ctype == 'ble':
                addr = cfg.get('address', '')
                driver = NSOWirelessDriver(
                    mac_address=addr or None,
                    use_gui=False,
                    log_file=self.log_file,
                    use_dsu=False,
                    debug=self.debug,
                    dsu_server=self.dsu_server,
                    dsu_pad_id=slot,
                    dsu_connection_type=0x02,
                )
                driver._dsu_owned = False
                self.drivers.append(driver)

    def start(self):
        """Start all controllers. Each driver runs in its own thread."""
        print("NSO GameCube Controller Bridge (Multi-Controller)")
        print("=" * 70)
        self._create_drivers()
        if not self.drivers:
            print("✗ No controllers configured")
            return False

        if self.dsu_server and not self.dsu_server.start():
            print("⚠️  DSU server failed to start", flush=True)
            self.dsu_server = None

        self.running = True

        def run_driver(d):
            try:
                d.start()
            except Exception as e:
                print(f"✗ Driver error: {e}", flush=True)
                import traceback
                traceback.print_exc()

        for driver in self.drivers:
            t = threading.Thread(target=run_driver, args=(driver,), daemon=True)
            t.start()
            self._threads.append(t)

        print("✓ Multi-controller driver started")
        print("\nDriver is running. Press Ctrl+C to stop.\n")
        try:
            while self.running:
                time.sleep(0.1)
        except KeyboardInterrupt:
            self.stop()
        return True

    def stop(self):
        """Stop all drivers and DSU server."""
        self.running = False
        for driver in self.drivers:
            driver.running = False
            driver.stop()
        if self.dsu_server:
            self.dsu_server.stop()
        self.drivers.clear()
        self._threads.clear()
        print("\nDriver stopped")


class StickWidget:
    """Custom widget for drawing stick position (PyQt5 wrapper)."""
    
    def __init__(self, width, height):
        from PyQt5.QtWidgets import QWidget
        from PyQt5.QtGui import QPainter, QColor, QPen
        
        # Create a custom QWidget
        class _StickWidget(QWidget):
            def __init__(self, parent_stick_widget, w, h):
                super().__init__()
                self.parent_stick = parent_stick_widget
                self.setFixedSize(w, h)
                self.stick_x = 0
                self.stick_y = 0
                self.max_range = 128
                
            def set_stick_position(self, x, y):
                self.stick_x = max(-self.max_range, min(self.max_range, x))
                self.stick_y = max(-self.max_range, min(self.max_range, y))
                self.update()
                
            def paintEvent(self, event):
                painter = QPainter(self)
                painter.setRenderHint(QPainter.Antialiasing)
                
                w = self.width()
                h = self.height()
                center_x = w // 2
                center_y = h // 2
                radius = min(w, h) // 2 - 10
                
                # Draw background circle (ring)
                painter.setPen(QPen(QColor("gray"), 2))
                painter.drawEllipse(center_x - radius, center_y - radius, radius * 2, radius * 2)
                
                # Draw center crosshair
                painter.setPen(QPen(QColor("lightgray"), 1))
                painter.drawLine(center_x - radius, center_y, center_x + radius, center_y)
                painter.drawLine(center_x, center_y - radius, center_x, center_y + radius)
                
                # Draw center dot
                painter.setBrush(QColor("lightgray"))
                painter.drawEllipse(center_x - 2, center_y - 2, 4, 4)
                
                # Draw stick position dot
                stick_x = center_x + int((self.stick_x / self.max_range) * radius)
                stick_y = center_y - int((self.stick_y / self.max_range) * radius)  # Invert Y
                
                painter.setBrush(QColor("blue"))
                painter.setPen(QPen(QColor("darkblue"), 2))
                painter.drawEllipse(stick_x - 8, stick_y - 8, 16, 16)
        
        self.widget = _StickWidget(self, width, height)
        
    def set_stick_position(self, x, y):
        """Update stick position and trigger repaint."""
        self.widget.set_stick_position(x, y)
        
    def __getattr__(self, name):
        """Delegate attribute access to the widget."""
        return getattr(self.widget, name)


class ControllerGUIPyQt5:
    """PyQt5 GUI for visualizing controller input."""
    
    def __init__(self, driver):
        from PyQt5.QtWidgets import (QApplication, QWidget, QLabel, QVBoxLayout, 
                                     QHBoxLayout, QFrame, QGridLayout)
        from PyQt5.QtCore import Qt, QTimer
        from PyQt5.QtGui import QPainter, QColor, QPen, QFont
        
        self.driver = driver
        self.app = QApplication.instance()
        if self.app is None:
            self.app = QApplication(sys.argv)
        
        self.window = QWidget()
        self.window.setWindowTitle("NSO GameCube Controller")
        self.window.setGeometry(100, 100, 800, 600)
        
        # Start read loop in background thread
        read_thread = threading.Thread(target=driver.read_loop, daemon=True)
        read_thread.start()
        
        self.setup_ui()
        
        # Setup update timer
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_display)
        self.timer.start(16)  # ~60 FPS
        
    def setup_ui(self):
        """Setup the GUI elements."""
        from PyQt5.QtWidgets import (QVBoxLayout, QHBoxLayout, QLabel, QFrame, QGridLayout)
        from PyQt5.QtCore import Qt
        from PyQt5.QtGui import QFont
        
        main_layout = QVBoxLayout()
        
        # Title
        title = QLabel("NSO GameCube Controller")
        title.setFont(QFont("Arial", 16, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title)
        
        # Content area
        content_layout = QHBoxLayout()
        
        # Left side - Buttons and Triggers
        left_frame = QFrame()
        left_layout = QVBoxLayout()
        
        # Buttons section
        btn_label = QLabel("Buttons")
        btn_label.setFont(QFont("Arial", 12, QFont.Bold))
        left_layout.addWidget(btn_label)
        
        button_grid = QGridLayout()
        self.button_labels = {}
        button_config = [
            ('A', 0, 0), ('B', 0, 1), ('X', 0, 2), ('Y', 0, 3),
            ('Start', 1, 0), ('Z', 1, 1), ('R', 1, 2), ('L', 1, 3),
            ('Dpad_Up', 2, 1), ('Dpad_Down', 3, 1), 
            ('Dpad_Left', 2, 0), ('Dpad_Right', 2, 2),
            ('Home', 4, 0), ('Capture', 4, 1), ('ZL', 4, 2),
        ]
        
        for btn_name, row, col in button_config:
            btn = QLabel(btn_name)
            btn.setAlignment(Qt.AlignCenter)
            btn.setStyleSheet("border: 2px solid gray; padding: 5px; min-width: 60px;")
            button_grid.addWidget(btn, row, col)
            self.button_labels[btn_name] = btn
        
        left_layout.addLayout(button_grid)
        
        # Triggers
        trigger_label = QLabel("Triggers")
        trigger_label.setFont(QFont("Arial", 12, QFont.Bold))
        left_layout.addWidget(trigger_label)
        
        self.trigger_l_label = QLabel("L: 0")
        left_layout.addWidget(self.trigger_l_label)
        self.trigger_l_bar = QFrame()
        self.trigger_l_bar.setStyleSheet("background-color: blue; max-height: 20px;")
        self.trigger_l_bar.setFixedHeight(20)
        left_layout.addWidget(self.trigger_l_bar)
        
        self.trigger_r_label = QLabel("R: 0")
        left_layout.addWidget(self.trigger_r_label)
        self.trigger_r_bar = QFrame()
        self.trigger_r_bar.setStyleSheet("background-color: red; max-height: 20px;")
        self.trigger_r_bar.setFixedHeight(20)
        left_layout.addWidget(self.trigger_r_bar)
        
        left_frame.setLayout(left_layout)
        content_layout.addWidget(left_frame)
        
        # Right side - Sticks
        right_frame = QFrame()
        right_layout = QVBoxLayout()
        
        # Main stick
        main_label = QLabel("Main Stick")
        main_label.setFont(QFont("Arial", 12, QFont.Bold))
        right_layout.addWidget(main_label)
        
        self.main_stick_label = QLabel("X: 0, Y: 0")
        right_layout.addWidget(self.main_stick_label)
        
        self.main_stick_canvas = StickWidget(200, 200)
        right_layout.addWidget(self.main_stick_canvas.widget)
        
        # C-stick
        c_label = QLabel("C-Stick")
        c_label.setFont(QFont("Arial", 12, QFont.Bold))
        right_layout.addWidget(c_label)
        
        self.c_stick_label = QLabel("X: 0, Y: 0")
        right_layout.addWidget(self.c_stick_label)
        
        self.c_stick_canvas = StickWidget(200, 200)
        right_layout.addWidget(self.c_stick_canvas.widget)
        
        right_frame.setLayout(right_layout)
        content_layout.addWidget(right_frame)
        
        main_layout.addLayout(content_layout)
        self.window.setLayout(main_layout)
        
    def update_display(self):
        """Update the display with current controller state."""
        if not self.driver.running:
            self.window.close()
            return
            
        if self.driver.current_state:
            state = self.driver.current_state
            
            # Update buttons
            for btn_name, btn_widget in self.button_labels.items():
                pressed = state['buttons'].get(btn_name, False)
                if pressed:
                    btn_widget.setStyleSheet("border: 2px solid gray; padding: 5px; min-width: 60px; background-color: yellow;")
                else:
                    btn_widget.setStyleSheet("border: 2px solid gray; padding: 5px; min-width: 60px;")
            
            # Update triggers
            trigger_l = state.get('trigger_l', 0)
            trigger_r = state.get('trigger_r', 0)
            
            self.trigger_l_label.setText(f"L: {trigger_l}")
            self.trigger_l_bar.setFixedWidth(int((trigger_l / 255) * 200))
            
            self.trigger_r_label.setText(f"R: {trigger_r}")
            self.trigger_r_bar.setFixedWidth(int((trigger_r / 255) * 200))
            
            # Update sticks
            sticks = state.get('sticks', {})
            main_x = sticks.get('main_x', 0)
            main_y = sticks.get('main_y', 0)
            c_x = sticks.get('c_x', 0)
            c_y = sticks.get('c_y', 0)
            
            self.main_stick_label.setText(f"X: {main_x:+4d}, Y: {main_y:+4d}")
            self.c_stick_label.setText(f"X: {c_x:+4d}, Y: {c_y:+4d}")
            
            # Update stick positions
            self.main_stick_canvas.set_stick_position(main_x, main_y)
            self.c_stick_canvas.set_stick_position(c_x, c_y)
        
    def run(self):
        """Run the GUI main loop."""
        print("✓ Driver started successfully!")
        print("GUI window opened. Close the window to stop.\n")
        self.window.show()
        self.app.exec_()
        self.driver.stop()


class ControllerGUI:
    """Tkinter GUI for visualizing controller input."""
    
    def __init__(self, driver):
        self.driver = driver
        self.root = tk.Tk()
        self.root.title("NSO GameCube Controller")
        self.root.geometry("800x600")
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        # Start read loop in background thread
        read_thread = threading.Thread(target=driver.read_loop, daemon=True)
        read_thread.start()
        
        self.setup_ui()
        
    def setup_ui(self):
        """Setup the GUI elements."""
        # Title
        title = tk.Label(self.root, text="NSO GameCube Controller", font=("Arial", 16, "bold"))
        title.pack(pady=10)
        
        # Main container
        main_frame = tk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Left side - Buttons
        left_frame = tk.Frame(main_frame)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)
        
        tk.Label(left_frame, text="Buttons", font=("Arial", 12, "bold")).pack()
        button_frame = tk.Frame(left_frame)
        button_frame.pack(pady=10)
        
        # Button labels and states
        self.button_labels = {}
        button_config = [
            ('A', 0, 0), ('B', 0, 1), ('X', 0, 2), ('Y', 0, 3),
            ('Start', 1, 0), ('Z', 1, 1), ('R', 1, 2), ('L', 1, 3),
            ('Dpad_Up', 2, 1), ('Dpad_Down', 2, 1), ('Dpad_Left', 2, 0), ('Dpad_Right', 2, 2),
            ('Home', 3, 0), ('Capture', 3, 1), ('ZL', 3, 2),
        ]
        
        for btn_name, row, col in button_config:
            frame = tk.Frame(button_frame, relief=tk.RAISED, borderwidth=2, width=80, height=40)
            frame.grid(row=row, column=col, padx=2, pady=2)
            frame.pack_propagate(False)
            
            label = tk.Label(frame, text=btn_name, font=("Arial", 9))
            label.pack(fill=tk.BOTH, expand=True)
            self.button_labels[btn_name] = {'frame': frame, 'label': label}
        
        # Triggers
        trigger_frame = tk.Frame(left_frame)
        trigger_frame.pack(pady=10)
        
        tk.Label(trigger_frame, text="Triggers", font=("Arial", 12, "bold")).pack()
        
        self.trigger_l_frame = tk.Frame(trigger_frame, bg="lightgray", width=200, height=30)
        self.trigger_l_frame.pack(pady=5)
        self.trigger_l_bar = tk.Frame(self.trigger_l_frame, bg="blue", height=30)
        self.trigger_l_label = tk.Label(trigger_frame, text="L: 0")
        self.trigger_l_label.pack()
        
        self.trigger_r_frame = tk.Frame(trigger_frame, bg="lightgray", width=200, height=30)
        self.trigger_r_frame.pack(pady=5)
        self.trigger_r_bar = tk.Frame(self.trigger_r_frame, bg="red", height=30)
        self.trigger_r_label = tk.Label(trigger_frame, text="R: 0")
        self.trigger_r_label.pack()
        
        # Right side - Sticks
        right_frame = tk.Frame(main_frame)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5)
        
        # Main stick
        main_stick_frame = tk.Frame(right_frame)
        main_stick_frame.pack(pady=10)
        
        tk.Label(main_stick_frame, text="Main Stick", font=("Arial", 12, "bold")).pack()
        self.main_stick_canvas = tk.Canvas(main_stick_frame, width=200, height=200, bg="white", borderwidth=2, relief=tk.SUNKEN)
        self.main_stick_canvas.pack()
        self.main_stick_label = tk.Label(main_stick_frame, text="X: 0, Y: 0")
        self.main_stick_label.pack()
        
        # C-stick
        c_stick_frame = tk.Frame(right_frame)
        c_stick_frame.pack(pady=10)
        
        tk.Label(c_stick_frame, text="C-Stick", font=("Arial", 12, "bold")).pack()
        self.c_stick_canvas = tk.Canvas(c_stick_frame, width=200, height=200, bg="white", borderwidth=2, relief=tk.SUNKEN)
        self.c_stick_canvas.pack()
        self.c_stick_label = tk.Label(c_stick_frame, text="X: 0, Y: 0")
        self.c_stick_label.pack()
        
        # Draw stick backgrounds
        self.draw_stick_background(self.main_stick_canvas)
        self.draw_stick_background(self.c_stick_canvas)
        
    def draw_stick_background(self, canvas):
        """Draw the stick visualization background."""
        # Store canvas dimensions for later use
        canvas.update_idletasks()
        width = canvas.winfo_width() or 200
        height = canvas.winfo_height() or 200
        
    def update_stick(self, canvas, label_widget, x, y, max_range=128):
        """Update stick visualization - draws background ring and moving dot."""
        # Clear only the stick dot, keep background
        canvas.delete("stick")
        
        # Get canvas dimensions
        canvas.update_idletasks()
        width = canvas.winfo_width() or 200
        height = canvas.winfo_height() or 200
        center_x, center_y = width // 2, height // 2
        radius = min(width, height) // 2 - 10
        
        # Draw background circle and crosshair (tagged as background so they persist)
        if not canvas.find_withtag("background"):
            # Draw outer circle (ring)
            canvas.create_oval(center_x - radius, center_y - radius,
                              center_x + radius, center_y + radius,
                              outline="gray", width=2, tags="background")
            
            # Draw center crosshair
            canvas.create_line(center_x - radius, center_y, center_x + radius, center_y, 
                              fill="lightgray", tags="background")
            canvas.create_line(center_x, center_y - radius, center_x, center_y + radius, 
                              fill="lightgray", tags="background")
            
            # Draw center dot
            canvas.create_oval(center_x - 2, center_y - 2, center_x + 2, center_y + 2,
                              fill="lightgray", outline="", tags="background")
        
        # Clamp values to range
        x = max(-max_range, min(max_range, x))
        y = max(-max_range, min(max_range, y))
        
        # Scale to canvas coordinates (normalize to radius)
        stick_x = center_x + int((x / max_range) * radius)
        stick_y = center_y - int((y / max_range) * radius)  # Invert Y for screen coordinates
        
        # Draw stick position dot (blue dot that moves)
        canvas.create_oval(stick_x - 8, stick_y - 8, stick_x + 8, stick_y + 8,
                          fill="blue", outline="darkblue", width=2, tags="stick")
        
        # Update label
        label_widget.config(text=f"X: {x:+4d}, Y: {y:+4d}")
        
    def update_state(self, state):
        """Update GUI with new controller state."""
        # Update buttons
        for btn_name, btn_data in self.button_labels.items():
            pressed = state['buttons'].get(btn_name, False)
            if pressed:
                btn_data['frame'].config(bg="yellow")
                btn_data['label'].config(bg="yellow", fg="black")
            else:
                btn_data['frame'].config(bg="SystemButtonFace")
                btn_data['label'].config(bg="SystemButtonFace", fg="black")
        
        # Update triggers
        trigger_l = state.get('trigger_l', 0)
        trigger_r = state.get('trigger_r', 0)
        
        # Update L trigger bar
        l_width = int((trigger_l / 255) * 200)
        self.trigger_l_bar.place(x=0, y=0, width=l_width)
        self.trigger_l_label.config(text=f"L: {trigger_l}")
        
        # Update R trigger bar
        r_width = int((trigger_r / 255) * 200)
        self.trigger_r_bar.place(x=0, y=0, width=r_width)
        self.trigger_r_label.config(text=f"R: {trigger_r}")
        
        # Update sticks
        sticks = state.get('sticks', {})
        self.update_stick(self.main_stick_canvas, self.main_stick_label,
                         sticks.get('main_x', 0), sticks.get('main_y', 0))
        self.update_stick(self.c_stick_canvas, self.c_stick_label,
                         sticks.get('c_x', 0), sticks.get('c_y', 0))
        
    def run(self):
        """Run the GUI main loop."""
        def update_loop():
            if self.driver.running:
                if self.driver.current_state:
                    self.update_state(self.driver.current_state)
                self.root.after(16, update_loop)  # ~60 FPS
            else:
                self.root.quit()
        
        print("✓ Driver started successfully!")
        print("GUI window opened. Close the window to stop.\n")
        
        # Force window to appear
        self.root.update_idletasks()
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        
        # Make sure window is visible
        self.root.attributes('-topmost', True)
        self.root.attributes('-topmost', False)
        
        # Start update loop
        update_loop()
        
        # Run main loop
        print("Starting Tkinter main loop...")
        try:
            self.root.mainloop()
        except Exception as e:
            print(f"Error in main loop: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.driver.stop()
        
    def on_closing(self):
        """Handle window close."""
        self.driver.stop()
        self.root.destroy()


def main():
    import argparse
    from datetime import datetime
    
    parser = argparse.ArgumentParser(description='NSO GameCube Controller Driver')
    parser.add_argument('--gui', action='store_true', help='Use GUI mode (requires tkinter or PyQt5)')
    parser.add_argument('--debug', action='store_true', help='Show detailed raw byte output')
    parser.add_argument('--log', type=str, help='Log file path (logs every second with all interpretations)')
    parser.add_argument('--no-dsu', action='store_true',
                       help='Disable DSU server (default: DSU on for Dolphin)')
    parser.add_argument('--usb', action='store_true',
                       help='Use USB (default when neither --usb nor --ble)')
    parser.add_argument('--ble', action='store_true',
                       help='Use BLE instead of USB (wireless; hold pair button to connect)')
    parser.add_argument('--address', type=str, default=None,
                       help='BLE address when using --ble (optional: omit to auto-discover; hold pair button when starting)')
    parser.add_argument('--ble-report-offset', type=int, default=0, metavar='N',
                       help='Bytes to skip so BLE matches USB layout (default 0). Buttons 3,4,5; sticks 6-8,9-11; triggers 13,14.')
    parser.add_argument('--ble-report-layout', type=str, default='auto',
                       choices=('auto', 'standard', 'reordered', '0x3f'),
                       help='BLE report layout: auto (try reordered then standard), standard (0x30), reordered (sticks then buttons), 0x3f (simple report)')
    parser.add_argument('--ble-debug', action='store_true',
                       help='Print len(data) and hex dump of first bytes for first few BLE reports (to check offset: 30 = shift 0, else shift -1)')
    parser.add_argument('--ble-scan', action='store_true',
                       help='Scan for BLE devices and list addresses (put controller in pairing mode first). Use the address shown with --ble --address <addr>')
    parser.add_argument('--ble-scan-diff', action='store_true',
                       help='Two scans in one run: first with controller ON (pairing mode), then OFF. Prints the address that disappeared (your controller).')
    parser.add_argument('--ble-discover', action='store_true',
                       help='Interactive BLE calibration: prompts for each button/stick; logs raw byte changes. Use with --ble (--address optional).')
    parser.add_argument('--free-dsu-port', action='store_true',
                       help='Kill process holding DSU port 26760 (for orphaned/zombie instances), then exit.')
    parser.add_argument('--multi', action='store_true',
                       help='Multi-controller mode. Reads slots config from app config dir.')
    args = parser.parse_args()

    if getattr(args, 'free_dsu_port', False):
        from dsu_server import free_orphaned_port, DSUServer
        if free_orphaned_port(DSUServer.DSU_PORT):
            print(f"✓ Freed port {DSUServer.DSU_PORT}")
        else:
            print(f"Port {DSUServer.DSU_PORT} is not in use (nothing to free)")
        return 0
    
    # Set up log file
    log_file = args.log
    if log_file:
        # Create log file with header
        try:
            with open(log_file, 'w') as f:
                f.write(f"# NSO GameCube Controller Log\n")
                f.write(f"# Started: {datetime.now().isoformat()}\n")
                f.write(f"# Format: JSON Lines (one JSON object per line)\n")
                f.write(f"# Instructions: Move sticks to cardinal directions (L, R, U, D) and hold for each\n")
                f.write(f"# Each line represents one sample taken every second\n\n")
            print(f"Logging to: {log_file}")
            print("Move sticks to cardinal directions (L, R, U, D) and hold each position")
            print("Logging every second...\n")
        except Exception as e:
            print(f"Error creating log file: {e}")
            log_file = None
    
    # Diagnostic output
    print(f"GUI Detection:")
    print(f"  GUI_AVAILABLE: {GUI_AVAILABLE}")
    print(f"  GUI_TYPE: {GUI_TYPE}")
    print(f"  Requested --gui: {args.gui}")
    print()
    
    use_gui = args.gui and GUI_AVAILABLE
    if args.gui and not GUI_AVAILABLE:
        print("✗ GUI requested but no GUI library available.")
        print(f"  Detected GUI type: {GUI_TYPE}")
        print("\nTo enable GUI:")
        print("  Option 1: Install PyQt5 in venv:")
        print("    source venv/bin/activate")
        print("    pip install PyQt5")
        print("\n  Option 2: Use system Python (has tkinter):")
        print("    /usr/bin/python3 main.py --gui")
        print("    (Note: system Python may not have pyusb/hidapi)")
        print("\n  Option 3: Install python-tk for venv Python:")
        print("    brew install python-tk")
        print("    (Then recreate venv or use system Python)")
        print("\nContinuing in terminal mode...\n")
        use_gui = False
    
    if use_gui:
        print(f"Starting in GUI mode ({GUI_TYPE})...")
    else:
        print("Starting in terminal mode (use --gui for visual interface)...")
        if args.debug or getattr(args, 'ble_debug', False):
            print("Debug mode: Latency stats every ~100 input reports")

    if getattr(args, 'ble_scan_diff', False):
        if not BLE_AVAILABLE or BleakScanner is None:
            print("✗ --ble-scan-diff requires bleak. Run: pip install bleak")
            return 1
        print("BLE scan (diff): we will run two short scans.")
        print("First scan: controller ON (pairing mode). Second scan: controller OFF.\n")
        print("Put controller in pairing mode, then press Enter to start first scan...")
        input()
        print(f"Scanning for {BLE_SCAN_DIFF_DURATION_SEC} seconds...")
        devices_on = asyncio.run(
            BleakScanner.discover(timeout=BLE_SCAN_DIFF_DURATION_SEC)
        )
        addrs_on = {d.address for d in devices_on}
        print(f"  First scan: {len(devices_on)} device(s).\n")
        print("Turn controller OFF, then press Enter to start second scan...")
        input()
        print(f"Scanning for {BLE_SCAN_DIFF_DURATION_SEC} seconds...")
        devices_off = asyncio.run(
            BleakScanner.discover(timeout=BLE_SCAN_DIFF_DURATION_SEC)
        )
        addrs_off = {d.address for d in devices_off}
        disappeared = addrs_on - addrs_off
        if not disappeared:
            print("  No device disappeared between scans. Make sure the controller was ON (pairing) in the first scan and OFF in the second.")
            return 0
        # Build name lookup from first scan
        name_by_addr = {d.address: (d.name or "(no name)") for d in devices_on}
        # BlueRetro identifies Nintendo BLE by name "DeviceName" - try that one first
        def _sort_candidates(addr):
            name = (name_by_addr.get(addr, "") or "").lower()
            return (0 if name == "devicename" else 1, addr)
        ordered = sorted(disappeared, key=_sort_candidates)
        print(f"  Second scan: {len(devices_off)} device(s).")
        print(f"\nDevice(s) that disappeared (candidates): {len(disappeared)}. 'DeviceName' tried first (Nintendo BLE).\n")
        for addr in ordered:
            print(f"  {addr}  {name_by_addr.get(addr, '(no name)')}")

        async def _try_handshake(addr):
            """Connect and try Nintendo BLE handshake on any writable characteristic; return True if one accepts."""
            try:
                async with BleakClient(addr, timeout=BLE_HANDSHAKE_TRY_TIMEOUT_SEC) as client:
                    for svc in client.services:
                        for char in svc.characteristics:
                            if "write" in char.properties or "write-without-response" in char.properties:
                                try:
                                    await client.write_gatt_char(char.uuid, BLE_HANDSHAKE_READ_SPI)
                                    return True
                                except Exception:
                                    pass
                    return False
            except Exception:
                return False

        print("\nTrying each candidate (only the real controller accepts the handshake). Timeout 1.5s each.")
        print("Put controller in pairing mode again, then press Enter...")
        input()
        controller_addr = None
        for addr in ordered:
            print(f"  Trying {addr}...", end=" ", flush=True)
            if asyncio.run(_try_handshake(addr)):
                print("✓ controller")
                controller_addr = addr
                break
            print("no")
        if controller_addr:
            print(f"\nController at: {controller_addr}")
            print(f"Run: python main.py --ble --address {controller_addr}")
        else:
            print("\nNone of the candidates accepted the handshake. Put controller in pairing mode and run --ble-scan-diff again.")
        return 0

    if getattr(args, 'ble_scan', False):
        if not BLE_AVAILABLE or BleakScanner is None:
            print("✗ --ble-scan requires bleak. Run: pip install bleak")
            return 1
        async def _do_scan():
            print("BLE scan: put your controller in pairing mode now.")
            print(f"Scanning for {BLE_SCAN_DURATION_SEC} seconds...\n")
            devices = await BleakScanner.discover(timeout=BLE_SCAN_DURATION_SEC)
            if not devices:
                print("No devices found. Put controller in pairing mode and try again.")
                return
            print(f"Found {len(devices)} device(s). Use one of these with --ble --address <ADDRESS>:\n")
            print("(On macOS these show as UUIDs, not MAC addresses. They still work with --ble --address.)\n")
            for d in sorted(devices, key=lambda x: (x.name or "")):
                name = (d.name or "(no name)")
                addr = d.address
                rssi = getattr(d, 'rssi', None)
                r = f"  RSSI: {rssi} dBm" if rssi is not None else ""
                print(f"  {addr}  {name}  {r}")
            print("\nTo find your controller: use --ble-scan-diff for two scans in one run.")
            print("\nExample: python main.py --ble --address", devices[0].address)
        asyncio.run(_do_scan())
        return 0

    if args.ble_discover and not args.ble:
        print("✗ --ble-discover requires --ble. Use: python main.py --ble --ble-discover")
        return 1

    if getattr(args, 'multi', False):
        try:
            from controller_storage import load_slots_config
        except ImportError:
            print("✗ Multi-controller requires controller_storage module")
            return 1
        slots_config = load_slots_config()
        if not slots_config:
            print("✗ No controllers configured. Use the launcher to assign slots.")
            return 1
        driver = MultiControllerDriver(
            slots_config,
            use_dsu=not getattr(args, 'no_dsu', False),
            use_gui=False,
            log_file=log_file,
            debug=getattr(args, 'debug', False),
        )
        try:
            driver.start()
        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()
            return 1
        return 0

    if args.ble:
        if not BLE_AVAILABLE:
            print("✗ --ble requires bleak. Run: pip install bleak")
            try:
                import bleak
            except Exception as e:
                print(f"  (import failed: {e})")
            return 1
        # DSU enabled by default with BLE so Dolphin can use the controller without --dsu
        driver = NSOWirelessDriver(
            args.address,
            report_id_offset=args.ble_report_offset,
            ble_report_layout=args.ble_report_layout,
            ble_debug=args.ble_debug,
            ble_discover=getattr(args, 'ble_discover', False),
            use_gui=use_gui if not getattr(args, 'ble_discover', False) else False,
            log_file=log_file,
            use_dsu=not getattr(args, 'no_dsu', False) and not getattr(args, 'ble_discover', False),
            debug=args.ble_debug,
        )
    else:
        driver = NSODriver(use_gui=use_gui, log_file=log_file, use_dsu=not getattr(args, 'no_dsu', False), debug=getattr(args, 'debug', False))

    try:
        driver.start()
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
