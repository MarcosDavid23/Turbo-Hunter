# Turbo Hunter 0.5.7
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

VERSION = "0.5.7"
CONFIG_VERSION = "0.5.7"
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
MEMORY_SCAN_FILE = BASE_DIR / ".turbo_hunter_memory_scan"
REPAIR_CMD = INTERNAL_DIR / "installer" / "REINSTALAR TURBO HUNTER.cmd"
DEER_PNG = ASSETS_DIR / "turbo_hunter_deer.png"
ICON_PNG = DEER_PNG

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_SINGLE_INSTANCE_HANDLE = None


def ensure_single_instance():
    """Ignore a second launcher silently so two labs cannot alter one test."""
    global _SINGLE_INSTANCE_HANDLE
    if sys.platform != "win32":
        return
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(
            None,
            False,
            "Local\\TurboHunterOfficial054",
        )
        if handle and kernel32.GetLastError() == 183:
            kernel32.CloseHandle(handle)
            raise SystemExit(0)
        _SINGLE_INSTANCE_HANDLE = handle
    except SystemExit:
        raise
    except Exception:
        pass


ensure_single_instance()

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
    "zh-CN": ("左上角", "右上角", "左下角", "右下角"),
}

LANGUAGE_NAMES = {
    "pt-BR": "Português (Brasil)",
    "en": "English",
    "zh-CN": "简体中文 (Chinês)",
}

