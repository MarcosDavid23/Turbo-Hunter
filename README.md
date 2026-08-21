# Turbo Hunter

**Kill Locator / Localizador de Abates for theHunter: Call of the Wild**

Current source version: **0.4.1**

## What is Turbo Hunter?

Turbo Hunter tracks animals killed by the player and automatically points the in-game GPS to the nearest uncollected animal.

It also includes an in-game HUD showing the number of pending kills.

## How it works

Turbo Hunter is a standalone Python project.

It uses **Frida** to connect to the running `theHunter: Call of the Wild` process and interact with game functions and memory required by the kill locator.

The DirectX 11 HUD is implemented using the included C source file:

`TurboHunter/app/hud_directx11.c`

Turbo Hunter is **not a Cheat Engine executable and was not compiled from a Cheat Engine table**.

The source code published here is provided so users can inspect how the program works.

## Main files

* `TurboHunter/app/TurboHunter.pyw` - graphical interface
* `TurboHunter/app/turbo_hunter.py` - main kill locator logic
* `TurboHunter/app/hud_directx11.c` - DirectX 11 HUD
* `TurboHunter/app/hud_config.json` - user settings
* `TurboHunter/installer/` - installation scripts
* `INSTALAR TURBO HUNTER.vbs` - installer launcher

## Installation

1. Download the complete project.
2. Keep the folder structure intact.
3. Run `INSTALAR TURBO HUNTER.vbs`.
4. Click **INSTALL**.
5. Wait while Turbo Hunter prepares Python and Frida.
6. When installation is complete, open `INICIAR TURBO HUNTER.vbs`.

Internet access may be required during the first installation.

The installer can use an existing compatible Python installation or prepare its own Python environment.

Turbo Hunter 0.4.1 uses:

`Frida 17.17.0`

## Usage

1. Start Turbo Hunter.
2. Click **START**.
3. Turbo Hunter can remain on **WAITING FOR GAME**.
4. Open `theHunter: Call of the Wild`.
5. Turbo Hunter will connect automatically when the game is detected.
6. Hunt normally.
7. The GPS will point to the nearest pending animal.

## Hotkeys

* **F8** - Move the HUD between the four corners.
* **F9** - Show or hide the HUD.

## Source code and security

The complete source code for Turbo Hunter 0.4.1 is available in this repository for inspection.

The installer uses PowerShell to prepare the required environment and install Frida.

Because Turbo Hunter connects to and interacts with the memory of a running game process, some antivirus or browser security systems may classify the program or installer as suspicious.

Users who prefer to inspect the project before running it can review all Python, PowerShell, VBS and C source files directly in this repository.

## Languages

Turbo Hunter automatically detects the Windows interface language.

* Portuguese Windows: Portuguese (Brazil)
* Other languages: English

Additional languages may be added in future versions.

## Game

**theHunter: Call of the Wild**

Turbo Hunter is an independent community project and is not affiliated with the game's developer or publisher.
