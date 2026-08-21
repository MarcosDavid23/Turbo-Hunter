# Turbo Hunter 0.4.1 - GUI without CMD window
import ctypes
import importlib.util
import json
import locale
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

VERSION = "0.4.1"
BASE_DIR = Path(__file__).resolve().parent
INTERNAL_DIR = BASE_DIR.parent
ROOT_DIR = INTERNAL_DIR.parent
RUNTIME_DIR = INTERNAL_DIR / "runtime"
PACKAGES_DIR = RUNTIME_DIR / "packages"
ASSETS_DIR = INTERNAL_DIR / "assets"

if PACKAGES_DIR.exists():
    sys.path.insert(0, str(PACKAGES_DIR))

CORE_FILE = BASE_DIR / "turbo_hunter.py"
CONFIG_FILE = BASE_DIR / "hud_config.json"
STOP_FILE = BASE_DIR / ".turbo_hunter_stop"
INSTALLER_PS1 = INTERNAL_DIR / "installer" / "Installer.ps1"
DEER_PNG = ASSETS_DIR / "turbo_hunter_deer.png"
ICON_PNG = DEER_PNG

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

CORNERS = {
    "pt-BR": (
        "superior esquerdo",
        "superior direito",
        "inferior esquerdo",
        "inferior direito",
    ),
    "en": (
        "top left",
        "top right",
        "bottom left",
        "bottom right",
    ),
}