TEXT = {
    "pt-BR": {
        "subtitle": "Abates e marcação automática • theHunter: Call of the Wild",
        "status": "STATUS",
        "stopped": "PARADO",
        "click_start": "Clique em INICIAR. O Turbo Hunter pode esperar o jogo abrir.",
        "test_title": "NO JOGO",
        "test_steps": (
            "Cace normalmente. O GPS procura e marca o corpo mais próximo "
            "assim que a posição estiver disponível.\n"
            "Se faltar um abate, aperte F6 uma vez. Se ainda faltar, "
            "examine a pista de sangue do animal."
        ),
        "gps_manual_needed": (
            "Analisando o mapa para criar a marcação automaticamente. "
            "Se aparecer ERRO DE MARCAÇÃO, clique PARAR e envie o ZIP."
        ),
        "config": "CONFIGURAÇÃO",
        "hud_corner": "Canto do HUD",
        "solo_protection": "Proteção contra multiplayer",
        "solo_help_on": "Ativada: funciona somente no modo SOLO e bloqueia partidas multiplayer.",
        "solo_help_off": "Desativada: permite jogar com outras pessoas por conta e risco.",
        "waypoint_protection": "Proteger minha marcação no mapa",
        "waypoint_help_on": "Ativada: não mexe na sua marcação. Aguarda você apagá-la.",
        "waypoint_help_off": "Desativada: pode mover a marcação para indicar o animal mais próximo.",
        "start": "INICIAR",
        "stop": "PARAR",
        "scan_button": "PROCURAR NOVAMENTE (F6 / NUMPAD *)",
        "activity": "ATIVIDADE",
        "no_activity": "Nenhuma ação necessária. Pode continuar caçando.",
        "keys": "F6 / * recuperação • F8 canto • F9 HUD",
        "language_label": "Idioma / Language / 语言",
        "see_clue": "Se faltar um animal, examine a pista de sangue dele.",
        "send_zip": "Ocorreu um erro. Clique em PARAR e envie o ZIP.",
        "scan_requested": "Nova busca iniciada. Aguarde ANALISANDO desaparecer no HUD.",
        "scan_not_running": "Inicie o Turbo Hunter antes de usar F6.",
        "scan_request_failed": "Não foi possível enviar o comando de validação: ",
        "core_missing": "Arquivo principal do Turbo Hunter não foi encontrado.",
        "starting": "INICIANDO",
        "preparing_waiting": "Preparando e aguardando o jogo...",
        "preparing": "Preparando o Turbo Hunter.",
        "components_missing": "Os componentes do Turbo Hunter estão ausentes ou danificados.",
        "repair_question": "Deseja reparar a instalação agora?",
        "repair_failed": "Não foi possível abrir o reparo da instalação.",
        "launch_failed": "Não foi possível iniciar o Turbo Hunter: ",
        "stopping": "ENCERRANDO",
        "cleaning": "Encerrando o GPS e desconectando do jogo...",
        "active": "ATIVO",
        "gps_pending": "GPS funcionando • {count} abate(s) pendente(s)",
        "connected": "Turbo Hunter conectado ao jogo.",
        "searching_previous": "Procurando abates anteriores...",
        "previous_found": "Abates anteriores encontrados: {count}.",
        "deep_capture": "Analisando vivos, mortos e coletados. Aguarde.",
        "scan_finished": "Busca limpa concluída. Colete em qualquer ordem.",
        "collection_ready": "Abates localizados; confirme a contagem ao coletar.",
        "collection_wait": "ANALISANDO: aguarde as duas leituras da memória.",
        "collection_done": "Análise concluída. Pode continuar caçando; PARAR gera o ZIP.",
        "lab_error_zip": "Ocorreu um erro. Clique em PARAR e envie o ZIP de diagnóstico.",
        "package_ready": "Pacote completo criado. Envie o ZIP mais recente.",
        "new_kill": "Novo abate registrado. Pendentes: {count}.",
        "collected": "Animal coletado. Restam {count}.",
        "all_collected": "Todos os animais pendentes foram coletados.",
        "mp_unlocked": "Proteção contra multiplayer desativada, por conta e risco.",
        "wp_on_activity": "Sua marcação está protegida. O GPS aguarda você apagá-la.",
        "wp_off_activity": "Proteção da marcação desativada. O Turbo Hunter pode movê-la automaticamente.",
        "wp_respected": "Seu waypoint foi respeitado. Limpe o point no jogo para liberar o GPS automático.",
        "wp_cleared": "Waypoint limpo. GPS automático liberado novamente.",
        "waiting_game": "AGUARDANDO JOGO",
        "open_game": "Abra o theHunter: Call of the Wild. A conexão será automática.",
        "ready_waiting": "Turbo Hunter pronto e esperando o jogo abrir.",
        "connecting": "CONECTANDO",
        "game_detected": "Jogo detectado. Preparando GPS e HUD...",
        "game_found": "theHunter encontrado.",
        "blocked": "BLOQUEADO",
        "solo_blocked": "A proteção contra multiplayer bloqueou esta sessão.",
        "safe_disconnect": "Turbo Hunter desconectou por segurança.",
        "unsupported_build": "O jogo foi atualizado. O Turbo Hunter entrou no modo compatível.",
        "build_disconnect": "Nenhuma trava foi aplicada; recursos alterados serão registrados no ZIP.",
        "connection_problem": "O jogo foi encontrado, mas a conexão não foi autorizada.",
        "connection_help": "Clique em PARAR e envie o ZIP de diagnóstico.",
        "attention": "ATENÇÃO",
        "problem": "O Turbo Hunter encontrou um problema. Consulte os logs se necessário.",
        "error": "ERRO",
        "waiting_connect": "Aguardando o Turbo Hunter conectar ao jogo...",
        "ended_log": "O Turbo Hunter foi encerrado. Se houve problema, consulte os arquivos de log.",
    },
    "en": {
        "subtitle": "Kills and automatic waypoints • theHunter: Call of the Wild",
        "status": "STATUS",
        "stopped": "STOPPED",
        "click_start": "Click START. Turbo Hunter can wait for the game to open.",
        "test_title": "IN GAME",
        "test_steps": (
            "Hunt normally. GPS finds and marks the nearest body as soon "
            "as your position is available.\n"
            "If a kill is missing, press F6 once. If it is still missing, "
            "inspect that animal’s blood clue."
        ),
        "gps_manual_needed": (
            "Scanning the map to create the waypoint automatically. "
            "If MARKER ERROR appears, click STOP and send the ZIP."
        ),
        "config": "SETTINGS",
        "hud_corner": "HUD corner",
        "solo_protection": "Multiplayer protection",
        "solo_help_on": "Enabled: works only in SOLO mode and blocks multiplayer sessions.",
        "solo_help_off": "Disabled: allows playing with other people at your own risk.",
        "waypoint_protection": "Protect my map marker",
        "waypoint_help_on": "Enabled: does not move your marker. It waits until you clear it.",
        "waypoint_help_off": "Disabled: may move the marker to show the nearest animal.",
        "start": "START",
        "stop": "STOP",
        "scan_button": "SEARCH AGAIN (F6 / NUMPAD *)",
        "activity": "ACTIVITY",
        "no_activity": "No action needed. Keep hunting.",
        "keys": "F6 / * recovery • F8 corner • F9 HUD",
        "language_label": "Idioma / Language / 语言",
        "see_clue": "If an animal is missing, inspect its blood clue.",
        "send_zip": "An error occurred. Click STOP and send the ZIP.",
        "scan_requested": "Command sent. Wait until SEARCHING KILLS leaves the HUD.",
        "scan_not_running": "Start Turbo Hunter before pressing F6.",
        "scan_request_failed": "The ID validation command could not be sent: ",
        "core_missing": "The main Turbo Hunter file was not found.",
        "starting": "STARTING",
        "preparing_waiting": "Preparing and waiting for the game...",
        "preparing": "Preparing Turbo Hunter.",
        "components_missing": "Turbo Hunter components are missing or damaged.",
        "repair_question": "Repair the installation now?",
        "repair_failed": "The installation repair could not be opened.",
        "launch_failed": "Turbo Hunter could not be started: ",
        "stopping": "STOPPING",
        "cleaning": "Stopping GPS and disconnecting from the game...",
        "active": "ACTIVE",
        "gps_pending": "GPS active • {count} pending kill(s)",
        "connected": "Turbo Hunter connected to the game.",
        "searching_previous": "Searching for previous kills...",
        "previous_found": "Previous kills found: {count}.",
        "deep_capture": "Validating IDs and available bodies. Please wait.",
        "scan_finished": "ID validation completed.",
        "collection_ready": "Animals located; check the count while harvesting.",
        "collection_wait": "Exact harvest detected. Wait 15 seconds.",
        "collection_done": "Analysis complete. Keep hunting; STOP creates the ZIP.",
        "lab_error_zip": "An error occurred. Click STOP and send the diagnostic ZIP.",
        "package_ready": "Complete package created. Send the newest ZIP.",
        "new_kill": "New kill registered. Pending: {count}.",
        "collected": "Animal collected. {count} remaining.",
        "all_collected": "All pending animals were collected.",
        "mp_unlocked": "Multiplayer protection is disabled, at your own risk.",
        "wp_on_activity": "Your marker is protected. GPS waits until you clear it.",
        "wp_off_activity": "Marker protection is disabled. Turbo Hunter may move it automatically.",
        "wp_respected": "Your waypoint was respected. Clear it in-game to release automatic GPS.",
        "wp_cleared": "Waypoint cleared. Automatic GPS is available again.",
        "waiting_game": "WAITING FOR GAME",
        "open_game": "Open theHunter: Call of the Wild. Connection will be automatic.",
        "ready_waiting": "Turbo Hunter is ready and waiting for the game.",
        "connecting": "CONNECTING",
        "game_detected": "Game detected. Preparing GPS and HUD...",
        "game_found": "theHunter found.",
        "blocked": "BLOCKED",
        "solo_blocked": "Multiplayer protection blocked this session.",
        "safe_disconnect": "Turbo Hunter disconnected for safety.",
        "unsupported_build": "The game was updated. Turbo Hunter entered compatibility mode.",
        "build_disconnect": "No lock was applied; changed features will be recorded in the ZIP.",
        "connection_problem": "The game was found, but the connection was not authorized.",
        "connection_help": "Click STOP and send the diagnostic ZIP.",
        "attention": "ATTENTION",
        "problem": "Turbo Hunter found a problem. Check the logs if needed.",
        "error": "ERROR",
        "waiting_connect": "Waiting for Turbo Hunter to connect to the game...",
        "ended_log": "Turbo Hunter stopped. If there was a problem, check the log files.",
    },
    "zh-CN": {
        "subtitle": "狩猎计数与自动地图标记 • theHunter: Call of the Wild",
        "status": "状态", "stopped": "已停止",
        "click_start": "点击开始。程序可以等待游戏启动。",
        "config": "选项", "hud_corner": "显示位置",
        "language_label": "Idioma / Language / 语言",
        "see_clue": "如有猎物未找到，请检查它的血迹线索。",
        "send_zip": "发生错误。请点击停止并发送诊断 ZIP。",
        "solo_protection": "多人游戏保护",
        "solo_help_on": "开启：仅在单人模式运行。",
        "solo_help_off": "关闭：多人游戏可能使账号承担风险。",
        "waypoint_protection": "保护我的地图标记",
        "waypoint_help_on": "开启：不会移动你创建的标记。",
        "waypoint_help_off": "关闭：标记会自动指向最近的动物。",
        "start": "开始", "stop": "停止", "scan_button": "重新搜索（F6 / 数字键盘 *）",
        "activity": "活动", "no_activity": "无需操作，可以继续狩猎。",
        "keys": "F6 / * 恢复 • F8 位置 • F9 显示",
        "scan_requested": "已开始重新搜索，请稍候。",
        "scan_not_running": "请先启动 Turbo Hunter。",
        "scan_request_failed": "无法启动搜索：",
        "core_missing": "找不到 Turbo Hunter 主程序。",
        "starting": "正在启动", "preparing_waiting": "正在准备并等待游戏启动…",
        "preparing": "正在准备 Turbo Hunter。",
        "components_missing": "Turbo Hunter 组件缺失或损坏。",
        "repair_question": "现在修复安装吗？", "repair_failed": "无法启动安装修复。",
        "launch_failed": "无法启动 Turbo Hunter：",
        "stopping": "正在停止", "cleaning": "正在断开游戏连接…",
        "active": "运行中", "gps_pending": "正在标记 • 待收集：{count}",
        "connected": "已连接游戏。", "searching_previous": "正在搜索之前的猎物…",
        "previous_found": "已找到之前的猎物：{count}。",
        "deep_capture": "正在检查猎物，请稍候。",
        "scan_finished": "搜索完成。", "collection_ready": "已找到猎物，可以收集。",
        "collection_wait": "正在确认收集，请稍候。",
        "collection_done": "检查完成，可以继续狩猎。",
        "lab_error_zip": "发生错误。请点击停止并发送诊断 ZIP。",
        "package_ready": "已生成诊断 ZIP，请发送最新文件。",
        "new_kill": "新增猎物。待收集：{count}。",
        "collected": "已收集。剩余：{count}。",
        "all_collected": "已收集全部猎物。",
        "mp_unlocked": "多人游戏保护已关闭，风险由玩家承担。",
        "wp_on_activity": "已保护你的地图标记。",
        "wp_off_activity": "地图标记可自动移动。",
        "wp_respected": "你的地图标记受到保护，清除后可自动标记。",
        "wp_cleared": "地图标记已清除，自动标记已恢复。",
        "waiting_game": "等待游戏", "open_game": "启动游戏后将自动连接。",
        "ready_waiting": "程序已就绪，正在等待游戏。",
        "connecting": "正在连接", "game_detected": "已找到游戏，正在准备…",
        "game_found": "已找到游戏。", "blocked": "已阻止",
        "solo_blocked": "多人游戏保护阻止了本次连接。",
        "safe_disconnect": "已安全断开连接。",
        "unsupported_build": "游戏已更新，正在使用兼容模式。",
        "build_disconnect": "更改的功能会记录在诊断 ZIP 中。",
        "connection_problem": "已找到游戏，但连接失败。",
        "connection_help": "点击停止并发送诊断 ZIP。",
        "attention": "注意", "problem": "程序遇到问题，请查看日志。",
        "error": "错误", "waiting_connect": "正在等待游戏连接…",
        "ended_log": "程序已停止。如遇问题，请查看日志。",
    },
}


