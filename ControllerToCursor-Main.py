"""
Controller Mouse Emulator
Emulates mouse input using a gamepad. Configuration via config.toml.
"""

# -- Suppress startup noise ----------------------------------------------------
import sys, os, warnings, io
warnings.filterwarnings("ignore")
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'
_stderr = sys.stderr; sys.stderr = io.StringIO()
import pygame
sys.stderr = _stderr

# -- Imports -------------------------------------------------------------------
import tomllib, pyautogui, subprocess, threading, time, ctypes
from typing import Optional

# PyAutoGUI safety settings
pyautogui.PAUSE    = 0
pyautogui.FAILSAFE = False

# -- Global state --------------------------------------------------------------
controller       = None
controller_count = 0
config           = {}
emulation_mode   = True
button_memory    = {}
frame_cache      = {}
last_frame_ts    = 0
clock            = pygame.time.Clock()
PROMPT           = "  > "
W                = 52
term_lock        = threading.Lock()
_vt_enabled      = False
last_menu_body: list = []
startup_log: list = []

def _enable_vt() -> bool:
    """Enables Virtual Terminal processing for ANSI escape codes on Windows."""
    if os.name != "nt":
        return True
    try:
        h = ctypes.windll.kernel32.GetStdHandle(-11)
        m = ctypes.c_uint32()
        if not ctypes.windll.kernel32.GetConsoleMode(h, ctypes.byref(m)):
            return False
        m.value |= 0x0004
        if not ctypes.windll.kernel32.SetConsoleMode(h, m):
            return False
    except Exception:
        return False
    return True

def _clear_screen():
    os.system("cls" if os.name == "nt" else "clear")

def _chrome_top_lines() -> list:
    """Generates the persistent header lines for the UI."""
    cname = controller.get_name() if controller else "none"
    line3 = f"  Controller: {cname}   |   Emulation: {'ON' if emulation_mode else 'OFF'}"
    line4 = f"  Commands:  help  ·  calibrate  ·  status  ·  reload  ·  quit"
    return [
        "  Controller Mouse Emulator",
        "  " + "─" * W,
        line3,
        line4,
    ]

def _redraw_chrome_in_place() -> None:
    """Updates only the status line (3) using VT escape codes to prevent flickering."""
    if not _vt_enabled:
        return
    cname = controller.get_name() if controller else "none"
    line3 = f"  Controller: {cname}   |   Emulation: {'ON' if emulation_mode else 'OFF'}"
    with term_lock:
        # Move cursor to line 3, clear line, write status, return to saved position
        sys.stdout.write(f"\033[s\033[3;1H\033[2K{line3}\033[u")
        sys.stdout.flush()

def _refresh_after_emulation_toggle() -> None:
    """Full or in-place refresh of the status bar when state changes."""
    if _vt_enabled:
        _redraw_chrome_in_place()
        return
    with term_lock:
        _clear_screen()
        for L in _chrome_top_lines():
            _original_print(L)
        _original_print("  " + "·" * W)
        for L in last_menu_body:
            _original_print(L)
        sys.stdout.write(PROMPT)
        sys.stdout.flush()

def _paint_menu(content_lines) -> None:
    """Renders the UI chrome and content lines, replacing the previous view."""
    global last_menu_body
    body = list(content_lines) if content_lines is not None else []
    last_menu_body = body
    with term_lock:
        _clear_screen()
        for L in _chrome_top_lines():
            _original_print(L)
        _original_print("  " + "·" * W)
        for L in body:
            _original_print(L)
        sys.stdout.flush()

def clear():
    _paint_menu([])

