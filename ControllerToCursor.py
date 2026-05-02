"""
Controller Mouse Emulator — GUI Edition
Full-featured gamepad-to-mouse emulator with tkinter GUI.

PyInstaller build command:
Windows: pyinstaller --onefile --windowed --icon=icon.ico --add-data "icon.ico;." ControllerToCursor.py
Linux: pyinstaller --onefile --windowed --icon=icon.ico --add-data "icon.ico:." ControllerToCursor.py

Pip dependencies:
pip install pygame pyautogui tomli-w pyinstaller

"""

import sys
import os
import warnings
import io
import math
import time
import threading
import subprocess
import tomllib
import tomli_w

warnings.filterwarnings("ignore")
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'
os.environ['SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS'] = '1'

if sys.platform.startswith("linux"):
    os.environ.setdefault("DISPLAY", ":0")

_stderr = sys.stderr
sys.stderr = io.StringIO()
import pygame
sys.stderr = _stderr

import pyautogui
pyautogui.PAUSE    = 0
pyautogui.FAILSAFE = False

import tkinter as tk
from tkinter import ttk

# ─────────────────────────────────────────────────────────────────────────────
# Global state
# ─────────────────────────────────────────────────────────────────────────────
controller        = None
controller_count  = 0
config            = {}
emulation_mode    = True
sniper_mode       = False
calibrated_deadzone = 0.08
input_capture_active = False
mouse_residual_x = 0.0
mouse_residual_y = 0.0

screen_h = 0
screen_w = 0

button_memory  = {}
frame_cache    = {}
last_frame_ts  = 0

clock = pygame.time.Clock()

# ─────────────────────────────────────────────────────────────────────────────
# Button / hat maps
# ─────────────────────────────────────────────────────────────────────────────
_BUTTON_MAP = {
    "DP-U": (0,  1),
    "DP-D": (0, -1),
    "DP-L": (-1, 0),
    "DP-R": (1,  0),
}
_CONFIG_BUTTON_KEYS = {
    "A":  "A",
    "B":  "B",
    "X":  "X",
    "Y":  "Y",
    "LS": "left_stick_button",
    "RS": "right_stick_button",
    "LM": "left_menu",
    "RM": "right_menu",
    "LB": "left_shoulder_button",
    "RB": "right_shoulder_button",
}

ALL_BUTTON_LABELS = list(_BUTTON_MAP.keys()) + list(_CONFIG_BUTTON_KEYS.keys())

HOTKEY_ACTIONS = [
    "toggle_hotkey_1", "toggle_hotkey_2", "toggle_hotkey_3", "toggle_hotkey_4",
    "left_mouse", "right_mouse", "quit",
    "speech_to_text", "on_screen_keyboard",
    "mouse_speed_boost", "sniper_mode",
]

HOTKEY_LABELS = {
    "toggle_hotkey_1":    "Toggle Key 1",
    "toggle_hotkey_2":    "Toggle Key 2",
    "toggle_hotkey_3":    "Toggle Key 3",
    "toggle_hotkey_4":    "Toggle Key 4",
    "left_mouse":         "Left Mouse Button",
    "right_mouse":        "Right Mouse Button",
    "quit":               "Quit",
    "speech_to_text":     "Speech To Text",
    "on_screen_keyboard": "On Screen Keyboard",
    "mouse_speed_boost":  "Speed Boost",
    "sniper_mode":        "Sniper Mode",
}

BUTTON_DISPLAY_NAMES = {
    "A": "A Button",
    "B": "B Button",
    "X": "X Button",
    "Y": "Y Button",
    "left_shoulder_button": "Left Bumper (LB)",
    "right_shoulder_button": "Right Bumper (RB)",
    "left_menu": "View / Back",
    "right_menu": "Menu / Start",
    "left_stick_button": "Left Stick Click (L3)",
    "right_stick_button": "Right Stick Click (R3)",
}


def _hotkey_label_for_button_index(index):
    """Resolve a pygame button index to a hotkey combobox label (e.g. A, LS) using config."""
    for short, cfg_key in _CONFIG_BUTTON_KEYS.items():
        if config.get("buttons", {}).get(cfg_key) == index:
            return short
    return None


def _hotkey_label_for_hat(vec):
    """Resolve a D-pad hat vector to a label such as DP-U."""
    if vec == (0, 0):
        return None
    for short, pair in _BUTTON_MAP.items():
        if pair == vec:
            return short
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Config I/O
# ─────────────────────────────────────────────────────────────────────────────
def _config_path():
    if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
    else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, "ControllerToCursor-config.toml")

def load_config():
    global config
    path = _config_path()
    try:
        with open(path, "rb") as f:
            config = tomllib.load(f)
    except FileNotFoundError:
        # Create default config if none exists
        config = {
            "hotkeys": {
                "toggle_hotkey_1": "DP-U",
                "toggle_hotkey_2": "Y",
                "toggle_hotkey_3": "",
                "toggle_hotkey_4": "",
                "left_mouse": "A",
                "right_mouse": "B",
                "quit": "DP-D",
                "speech_to_text": "DP-L",
                "on_screen_keyboard": "DP-R",
                "mouse_speed_boost": "LS",
                "sniper_mode": "LB",
            },
            "mouse":   {"sensitivity": 10, "scroll_sensitivity": 10, "use_acceleration": True},
            "buttons": {
                "A": 0, "B": 1, "X": 2, "Y": 3,
                "left_stick_button": 8, "right_stick_button": 9,
                "right_menu": 7, "left_menu": 6,
                "right_shoulder_button": 5, "left_shoulder_button": 4,
            },
            "advanced": {"deadzone": 0.01, "sniper_factor": 0.25, "h_scroll": True},
        }
        # Save the newly created default config
        try:
            with open(path, "wb") as f:
                tomli_w.dump(config, f)
        except Exception:
            pass
    # Ensure all sections exist
    config.setdefault("mouse",    {})
    config.setdefault("hotkeys",  {})
    config.setdefault("buttons",  {})
    config.setdefault("advanced", {})
    # Set defaults but keep existing values
    config["mouse"].setdefault("sensitivity",       10)
    config["mouse"].setdefault("scroll_sensitivity", 10)
    config["mouse"].setdefault("use_acceleration",  True)
    config["advanced"].setdefault("deadzone",       0.01)
    config["advanced"].setdefault("sniper_factor",  0.25)
    config["advanced"].setdefault("h_scroll",       True)
    for a in HOTKEY_ACTIONS:
        config["hotkeys"].setdefault(a, "")