def detect_windows_language():
    if sys.platform == "win32":
        try:
            buffer = ctypes.create_unicode_buffer(85)
            if ctypes.windll.kernel32.GetUserDefaultLocaleName(buffer, len(buffer)):
                name = buffer.value
                return "pt-BR" if name.lower().startswith("pt") else "zh-CN" if name.lower().startswith("zh") else "en"
        except Exception:
            pass
    try:
        name = (locale.getlocale()[0] or "").lower()
        return "pt-BR" if name.startswith("pt") else "zh-CN" if name.startswith("zh") else "en"
    except Exception:
        return "en"


def resolve_language(value):
    value = str(value or "auto").strip()
    if value.lower() == "auto":
        return detect_windows_language()
    if value.lower().startswith("pt"):
        return "pt-BR"
    if value.lower().startswith("zh"):
        return "zh-CN"
    return "en"


def normalized_config():
    data = {
        "corner": 3,
        "name": CORNERS["pt-BR"][3],
        "solo_only": 1,
        "protect_setwaypoint": 0,
        "language": "auto",
        "lab_config_version": CONFIG_VERSION,
    }
    loaded_version = ""
    try:
        loaded = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            loaded_version = str(loaded.get("lab_config_version", ""))
            data.update(loaded)
    except Exception:
        pass

    # Instalação nova: proteção ativa; escolha explícita anterior é preservada.

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
        protect = 1 if int(data.get("protect_setwaypoint", 0)) != 0 else 0
    except Exception:
        protect = 0

    language = str(data.get("language", "auto") or "auto")
    if language.lower() not in ("auto", "en", "pt-br", "pt_br", "pt", "zh-cn", "zh_cn", "zh"):
        language = "auto"

    ui_lang = resolve_language(language)
    return {
        "corner": corner,
        "name": CORNERS[ui_lang][corner],
        "solo_only": solo_only,
        "protect_setwaypoint": protect,
        "language": language,
        "lab_config_version": CONFIG_VERSION,
    }


