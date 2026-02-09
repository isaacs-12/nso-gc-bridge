#!/usr/bin/env python3
"""
NSO GameCube Controller Bridge - Launcher UI

A simple GUI to run the driver with checkboxes for flags and a log view.
Double-click or run: python3 launcher.py
"""

import subprocess
import sys
import os
import threading
import queue

# Set TCL/TK paths for bundled .app (must run before tkinter import)
if getattr(sys, "frozen", False):
    exe_dir = os.path.dirname(sys.executable)
    resources = os.path.normpath(os.path.join(exe_dir, "..", "Resources"))
    for tcl_name, tk_name in [("tcl9.0", "tk9.0"), ("tcl8.6", "tk8.6")]:
        tcl_path = os.path.join(resources, tcl_name)
        tk_path = os.path.join(resources, tk_name)
        if os.path.isdir(tcl_path) and os.path.isdir(tk_path):
            os.environ["TCL_LIBRARY"] = tcl_path
            os.environ["TK_LIBRARY"] = tk_path
            break


def _get_script_dir():
    """Script directory, or bundle path when frozen (py2app or PyInstaller)."""
    if getattr(sys, "_MEIPASS", None):
        # PyInstaller: data files extracted to _MEIPASS
        return sys._MEIPASS
    if getattr(sys, "frozen", False):
        # py2app: executable is in .app/Contents/MacOS/, resources in Contents/Resources/
        exe_dir = os.path.dirname(sys.executable)
        return os.path.normpath(os.path.join(exe_dir, "..", "Resources"))
    return os.path.dirname(os.path.abspath(__file__))


SCRIPT_DIR = _get_script_dir()
os.chdir(SCRIPT_DIR)

try:
    from controller_storage import (
        load_controllers, save_controllers, add_controller, remove_controller,
        get_last_connected, load_slots_config, save_slots_config,
    )
except ImportError:
    def load_controllers():
        return []
    def save_controllers(_):
        pass
    def add_controller(addr, name):
        pass
    def remove_controller(addr):
        pass
    def get_last_connected():
        return None
    def load_slots_config():
        return []
    def save_slots_config(_):
        pass

try:
    import tkinter as tk
    from tkinter import ttk, scrolledtext, messagebox, simpledialog
except ImportError:
    print("tkinter is required for the launcher. Install python-tk or use system Python.")
    sys.exit(1)


class ToolTip:
    """Show a tooltip on hover after a short delay."""
    def __init__(self, widget, text, delay_ms=500):
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self.tip_window = None
        self.after_id = None
        widget.bind("<Enter>", self._on_enter)
        widget.bind("<Leave>", self._on_leave)

    def _on_enter(self, event=None):
        self.after_id = self.widget.after(self.delay_ms, self._show)

    def _on_leave(self, event=None):
        if self.after_id:
            self.widget.after_cancel(self.after_id)
            self.after_id = None
        self._hide()

    def _show(self):
        self.after_id = None
        if self.tip_window:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(tw, text=self.text, justify=tk.LEFT,
                         relief=tk.SOLID, borderwidth=1, font=("TkDefaultFont", 9), padx=4, pady=2)
        label.pack()

    def _hide(self):
        if self.tip_window:
            self.tip_window.destroy()
            self.tip_window = None


def build_command(use_ble, ble_address, use_dsu, use_gui, use_debug, log_path, multi_slots=None):
    """Build the command list for main.py based on selected options.
    If multi_slots is not None, use multi-controller mode (list of {slot, type, address?})."""
    cmd = [sys.executable, "main.py"]
    if multi_slots is not None:
        cmd.append("--multi")
        if not use_dsu:
            cmd.append("--no-dsu")
        if use_debug:
            cmd.append("--debug")
        if log_path and log_path.strip():
            cmd.extend(["--log", log_path.strip()])
        return cmd
    if use_ble:
        cmd.append("--ble")
        if ble_address and ble_address.strip():
            cmd.extend(["--address", ble_address.strip()])
        if use_debug:
            cmd.append("--ble-debug")
    else:
        cmd.append("--usb")
        if use_debug:
            cmd.append("--debug")
    if not use_dsu:
        cmd.append("--no-dsu")
    if use_gui:
        cmd.append("--gui")
    if log_path and log_path.strip():
        cmd.extend(["--log", log_path.strip()])
    return cmd


class LauncherApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("NSO GameCube Controller Bridge")
        self.root.minsize(420, 420)
        self.root.geometry("560x560")

        self.process = None  # subprocess when driver is running
        self.log_queue = queue.Queue()

        self._build_ui()
        self._poll_log_queue()

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        # --- Connection ---
        conn_frame = ttk.LabelFrame(main, text="Connection", padding=5)
        conn_frame.pack(fill=tk.X, pady=(0, 5))

        self.multi_var = tk.BooleanVar(value=False)
        multi_cb = ttk.Checkbutton(conn_frame, text="Multi-controller (assign slots)", variable=self.multi_var,
                                   command=self._toggle_multi_mode)
        multi_cb.pack(anchor=tk.W)
        ToolTip(multi_cb, "Connect 2–4 controllers. Assign each to a Dolphin port (slot 0–3).")

        self.single_frame = ttk.Frame(conn_frame)
        self.single_frame.pack(fill=tk.X, pady=(5, 0))

        self.conn_var = tk.StringVar(value="usb")
        usb_rb = ttk.Radiobutton(self.single_frame, text="USB (wired)", variable=self.conn_var, value="usb")
        usb_rb.pack(anchor=tk.W)
        ToolTip(usb_rb, "Connect controller via USB cable. Ideal for low input latency (~4ms)")
        ble_rb = ttk.Radiobutton(self.single_frame, text="BLE (wireless)", variable=self.conn_var, value="ble")
        ble_rb.pack(anchor=tk.W)
        ToolTip(ble_rb, "Connect controller wirelessly. Hold pair button when scanning.")

        addr_frame = ttk.Frame(self.single_frame)
        addr_frame.pack(fill=tk.X, pady=(5, 0))
        ttk.Label(addr_frame, text="Select Saved Controller (or scan for new):").pack(anchor=tk.W)
        self.ble_controller_var = tk.StringVar(value="Scan for controller")
        self.ble_controller_combo = ttk.Combobox(addr_frame, textvariable=self.ble_controller_var, width=42, state="readonly")
        self.ble_controller_combo.pack(fill=tk.X, pady=2)
        ToolTip(self.ble_controller_combo, "Pick a saved controller to connect directly, or Scan to find a new one.")
        self._refresh_controller_combo()

        self.multi_frame = ttk.Frame(conn_frame)
        self.multi_hint = ttk.Label(self.multi_frame, text="", foreground="gray")
        self._build_multi_slots_ui()
        self.multi_hint.pack(anchor=tk.W, pady=(0, 2))

        manage_frame = ttk.Frame(conn_frame)
        manage_frame.pack(fill=tk.X, pady=(2, 0))
        manage_btn = ttk.Button(manage_frame, text="Manage saved controllers", command=self._on_manage_saved)
        manage_btn.pack(side=tk.LEFT)
        ToolTip(manage_btn, "Add, remove, or rename saved controllers. Use Add last connected during/after a successful connection to save the controller for easy connection in the future.")

        self.advanced_expanded = False
        self.advanced_toggle = ttk.Label(conn_frame, text="▶ Advanced", cursor="hand2")
        self.advanced_toggle.pack(anchor=tk.W, pady=(5, 0))
        self.advanced_toggle.bind("<Button-1>", self._toggle_advanced)
        ToolTip(self.advanced_toggle, "Show scan tools for finding controller addresses.")

        self.advanced_frame = ttk.Frame(conn_frame)
        scan_btn = ttk.Button(self.advanced_frame, text="Scan for BLE devices", command=self._on_ble_scan)
        scan_btn.pack(side=tk.LEFT, padx=(0, 5))
        ToolTip(scan_btn, "List nearby BLE devices. Put controller in pairing mode first. Copy an address to add in Manage.")
        diff_btn = ttk.Button(self.advanced_frame, text="Find BLE controller (scan diff)", command=self._on_ble_scan_diff)
        diff_btn.pack(side=tk.LEFT)
        ToolTip(diff_btn, "Two scans: controller ON then OFF. Identifies your controller among multiple BLE devices. Use Send Enter when prompted.")

        self._toggle_multi_mode()

        # --- Options ---
        opt_frame = ttk.LabelFrame(main, text="Options", padding=5)
        opt_frame.pack(fill=tk.X, pady=(0, 5))

        self.dsu_var = tk.BooleanVar(value=True)
        dsu_row = ttk.Frame(opt_frame)
        dsu_row.pack(fill=tk.X)
        dsu_cb = ttk.Checkbutton(dsu_row, text="DSU server (Dolphin)", variable=self.dsu_var)
        dsu_cb.pack(side=tk.LEFT)
        ToolTip(dsu_cb, "Enable DSU server so Dolphin can use the controller. Required for emulator input.")
        free_btn = ttk.Button(dsu_row, text="Free orphaned port", command=self._on_free_port)
        free_btn.pack(side=tk.LEFT, padx=(10, 0))
        ToolTip(free_btn, "Kill any process holding port 26760. Use when you see 'Address already in use'.")

        self.gui_var = tk.BooleanVar(value=False)
        gui_cb = ttk.Checkbutton(opt_frame, text="Show controller GUI", variable=self.gui_var)
        gui_cb.pack(anchor=tk.W)
        ToolTip(gui_cb, "Open a window showing live button and stick input. Useful for debugging")

        self.debug_var = tk.BooleanVar(value=False)
        debug_cb = ttk.Checkbutton(opt_frame, text="Debug (latency)", variable=self.debug_var)
        debug_cb.pack(anchor=tk.W)
        ToolTip(debug_cb, "Print input latency stats every ~100 reports. Measures time between input reports sent over BLE or USB.")

        # --- Buttons ---
        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill=tk.X, pady=5)

        self.start_btn = ttk.Button(btn_frame, text="Start Driver", command=self._on_start)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 5))
        ToolTip(self.start_btn, "Start the controller driver. For BLE, hold pair button if scanning.")

        self.stop_btn = ttk.Button(btn_frame, text="Stop", command=self._on_stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=(0, 5))
        ToolTip(self.stop_btn, "Stop the running driver.")

        self.rumble_btn = ttk.Button(btn_frame, text="Test Rumble", command=self._on_test_rumble, state=tk.DISABLED)
        self.rumble_btn.pack(side=tk.LEFT, padx=(0, 5))
        ToolTip(self.rumble_btn, "Send a short rumble burst to the controller (driver must be running).")

        self.send_enter_btn = ttk.Button(btn_frame, text="Send Enter", command=self._on_send_enter, state=tk.DISABLED)
        self.send_enter_btn.pack(side=tk.LEFT, padx=(0, 5))
        ToolTip(self.send_enter_btn, "Advance interactive prompts (Find BLE controller). Or press Enter key.")
        self.send_enter_hint = ttk.Label(btn_frame, text="(Enter key or click when prompted)", foreground="gray")
        self.send_enter_hint.pack(side=tk.LEFT)

        # --- Log view ---
        log_frame = ttk.LabelFrame(main, text="Log", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(5, 0))

        self.log_text = scrolledtext.ScrolledText(log_frame, height=12, wrap=tk.WORD, font=("Menlo", 10), state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        self.interactive_hint = ttk.Label(log_frame, text="", foreground="blue")
        self.interactive_hint.pack(anchor=tk.W, pady=(2, 0))

        # Bind Enter key to send to interactive process (works when Send Enter is enabled)
        self.root.bind("<Return>", self._on_enter_key)
        self.root.bind("<KP_Enter>", self._on_enter_key)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_multi_slots_ui(self):
        """Build the 4-slot grid for multi-controller mode."""
        self.slot_vars = []
        self.slot_combos = []
        self._controller_map = getattr(self, "_controller_map", {})
        for slot in range(4):
            row = ttk.Frame(self.multi_frame)
            row.pack(fill=tk.X, pady=2)
            ttk.Label(row, text=f"Slot {slot} (Port {slot + 1}):", width=14, anchor=tk.W).pack(side=tk.LEFT, padx=(0, 5))
            type_var = tk.StringVar(value="empty")
            self.slot_vars.append(type_var)
            for val, lbl in [("empty", "Empty"), ("usb", "USB"), ("ble", "BLE")]:
                rb = ttk.Radiobutton(row, text=lbl, variable=type_var, value=val,
                                     command=lambda s=slot: self._on_slot_type_change(s))
                rb.pack(side=tk.LEFT, padx=(0, 8))
            combo = ttk.Combobox(row, width=28, state="readonly")
            self.slot_combos.append(combo)
            combo.bind("<<ComboboxSelected>>", lambda e, s=slot: self._on_slot_combo_change(s))

    def _refresh_slot_combos(self):
        """Refresh BLE controller dropdowns for all slots."""
        controllers = load_controllers()
        values = ["Scan for controller"]
        self._controller_map = {}
        for c in controllers:
            name = c.get("name", c["address"])
            addr = c["address"]
            values.append(name)
            self._controller_map[name] = addr
        for combo in self.slot_combos:
            combo["values"] = values
            if combo.get() not in values:
                combo.set("Scan for controller")

    def _on_slot_type_change(self, slot):
        """Show/hide BLE combo when slot type changes."""
        combo = self.slot_combos[slot]
        if self.slot_vars[slot].get() == "ble":
            combo.pack(side=tk.LEFT, padx=(5, 0))
        else:
            combo.pack_forget()

    def _on_slot_combo_change(self, slot):
        pass  # Selection stored in combo

    def _update_multi_hint(self):
        """Update USB count hint in multi-controller mode."""
        try:
            from main import count_usb_controllers
            n = count_usb_controllers()
            self.multi_hint.config(text=f"({n} USB controller(s) detected)" if n else "(No USB controllers detected)")
        except Exception:
            self.multi_hint.config(text="")

    def _toggle_multi_mode(self):
        """Show single or multi-controller UI based on checkbox."""
        if self.multi_var.get():
            self.single_frame.pack_forget()
            self.multi_frame.pack(fill=tk.X, pady=(5, 0))
            self._refresh_slot_combos()
            self._update_multi_hint()
            saved = load_slots_config()
            slot_to_cfg = {c["slot"]: c for c in saved}
            for i in range(4):
                cfg = slot_to_cfg.get(i)
                if cfg:
                    self.slot_vars[i].set(cfg.get("type", "empty"))
                    if cfg.get("type") == "ble" and cfg.get("address"):
                        for c in load_controllers():
                            if c["address"] == cfg["address"]:
                                self.slot_combos[i].set(c.get("name", c["address"]))
                                break
                else:
                    self.slot_vars[i].set("empty")
                self._on_slot_type_change(i)
        else:
            self.multi_frame.pack_forget()
            self.single_frame.pack(fill=tk.X, pady=(5, 0))

    def _get_multi_slots_config(self):
        """Build slots config from UI. Returns list of {slot, type, address?} or None if not multi."""
        if not self.multi_var.get():
            return None
        slots = []
        for slot in range(4):
            type_var = self.slot_vars[slot].get()
            if type_var == "empty":
                continue
            cfg = {"slot": slot, "type": type_var}
            if type_var == "ble":
                sel = self.slot_combos[slot].get()
                addr = self._controller_map.get(sel, "") if sel and sel != "Scan for controller" else ""
                cfg["address"] = addr
            slots.append(cfg)
        return slots if slots else None

    def _toggle_advanced(self, event=None):
        """Expand or collapse the Advanced section."""
        self.advanced_expanded = not self.advanced_expanded
        if self.advanced_expanded:
            self.advanced_frame.pack(fill=tk.X, pady=(2, 0))
            self.advanced_toggle.config(text="▼ Advanced")
        else:
            self.advanced_frame.pack_forget()
            self.advanced_toggle.config(text="▶ Advanced")

    def _refresh_controller_combo(self):
        """Reload saved controllers into the dropdown."""
        controllers = load_controllers()
        values = ["Scan for controller"]
        self._controller_map = {}  # name -> address
        for c in controllers:
            name = c.get("name", c["address"])
            addr = c["address"]
            values.append(name)
            self._controller_map[name] = addr
        self.ble_controller_combo["values"] = values
        if hasattr(self, "slot_combos"):
            for combo in self.slot_combos:
                combo["values"] = values
        last = get_last_connected()
        if last and last in [c["address"] for c in controllers]:
            for c in controllers:
                if c["address"] == last:
                    self.ble_controller_var.set(c.get("name", last))
                    break
        elif self.ble_controller_var.get() not in values:
            self.ble_controller_var.set("Scan for controller")

    def _get_ble_address(self):
        """Return the BLE address to use (empty for scan)."""
        sel = self.ble_controller_var.get()
        if sel == "Scan for controller" or not sel:
            return ""
        return getattr(self, "_controller_map", {}).get(sel, "")

    def _on_manage_saved(self):
        """Open dialog to add/remove/rename saved controllers."""
        dlg = tk.Toplevel(self.root)
        dlg.title("Saved Controllers")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.geometry("500x300")

        main = ttk.Frame(dlg, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        ttk.Label(main, text="Saved controllers (connect directly without scanning):").pack(anchor=tk.W)
        list_frame = ttk.Frame(main)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        listbox = tk.Listbox(list_frame, height=8)
        listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(list_frame, command=listbox.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        listbox.config(yscrollcommand=scroll.set)

        def refresh_list():
            listbox.delete(0, tk.END)
            for c in load_controllers():
                listbox.insert(tk.END, f"{c.get('name', 'Unnamed')}  ({c['address'][:20]}...)")

        refresh_list()

        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill=tk.X, pady=5)
        add_btn = ttk.Button(btn_frame, text="Add...", command=lambda: _add_dialog(dlg, refresh_list))
        add_btn.pack(side=tk.LEFT, padx=(0, 5))
        ToolTip(add_btn, "Add a controller by address and name. Get address from Scan or Find BLE controller.")
        rem_btn = ttk.Button(btn_frame, text="Remove", command=lambda: _remove_selected(listbox, refresh_list))
        rem_btn.pack(side=tk.LEFT, padx=(0, 5))
        ToolTip(rem_btn, "Remove the selected controller from saved list.")
        last_btn = ttk.Button(btn_frame, text="Add last connected", command=lambda: _add_last(dlg, refresh_list))
        last_btn.pack(side=tk.LEFT, padx=(0, 5))
        ToolTip(last_btn, "Save the controller you just connected to. Connect first, then add here.")
        close_btn = ttk.Button(btn_frame, text="Close", command=dlg.destroy)
        close_btn.pack(side=tk.RIGHT)
        ToolTip(close_btn, "Close this dialog.")

        def _add_dialog(parent, refresh):
            addr = simpledialog.askstring("Add Controller", "BLE address:", parent=parent)
            if addr and addr.strip():
                name = simpledialog.askstring("Add Controller", "Name for this controller:", parent=parent)
                if name is not None:
                    add_controller(addr.strip(), name.strip() or addr.strip())
                    refresh()
                    self._refresh_controller_combo()

        def _remove_selected(lb, refresh):
            sel = lb.curselection()
            if not sel:
                return
            idx = sel[0]
            controllers = load_controllers()
            if 0 <= idx < len(controllers):
                remove_controller(controllers[idx]["address"])
                refresh()
                self._refresh_controller_combo()

        def _add_last(parent, refresh):
            addr = get_last_connected()
            if not addr:
                messagebox.showinfo("Add Last Connected", "No last connected address. Connect to a controller first.", parent=parent)
                return
            if addr in [c["address"] for c in load_controllers()]:
                messagebox.showinfo("Add Last Connected", "Already saved.", parent=parent)
                return
            name = simpledialog.askstring("Add Controller", f"Name for {addr[:24]}...:", parent=parent)
            if name is not None:
                add_controller(addr, name.strip() or addr[:16])
                refresh()
                self._refresh_controller_combo()

    def _log(self, msg):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, msg)
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def _on_test_rumble(self):
        """Send a test rumble packet to the DSU server (runs in background)."""
        def do_rumble():
            try:
                from dsu_server import send_test_rumble, DSUServer
                if send_test_rumble(port=DSUServer.DSU_PORT, slot=0, duration_ms=500):
                    self.log_queue.put(("append", "✓ Rumble test sent\n"))
                else:
                    self.log_queue.put(("append", "✗ Rumble test failed (is driver running?)\n"))
            except Exception as e:
                self.log_queue.put(("append", f"✗ Rumble test error: {e}\n"))
        threading.Thread(target=do_rumble, daemon=True).start()

    def _on_free_port(self):
        """Kill process holding DSU port 26760."""
        try:
            from dsu_server import free_orphaned_port, DSUServer
            if free_orphaned_port(DSUServer.DSU_PORT):
                self._log(f"✓ Freed port {DSUServer.DSU_PORT}\n")
            else:
                self._log(f"Port {DSUServer.DSU_PORT} is not in use (nothing to free)\n")
        except Exception as e:
            self._log(f"Could not free port: {e}\n")

    def _on_ble_scan(self):
        """Run --ble-scan and show output in log."""
        self._log("\n>>> Scanning for BLE devices (put controller in pairing mode)...\n\n")
        def run():
            try:
                result = subprocess.run(
                    [sys.executable, "main.py", "--ble-scan"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=SCRIPT_DIR,
                    timeout=25,
                )
                out = (result.stdout or "") + (result.stderr or "")
                self.log_queue.put(("append", out + "\n"))
            except subprocess.TimeoutExpired:
                self.log_queue.put(("append", "[Scan timed out]\n"))
            except Exception as e:
                self.log_queue.put(("append", f"[Error: {e}]\n"))
        threading.Thread(target=run, daemon=True).start()

    def _on_ble_scan_diff(self):
        """Run --ble-scan-diff (interactive). Uses main process slot; click Send Enter when prompted."""
        if self.process and self.process.poll() is None:
            self._log("\nStop the driver first, then click Find BLE controller.\n")
            return
        cmd = [sys.executable, "main.py", "--ble-scan-diff"]
        self._log(f">>> {' '.join(cmd)}\n\n")
        self._run_interactive_process(cmd)

    def _on_send_enter(self):
        """Send Enter to the running process (for interactive prompts)."""
        self._send_enter_to_process()

    def _on_enter_key(self, event=None):
        """Handle Enter key - send to process when interactive, else do nothing."""
        if self._send_enter_to_process():
            return "break"  # Consume key so it doesn't insert newline in focused widget

    def _send_enter_to_process(self):
        """Send Enter to the running process. Returns True if sent."""
        if self.process and self.process.poll() is None and self.process.stdin:
            try:
                self.process.stdin.write("\n")
                self.process.stdin.flush()
                return True
            except Exception:
                pass
        return False

    def _run_interactive_process(self, cmd):
        """Run a process with stdin=PIPE so user can send Enter via button."""
        def run():
            try:
                env = os.environ.copy()
                env.setdefault("PYTHONIOENCODING", "utf-8")
                env["PYTHONUNBUFFERED"] = "1"
                self.process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    cwd=SCRIPT_DIR,
                    env=env,
                )
                self.root.after(0, lambda: self._set_running(True))
                for line in self.process.stdout:
                    self.log_queue.put(("append", line))
                self.process.wait()
            except Exception as e:
                self.log_queue.put(("append", f"\n[Error: {e}]\n"))
            finally:
                self.root.after(0, lambda: self._set_running(False))
                self.log_queue.put(("append", f"\n[Process exited with code {self.process.returncode if self.process else 'N/A'}]\n"))
        threading.Thread(target=run, daemon=True).start()

    def _poll_log_queue(self):
        try:
            while True:
                action, data = self.log_queue.get_nowait()
                if action == "append":
                    self.log_text.config(state=tk.NORMAL)
                    self.log_text.insert(tk.END, data)
                    self.log_text.see(tk.END)
                    self.log_text.config(state=tk.DISABLED)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_log_queue)

    def _on_start(self):
        if self.process and self.process.poll() is None:
            return
        multi_slots = self._get_multi_slots_config()
        use_dsu = self.dsu_var.get()
        use_gui = self.gui_var.get()
        use_debug = self.debug_var.get()

        if multi_slots is not None:
            save_slots_config(multi_slots)
            use_ble = False
            ble_addr = ""
        else:
            use_ble = self.conn_var.get() == "ble"
            ble_addr = self._get_ble_address() if use_ble else ""

        cmd = build_command(use_ble, ble_addr, use_dsu, use_gui, use_debug, None, multi_slots)
        self._log(f">>> {' '.join(cmd)}\n\n")

        use_stdin = False

        def run():
            try:
                env = os.environ.copy()
                env.setdefault("PYTHONIOENCODING", "utf-8")
                env["PYTHONUNBUFFERED"] = "1"
                self.process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE if use_stdin else subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    cwd=SCRIPT_DIR,
                    env=env,
                )
                self.root.after(0, lambda: self._set_running(True))
                for line in self.process.stdout:
                    self.log_queue.put(("append", line))
                self.process.wait()
            except Exception as e:
                self.log_queue.put(("append", f"\n[Error: {e}]\n"))
            finally:
                self.root.after(0, lambda: self._set_running(False))
                self.log_queue.put(("append", f"\n[Process exited with code {self.process.returncode if self.process else 'N/A'}]\n"))

        threading.Thread(target=run, daemon=True).start()

    def _set_running(self, running):
        self.start_btn.config(state=tk.DISABLED if running else tk.NORMAL)
        self.stop_btn.config(state=tk.NORMAL if running else tk.DISABLED)
        self.rumble_btn.config(state=tk.NORMAL if running else tk.DISABLED)
        # Send Enter enabled when process runs and has stdin (scan-diff)
        has_stdin = running and self.process and self.process.stdin is not None
        self.send_enter_btn.config(state=tk.NORMAL if has_stdin else tk.DISABLED)
        self.interactive_hint.config(
            text="Press Enter (or click Send Enter) when the log prompts you." if has_stdin else ""
        )

    def _on_stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self._log("\n[Stopping...]\n")

    def _on_close(self):
        if self.process and self.process.poll() is None:
            if messagebox.askyesno("Stop driver?", "Driver is still running. Stop it and quit?"):
                self.process.terminate()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = LauncherApp()
    app.run()
