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


def build_command(use_ble, ble_address, use_dsu, use_gui, use_debug, log_path):
    """Build the command list for main.py based on selected options."""
    cmd = [sys.executable, "main.py"]
    if use_ble:
        cmd.append("--ble")
        if ble_address and ble_address.strip():
            cmd.extend(["--address", ble_address.strip()])
        if use_debug:
            cmd.append("--ble-debug")  # Debug only works with BLE (raw byte dumps)
    else:
        cmd.append("--usb")
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
        self.root.minsize(420, 380)
        self.root.geometry("520x480")

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

        # --- Options ---
        opt_frame = ttk.LabelFrame(main, text="Options", padding=5)
        opt_frame.pack(fill=tk.X, pady=(0, 5))

        self.dsu_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt_frame, text="DSU server (Dolphin)", variable=self.dsu_var).pack(anchor=tk.W)

        self.gui_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt_frame, text="Show controller GUI", variable=self.gui_var).pack(anchor=tk.W)

        self.debug_var = tk.BooleanVar(value=False)
        self.debug_cb = ttk.Checkbutton(opt_frame, text="Debug (BLE only: raw byte dumps)", variable=self.debug_var)
        self.debug_cb.pack(anchor=tk.W)

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

        # --- Log view ---
        log_frame = ttk.LabelFrame(main, text="Log", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(5, 0))

        self.log_text = scrolledtext.ScrolledText(log_frame, height=12, wrap=tk.WORD, font=("Menlo", 10))
        self.log_text.pack(fill=tk.BOTH, expand=True)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._update_debug_visibility()
        self.conn_var.trace_add("write", lambda *a: self._update_debug_visibility())

    def _update_debug_visibility(self):
        """Show debug option only for BLE (USB doesn't support it)."""
        use_ble = self.conn_var.get() == "ble"
        if use_ble:
            self.debug_cb.config(state=tk.NORMAL)
        else:
            self.debug_var.set(False)
            self.debug_cb.config(state=tk.DISABLED)

    def _log(self, msg):
        self.log_text.insert(tk.END, msg)
        self.log_text.see(tk.END)

    def _poll_log_queue(self):
        try:
            while True:
                action, data = self.log_queue.get_nowait()
                if action == "append":
                    self.log_text.insert(tk.END, data)
                    self.log_text.see(tk.END)
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
        log_path = self.log_path_var.get() if self.log_var.get() else None

        cmd = build_command(use_ble, ble_addr, use_dsu, use_gui, use_debug, log_path)
        self._log(f">>> {' '.join(cmd)}\n\n")

        def run():
            try:
                self.process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    cwd=SCRIPT_DIR,
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