def write_config(config):
    CONFIG_FILE.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def python_for_subprocess():
    # A interface usa pythonw.exe (sem console); o núcleo usa python.exe
    # com stdout redirecionado e CREATE_NO_WINDOW para manter o log íntegro.
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        console_python = executable.with_name("python.exe")
        if console_python.is_file():
            return str(console_python)
    return str(executable)


class ActivityFeed(ttk.Frame):
    """Small scrollable history of the messages already shown in Activity."""

    def __init__(self, parent, initial):
        super().__init__(parent, style="Card.TFrame")
        self.message = None
        self.view = tk.Text(
            self, height=6, wrap="word", bg="#181e22", fg="#b8c1c6",
            font=("Segoe UI", 9), relief="flat", borderwidth=0,
            highlightthickness=0, cursor="arrow", padx=2, pady=4,
        )
        bar = ttk.Scrollbar(self, orient="vertical", command=self.view.yview)
        self.view.configure(yscrollcommand=bar.set, state="disabled")
        bar.pack(side="right", fill="y")
        self.view.pack(side="left", fill="both", expand=True)
        self.configure(text=initial)

    def configure(self, cnf=None, **kwargs):
        message = kwargs.pop("text", None)
        if message is not None:
            message = str(message).strip()
            if message and message != self.message:
                self.message = message
                follow_latest = self.view.yview()[1] >= 0.98
                self.view.configure(state="normal")
                self.view.insert("end", time.strftime("%H:%M:%S") + "  " + message + "\n")
                if int(self.view.index("end-1c").split(".")[0]) > 140:
                    self.view.delete("1.0", "21.0")
                self.view.configure(state="disabled")
                if follow_latest:
                    self.view.see("end")
        if cnf is not None or kwargs:
            return super().configure(cnf, **kwargs)


class TurboHunterGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.process = None
        self.output_queue = queue.Queue()
        self.closing = False
        self._close_deadline = 0.0
        self._last_pending = 0
        self._activity_hold_until = 0.0
        self._memory_scan_active = False
        self.config_data = normalized_config()
        self.lang = resolve_language(self.config_data.get("language", "auto"))
        self.t = TEXT[self.lang]
        self.corners = CORNERS[self.lang]
        self.config_data["name"] = self.corners[self.config_data["corner"]]
        write_config(self.config_data)

        self.title(f"Turbo Hunter {VERSION}")
        self.geometry("640x760")
        self.minsize(590, 620)
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
        style.configure("Scan.TButton", font=("Segoe UI", 11, "bold"), padding=(18, 10), background="#cf7621", foreground="#181818", borderwidth=0)
        style.map("Scan.TButton", background=[("active", "#eb9130"), ("pressed", "#b96318"), ("disabled", "#5c5146")], foreground=[("disabled", "#a9a39d")])

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

        language_row = ttk.Frame(config_card, style="Card.TFrame")
        language_row.pack(fill="x", pady=(10, 8))
        ttk.Label(language_row, text=self.t["language_label"], style="CardText.TLabel").pack(side="left")
        self.language_var = tk.StringVar(value=LANGUAGE_NAMES[self.lang])
        self.language_combo = ttk.Combobox(language_row, state="readonly", values=list(LANGUAGE_NAMES.values()), textvariable=self.language_var, width=20)
        self.language_combo.pack(side="right")
        self.language_combo.bind("<<ComboboxSelected>>", self._change_language)

        row = ttk.Frame(config_card, style="Card.TFrame")
        row.pack(fill="x", pady=(10, 8))
        ttk.Label(row, text=self.t["hud_corner"], style="CardText.TLabel").pack(side="left")
        self.corner_var = tk.StringVar(value=self.corners[self.config_data["corner"]])
        self.corner_combo = ttk.Combobox(row, state="readonly", values=self.corners, textvariable=self.corner_var, width=23)
        self.corner_combo.pack(side="right")

        self.solo_var = tk.IntVar(value=self.config_data["solo_only"])
        self.solo_check = ttk.Checkbutton(
            config_card,
            text=self.t["solo_protection"],
            variable=self.solo_var,
            command=self._refresh_option_help,
        )
        self.solo_check.pack(anchor="w", pady=(4, 0))
        self.solo_help_label = ttk.Label(
            config_card,
            style="CardText.TLabel",
            wraplength=525,
            justify="left",
        )
        self.solo_help_label.pack(anchor="w", padx=(22, 0))

        self.protect_var = tk.IntVar(value=self.config_data["protect_setwaypoint"])
        self.protect_check = ttk.Checkbutton(
            config_card,
            text=self.t["waypoint_protection"],
            variable=self.protect_var,
            command=self._refresh_option_help,
        )
        self.protect_check.pack(anchor="w", pady=(10, 0))
        self.waypoint_help_label = ttk.Label(
            config_card,
            style="CardText.TLabel",
            wraplength=525,
            justify="left",
        )
        self.waypoint_help_label.pack(anchor="w", padx=(22, 0))
        self._refresh_option_help()

        buttons = ttk.Frame(root, style="Root.TFrame")
        buttons.pack(fill="x", pady=(0, 12))
        self.start_button = ttk.Button(buttons, text=self.t["start"], style="Start.TButton", command=self.start_mod)
        self.start_button.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.stop_button = ttk.Button(buttons, text=self.t["stop"], style="Stop.TButton", command=self.stop_mod, state="disabled")
        self.stop_button.pack(side="left", fill="x", expand=True, padx=(6, 0))

        self.scan_button = ttk.Button(
            root,
            text=self.t["scan_button"],
            style="Scan.TButton",
            command=self.request_memory_scan,
            state="disabled",
        )
        self.scan_button.pack(fill="x", pady=(0, 12))

        activity = ttk.Frame(root, style="Card.TFrame", padding=14)
        activity.pack(fill="both", expand=True)
        ttk.Label(activity, text=self.t["activity"], style="CardTitle.TLabel").pack(anchor="w")
        self.activity_label = ActivityFeed(activity, self.t["no_activity"])
        self.activity_label.pack(fill="both", expand=True, pady=(7, 8))
        ttk.Label(activity, text=self.t["keys"], style="CardText.TLabel").pack(anchor="w")

    def _change_language(self, _event):
        if self.process is not None and self.process.poll() is None:
            return
        selected = next((key for key, label in LANGUAGE_NAMES.items()
                         if label == self.language_var.get()), self.lang)
        if selected == self.lang:
            return
        self._save_ui_config()
        self.config_data["language"] = selected
        self.config_data["name"] = CORNERS[selected][self.config_data["corner"]]
        write_config(self.config_data)
        self.lang = selected
        self.t = TEXT[selected]
        self.corners = CORNERS[selected]
        for child in self.winfo_children():
            child.destroy()
        self.title(f"Turbo Hunter {VERSION}")
        self._build_ui()

    def _refresh_option_help(self):
        solo_key = "solo_help_on" if self.solo_var.get() else "solo_help_off"
        waypoint_key = "waypoint_help_on" if self.protect_var.get() else "waypoint_help_off"
        self.solo_help_label.configure(text=self.t[solo_key])
        self.waypoint_help_label.configure(text=self.t[waypoint_key])

    def _set_controls_running(self, running):
        self.start_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")
        self.scan_button.configure(state="normal" if running else "disabled")
        self.corner_combo.configure(state="disabled" if running else "readonly")
        self.language_combo.configure(state="disabled" if running else "readonly")
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
            "lab_config_version": CONFIG_VERSION,
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
            for trigger_file in (STOP_FILE, MEMORY_SCAN_FILE):
                if trigger_file.exists():
                    trigger_file.unlink()
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
        child_env["PYTHONIOENCODING"] = "utf-8"
        child_env["PYTHONUTF8"] = "1"

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
        if not REPAIR_CMD.exists():
            messagebox.showerror("Turbo Hunter", self.t["repair_failed"])
            return False
        try:
            # O reparo mostra cada etapa na própria janela CMD do instalador.
            os.startfile(str(REPAIR_CMD))
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

    def request_memory_scan(self):
        if self.process is None or self.process.poll() is not None:
            self.activity_label.configure(text=self.t["scan_not_running"])
            return

        try:
            MEMORY_SCAN_FILE.write_text("scan", encoding="ascii")
            self._memory_scan_active = True
            self._activity_hold_until = time.monotonic() + 15.0
            self.activity_label.configure(text=self.t["scan_requested"])
        except Exception as exc:
            self._memory_scan_active = False
            messagebox.showerror(
                "Turbo Hunter",
                self.t["scan_request_failed"] + str(exc),
            )

    def _finish_stopped(self):
        self.process = None
        self._memory_scan_active = False
        try:
            if MEMORY_SCAN_FILE.exists():
                MEMORY_SCAN_FILE.unlink()
        except Exception:
            pass
        self._set_controls_running(False)
        self.status_label.configure(text=self.t["stopped"])
        self.detail_label.configure(text=self.t["click_start"])
        try:
            cfg = normalized_config()
            self.corner_var.set(self.corners[cfg["corner"]])
            self.solo_var.set(cfg["solo_only"])
            self.protect_var.set(cfg["protect_setwaypoint"])
            self._refresh_option_help()
        except Exception:
            pass

    def _set_active(self):
        self.status_label.configure(text=self.t["active"])
        self.detail_label.configure(text=self.t["gps_pending"].format(count=self._last_pending))

    def _handle_core_line(self, line):
        if not line:
            return

        if "🟢 LAB:" in line:
            self._memory_scan_active = False
            self._activity_hold_until = 0.0
            self.activity_label.configure(text=self.t["no_activity"])
            self.status_label.configure(text=self.t["active"])
            return

        if "🟠 LAB:" in line:
            message = line.split("🟠 LAB:", 1)[-1].strip()
            stop_needed = any(token in message for token in ("HORA DE PARAR", "ENVIAR ZIP", "ENVIE O ZIP"))
            if self.lang != "pt-BR":
                if stop_needed:
                    message = self.t["send_zip"]
                elif "PISTA" in message:
                    message = self.t["see_clue"]
                else:
                    message = self.t["lab_error_zip"]
            # Uma orientação continua visível até a etapa acabar ou mudar.
            self._activity_hold_until = float("inf")
            self.activity_label.configure(text=message)
            if stop_needed:
                self.status_label.configure(text=self.t["stopping"])
            else:
                self.status_label.configure(text=self.t["active"])
            return

        if "DEPURAÇÃO PRONTA:" in line:
            self._activity_hold_until = time.monotonic() + 300.0
            self.activity_label.configure(text=self.t["collection_ready"])
            return

        if "DEPURAÇÃO: coleta exata detectada" in line:
            self._activity_hold_until = time.monotonic() + 20.0
            self.activity_label.configure(text=self.t["collection_wait"])
            return

        if "DEPURAÇÃO CONCLUÍDA" in line:
            self._activity_hold_until = time.monotonic() + 300.0
            self.activity_label.configure(text=self.t["collection_done"])
            return

        if ("ID VIVO: validação iniciada" in line or
                "ID VIVO: confirmando" in line):
            self._memory_scan_active = True
            self.activity_label.configure(text=self.t["deep_capture"])
            return

        if ("ID VIVO:" in line and "disponível(is)" in line):
            self._memory_scan_active = False
            self.activity_label.configure(text=self.t["scan_finished"])
            return

        if "ID VIVO:" in line:
            self._memory_scan_active = False
            self.activity_label.configure(text=line.split("] ", 1)[-1])
            return

        if "PACOTE DE LOG CRIADO" in line:
            self.activity_label.configure(text=self.t["package_ready"])
            return

        match = re.search(
            r"BUSCA LIMPA:\s*(\d+) cadáver\(es\) disponível",
            line,
        )
        if match:
            self._last_pending = int(match.group(1))
            self._memory_scan_active = False
            self._set_active()
            return

        if "procurando cadáveres sem histórico" in line:
            self._memory_scan_active = True
            self.activity_label.configure(text=self.t["deep_capture"])
            return

        if "PROCURANDO ABATES ANTERIORES" in line:
            self._activity_hold_until = time.monotonic() + 60.0
            self.activity_label.configure(text=self.t["searching_previous"])
            return

        match = re.search(r"ABATES ANTERIORES ENCONTRADOS:\s*(\d+)", line)
        if match:
            self._last_pending = int(match.group(1))
            self._set_active()
            return

        if "TURBO HUNTER PRONTO" in line:
            self._activity_hold_until = 0.0
            self._set_active()
            self.activity_label.configure(text=self.t["no_activity"])
            return

        if "HUD DIRECTX ATIVO" in line or "ABATES:" in line:
            match = re.search(r"ABATES:\s*(\d+)", line)
            if match:
                self._last_pending = int(match.group(1))
            self._set_active()
            if (not self._memory_scan_active and
                    time.monotonic() >= self._activity_hold_until):
                self.activity_label.configure(text=self.t["no_activity"])
            return

        match = re.search(r"CADAVER GUARDADO #(\d+)", line)
        if match:
            self._last_pending = int(match.group(1))
            self._set_active()
            return

        match = re.search(r"restantes=(\d+)", line)
        if match and ("CADAVER COLETADO" in line or
                      "COLETA CONFIRMADA PELO ID" in line):
            self._last_pending = int(match.group(1))
            self._set_active()
            return

        if "GPS limpo" in line:
            # Limpar uma marcação não significa que a fila de abates zerou.
            return

        if "PROTEÇÃO SOLO DESATIVADA" in line:
            return
        if "PROTEÇÃO DE WAYPOINT ATIVA" in line:
            return
        if "PROTEÇÃO DE WAYPOINT DESATIVADA" in line:
            return
        if "WAYPOINT DO JOGADOR PROTEGIDO" in line:
            return
        if "Waypoint do jogador limpo" in line:
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
        if (
            "JOGO ATUALIZADO" in line
            or "BUILD NAO SUPORTADA" in line
            or "BUILD NÃO SUPORTADA" in line
        ):
            self.status_label.configure(text=self.t["attention"])
            self.detail_label.configure(text=self.t["unsupported_build"])
            self.activity_label.configure(text=self.t["build_disconnect"])
            return
        if "ERRO DE CONEXAO" in line:
            self.status_label.configure(text=self.t["attention"])
            self.detail_label.configure(text=self.t["connection_problem"])
            self.activity_label.configure(text=self.t["connection_help"])
            return
        if "FRIDA ERRO" in line or "ERRO:" in line or "❌" in line:
            self.status_label.configure(text=self.t["attention"])
            self.detail_label.configure(text=self.t["problem"])
            self.activity_label.configure(text=self.t["lab_error_zip"])

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
        if self.closing:
            return

        self.closing = True
        try:
            if MEMORY_SCAN_FILE.exists():
                MEMORY_SCAN_FILE.unlink()
        except Exception:
            pass

        if self.process is not None and self.process.poll() is None:
            self.status_label.configure(text=self.t["stopping"])
            self.detail_label.configure(text=self.t["cleaning"])
            try:
                STOP_FILE.write_text("stop", encoding="ascii")
            except Exception:
                try:
                    self.process.terminate()
                except Exception:
                    pass
                self.destroy()
                return

            # Aguarda o ZIP sem bloquear a fila de eventos do Tk. Assim a
            # janela continua respondendo enquanto o laboratório termina.
            self._close_deadline = time.monotonic() + 8.0
            self.after(50, self._poll_close_shutdown)
            return

        self.destroy()

    def _poll_close_shutdown(self):
        if self.process is None or self.process.poll() is not None:
            self.destroy()
            return

        if time.monotonic() >= self._close_deadline:
            try:
                self.process.terminate()
            except Exception:
                pass
            self.destroy()
            return

        self.after(100, self._poll_close_shutdown)


if __name__ == "__main__":
    app = TurboHunterGUI()
    if "--autostart" in sys.argv:
        app.after(300, app.start_mod)
    app.mainloop()
