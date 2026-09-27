# Turbo Hunter

**Kill Tracker & Lost Harvest Finder for theHunter: Call of the Wild**

Current source version: **0.5.7**

## What is Turbo Hunter?

Turbo Hunter tracks animals you have brought down but have not yet harvested. Its in-game display shows the number of pending animals, and the game's GPS points to the nearest one.

Version 0.5.7 improves the speed of automatic marking, updates the marker after a harvest, and makes it easier to find or skip a marker that needs another check. It also improves installation over older versions and adds a desktop shortcut to launch the mod and the game together.

## Installation

1. Download and extract the complete ZIP. Do not run the installer from inside the ZIP.
2. Run `INSTALL TURBO HUNTER.cmd` and wait for installation to finish.
3. Use the **Turbo Hunter + theHunter** shortcut on your desktop to start Turbo Hunter and the game together.

The installer is for **Windows** and may need an internet connection on first use. The shortcut can find a Steam or Epic Games installation; if needed, it will ask you to locate the game's executable.

## How to play

Enter a **SOLO** hunt and wait for the on-screen counter. Hunt normally. Turbo Hunter marks the nearest downed animal and updates the count and marker when you harvest it.

### Keys

- **F6** or **NUMPAD \***: Check again and wait for **ANALYZING** to finish.
- **Second press near the current marker**: Follow the prompt to examine a blood clue.
- **Third press near the current marker**: Skip only that animal and move to the next. Wait for the analysis to finish between presses.
- **F8**: Move the on-screen display to another corner.
- **F9**: Show or hide the display.

If something goes wrong, click **STOP** in the Turbo Hunter window and send the ZIP it creates along with a brief description of what happened.

For instructions in **English, Portuguese (Brazil) and Simplified Chinese**, see [`README - LEIA-ME.txt`](README%20-%20LEIA-ME.txt).

## Protections and languages

- Multiplayer protection is enabled by default and allows Turbo Hunter to run only in SOLO mode. You can disable it if you intentionally want to play with others, at your own risk.
- Manual waypoint protection can preserve a waypoint you placed until you remove it. This setting is disabled by default.
- The graphical interface supports Portuguese (Brazil), English and Simplified Chinese. The in-game display remains in English when Chinese is selected because its graphical font supports Latin characters only.

## Source code

Turbo Hunter is a standalone Python project. It uses **Frida** to connect to the running game, and its DirectX 11 display is implemented in C. The source is published so users can inspect how the program works.

Main files:

- `TurboHunter/app/TurboHunter.pyw` — graphical interface
- `TurboHunter/app/turbo_hunter.py` — tracking and marking
- `TurboHunter/app/hud_directx11.c` — in-game display
- `TurboHunter/app/hud_config.json` — default display settings
- `TurboHunter/installer/InstallerWorker.ps1` — installation and repair
- `TurboHunter/installer/LaunchTogether.ps1` — desktop shortcut launcher
- `TurboHunter/installer/INICIAR_TEMPLATE.vbs` — launcher template
- `TurboHunter/installer/REINSTALAR TURBO HUNTER.cmd` — manual repair
- `INSTALL TURBO HUNTER.cmd` — installer

Because the program connects to a running game process, some antivirus or browser security systems may flag it. The source files can be inspected in this repository.

Turbo Hunter is an independent community project and is not affiliated with the developer or publisher of **theHunter: Call of the Wild**.