TEXT = {
    "pt-BR": {
        "subtitle": "Localizador de Abates • theHunter: Call of the Wild",
        "status": "STATUS",
        "stopped": "PARADO",
        "click_start": "Clique em INICIAR. O Turbo Hunter pode esperar o jogo abrir.",
        "config": "CONFIGURAÇÃO",
        "hud_corner": "Canto do HUD",
        "solo_protection": "Proteção SOLO",
        "solo_help": "Ativada: bloqueia multiplayer. Desativada: multiplayer por conta e risco.",
        "waypoint_protection": "Proteção de waypoint",
        "waypoint_on": "Ativada: respeita seu waypoint e espera você limpá-lo para voltar ao GPS.",
        "waypoint_off": "Desativada: o Turbo Hunter pode mover/reassumir o waypoint automaticamente.",
        "start": "INICIAR",
        "stop": "PARAR",
        "activity": "ATIVIDADE",
        "no_activity": "Nenhuma atividade ainda.",
        "keys": "F8 = mudar canto   •   F9 = ocultar/mostrar HUD",
        "core_missing": "Arquivo principal do Turbo Hunter não foi encontrado.",
        "starting": "INICIANDO",
        "preparing_waiting": "Preparando e aguardando o jogo...",
        "preparing": "Preparando o Turbo Hunter.",
        "components_missing": "Os componentes do Turbo Hunter estão ausentes ou danificados.",
        "repair_question": "Deseja reparar a instalação agora?",
        "repair_failed": "Não foi possível abrir o reparo da instalação.",
        "launch_failed": "Não foi possível iniciar o Turbo Hunter: ",
        "stopping": "ENCERRANDO",
        "cleaning": "Limpando o GPS e desconectando do jogo...",
        "active": "ATIVO",
        "gps_pending": "GPS funcionando • {count} abate(s) pendente(s)",
        "connected": "Turbo Hunter conectado ao jogo.",
        "new_kill": "Novo abate registrado. Pendentes: {count}.",
        "collected": "Animal coletado. Restam {count}.",
        "all_collected": "Todos os animais pendentes foram coletados.",
        "mp_unlocked": "Modo multiplayer liberado por configuração, por conta e risco.",
        "wp_on_activity": "Waypoint manual protegido: o GPS só volta depois que você limpar o point.",
        "wp_off_activity": "Proteção de waypoint desligada: o Turbo Hunter pode mover o point automaticamente.",
        "wp_respected": "Seu waypoint foi respeitado. Limpe o point no jogo para liberar o GPS automático.",
        "wp_cleared": "Waypoint limpo. GPS automático liberado novamente.",
        "waiting_game": "AGUARDANDO JOGO",
        "open_game": "Abra o theHunter: Call of the Wild. A conexão será automática.",
        "ready_waiting": "Turbo Hunter pronto e esperando o jogo abrir.",
        "connecting": "CONECTANDO",
        "game_detected": "Jogo detectado. Preparando GPS e HUD...",
        "game_found": "theHunter encontrado.",
        "blocked": "BLOQUEADO",
        "solo_blocked": "A proteção SOLO detectou uma sessão multiplayer.",
        "safe_disconnect": "Turbo Hunter desconectou por segurança.",
        "attention": "ATENÇÃO",
        "problem": "O Turbo Hunter encontrou um problema. Consulte os logs se necessário.",
        "error": "ERRO",
        "waiting_connect": "Aguardando o Turbo Hunter conectar ao jogo...",
        "ended_log": "O Turbo Hunter foi encerrado. Se houve problema, consulte os arquivos de log.",
    },
    "en": {
        "subtitle": "Kill Locator • theHunter: Call of the Wild",
        "status": "STATUS",
        "stopped": "STOPPED",
        "click_start": "Click START. Turbo Hunter can wait for the game to open.",
        "config": "SETTINGS",
        "hud_corner": "HUD corner",
        "solo_protection": "SOLO protection",
        "solo_help": "Enabled: blocks multiplayer. Disabled: multiplayer at your own risk.",
        "waypoint_protection": "Waypoint protection",
        "waypoint_on": "Enabled: respects your waypoint and waits until you clear it before returning to GPS.",
        "waypoint_off": "Disabled: Turbo Hunter may automatically move/reclaim the waypoint.",
        "start": "START",
        "stop": "STOP",
        "activity": "ACTIVITY",
        "no_activity": "No activity yet.",
        "keys": "F8 = move HUD corner   •   F9 = show/hide HUD",
        "core_missing": "The main Turbo Hunter file was not found.",
        "starting": "STARTING",
        "preparing_waiting": "Preparing and waiting for the game...",
        "preparing": "Preparing Turbo Hunter.",
        "components_missing": "Turbo Hunter components are missing or damaged.",
        "repair_question": "Repair the installation now?",
        "repair_failed": "The installation repair could not be opened.",
        "launch_failed": "Turbo Hunter could not be started: ",
        "stopping": "STOPPING",
        "cleaning": "Clearing GPS and disconnecting from the game...",
        "active": "ACTIVE",
        "gps_pending": "GPS active • {count} pending kill(s)",
        "connected": "Turbo Hunter connected to the game.",
        "new_kill": "New kill registered. Pending: {count}.",
        "collected": "Animal collected. {count} remaining.",
        "all_collected": "All pending animals were collected.",
        "mp_unlocked": "Multiplayer enabled by configuration, at your own risk.",
        "wp_on_activity": "Manual waypoint protected: GPS only returns after you clear the point.",
        "wp_off_activity": "Waypoint protection is off: Turbo Hunter may move the point automatically.",
        "wp_respected": "Your waypoint was respected. Clear it in-game to release automatic GPS.",
        "wp_cleared": "Waypoint cleared. Automatic GPS is available again.",
        "waiting_game": "WAITING FOR GAME",
        "open_game": "Open theHunter: Call of the Wild. Connection will be automatic.",
        "ready_waiting": "Turbo Hunter is ready and waiting for the game.",
        "connecting": "CONNECTING",
        "game_detected": "Game detected. Preparing GPS and HUD...",
        "game_found": "theHunter found.",
        "blocked": "BLOCKED",
        "solo_blocked": "SOLO protection detected a multiplayer session.",
        "safe_disconnect": "Turbo Hunter disconnected for safety.",
        "attention": "ATTENTION",
        "problem": "Turbo Hunter found a problem. Check the logs if needed.",
        "error": "ERROR",
        "waiting_connect": "Waiting for Turbo Hunter to connect to the game...",
        "ended_log": "Turbo Hunter stopped. If there was a problem, check the log files.",
    },
}


def detect_windows_language():
    if sys.platform == "win32":
        try:
            buffer = ctypes.create_unicode_buffer(85)
            if ctypes.windll.kernel32.GetUserDefaultLocaleName(buffer, len(buffer)):
                name = buffer.value
                return "pt-BR" if name.lower().startswith("pt") else "en"
        except Exception:
            pass
    try:
        name = (locale.getlocale()[0] or "").lower()
        return "pt-BR" if name.startswith("pt") else "en"
    except Exception:
        return "en"


