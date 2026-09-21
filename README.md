# Turbo Hunter

**Kill Tracker & Lost Harvest Finder for theHunter: Call of the Wild**

Current source version: **0.5.4**

## What is Turbo Hunter?

Turbo Hunter tracks animals killed by the player but not yet harvested. Its in-game HUD shows the number of pending kills, while the game's GPS automatically points to the nearest pending body.

Version 0.5.4 also includes manual recovery for missing kills, multiplayer protection, manual-waypoint protection, an improved installer and interfaces in Portuguese (Brazil), English and Simplified Chinese.

## How it works

Turbo Hunter is a standalone Python project. It uses **Frida** to connect to the running `theHunter: Call of the Wild` process and interact with the game functions and memory required by the tracker.

The DirectX 11 HUD is implemented in:

`TurboHunter/app/hud_directx11.c`

Turbo Hunter is **not a Cheat Engine executable and was not compiled from a Cheat Engine table**. The source is published so users can inspect how the program works.

## Main files

- `TurboHunter/app/TurboHunter.pyw` — graphical interface
- `TurboHunter/app/turbo_hunter.py` — main tracking and waypoint logic
- `TurboHunter/app/hud_directx11.c` — DirectX 11 HUD
- `TurboHunter/app/hud_config.json` — default settings
- `TurboHunter/installer/InstallerWorker.ps1` — installer and repair worker
- `TurboHunter/installer/INICIAR_TEMPLATE.vbs` — launcher template
- `TurboHunter/installer/REINSTALAR TURBO HUNTER.cmd` — manual repair
- `INSTALAR TURBO HUNTER.cmd` — main installer
- `README - LEIA-ME.txt` — complete instructions in English, Portuguese and Simplified Chinese

## Installation

1. Download and extract the complete ZIP into a new folder.
2. Do not run the installer from inside the ZIP.
3. Run `INSTALAR TURBO HUNTER.cmd` once.
4. Wait for all three installation steps to finish.
5. After installation, use `INICIAR TURBO HUNTER.vbs` to launch the program.

Internet access may be required during the first installation. If Python or Frida is missing, the launcher can offer to repair the installation. Turbo Hunter 0.5.4 uses **Frida 17.17.0**.

## Usage

1. Open Turbo Hunter before or after launching the game.
2. Select Portuguese (Brazil), English or Simplified Chinese.
3. Keep multiplayer protection enabled.
4. Enter a **SOLO** hunt.
5. Click **START**.
6. Wait until the HUD is active and shows `KILLS: 0`.
7. Hunt normally. When a kill is pending, the GPS marks the nearest body.

## Hotkeys

- **F6** or **NUMPAD \*** — search again for missing kills, refresh player position and restore the GPS
- **F8** — move the HUD between the four screen corners
- **F9** — show or hide the HUD
- **F7** — disabled in this version

## Protections

- **Multiplayer protection** is enabled by default and allows Turbo Hunter to run only in SOLO mode. Disabling it permits multiplayer at the user's own risk.
- **Manual waypoint protection** preserves a waypoint placed by the player until it is cleared.

## Languages

- Portuguese (Brazil)
- English
- Simplified Chinese interface; the in-game HUD remains in English because its graphical font currently supports Latin characters only

## Source code and security

The complete source code for Turbo Hunter 0.5.4 is available in this repository for inspection. The installer uses PowerShell to prepare Python and Frida.

Because Turbo Hunter connects to and interacts with the memory of a running game process, some antivirus or browser security systems may classify the program or installer as suspicious. Users may review the Python, PowerShell, VBS, CMD and C source files directly in this repository.

## Game

**theHunter: Call of the Wild**

Turbo Hunter is an independent community project and is not affiliated with the game's developer or publisher.