# -- Default config template ---------------------------------------------------
DEFAULT_CONFIG = """\
[hotkeys]
# Button names: A B X Y | DP-U DP-D DP-L DP-R | LB RB | LM RM | LS RS
toggle_emulation   = "DP-D"   # combine with toggle_combo_2..4 to pause/resume
toggle_combo_2     = "Y"
toggle_combo_3     = ""
toggle_combo_4     = ""
quit               = "DP-U"
left_click         = "A"
right_click        = "B"
speed_boost        = "RB"     # hold to double mouse speed
sniper_mode        = "LB"     # hold to slow mouse for precision aiming
speech_to_text     = "DP-L"
on_screen_keyboard = "DP-R"

[mouse]
sensitivity    = 5     # base speed (1 = slow, 10 = fast)
sniper_factor  = 0.3   # sniper multiplier (0.1–0.9, lower = slower)

[buttons]
# Pygame button indices - run 'calibrate' to detect the correct values for your device
A                  = 0
B                  = 1
X                  = 2
Y                  = 3
left_bumper        = 4
right_bumper       = 5
back_button        = 6   
start_button       = 7   
left_stick_click   = 8
right_stick_click  = 9
"""

# -- Mapping -------------------------------------------------------------------
_HAT_MAP = {
    "DP-U": (0,  1),
    "DP-D": (0, -1),
    "DP-L": (-1, 0),
    "DP-R": (1,  0),
}
_BTN_KEYS = {
    "A":  "A",  "B": "B",  "X": "X",  "Y": "Y",
    "LB": "left_bumper",   "RB": "right_bumper",
    "LM": "back_button",   "RM": "start_button",
    "LS": "left_stick_click", "RS": "right_stick_click",
}

# -- Console Helpers -----------------------------------------------------------

_original_print = print

def _header_block(title: str) -> list:
    u = "─" * W
    return [f"  {u}", f"  {title}", f"  {u}"]

def _help_lines() -> list:
    return [
        "  calibrate   Assign/Map controller buttons",
        "  status      Show controller details and current config",
        "  reload      Reload controller and config.toml",
        "  help        Show this help screen",
        "  quit        Exit application (or use gamepad shortcut)",
        "",
    ]

def show_help():
    body: list = []
    if startup_log:
        body.extend(startup_log)
        body.append("")
        startup_log.clear()
    body.extend(_header_block("Commands"))
    body.extend(_help_lines())
    _paint_menu(body)

# -- Config Logic --------------------------------------------------------------

def _config_path():
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, "config.toml")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.toml")

def setup():
    global _vt_enabled
    _vt_enabled = _enable_vt()
    startup_log.clear()
    _init_controller()
    _load_config()

def _init_controller():
    global controller, controller_count

    pygame.init()
    pygame.joystick.init()
    controller_count = pygame.joystick.get_count()

    if controller_count == 0:
        startup_log.append("  [!] No gamepad/controller detected.")
        controller = None
        return

    controller = pygame.joystick.Joystick(0)
    controller.init()
    startup_log.append(f"  [✓] Controller: {controller.get_name()}")
    startup_log.append(f"      Buttons: {controller.get_numbuttons()}   Axes: {controller.get_numaxes()}")

def _load_config():
    global config

    path = _config_path()
    if not os.path.exists(path):
        startup_log.append(f"  [!] config.toml missing - creating default: {path}")
        with open(path, "w", encoding="utf-8") as f:
            f.write(DEFAULT_CONFIG)

    try:
        with open(path, "rb") as f:
            config = tomllib.load(f)
        startup_log.append(f"  [✓] config.toml loaded")
    except Exception as e:
        startup_log.append(f"  [!] Failed to load config: {e}")
        config = {}

def reload_all():
    """Re-initialize hardware and reload settings from disk."""
    global controller
    try:
        if controller:
            controller.quit()
        pygame.joystick.quit()
    except Exception:
        pass
    startup_log.clear()
    _init_controller()
    _load_config()
    extra = [line for line in list(startup_log)]
    startup_log.clear()
    body: list = []
    if extra:
        body.extend(extra)
        body.append("")
    body.append("  [✓] Reload complete.")
    body.append("")
    body.extend(_header_block("Commands"))
    body.extend(_help_lines())
    _paint_menu(body)

# -- Input Detection -----------------------------------------------------------

def _to_code(button_name):
    if button_name in _HAT_MAP:
        return _HAT_MAP[button_name]
    key = _BTN_KEYS.get(button_name)
    if key:
        return config.get("buttons", {}).get(key, 100)
    return 100

