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
from typing import Dict, Optional


class DSUServer:
    """
    DSU Server that broadcasts controller data over UDP.
    
    Dolphin can connect to this server using:
    Controllers > Alternate Input Sources > DSU Client
    """
    
    # DSU Protocol constants
    DSU_PORT = 26760
    PROTOCOL_VERSION = 1001
    PACKET_TYPE_VERSION = 0x00100000  # Changed from 0x01000000
    PACKET_TYPE_PAD_INFO = 0x00100001  # Changed from 0x01000001
    PACKET_TYPE_PAD_DATA = 0x00100002  # Changed from 0x01000002
    
    def __init__(self, server_id: int = 0):
        self.server_id = server_id
        self.socket = None
        self.running = False
        self.packet_counter = 0
        self.last_state = None
        self.thread = None
        self._logged_clients = set()
        
    def start(self):
        """Start the DSU server and the background handler."""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind(('127.0.0.1', self.DSU_PORT))
            self.socket.settimeout(1.0)
            self.running = True
            
            # CRITICAL: Start the request handler in a background thread
            self.thread = threading.Thread(target=self.handle_requests, daemon=True)
            self.thread.start()
            
            # Give thread a moment to start
            import time
            time.sleep(0.1)
            
            print(f"✓ DSU Server started on 127.0.0.1:{self.DSU_PORT}", flush=True)
            print("  Dolphin can connect via: Controllers > Alternate Input Sources > DSU Client", flush=True)
            print("  Server ID:", self.server_id, flush=True)
            return True
        except Exception as e:
            print(f"✗ Failed to start DSU server: {e}", flush=True)
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
        
        # Total size: 32 bytes (16 header + 4 type + 12 payload)
        packet = bytearray(32)
        
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
        # Temporarily zero the CRC field
        crc_field_backup = packet[8:12]
        packet[8:12] = b'\x00\x00\x00\x00'
        crc32 = self._calculate_crc32(bytes(packet))
        packet[8:12] = crc_field_backup
        struct.pack_into('<I', packet, 8, crc32)
        
        return bytes(packet)
    
    def _create_pad_data_packet(self, state: Dict, pad_id: int = 0) -> bytes:
        """
        Create a DSU pad data packet.
        
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
        buttons = state.get('buttons', {})
        sticks = state.get('sticks', {})
        trigger_l = state.get('trigger_l', 0)
        trigger_r = state.get('trigger_r', 0)
        
        # Debug: Check if buttons dictionary is correct format
        if buttons and not hasattr(self, '_checked_button_format'):
            print(f"     Debug: Button keys sample: {list(buttons.keys())[:5] if buttons else 'empty'}", flush=True)
            print(f"     Debug: Button values sample: {[(k, v, type(v)) for k, v in list(buttons.items())[:3]]}", flush=True)
            self._checked_button_format = True
        
        # Build packet (100 bytes total)
        packet = bytearray(100)
        
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
        if buttons.get('Z', False):
            btns[1] |= (1 << 1)  # Z -> R2 (Digital)
        if buttons.get('ZL', False):
            btns[1] |= (1 << 0)  # ZL -> L2 (Digital)
        
        # Byte 38: PS Button (Home)
        if buttons.get('Home', False):
            btns[2] |= (1 << 0)
        
        # Byte 39: Touchpad Click (Leave as 0)
        
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
        
        # Debug: Log button bytes when any button is pressed
        active_buttons = [k for k, v in buttons.items() if v]
        if active_buttons:
            # Only log occasionally to avoid spam
            if not hasattr(self, '_button_debug_counter'):
                self._button_debug_counter = 0
            self._button_debug_counter += 1
            if self._button_debug_counter <= 5 or self._button_debug_counter % 100 == 0:
                print(f"     Debug: Buttons={active_buttons}, Byte36=0x{btns[0]:02x} ({btns[0]:08b}), Byte37=0x{btns[1]:02x} ({btns[1]:08b})", flush=True)
        
        # Sticks (bytes 40-43)
        # Convert from signed offset (difference from center) to 0-255 (centered at 128)
        def stick_to_byte(value):
            """
            Convert a signed offset (difference from center) to 0-255.
            Assumes value is roughly -2048 to +2047.
            """
            # 1. Normalize the signed offset to a -1.0 to 1.0 float
            # We use 2000 as a divisor to give a little 'headroom' for outer edges
            normalized = value / 2000.0
            
            # 2. Clamp it so it doesn't exceed 1.0 or -1.0
            clamped = max(-1.0, min(1.0, normalized))
            
            # 3. Map -1.0...1.0 to 0...255 (128 is center)
            return int(clamped * 127 + 128) & 0xFF
        
        main_x = sticks.get('main_x', 0)
        main_y = sticks.get('main_y', 0)
        c_x = sticks.get('c_x', 0)
        c_y = sticks.get('c_y', 0)
        
        packet[40] = stick_to_byte(main_x)  # Left Stick X
        packet[41] = stick_to_byte(-main_y)  # Left Stick Y (inverted)
        packet[42] = stick_to_byte(c_x)      # Right Stick X
        packet[43] = stick_to_byte(-c_y)     # Right Stick Y (inverted)
        
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
        
        return bytes(packet)
    
    def update(self, state: Dict):
        """
        Update controller state (stored for responding to client requests).
        
        Args:
            state: Dictionary containing buttons, sticks, triggers
        """
        self.last_state = state
    
    def handle_requests(self):
        """Handle incoming DSU client requests (runs in background thread)."""
        import sys
        clients = set()  # Track connected clients
        print("Listening for Dolphin discovery packets...", flush=True)  # Debug log
        
        while self.running:
            try:
                if self.socket:
                    # Check for incoming requests
                    try:
                        data, addr = self.socket.recvfrom(1024)
                        
                        # Need at least 20 bytes to read message type
                        if len(data) < 20:
                            continue
                        
                        # Check magic bytes - Dolphin sends "DSUC" (Client -> Server)
                        magic = data[0:4]
                        if magic != b'DSUC':
                            continue
                        
                        # The message type is at bytes 16-19 (Little Endian)
                        msg_type = struct.unpack('<I', data[16:20])[0]
                        
                        if msg_type == self.PACKET_TYPE_VERSION:
                            print(f"  -> Version request from {addr[0]}", flush=True)
                            # Protocol Version Request (0x1000000)
                            # Respond with version info
                            packet = bytearray(24)  # 16 byte header + 8 byte payload
                            # Header: "DSUS" (server response)
                            packet[0:4] = b'DSUS'
                            # Protocol version: 1001
                            struct.pack_into('<H', packet, 4, self.PROTOCOL_VERSION)
                            # Packet length: 8 (4 bytes type + 2 bytes version + 2 bytes padding)
                            struct.pack_into('<H', packet, 6, 8)
                            # CRC32 placeholder (will calculate after)
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
                            self.socket.sendto(packet, addr)
                        
                        elif msg_type == self.PACKET_TYPE_PAD_INFO:
                            # Dolphin is asking: "Who is connected?"
                            try:
                                if len(data) < 24:
                                    continue
                                
                                num_slots = struct.unpack('<i', data[20:24])[0]
                                # It might ask for 4 slots, but sometimes the packet is short.
                                # Let's respond to exactly what it asked for.
                                slots_to_report = [data[24+i] for i in range(num_slots)] if len(data) >= 24 + num_slots else [0]
                                
                                req_server_id = struct.unpack('<I', data[12:16])[0] if len(data) >= 16 else self.server_id
                                
                                for slot_id in slots_to_report:
                                    # We only want Slot 0 to be our GameCube controller
                                    is_connected = (slot_id == 0)
                                    packet = self._create_pad_info_packet(pad_id=slot_id, connected=is_connected, server_id=req_server_id)
                                    self.socket.sendto(packet, addr)
                                
                                # This print will confirm Dolphin is hitting your server
                                print(f"  <- Reported Slot 0 as Connected to {addr[0]}", flush=True)
                                
                            except Exception as e:
                                print(f"  -> ERROR: Pad Info Response failed: {e}", flush=True)
                        
                        elif msg_type == self.PACKET_TYPE_PAD_DATA:
                            # Pad Data Request (0x1000002)
                            clients.add(addr)
                            if self.last_state:
                                packet = self._create_pad_data_packet(self.last_state, pad_id=0)
                                self.socket.sendto(packet, addr)
                                # Only log connection once, then log occasionally
                                if addr not in self._logged_clients:
                                    print(f"  <- Dolphin connected, streaming controller data", flush=True)
                                    self._logged_clients.add(addr)
                                elif len(self._logged_clients) == 1 and len(clients) == 1:
                                    # Log every 100th request to confirm we're responding
                                    if not hasattr(self, '_pad_data_counter'):
                                        self._pad_data_counter = 0
                                    self._pad_data_counter += 1
                                    if self._pad_data_counter % 100 == 0:
                                        buttons = self.last_state.get('buttons', {})
                                        active_btns = [k for k, v in buttons.items() if v]
                                        print(f"  <- Sent pad data (request #{self._pad_data_counter}, buttons={len(active_btns)})", flush=True)
                            else:
                                print(f"  -> Pad Data Request but no controller state available", flush=True)
                    
                    except socket.timeout:
                        # No data, continue (this is normal)
                        pass
                    except Exception as e:
                        # Connection closed or error, log it
                        print(f"  -> Socket error: {e}", flush=True)
            except Exception as e:
                print(f"  -> Fatal error: {e}", flush=True)
            
            time.sleep(0.001)  # Small delay to prevent CPU spinning
        
        print("DSU request handler thread stopped", flush=True)
