#!/usr/bin/env python3
"""
DSU (Cemuhook) Server - UDP-based controller protocol for Dolphin and other emulators.

This implements the DSU protocol to send controller data over UDP to Dolphin.
No kernel extensions or virtual HID devices needed - just UDP packets!
"""

import socket
import struct
import zlib
import time
import threading
import subprocess
from typing import Dict, Optional


def free_orphaned_port(port: int = 26760) -> bool:
    """Kill process(es) holding the DSU port. Returns True if something was killed."""
    try:
        result = subprocess.run(
            ["lsof", "-i", f":{port}", "-t"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        pids = result.stdout.strip().split()
        if not pids:
            return False
        for pid in pids:
            try:
                subprocess.run(["kill", pid], check=True, timeout=2)
            except subprocess.CalledProcessError:
                try:
                    subprocess.run(["kill", "-9", pid], check=True, timeout=2)
                except subprocess.CalledProcessError:
                    pass
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        return False


class DSUServer:
    """
    DSU Server that broadcasts controller data over UDP.
    
    Dolphin can connect to this server using:
    Controllers > Alternate Input Sources > DSU Client
    """
    
    # DSU Protocol constants
    DSU_PORT = 26760
    DSU_PORT_MAX_ATTEMPTS = 5  # Try 26760..26764 if port in use
    PROTOCOL_VERSION = 1001
    PACKET_TYPE_VERSION = 0x00100000  # Changed from 0x01000000
    PACKET_TYPE_PAD_INFO = 0x00100001  # Changed from 0x01000001
    PACKET_TYPE_PAD_DATA = 0x00100002  # Changed from 0x01000002
    
    def __init__(self, server_id: int = 0):
        self.server_id = server_id
        self.socket = None
        self.port = self.DSU_PORT  # Actual port in use (may differ if fallback used)
        self.running = False
        self.packet_counter = 0
        self.last_state = None
        self.thread = None
        self._logged_clients = set()
        # State latch: Store buttons that haven't been "seen" by Dolphin yet
        self.pending_presses = set()
        # Pre-allocate packet buffers to avoid GC pressure
        self._pad_data_buffer = bytearray(100)
        self._version_buffer = bytearray(24)
        self._pad_info_buffer = bytearray(32)
        
    def start(self):
        """Start the DSU server and the background handler.
        Tries ports 26760..26764 if the default is in use (e.g. zombie process)."""
        last_err = None
        for attempt in range(self.DSU_PORT_MAX_ATTEMPTS):
            port = self.DSU_PORT + attempt
            try:
                self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self.socket.bind(('127.0.0.1', port))
                self.port = port
                self.socket.settimeout(0.005)  # 5ms timeout - wakes thread immediately when packet arrives
                self.running = True

                # CRITICAL: Start the request handler in a background thread
                self.thread = threading.Thread(target=self.handle_requests, daemon=True)
                self.thread.start()

                if port != self.DSU_PORT:
                    print(f"✓ DSU Server started on 127.0.0.1:{port} (port {self.DSU_PORT} was in use)", flush=True)
                    print(f"  Configure Dolphin DSU client to use port {port}", flush=True)
                    print(f"  To free the default port: python main.py --free-dsu-port (or use launcher's Free orphaned port)", flush=True)
                else:
                    print(f"✓ DSU Server started on 127.0.0.1:{port}", flush=True)
                return True
            except OSError as e:
                last_err = e
                if self.socket:
                    try:
                        self.socket.close()
                    except Exception:
                        pass
                    self.socket = None
                if e.errno != 48:  # 48 = Address already in use
                    break
            except Exception as e:
                last_err = e
                if self.socket:
                    try:
                        self.socket.close()
                    except Exception:
                        pass
                    self.socket = None
                break

        print(f"✗ Failed to start DSU server: {last_err}", flush=True)
        import traceback
        traceback.print_exc()
        return False
    
    def stop(self):
        """Stop the DSU server."""
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)
        if self.socket:
            self.socket.close()
            self.socket = None
    
    def _calculate_crc32(self, data: bytes) -> int:
        """Calculate CRC32 checksum for packet."""
        return zlib.crc32(data) & 0xFFFFFFFF
    
    def _create_pad_info_packet(self, pad_id: int = 0, connected: bool = True, server_id: int = None) -> bytes:
        """
        Create a DSU pad info packet (response to Pad Info Request).
        
        Total size: 32 bytes (16 header + 4 type + 12 payload)
        """
        if server_id is None:
            server_id = self.server_id
        
        # Use pre-allocated buffer to avoid GC pressure
        packet = self._pad_info_buffer
        packet[:] = b'\x00' * 32  # Clear buffer
        
        # Header
        packet[0:4] = b'DSUS'
        struct.pack_into('<H', packet, 4, self.PROTOCOL_VERSION)
        struct.pack_into('<H', packet, 6, 16)  # 4 (type) + 12 (payload)
        
        # Server ID
        struct.pack_into('<I', packet, 12, server_id)
        
        # Message type: 0x00100001
        struct.pack_into('<I', packet, 16, self.PACKET_TYPE_PAD_INFO)
        
        # Pad Info
        packet[20] = pad_id
        packet[21] = 0x02  # Connected
        # Model: 0x02 = DualShock 4
        packet[22] = 0x02
        # Connection: 0x01 = USB
        packet[23] = 0x01
        # MAC Address (6 bytes)
        packet[24:30] = bytes([0x00, 0x11, 0x22, 0x33, 0x44, pad_id])
        
        packet[30] = 0x05 if connected else 0x00  # Battery: 0x05 = Full, 0x00 = Not applicable
        packet[31] = 0x00  # Termination byte
        
        # CRC calculation: over whole packet (bytes 0-31) with CRC field (bytes 8-11) zeroed
        crc_field_backup = packet[8:12]
        packet[8:12] = b'\x00\x00\x00\x00'
        crc32 = self._calculate_crc32(bytes(packet))
        packet[8:12] = crc_field_backup
        struct.pack_into('<I', packet, 8, crc32)
        
        return bytes(packet)
    
    def _create_pad_data_packet(self, state: Dict, pad_id: int = 0) -> bytes:
        """
        Create a DSU pad data packet. Parses on-demand from raw bytes for minimum latency.
        
        Packet format (100 bytes total):
        - Header: "DSUS" (4 bytes)
        - Protocol version: 1001 (2 bytes, LE)
        - Packet length: 84 (2 bytes, LE)
        - CRC32: 4 bytes (LE)
        - Server ID: 4 bytes (LE)
        - PadDataRsp: 0x1000002 (4 bytes, LE)
        - Pad ID: 1 byte
        - Pad State: 1 byte (2 = connected)
        - Model/Connection: 2 bytes
        - MAC Address: 6 bytes
        - Battery/Active: 2 bytes
        - Counter: 4 bytes (LE)
        - Buttons: bytes 36-39
        - Sticks: bytes 40-43
        - Triggers: bytes 54-55
        - Rest: padding/IMU data
        """
        # Parse on-demand from raw bytes if available (faster - parse only when sending)
        if 'raw_bytes' in state and 'parsed' in state:
            # Use pre-parsed data if available (fallback)
            parsed = state.get('parsed', {})
        else:
            # Legacy mode: use already parsed state
            parsed = state
        
        # Get buttons and apply state latch - force pending presses to True
        buttons = parsed.get('buttons', {}).copy()  # Copy to avoid modifying original
        # Before packing buttons, force any 'pending' presses to True
        # This ensures that even if the user released the button,
        # we send a 'Pressed' state for at least one DSU packet.
        for btn in list(self.pending_presses):
            buttons[btn] = True
        
        sticks = parsed.get('sticks', {})
        trigger_l = parsed.get('trigger_l', 0)
        trigger_r = parsed.get('trigger_r', 0)
        
        # Use pre-allocated buffer to avoid GC pressure
        packet = self._pad_data_buffer
        packet[:] = b'\x00' * 100  # Clear buffer
        
        # Header: "DSUS"
        packet[0:4] = b'DSUS'
        
        # Protocol version: 1001 (little-endian)
        struct.pack_into('<H', packet, 4, self.PROTOCOL_VERSION)
        
        # Packet length: 84 (excluding 16-byte header)
        struct.pack_into('<H', packet, 6, 84)
        
        # CRC32 placeholder (will calculate after)
        # packet[8:12] = CRC32
        
        # Server ID
        struct.pack_into('<I', packet, 12, self.server_id)
        
        # PadDataRsp: 0x1000002
        struct.pack_into('<I', packet, 16, self.PACKET_TYPE_PAD_DATA)
        
        # Pad ID
        packet[20] = pad_id
        
        # Pad State: 2 = connected
        packet[21] = 2
        
        # Model: 0x02 = DualShock 4 (full gyro)
        packet[22] = 0x02
        # Connection: 0x01 = USB
        packet[23] = 0x01
        
        # MAC Address: Use a fake MAC (6 bytes)
        packet[24:30] = bytes([0x00, 0x11, 0x22, 0x33, 0x44, pad_id])
        
        # Battery/Active: Battery = 0x05 (full), Active = 0x01
        packet[30] = 0x05  # Battery
        packet[31] = 0x01  # Active
        
        # Packet counter
        self.packet_counter += 1
        struct.pack_into('<I', packet, 32, self.packet_counter)
        
        # Buttons (byte 36-39)
        # Initialize 4 bytes of button data
        btns = [0] * 4
        
        # Byte 36: D-Pad, Options, R3, L3, Share
        # Bits: 0:Share, 1:L3, 2:R3, 3:Options, 4:Up, 5:Right, 6:Down, 7:Left
        if buttons.get('Dpad_Up', False):
            btns[0] |= (1 << 4)
        if buttons.get('Dpad_Right', False):
            btns[0] |= (1 << 5)
        if buttons.get('Dpad_Down', False):
            btns[0] |= (1 << 6)
        if buttons.get('Dpad_Left', False):
            btns[0] |= (1 << 7)
        if buttons.get('Start', False):
            btns[0] |= (1 << 3)  # Start -> Options
        if buttons.get('Z', False):
            btns[0] |= (1 << 2)  # Z -> R3 (Right stick click, separate from analog triggers)
        
        # Byte 37: Square, Cross, Circle, Triangle, R1, L1, R2, L2
        # Bits: 0:L2, 1:R2, 2:L1, 3:R1, 4:Triangle, 5:Circle, 6:Cross, 7:Square
        if buttons.get('X', False):
            btns[1] |= (1 << 7)  # X -> Square
        if buttons.get('A', False):
            btns[1] |= (1 << 6)  # A -> Cross
        if buttons.get('B', False):
            btns[1] |= (1 << 5)  # B -> Circle
        if buttons.get('Y', False):
            btns[1] |= (1 << 4)  # Y -> Triangle
        if buttons.get('R', False):
            btns[1] |= (1 << 3)  # R -> R1
        if buttons.get('L', False):
            btns[1] |= (1 << 2)  # L -> L1
        # Z is now mapped to R3 (byte 36, bit 2) instead of R2 to avoid conflict with analog trigger
        if buttons.get('ZL', False):
            btns[1] |= (1 << 0)  # ZL -> L2 (Digital)
        
        # Byte 38: PS Button (Home)
        if buttons.get('Home', False):
            btns[2] |= (1 << 0)
        
        # Byte 39: Touchpad Click (Capture -> Touchpad)
        if buttons.get('Capture', False):
            btns[3] |= (1 << 0)  # Capture -> Touchpad Click
        
        # Assign to packet
        packet[36:40] = btns
        
        # Analog buttons (bytes 44-53): Set to 255 when pressed, 0 when not
        # Bytes 44-47: Analog D-Pad (Left, Down, Right, Up)
        packet[44] = 255 if buttons.get('Dpad_Left', False) else 0
        packet[45] = 255 if buttons.get('Dpad_Down', False) else 0
        packet[46] = 255 if buttons.get('Dpad_Right', False) else 0
        packet[47] = 255 if buttons.get('Dpad_Up', False) else 0
        # Bytes 48-51: Analog buttons (Y/Triangle, B/Circle, A/Cross, X/Square)
        packet[48] = 255 if buttons.get('Y', False) else 0
        packet[49] = 255 if buttons.get('B', False) else 0
        packet[50] = 255 if buttons.get('A', False) else 0
        packet[51] = 255 if buttons.get('X', False) else 0
        # Bytes 52-53: Analog R1, L1 (R, L buttons)
        packet[52] = 255 if buttons.get('R', False) else 0
        packet[53] = 255 if buttons.get('L', False) else 0
        
        # Sticks (bytes 40-43)
        # Convert from signed offset (difference from center) to 0-255 (centered at 128)
        # Use circular normalization to ensure consistent magnitude regardless of direction
        import math
        
        def normalize_stick_pair(x, y, max_range=1400.0):
            """
            Normalize a stick pair (x, y) using circular normalization.
            This ensures diagonals reach the same magnitude as cardinals.
            
            Args:
                x: Signed offset in X axis
                y: Signed offset in Y axis  
                max_range: Maximum expected range from center
                
            Returns:
                Tuple of (normalized_x, normalized_y) in range -1.0 to 1.0
            """
            # Calculate magnitude (distance from center)
            magnitude = math.sqrt(x * x + y * y)
            
            if magnitude == 0:
                return (0.0, 0.0)
            
            # Normalize magnitude to 0-1 range, then clamp
            normalized_magnitude = min(1.0, magnitude / max_range)
            
            # Scale both axes proportionally to maintain direction
            # This ensures diagonals reach the same magnitude as cardinals
            scale = normalized_magnitude / magnitude
            normalized_x = x * scale
            normalized_y = y * scale
            
            return (normalized_x, normalized_y)
        
        def stick_value_to_byte(normalized_value):
            """Convert normalized value (-1.0 to 1.0) to 0-255 byte (128 is center)."""
            return int(normalized_value * 127 + 128) & 0xFF
        
        main_x = sticks.get('main_x', 0)
        main_y = sticks.get('main_y', 0)
        c_x = sticks.get('c_x', 0)
        c_y = sticks.get('c_y', 0)
        
        # Normalize each stick pair using circular normalization
        main_norm_x, main_norm_y = normalize_stick_pair(main_x, main_y)
        c_norm_x, c_norm_y = normalize_stick_pair(c_x, c_y)
        
        packet[40] = stick_value_to_byte(main_norm_x)  # Left Stick X
        packet[41] = stick_value_to_byte(-main_norm_y)  # Left Stick Y (inverted)
        packet[42] = stick_value_to_byte(c_norm_x)      # Right Stick X
        packet[43] = stick_value_to_byte(-c_norm_y)     # Right Stick Y (inverted)
        
        # Triggers (bytes 54-55)
        # Ensure they are clamped 0-255
        packet[54] = max(0, min(255, int(trigger_l))) & 0xFF  # L2
        packet[55] = max(0, min(255, int(trigger_r))) & 0xFF  # R2
        
        # Calculate CRC32: over whole packet (bytes 0-99) with CRC field (bytes 8-11) zeroed
        # This matches how Pad Info packets calculate CRC
        crc_field_backup = packet[8:12]
        packet[8:12] = b'\x00\x00\x00\x00'
        crc32 = self._calculate_crc32(bytes(packet))
        packet[8:12] = crc_field_backup
        struct.pack_into('<I', packet, 8, crc32)
        
        # CLEAR the latch after creating the packet
        # This ensures each pressed button is sent at least once
        self.pending_presses.clear()
        
        return bytes(packet)
    
    def _respond_version(self, data, addr):
        """Respond to version request immediately."""
        packet = self._version_buffer
        packet[:] = b'\x00' * 24  # Clear buffer
        # Header: "DSUS" (server response)
        packet[0:4] = b'DSUS'
        # Protocol version: 1001
        struct.pack_into('<H', packet, 4, self.PROTOCOL_VERSION)
        # Packet length: 8 (4 bytes type + 2 bytes version + 2 bytes padding)
        struct.pack_into('<H', packet, 6, 8)
        # Server ID (copy from request if present, otherwise use default)
        if len(data) >= 16:
            server_id = struct.unpack('<I', data[12:16])[0]
        else:
            server_id = self.server_id
        struct.pack_into('<I', packet, 12, server_id)
        # Message type: 0x1000000
        struct.pack_into('<I', packet, 16, self.PACKET_TYPE_VERSION)
        # Version: 1001
        struct.pack_into('<H', packet, 20, self.PROTOCOL_VERSION)
        # Padding
        packet[22:24] = b'\x00\x00'
        # Calculate CRC32: over whole packet (bytes 0-23) with CRC field (bytes 8-11) zeroed
        crc_field_backup = packet[8:12]
        packet[8:12] = b'\x00\x00\x00\x00'
        crc = self._calculate_crc32(bytes(packet))
        packet[8:12] = crc_field_backup
        struct.pack_into('<I', packet, 8, crc)
        self.socket.sendto(bytes(packet), addr)
    
    def _respond_pad_info(self, data, addr):
        """Respond to pad info request immediately."""
        try:
            if len(data) >= 24:
                num_slots = struct.unpack('<i', data[20:24])[0]
                slots_to_report = [data[24+i] for i in range(num_slots)] if len(data) >= 24 + num_slots else [0]
                req_server_id = struct.unpack('<I', data[12:16])[0] if len(data) >= 16 else self.server_id
                
                for slot_id in slots_to_report:
                    # We only want Slot 0 to be our GameCube controller
                    is_connected = (slot_id == 0)
                    packet = self._create_pad_info_packet(pad_id=slot_id, connected=is_connected, server_id=req_server_id)
                    self.socket.sendto(packet, addr)
        except Exception:
            pass  # Silently ignore pad info errors
    
    def update(self, state: Dict):
        """
        Update controller state (stored for responding to client requests).
        Tracks button presses in a latch to ensure quick taps aren't dropped.
        
        Args:
            state: Dictionary containing buttons, sticks, triggers
        """
        # Check for new presses and add them to the latch
        if 'parsed' in state:
            btns = state['parsed'].get('buttons', {})
        else:
            btns = state.get('buttons', {})
        
        # Track any newly pressed buttons
        for btn, pressed in btns.items():
            if pressed:
                self.pending_presses.add(btn)
        
        self.last_state = state
    
    def handle_requests(self):
        """Handle incoming DSU client requests (runs in background thread). Prioritizes reactive mode."""
        clients = set()  # Track connected clients
        last_update_time = 0
        update_interval = 1.0 / 1000.0  # 1000Hz = 1ms for maximum responsiveness
        
        while self.running:
            if not self.socket:
                break
            
            try:
                # Use socket timeout to wake thread IMMEDIATELY when packet arrives (reactive mode)
                # This lets the OS wake the thread the MOMENT a packet arrives - lowest latency path
                data, addr = self.socket.recvfrom(1024)
                
                # IMMEDIATELY process and respond - this is the lowest latency path
                if data and len(data) >= 20:
                    # Check magic bytes - Dolphin sends "DSUC" (Client -> Server)
                    magic = data[0:4]
                    if magic == b'DSUC':
                        # The message type is at bytes 16-19 (Little Endian)
                        msg_type = struct.unpack('<I', data[16:20])[0]
                        
                        if msg_type == self.PACKET_TYPE_VERSION:
                            # Protocol Version Request - respond immediately
                            self._respond_version(data, addr)
                        
                        elif msg_type == self.PACKET_TYPE_PAD_INFO:
                            # Dolphin is asking: "Who is connected?" - respond immediately
                            self._respond_pad_info(data, addr)
                        
                        elif msg_type == self.PACKET_TYPE_PAD_DATA:
                            # Pad Data Request - SEND IMMEDIATELY (reactive mode)
                            clients.add(addr)
                            if self.last_state:
                                packet = self._create_pad_data_packet(self.last_state, pad_id=0)
                                self.socket.sendto(packet, addr)
                                # Log connection once
                                if addr not in self._logged_clients:
                                    print(f"✓ Dolphin connected", flush=True)
                                    self._logged_clients.add(addr)
            
            except socket.timeout:
                # Only if no request came in, do we do the 1000Hz push logic
                current_time = time.time()
                if self.last_state and clients and (current_time - last_update_time) >= update_interval:
                    packet = self._create_pad_data_packet(self.last_state, pad_id=0)
                    for client_addr in clients.copy():
                        try:
                            self.socket.sendto(packet, client_addr)
                        except Exception:
                            clients.discard(client_addr)
                    last_update_time = current_time
            
            except OSError:
                # Socket closed
                break
            except Exception:
                # Silently continue on other errors
                pass