def save_config():
    path = _config_path()
    try:
        with open(path, "wb") as f:
            tomli_w.dump(config, f)
        return True
    except Exception as e:
        return str(e)

# ─────────────────────────────────────────────────────────────────────────────
# Controller init
# ─────────────────────────────────────────────────────────────────────────────

# Index of the currently selected joystick (chosen via dropdown when >1)
selected_controller_index = 0

def init_controller():
    global controller, controller_count, selected_controller_index
    pygame.init()
    pygame.joystick.init()
    pygame.event.pump()
    controller_count = pygame.joystick.get_count()
    if controller_count == 0:
        controller = None
        return False
    idx = min(selected_controller_index, controller_count - 1)
    controller = pygame.joystick.Joystick(idx)
    controller.init()
    return True

_controller_lock = threading.Lock()

def rescan_controller():
    """Manual rescan — safe to call from any thread. Never reinits the joystick subsystem."""
    global controller, controller_count, selected_controller_index
    with _controller_lock:
        count = pygame.joystick.get_count()
        controller_count = count
        if count == 0:
            controller = None
            return False
        idx = min(selected_controller_index, count - 1)
        joy = pygame.joystick.Joystick(idx)
        joy.init()
        controller = joy
        return True

def select_controller(index: int):
    """Switch the active controller to a specific joystick index."""
    global selected_controller_index
    selected_controller_index = index
    rescan_controller()

def _background_auto_scan():
    """Watches for plug/unplug by polling get_count(). Never calls joystick.quit/init."""
    import time as _time
    prev_count = -1
    while True:
        try:
            count = pygame.joystick.get_count()
            if count != prev_count:
                prev_count = count
                rescan_controller()
                try:
                    app.after(0, lambda c=count: app._on_controller_count_changed(c))
                except Exception:
                    pass
        except Exception:
            pass
        _time.sleep(2)

# ─────────────────────────────────────────────────────────────────────────────
# Input detection
# ─────────────────────────────────────────────────────────────────────────────
def _to_code(button_name):
    if button_name in _BUTTON_MAP:
        return _BUTTON_MAP[button_name]
    if button_name in _CONFIG_BUTTON_KEYS:
        return config.get("buttons", {}).get(_CONFIG_BUTTON_KEYS[button_name], 100)
    return 100

def is_pressed(action_name):
    global last_frame_ts, frame_cache, button_memory
    ticks = pygame.time.get_ticks()
    if ticks != last_frame_ts:
        last_frame_ts = ticks
        frame_cache   = {}
    if action_name in frame_cache:
        return frame_cache[action_name]

    button_name = config.get("hotkeys", {}).get(action_name, "")
    if not button_name:
        return False
    code = _to_code(button_name)
    if code == 100 or controller is None:
        return False
    try:
        if isinstance(code, tuple):
            is_down = controller.get_hat(0) == code
        else:
            is_down = bool(controller.get_button(int(code)))
    except Exception:
        is_down = False

    was_down = button_memory.get(action_name, False)
    if is_down and not was_down:
        result = "just_pressed"
    elif not is_down and was_down:
        result = "just_released"
    elif is_down:
        result = "is_held"
    else:
        result = False

    frame_cache[action_name]   = result
    button_memory[action_name] = is_down
    return result

# ─────────────────────────────────────────────────────────────────────────────
# Deadzone calibration
# ─────────────────────────────────────────────────────────────────────────────
def calibrate_deadzone(samples=60, callback=None):
    """Measure stick-at-rest noise and set deadzone. Runs in background thread."""
    def _run():
        global calibrated_deadzone
        if controller is None:
            if callback:
                callback(None, "No controller connected.")
            return
        if callback:
            callback("running", f"Hold sticks still… sampling {samples} frames.")
        max_noise = 0.0
        for _ in range(samples):
            pygame.event.pump()
            try:
                x = abs(controller.get_axis(0))
                y = abs(controller.get_axis(1))
                max_noise = max(max_noise, x, y)
            except Exception:
                pass
            time.sleep(1/60)
        dz = min(max_noise * 1.3 + 0.01, 0.35)
        calibrated_deadzone = dz
        config["advanced"]["deadzone"] = round(dz, 4)
        save_config()
        if callback:
            callback("done", f"Deadzone set to {dz:.4f}")
    threading.Thread(target=_run, daemon=True).start()

# ─────────────────────────────────────────────────────────────────────────────
# Core emulation
# ─────────────────────────────────────────────────────────────────────────────
def toggle_emulation_mode():
    global emulation_mode
    if input_capture_active:
        return
    active_keys = [
        f"toggle_hotkey_{i}"
        for i in range(1, 5)
        if config.get("hotkeys", {}).get(f"toggle_hotkey_{i}", "").strip()
    ]
    if active_keys:
        states        = [is_pressed(k) for k in active_keys]
        all_down      = all(s and s != "just_released" for s in states)
        any_new_press = any(s == "just_pressed" for s in states)
        if all_down and any_new_press:
            emulation_mode = not emulation_mode

    if (is_pressed("quit") == "just_pressed") and emulation_mode == True:
        pygame.quit()
        os.kill(os.getpid(), 9)

