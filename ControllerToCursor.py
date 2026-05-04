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
    "toggle_emulation",
    "left_mouse", "right_mouse", "quit",
    "speech_to_text", "on_screen_keyboard",
    "mouse_speed_boost", "sniper_mode",
]

HOTKEY_LABELS = {
    "toggle_emulation":   "Toggle emulation on/off",
    "left_mouse":         "Left mouse button",
    "right_mouse":        "Right mouse button",
    "quit":               "Quit application",
    "speech_to_text":     "Speech to text",
    "on_screen_keyboard": "On-screen keyboard",
    "mouse_speed_boost":  "Pointer speed boost",
    "sniper_mode":        "Sniper mode (slow pointer)",
}

HOTKEY_HINT = (
    "One binding: that control alone triggers the action. "
    "Several bindings: all of them must be held together (chord). "
    "Use multiple rows only when you want a chord."
)

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


def _normalize_hotkey_list(value):
    """Turn legacy string or list config values into a clean list of binding labels."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    s = str(value).strip()
    return [s] if s else []


def _dpad_button_index(direction_label):
    """Legacy: pygame button index for D-pad direction, or None for hat/default."""
    d = config.get("dpad", {})
    if direction_label not in d:
        return None
    try:
        v = int(d[direction_label])
    except (TypeError, ValueError):
        return None
    if v < 0:
        return None
    return v


def _binding_physical_down(button_name):
    """True if the physical control for a label (face button, D-pad, …) is pressed."""
    if controller is None:
        return False
    if button_name in _BUTTON_MAP:
        bind = config.get("dpad_bind", {}).get(button_name)
        if isinstance(bind, dict):
            t = bind.get("type")
            if t == "hat":
                try:
                    hx, hy = int(bind["x"]), int(bind["y"])
                    return controller.get_hat(0) == (hx, hy)
                except Exception:
                    return False
            if t == "btn":
                try:
                    return bool(controller.get_button(int(bind["btn"])))
                except Exception:
                    return False
        bi = _dpad_button_index(button_name)
        if bi is not None:
            try:
                return bool(controller.get_button(bi))
            except Exception:
                return False
        try:
            return controller.get_hat(0) == _BUTTON_MAP[button_name]
        except Exception:
            return False
    if button_name in _CONFIG_BUTTON_KEYS:
        idx = config.get("buttons", {}).get(_CONFIG_BUTTON_KEYS[button_name], 100)
        if idx == 100:
            return False
        try:
            return bool(controller.get_button(int(idx)))
        except Exception:
            return False
    return False


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
            "setup": {"welcome_dismissed": False},
            "hotkeys": {
                "toggle_emulation": ["DP-U", "Y"],
                "left_mouse": ["A"],
                "right_mouse": ["B"],
                "quit": ["DP-D"],
                "speech_to_text": ["DP-L"],
                "on_screen_keyboard": ["DP-R"],
                "mouse_speed_boost": ["LS"],
                "sniper_mode": ["LB"],
            },
            "mouse": {
                "sensitivity": 10,
                "scroll_sensitivity": 10,
                "use_acceleration": True,
                "speed_boost_factor": 2.0,
            },
            "dpad": {},
            "dpad_bind": {},
            "axes": {
                "move_x": 0,
                "move_y": 1,
                "scroll_vertical": 3,
                "scroll_horizontal": 2,
            },
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
    config.setdefault("setup", {})
    config.setdefault("mouse", {})
    config.setdefault("hotkeys", {})
    config.setdefault("dpad", {})
    config.setdefault("dpad_bind", {})
    config.setdefault("axes", {})
    config.setdefault("buttons", {})
    config.setdefault("advanced", {})
    config["setup"].setdefault("welcome_dismissed", False)
    config["mouse"].setdefault("sensitivity", 10)
    config["mouse"].setdefault("scroll_sensitivity", 10)
    config["mouse"].setdefault("use_acceleration", True)
    config["mouse"].setdefault("speed_boost_factor", 2.0)
    config["axes"].setdefault("move_x", 0)
    config["axes"].setdefault("move_y", 1)
    config["axes"].setdefault("scroll_vertical", 3)
    config["axes"].setdefault("scroll_horizontal", 2)
    config["advanced"].setdefault("deadzone", 0.01)
    config["advanced"].setdefault("sniper_factor", 0.25)
    config["advanced"].setdefault("h_scroll", True)
    db = config.setdefault("dpad_bind", {})
    for lbl in list(_BUTTON_MAP.keys()):
        if lbl in db:
            continue
        legacy = config.get("dpad", {}).get(lbl)
        if legacy is None:
            continue
        try:
            iv = int(legacy)
        except (TypeError, ValueError):
            continue
        hx, hy = _BUTTON_MAP[lbl]
        if iv >= 0:
            db[lbl] = {"type": "btn", "btn": iv}
        elif iv == -1:
            db[lbl] = {"type": "hat", "x": hx, "y": hy}
    merged_toggle = []
    for i in range(1, 5):
        legacy = f"toggle_hotkey_{i}"
        if legacy in config["hotkeys"]:
            for b in _normalize_hotkey_list(config["hotkeys"].pop(legacy, [])):
                if b and b not in merged_toggle:
                    merged_toggle.append(b)
    te = _normalize_hotkey_list(config["hotkeys"].get("toggle_emulation", []))
    if not te:
        config["hotkeys"]["toggle_emulation"] = merged_toggle if merged_toggle else ["DP-U", "Y"]
    else:
        config["hotkeys"]["toggle_emulation"] = te
    for a in HOTKEY_ACTIONS:
        if a == "toggle_emulation":
            continue
        if a not in config["hotkeys"]:
            config["hotkeys"][a] = []
        else:
            config["hotkeys"][a] = _normalize_hotkey_list(config["hotkeys"][a])

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
def is_pressed(action_name):
    global last_frame_ts, frame_cache, button_memory
    ticks = pygame.time.get_ticks()
    if ticks != last_frame_ts:
        last_frame_ts = ticks
        frame_cache = {}
    if action_name in frame_cache:
        return frame_cache[action_name]

    bindings = _normalize_hotkey_list(config.get("hotkeys", {}).get(action_name, []))
    if not bindings or controller is None:
        frame_cache[action_name] = False
        button_memory[action_name] = False
        return False

    all_down = all(_binding_physical_down(bn) for bn in bindings)

    was_down = button_memory.get(action_name, False)
    if all_down and not was_down:
        result = "just_pressed"
    elif not all_down and was_down:
        result = "just_released"
    elif all_down:
        result = "is_held"
    else:
        result = False

    frame_cache[action_name] = result
    button_memory[action_name] = all_down
    return result

# ─────────────────────────────────────────────────────────────────────────────
# Deadzone calibration
# ─────────────────────────────────────────────────────────────────────────────
def calibrate_deadzone(samples=60, callback=None):
    """Measure stick-at-rest noise and set deadzone. Runs in background thread."""
    def _run():
        global calibrated_deadzone
        with _controller_lock:
            joy = controller
        if joy is None:
            if callback:
                callback(None, "No controller connected.")
            return
        ax_cfg = config.get("axes", {})
        imx = int(ax_cfg.get("move_x", 0))
        imy = int(ax_cfg.get("move_y", 1))
        if callback:
            callback("running", f"Hold sticks still — sampling {samples} frames.")
        max_noise = 0.0
        for _ in range(samples):
            pygame.event.pump()
            try:
                with _controller_lock:
                    joy = controller
                if not joy:
                    break
                n = joy.get_numaxes()
                xi = imx if 0 <= imx < n else 0
                yi = imy if 0 <= imy < n else min(1, max(0, n - 1))
                x = abs(joy.get_axis(xi))
                y = abs(joy.get_axis(yi))
                max_noise = max(max_noise, x, y)
            except Exception:
                pass
            time.sleep(1 / 60)
        dz = min(max_noise * 1.3 + 0.01, 0.35)
        calibrated_deadzone = dz
        config["advanced"]["deadzone"] = round(dz, 4)
        save_config()
        if callback:
            callback("done", f"Deadzone set to {dz:.4f}")
    threading.Thread(target=_run, daemon=True).start()


def _resolve_pointer_scroll_axes(n_ax, ax_move_x, ax_move_y, ax_scroll_v, ax_scroll_h, h_scroll_on):
    """
    Pick pygame axis indices so pointer X/Y never share the same analog channel and
    scroll never reuses a pointer axis. Misconfigured configs (e.g. everything set
    to a trigger index) otherwise produce constant diagonal drift and phantom scroll.
    """
    if n_ax <= 0:
        return 0, 0, True, None, None

    imx = ax_move_x if 0 <= ax_move_x < n_ax else 0
    imy = ax_move_y if 0 <= ax_move_y < n_ax else min(1, max(0, n_ax - 1))
    y_axis_disabled = False
    if imx == imy:
        if n_ax > 1:
            imy = (imx + 1) % n_ax
        else:
            y_axis_disabled = True

    sv = ax_scroll_v if 0 <= ax_scroll_v < n_ax else None
    if sv is not None and sv in (imx, imy):
        sv = None

    sh = ax_scroll_h if 0 <= ax_scroll_h < n_ax else None
    if not h_scroll_on:
        sh = None
    elif sh is not None and (sh in (imx, imy) or sh == sv):
        sh = None

    if sv is not None and sh is not None and sv == sh:
        sh = None

    return imx, imy, y_axis_disabled, sv, sh


# ─────────────────────────────────────────────────────────────────────────────
# Core emulation
# ─────────────────────────────────────────────────────────────────────────────
def toggle_emulation_mode():
    global emulation_mode
    if input_capture_active:
        return
    if _normalize_hotkey_list(config.get("hotkeys", {}).get("toggle_emulation", [])):
        st = is_pressed("toggle_emulation")
        if st == "just_pressed":
            emulation_mode = not emulation_mode

    if (is_pressed("quit") == "just_pressed") and emulation_mode == True:
        pygame.quit()
        os.kill(os.getpid(), 9)

def emulate_mouse():
    global sniper_mode, mouse_residual_x, mouse_residual_y
    if controller is None or not config:
        return
    if input_capture_active:
        return

    ui_sens      = float(config["mouse"].get("sensitivity", 10))
    ui_scroll    = float(config["mouse"].get("scroll_sensitivity", 10))
    use_accel    = bool(config["mouse"].get("use_acceleration", True))
    threshold    = float(config["advanced"].get("deadzone", calibrated_deadzone))
    sniper_fac   = float(config["advanced"].get("sniper_factor", 0.1))
    sniper_fac   = max(0.001, min(25.0, sniper_fac))
    h_scroll_on = bool(config["advanced"].get("h_scroll", True))
    ax = config.get("axes", {})
    ax_move_x = int(ax.get("move_x", 0))
    ax_move_y = int(ax.get("move_y", 1))
    ax_scroll_v = int(ax.get("scroll_vertical", 3))
    ax_scroll_h = int(ax.get("scroll_horizontal", 2))

    sens = (ui_sens / 10.0)
    scroll_sens = (ui_scroll / 3.0)
    boost_mult = float(config["mouse"].get("speed_boost_factor", 2.0))
    boost_mult = max(0.25, min(60.0, boost_mult))

    if is_pressed("mouse_speed_boost") in ("is_held", "just_pressed"):
        sens *= boost_mult

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
            app._notify("warn", "Speech to text shortcut is not available on Linux.")
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
                app._notify("err", "FreeVK.exe not found. Get it from: https://freevirtualkeyboard.com/")

    try:
        n_ax = controller.get_numaxes()
    except Exception:
        n_ax = 0

    imx, imy, y_axis_disabled, scroll_v_axis, scroll_h_axis = _resolve_pointer_scroll_axes(
        n_ax, ax_move_x, ax_move_y, ax_scroll_v, ax_scroll_h, h_scroll_on
    )

    # Mouse movement — configurable axes (default left stick)
    if n_ax > 0:
        try:
            rx = controller.get_axis(imx)
            ry = 0.0 if y_axis_disabled else controller.get_axis(imy)
            x = rx if abs(rx) > threshold else 0.0
            y = ry if abs(ry) > threshold else 0.0
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

    def handle_scroll(axis_value, last_time_attr, is_vertical):
        boost_app = boost_mult if is_pressed("mouse_speed_boost") in ("is_held", "just_pressed") else 1.0

        if sys.platform.startswith("linux"):
            if abs(axis_value) < threshold:
                return
            mag = min(1.0, (abs(axis_value) - threshold) / max(1e-6, 1.0 - threshold))
            sens_part = max(0.4, scroll_sens / 5.0)
            interval = 0.28 / (mag * sens_part * boost_app + 0.15)
            interval = max(0.1, min(0.5, interval))
            now = time.time()
            if now - getattr(app, last_time_attr, 0) >= interval:
                direction = -1 if axis_value > 0 else 1
                if is_vertical:
                    pyautogui.scroll(direction)
                else:
                    pyautogui.hscroll(direction)
                setattr(app, last_time_attr, now)
        else:
            if abs(axis_value) < threshold:
                return
            amount = int(axis_value * scroll_sens * 5 * boost_app)
            if amount != 0:
                if is_vertical:
                    pyautogui.scroll(-amount)
                else:
                    pyautogui.hscroll(amount)


    # --- Vertical scroll ---
    try:
        if scroll_v_axis is not None:
            s = controller.get_axis(scroll_v_axis)
            handle_scroll(s, "last_scroll_time_v", True)
    except Exception:
        pass

    # --- Horizontal scroll ---
    try:
        if scroll_h_axis is not None:
            h = controller.get_axis(scroll_h_axis)
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

        """
        self.update_idletasks()
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        try:
            dpi = float(self.winfo_fpixels("1i"))
            self._ui_scale = max(0.88, min(1.45, dpi / 96.0))
        except Exception:
            self._ui_scale = 1.0
        if screen_w >= 3840:
            self._ui_scale = min(self._ui_scale, 1.05)
        elif screen_w >= 3000:
            self._ui_scale = min(self._ui_scale, 1.1)
        elif screen_w >= 2560:
            self._ui_scale = min(self._ui_scale, 1.18)

        bs = max(8, int(round(9 * self._ui_scale)))
        self.FH = ("Segoe UI", bs)
        self.FB = ("Segoe UI", bs, "bold")
        self.FT = ("Segoe UI", max(11, bs + 4), "bold")
        self.FSM = ("Segoe UI", max(8, bs - 1))

        w = max(480, min(760, int(screen_w * 0.22)))
        h = max(520, min(900, int(screen_h * 0.62)))
        self.minsize(400, 440)
        self.geometry(f"{w}x{h}")
        """

        # Automaticalley adjust to different screensizes

        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()

        try:
            dpi = float(self.winfo_fpixels("1i"))
            self._ui_scale = max(0.88, min(1.45, dpi / 96.0))
        except Exception:
            self._ui_scale = 1.5

        if screen_w >= 3840:
            width, height = 888, 1379
            self._ui_scale = min(self._ui_scale, 1.05)
        elif screen_w >= 2560:
            width, height = 700, 1100
            self._ui_scale = min(self._ui_scale, 1.1)
        elif screen_w >= 1920:
            width, height = 800, 900
            self._ui_scale = min(self._ui_scale, 0.8)

        else:
            width, height = 800, 900

        self.minsize(width, height)
        self.geometry(f"{width}x{height}")

        bs = max(8, int(round(9 * self._ui_scale)))
        self.FH = ("Segoe UI", bs)
        self.FB = ("Segoe UI", bs, "bold")
        self.FT = ("Segoe UI", max(11, bs + 4), "bold")
        self.FSM = ("Segoe UI", max(8, bs - 1))

        self.BG = "#1a1b1e"
        self.PANEL = "#222326"
        self.CARD = "#2a2b30"
        self.ELEVATED = "#35363d"
        self.BORDER = "#3f4049"
        self.ACCENT = "#4c8dff"
        self.ACCENT_HOVER = "#6ca8ff"
        self.TAB_SEL = "#323542"
        self.TEXT = "#ececf1"
        self.MUTED = "#9b9baa"
        self.GREEN = "#5bd085"
        self.AMBER = "#e9b949"
        self.RED = "#f07178"
        self.SCALE_TROUGH = "#3a3b44"
        self.SLIDER_THUMB = "#ffffff"
        self.ENTRY_BG = "#313238"

        self.configure(bg=self.BG)
        self._notify_after_id = None
        self._hk_listen_action = None
        self._hk_listen_target_var = None
        self._hk_listen_prev_emu = True
        self.hk_bind_rows = {}
        self._hk_trailing_add = {}
        self._hk_hosts = {}
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
            padding=[14, 9],
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
            troughcolor=self.BG,
            borderwidth=0,
            arrowcolor=self.MUTED,
        )
        style.map("TScrollbar", background=[("active", self.BORDER)])

        style.configure(
            "TCombobox",
            fieldbackground=self.ENTRY_BG,
            background=self.BORDER,
            foreground=self.TEXT,
            arrowcolor=self.ACCENT,
            borderwidth=1,
            relief="flat",
            padding=(8, 4),
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", self.ENTRY_BG), ("focus", self.CARD)],
            selectbackground=[("readonly", self.ENTRY_BG)],
            selectforeground=[("readonly", self.TEXT)],
            background=[("active", self.ELEVATED)],
        )

        self.option_add("*TCombobox*Listbox.background", self.CARD)
        self.option_add("*TCombobox*Listbox.foreground", self.TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", self.ACCENT)
        self.option_add("*TCombobox*Listbox.selectForeground", self.TEXT)
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
            text="Gamepad → mouse & shortcuts. Controller-friendly layout: set up Mapping first, then Actions.",
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
            bg=self.ELEVATED,
            fg=self.MUTED,
            padx=14,
            pady=8,
        )
        self.lbl_status.pack()

        body = tk.Frame(self, bg=self.BG)
        body.pack(fill="both", expand=True, padx=16, pady=(12, 0))

        nb = ttk.Notebook(body)
        nb.pack(fill="both", expand=True)

        self._tab_status(nb)
        self._tab_mapping(nb)
        self._tab_hotkeys(nb)
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

    # ── Tab: Home ──────────────────────────────────────────────────────────
    def _tab_status(self, nb):
        root = self._tab_frame(nb, "Home")

        self._welcome_shell = tk.Frame(root, bg=self.BORDER)
        if not config.get("setup", {}).get("welcome_dismissed", False):
            self._welcome_shell.pack(fill="x", pady=(0, 12))
        inner_w = tk.Frame(self._welcome_shell, bg=self.TAB_SEL, highlightthickness=0)
        inner_w.pack(fill="x", padx=1, pady=1)
        tk.Label(
            inner_w,
            text="Quick start",
            font=self.FB,
            bg=self.TAB_SEL,
            fg=self.TEXT,
            anchor="w",
        ).pack(anchor="w", padx=14, pady=(12, 4))
        tk.Label(
            inner_w,
            text=(
                "Defaults match many Xbox-style pads. Go to Mapping → 'Start button walkthrough' to set your controller mappings."
                " Set stick axes in Mapping if needed. "
                "Then tune Actions and Pointer settings for your liking. Refer to the README for further explenation."
            ),
            font=self.FSM,
            bg=self.TAB_SEL,
            fg=self.MUTED,
            anchor="w",
            justify="left",
            wraplength=560,
        ).pack(anchor="w", padx=14, pady=(0, 8))
        # Beispiel: Button in Grün
        self._btn(inner_w, "Got it", self._dismiss_welcome, color=self.ACCENT).pack(anchor="w", padx=14, pady=(0, 12))

        c = self._card(
            root,
            "Device",
            "Connected gamepad from the OS driver. Devices are detected when plugged in.",
        )
        self.lbl_ctrl_name = self._info_row(c, "Name", "—")
        self.lbl_ctrl_axes = self._info_row(c, "Axes", "—")
        self.lbl_ctrl_btns = self._info_row(c, "Buttons", "—")

        sel_row = tk.Frame(c, bg=self.CARD)
        sel_row.pack(fill="x", pady=(10, 0))
        tk.Label(sel_row, text="Active controller", font=self.FSM, bg=self.CARD, fg=self.MUTED).pack(
            side="left", padx=(0, 12)
        )
        combo_shell = tk.Frame(sel_row, bg=self.BORDER, padx=1, pady=1)
        combo_shell.pack(side="left", fill="x", expand=True, padx=(0, 20))
        inner_combo = tk.Frame(combo_shell, bg=self.CARD)
        inner_combo.pack(fill="both", expand=True)
        self._ctrl_select_var = tk.StringVar(value="Auto")
        self._ctrl_dropdown = ttk.Combobox(
            inner_combo,
            textvariable=self._ctrl_select_var,
            values=["No controller found"],
            width=28,
            state="readonly",
        )
        self._ctrl_dropdown.pack(fill="x", padx=6, pady=5)
        self._ctrl_dropdown.bind("<<ComboboxSelected>>", self._on_ctrl_dropdown_select)
        self._btn(sel_row, "Rescan", self._rescan).pack(side="left")
        # Populate dropdown on startup
        self.after(100, lambda: self._on_controller_count_changed(controller_count))

        c2 = self._card(
            root,
            "Live status",
            "Current emulation state and saved pointer deadzone / speed.",
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

    def _dismiss_welcome(self):
        config.setdefault("setup", {})["welcome_dismissed"] = True
        r = save_config()
        try:
            self._welcome_shell.pack_forget()
        except Exception:
            pass
        if r is True:
            self._notify("ok", "Welcome panel dismissed.")
        else:
            self._notify("warn", "Could not save welcome flag; panel hidden for this session.")

    def _add_hotkey_binding_row(self, action, host, value):
        prev_add = self._hk_trailing_add.get(action)
        if prev_add is not None:
            try:
                prev_add.pack_forget()
            except tk.TclError:
                pass

        row = tk.Frame(host, bg=self.CARD)
        row.pack(fill="x", pady=2)
        var = tk.StringVar(value=value)
        ttk.Combobox(
            row,
            textvariable=var,
            values=[""] + ALL_BUTTON_LABELS,
            width=14,
            state="readonly",
        ).pack(side="left")
        ab = self._btn(
            row,
            "Assign…",
            lambda v=var: self._start_hk_listen(action, v),
            secondary=True,
        )
        ab.pack(side="left", padx=(10, 6))
        self._btn(
            row,
            "Remove",
            lambda fr=row: self._remove_hotkey_binding_row(action, fr),
            secondary=True,
        ).pack(side="left")
        add_b = self._btn(
            row,
            "+ Add",
            lambda a=action, h=host: self._add_hotkey_binding_row(a, h, ""),
            secondary=True,
        )
        add_b.pack(side="left", padx=(10, 0))
        self._hk_trailing_add[action] = add_b

        if action not in self.hk_bind_rows:
            self.hk_bind_rows[action] = []
        self.hk_bind_rows[action].append({"frame": row, "var": var, "assign": ab})

    def _remove_hotkey_binding_row(self, action, row_frame):
        lst = self.hk_bind_rows.get(action, [])
        add_btn = self._hk_trailing_add.get(action)
        if add_btn is not None:
            try:
                add_btn.pack_forget()
            except tk.TclError:
                pass
        self.hk_bind_rows[action] = [x for x in lst if x["frame"] is not row_frame]
        row_frame.destroy()
        rest = self.hk_bind_rows[action]
        host = self._hk_hosts.get(action)
        if not rest and host is not None:
            self._add_hotkey_binding_row(action, host, "")
            return
        if add_btn is not None and rest:
            last_fr = rest[-1]["frame"]
            add_btn.pack(side="left", padx=(10, 0), in_=last_fr)

    # ── Tab: Mapping (calibration & hardware first) ──────────────────────────
    def _tab_mapping(self, nb):
        root = self._tab_frame(nb, "Mapping")

        c_cal = self._card(
            root,
            "Calibration",
            "Run these before editing raw indices. Emulation pauses while a walkthrough is active.",
        )
        tk.Label(
            c_cal,
            text=(
                "Walkthrough: face buttons and shoulders, then each D-pad direction (hat or individual switches). "
                "Stick axes for pointer and scrolling are set with the indices below."
            ),
            font=self.FSM,
            bg=self.CARD,
            fg=self.MUTED,
            justify="left",
            anchor="w",
        ).pack(anchor="w", pady=(0, 8))
        self._btn(c_cal, "Start button walkthrough", self._start_button_cal).pack(anchor="w")
        self.lbl_cal_progress = tk.Label(c_cal, text="", font=self.FH, bg=self.CARD, fg=self.AMBER)
        self.lbl_cal_progress.pack(anchor="w", pady=(8, 0))

        tk.Label(
            c_cal,
            text="Deadzone from sticks: rest both sticks, hands off, then measure once.",
            font=self.FSM,
            bg=self.CARD,
            fg=self.MUTED,
            justify="left",
            anchor="w",
        ).pack(anchor="w", pady=(14, 8))
        self._btn(c_cal, "Measure deadzone from sticks", self._start_dz_cal).pack(anchor="w")
        self.lbl_dz_result = tk.Label(
            c_cal,
            text=f"Last measured / saved deadzone: {config['advanced']['deadzone']:.4f}",
            font=self.FH,
            bg=self.CARD,
            fg=self.GREEN,
        )
        self.lbl_dz_result.pack(anchor="w", pady=(8, 0))

        c_dz = self._card(
            root,
            "Deadzone (manual)",
            "Filter small analog noise around center. You can tune here or use measurement above.",
        )
        self.dz_var = tk.DoubleVar(value=float(config["advanced"].get("deadzone", 0.01)))
        self._option_with_hint(
            c_dz,
            "Analog deadzone",
            self.dz_var,
            "Typical range 0.01–0.15. Higher = larger neutral zone before the pointer moves.",
            is_slider=True,
            slider_from=0.0,
            slider_to=0.4,
            slider_resolution=0.005,
            entry_decimals=4,
            entry_min=0.0,
            entry_max=0.9999,
        )

        c_ax = self._card(
            root,
            "Stick & scroll axes",
            "Axis indices as reported by pygame (0-based). Pointer X and Y must be different axes; "
            "scroll axes must not duplicate pointer axes (otherwise drift / phantom scrolling).",
        )
        ax = config.get("axes", {})
        self.ax_mx_var = tk.IntVar(value=int(ax.get("move_x", 0)))
        self.ax_my_var = tk.IntVar(value=int(ax.get("move_y", 1)))
        self.ax_sv_var = tk.IntVar(value=int(ax.get("scroll_vertical", 3)))
        self.ax_sh_var = tk.IntVar(value=int(ax.get("scroll_horizontal", 2)))
        for lab, var, hint in (
            ("Pointer X axis index", self.ax_mx_var, "Usually left stick horizontal."),
            ("Pointer Y axis index", self.ax_my_var, "Usually left stick vertical."),
            ("Vertical scroll axis index", self.ax_sv_var, "Often right stick vertical."),
            ("Horizontal scroll axis index", self.ax_sh_var, "Often right stick horizontal."),
        ):
            self._spin_axis_row(c_ax, lab, var, hint)

        c_man = self._card(
            root,
            "Manual button indices",
            "Only if calibration cannot identify a button. Values are pygame button indices.",
        )
        self.btn_vars = {}
        for cfg_key, idx in config.get("buttons", {}).items():
            row = tk.Frame(c_man, bg=self.CARD)
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
                bg=self.ENTRY_BG,
                fg=self.TEXT,
                insertbackground=self.TEXT,
                relief="flat",
                highlightthickness=1,
                highlightbackground=self.BORDER,
                highlightcolor=self.ACCENT,
            ).pack(side="right", padx=(8, 0))

        save_r = tk.Frame(c_man, bg=self.CARD)
        save_r.pack(fill="x", pady=(16, 0))
        self._btn(save_r, "Save mapping", self._save_mapping).pack(side="left")

    def _spin_axis_row(self, parent, title, var, hint):
        bg = parent.cget("bg")
        tk.Label(parent, text=title, font=self.FB, bg=bg, fg=self.TEXT, anchor="w").pack(anchor="w", pady=(10, 2))
        tk.Label(
            parent,
            text=hint,
            font=self.FSM,
            bg=bg,
            fg=self.MUTED,
            anchor="w",
            justify="left",
            wraplength=520,
        ).pack(anchor="w", pady=(0, 4))
        row = tk.Frame(parent, bg=bg)
        row.pack(fill="x", pady=(0, 4))
        ent = tk.Entry(
            row,
            width=6,
            bg=self.ENTRY_BG,
            fg=self.TEXT,
            insertbackground=self.TEXT,
            relief="flat",
            highlightthickness=1,
            highlightbackground=self.BORDER,
        )
        ent.pack(side="left")
        self._numeric_entry_bind(ent, var, 0, 15, is_int=True, decimals=0, entry_lo=0.0, entry_hi=32.0)

    # ── Tab: Actions (multiple bindings per feature) ───────────────────────
    def _tab_hotkeys(self, nb):
        root = self._tab_frame(nb, "Actions")

        c = self._card(root, "Controller bindings", HOTKEY_HINT)
        self.hk_bind_rows = {}
        for action in HOTKEY_ACTIONS:
            block = tk.Frame(c, bg=self.CARD)
            block.pack(fill="x", pady=(12, 6))
            tk.Label(
                block,
                text=HOTKEY_LABELS[action],
                font=self.FB,
                bg=self.CARD,
                fg=self.TEXT,
                anchor="w",
            ).pack(anchor="w")
            host = tk.Frame(block, bg=self.CARD)
            host.pack(fill="x", pady=(6, 0))
            self._hk_hosts[action] = host
            self.hk_bind_rows[action] = []
            initial = _normalize_hotkey_list(config["hotkeys"].get(action, []))
            if not initial:
                self._add_hotkey_binding_row(action, host, "")
            else:
                for val in initial:
                    self._add_hotkey_binding_row(action, host, val)

        save_row = tk.Frame(c, bg=self.CARD)
        save_row.pack(fill="x", pady=(18, 0))
        self._btn(save_row, "Save actions", self._save_hotkeys).pack(side="left")

    # ── Tab: Pointer & scroll ────────────────────────────────────────────────
    def _tab_mouse(self, nb):
        root = self._tab_frame(nb, "Pointer")

        c = self._card(
            root,
            "Speed",
            "Sensitivity scales about 10× at value 10; use the fields for precise numbers beyond the slider.",
        )
        self.sens_var = tk.IntVar(value=config["mouse"]["sensitivity"])
        self.scrl_var = tk.IntVar(value=config["mouse"]["scroll_sensitivity"])
        self.boost_fac_var = tk.DoubleVar(value=float(config["mouse"].get("speed_boost_factor", 2.0)))
        self.accel_var = tk.BooleanVar(value=config["mouse"].get("use_acceleration", True))
        self.hscrl_var = tk.BooleanVar(value=config["advanced"].get("h_scroll", True))
        self.snip_var = tk.DoubleVar(value=config["advanced"].get("sniper_factor", 0.1))

        self._slider_row(c, "Pointer sensitivity", self.sens_var)
        self._slider_row(c, "Scroll sensitivity", self.scrl_var)
        self._option_with_hint(
            c,
            "Speed boost multiplier (while boost held)",
            self.boost_fac_var,
            "Pointer and scroll gain when the speed-boost action is held.",
            is_slider=True,
            slider_from=1.0,
            slider_to=40.0,
            slider_resolution=0.25,
            entry_decimals=2,
            entry_min=0.25,
            entry_max=60.0,
        )
        self._option_with_hint(
            c,
            "Sniper factor",
            self.snip_var,
            "Multiplier while sniper mode is held (<1 slows down; >1 speeds up).",
            is_slider=True,
            slider_from=0.01,
            slider_to=2.0,
            slider_resolution=0.01,
            entry_decimals=3,
            entry_min=0.001,
            entry_max=25.0,
        )

        c2 = self._card(
            root,
            "Behavior",
            "Acceleration and horizontal scroll.",
        )
        self._option_with_hint(
            c2,
            "Pointer acceleration (curved response)",
            self.accel_var,
            "Smaller moves are finer; larger moves accelerate. Turn off for linear response.",
        )
        self._option_with_hint(
            c2,
            "Horizontal scroll (uses horizontal scroll axis index from Mapping)",
            self.hscrl_var,
            "Maps the chosen horizontal axis to sideways scrolling when the OS supports it.",
        )

        self._btn(c2, "Save pointer settings", self._save_mouse).pack(anchor="w", pady=(12, 0))

    # ── Widget helpers ─────────────────────────────────────────────────────
    def _bind_canvas_wheel(self, canvas):
        def on_wheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def on_linux(_e):
            canvas.yview_scroll(-1, "units")

        def on_enter(_):
            canvas.bind_all("<MouseWheel>", on_wheel)
            if sys.platform.startswith("linux"):
                canvas.bind_all("<Button-4>", on_linux)
                canvas.bind_all("<Button-5>", lambda e: canvas.yview_scroll(1, "units"))

        def on_leave(_):
            canvas.unbind_all("<MouseWheel>")
            if sys.platform.startswith("linux"):
                canvas.unbind_all("<Button-4>")
                canvas.unbind_all("<Button-5>")

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
                sw = max(320, self.winfo_screenwidth())
                hint.configure(wraplength=max(280, min(760, int(sw * 0.42))))

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

    def _scale_slider_kwargs(self):
        slen = max(14, int(round(16 * self._ui_scale)))
        return {
            "showvalue": 0,
            "bg": self.CARD,
            "fg": self.TEXT,
            "troughcolor": self.SCALE_TROUGH,
            "highlightthickness": 0,
            "sliderrelief": "flat",
            "bd": 0,
            "activebackground": self.SLIDER_THUMB,
            "background": self.SLIDER_THUMB,
            "sliderlength": slen,
        }

    def _scale_set_silent(self, scale, value):
        scale._ctc_silent = True
        try:
            scale.set(value)
        finally:
            scale._ctc_silent = False

    def _on_int_scale_user(self, scale, var, v):
        if getattr(scale, "_ctc_silent", False):
            return
        var.set(int(round(float(v))))

    def _on_float_scale_user(self, scale, var, v, decimals):
        if getattr(scale, "_ctc_silent", False):
            return
        x = float(v)
        if decimals:
            var.set(round(x, decimals))
        else:
            var.set(x)

    def _numeric_entry_bind(
        self,
        entry,
        var,
        slider_lo,
        slider_hi,
        *,
        is_int,
        decimals=2,
        entry_lo=None,
        entry_hi=None,
        after_var=None,
    ):
        fmt_dec = max(0, int(decimals))
        elo = float(slider_lo if entry_lo is None else entry_lo)
        ehi = float(slider_hi if entry_hi is None else entry_hi)

        def var_to_entry(*_):
            try:
                entry.delete(0, tk.END)
                if is_int:
                    entry.insert(0, str(int(var.get())))
                else:
                    v = float(var.get())
                    if fmt_dec:
                        entry.insert(0, f"{v:.{fmt_dec}f}".rstrip("0").rstrip("."))
                    else:
                        entry.insert(0, str(v))
            except (tk.TclError, ValueError):
                pass

        def trace_wrap(*_):
            var_to_entry()
            if after_var:
                after_var()

        def entry_apply(_event=None):
            try:
                raw = entry.get().strip().replace(",", ".")
                if is_int:
                    v = int(round(float(raw)))
                    v = int(max(elo, min(ehi, v)))
                    var.set(v)
                else:
                    v = float(raw)
                    v = max(elo, min(ehi, v))
                    if fmt_dec:
                        var.set(round(v, fmt_dec))
                    else:
                        var.set(v)
            except ValueError:
                var_to_entry()

        entry.bind("<Return>", entry_apply)
        entry.bind("<FocusOut>", entry_apply)
        var.trace_add("write", lambda *_: trace_wrap())
        trace_wrap()


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
        entry_decimals=2,
        entry_min=None,
        entry_max=None,
    ):
        bg = parent.cget("bg")
        wrap = max(280, min(720, int(self.winfo_screenwidth() * 0.38)))
        if is_slider:
            top = tk.Frame(parent, bg=bg)
            top.pack(fill="x", pady=(10, 0))
            tk.Label(top, text=title, font=self.FB, bg=bg, fg=self.TEXT, anchor="w").pack(
                side="left", fill="x", expand=True
            )
            tk.Label(
                parent,
                text=hint,
                font=self.FSM,
                bg=bg,
                fg=self.MUTED,
                justify="left",
                anchor="w",
                wraplength=wrap,
            ).pack(anchor="w", pady=(4, 6))
            row = tk.Frame(parent, bg=bg)
            row.pack(fill="x", pady=(0, 4))
            sc = tk.Scale(
                row,
                from_=slider_from,
                to=slider_to,
                resolution=slider_resolution,
                orient="horizontal",
                command=lambda v: self._on_float_scale_user(sc, var, v, entry_decimals),
                **self._scale_slider_kwargs(),
            )
            sc.pack(side="left", fill="x", expand=True, padx=(0, 10))
            ent = tk.Entry(
                row,
                width=10,
                bg=self.ENTRY_BG,
                fg=self.TEXT,
                insertbackground=self.TEXT,
                relief="flat",
                highlightthickness=1,
                highlightbackground=self.BORDER,
            )
            ent.pack(side="right")

            def sync_float_thumb(*_):
                try:
                    vv = float(var.get())
                except (tk.TclError, ValueError):
                    return
                clipped = max(float(slider_from), min(float(slider_to), vv))
                self._scale_set_silent(sc, clipped)

            self._numeric_entry_bind(
                ent,
                var,
                slider_from,
                slider_to,
                is_int=False,
                decimals=entry_decimals,
                entry_lo=entry_min,
                entry_hi=entry_max,
                after_var=sync_float_thumb,
            )
            sync_float_thumb()
            return
        tk.Checkbutton(
            parent,
            text=title,
            variable=var,
            font=self.FH,
            bg=bg,
            fg=self.TEXT,
            selectcolor=self.ENTRY_BG,
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
            wraplength=wrap,
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
            activebackground=self.ACCENT_HOVER,
            activeforeground="white",
            relief="flat",
            padx=14,
            pady=6,
            cursor="hand2",
            highlightthickness=0,
            takefocus=0,
        )
        return b

    def _slider_row(self, parent, label, var, *, scale_max=40, entry_min=1, entry_max=999999):
        bg = parent.cget("bg")
        row = tk.Frame(parent, bg=bg)
        row.pack(fill="x", pady=(6, 0))
        tk.Label(row, text=label, font=self.FH, bg=bg, fg=self.TEXT, anchor="w").pack(
            side="left", fill="x", expand=True
        )
        row2 = tk.Frame(parent, bg=bg)
        row2.pack(fill="x", pady=(0, 8))
        sc = tk.Scale(
            row2,
            from_=1,
            to=scale_max,
            orient="horizontal",
            resolution=1,
            command=lambda v: self._on_int_scale_user(sc, var, v),
            **self._scale_slider_kwargs(),
        )
        sc.pack(side="left", fill="x", expand=True, padx=(0, 10))
        ent = tk.Entry(
            row2,
            width=7,
            bg=self.ENTRY_BG,
            fg=self.TEXT,
            insertbackground=self.TEXT,
            relief="flat",
            highlightthickness=1,
            highlightbackground=self.BORDER,
        )
        ent.pack(side="right")

        def sync_thumb(*_):
            try:
                vv = int(var.get())
            except (tk.TclError, ValueError):
                return
            self._scale_set_silent(sc, min(scale_max, max(1, vv)))

        self._numeric_entry_bind(
            ent,
            var,
            1,
            scale_max,
            is_int=True,
            decimals=0,
            entry_lo=float(entry_min),
            entry_hi=float(entry_max),
            after_var=sync_thumb,
        )
        sync_thumb()

    # ── Hotkey capture (press-to-assign) ───────────────────────────────────
    def _on_escape(self, event=None):
        if self._hk_listen_action:
            self._stop_hk_listen(cancelled=True)
        return "break"

    def _start_hk_listen(self, action, target_var):
        global emulation_mode, input_capture_active
        if self._hk_listen_action:
            self._stop_hk_listen(cancelled=True)
        if controller is None:
            self._notify("warn", "Connect a controller before assigning bindings.")
            return
        self._hk_listen_prev_emu = emulation_mode
        emulation_mode = False
        input_capture_active = True
        self._hk_listen_action = action
        self._hk_listen_target_var = target_var
        for rows in self.hk_bind_rows.values():
            for r in rows:
                r["assign"].config(text="Assign…", state="disabled")
        for ab in self._hk_trailing_add.values():
            try:
                ab.config(state="disabled")
            except tk.TclError:
                pass
        for r in self.hk_bind_rows.get(action, []):
            if r["var"] is target_var:
                r["assign"].config(text="Listening…", state="disabled")
        self._notify(
            "ok",
            f"Press a mapped face button, bumper, stick click, or D-pad for «{HOTKEY_LABELS[action]}». Esc cancels.",
        )
        self.after(40, self._poll_hk_listen)

    def _stop_hk_listen(self, cancelled=False):
        global emulation_mode, input_capture_active
        self._hk_listen_action = None
        self._hk_listen_target_var = None
        input_capture_active = False
        emulation_mode = self._hk_listen_prev_emu
        for rows in self.hk_bind_rows.values():
            for r in rows:
                r["assign"].config(text="Assign…", state="normal")
        for ab in self._hk_trailing_add.values():
            try:
                ab.config(state="normal")
            except tk.TclError:
                pass
        if cancelled:
            self._notify("warn", "Assignment cancelled.")

    def _poll_hk_listen(self):
        action = self._hk_listen_action
        tv = self._hk_listen_target_var
        if not action or tv is None:
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
                tv.set(hlab)
                self._stop_hk_listen()
                self._notify("ok", f"Set binding for «{HOTKEY_LABELS[action]}» → {hlab}")
                return
        except Exception:
            pass
        for i in range(controller.get_numbuttons()):
            if controller.get_button(i):
                lab = _hotkey_label_for_button_index(i)
                if lab:
                    tv.set(lab)
                    self._stop_hk_listen()
                    self._notify("ok", f"Set binding for «{HOTKEY_LABELS[action]}» → {lab}")
                    return
                self._stop_hk_listen()
                self._notify(
                    "warn",
                    "This button index is not mapped yet. Run the button walkthrough on the Mapping tab first.",
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
        for action, rows in self.hk_bind_rows.items():
            bindings = [r["var"].get().strip() for r in rows if r["var"].get().strip()]
            config["hotkeys"][action] = bindings
        r = save_config()
        if r is True:
            self._notify("ok", "Actions saved.")
        else:
            self._notify("err", f"Could not save: {r}")

    def _save_mapping(self):
        for cfg_key, var in self.btn_vars.items():
            try:
                config["buttons"][cfg_key] = int(var.get())
            except ValueError:
                pass
        config.setdefault("axes", {})
        config["axes"]["move_x"] = max(0, min(32, int(self.ax_mx_var.get())))
        config["axes"]["move_y"] = max(0, min(32, int(self.ax_my_var.get())))
        config["axes"]["scroll_vertical"] = max(0, min(32, int(self.ax_sv_var.get())))
        config["axes"]["scroll_horizontal"] = max(0, min(32, int(self.ax_sh_var.get())))
        dz = float(self.dz_var.get())
        config["advanced"]["deadzone"] = round(max(0.0, min(0.9999, dz)), 4)
        global calibrated_deadzone
        calibrated_deadzone = float(config["advanced"]["deadzone"])
        r = save_config()
        if r is True:
            self._notify("ok", "Mapping saved (buttons, axes, deadzone).")
            try:
                self.lbl_dz_result.config(
                    text=f"Last measured / saved deadzone: {config['advanced']['deadzone']:.4f}",
                    fg=self.GREEN,
                )
            except Exception:
                pass
        else:
            self._notify("err", f"Could not save: {r}")

    def _save_mouse(self):
        config["mouse"]["sensitivity"] = max(1, min(999999, int(self.sens_var.get())))
        config["mouse"]["scroll_sensitivity"] = max(1, min(999999, int(self.scrl_var.get())))
        boost = float(self.boost_fac_var.get())
        config["mouse"]["speed_boost_factor"] = round(max(0.25, min(60.0, boost)), 2)
        config["mouse"]["use_acceleration"] = self.accel_var.get()
        config["advanced"]["h_scroll"] = self.hscrl_var.get()
        sf = float(self.snip_var.get())
        config["advanced"]["sniper_factor"] = round(max(0.001, min(25.0, sf)), 4)
        r = save_config()
        if r is True:
            self._notify("ok", "Pointer settings saved.")
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
        config.setdefault("dpad", {})
        q = [("button", k) for k in config["buttons"].keys()]
        for d in ("DP-Up", "DP-Down", "DP-Left", "DP-Right"):
            q.append(("dpad", d))
        self._cal_queue = q
        self._cal_step_i = 0
        self._cal_total = len(q)
        self._cal_btn_captured = False
        self._cal_dpad_done_capture = False
        self._show_cal_step()
        self.after(100, self._poll_cal)

    def _show_cal_step(self):
        if self._cal_step_i >= self._cal_total:
            return
        step = self._cal_queue[self._cal_step_i]
        n = self._cal_step_i + 1
        if step[0] == "button":
            lab = BUTTON_DISPLAY_NAMES.get(step[1], step[1])
            self.lbl_cal_progress.config(
                text=f"[{n}/{self._cal_total}] Press: {lab}",
                fg=self.AMBER,
            )
        elif step[0] == "dpad":
            self.lbl_cal_progress.config(
                text=f"[{n}/{self._cal_total}] D-pad: press {step[1]}.",
                fg=self.AMBER,
            )

    def _finish_cal_wizard(self):
        global emulation_mode, input_capture_active
        save_config()
        self.lbl_cal_progress.config(text="Calibration finished — saved.", fg=self.GREEN)
        input_capture_active = False
        emulation_mode = self._cal_prev_emu

    def _advance_cal(self, delay_ms=40):
        self._cal_step_i += 1
        self._cal_btn_captured = False
        self._cal_dpad_done_capture = False
        if self._cal_step_i >= self._cal_total:
            self._finish_cal_wizard()
        else:
            self._show_cal_step()
            self.after(delay_ms, self._poll_cal)

    def _poll_cal(self):
        global emulation_mode, input_capture_active
        if not input_capture_active or self._cal_step_i >= self._cal_total:
            return
        if controller is None:
            self.lbl_cal_progress.config(text="Controller disconnected.", fg=self.RED)
            input_capture_active = False
            emulation_mode = self._cal_prev_emu
            return

        step = self._cal_queue[self._cal_step_i]
        pygame.event.pump()
        kind = step[0]

        if kind == "button":
            cfg_key = step[1]
            nbtn = controller.get_numbuttons()
            any_down = any(controller.get_button(i) for i in range(nbtn))
            if not self._cal_btn_captured:
                if any_down:
                    for i in range(nbtn):
                        if controller.get_button(i):
                            config["buttons"][cfg_key] = i
                            break
                    self._cal_btn_captured = True
            elif not any_down:
                self._advance_cal(60)
            self.after(45, self._poll_cal)
            return

        if kind == "dpad":
            label = step[1]
            try:
                hat = controller.get_hat(0)
            except Exception:
                hat = (0, 0)
            nbtn = controller.get_numbuttons()
            any_btn = any(controller.get_button(i) for i in range(nbtn))
            if not self._cal_dpad_done_capture:
                if hat != (0, 0):
                    config.setdefault("dpad_bind", {})[label] = {
                        "type": "hat",
                        "x": int(hat[0]),
                        "y": int(hat[1]),
                    }
                    config.setdefault("dpad", {})[label] = -1
                    self._cal_dpad_done_capture = True
                elif any_btn:
                    for i in range(nbtn):
                        if controller.get_button(i):
                            config.setdefault("dpad_bind", {})[label] = {"type": "btn", "btn": int(i)}
                            config.setdefault("dpad", {})[label] = int(i)
                            self._cal_dpad_done_capture = True
                            break
            else:
                released = hat == (0, 0) and not any_btn
                if released:
                    self._advance_cal(80)
            self.after(45, self._poll_cal)
            return

    def _start_dz_cal(self):
        def cb(state, msg):
            if state == "running":
                self.lbl_dz_result.config(text=msg, fg=self.AMBER)
            elif state == "done":
                self.lbl_dz_result.config(
                    text=f"Last measured / saved deadzone: {config['advanced']['deadzone']:.4f}",
                    fg=self.GREEN,
                )
                try:
                    self.dz_var.set(float(config["advanced"]["deadzone"]))
                except Exception:
                    pass
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
                bg=self.TAB_SEL,
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