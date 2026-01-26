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

# Initialization data discovered by the community
DEFAULT_REPORT_DATA = [
    0x03, 0x91, 0x00, 0x0d, 0x00, 0x08,
    0x00, 0x00, 0x01, 0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF
]

SET_LED_DATA = [
    0x09, 0x91, 0x00, 0x07, 0x00, 0x08,
    0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00
]


class NSODriver:
    """NSO GameCube Controller Driver."""
    
    def __init__(self, use_gui=False, log_file=None, use_dsu=False):
        self.usb_device = None
        self.hid_device = None
        self.running = False
        self.out_endpoint = None
        self.use_gui = use_gui and GUI_AVAILABLE
        self.current_state = None
        self.gui_window = None
        self.log_file = log_file
        self.last_log_time = 0
        self.log_interval = 1.0  # Log every 1 second
        
        # DSU server support (UDP-based, works on all platforms!)
        self.dsu_server = None
        if use_dsu and DSU_AVAILABLE:
            self.dsu_server = DSUServer()
        
        # Calibration offsets (assume controller starts in neutral position)
        self.calibration = {
            'main_x_center': None,
            'main_y_center': None,
            'c_x_center': None,
            'c_y_center': None,
            'calibrated': False
        }
        
    def find_usb_device(self):
        """Find USB device and get endpoints."""
        self.usb_device = usb.core.find(idVendor=VID, idProduct=PID)
        if self.usb_device is None:
            return False
        
        try:
            if self.usb_device.is_kernel_driver_active(INTERFACE_NUM):
                self.usb_device.detach_kernel_driver(INTERFACE_NUM)
        except:
            pass
        
        try:
            self.usb_device.set_configuration()
        except:
            pass
        
        # Find bulk OUT endpoint
        cfg = self.usb_device.get_active_configuration()
        intf = cfg[(INTERFACE_NUM, 0)]
        
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
        
        time.sleep(0.1)
        
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
        
        time.sleep(0.1)
        
        print("  ✓ USB initialization complete\n")
        return True
    
    def open_hid_device(self):
        """Open HID device for reading input."""
        try:
            self.hid_device = hid.device()
            self.hid_device.open(VID, PID)
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
                time.sleep(0.01)  # Small delay between samples
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
    
    def parse_input(self, data):
        """Parse HID input data based on discovered format."""
        if len(data) < 12:  # Need at least 12 bytes for sticks (bytes 6-11)
            return None
        
        # Button mapping (from discovered format)
        buttons = {
            'B': (data[3] & 0x01) != 0,
            'A': (data[3] & 0x02) != 0,
            'Y': (data[3] & 0x04) != 0,
            'X': (data[3] & 0x08) != 0,
            'R': (data[3] & 0x10) != 0,
            'Z': (data[3] & 0x20) != 0,
            'Start': (data[3] & 0x40) != 0,
            'Dpad_Down': (data[4] & 0x01) != 0,
            'Dpad_Right': (data[4] & 0x02) != 0,
            'Dpad_Left': (data[4] & 0x04) != 0,
            'Dpad_Up': (data[4] & 0x08) != 0,
            'L': (data[4] & 0x10) != 0,
            'ZL': (data[4] & 0x20) != 0,
            'Home': (data[5] & 0x01) != 0,
            'Capture': (data[5] & 0x02) != 0,
        }
        
        # Analog triggers (bytes 13 and 14) - restore original working positions
        trigger_l = data[13] if len(data) > 13 else 0
        trigger_r = data[14] if len(data) > 14 else 0
        
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
        
        if len(data) >= 12:
            # STEP 1: Extract 12-bit values from nibble-packed bytes
            # Main Stick (Left Stick) extraction
            # Byte 6: Lower 8 bits of X
            # Byte 7: Upper 4 bits of X (low nibble), Lower 4 bits of Y (high nibble)
            # Byte 8: Upper 8 bits of Y
            main_x_raw = data[6] | ((data[7] & 0x0F) << 8)
            main_y_raw = (data[7] >> 4) | (data[8] << 4)
            
            # C-Stick (Right Stick) extraction
            # Byte 9: Lower 8 bits of X
            # Byte 10: Upper 4 bits of X (low nibble), Lower 4 bits of Y (high nibble)
            # Byte 11: Upper 8 bits of Y
            c_x_raw = data[9] | ((data[10] & 0x0F) << 8)
            c_y_raw = (data[10] >> 4) | (data[11] << 4)
            
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
                    'main': [data[6], data[7], data[8]],
                    'c': [data[9], data[10], data[11]],
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
        if not self.use_gui:
            print("\nReading controller input...")
            print("Press buttons and move sticks!")
            if self.log_file:
                print(f"Logging to: {self.log_file} (every {self.log_interval}s)")
                print("Move sticks to cardinal directions (L, R, U, D) and hold each position")
            else:
                print("(Use --gui flag for visual interface or --log FILE to log data)")
            print()
        
        last_data = None
        last_output = None
        sample_count = 0
        
        while self.running:
            try:
                data = self.hid_device.read(64)
                if data:
                    data_list = list(data)
                    
                    # Process all data for logging (even if unchanged)
                    parsed = self.parse_input(data_list)
                    if parsed:
                        self.current_state = parsed
                        
                        # Update DSU server if running (UDP-based, works on all platforms!)
                        if self.dsu_server and self.dsu_server.running:
                            try:
                                self.dsu_server.update(parsed)
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
                            else:
                                # Terminal output with raw byte analysis
                                sample_count += 1
                                
                                # Show raw bytes for first few samples
                                if sample_count <= 5:
                                    print(f"\n--- Sample {sample_count} ---")
                                    print(f"Bytes 0-19: {' '.join([f'{b:02x}' for b in data_list[:20]])}")
                                    print(f"Bytes 6-7 (main?): {data_list[6]:02x} {data_list[7]:02x} = ({data_list[6]}, {data_list[7]})")
                                    print(f"Bytes 8-9:        {data_list[8]:02x} {data_list[9]:02x} = ({data_list[8]}, {data_list[9]})")
                                    print(f"Bytes 10-11 (C?): {data_list[10]:02x} {data_list[11]:02x} = ({data_list[10]}, {data_list[11]})")
                                    print(f"Bytes 12-13:      {data_list[12]:02x} {data_list[13]:02x} = ({data_list[12]}, {data_list[13]})")
                                
                                sticks = parsed.get('sticks', {})
                                main_x = sticks.get('main_x', 0)
                                main_y = sticks.get('main_y', 0)
                                c_x = sticks.get('c_x', 0)
                                c_y = sticks.get('c_y', 0)
                                
                                # Show all interpretations
                                active_buttons = [name for name, pressed in parsed['buttons'].items() if pressed]
                                
                                output_parts = []
                                if active_buttons:
                                    output_parts.append(f"Buttons: {', '.join(active_buttons)}")
                                
                                output_parts.append(f"Main: ({main_x:+4d}, {main_y:+4d}) | C: ({c_x:+4d}, {c_y:+4d})")
                                
                                # Show alternative interpretations if they're different
                                if sample_count <= 3:
                                    alt_x = sticks.get('main_x_alt', 0)
                                    alt_y = sticks.get('main_y_alt', 0)
                                    if alt_x != main_x or alt_y != main_y:
                                        output_parts.append(f"[Alt: ({alt_x:+4d}, {alt_y:+4d})]")
                                
                                if parsed['trigger_l'] > 5 or parsed['trigger_r'] > 5:
                                    output_parts.append(f"Triggers: L={parsed['trigger_l']:3d} R={parsed['trigger_r']:3d}")
                                
                                output = " | ".join(output_parts)
                                
                                # Print if output changed or it's a sample
                                if output != last_output or sample_count <= 5:
                                    print(f"\r{output:<80}", end='', flush=True)
                                    last_output = output
                        
                        last_data = data_list
            except Exception as e:
                if 'timeout' not in str(e).lower():
                    if not self.use_gui:
                        print(f"\nRead error: {e}")
            
            # Minimal sleep - removed for maximum responsiveness (HID is non-blocking)
            # Use a tiny yield to prevent CPU spinning
            time.sleep(0.0001)  # 0.1ms - minimal delay for CPU efficiency
    
    def start(self):
        """Start the driver."""
        print("NSO GameCube Controller Driver")
        print("="*70)
        
        # Step 1: Find and initialize USB device
        if not self.find_usb_device():
            print("✗ Failed to find USB device")
            return False
        
        print("✓ USB device found")
        
        if not self.initialize_usb():
            print("✗ USB initialization failed")
            return False
        
        # Step 2: Open HID device for reading
        if not self.open_hid_device():
            print("✗ Failed to open HID device")
            print("  Make sure the device is initialized via USB first")
            return False
        
        # Step 2.5: Calibrate sticks (assume neutral position at startup)
        time.sleep(0.2)  # Give device a moment to stabilize
        self.calibrate_sticks()
        
        # Step 2.6: Start DSU server if requested
        if self.dsu_server:
            if not self.dsu_server.start():
                print("⚠️  DSU server failed to start", flush=True)
                self.dsu_server = None
        
        # Step 3: Start reading
        self.running = True
        
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
        
        # Stop DSU server if running
        if self.dsu_server:
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
    parser.add_argument('--dsu', action='store_true',
                       help='Start DSU server for Dolphin (UDP-based, works on all platforms including macOS!)')
    args = parser.parse_args()
    
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
        print("    /usr/bin/python3 driver_final.py --gui")
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
        if args.debug:
            print("Debug mode: Will show raw byte dumps")
    
    driver = NSODriver(use_gui=use_gui, log_file=log_file, use_dsu=args.dsu)
    
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