def emulate_mouse():
    global sniper_mode, mouse_residual_x, mouse_residual_y
    if controller is None or not config:
        return

    ui_sens      = float(config["mouse"].get("sensitivity", 10))
    ui_scroll    = float(config["mouse"].get("scroll_sensitivity", 10))
    use_accel    = bool(config["mouse"].get("use_acceleration", True))
    threshold    = float(config["advanced"].get("deadzone", calibrated_deadzone))
    sniper_fac   = float(config["advanced"].get("sniper_factor", 0.1))
    h_scroll_on  = bool(config["advanced"].get("h_scroll", True))

    sens = (ui_sens / 10.0)
    scroll_sens = (ui_scroll / 3.0)

    # Speed boost
    if is_pressed("mouse_speed_boost") in ("is_held", "just_pressed"):
        sens *= 2

    # Sniper mode
    sniper_active = is_pressed("sniper_mode") in ("is_held", "just_pressed")
    if sniper_active:
        sens *= sniper_fac

    # Clicks
    if is_pressed("left_mouse")  == "just_pressed":  pyautogui.mouseDown()
    if is_pressed("left_mouse")  == "just_released": pyautogui.mouseUp()
    if is_pressed("right_mouse") == "just_pressed":  pyautogui.rightClick()

    # System hotkeys
    if is_pressed("speech_to_text") == "just_pressed":
        if sys.platform.startswith("linux"):
            app._notify("warn", "This Feature is not availible on Linux")
        else:
            pyautogui.hotkey('win', 'h')
    
    if is_pressed("on_screen_keyboard") == "just_pressed":
        if sys.platform.startswith("linux"):
            try:
                subprocess.Popen(["onboard"])
            except Exception:
                app._notify("err", "Onboard not found. Install with: sudo apt install onboard")
        else:
            if getattr(sys, 'frozen', False):
                base_dir = os.path.dirname(sys.executable)
            else:
                base_dir = os.path.dirname(os.path.abspath(__file__))
            file_path = os.path.join(base_dir, "FreeVK.exe")
            if os.path.exists(file_path):
                subprocess.Popen(file_path)
            else:
                app._notify("error", "FreeVK.exe not found. Get it from: https://freevirtualkeyboard.com/")




    # Mouse movement — left stick axes 0/1
    try:
        rx = controller.get_axis(0)
        ry = controller.get_axis(1)
        x  = rx if abs(rx) > threshold else 0.0
        y  = ry if abs(ry) > threshold else 0.0
        if use_accel:
            # Sniper mode uses a softer curve so tiny stick movement still resolves.
            curve = 2.0 if not sniper_active else 1.35
            scale = 8.0 if not sniper_active else 5.0
            mx = math.copysign(abs(x) ** curve, x) * sens * scale
            my = math.copysign(abs(y) ** curve, y) * sens * scale
        else:
            scale = 2.0 if not sniper_active else 1.4
            mx = x * sens * scale
            my = y * sens * scale

        if sniper_active:
            if x and abs(mx) < 0.2:
                mx = math.copysign(0.2, x)
            if y and abs(my) < 0.2:
                my = math.copysign(0.2, y)

        if mx or my:
            mouse_residual_x += mx
            mouse_residual_y += my
            step_x = int(mouse_residual_x)
            step_y = int(mouse_residual_y)
            mouse_residual_x -= step_x
            mouse_residual_y -= step_y
            if step_x or step_y:
                pyautogui.moveRel(step_x, step_y)
    except Exception:
        pass

    # Scroll handling (Linux = step-based, Windows = proportional) ---
    def handle_scroll(axis_value, last_time_attr, is_vertical):
        boost = 2 if is_pressed("mouse_speed_boost") in ("is_held", "just_pressed") else 1

        # Linux: step-based scrolling for stability
        if sys.platform.startswith("linux"):
            if abs(axis_value) < threshold:
                setattr(app, last_time_attr, time.time())
                return

            now = time.time()
            interval = 0.06 / boost   # boost makes scroll faster

            if now - getattr(app, last_time_attr, 0) > interval:
                direction = -1 if axis_value > 0 else 1
                if is_vertical:
                    pyautogui.scroll(direction)
                else:
                    pyautogui.hscroll(direction)
                setattr(app, last_time_attr, now)

        # Windows: proportional scrolling
        else:
            if abs(axis_value) < threshold:
                return
            amount = int(axis_value * scroll_sens * 5 * boost)
            if amount != 0:
                if is_vertical:
                    pyautogui.scroll(-amount)
                else:
                    pyautogui.hscroll(amount)


    # --- Vertical scroll (axis 3) ---
    try:
        if controller.get_numaxes() > 3:
            s = controller.get_axis(3)
            handle_scroll(s, "last_scroll_time_v", True)
    except Exception:
        pass

    # --- Horizontal scroll (axis 2) ---
    if h_scroll_on:
        try:
            if controller.get_numaxes() > 2:
                h = controller.get_axis(2)
                handle_scroll(h, "last_scroll_time_h", False)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Main loop (runs in background thread)
# ─────────────────────────────────────────────────────────────────────────────
def main_loop():
    while True:
        pygame.event.pump()

        if emulation_mode:
            with _controller_lock:
                toggle_emulation_mode()
                emulate_mouse()
        else:
            with _controller_lock:
                toggle_emulation_mode()

        time.sleep(0.001) 
        clock.tick(120)

