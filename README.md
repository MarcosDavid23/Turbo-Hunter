# Turbo Hunter

**Kill Tracker & Lost Harvest Finder for theHunter: Call of the Wild**

Current source version: **0.6.0**

## What is Turbo Hunter?

Turbo Hunter tracks animals you have brought down but have not yet harvested. Its in-game display shows the number of pending animals, and the game's GPS points to the nearest one.

Version 0.6.0 fixes manual waypoint protection, preserves your saved options, and improves installation over older versions. It also adds clearer update and uninstall controls while keeping the window compact.

## Installation

1. Download [TurboHunter_0.6.0_NEXUS.zip](https://github.com/MarcosDavid23/Turbo-Hunter/releases/download/v0.6.0/TurboHunter_0.6.0_NEXUS.zip), extract it completely, and open the **Turbo Hunter** folder. Do not run the installer from inside the ZIP.
2. Run `INSTALL TURBO HUNTER.cmd` and wait for installation to finish.
3. Use the **Turbo Hunter** shortcut on your desktop to open the mod.
4. Click **START GAME**, or enable automatic game launch in the mod's window.

The installer is for **Windows 10/11 x64** and downloads its private Python and Frida components on first use. Download percentages appear in the installer window. Python does not need to be installed manually. The game launcher supports Steam and Epic Games; if needed, it will ask you to locate the game's executable.

Use the same installer to update. It removes old mod-owned Python, libraries and obsolete files before installing the new files. Your saved settings are preserved, and Python installed separately for other programs is left in place.

The approved installer ZIP is also kept in [`releases/`](releases/) so its exact contents and checksum can be checked against the published release.

## How to play

Enter a **SOLO** hunt and click **START MOD** if it is not already running. Wait for the on-screen counter. Hunt normally. Turbo Hunter marks the nearest downed animal and updates the count and marker when you harvest it.

### Keys

- **F6** or **NUMPAD \***: Check again and wait for **ANALYZING** to finish.
- **Second press near the current marker**: Follow the prompt to examine a blood clue.
- **Third press near the current marker**: Skip only that animal and move to the next. Wait for the analysis to finish between presses.
- **F8**: Move the on-screen display to another corner.
- **F9**: Show or hide the display.

If something goes wrong, report what happened on the mod's page and include a screenshot of the message. The **About** window can open the mod folder if a support log is needed.

For instructions in **English, Portuguese (Brazil) and Simplified Chinese**, see [`README - LEIA-ME.txt`](README%20-%20LEIA-ME.txt).

## Protections and languages

- Multiplayer protection is enabled by default and allows Turbo Hunter to run only in SOLO mode. You can disable it if you intentionally want to play with others, at your own risk.
- Manual waypoint protection can preserve a waypoint you placed until you remove it. This setting is disabled by default.
- Protection choices, language, display corner and automatic game launch are saved. Updating or reopening the mod does not switch your protection settings on or off. Change protection settings while the mod is stopped.
- The graphical interface supports Portuguese (Brazil), English and Simplified Chinese. The in-game display remains in English when Chinese is selected because its graphical font supports Latin characters only.

## Updates and removal

When Nexus Mods reports a newer version, a clickable notice opens the download page. **Update mod** also checks manually and offers an **Open Nexus Mods** button if a newer version cannot be found or confirmed. Automatic checks remain quiet when there is no confirmed newer version.

To uninstall the mod, its settings and its private components, use `UNINSTALL TURBO HUNTER.cmd`, **Uninstall** in the mod window, or **Windows Settings > Apps > Turbo Hunter > Uninstall**.

## Changes in 0.6.0

- Fixed manual waypoint protection becoming inactive during a hunt, including protection of an existing player waypoint.
- Preserved settings when updating, reopening the mod or moving the display.
- Removed old mod-owned components before installing, with visible download progress.
- Added a newer-version notice with a Nexus link and feedback for manual update checks.
- Fixed uninstalling from inside the mod window.
- Kept a compact window with visible Update, Uninstall and About controls. Activity has its own scrollbar; the main scrollbar appears when the window is too small.

## Source code

Turbo Hunter is a standalone Python project. It uses **Frida** to connect to the running game, and its DirectX 11 display is implemented in C. The source is published so users can inspect how the program works.

Main files:

- `TurboHunter/app/TurboHunter.pyw` — graphical interface
- `TurboHunter/app/turbo_hunter.py` — tracking and marking
- `TurboHunter/app/hud_directx11.c` — in-game display
- `TurboHunter/app/hud_config.json` — default display settings
- `TurboHunter/app/updates.py` — Nexus version checks
- `TurboHunter/installer/Install.ps1` — installation and update preparation
- `TurboHunter/installer/Common.ps1` — shared component cleanup and downloads
- `TurboHunter/installer/InstallerWorker.ps1` — private runtime installation and repair
- `TurboHunter/installer/Uninstall.ps1` — uninstallation
- `TurboHunter/installer/LaunchTogether.ps1` — desktop shortcut launcher
- `TurboHunter/installer/StartGame.ps1` — Steam, Epic Games or executable launcher
- `TurboHunter/installer/INICIAR_TEMPLATE.vbs` — launcher template
- `TurboHunter/installer/REPAIR TURBO HUNTER.cmd` — manual repair
- `INSTALL TURBO HUNTER.cmd` — installer
- `UNINSTALL TURBO HUNTER.cmd` — uninstaller

Because the program connects to a running game process, some antivirus or browser security systems may flag it. The source files can be inspected in this repository.

Turbo Hunter is an independent community project and is not affiliated with the developer or publisher of **theHunter: Call of the Wild**.

