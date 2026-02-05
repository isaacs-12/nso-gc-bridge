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
    import tkinter as tk
    from tkinter import ttk, scrolledtext, messagebox
except ImportError:
    print("tkinter is required for the launcher. Install python-tk or use system Python.")
    sys.exit(1)


def build_command(use_ble, ble_address, use_dsu, use_gui, use_debug, use_discover, log_path):
    """Build the command list for main.py based on selected options."""
    cmd = [sys.executable, "main.py"]
    if use_ble:
        cmd.append("--ble")
        if ble_address and ble_address.strip():
            cmd.extend(["--address", ble_address.strip()])
        if use_debug:
            cmd.append("--ble-debug")
        if use_discover:
            cmd.append("--ble-discover")
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

        self.conn_var = tk.StringVar(value="usb")
        ttk.Radiobutton(conn_frame, text="USB (wired)", variable=self.conn_var, value="usb").pack(anchor=tk.W)
        ttk.Radiobutton(conn_frame, text="BLE (wireless)", variable=self.conn_var, value="ble").pack(anchor=tk.W)

        addr_frame = ttk.Frame(conn_frame)
        addr_frame.pack(fill=tk.X, pady=(5, 0))
        ttk.Label(addr_frame, text="BLE address (optional, leave empty to auto-discover):").pack(anchor=tk.W)
        self.ble_address_var = tk.StringVar()
        self.ble_address_entry = ttk.Entry(addr_frame, textvariable=self.ble_address_var, width=45)
        self.ble_address_entry.pack(fill=tk.X, pady=2)

        scan_frame = ttk.Frame(conn_frame)
        scan_frame.pack(fill=tk.X, pady=(5, 0))
        ttk.Button(scan_frame, text="Scan for BLE devices", command=self._on_ble_scan).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(scan_frame, text="Find BLE controller (scan diff)", command=self._on_ble_scan_diff).pack(side=tk.LEFT)

        # --- Options ---
        opt_frame = ttk.LabelFrame(main, text="Options", padding=5)
        opt_frame.pack(fill=tk.X, pady=(0, 5))

        self.dsu_var = tk.BooleanVar(value=True)
        dsu_row = ttk.Frame(opt_frame)
        dsu_row.pack(fill=tk.X)
        ttk.Checkbutton(dsu_row, text="DSU server (Dolphin)", variable=self.dsu_var).pack(side=tk.LEFT)
        ttk.Button(dsu_row, text="Free orphaned port", command=self._on_free_port).pack(side=tk.LEFT, padx=(10, 0))

        self.gui_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt_frame, text="Show controller GUI", variable=self.gui_var).pack(anchor=tk.W)

        self.debug_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt_frame, text="Debug (raw bytes, latency)", variable=self.debug_var).pack(anchor=tk.W)

        self.discover_var = tk.BooleanVar(value=False)
        self.discover_cb = ttk.Checkbutton(opt_frame, text="BLE discover (interactive calibration)", variable=self.discover_var)
        self.discover_cb.pack(anchor=tk.W)

        log_opt = ttk.Frame(opt_frame)
        log_opt.pack(fill=tk.X, pady=(2, 0))
        self.log_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(log_opt, text="Log to file:", variable=self.log_var).pack(side=tk.LEFT)
        self.log_path_var = tk.StringVar(value="latency.jsonl")
        self.log_path_entry = ttk.Entry(log_opt, textvariable=self.log_path_var, width=25)
        self.log_path_entry.pack(side=tk.LEFT, padx=5)

        # --- Buttons ---
        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill=tk.X, pady=5)

        self.start_btn = ttk.Button(btn_frame, text="Start Driver", command=self._on_start)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 5))

        self.stop_btn = ttk.Button(btn_frame, text="Stop", command=self._on_stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=(0, 5))

        self.send_enter_btn = ttk.Button(btn_frame, text="Send Enter", command=self._on_send_enter, state=tk.DISABLED)
        self.send_enter_btn.pack(side=tk.LEFT, padx=(0, 5))
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
        self._update_ble_options_visibility()
        self.conn_var.trace_add("write", lambda *a: self._update_ble_options_visibility())

    def _update_ble_options_visibility(self):
        """Show BLE-only options only when BLE is selected."""
        use_ble = self.conn_var.get() == "ble"
        if use_ble:
            self.discover_cb.config(state=tk.NORMAL)
        else:
            self.discover_var.set(False)
            self.discover_cb.config(state=tk.DISABLED)

    def _log(self, msg):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, msg)
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

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
        use_ble = self.conn_var.get() == "ble"
        ble_addr = self.ble_address_var.get()
        use_dsu = self.dsu_var.get()
        use_gui = self.gui_var.get()
        use_debug = self.debug_var.get()
        use_discover = use_ble and self.discover_var.get()
        log_path = self.log_path_var.get() if self.log_var.get() else None

        cmd = build_command(use_ble, ble_addr, use_dsu, use_gui, use_debug, use_discover, log_path)
        self._log(f">>> {' '.join(cmd)}\n\n")

        use_stdin = use_discover  # BLE discover needs Enter for interactive prompts

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
        # Send Enter enabled when process runs and has stdin (discover or scan-diff)
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