def resolve_language(value):
    value = str(value or "auto").strip()
    if value.lower() == "auto":
        return detect_windows_language()
    if value.lower().startswith("pt"):
        return "pt-BR"
    return "en"


def normalized_config():
    data = {
        "corner": 3,
        "name": CORNERS["pt-BR"][3],
        "solo_only": 1,
        "protect_setwaypoint": 1,
        "language": "auto",
    }
    try:
        loaded = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data.update(loaded)
    except Exception:
        pass

    try:
        corner = int(data.get("corner", 3))
    except Exception:
        corner = 3
    if not 0 <= corner <= 3:
        corner = 3

    try:
        solo_only = 1 if int(data.get("solo_only", 1)) != 0 else 0
    except Exception:
        solo_only = 1

    try:
        protect = 1 if int(data.get("protect_setwaypoint", 1)) != 0 else 0
    except Exception:
        protect = 1

    language = str(data.get("language", "auto") or "auto")
    if language.lower() not in ("auto", "en", "pt-br", "pt_br", "pt"):
        language = "auto"

    ui_lang = resolve_language(language)
    return {
        "corner": corner,
        "name": CORNERS[ui_lang][corner],
        "solo_only": solo_only,
        "protect_setwaypoint": protect,
        "language": language,
    }


def write_config(config):
    CONFIG_FILE.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def python_for_subprocess():
    return str(Path(sys.executable))


class TurboHunterGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.process = None
        self.output_queue = queue.Queue()
        self.closing = False
        self._last_pending = 0
        self.config_data = normalized_config()
        self.lang = resolve_language(self.config_data.get("language", "auto"))
        self.t = TEXT[self.lang]
        self.corners = CORNERS[self.lang]
        self.config_data["name"] = self.corners[self.config_data["corner"]]
        write_config(self.config_data)

        self.title(f"Turbo Hunter {VERSION}")
        self.geometry("620x545")
        self.minsize(590, 520)
        self.configure(bg="#101417")
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self._window_icon = None
        self._deer_image = None
        self._load_icons()
        self._setup_style()
        self._build_ui()
        self.after(100, self._poll_output)
        self.after(500, self._poll_process)

    def _load_icons(self):
        if ICON_PNG.exists():
            try:
                self._window_icon = tk.PhotoImage(file=str(ICON_PNG))
                self.iconphoto(True, self._window_icon)
            except Exception:
                self._window_icon = None
        if DEER_PNG.exists():
            try:
                full = tk.PhotoImage(file=str(DEER_PNG))
                self._deer_image = full.subsample(4, 4)
            except Exception:
                self._deer_image = None

    def _setup_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Root.TFrame", background="#101417")
        style.configure("Card.TFrame", background="#181e22")
        style.configure("Title.TLabel", background="#101417", foreground="#f4f7f8", font=("Segoe UI", 22, "bold"))
        style.configure("Subtitle.TLabel", background="#101417", foreground="#9eabb2", font=("Segoe UI", 10))
        style.configure("CardTitle.TLabel", background="#181e22", foreground="#f4f7f8", font=("Segoe UI", 11, "bold"))
        style.configure("CardText.TLabel", background="#181e22", foreground="#b8c1c6", font=("Segoe UI", 9))
        style.configure("Status.TLabel", background="#181e22", foreground="#f4f7f8", font=("Segoe UI", 18, "bold"))
        style.configure("TCheckbutton", background="#181e22", foreground="#edf2f4", font=("Segoe UI", 10))
        style.map("TCheckbutton", background=[("active", "#181e22")])
        style.configure("TCombobox", font=("Segoe UI", 10))
        style.configure("Start.TButton", font=("Segoe UI", 11, "bold"), padding=(18, 10), background="#eb9130", foreground="#181818", borderwidth=0)
        style.map("Start.TButton", background=[("active", "#f5a64d"), ("pressed", "#d77f24"), ("disabled", "#5c5146")], foreground=[("disabled", "#a9a39d")])
        style.configure("Stop.TButton", font=("Segoe UI", 11, "bold"), padding=(18, 10), background="#343d42", foreground="#f4f7f8", borderwidth=0)
        style.map("Stop.TButton", background=[("active", "#465159"), ("pressed", "#293035"), ("disabled", "#20262a")], foreground=[("disabled", "#69757c")])

    def _build_ui(self):
        root = ttk.Frame(self, style="Root.TFrame", padding=20)
        root.pack(fill="both", expand=True)

        header = ttk.Frame(root, style="Root.TFrame")
        header.pack(fill="x", pady=(0, 14))

        if self._deer_image is not None:
            deer = tk.Label(header, image=self._deer_image, bg="#101417", borderwidth=0)
        else:
            deer = tk.Label(header, text="TH", bg="#101417", fg="#eb9130", font=("Segoe UI", 22, "bold"))
        deer.pack(side="left", padx=(0, 10))

        titles = ttk.Frame(header, style="Root.TFrame")
        titles.pack(side="left", fill="x", expand=True)
        ttk.Label(titles, text="TURBO HUNTER", style="Title.TLabel").pack(anchor="w")
        ttk.Label(titles, text=self.t["subtitle"], style="Subtitle.TLabel").pack(anchor="w")

        status_card = ttk.Frame(root, style="Card.TFrame", padding=16)
        status_card.pack(fill="x", pady=(0, 12))
        ttk.Label(status_card, text=self.t["status"], style="CardTitle.TLabel").pack(anchor="w")
        self.status_label = ttk.Label(status_card, text=self.t["stopped"], style="Status.TLabel")
        self.status_label.pack(anchor="w", pady=(4, 0))
        self.detail_label = ttk.Label(status_card, text=self.t["click_start"], style="CardText.TLabel")
        self.detail_label.pack(anchor="w", pady=(3, 0))

        config_card = ttk.Frame(root, style="Card.TFrame", padding=16)
        config_card.pack(fill="x", pady=(0, 12))
        ttk.Label(config_card, text=self.t["config"], style="CardTitle.TLabel").pack(anchor="w")

        row = ttk.Frame(config_card, style="Card.TFrame")
        row.pack(fill="x", pady=(10, 8))
        ttk.Label(row, text=self.t["hud_corner"], style="CardText.TLabel").pack(side="left")
        self.corner_var = tk.StringVar(value=self.corners[self.config_data["corner"]])
        self.corner_combo = ttk.Combobox(row, state="readonly", values=self.corners, textvariable=self.corner_var, width=23)
        self.corner_combo.pack(side="right")

        self.solo_var = tk.IntVar(value=self.config_data["solo_only"])
        self.solo_check = ttk.Checkbutton(config_card, text=self.t["solo_protection"], variable=self.solo_var)
        self.solo_check.pack(anchor="w", pady=(4, 0))
        ttk.Label(config_card, text=self.t["solo_help"], style="CardText.TLabel").pack(anchor="w", padx=(22, 0))

        self.protect_var = tk.IntVar(value=self.config_data["protect_setwaypoint"])
        self.protect_check = ttk.Checkbutton(config_card, text=self.t["waypoint_protection"], variable=self.protect_var)
        self.protect_check.pack(anchor="w", pady=(10, 0))
        ttk.Label(config_card, text=self.t["waypoint_on"], style="CardText.TLabel").pack(anchor="w", padx=(22, 0))
        ttk.Label(config_card, text=self.t["waypoint_off"], style="CardText.TLabel").pack(anchor="w", padx=(22, 0))

        buttons = ttk.Frame(root, style="Root.TFrame")
        buttons.pack(fill="x", pady=(0, 12))
        self.start_button = ttk.Button(buttons, text=self.t["start"], style="Start.TButton", command=self.start_mod)
        self.start_button.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.stop_button = ttk.Button(buttons, text=self.t["stop"], style="Stop.TButton", command=self.stop_mod, state="disabled")
        self.stop_button.pack(side="left", fill="x", expand=True, padx=(6, 0))

        activity = ttk.Frame(root, style="Card.TFrame", padding=14)
        activity.pack(fill="both", expand=True)
        ttk.Label(activity, text=self.t["activity"], style="CardTitle.TLabel").pack(anchor="w")
        self.activity_label = ttk.Label(activity, text=self.t["no_activity"], style="CardText.TLabel", wraplength=540, justify="left")
        self.activity_label.pack(anchor="w", pady=(7, 8))
        ttk.Label(activity, text=self.t["keys"], style="CardText.TLabel").pack(anchor="w")

    def _set_controls_running(self, running):
        self.start_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")
        self.corner_combo.configure(state="disabled" if running else "readonly")
        self.solo_check.configure(state="disabled" if running else "normal")
        self.protect_check.configure(state="disabled" if running else "normal")

    def _save_ui_config(self):
        try:
            corner = self.corners.index(self.corner_var.get())
        except ValueError:
            corner = 3
        config = {
            "corner": corner,
            "name": self.corners[corner],
            "solo_only": 1 if self.solo_var.get() else 0,
            "protect_setwaypoint": 1 if self.protect_var.get() else 0,
            "language": self.config_data.get("language", "auto"),
        }
        write_config(config)
        self.config_data = config

    def start_mod(self):
        if self.process is not None and self.process.poll() is None:
            return
        if not CORE_FILE.exists():
            messagebox.showerror("Turbo Hunter", self.t["core_missing"])
            return

        self._save_ui_config()
        try:
            if STOP_FILE.exists():
                STOP_FILE.unlink()
        except Exception:
            pass

        self._set_controls_running(True)
        self.status_label.configure(text=self.t["starting"])
        self.detail_label.configure(text=self.t["preparing_waiting"])
        self.activity_label.configure(text=self.t["preparing"])
        threading.Thread(target=self._prepare_and_launch, daemon=True).start()

    def _prepare_and_launch(self):
        python_exe = python_for_subprocess()
        if importlib.util.find_spec("frida") is None:
            self.output_queue.put(("repair", self.t["components_missing"]))
            return

        child_env = os.environ.copy()
        current_pythonpath = child_env.get("PYTHONPATH", "")
        child_env["PYTHONPATH"] = str(PACKAGES_DIR) if not current_pythonpath else str(PACKAGES_DIR) + os.pathsep + current_pythonpath

        try:
            self.process = subprocess.Popen(
                [python_exe, str(CORE_FILE)],
                cwd=str(BASE_DIR),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=CREATE_NO_WINDOW,
                env=child_env,
            )
        except Exception as exc:
            self.output_queue.put(("gui_error", self.t["launch_failed"] + str(exc)))
            return

        self.output_queue.put(("gui", "STARTED"))
        try:
            for line in self.process.stdout:
                self.output_queue.put(("core", line.rstrip()))
        except Exception:
            pass

    def _open_repair(self):
        if not INSTALLER_PS1.exists():
            messagebox.showerror("Turbo Hunter", self.t["repair_failed"])
            return False
        try:
            subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", str(INSTALLER_PS1)],
                creationflags=CREATE_NO_WINDOW,
            )
            return True
        except Exception:
            messagebox.showerror("Turbo Hunter", self.t["repair_failed"])
            return False

    def stop_mod(self):
        if self.process is None or self.process.poll() is not None:
            self._finish_stopped()
            return
        self.status_label.configure(text=self.t["stopping"])
        self.detail_label.configure(text=self.t["cleaning"])
        try:
            STOP_FILE.write_text("stop", encoding="ascii")
        except Exception:
            try:
                self.process.terminate()
            except Exception:
                pass

    def _finish_stopped(self):
        self.process = None
        self._set_controls_running(False)
        self.status_label.configure(text=self.t["stopped"])
        self.detail_label.configure(text=self.t["click_start"])
        try:
            cfg = normalized_config()
            self.corner_var.set(self.corners[cfg["corner"]])
            self.solo_var.set(cfg["solo_only"])
            self.protect_var.set(cfg["protect_setwaypoint"])
        except Exception:
            pass

    def _set_active(self):
        self.status_label.configure(text=self.t["active"])
        self.detail_label.configure(text=self.t["gps_pending"].format(count=self._last_pending))

    def _handle_core_line(self, line):
        if not line:
            return

        if "HUD DIRECTX ATIVO" in line or "ABATES:" in line:
            match = re.search(r"ABATES:\s*(\d+)", line)
            if match:
                self._last_pending = int(match.group(1))
            self._set_active()
            self.activity_label.configure(text=self.t["connected"])
            return

        match = re.search(r"CADAVER GUARDADO #(\d+)", line)
        if match:
            self._last_pending = int(match.group(1))
            self._set_active()
            self.activity_label.configure(text=self.t["new_kill"].format(count=self._last_pending))
            return

        match = re.search(r"restantes=(\d+)", line)
        if match and "CADAVER COLETADO" in line:
            self._last_pending = int(match.group(1))
            self._set_active()
            self.activity_label.configure(text=self.t["collected"].format(count=self._last_pending))
            return

        if "GPS limpo" in line:
            self._last_pending = 0
            self._set_active()
            self.activity_label.configure(text=self.t["all_collected"])
            return

        if "PROTEÇÃO SOLO DESATIVADA" in line:
            self.activity_label.configure(text=self.t["mp_unlocked"])
            return
        if "PROTEÇÃO DE WAYPOINT ATIVA" in line:
            self.activity_label.configure(text=self.t["wp_on_activity"])
            return
        if "PROTEÇÃO DE WAYPOINT DESATIVADA" in line:
            self.activity_label.configure(text=self.t["wp_off_activity"])
            return
        if "WAYPOINT DO JOGADOR PROTEGIDO" in line:
            self.activity_label.configure(text=self.t["wp_respected"])
            return
        if "Waypoint do jogador limpo" in line:
            self.activity_label.configure(text=self.t["wp_cleared"])
            return
        if "AGUARDANDO JOGO" in line:
            self.status_label.configure(text=self.t["waiting_game"])
            self.detail_label.configure(text=self.t["open_game"])
            self.activity_label.configure(text=self.t["ready_waiting"])
            return
        if "JOGO DETECTADO" in line:
            self.status_label.configure(text=self.t["connecting"])
            self.detail_label.configure(text=self.t["game_detected"])
            self.activity_label.configure(text=self.t["game_found"])
            return
        if "MULTIPLAYER DETECTADO/BLOQUEADO" in line:
            self.status_label.configure(text=self.t["blocked"])
            self.detail_label.configure(text=self.t["solo_blocked"])
            self.activity_label.configure(text=self.t["safe_disconnect"])
            return
        if "ERRO" in line or "falhou" in line.lower():
            self.status_label.configure(text=self.t["attention"])
            self.detail_label.configure(text=self.t["problem"])
            self.activity_label.configure(text=line.split("] ", 1)[-1])

    def _poll_output(self):
        try:
            while True:
                kind, payload = self.output_queue.get_nowait()
                if kind == "core":
                    self._handle_core_line(payload)
                elif kind == "gui" and payload == "STARTED":
                    self.status_label.configure(text=self.t["connecting"])
                    self.detail_label.configure(text=self.t["waiting_connect"])
                elif kind == "gui_error":
                    self.status_label.configure(text=self.t["error"])
                    self.detail_label.configure(text=payload)
                    self.activity_label.configure(text=payload)
                    self._set_controls_running(False)
                    messagebox.showerror("Turbo Hunter", payload)
                elif kind == "repair":
                    self._set_controls_running(False)
                    self.status_label.configure(text=self.t["error"])
                    self.detail_label.configure(text=payload)
                    if messagebox.askyesno("Turbo Hunter", payload + "\n\n" + self.t["repair_question"]):
                        if self._open_repair():
                            self.after(300, self.destroy)
        except queue.Empty:
            pass

        if not self.closing:
            self.after(100, self._poll_output)

    def _poll_process(self):
        if self.process is not None and self.process.poll() is not None:
            code = self.process.returncode
            self._finish_stopped()
            if code not in (0, None) and not self.closing:
                self.activity_label.configure(text=self.t["ended_log"])
        if not self.closing:
            self.after(500, self._poll_process)

    def on_close(self):
        self.closing = True
        if self.process is not None and self.process.poll() is None:
            try:
                STOP_FILE.write_text("stop", encoding="ascii")
            except Exception:
                pass
            deadline = time.time() + 1.5
            while self.process.poll() is None and time.time() < deadline:
                try:
                    self.update_idletasks()
                except Exception:
                    break
                time.sleep(0.05)
            if self.process.poll() is None:
                try:
                    self.process.terminate()
                except Exception:
                    pass
        self.destroy()


if __name__ == "__main__":
    app = TurboHunterGUI()
    if "--autostart" in sys.argv:
        app.after(300, app.start_mod)
    app.mainloop()