# ─────────────────────────────────────────────────────────────────────────────
# GUI
# ─────────────────────────────────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ControllerToCursor")

        global screen_h, screen_w
        
        # Automaticalley adjust to different screensizes
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()

        if screen_w >= 3840:
            width, height = 888, 1379
        elif screen_w >= 2560:
            width, height = 700, 1100
        elif screen_w >= 1920:
            width, height = 600, 960
        else:
            width, height = 300, 400

        self.minsize(width, height)
        self.geometry(f"{width}x{height}")

    
        # Fonts
        self.FH  = ("Segoe UI", 10)
        self.FB  = ("Segoe UI", 10, "bold")
        self.FT = ("Segoe UI", 15, "bold")
        self.FSM = ("Segoe UI", 9)

        # Design tokens
        self.BG = "#0f1419"
        self.PANEL = "#1a2332"
        self.CARD = "#1c2435"
        self.ELEVATED = "#243044"
        self.BORDER = "#334155"
        self.ACCENT = "#3b82f6"
        self.ACCENT_DIM = "#1e3a5f"
        self.TAB_SEL = "#2563eb"
        self.TEXT = "#f1f5f9"
        self.MUTED = "#94a3b8"
        self.GREEN = "#34d399"
        self.AMBER = "#fbbf24"
        self.RED = "#f87171"

        self.configure(bg=self.BG)
        self._notify_after_id = None
        self._hk_listen_action = None
        self._hk_listen_prev_emu = True
        self._hk_assign_buttons = {}
        self.resizable(True, True)

        # Icon logic
        try:
            if getattr(sys, 'frozen', False):
                base_dir = sys._MEIPASS
            else:
                base_dir = os.path.dirname(os.path.abspath(__file__))

            icon_path = os.path.join(base_dir, "icon.ico")
            self.iconbitmap(icon_path)
        except Exception:
            pass

        self._build_ui()
        self.bind("<Escape>", self._on_escape)
        self._refresh_status()
        self.after(500, self._poll_status)

    # ── UI construction ────────────────────────────────────────────────────
    def _apply_ttk_theme(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TNotebook", background=self.BG, borderwidth=0)
        style.configure(
            "TNotebook.Tab",
            background=self.ELEVATED,
            foreground=self.MUTED,
            font=self.FH,
            padding=[16, 8],
            borderwidth=0,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", self.TAB_SEL), ("!selected", self.ELEVATED)],
            foreground=[("selected", self.TEXT), ("!selected", self.MUTED)],
        )
        style.configure(
            "TScrollbar",
            background=self.ELEVATED,
            troughcolor=self.PANEL,
            borderwidth=0,
            arrowcolor=self.MUTED,
        )
        style.map("TScrollbar", background=[("active", self.BORDER)])

        style.configure(
            "TCombobox",
            fieldbackground=self.ELEVATED,
            background=self.BORDER,
            foreground=self.TEXT,
            arrowcolor=self.ACCENT,
            borderwidth=0,
            relief="flat",
            padding=4
        )
        
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", self.ELEVATED), ("focus", self.ELEVATED)],
            selectbackground=[("readonly", self.ELEVATED)],
            selectforeground=[("readonly", self.TEXT)],
            background=[("active", self.ACCENT_DIM)]
        )

        style.layout("TCombobox", [
            ('combobox.field', {
                'sticky': 'nswe',
                'children': [
                    ('combobox.downarrow', {'side': 'right', 'sticky': 'ns'}),
                    ('combobox.padding', {
                        'expand': '1',
                        'sticky': 'nswe',
                        'children': [
                            ('combobox.textarea', {'sticky': 'nswe'})
                        ]
                    })
                ]
            })
        ])

        self.option_add("*TCombobox*Listbox.background", self.ELEVATED)
        self.option_add("*TCombobox*Listbox.foreground", self.TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", self.ACCENT)
        self.option_add("*TCombobox*Listbox.selectForeground", "white")
        self.option_add("*TCombobox*Listbox.font", self.FH)
        self.option_add("*TCombobox*Listbox.borderwidth", "0")

    def _build_ui(self):
        self._apply_ttk_theme()

        hdr = tk.Frame(self, bg=self.PANEL, highlightthickness=1, highlightbackground=self.BORDER)
        hdr.pack(fill="x")
        left = tk.Frame(hdr, bg=self.PANEL)
        left.pack(side="left", fill="y", padx=20, pady=14)
        tk.Label(
            left,
            text="ControllerToCursor",
            font=self.FT,
            bg=self.PANEL,
            fg=self.TEXT,
        ).pack(anchor="w")
        tk.Label(
            left,
            text="Gamepad to mouse and keyboard — GUI Version.",
            font=self.FSM,
            bg=self.PANEL,
            fg=self.MUTED,
        ).pack(anchor="w", pady=(2, 0))

        right = tk.Frame(hdr, bg=self.PANEL)
        right.pack(side="right", padx=20, pady=14)
        self.lbl_status = tk.Label(
            right,
            text="Emulation off",
            font=self.FB,
            bg=self.ACCENT_DIM,
            fg=self.TEXT,
            padx=12,
            pady=6,
        )
        self.lbl_status.pack()

        body = tk.Frame(self, bg=self.BG)
        body.pack(fill="both", expand=True, padx=12, pady=(10, 0))

        nb = ttk.Notebook(body)
        nb.pack(fill="both", expand=True)

        self._tab_status(nb)
        self._tab_hotkeys(nb)
        self._tab_buttons(nb)
        self._tab_mouse(nb)

        foot = tk.Frame(self, bg=self.PANEL, highlightthickness=1, highlightbackground=self.BORDER)
        foot.pack(side="bottom", fill="x", padx=12, pady=(8, 10))
        self.notify_lbl = tk.Label(
            foot,
            text="",
            font=self.FSM,
            bg=self.PANEL,
            fg=self.MUTED,
            anchor="w",
            justify="left",
            padx=14,
            pady=8,
        )
        self.notify_lbl.pack(fill="x")

        def _wrap_notify(event):
            self.notify_lbl.configure(wraplength=max(280, event.width - 36))

        foot.bind("<Configure>", _wrap_notify)

    # ── Tab: Status ────────────────────────────────────────────────────────
    def _tab_status(self, nb):
        root = self._tab_frame(nb, "Status")

        c = self._card(
            root,
            "Device",
            "Connected gamepad reported by the driver. Controllers are detected automatically.",
        )
        self.lbl_ctrl_name = self._info_row(c, "Name", "—")
        self.lbl_ctrl_axes = self._info_row(c, "Axes", "—")
        self.lbl_ctrl_btns = self._info_row(c, "Buttons", "—")

        # Controller selector row (dropdown + rescan button)
        sel_row = tk.Frame(c, bg=self.CARD)
        sel_row.pack(fill="x", pady=(8, 0))
        tk.Label(sel_row, text="Active controller:", font=self.FSM, bg=self.CARD, fg=self.MUTED).pack(side="left")
        self._ctrl_select_var = tk.StringVar(value="Auto")
        self._ctrl_dropdown = ttk.Combobox(
            sel_row,
            textvariable=self._ctrl_select_var,
            values=["No controller found"],
            width=30,
            state="readonly",
        )
        self._ctrl_dropdown.pack(side="left", padx=(8, 8))
        self._ctrl_dropdown.bind("<<ComboboxSelected>>", self._on_ctrl_dropdown_select)
        btn_row = tk.Frame(c, bg=self.CARD)
        btn_row.pack(fill="x", pady=(4, 0))
        self._btn(btn_row, "Rescan controller", self._rescan).pack(side="left")
        # Populate dropdown on startup
        self.after(100, lambda: self._on_controller_count_changed(controller_count))

        c2 = self._card(
            root,
            "Emulation snapshot",
            "Values reflect the running app (deadzone file value and current pointer speed preset).",
        )
        self.lbl_emu_mode = self._info_row(c2, "Mode", "—")
        self.lbl_deadzone = self._info_row(c2, "Deadzone", "—")
        self.lbl_sensitivity = self._info_row(c2, "Pointer speed (1–20)", "—")

        c3 = self._card(
            root,
            "Session",
            "Turn emulation off to use the gamepad in games without closing this window.",
        )
        row = tk.Frame(c3, bg=self.CARD)
        row.pack(fill="x")
        self.btn_toggle = self._btn(row, "Emulation on", self._toggle_emulation, color=self.GREEN)
        self.btn_toggle.pack(side="left", padx=(0, 8))
        self._btn(row, "Quit application", self._quit, color=self.RED).pack(side="left")

    # ── Tab: Hotkeys ───────────────────────────────────────────────────────
    def _tab_hotkeys(self, nb):
        root = self._tab_frame(nb, "Hotkeys")

        c = self._card(
            root,
            "Actions",
            "Pick a button from the list, use Assign to capture the next press, or leave empty to disable. "
            "Assign uses the same mappings as the Buttons tab (calibrate there first if a face button is unknown).",
        )
        self.hk_vars = {}
        self._hk_assign_buttons.clear()
        for action in HOTKEY_ACTIONS:
            row = tk.Frame(c, bg=self.CARD)
            row.pack(fill="x", pady=4)
            tk.Label(
                row,
                text=HOTKEY_LABELS[action],
                font=self.FH,
                bg=self.CARD,
                fg=self.TEXT,
                anchor="w",
            ).pack(side="left", fill="x", expand=True)
            var = tk.StringVar(value=config["hotkeys"].get(action, ""))
            self.hk_vars[action] = var
            ab = self._btn(
                row,
                "Assign...",
                lambda a=action: self._start_hk_listen(a),
                secondary=True,
            )
            ab.pack(side="right", padx=(6, 6))
            self._hk_assign_buttons[action] = ab
            cb = ttk.Combobox(
                row,
                textvariable=var,
                values=[""] + ALL_BUTTON_LABELS,
                width=12,
                state="readonly",
            )
            cb.pack(side="right")

        save_row = tk.Frame(c, bg=self.CARD)
        save_row.pack(fill="x", pady=(16, 0))
        self._btn(save_row, "Save hotkeys", self._save_hotkeys).pack(side="left")

    # ── Tab: Buttons ───────────────────────────────────────────────────────
    def _tab_buttons(self, nb):
        root = self._tab_frame(nb, "Buttons")

        c = self._card(
            root,
            "Pygame button indices",
            "Each physical control maps to an index used by pygame. Calibration below fills these automatically; "
            "manual edits are for advanced setups.",
        )
        self.btn_vars = {}
        for cfg_key, idx in config.get("buttons", {}).items():
            row = tk.Frame(c, bg=self.CARD)
            row.pack(fill="x", pady=4)
            label = BUTTON_DISPLAY_NAMES.get(cfg_key, cfg_key.replace("_", " ").title())
            tk.Label(row, text=label, font=self.FH, bg=self.CARD, fg=self.TEXT, anchor="w").pack(
                side="left",
                fill="x",
                expand=True,
            )
            var = tk.StringVar(value=str(idx))
            self.btn_vars[cfg_key] = var
            tk.Entry(
                row,
                textvariable=var,
                width=8,
                bg=self.ELEVATED,
                fg=self.TEXT,
                insertbackground=self.TEXT,
                relief="flat",
                highlightthickness=1,
                highlightbackground=self.BORDER,
                highlightcolor=self.BORDER,
            ).pack(side="right", padx=(8, 0))

        c2 = self._card(
            root,
            "Calibration",
            "Walk through prompts in order. Emulation pauses until calibration finishes.",
        )
        tk.Label(
            c2,
            text="Press each listed control when asked; indices are written to the table above and saved.",
            font=self.FSM,
            bg=self.CARD,
            fg=self.MUTED,
            justify="left",
            anchor="w",
        ).pack(anchor="w", pady=(0, 8))
        self._btn(c2, "Start button walkthrough", self._start_button_cal).pack(anchor="w")
        self.lbl_cal_progress = tk.Label(c2, text="", font=self.FH, bg=self.CARD, fg=self.AMBER)
        self.lbl_cal_progress.pack(anchor="w", pady=(8, 0))

        tk.Label(
            c2,
            text="Deadzone: rest both sticks, keep hands off, then run once. This filters stick noise at rest.",
            font=self.FSM,
            bg=self.CARD,
            fg=self.MUTED,
            justify="left",
            anchor="w",
        ).pack(anchor="w", pady=(16, 8))
        self._btn(c2, "Measure deadzone from sticks", self._start_dz_cal).pack(anchor="w")
        self.lbl_dz_result = tk.Label(
            c2,
            text=f"Current deadzone: {config['advanced']['deadzone']:.4f}",
            font=self.FH,
            bg=self.CARD,
            fg=self.GREEN,
        )
        self.lbl_dz_result.pack(anchor="w", pady=(8, 0))

        self._btn(c2, "Save button map", self._save_buttons).pack(anchor="w", pady=(16, 0))

    # ── Tab: Mouse ─────────────────────────────────────────────────────────
    def _tab_mouse(self, nb):
        root = self._tab_frame(nb, "Mouse")

        c = self._card(
            root,
            "Movement",
            "Pointer and scroll gain on a 1–20 scale. 10 is the baseline feel; lower is slower, higher is faster.",
        )
        self.sens_var = tk.IntVar(value=config["mouse"]["sensitivity"])
        self.scrl_var = tk.IntVar(value=config["mouse"]["scroll_sensitivity"])
        self.accel_var = tk.BooleanVar(value=config["mouse"].get("use_acceleration", True))
        self.hscrl_var = tk.BooleanVar(value=config["advanced"].get("h_scroll", True))
        self.snip_var = tk.DoubleVar(value=config["advanced"].get("sniper_factor", 0.1))

        self._slider_row(c, "Pointer sensitivity (1–20)", self.sens_var, 1, 20)
        self._slider_row(c, "Scroll sensitivity (1–20)", self.scrl_var, 1, 20)

        c2 = self._card(
            root,
            "Behavior",
            "Optional tweaks for precision work and different scroll axes.",
        )
        self._option_with_hint(
            c2,
            "Dynamic acceleration (quadratic response)",
            self.accel_var,
            "When on, small stick moves are finer and large moves accelerate — natural for desktop use. "
            "Turn off for linear 1:1 movement (some users prefer this in strategy games or pixel work).",
        )
        self._option_with_hint(
            c2,
            "Horizontal scroll (right stick X)",
            self.hscrl_var,
            "Maps the right stick horizontal axis to sideways scrolling where the OS supports it.",
        )
        self._option_with_hint(
            c2,
            "Sniper factor (0.01–0.50)",
            self.snip_var,
            "While the sniper hotkey is held, pointer speed is multiplied by this value for aiming.",
            is_slider=True,
            slider_from=0.01,
            slider_to=0.5,
            slider_resolution=0.01,
        )

        self._btn(c2, "Save mouse settings", self._save_mouse).pack(anchor="w", pady=(12, 0))

    # ── Widget helpers ─────────────────────────────────────────────────────
    def _bind_canvas_wheel(self, canvas):
        def on_wheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def on_enter(_):
            canvas.bind_all("<MouseWheel>", on_wheel)

        def on_leave(_):
            canvas.unbind_all("<MouseWheel>")

        canvas.bind("<Enter>", on_enter)
        canvas.bind("<Leave>", on_leave)

    def _tab_frame(self, nb, label):
        shell = tk.Frame(nb, bg=self.BG)
        nb.add(shell, text=f"  {label}  ")
        canvas = tk.Canvas(
            shell,
            bg=self.PANEL,
            highlightthickness=0,
            bd=0,
        )
        vsb = ttk.Scrollbar(shell, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)

        outer = tk.Frame(canvas, bg=self.PANEL)
        win_id = canvas.create_window((0, 0), window=outer, anchor="nw")
        inner = tk.Frame(outer, bg=self.PANEL)
        inner.pack(fill="both", expand=True, padx=20, pady=16)

        def _toggle_vsb():
            canvas.update_idletasks()
            br = canvas.bbox("all")
            if not br:
                return
            content_h = br[3] - br[1]
            view_h = max(canvas.winfo_height(), 1)
            if content_h <= view_h + 1:
                vsb.pack_forget()
            elif not vsb.winfo_ismapped():
                vsb.pack(side="right", fill="y")

        def _on_inner_configure(_):
            canvas.configure(scrollregion=canvas.bbox("all"))
            _toggle_vsb()

        def _on_canvas_configure(event):
            if event.width > 1:
                canvas.itemconfigure(win_id, width=event.width)
            canvas.configure(scrollregion=canvas.bbox("all"))
            _toggle_vsb()

        inner.bind("<Configure>", _on_inner_configure)
        canvas.bind("<Configure>", _on_canvas_configure)
        self._bind_canvas_wheel(canvas)

        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        return inner

    def _card(self, parent, title, blurb=None):
        """Bordered content block for clear visual grouping."""
        shell = tk.Frame(parent, bg=self.BORDER)
        shell.pack(fill="x", pady=(0, 12))
        inner = tk.Frame(shell, bg=self.CARD)
        inner.pack(fill="x", padx=1, pady=1)
        head = tk.Frame(inner, bg=self.CARD)
        head.pack(fill="x", padx=16, pady=(14, 8))
        tk.Label(head, text=title, font=self.FB, bg=self.CARD, fg=self.TEXT, anchor="w").pack(anchor="w")
        if blurb:
            hint = tk.Label(
                head,
                text=blurb,
                font=self.FSM,
                bg=self.CARD,
                fg=self.MUTED,
                anchor="w",
                justify="left",
            )
            hint.pack(anchor="w", pady=(6, 0))

            def _wrap_hint(event):
                hint.configure(wraplength=max(260, event.width - 8))

            head.bind("<Configure>", _wrap_hint)
        body = tk.Frame(inner, bg=self.CARD)
        body.pack(fill="x", padx=16, pady=(0, 16))
        return body

    def _info_row(self, parent, label, value):
        bg = parent.cget("bg")
        row = tk.Frame(parent, bg=bg)
        row.pack(fill="x", pady=2)
        tk.Label(row, text=label, font=self.FH, bg=bg, fg=self.MUTED, width=20, anchor="w").pack(side="left")
        lbl = tk.Label(row, text=value, font=self.FB, bg=bg, fg=self.TEXT, anchor="w")
        lbl.pack(side="left")
        return lbl

    def _option_with_hint(
        self,
        parent,
        title,
        var,
        hint,
        *,
        is_slider=False,
        slider_from=0.01,
        slider_to=0.5,
        slider_resolution=0.01,
    ):
        bg = parent.cget("bg")
        if is_slider:
            top = tk.Frame(parent, bg=bg)
            top.pack(fill="x", pady=(10, 0))
            tk.Label(top, text=title, font=self.FB, bg=bg, fg=self.TEXT, anchor="w").pack(side="left")
            val_lbl = tk.Label(top, font=self.FH, bg=bg, fg=self.MUTED, anchor="e")
            val_lbl.pack(side="right")

            def sync(*_):
                val_lbl.config(text=f"{float(var.get()):.2f}")

            var.trace_add("write", lambda *_: sync())
            sync()
            tk.Label(
                parent,
                text=hint,
                font=self.FSM,
                bg=bg,
                fg=self.MUTED,
                justify="left",
                anchor="w",
                wraplength=520,
            ).pack(anchor="w", pady=(4, 6))
            tk.Scale(
                parent,
                variable=var,
                from_=slider_from,
                to=slider_to,
                resolution=slider_resolution,
                orient="horizontal",
                command=lambda *_: sync(),
                showvalue=0,
                bg=bg,
                fg=self.TEXT,
                troughcolor=self.ELEVATED,
                highlightthickness=0,
                sliderrelief="flat",
                bd=0,
            ).pack(fill="x", pady=(0, 4))
            return
        tk.Checkbutton(
            parent,
            text=title,
            variable=var,
            font=self.FH,
            bg=bg,
            fg=self.TEXT,
            selectcolor=self.ELEVATED,
            activebackground=bg,
            activeforeground=self.TEXT,
            anchor="w",
            takefocus=0,
            highlightthickness=0,
            bd=0,
        ).pack(anchor="w", pady=(8, 0))
        tk.Label(
            parent,
            text=hint,
            font=self.FSM,
            bg=bg,
            fg=self.MUTED,
            justify="left",
            anchor="w",
            wraplength=520,
        ).pack(anchor="w", padx=(22, 0), pady=(2, 10))

    def _btn(self, parent, text, cmd, color=None, secondary=False):
        if secondary:
            b = tk.Button(
                parent,
                text=text,
                command=cmd,
                font=self.FH,
                bg=self.ELEVATED,
                fg=self.TEXT,
                activebackground=self.BORDER,
                activeforeground=self.TEXT,
                relief="flat",
                padx=14,
                pady=6,
                cursor="hand2",
                highlightthickness=1,
                highlightbackground=self.BORDER,
                highlightcolor=self.BORDER,
                takefocus=0,
            )
            return b
        c = color or self.ACCENT
        b = tk.Button(
            parent,
            text=text,
            command=cmd,
            font=self.FH,
            bg=c,
            fg="white",
            activebackground=self.TAB_SEL,
            activeforeground="white",
            relief="flat",
            padx=14,
            pady=6,
            cursor="hand2",
            highlightthickness=0,
            takefocus=0,
        )
        return b

    def _slider_row(self, parent, label, var, frm, to):
        bg = parent.cget("bg")
        row = tk.Frame(parent, bg=bg)
        row.pack(fill="x", pady=(6, 0))
        tk.Label(row, text=label, font=self.FH, bg=bg, fg=self.TEXT, anchor="w").pack(side="left")
        val = tk.Label(row, font=self.FH, bg=bg, fg=self.MUTED, anchor="e")
        val.pack(side="right")

        def sync(*_):
            val.config(text=str(var.get()))

        var.trace_add("write", lambda *_: sync())
        sync()
        tk.Scale(
            parent,
            variable=var,
            from_=frm,
            to=to,
            orient="horizontal",
            command=lambda *_: sync(),
            showvalue=0,
            bg=bg,
            fg=self.TEXT,
            troughcolor=self.ELEVATED,
            highlightthickness=0,
            sliderrelief="flat",
            bd=0,
            resolution=1,
        ).pack(fill="x", pady=(0, 8))

    # ── Hotkey capture (press-to-assign) ───────────────────────────────────
    def _on_escape(self, event=None):
        if self._hk_listen_action:
            self._stop_hk_listen(cancelled=True)
        return "break"

    def _start_hk_listen(self, action):
        global emulation_mode, input_capture_active
        if self._hk_listen_action:
            self._stop_hk_listen(cancelled=True)
        if controller is None:
            self._notify("warn", "Connect a controller before assigning buttons.")
            return
        self._hk_listen_prev_emu = emulation_mode
        emulation_mode = False
        input_capture_active = True
        self._hk_listen_action = action
        for a, btn in self._hk_assign_buttons.items():
            if a == action:
                btn.config(text="Listening...", state="disabled")
            else:
                btn.config(state="disabled")
        self._notify(
            "ok",
            f"Press a mapped button or D-pad for '{HOTKEY_LABELS[action]}'. Esc cancels.",
        )
        self.after(40, self._poll_hk_listen)

    def _stop_hk_listen(self, cancelled=False):
        global emulation_mode, input_capture_active
        self._hk_listen_action = None
        input_capture_active = False
        emulation_mode = self._hk_listen_prev_emu
        for btn in self._hk_assign_buttons.values():
            btn.config(text="Assign...", state="normal")
        if cancelled:
            self._notify("warn", "Assignment cancelled.")

    def _poll_hk_listen(self):
        action = self._hk_listen_action
        if not action:
            return
        pygame.event.pump()
        if not controller:
            self._stop_hk_listen()
            self._notify("err", "Controller disconnected during assignment.")
            return
        try:
            hat = controller.get_hat(0)
            hlab = _hotkey_label_for_hat(hat)
            if hlab:
                self.hk_vars[action].set(hlab)
                self._stop_hk_listen()
                self._notify("ok", f"Assigned '{HOTKEY_LABELS[action]}' -> {hlab}")
                return
        except Exception:
            pass
        for i in range(controller.get_numbuttons()):
            if controller.get_button(i):
                lab = _hotkey_label_for_button_index(i)
                if lab:
                    self.hk_vars[action].set(lab)
                    self._stop_hk_listen()
                    self._notify("ok", f"Assigned '{HOTKEY_LABELS[action]}' -> {lab}")
                    return
                self._stop_hk_listen()
                self._notify(
                    "warn",
                    "This button index is not mapped yet. Run button calibration on the Buttons tab first.",
                )
                return
        self.after(50, self._poll_hk_listen)

    # ── Actions ────────────────────────────────────────────────────────────
    def _toggle_emulation(self):
        global emulation_mode
        emulation_mode = not emulation_mode
        self._refresh_status()

    def _on_controller_count_changed(self, count):
        """Called by the background auto-scan thread when controller count changes."""
        try:
            if count == 0:
                self._ctrl_dropdown.config(values=["No controller found"], state="disabled")
                self._ctrl_select_var.set("No controller found")
            else:
                names = []
                for i in range(count):
                    try:
                        j = pygame.joystick.Joystick(i)
                        j.init()
                        names.append(f"[{i}] {j.get_name()}")
                    except Exception:
                        names.append(f"[{i}] Controller {i}")
                self._ctrl_dropdown.config(values=names, state="readonly")
                # Keep selection if still valid, else default to first
                idx = min(selected_controller_index, count - 1)
                self._ctrl_select_var.set(names[idx])
            self._refresh_status()
        except Exception:
            pass

    def _on_ctrl_dropdown_select(self, _event=None):
        """User picked a different controller from the dropdown."""
        val = self._ctrl_select_var.get()
        try:
            idx = int(val.split("]")[0].replace("[", "").strip())
        except Exception:
            idx = 0
        select_controller(idx)
        self._refresh_status()
        self._notify("ok", f"Switched to: {val}")

    def _rescan(self):
        ok = rescan_controller()
        self._on_controller_count_changed(controller_count)
        self._refresh_status()
        if not ok:
            self._notify(
                "warn",
                "No controller found. Plug in a device — it will be detected automatically.",
            )

    def _quit(self):
        pygame.quit()
        os.kill(os.getpid(), 9)

    def _save_hotkeys(self):
        for action, var in self.hk_vars.items():
            config["hotkeys"][action] = var.get()
        r = save_config()
        if r is True:
            self._notify("ok", "Hotkeys saved.")
        else:
            self._notify("err", f"Could not save: {r}")

    def _save_buttons(self):
        for cfg_key, var in self.btn_vars.items():
            try:
                config["buttons"][cfg_key] = int(var.get())
            except ValueError:
                pass
        r = save_config()
        if r is True:
            self._notify("ok", "Button map saved.")
        else:
            self._notify("err", f"Could not save: {r}")

    def _save_mouse(self):
        # Save UI values
        config["mouse"]["sensitivity"]        = max(1, min(20, int(self.sens_var.get())))
        config["mouse"]["scroll_sensitivity"] = max(1, min(20, int(self.scrl_var.get())))
        config["mouse"]["use_acceleration"]   = self.accel_var.get()
        config["advanced"]["h_scroll"]        = self.hscrl_var.get()
        config["advanced"]["sniper_factor"]   = round(self.snip_var.get(), 2)
        r = save_config()
        if r is True:
            self._notify("ok", "Mouse settings saved.")
        else:
            self._notify("err", f"Could not save: {r}")

    def _start_button_cal(self):
        global emulation_mode, input_capture_active
        if controller is None:
            self._notify("warn", "Connect a controller before running calibration.")
            return
        self._cal_prev_emu = emulation_mode
        emulation_mode = False
        input_capture_active = True
        button_names = list(config["buttons"].keys())
        self._cal_idx = 0
        self._cal_names = button_names
        self.lbl_cal_progress.config(
            text=f"[1/{len(button_names)}]  Press:  {BUTTON_DISPLAY_NAMES.get(button_names[0], button_names[0])}",
        )
        self._poll_cal()

    def _poll_cal(self):
        global emulation_mode, input_capture_active
        if self._cal_idx >= len(self._cal_names):
            save_config()
            self.lbl_cal_progress.config(
                text="Calibration finished — saved.",
                fg=self.GREEN,
            )
            input_capture_active = False
            emulation_mode = self._cal_prev_emu
            return

        cfg_key = self._cal_names[self._cal_idx]
        pygame.event.pump()
        for i in range(controller.get_numbuttons() if controller else 0):
            if controller.get_button(i):
                config["buttons"][cfg_key] = i
                self._cal_idx += 1
                nxt = self._cal_names[self._cal_idx] if self._cal_idx < len(self._cal_names) else None
                if nxt:
                    self.lbl_cal_progress.config(
                        text=f"[{self._cal_idx + 1}/{len(self._cal_names)}]  Press:  {BUTTON_DISPLAY_NAMES.get(nxt, nxt)}",
                    )
                self.after(300, self._poll_cal)
                return
        self.after(50, self._poll_cal)

    def _start_dz_cal(self):
        def cb(state, msg):
            if state == "running":
                self.lbl_dz_result.config(text=msg, fg=self.AMBER)
            elif state == "done":
                self.lbl_dz_result.config(
                    text=f"Current deadzone: {config['advanced']['deadzone']:.4f}",
                    fg=self.GREEN,
                )
            elif state is None:
                self.lbl_dz_result.config(text=msg, fg=self.RED)
        calibrate_deadzone(callback=cb)

    def _notify(self, kind, message, duration=7000):
        colors = {"ok": self.GREEN, "warn": self.AMBER, "err": self.RED}
        fg = colors.get(kind, self.MUTED)
        self.notify_lbl.config(text=message, fg=fg)
        if self._notify_after_id is not None:
            try:
                self.after_cancel(self._notify_after_id)
            except Exception:
                pass

        def clear():
            self.notify_lbl.config(text="", fg=self.MUTED)
            self._notify_after_id = None

        self._notify_after_id = self.after(duration, clear)

    # ── Status polling / refresh ───────────────────────────────────────────
    def _refresh_status(self):
        # Controller info
        if controller:
            try:
                name = controller.get_name()
                axes = controller.get_numaxes()
                btns = controller.get_numbuttons()
            except Exception:
                name, axes, btns = "—", "—", "—"
            self.lbl_ctrl_name.config(text=name)
            self.lbl_ctrl_axes.config(text=str(axes))
            self.lbl_ctrl_btns.config(text=str(btns))
        else:
            self.lbl_ctrl_name.config(text="No controller")
            self.lbl_ctrl_axes.config(text="—")
            self.lbl_ctrl_btns.config(text="—")

        # Emulation status
        self.lbl_emu_mode.config(text="On" if emulation_mode else "Off")
        self.lbl_deadzone.config(text=f"{config['advanced'].get('deadzone', calibrated_deadzone):.4f}")
        self.lbl_sensitivity.config(text=str(config["mouse"].get("sensitivity", 10)))

        if emulation_mode:
            self.lbl_status.config(
                text="Emulation on",
                bg=self.ACCENT_DIM,
                fg=self.GREEN,
            )
        else:
            self.lbl_status.config(
                text="Emulation off",
                bg=self.ELEVATED,
                fg=self.MUTED,
            )

        self.btn_toggle.config(
            text="Turn emulation off" if emulation_mode else "Turn emulation on",
            bg=self.GREEN if emulation_mode else self.ACCENT,
        )

    def _poll_status(self):
        self._refresh_status()
        self.after(500, self._poll_status)

# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    load_config()
    init_controller()
    app = App()
    # Start background main loop
    t = threading.Thread(target=main_loop, daemon=True)
    t.start()
    # Start background controller auto-scan
    t_scan = threading.Thread(target=_background_auto_scan, daemon=True)
    t_scan.start()
    app.mainloop()