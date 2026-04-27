# ControllerToCursor

A full-featured gamepad-to-mouse emulator with a clean dark GUI — use any controller as a mouse and keyboard on Windows without any drivers or background services.



### Installation \& Usage

1. Go to the [Releases](https://github.com/Basti0307/ControllerToCursor/releases) page.
2. Download `ControllerToCursor.exe`.
3. Run the executable — no installation required.
4. Plug in your controller **before** launching, or click **Rescan controller** after connecting it.

>  **Note:** Antivirus software may flag the .exe as a "False Positive" because it was compiled with PyInstaller. This is a known and common issue with Python executables and is safe to ignore.



### On-Screen Keyboard (FreeVK)

The **On Screen Keyboard** hotkey launches [FreeVK](https://freevirtualkeyboard.com/) — a free, lightweight virtual keyboard for Windows.

**To use this feature:**

1. Download **FreeVK** from [https://freevirtualkeyboard.com/](https://freevirtualkeyboard.com/).
2. Place `FreeVK.exe` **in the same folder** as `ControllerToCursor.exe`.
3. Assign a controller button to the **On Screen Keyboard** action in the Hotkeys tab.
4. Press that button while emulation is active — the keyboard will pop up instantly.

If `FreeVK.exe` is not found, the app will display an error message with a reminder of where to get it.



### Features

* **Mouse Emulation:** Control your cursor with the left analog stick. Supports acceleration for natural feel.
* **Scroll Support:** Vertical and horizontal scrolling via the right stick.
* **Sniper Mode:** Hold a button to dramatically slow the cursor for pixel-precise aiming.
* **Speed Boost:** Hold a button to temporarily double the mouse speed.
* **Left \& Right Click:** Map any controller button to left and right mouse buttons.
* **On-Screen Keyboard:** Instantly open a virtual keyboard with a single button press (requires FreeVK).
* **Speech To Text:** Trigger Windows speech recognition (Win + H) from the controller.
* **Toggle Hotkey:** Use a configurable button combo to pause and resume emulation without closing the app — handy for switching to a game.
* **Button Calibration:** One-click calibration wizard that automatically maps all your controller buttons, regardless of brand.
* **Deadzone Calibration:** Measures your stick's resting noise and sets a precise deadzone automatically.
* **Fully Configurable:** All bindings, sensitivity, scroll speed, sniper factor, and deadzone are stored in a `ControllerToCursor-config.toml` file next to the executable and can be edited at any time.
* **Portable:** No installation required. Single `.exe`, runs anywhere.



### Configuration

On first launch, a `ControllerToCursor-config.toml` file is created automatically next to the executable with sensible defaults. All settings from the GUI are saved here.

**Default hotkey layout:**

|Action|Default Button|
|-|-|
|Toggle Emulation|D-Pad Up|
|Toggle Key 2|Y|
|Left Mouse Button|A|
|Right Mouse Button|B|
|Quit|D-Pad Down|
|Speech To Text|D-Pad Left|
|On Screen Keyboard|D-Pad Right|
|Speed Boost (gives the cursor a slight boost in speed)|Left Stick Click (L3)|
|Sniper Mode (slows the cursor down)|Left Bumper (LB)|

All bindings can be reassigned freely in the **Hotkeys** tab.



### GUI Overview

The app has four tabs:

* **Status** — Shows the connected controller name, axis/button count, current deadzone and sensitivity, and a toggle to pause/resume emulation.
* **Hotkeys** — Assign controller buttons to all actions, either by selecting from a dropdown or using the **Assign...** button to capture the next physical press.
* **Buttons** — Maps physical controller buttons to internal pygame indices. Run the **Button Calibration** wizard here if your controller's buttons aren't being recognized correctly.
* **Mouse** — Adjust pointer sensitivity (1–20), scroll sensitivity, acceleration toggle, horizontal scroll toggle, and the sniper slowdown factor.



### Tips

* Run **Button Calibration** first if this is your first time using the app or if you have a non-Xbox controller.
* The **Toggle Hotkey** feature supports up to 4 buttons that must all be held simultaneously — useful for chord combos that won't fire by accident.
* Settings are saved per-button via **Save** buttons in each tab, not globally, so you can adjust one section without affecting the others.
* The config `.toml` file can be hand-edited with any text editor for advanced setups.



### Legal Disclaimer

This tool is for personal and private use only.

1. **No Liability:** The developer assumes no liability for any damage or issues arising from the use of this software.
2. **"As-Is" Software:** This software is provided under the MIT License without any warranties. Use it at your own risk.



### Credits \& Third-Party Licenses

This application uses the following open-source projects:

* [pygame](https://www.pygame.org/) (LGPL) — Controller input handling.
* [pyautogui](https://github.com/asweigart/pyautogui) (BSD-3-Clause) — Mouse and keyboard emulation.
* [tomllib](https://docs.python.org/3/library/tomllib.html) / [tomli-w](https://github.com/hukkin/tomli-w) (MIT) — Config file reading and writing.
* [tkinter](https://docs.python.org/3/library/tkinter.html) — GUI Configuration.
* [FreeVK](https://freevirtualkeyboard.com/) *(optional, not bundled)* — On-screen virtual keyboard.
* Various LLMs (AI) were used in the process of creating this program.

\---

Developed by [Basti0307](https://github.com/Basti0307)