def is_pressed(action_name):
    """
    Returns the state of a configured hotkey action.
    Caches results per tick to ensure consistency.
    """
    global last_frame_ts, frame_cache, button_memory

    ticks = pygame.time.get_ticks()
    if ticks != last_frame_ts:
        last_frame_ts = ticks
        frame_cache   = {}

    if action_name in frame_cache:
        return frame_cache[action_name]

    btn_name = config.get("hotkeys", {}).get(action_name, "")
    if not btn_name or controller is None:
        return False

    code = _to_code(btn_name)
    if code == 100:
        return False

    try:
        is_down = controller.get_hat(0) == code if isinstance(code, tuple) \
                  else bool(controller.get_button(int(code)))
    except Exception:
        is_down = False

    was_down = button_memory.get(action_name, False)

    if   is_down and not was_down: result = "just_pressed"
    elif not is_down and was_down: result = "just_released"
    elif is_down:                  result = "is_held"
    else:                          result = False

    frame_cache[action_name]   = result
    button_memory[action_name] = is_down
    return result

# -- Emulation Logic -----------------------------------------------------------

def toggle_emulation_mode():
    global emulation_mode

    active_keys = [
        k for k in ["toggle_emulation", "toggle_combo_2", "toggle_combo_3", "toggle_combo_4"]
        if config.get("hotkeys", {}).get(k, "").strip()
    ]

    if active_keys:
        states = [is_pressed(k) for k in active_keys]
        if all(s and s != "just_released" for s in states) and \
           any(s == "just_pressed" for s in states):
            emulation_mode = not emulation_mode
            _refresh_after_emulation_toggle()

    if is_pressed("quit") == "just_pressed":
        _clear_screen()
        with term_lock:
            _original_print("  Exiting ...\n")
        pygame.quit()
        os.kill(os.getpid(), 9)

def emulate_mouse():
    if controller is None or not config:
        return

    mouse_cfg = config.get("mouse", {})
    sens      = mouse_cfg.get("sensitivity", 5)
    threshold = 0.1

    # Sniper mode (priority) or speed boost
    if is_pressed("sniper_mode") in ("is_held", "just_pressed"):
        sens *= mouse_cfg.get("sniper_factor", 0.3)
    elif is_pressed("speed_boost") in ("is_held", "just_pressed"):
        sens *= 2

    # Clicks
    if is_pressed("left_click")  == "just_pressed":  pyautogui.mouseDown()
    if is_pressed("left_click")  == "just_released": pyautogui.mouseUp()
    if is_pressed("right_click") == "just_pressed":  pyautogui.rightClick()

    # System shortcuts
    if is_pressed("speech_to_text")     == "just_pressed": pyautogui.hotkey('win', 'h')
    if is_pressed("on_screen_keyboard") == "just_pressed":
        base = sys._MEIPASS if hasattr(sys, '_MEIPASS') else os.path.dirname(os.path.abspath(__file__))
        try: subprocess.Popen(os.path.join(base, "FreeVK.exe"))
        except Exception as e:
            with term_lock: _original_print(f"  [!] FreeVK: {e}")

    # Mouse movement — left stick (axes 0/1)
    try:
        x = controller.get_axis(0) if abs(controller.get_axis(0)) > threshold else 0
        y = controller.get_axis(1) if abs(controller.get_axis(1)) > threshold else 0
        pyautogui.moveRel(x * sens * 2, y * sens * 2)
    except Exception:
        pass

    # Scroll — right stick Y (axis 3)
    try:
        if controller.get_numaxes() > 3:
            s = controller.get_axis(3) if abs(controller.get_axis(3)) > threshold else 0
            pyautogui.scroll(int(s * sens * 5) * -1)
    except Exception:
        pass

# -- Calibration ---------------------------------------------------------------

def calibrate_controller():
    global emulation_mode
    emulation_mode = False

    if controller is None:
        _paint_menu(["  [!] No controller connected."])
        emulation_mode = True
        return

    if not os.path.exists(_config_path()):
        _paint_menu(["  [!] config.toml not found."])
        emulation_mode = True
        return

    button_names = list(config["buttons"].keys())
    n = len(button_names)
    acc: list = []
    intro = (
        _header_block(f"Calibration ({n} buttons)")
        + ["  Press the button/D-pad direction as labeled.", ""]
    )

    for idx, name in enumerate(button_names):
        _paint_menu(
            intro
            + acc
            + [f"  === [{idx+1}/{n}]  {name!s}  ===", "  press now", ""]
        )

        while True:
            pygame.event.pump()

            for i in range(controller.get_numbuttons()):
                if controller.get_button(i):
                    config["buttons"][name] = i
                    acc.append(f"  [✓]  {name}  =  BUTTON {i}")
                    time.sleep(0.35)
                    break
            else:
                hat = controller.get_hat(0)
                if hat != (0, 0):
                    config["buttons"][name] = hat
                    acc.append(f"  [✓]  {name}  =  D-Pad {list(hat)}")
                    time.sleep(0.35)
                    break
                time.sleep(0.05)
                continue
            break

    err = save_to_toml()
    tail = (
        (["  [!] Save error: " + err] if err else ["  [✓] config.toml updated."])
        + ["  Calibration finished. Emulation resumed.", ""]
    )
    _paint_menu(intro + acc + [""] + _header_block("Done") + tail)
    emulation_mode = True

def save_to_toml() -> Optional[str]:
    """Saves current config mapping to the TOML file."""
    try:
        with open(_config_path(), "w", encoding="utf-8") as f:
            for section, content in config.items():
                f.write(f"[{section}]\n")
                for key, val in content.items():
                    if isinstance(val, str):     f.write(f'{key} = "{val}"\n')
                    elif isinstance(val, tuple): f.write(f'{key} = {list(val)}\n')
                    else:                        f.write(f"{key} = {val}\n")
                f.write("\n")
    except Exception as e:
        return str(e)
    return None

# -- Status --------------------------------------------------------------------

def show_status():
    body = _header_block("Status Details") + [""]
    if controller:
        body.append(f"  Device Name : {controller.get_name()}")
        body.append(f"  Buttons     : {controller.get_numbuttons()}")
        body.append(f"  Axes        : {controller.get_numaxes()}")
    else:
        body.append("  Device: — (None)")

    mouse_cfg = config.get("mouse", {})
    body += [
        "",
        f"  (Status: Emulation/Controller info in header above)",
        f"  Sensitivity: {mouse_cfg.get('sensitivity', '?')}",
        f"  Sniper     : {mouse_cfg.get('sniper_factor', '?')}",
        "",
        "  Hotkeys:",
    ]
    for k, v in config.get("hotkeys", {}).items():
        if v and str(v).strip():
            body.append(f"    {k:<24}  {v}")

    body += [
        "",
        "  Button Mapping (indices):",
    ]
    for k, v in config.get("buttons", {}).items():
        body.append(f"    {k:<24}  {v!r}")
    body.append("")
    _paint_menu(body)

# -- Main Loop -----------------------------------------------------------------

def console():
    """Handles the CLI menu and user commands in a separate thread."""
    time.sleep(0.1)
    show_help()

    while True:
        try:
            with term_lock:
                sys.stdout.write(PROMPT)
                sys.stdout.flush()
            cmd = input().strip().lower()

            if   cmd == "quit":
                _clear_screen()
                with term_lock:
                    _original_print("  Goodbye.\n")
                pygame.quit()
                os.kill(os.getpid(), 9)
            elif cmd == "calibrate": calibrate_controller()
            elif cmd == "status":    show_status()
            elif cmd == "reload":    reload_all()
            elif cmd == "help":      show_help()
            elif cmd == "":          pass
            else:
                _paint_menu(
                    _header_block("Unknown Command")
                    + [f"  {cmd!r} — type 'help' for available commands.", ""]
                )

        except EOFError:
            break
        except KeyboardInterrupt:
            with term_lock:
                _original_print("\n  Stopped (Ctrl+C).\n")
            pygame.quit()
            os.kill(os.getpid(), 9)

if __name__ == "__main__":
    setup()
    # Run the console UI in the background
    threading.Thread(target=console, daemon=True).start()

    while True:
        pygame.event.pump()
        toggle_emulation_mode()
        if emulation_mode:
            emulate_mouse()
        clock.tick(120)