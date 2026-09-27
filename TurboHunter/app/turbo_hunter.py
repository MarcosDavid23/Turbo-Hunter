# Turbo Hunter 0.5.7 - Waypoint automático e coleta por DNA
# theHunter: Call of the Wild
# Kill Locator / Localizador de Abates
# Configurações de segurança e waypoint: hud_config.json

import ctypes
import json
import locale
import os
import platform
import sys
import time
import threading
import traceback
import zipfile
from datetime import datetime
from pathlib import Path


def configure_utf8_output():
    """Keep Windows pipes and consoles compatible with every log message."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def print_console(text):
    """A closed or limited console must never interrupt game event handling."""
    try:
        print(text, flush=True)
    except Exception:
        pass


configure_utf8_output()

try:
    import frida
except ImportError:
    print("ERRO: Frida nao instalado.")
    print('Use "INICIAR TURBO HUNTER.vbs" para reparar a instalação.')
    input("ENTER para sair...")
    raise SystemExit(1)

PROCESS_NAME = "theHunterCotW_F.exe"
LOG_FILE = Path(__file__).with_name("turbo_hunter_log.txt")
PREVIOUS_LOG_FILE = Path(__file__).with_name("turbo_hunter_log_anterior.txt")
HUD_C_FILE = Path(__file__).with_name("hud_directx11.c")
HUD_CONFIG_FILE = Path(__file__).with_name("hud_config.json")
GUI_STOP_FILE = Path(__file__).with_name(".turbo_hunter_stop")
MEMORY_SCAN_FILE = Path(__file__).with_name(".turbo_hunter_memory_scan")
LAB_VERSION = "0.5.7"
LAB_LIVE_FILE = Path(__file__).with_name("lab_id_vivo_live.jsonl")
LAB_PACKAGE_PREFIX = "TurboHunter_Log"
LEGACY_LAB_STATE_FILE = Path(__file__).with_name("lab_id_vivo_state.json")
_LAB_LOCAL_APPDATA = os.environ.get("LOCALAPPDATA", "").strip()
LAB_STATE_FILE = (
    Path(_LAB_LOCAL_APPDATA) / "TurboHunter" / "Laboratorio" /
    "lab_id_vivo_state.json"
    if _LAB_LOCAL_APPDATA else LEGACY_LAB_STATE_FILE
)


def detect_machine_label():
    """Create a readable filename label without any manual configuration."""
    raw = str(platform.node() or "PC").strip().upper()
    clean = "".join(char if char.isalnum() else "_" for char in raw)
    clean = clean.strip("_")[:24]
    return clean or "PC"


MACHINE_LABEL = detect_machine_label()

stop_event = threading.Event()
expected_detach_event = threading.Event()
log_lock = threading.Lock()
lab_lock = threading.Lock()
harvest_state_lock = threading.Lock()
lab_session_id = "nao_iniciado"

JS = r"""
'use strict';

// Laboratório integrado: abates, coleta, posição e waypoint.
const POSITION_ONLY_LAB = false;
const PUBLIC_BETA = true;

const HUD_C_SOURCE = "__HUD_C_SOURCE_PLACEHOLDER__";
const SOLO_ONLY_PROTECTION = __SOLO_ONLY_PLACEHOLDER__;
const PROTECT_SETWAYPOINT = __PROTECT_SETWAYPOINT_PLACEHOLDER__;
const TARGET_GAME_BUILD = "3331010";

const BASE = Process.mainModule.base;
const GAME_IDENTITY = `${Process.id}|${BASE.toString()}`;

const RVA_SET_WAYPOINT      = 0x00BE53B0;
const RVA_CLEAR_WAYPOINT    = 0x00B9CB60;
// Capturado ao remover o ponto no mapa no laboratório 0.9.35. A assinatura
// completa abaixo deve bater nesta execução antes de usar o endereço.
const RVA_CAPTURED_CLEAR_WAYPOINT = 0x00B9CEB0;
const CAPTURED_CLEAR_SIGNATURE =
    "40534883ec30488b91a0030000488bd9c681d303000000" +
    "4533c0488b0d8f57c401e80a07fcff";
const RVA_MAP_SINGLETON     = 0x028086C0;
// Referência única ao objeto recebido pela função atual do mapa,
// observada no ZIP de 19/09. Sua validade é verificada em cada leitura.
const RVA_AUTO_MAP_SLOT     = 0x02803650;
const RVA_POI_REGISTRY_SLOT = 0x027E2660;

// 0x67CCF0 retorna diretamente o objeto usado pelo jogo em player+0x68.
const RVA_GET_PLAYER_ENTITY = 0x0067CCF0;

const SET_ADDR     = BASE.add(RVA_SET_WAYPOINT);
const CLEAR_ADDR   = BASE.add(RVA_CLEAR_WAYPOINT);
const CAPTURED_CLEAR_ADDR = BASE.add(RVA_CAPTURED_CLEAR_WAYPOINT);
const MAP_SLOT     = BASE.add(RVA_MAP_SINGLETON);
const AUTO_MAP_SLOT = BASE.add(RVA_AUTO_MAP_SLOT);
const GET_ENTITY   = BASE.add(RVA_GET_PLAYER_ENTITY);
const REFERENCE_REGISTRY_SLOT = BASE.add(RVA_POI_REGISTRY_SLOT);
let activeRegistrySlot = REFERENCE_REGISTRY_SLOT;
let activeMapSlot = MAP_SLOT;

function validateNativeRva(name, rva, address, requireExecute) {
    const moduleSize = Number(Process.mainModule.size);

    if (!Number.isFinite(moduleSize) || moduleSize <= 0 ||
        rva < 0 || rva >= moduleSize) {
        throw new Error(
            `BUILD/ENDERECO INCOMPATIVEL: ${name} fora do modulo principal ` +
            `(build suportada ${TARGET_GAME_BUILD}).`
        );
    }

    if (typeof Process.findRangeByAddress !== "function") {
        throw new Error(
            `BUILD/ENDERECO INCOMPATIVEL: Frida nao conseguiu validar ${name}.`
        );
    }

    const range = Process.findRangeByAddress(address);
    const protection = range ? String(range.protection || "") : "";

    if (!range || protection.indexOf("r") < 0 ||
        (requireExecute && protection.indexOf("x") < 0)) {
        throw new Error(
            `BUILD/ENDERECO INCOMPATIVEL: ${name} nao possui a protecao esperada ` +
            `(build suportada ${TARGET_GAME_BUILD}).`
        );
    }
}

function optionalNativeRva(name, rva, address, requireExecute) {
    try {
        validateNativeRva(name, rva, address, requireExecute);
        return true;
    } catch (error) {
        send({
            type:"log",
            text:"⚠️ MODO COMPATÍVEL: " + name +
                " mudou ou ficou indisponível após atualização do jogo; " +
                "o Turbo Hunter continuará sem bloquear. Detalhe: " +
                String(error)
        });
        return false;
    }
}

// O RVA anterior aponta para outro código nesta build. Não executá-lo.
const setWaypointAddressReady = false;
// O antigo ClearWaypoint também mudou. Deixar desligado até capturar a
// função atual; evita atribuir a um endereço qualquer a remoção do ponto.
const clearWaypointAddressReady = false;
// A atualização atual manteve código executável neste RVA, mas mudou a
// assinatura: chamá-lo como a função antiga causa access violation. O LAB
// continua funcional sem a posição do jogador e seleciona o próximo cadáver
// pelo catálogo até capturarmos o novo endereço com segurança.
const ENABLE_LEGACY_PLAYER_LOOKUP = false;
const getPlayerEntityAddressReady = ENABLE_LEGACY_PLAYER_LOOKUP &&
    optionalNativeRva(
        "GetPlayerEntity", RVA_GET_PLAYER_ENTITY, GET_ENTITY, true
    );
const mapSingletonAddressReady = optionalNativeRva(
    "MapSingleton", RVA_MAP_SINGLETON, MAP_SLOT, false
);
const autoMapSlotAddressReady = optionalNativeRva(
    "AutoMapSlot", RVA_AUTO_MAP_SLOT, AUTO_MAP_SLOT, false
);

// A busca inicial e um complemento. Se esta lista nao estiver disponivel,
// o rastreamento normal deve continuar funcionando sem bloqueio.
let oldCorpseSearchAddressReady = optionalNativeRva(
    "PoiRegistry", RVA_POI_REGISTRY_SLOT, REFERENCE_REGISTRY_SLOT, false
);

function optionalNativeFunction(name, ready, address, returnType, argumentTypes) {
    if (!ready)
        return null;
    try {
        return new NativeFunction(address, returnType, argumentTypes);
    } catch (error) {
        send({
            type:"log",
            text:"⚠️ MODO COMPATÍVEL: função " + name +
                " não pôde ser preparada; continuando sem bloquear. Detalhe: " +
                String(error)
        });
        return null;
    }
}

const SetWaypoint = optionalNativeFunction(
    "SetWaypoint", setWaypointAddressReady,
    SET_ADDR, 'void', ['pointer', 'pointer']
);
const ClearWaypoint = optionalNativeFunction(
    "ClearWaypoint", clearWaypointAddressReady,
    CLEAR_ADDR, 'void', ['pointer']
);
const GetPlayerEntity = optionalNativeFunction(
    "GetPlayerEntity", getPlayerEntityAddressReady,
    GET_ENTITY, 'pointer', []
);
const WAYPOINT_VECTOR = Memory.alloc(12);

const INTERNAL_HOOK_COOLDOWN_MS = 1200;
const EXTERNAL_HOOK_DEBOUNCE_MS = 800;
const EXTERNAL_REAPPLY_DELAY_MS = 5000;
const HARVEST_RECOVERED_MAX_M = 60;
const WEIGHT_TOLERANCE = 0.00001;
const RESERVE_CHANGE_CONFIRM_MS = 2000;
const HUD_WARNING_THRESHOLD = 20;
const HUD_WARNING_DURATION_MS = 10000;
const CURSOR_SHOWING = 0x00000001;
const OLD_CORPSE_TYPE = 39;
const OLD_CORPSE_DESCRIPTOR_SIZE = 0x100;
const OLD_CORPSE_MAX_SCAN = 4096;
// O waypoint manual apareceu no catálogo da build atual como tipo 10 com
// este ID fixo. Esse caminho independe do RVA antigo de SetWaypoint.
const MANUAL_WAYPOINT_TYPE = 10;
const MANUAL_WAYPOINT_REGISTRY_ID = "4886718345"; // 0x0000000123456789
// Escrever só as coordenadas do catálogo deixou o marcador visual na pista.
// A partir deste teste o GPS usa somente a função nativa validada do jogo.
const REGISTRY_WAYPOINT_FALLBACK = false;
const WAYPOINT_WRITER_MONITOR_MS = 45000;
const WAYPOINT_WRITER_MAX_ACCESSES = 96;
const WAYPOINT_VTABLE_FUNCTIONS = 32;
const WAYPOINT_ID_PATTERN = "89 67 45 23 01 00 00 00";
const WAYPOINT_ID_MAX_HITS = 64;
// O monitor de acesso e a varredura do waypoint foram introduzidos apenas
// para diagnóstico na 0.9.27. O jogo fechou logo após o primeiro acesso
// monitorado, portanto ambos ficam totalmente desativados neste teste seguro.
const WAYPOINT_WRITER_MONITOR_ENABLED = false;
const WAYPOINT_ID_CONSTANT_SCAN_ENABLED = true;
// Diagnóstico novo: observa a flag do mapa e o fim do catálogo por hardware.
// Não altera a proteção de páginas de memória e, portanto,
// não reutiliza o MemoryAccessMonitor que fechou o jogo na 0.9.27.
// Somente a captura dirigida da limpeza; nenhum monitor de página é usado.
const WAYPOINT_HARDWARE_WATCH_ENABLED = false;
const WAYPOINT_HARDWARE_WATCH_TIMEOUT_MS = 180000;
const WAYPOINT_HARDWARE_WATCH_MAX_HITS = 64;
const WAYPOINT_HARDWARE_WATCH_HEADER_ID = 0;
const WAYPOINT_HARDWARE_WATCH_TYPE_ID = 1;
const WAYPOINT_HARDWARE_WATCH_REGISTRY_ID = 2;
const REGISTRY_DISCOVERY_CHUNK_BYTES = 512 * 1024;
const REGISTRY_DISCOVERY_RETRY_MS = 10000;
const REGISTRY_DISCOVERY_MAX_ATTEMPTS = 8;
const REGISTRY_DISCOVERY_MAX_MODULE_BYTES = 256 * 1024 * 1024;
const REGISTRY_DISCOVERY_MAX_CANDIDATES = 64;
// Duas coletas forenses confirmadas possuíam neste campo uma ligação para
// o módulo principal do jogo. O registro fantasma capturado em 27/08 não
// possuía essa ligação, embora também fosse do tipo 39.
const OLD_CORPSE_OBJECT_LINK_OFFSET = 0xD8;
const INITIAL_SCAN_START_DELAY_MS = 2000;
const INITIAL_SCAN_INTERVAL_MS = 1000;
const INITIAL_SCAN_MAX_ATTEMPTS = 10;
const INITIAL_SCAN_REQUIRED_CONSECUTIVE = 2;
const LAB_VERSION = "0.5.7";
const BULK_STARTUP_RECOVERY = true;
const RAY_X_TRACE = true;
const RAY_X_CLUE_RADII_M = [1, 3, 5, 10, 25, 60];
const RAY_X_PROBABLE_DUPLICATE_MAX_M = 10;
// A pista morta informa duas posições diferentes: x/y/z é a pista examinada
// e animal_x/y/z é o corpo. Na build 3331010, a posição da pista coincide com
// o registro tipo 39 criado na queda. Ela serve apenas para associar a pista ao
// ID já pendente; a coleta continua sendo decidida pelo DNA/ID, nunca por raio.
const RAW_CLUE_CATALOG_MAX_XZ_M = 10.0;
const RAW_CLUE_CATALOG_MAX_Y_M = 6.0;
const RAW_CLUE_CATALOG_MIN_GAP_M = 3.0;
const RAW_CLUE_CATALOG_MAX_RATIO = 0.50;
const RAY_X_STATUS_INTERVAL_MS = 3000;
const COLLECTION_DEBUG_MODE = false;
const COLLECTION_DEBUG_MAX_DISTANCE = 35.0;
const COLLECTION_DEBUG_TARGET_DNA = "0|51.831421|2|1";
const COLLECTION_DEBUG_AFTER_DELAYS = [
    0, 50, 150, 300, 700, 1200, 2200, 3500, 5000, 8000, 15000
];
const LAB_REGISTRY_RAW_SIZE = 0x100;
const LAB_POINTER_PREVIEW_SIZE = 0x80;
const LAB_MAX_POINTER_PREVIEWS = 24;
const LAB_REGISTRY_MONITOR_MS = 2000;
const LAB_REGISTRY_HEARTBEAT_MS = 10000;
const RECENT_REMOVED_REGISTRY_WINDOW_MS = 20000;
// A confirmação E+ENTER pode chegar antes de o jogo retirar o registro tipo
// 39 do catálogo. Mantemos essa associação curta para resolver a remoção no
// próximo delta, sem usar posição ou retirar dois cadáveres de uma vez.
const DEFERRED_STABLE_HARVEST_WINDOW_MS =
    RECENT_REMOVED_REGISTRY_WINDOW_MS;
const DEFERRED_STABLE_HARVEST_WAIT_SECONDS = Math.ceil(
    DEFERRED_STABLE_HARVEST_WINDOW_MS / 1000
);
// Estrutura unificada confirmada no laboratório 0.9.5. O ID do registro fica
// no início, a posição vem logo depois e o jogo mantém um sinal independente
// dizendo se o cadáver ainda está disponível. O peso permanece como histórico
// depois da coleta, portanto nunca é usado sozinho para declarar um corpo vivo.
const STABLE_RECORD_COORD_X_OFFSET = 0x08;
const STABLE_RECORD_COORD_Y_OFFSET = 0x0C;
const STABLE_RECORD_COORD_Z_OFFSET = 0x10;
const STABLE_RECORD_LIFECYCLE_OFFSET = 0x28;
const STABLE_RECORD_ACTIVE_STATE = 7;
const STABLE_RECORD_AVAILABLE_OFFSET = 0x40;
const STABLE_RECORD_WEIGHT_OFFSET = 0xE4;
const STABLE_RECORD_POINTER_OFFSETS = [0x48, 0x50, 0x58];
const STABLE_RECORD_RAW_SIZE = 0x200;
// Segunda estrutura descoberta e confirmada pelos laboratórios anteriores.
// Ela mantém o peso e
// as coordenadas exatas do cadáver que não aparece na lista tipo 39.
const ALTERNATIVE_RECORD_WEIGHT_OFFSET = 0xAC;
const ALTERNATIVE_RECORD_COORD_X_OFFSET = 0x58;
const ALTERNATIVE_RECORD_COORD_Y_OFFSET = 0x64;
const ALTERNATIVE_RECORD_COORD_Z_OFFSET = 0x60;
const ALTERNATIVE_RECORD_AVAILABLE_OFFSET = 0x08;
const ALTERNATIVE_RECORD_LIFECYCLE_OFFSET = 0x28;
const ALTERNATIVE_RECORD_POINTER_OFFSETS = [0x10, 0x18, 0x20];
const ALTERNATIVE_RECORD_RAW_SIZE = 0x200;
const ALTERNATIVE_COORD_TOLERANCE_XZ = 2.0;
const ALTERNATIVE_COORD_TOLERANCE_Y = 3.0;
// Aves e corpos na água podem continuar se deslocando enquanto a descoberta
// percorre a memória. O ID de 64 bits precisa coincidir exatamente; esta margem
// só impede que esse movimento legítimo faça o mesmo registro ser rejeitado.
const STABLE_RECORD_COORD_TOLERANCE_XZ = 30.0;
const STABLE_RECORD_COORD_TOLERANCE_Y = 25.0;
const STABLE_METADATA_EXACT_RANGE_SIZE = 16 * 1024 * 1024;
const STABLE_METADATA_FALLBACK_MIN_SIZE = 8 * 1024 * 1024;
const STABLE_METADATA_FALLBACK_MAX_SIZE = 64 * 1024 * 1024;
const STABLE_METADATA_DISCOVERY_MAX_BYTES = 2 * 1024 * 1024 * 1024;
const STABLE_METADATA_DISCOVERY_MAX_ANCHORS = 4;
// Varredura forense: procura o peso exato de um animal conhecido somente em
// páginas de dados graváveis. É deliberadamente pesada e existe apenas neste
// laboratório, nunca na versão pública.
const FORENSIC_SCAN_CHUNK_BYTES = 16 * 1024 * 1024;
const FORENSIC_SCAN_MAX_BYTES = 6 * 1024 * 1024 * 1024;
const FORENSIC_SCAN_PROGRESS_BYTES = 256 * 1024 * 1024;
const FORENSIC_SCAN_CONSOLE_PROGRESS_BYTES = 1024 * 1024 * 1024;
const FORENSIC_SCAN_MAX_HITS = 384;
const FORENSIC_MAX_SAVED_CANDIDATES = 64;
const FORENSIC_WINDOW_BEFORE = 0x100;
const FORENSIC_WINDOW_SIZE = 0x400;
const FORENSIC_TRIGGER_DEBOUNCE_MS = 2500;
const DEATH_SIGNAL_PHASE_NORMAL = 0;
const DEATH_SIGNAL_PHASE_ATTACK = 1;
const DEATH_SIGNAL_PHASE_DONT_COLLECT = 2;
const DEATH_SIGNAL_PHASE_ANALYZING = 3;
const DEATH_SIGNAL_PHASE_CAN_COLLECT = 4;
const DEATH_SIGNAL_PHASE_SEE_CLUE = 5;
const DEATH_SIGNAL_PHASE_ERROR = 6;
const DEATH_SIGNAL_PHASE_STOP = 7;
const DEATH_SIGNAL_PHASE_SEND_ZIP = 8;
const DEATH_SIGNAL_AFTER_DEATH_DELAYS = [0, 700, 2000, 5000, 9000];
const DEATH_SIGNAL_AFTER_HARVEST_DELAYS = [0, 250, 1000, 3000, 7000, 12000];
const BULK_SCAN_RETRY_MS = 1000;
const BULK_SCAN_MAX_ATTEMPTS = 15;
const BULK_SCAN_CONFIRM_DELAY_MS = 1500;
const BULK_SCAN_MAX_BODIES = 128;

let capturedMap = ptr(0);
let hudGpsActionRequired = false;
let hudCountdownSeconds = -1;
let hudCountdownTimer = null;
let hudCountdownReason = "";
let hudCountdownSerial = 0;

let pending = [];
let currentKey = "";
let currentIndex = -1;
let switchingKey = "";
let switchSerial = 0;
let markerOwned = false;
let finalClearDone = false;
let manualWaypointOverride = false;
let scriptStopping = false;
let unsupportedBuildBlocked = false;
let detectedGameBuild = "";
let gameBuildWarningSent = false;

let insideSet = false;
let insideClear = false;
let setWaypointHookOperational = false;
let clearWaypointHookOperational = false;
let capturedClearWaypoint = null;
let capturedClearCheckDone = false;
let capturedClearRejected = false;
let internalHookIgnoreUntil = 0;
let lastInternalSetPosition = null;
let lastInternalSetAt = 0;
let lastExternalHookKind = "";
let lastExternalHookAt = 0;

let switchTimer = null;
let externalReapplyTimer = null;
let nearestUpdateTimer = null;
let staleWaypointCleanupTimer = null;
let nearestInterval = null;
let initialOldCorpseScanTimer = null;
let initialOldCorpseScanStarted = false;
let initialOldCorpseScanFinished = false;
let initialOldCorpseScanAttempts = 0;
let initialOldCorpseScanReserve = "";
let initialOldCorpseCandidates = [];
let initialOldCorpseLastReadableAttempt = 0;
let initialOldCorpseLastRejected = [];
const knownOldCorpseIds = new Set();

let labRegistryMonitorTimer = null;
let labLastRegistryFingerprint = "";
let labLastRegistryHeartbeatAt = 0;
let labSnapshotSequence = 0;
const labBurstTimers = [];
const labTrackedRegistrySlots = new Set();
let labLatestValidatedCorpseEntries = [];
let labLatestValidatedCorpseAt = 0;
let labPreviousRegistryTraceEntries = [];
let labPreviousRegistryTraceAt = 0;
let labRecentRemovedRegistryEntries = [];
let labMostRecentConfirmedHarvest = null;
let labDeferredStableHarvests = [];
let labDeferredStableHarvestSequence = 0;
let registryDiscoveryAttempts = 0;
let registryDiscoveryNextAt = 0;
let registryDiscoveryRunning = false;
let registryDiscoverySelected = false;
let registryDiscoveryLastCandidates = [];
let rayXMapSlotDiscoveryRunning = false;
let rayXMapSlotDiscoveryDoneForPointer = "";
let rayXLastMapStatusAt = 0;
let rayXLastMapStatusKey = "";
let rayXLastMapNullLogAt = 0;
let rayXWaitingForManualWaypoint = false;
let autoWaypointSetter = null;
let autoWaypointLastPreflight = "";
let autoWaypointLastPreflightAt = 0;
let autoWaypointAttemptedKey = "";
let autoWaypointAwaitingRegistry = "";
let autoMapVerifiedPointer = "";
let autoWaypointMissingSince = 0;
let rayXLastPlayerStatusAt = 0;
let rayXLastPlayerStatusKey = "";
let registryWaypointOriginalPosition = null;
let registryWaypointLastDescriptor = "";
let registryWaypointLastIndex = -1;
let registryWaypointLastTargetKey = "";
let registryWaypointLastApplyAt = 0;
let registryWaypointLastLogKey = "";
let waypointWriterMonitorArmed = false;
let waypointWriterMonitorTimer = null;
let waypointWriterMonitorGeneration = 0;
let waypointWriterMonitorBase = "";
let waypointWriterMonitorSize = 0;
let waypointWriterAccessCount = 0;
let waypointWriterCandidates = [];
let waypointWriterCapturedForSession = false;
let waypointHardwareWatchActive = false;
let waypointHardwareWatchTimer = null;
let waypointHardwareWatchObserver = null;
let waypointHardwareWatchHeader = ptr(0);
let waypointHardwareWatchDescriptor = ptr(0);
let waypointHardwareWatchInitialEnd = ptr(0);
let waypointHardwareWatchThreads = {};
let waypointHardwareWatchHits = [];
let waypointHardwareWatchHitCount = 0;
let waypointHardwareWatchCaptured = false;
let waypointHardwareExceptionHandlerReady = false;
let waypointHardwareWatchMode = "";
let waypointClearFlagAddress = ptr(0);
let waypointClearFlagWriteCaptured = false;
let waypointClearInstructionIssued = false;
let waypointClearLastDescriptor = "";
const waypointObjectDiagnosticsSeen = new Set();
let waypointIdScanStarted = false;
let waypointIdScanFinished = false;
let waypointConfirmedSetterInterceptor = null;
let waypointConfirmedSetterCaptures = 0;
let waypointConfirmedSetterAddress = "";
let preferredWaypointKey = "";
// A atualização 3331010 invalidou GetPlayerEntity. Depois de cada coleta o
// jogador está fisicamente no corpo recolhido, então essa posição vira a
// referência segura para escolher o próximo mais próximo enquanto o novo
// endereço real do jogador ainda está sendo localizado.
let lastNavigationAnchor = null;

let stableAvailabilityScanRunning = false;
let stableAvailabilityScanSerial = 0;
let stableMetadataRangeBase = "";
let stableMetadataRangeSize = 0;
let stableMetadataRangeProtection = "";
let stableMetadataRangeFile = "";

let forensicTargets = [];
let forensicCandidates = {};
let forensicScanRunning = false;
let forensicScanSerial = 0;
let forensicLastTriggerAt = 0;
let restoredPendingBuffer = [];
let runtimeStateImported = false;
let forensicMemoryApiStatus = {ok:false, error:"autoteste não executado"};
let collectionDebugArmed = null;
let collectionDebugArmTimer = null;
let collectionDebugHarvestSeen = false;
let collectionDebugCompleted = false;

let deathSignalPhase = DEATH_SIGNAL_PHASE_NORMAL;
let deathSignalBaselineRunning = false;
let deathSignalBaselineFinished = false;
let deathSignalBaselineEntries = [];
let deathSignalTarget = null;
let deathSignalTargetBaseline = null;
let deathSignalSnapshots = [];
let deathSignalDeathCaptureRunning = false;
let deathSignalHarvestObserved = false;
let deathSignalClueRequested = false;
let deathSignalRecordCorpse = null;
let deathSignalAnalysisAttempt = 0;
let deathSignalBaselineRecordCorpse = null;
let deathSignalBaselineAttempt = 0;
let clueFeedbackSerial = 0;

let bulkDeadBodyScanRunning = false;
let bulkDeadBodyScanFinished = false;
let bulkDeadBodyScanSerial = 0;
let bulkDeadBodyScanAttempt = 0;
let bulkDeadBodyScanTimer = null;
let bulkDeferredRegistryIds = new Set();
let bulkDeferredRegistryReserve = "";
// IDs crus do catálogo que já se provaram falsos nesta reserva. Alguns
// objetos temporários de pista usam o mesmo tipo 39 dos cadáveres antigos.
let rejectedCatalogRegistryIds = new Set();
let catalogNearManualChecks = {};
// O descarte manual vale somente para o alvo selecionado nesta reserva.
let f6TargetKey = "";
let f6PressCount = 0;
let skippedCorpseKeys = new Set();
let skippedStrongDnas = new Set();
let manualOldCorpseScanVisible = false;
let consecutiveEmptyManualScans = 0;
// A recuperação guiada só é acionada quando o jogador pede F6.
// Nenhuma quantidade de abates limita o tempo de caça.
let labRecoveryStage = 0;

let cachedEntity = ptr(0);
let playerPosConfirmed = false;
let getPlayerEntityDisabledAfterFailure = false;

let soloConfirmed = !SOLO_ONLY_PROTECTION;
let multiplayerBlocked = false;
let blockReason = "";
let activeReserve = "";
let reserveCandidate = "";
let reserveCandidateTimer = null;

let hudCorner = 1;
let hudUserVisible = true;
let hudGameVisible = true;
let hudAutoVisibilitySupported = false;
let hudWarningActive = false;
let hudWarningShown = false;
let hudLoggedGameVisible = false;
let hudLoggedGameHidden = false;
let hudModule = null;
let hudStateMemory = null;
let hudSetStateNative = null;
let hudGetStatusNative = null;
let hudShutdownNative = null;
let hudHooks = [];
let hudInitTimer = null;
let hudStatusTimer = null;
let hudVisibilityTimer = null;
let hudWarningTimer = null;
let hudLastReportedStatus = null;

let GetForegroundWindowNative = null;
let GetWindowThreadProcessIdNative = null;
let GetCursorInfoNative = null;
let hudForegroundPidMemory = null;
let hudCursorInfoMemory = null;

const reqPaths = {};
const reqBodies = {};
const reqProcessed = {};
const seenDeaths = new Set();
const seenDeathDnas = new Set();
const seenHarvests = new Set();
const harvestedDnas = new Set();
const seenRecoveredClues = new Set();
const harvestQueue = [];
let harvestQueueProcessing = false;
let harvestSequence = 0;

function log(text) {
    send({type:"log", text:text});
}

function notifyBlock(reason) {
    send({type:"multiplayer_block", reason:reason});
}

function notifyFatalBlock(reason) {
    send({type:"fatal_block", reason:reason});
}

function sendGpsStatus(event, message) {
    updateHudState();
    send({
        type:"gps_status",
        event:event,
        pending_count:pending.length,
        solo_confirmed:soloConfirmed,
        multiplayer_blocked:multiplayerBlocked,
        reserve:activeReserve,
        message:message || ""
    });
}

function sendLabEvent(event, data) {
    send({
        type:"lab_event",
        event:event,
        data:Object.assign({
            lab_version:LAB_VERSION,
            at_ms:Date.now(),
            reserve:activeReserve,
            pending_count:pending.length,
            solo_confirmed:soloConfirmed,
            multiplayer_blocked:multiplayerBlocked
        }, data || {})
    });
}

function labSanitizeValue(value, depth) {
    if (depth > 5)
        return "<limite>";

    if (value === null || value === undefined ||
        typeof value === "number" || typeof value === "boolean")
        return value;

    if (typeof value === "string")
        return value.length <= 512 ? value : value.slice(0, 512) + "<cortado>";

    if (Array.isArray(value))
        return value.slice(0, 64).map(function (item) {
            return labSanitizeValue(item, depth + 1);
        });

    if (typeof value !== "object")
        return String(value);

    const result = {};
    const keys = Object.keys(value);

    for (let i=0; i<keys.length; i++) {
        const key = keys[i];
        const low = key.toLowerCase();

        if (low.indexOf("anon") >= 0 || low.indexOf("session_uuid") >= 0 ||
            low.indexOf("account") >= 0 || low === "user_id" ||
            low.indexOf("token") >= 0)
            continue;

        result[key] = labSanitizeValue(value[key], depth + 1);
    }

    return result;
}

function labHex(address, length) {
    try {
        const buffer = address.readByteArray(length);

        if (buffer === null)
            return "";

        const bytes = new Uint8Array(buffer);
        let result = "";

        for (let i=0; i<bytes.length; i++) {
            const value = bytes[i].toString(16);
            result += value.length < 2 ? "0" + value : value;
        }

        return result;
    } catch (_) {
        return "";
    }
}

function labOffsetName(offset) {
    return "0x" + Number(offset).toString(16).toUpperCase();
}

function labDecodedFields(pointer) {
    const words = [];
    const qwords = [];

    for (let offset=0; offset<LAB_REGISTRY_RAW_SIZE; offset+=4) {
        try {
            const f32 = pointer.add(offset).readFloat();
            words.push({
                offset:labOffsetName(offset),
                u32:pointer.add(offset).readU32(),
                i32:pointer.add(offset).readS32(),
                f32:Number.isFinite(f32) && Math.abs(f32) < 1000000000
                    ? f32
                    : null
            });
        } catch (_) {}
    }

    for (let offset=0; offset<LAB_REGISTRY_RAW_SIZE; offset+=8) {
        try {
            qwords.push({
                offset:labOffsetName(offset),
                u64:pointer.add(offset).readU64().toString()
            });
        } catch (_) {}
    }

    return {words:words, qwords:qwords};
}

function labPointerPreviews(pointer) {
    const result = [];
    const seen = new Set();

    for (let offset=0;
        offset<LAB_REGISTRY_RAW_SIZE && result.length<LAB_MAX_POINTER_PREVIEWS;
        offset+=Process.pointerSize) {
        try {
            const target = pointer.add(offset).readPointer();

            if (!target || target.isNull())
                continue;

            const key = pstr(target);

            if (seen.has(key))
                continue;

            const range = Process.findRangeByAddress(target);

            if (!range || String(range.protection || "").indexOf("r") < 0)
                continue;

            const raw = labHex(target, LAB_POINTER_PREVIEW_SIZE);

            if (raw === "")
                continue;

            seen.add(key);
            let moduleName = "";

            try {
                const module = Process.findModuleByAddress(target);
                moduleName = module ? String(module.name || "") : "";
            } catch (_) {}

            result.push({
                source_offset:labOffsetName(offset),
                address:key,
                protection:String(range.protection || ""),
                module:moduleName,
                raw_hex:raw
            });
        } catch (_) {}
    }

    return result;
}

function inspectOldCorpseDescriptor(pointer) {
    try {
        if (pointer.add(0x60).readU8() !== OLD_CORPSE_TYPE)
            return null;

        const registryId = normalizeRegistryId(
            pointer.add(0x30).readU64().toString()
        );
        const x = pointer.add(0x20).readFloat();
        const y = pointer.add(0x24).readFloat();
        const z = pointer.add(0x28).readFloat();

        const result = {
            descriptor:pstr(pointer),
            registry_id:registryId,
            registry_id_hex:labHex(pointer.add(0x30), 8),
            x:x,
            y:y,
            z:z,
            object_link:"",
            object_link_valid:false,
            object_link_module:"",
            object_link_protection:"",
            rejection_reason:""
        };

        if (registryId === "") {
            result.rejection_reason = "registry_id_invalid";
            return result;
        }

        if (!validCoord(x) || !validCoord(y) || !validCoord(z)) {
            result.rejection_reason = "coordinates_invalid";
            return result;
        }

        let objectLink = ptr(0);
        try {
            objectLink = pointer.add(
                OLD_CORPSE_OBJECT_LINK_OFFSET
            ).readPointer();
        } catch (_) {
            result.rejection_reason = "object_link_unreadable";
            return result;
        }

        result.object_link = pstr(objectLink);

        if (!objectLink || objectLink.isNull()) {
            result.rejection_reason = "object_link_null";
            return result;
        }

        const module = Process.mainModule;
        const moduleEnd = module.base.add(module.size);

        if (objectLink.compare(module.base) < 0 ||
            objectLink.compare(moduleEnd) >= 0) {
            result.rejection_reason = "object_link_outside_game_module";
            return result;
        }

        const range = Process.findRangeByAddress(objectLink);
        const protection = range ? String(range.protection || "") : "";
        result.object_link_module = String(module.name || "");
        result.object_link_protection = protection;

        if (!range || protection.indexOf("r") < 0) {
            result.rejection_reason = "object_link_not_readable";
            return result;
        }

        result.object_link_valid = true;
        return result;
    } catch (_) {
        return null;
    }
}

function labDescriptorSnapshot(pointer, index, deep) {
    try {
        const type = pointer.add(0x60).readU8();
        const registryId = normalizeRegistryId(
            pointer.add(0x30).readU64().toString()
        );
        const result = {
            index:index,
            descriptor:pstr(pointer),
            type:type,
            registry_id:registryId,
            registry_id_hex:labHex(pointer.add(0x30), 8),
            x:pointer.add(0x20).readFloat(),
            y:pointer.add(0x24).readFloat(),
            z:pointer.add(0x28).readFloat(),
            raw_hex:labHex(pointer, LAB_REGISTRY_RAW_SIZE)
        };

        if (type === OLD_CORPSE_TYPE) {
            const validation = inspectOldCorpseDescriptor(pointer);
            result.corpse_validation = validation ? {
                object_link:validation.object_link,
                object_link_valid:validation.object_link_valid,
                object_link_module:validation.object_link_module,
                object_link_protection:validation.object_link_protection,
                rejection_reason:validation.rejection_reason
            } : {
                object_link:"",
                object_link_valid:false,
                object_link_module:"",
                object_link_protection:"",
                rejection_reason:"descriptor_unreadable"
            };
        }

        if (deep) {
            result.decoded = labDecodedFields(pointer);
            result.pointer_previews = labPointerPreviews(pointer);
        }

        return result;
    } catch (_) {
        return null;
    }
}

function labPendingSnapshot() {
    const result = [];

    for (let i=0; i<pending.length; i++) {
        const corpse = pending[i];
        result.push({
            index:i,
            key:corpseKey(corpse),
            reserve:corpse.reserve ?? "",
            species:corpse.species ?? null,
            weight:corpse.weight ?? null,
            gender:corpse.gender ?? null,
            difficulty:corpse.difficulty ?? null,
            x:corpse.x,
            y:corpse.y,
            z:corpse.z,
            special_tag:String(corpse.special_tag ?? ""),
            recovered_animal_id:String(
                corpse.recovered_animal_id ?? ""
            ),
            recovered_from_startup:corpse.recovered_from_startup === true,
            recovered_by_registry_catalog:
                corpse.recovered_by_registry_catalog === true,
            recovered_from_clue:corpse.recovered_from_clue === true,
            recovered_clue_association:String(
                corpse.recovered_clue_association ?? ""
            ),
            recovered_clue_x:finiteNumber(corpse.recovered_clue_x),
            recovered_clue_y:finiteNumber(corpse.recovered_clue_y),
            recovered_clue_z:finiteNumber(corpse.recovered_clue_z),
            restored_from_state:corpse.restored_from_state === true,
            restored_unverified:corpse.restored_unverified === true,
            recovered_registry_id:normalizeRegistryId(
                corpse.recovered_registry_id
            ),
            recovered_registry_id_history:Array.isArray(
                corpse.recovered_registry_id_history
            ) ? corpse.recovered_registry_id_history.slice() : [],
            recovered_registry_descriptor:String(
                corpse.recovered_registry_descriptor ?? ""
            ),
            recovered_object_link:String(
                corpse.recovered_object_link ?? ""
            ),
            recovered_stable_record_address:String(
                corpse.recovered_stable_record_address ?? ""
            ),
            recovered_stable_range_base:String(
                corpse.recovered_stable_range_base ?? ""
            ),
            recovered_from_alternative_layout:
                corpse.recovered_from_alternative_layout === true,
            recovered_alternative_record_address:String(
                corpse.recovered_alternative_record_address ?? ""
            ),
            recovered_alternative_range_base:String(
                corpse.recovered_alternative_range_base ?? ""
            ),
            recovered_availability:finiteNumber(
                corpse.recovered_availability
            ),
            recovered_lifecycle:finiteNumber(corpse.recovered_lifecycle),
            recovered_live_pointer_count:finiteNumber(
                corpse.recovered_live_pointer_count
            ),
            recovered_readable_pointer_count:finiteNumber(
                corpse.recovered_readable_pointer_count
            ),
            time:Number(corpse.time || 0),
            dna:strongCorpseDna(corpse)
        });
    }

    return result;
}

function labCollectRegistry(deep) {
    const bounds = oldCorpseRegistryBounds();

    if (!bounds)
        return {ok:false, reason:"registry_bounds_unreadable"};

    let registry = ptr(0);

    try { registry = activeRegistrySlot.readPointer(); }
    catch (_) {}

    let byteLength = 0;

    try { byteLength = bounds.end.sub(bounds.begin).toUInt32(); }
    catch (_) { return {ok:false, reason:"registry_length_unreadable"}; }

    const total = Math.min(
        Math.floor(byteLength / OLD_CORPSE_DESCRIPTOR_SIZE),
        OLD_CORPSE_MAX_SCAN
    );
    const typeCounts = {};
    const type39Entries = [];
    const waypointEntries = [];
    const validatedCorpseEntries = [];
    const neighborIndices = {};
    let registryView = null;

    // Uma leitura em bloco evita milhares de chamadas individuais por segundo.
    try {
        const buffer = bounds.begin.readByteArray(
            total * OLD_CORPSE_DESCRIPTOR_SIZE
        );
        if (buffer !== null)
            registryView = new Uint8Array(buffer);
    } catch (_) {}

    for (let index=0; index<total; index++) {
        const pointer = bounds.begin.add(index * OLD_CORPSE_DESCRIPTOR_SIZE);
        let type = -1;

        try {
            type = registryView !== null
                ? registryView[index * OLD_CORPSE_DESCRIPTOR_SIZE + 0x60]
                : pointer.add(0x60).readU8();
        }
        catch (_) { continue; }

        const typeKey = String(type);
        typeCounts[typeKey] = Number(typeCounts[typeKey] || 0) + 1;

        if (type === MANUAL_WAYPOINT_TYPE) {
            const waypointSnapshot = labDescriptorSnapshot(
                pointer,
                index,
                !!deep
            );
            if (waypointSnapshot &&
                normalizeRegistryId(waypointSnapshot.registry_id) ===
                    MANUAL_WAYPOINT_REGISTRY_ID &&
                validCoord(waypointSnapshot.x) &&
                validCoord(waypointSnapshot.y) &&
                validCoord(waypointSnapshot.z)) {
                waypointEntries.push(waypointSnapshot);
            }
        }

        if (type !== OLD_CORPSE_TYPE)
            continue;

        labTrackedRegistrySlots.add(index);

        const snapshot = labDescriptorSnapshot(pointer, index, !!deep);

        if (snapshot) {
            type39Entries.push(snapshot);
            if (snapshot.corpse_validation &&
                snapshot.corpse_validation.object_link_valid === true) {
                validatedCorpseEntries.push({
                    descriptor:snapshot.descriptor,
                    registry_id:snapshot.registry_id,
                    x:snapshot.x,
                    y:snapshot.y,
                    z:snapshot.z,
                    object_link:snapshot.corpse_validation.object_link
                });
            }
        }

        if (index > 0)
            neighborIndices[String(index - 1)] = true;
        if (index + 1 < total)
            neighborIndices[String(index + 1)] = true;
    }

    const neighbors = [];
    const neighborKeys = Object.keys(neighborIndices);

    for (let i=0; i<neighborKeys.length; i++) {
        const index = Number(neighborKeys[i]);
        const pointer = bounds.begin.add(index * OLD_CORPSE_DESCRIPTOR_SIZE);
        let type = -1;

        try {
            type = registryView !== null
                ? registryView[index * OLD_CORPSE_DESCRIPTOR_SIZE + 0x60]
                : pointer.add(0x60).readU8();
        }
        catch (_) { continue; }

        if (type === OLD_CORPSE_TYPE || type === MANUAL_WAYPOINT_TYPE)
            continue;

        // Na varredura profunda, preserve também os bytes e ponteiros dos registros
        // vizinhos. No teste 0.9.3, o waypoint do próprio Turbo Hunter ficou
        // justamente ao lado do tipo 39; essa comparação evita confundi-lo
        // novamente com uma estrutura do cadáver.
        const snapshot = labDescriptorSnapshot(pointer, index, !!deep);
        if (snapshot)
            neighbors.push(snapshot);
    }

    const trackedFormerSlots = [];
    const trackedSlots = Array.from(labTrackedRegistrySlots);

    for (let i=0; i<trackedSlots.length; i++) {
        const index = Number(trackedSlots[i]);

        if (!Number.isFinite(index) || index < 0 || index >= total)
            continue;

        let type = -1;
        try {
            type = registryView !== null
                ? registryView[index * OLD_CORPSE_DESCRIPTOR_SIZE + 0x60]
                : bounds.begin.add(index * OLD_CORPSE_DESCRIPTOR_SIZE + 0x60).readU8();
        } catch (_) { continue; }

        if (type === OLD_CORPSE_TYPE)
            continue;

        const snapshot = labDescriptorSnapshot(
            bounds.begin.add(index * OLD_CORPSE_DESCRIPTOR_SIZE),
            index,
            !!deep
        );
        if (snapshot)
            trackedFormerSlots.push(snapshot);
    }

    // A lista recente guarda todos os descritores tipo 39 com ID e posição
    // válidos. O antigo object_link fica somente como dado de diagnóstico;
    // o novo teste de disponibilidade é feito na estrutura unificada do ID.
    labLatestValidatedCorpseEntries = type39Entries.filter(function (entry) {
        return normalizeRegistryId(entry.registry_id) !== "" &&
            validCoord(entry.x) && validCoord(entry.y) && validCoord(entry.z);
    }).map(function (entry) {
        return {
            descriptor:entry.descriptor,
            registry_id:entry.registry_id,
            registry_id_hex:String(entry.registry_id_hex || ""),
            x:entry.x,
            y:entry.y,
            z:entry.z,
            object_link:entry.corpse_validation
                ? entry.corpse_validation.object_link : "",
            object_link_valid:entry.corpse_validation
                ? entry.corpse_validation.object_link_valid === true : false
        };
    });
    labLatestValidatedCorpseAt = Date.now();

    return {
        ok:true,
        registry_slot:pstr(activeRegistrySlot),
        registry_slot_reference:pstr(REFERENCE_REGISTRY_SLOT),
        registry_discovered:registryDiscoverySelected,
        registry_discovery_attempts:registryDiscoveryAttempts,
        registry:pstr(registry),
        registry_header_hex:registry && !registry.isNull()
            ? labHex(registry, LAB_REGISTRY_RAW_SIZE)
            : "",
        begin:pstr(bounds.begin),
        end:pstr(bounds.end),
        descriptor_count:total,
        type_counts:typeCounts,
        type39_entries:type39Entries,
        waypoint_entries:waypointEntries,
        validated_corpse_entries:validatedCorpseEntries,
        neighboring_entries:neighbors,
        tracked_former_type39_slots:trackedFormerSlots
    };
}

function labRegistryFingerprint(snapshot) {
    if (!snapshot || snapshot.ok !== true)
        return "unavailable|" + String(snapshot ? snapshot.reason : "null");

    return snapshot.type39_entries.map(function (entry) {
        return `${entry.registry_id}@${entry.index}:${entry.raw_hex}`;
    }).join("|") + "#waypoint#" + (snapshot.waypoint_entries || []).map(
        function (entry) {
            return `${entry.registry_id}@${entry.index}:` +
                `${Number(entry.x).toFixed(3)},` +
                `${Number(entry.y).toFixed(3)},` +
                `${Number(entry.z).toFixed(3)}`;
        }
    ).join("|") + "#former#" + snapshot.tracked_former_type39_slots.map(
        function (entry) {
            return `${entry.registry_id}@${entry.index}:${entry.type}:${entry.raw_hex}`;
        }
    ).join("|");
}

function labRegistryTraceEntry(entry) {
    return {
        index:Number(entry.index),
        descriptor:String(entry.descriptor || ""),
        registry_id:normalizeRegistryId(entry.registry_id),
        registry_id_hex:String(entry.registry_id_hex || ""),
        x:Number(entry.x),
        y:Number(entry.y),
        z:Number(entry.z),
        object_link:String(
            entry.corpse_validation
                ? entry.corpse_validation.object_link || ""
                : entry.object_link || ""
        )
    };
}

function harvestWaitsForStableRegistryId(match) {
    const method = String(match && match.method || "");
    return method === "aguardando_desaparecimento_do_id_estavel" ||
        method === "coleta_sem_dna_aguardando_id_estavel" ||
        method === "dna_parcial_ambiguo_aguardando_id_estavel" ||
        method === "peso_exato_ambiguo_aguardando_id_estavel";
}

function labPruneRecentRemovedRegistry(now, addedEntries) {
    const currentTime = Number(now || Date.now());
    const returnedIds = new Set((addedEntries || []).map(function (entry) {
        return normalizeRegistryId(entry.registry_id);
    }).filter(function (value) { return value !== ""; }));

    labRecentRemovedRegistryEntries =
        labRecentRemovedRegistryEntries.filter(function (entry) {
            const id = normalizeRegistryId(entry.registry_id);
            return id !== "" && !returnedIds.has(id) &&
                currentTime - Number(entry.removed_at || 0) <=
                    RECENT_REMOVED_REGISTRY_WINDOW_MS;
        });
}

function labTrackRecentRemovedRegistry(
    removedEntries,
    pendingByRegistryId,
    now,
    reason
) {
    labPruneRecentRemovedRegistry(now, []);

    for (let i=0; i<(removedEntries || []).length; i++) {
        const entry = removedEntries[i];
        const id = normalizeRegistryId(entry.registry_id);
        const pendingMatch = pendingByRegistryId[id] || null;

        if (id === "" || pendingMatch === null)
            continue;

        labRecentRemovedRegistryEntries =
            labRecentRemovedRegistryEntries.filter(function (existing) {
                return normalizeRegistryId(existing.registry_id) !== id;
            });
        labRecentRemovedRegistryEntries.push({
            registry_id:id,
            removed_at:Number(now || Date.now()),
            reserve:normalizeReserve(activeReserve),
            capture_reason:String(reason || "monitor"),
            registry_entry:labSanitizeValue(entry, 0),
            pending_key:String(pendingMatch.key || "")
        });
    }
}

function labConsumeRecentRemovedRegistry(payload, match) {
    const now = Date.now();
    labPruneRecentRemovedRegistry(now, []);

    if (!harvestWaitsForStableRegistryId(match))
        return null;

    const currentById = {};
    for (let i=0; i<pending.length; i++) {
        const id = normalizeRegistryId(pending[i].recovered_registry_id);
        if (id !== "")
            currentById[id] = pending[i];
    }

    const candidates = labRecentRemovedRegistryEntries.filter(
        function (entry) {
            return !!currentById[normalizeRegistryId(entry.registry_id)];
        }
    );

    sendLabEvent("recent_removed_registry_harvest_association", {
        match:labSanitizeValue(match, 0),
        candidate_count:candidates.length,
        candidates:labSanitizeValue(candidates, 0),
        pending:labPendingSnapshot()
    });

    if (candidates.length !== 1)
        return null;

    const selected = candidates[0];
    const registryId = normalizeRegistryId(selected.registry_id);
    const age = Math.max(0, now - Number(selected.removed_at || now));
    labRecentRemovedRegistryEntries =
        labRecentRemovedRegistryEntries.filter(function (entry) {
            return normalizeRegistryId(entry.registry_id) !== registryId;
        });

    const removed = removePendingByStableRegistryId(
        registryId,
        payload,
        age
    );
    if (removed !== null) {
        sendLabEvent("recent_removed_registry_harvest_removed", {
            registry_id:registryId,
            disappeared_before_harvest_ms:age,
            match:labSanitizeValue(match, 0),
            removed:labSanitizeValue(removed, 0),
            pending_after:labPendingSnapshot()
        });
    }
    return removed;
}

function labRecentlyDisappearedCatalogId(registryId) {
    const id = normalizeRegistryId(registryId);
    const now = Date.now();
    if (id === "" || labPreviousRegistryTraceAt === 0 ||
        now - labPreviousRegistryTraceAt > 2000)
        return false;

    return labRecentRemovedRegistryEntries.some(function (entry) {
        return normalizeRegistryId(entry.registry_id) === id &&
            normalizeReserve(entry.reserve) ===
                normalizeReserve(activeReserve) &&
            now - Number(entry.removed_at || 0) <= 3000 &&
            labPreviousRegistryTraceAt >= Number(entry.removed_at || 0) &&
            !labPreviousRegistryTraceEntries.some(function (current) {
                return normalizeRegistryId(current.registry_id) === id;
            });
    });
}

// O catálogo pode retirar o ID antes de publicar a coleta. Isso não prova
// qual DNA pertencia ao ID: validamos apenas que o ID deixou de existir, em
// duas leituras independentes, e removemos somente sua própria entrada sem
// DNA. Não relacionamos animais por posição ou por proximidade temporal.
function labScheduleAbsentCatalogRecheck(removed, sequence) {
    const now = Date.now();
    labPruneRecentRemovedRegistry(now, []);
    const matches = labRecentRemovedRegistryEntries.filter(function (entry) {
        return now - Number(entry.removed_at || 0) <= 3000 &&
            normalizeReserve(entry.reserve) ===
                normalizeReserve(activeReserve) &&
            labRecentlyDisappearedCatalogId(entry.registry_id) &&
            pending.some(function (corpse) {
                return corpse.recovered_by_registry_catalog === true &&
                    normalizeRegistryId(corpse.recovered_registry_id) ===
                        normalizeRegistryId(entry.registry_id);
            });
    });

    // Vários IDs somem juntos durante teleporte ou recarga. Uma coleta não
    // autoriza retirar vários registros do catálogo de uma vez.
    if (matches.length !== 1) {
        if (matches.length > 1)
            sendLabEvent("catalog_recheck_ambiguous_skipped", {
                sequence:sequence || 0,
                vanished:labSanitizeValue(matches, 0),
                pending:labPendingSnapshot()
            });
        return false;
    }

    const candidate = matches[0];
    const id = normalizeRegistryId(candidate.registry_id);
    const reserve = normalizeReserve(activeReserve);
    const key = String(candidate.pending_key || "");
    const first = scanOldCorpseRegistryOnce();
    if (first === null || (first.candidates || []).some(function (entry) {
        return normalizeRegistryId(entry.registry_id) === id;
    })) {
        sendLabEvent("catalog_recheck_first_read_unconfirmed", {
            sequence:sequence || 0, registry_id:id,
            readable:first !== null
        });
        return false;
    }

    sendLabEvent("catalog_recheck_started", {
        sequence:sequence || 0, registry_id:id,
        source_key:key,
        separately_collected:labSanitizeValue(removed, 0)
    });
    const timer = setTimeout(function () {
        if (scriptStopping || normalizeReserve(activeReserve) !== reserve ||
            !labRecentlyDisappearedCatalogId(id))
            return;

        const second = scanOldCorpseRegistryOnce();
        if (second === null || (second.candidates || []).some(
            function (entry) {
                return normalizeRegistryId(entry.registry_id) === id;
            })) {
            sendLabEvent("catalog_recheck_second_read_unconfirmed", {
                sequence:sequence || 0, registry_id:id,
                readable:second !== null
            });
            return;
        }

        const candidates = [];
        for (let i=0; i<pending.length; i++) {
            const corpse = pending[i];
            if (corpse.recovered_by_registry_catalog === true &&
                normalizeRegistryId(corpse.recovered_registry_id) === id &&
                corpseKey(corpse) === key)
                candidates.push(i);
        }
        if (candidates.length !== 1) {
            sendLabEvent("catalog_recheck_identity_changed_skipped", {
                sequence:sequence || 0, registry_id:id,
                candidate_count:candidates.length
            });
            return;
        }

        // Uma segunda coleta no mesmo intervalo invalida a relação entre
        // aquela confirmação e a remoção do catálogo.
        const currentMatches = labRecentRemovedRegistryEntries.filter(
            function (entry) {
                return normalizeReserve(entry.reserve) === reserve &&
                    now - Number(entry.removed_at || 0) <= 3000 &&
                    pending.some(function (corpse) {
                        return corpse.recovered_by_registry_catalog === true &&
                            normalizeRegistryId(corpse.recovered_registry_id) ===
                                normalizeRegistryId(entry.registry_id);
                    });
            }
        );
        if (currentMatches.length !== 1 ||
            normalizeRegistryId(currentMatches[0].registry_id) !== id)
            return;

        const retired = pending.splice(candidates[0], 1)[0];
        labRecentRemovedRegistryEntries =
            labRecentRemovedRegistryEntries.filter(function (entry) {
                return normalizeRegistryId(entry.registry_id) !== id;
            });
        emitRuntimeState("ID ausente confirmado em duas leituras");
        updateHudWarningForCount();
        sendLabEvent("catalog_missing_id_retired_after_harvest", {
            sequence:sequence || 0,
            registry_id:id,
            retired:labSanitizeValue(retired, 0),
            separately_collected:labSanitizeValue(removed, 0),
            identity_link_to_dna:"não comprovado",
            policy:"ID ausente duas vezes; sem associação por posição",
            pending_after:labPendingSnapshot()
        });
        log("🧬 CATÁLOGO: ID " + id +
            " ausente em duas leituras; contador corrigido.");

        if (!pending.length) {
            clearOwn("último ID não está mais no catálogo", true);
            finishLabCollectionTest("ID expirado validado após coleta");
        } else {
            scheduleNearestUpdate(40, "ID ausente removido; marcar outro corpo");
        }
    }, 250);
    labBurstTimers.push(timer);
    return true;
}

function labPruneDeferredStableHarvests(now) {
    const currentTime = Number(now || Date.now());
    const expired = [];

    labDeferredStableHarvests = labDeferredStableHarvests.filter(
        function (entry) {
            const queuedAt = Number(entry.queued_at || 0);
            const keep = queuedAt > 0 &&
                currentTime - queuedAt <= DEFERRED_STABLE_HARVEST_WINDOW_MS;
            if (!keep)
                expired.push(entry);
            return keep;
        }
    );

    if (expired.length) {
        sendLabEvent("deferred_stable_harvest_expired", {
            expired_count:expired.length,
            expired:labSanitizeValue(expired, 0),
            pending:labPendingSnapshot()
        });
    }
}

function labQueueDeferredStableHarvest(
    path,
    payload,
    sequence,
    match,
    pendingBefore
) {
    if (!harvestWaitsForStableRegistryId(match))
        return false;

    const now = Date.now();
    labPruneDeferredStableHarvests(now);

    const stableIdsBefore = [];
    const source = Array.isArray(pendingBefore) ? pendingBefore : [];
    for (let i=0; i<source.length; i++) {
        const id = normalizeRegistryId(source[i].recovered_registry_id);
        if (id !== "" && stableIdsBefore.indexOf(id) < 0)
            stableIdsBefore.push(id);
    }

    if (!stableIdsBefore.length) {
        sendLabEvent("deferred_stable_harvest_skipped", {
            reason:"nenhum ID estável pendente no instante da coleta",
            sequence:Number(sequence || 0),
            match:labSanitizeValue(match, 0),
            pending:labPendingSnapshot()
        });
        return false;
    }

    const harvestKey = scopedStrongCorpseDna(payload);
    for (let i=0; i<labDeferredStableHarvests.length; i++) {
        const existing = labDeferredStableHarvests[i];
        if (Number(sequence || 0) > 0 &&
            Number(existing.sequence || 0) === Number(sequence || 0))
            return true;
        if (harvestKey !== "" && String(existing.harvest_key || "") ===
            harvestKey)
            return true;
    }

    const queued = {
        queue_id:++labDeferredStableHarvestSequence,
        queued_at:now,
        sequence:Number(sequence || 0),
        path:String(path || ""),
        harvest_key:String(harvestKey || ""),
        payload:payload,
        match:labSanitizeValue(match, 0),
        pending_count_before:source.length,
        stable_ids_before:stableIdsBefore
    };
    labDeferredStableHarvests.push(queued);

    sendLabEvent("deferred_stable_harvest_queued", {
        queue_id:queued.queue_id,
        sequence:queued.sequence,
        path:queued.path,
        harvest_key:queued.harvest_key,
        match:queued.match,
        pending_count_before:queued.pending_count_before,
        stable_ids_before:queued.stable_ids_before,
        queue_count:labDeferredStableHarvests.length
    });
    log(
        "🧪 COLETA CONFIRMADA; aguardando o ID estável sair do catálogo " +
        "antes de descontar (" + DEFERRED_STABLE_HARVEST_WAIT_SECONDS +
        " s)."
    );
    return true;
}

function labResolveDeferredStableHarvests(
    removedEntries,
    pendingByRegistryId,
    now,
    reason
) {
    const currentTime = Number(now || Date.now());
    labPruneDeferredStableHarvests(currentTime);

    const removedPending = [];
    const seen = new Set();
    for (let i=0; i<(removedEntries || []).length; i++) {
        const entry = removedEntries[i];
        const id = normalizeRegistryId(entry && entry.registry_id);
        if (id === "" || seen.has(id) ||
            !pendingByRegistryId || !pendingByRegistryId[id])
            continue;
        seen.add(id);
        removedPending.push(entry);
    }

    if (!removedPending.length || !labDeferredStableHarvests.length)
        return null;

    if (removedPending.length !== 1 || labDeferredStableHarvests.length !== 1) {
        sendLabEvent("deferred_stable_harvest_ambiguous", {
            reason:"não existe uma associação um-para-um segura",
            capture_reason:String(reason || "monitor"),
            removed_pending:labSanitizeValue(removedPending, 0),
            deferred:labSanitizeValue(labDeferredStableHarvests, 0),
            pending:labPendingSnapshot()
        });
        return null;
    }

    const queued = labDeferredStableHarvests[0];
    const selected = removedPending[0];
    const registryId = normalizeRegistryId(selected.registry_id);
    if (queued.stable_ids_before.indexOf(registryId) < 0) {
        sendLabEvent("deferred_stable_harvest_id_not_in_before_snapshot", {
            queue_id:queued.queue_id,
            sequence:queued.sequence,
            registry_id:registryId,
            stable_ids_before:queued.stable_ids_before,
            capture_reason:String(reason || "monitor")
        });
        return null;
    }

    const age = Math.max(0, currentTime - Number(queued.queued_at ||
        currentTime));
    // Retire a queue item exactly once, even if a later read reports the same
    // removal again. removePendingByStableRegistryId still verifies one match.
    labDeferredStableHarvests.shift();
    labRecentRemovedRegistryEntries = labRecentRemovedRegistryEntries.filter(
        function (entry) {
            return normalizeRegistryId(entry.registry_id) !== registryId;
        }
    );

    const removed = removePendingByStableRegistryId(
        registryId,
        queued.payload,
        age
    );
    if (removed === null) {
        sendLabEvent("deferred_stable_harvest_removal_skipped", {
            queue_id:queued.queue_id,
            sequence:queued.sequence,
            registry_id:registryId,
            age_ms:age,
            pending:labPendingSnapshot()
        });
        return null;
    }

    sendLabEvent("deferred_stable_registry_harvest_resolved", {
        queue_id:queued.queue_id,
        sequence:queued.sequence,
        registry_id:registryId,
        age_ms:age,
        capture_reason:String(reason || "monitor"),
        removed:labSanitizeValue(removed, 0),
        pending_after:labPendingSnapshot()
    });
    return removed;
}

function labTraceRegistryDelta(snapshot, reason) {
    if (!RAY_X_TRACE || !snapshot || snapshot.ok !== true)
        return;

    const now = Date.now();
    const current = (snapshot.type39_entries || []).map(
        labRegistryTraceEntry
    );
    const beforeById = {};
    const afterById = {};

    for (let i=0; i<labPreviousRegistryTraceEntries.length; i++) {
        const item = labPreviousRegistryTraceEntries[i];
        beforeById[normalizeRegistryId(item.registry_id)] = item;
    }
    for (let i=0; i<current.length; i++) {
        const item = current[i];
        afterById[normalizeRegistryId(item.registry_id)] = item;
    }

    const added = [];
    const removed = [];
    const moved = [];
    const afterIds = Object.keys(afterById);
    const beforeIds = Object.keys(beforeById);

    for (let i=0; i<afterIds.length; i++) {
        const id = afterIds[i];
        if (!beforeById[id]) {
            added.push(afterById[id]);
            continue;
        }

        const before = beforeById[id];
        const after = afterById[id];
        const xz = distanceXZ(before, after);
        const y = Math.abs(Number(before.y) - Number(after.y));
        if (xz > 0.05 || y > 0.05) {
            moved.push({
                registry_id:id,
                before:before,
                after:after,
                distance_xz_m:xz,
                delta_y_m:y
            });
        }
    }

    for (let i=0; i<beforeIds.length; i++) {
        const id = beforeIds[i];
        if (!afterById[id])
            removed.push(beforeById[id]);
    }

    const isBaseline = labPreviousRegistryTraceAt === 0;
    if (isBaseline || added.length || removed.length || moved.length) {
        const player = getPlayerPos();
        const pendingSnapshot = labPendingSnapshot();
        const pendingByRegistryId = {};

        for (let i=0; i<pendingSnapshot.length; i++) {
            const id = normalizeRegistryId(
                pendingSnapshot[i].recovered_registry_id
            );
            if (id !== "")
                pendingByRegistryId[id] = pendingSnapshot[i];
        }

        labPruneRecentRemovedRegistry(now, added);
        if (!isBaseline && removed.length) {
            labTrackRecentRemovedRegistry(
                removed,
                pendingByRegistryId,
                now,
                reason
            );
        }

        sendLabEvent(
            isBaseline ? "ray_x_registry_baseline" : "ray_x_registry_delta",
            {
                capture_reason:String(reason || "monitor"),
                previous_at_ms:labPreviousRegistryTraceAt,
                elapsed_since_previous_ms:isBaseline
                    ? 0 : Math.max(0, now - labPreviousRegistryTraceAt),
                before_count:labPreviousRegistryTraceEntries.length,
                after_count:current.length,
                added:labSanitizeValue(added, 0),
                removed:labSanitizeValue(removed, 0),
                moved:labSanitizeValue(moved, 0),
                removed_pending_matches:removed.map(function (entry) {
                    const id = normalizeRegistryId(entry.registry_id);
                    return {
                        registry_id:id,
                        pending:pendingByRegistryId[id] || null,
                        distance_to_player_m:player
                            ? distanceXZ(player, entry) : null
                    };
                }),
                current:labSanitizeValue(current, 0),
                player:player,
                pending:pendingSnapshot
            }
        );

        if (!isBaseline && (added.length || removed.length)) {
            log(
                "🧪 RAIO-X CATÁLOGO: " +
                labPreviousRegistryTraceEntries.length + " -> " +
                current.length + " | adicionou=" + added.length +
                " removeu=" + removed.length +
                " | " + String(reason || "monitor")
            );
        }

        if (!isBaseline && removed.length) {
            labResolveDeferredStableHarvests(
                removed,
                pendingByRegistryId,
                now,
                reason
            );
        }
    }

    labPreviousRegistryTraceEntries = current;
    labPreviousRegistryTraceAt = now;

    // A lista do jogo pode mudar um instante DEPOIS do Enter. Nesse caso
    // onHarvest ainda não viu o desaparecimento e a checagem inicial não
    // começou. O monitor liga o ID removido à janela da coleta somente para
    // revalidar a ausência do próprio ID; nunca atribui DNA por posição.
    if (!isBaseline && removed.length &&
        labMostRecentConfirmedHarvest !== null &&
        !labMostRecentConfirmedHarvest.recheck_scheduled &&
        now - labMostRecentConfirmedHarvest.at <= 3000 &&
        labMostRecentConfirmedHarvest.reserve ===
            normalizeReserve(activeReserve)) {
        labMostRecentConfirmedHarvest.recheck_scheduled =
            labScheduleAbsentCatalogRecheck(
                labMostRecentConfirmedHarvest.removed,
                labMostRecentConfirmedHarvest.sequence
            );
    }
}

function waypointCodeContext(address) {
    const result = {
        address:pstr(address),
        rva:"",
        inside_game_module:false,
        instruction:"",
        symbol:"",
        code_before_hex:"",
        code_after_hex:""
    };

    try {
        const moduleEnd = BASE.add(Number(Process.mainModule.size));
        result.inside_game_module = address.compare(BASE) >= 0 &&
            address.compare(moduleEnd) < 0;
        if (result.inside_game_module) {
            result.rva = address.sub(BASE).toString();
            if (result.rva.indexOf("0x") !== 0)
                result.rva = "0x" + result.rva;
            const beforeBytes = Math.min(
                64,
                Number(address.sub(BASE).toUInt32())
            );
            result.code_before_hex = labHex(
                address.sub(beforeBytes),
                beforeBytes
            );
            result.code_after_hex = labHex(address, 160);
        }
    } catch (_) {}

    try { result.instruction = String(Instruction.parse(address)); }
    catch (_) {}
    try { result.symbol = String(DebugSymbol.fromAddress(address)); }
    catch (_) {}
    return result;
}

function waypointHardwareDescriptorState(address) {
    const result = {
        descriptor:pstr(address),
        readable:false,
        type:-1,
        registry_id:"",
        registry_id_hex:"",
        x:null,
        y:null,
        z:null,
        raw_hex:""
    };
    if (!address || address.isNull())
        return result;

    try {
        const range = Process.findRangeByAddress(address);
        if (!range || String(range.protection || "").indexOf("r") < 0)
            return result;
        const rangeEnd = range.base.add(Number(range.size || 0));
        if (address.add(OLD_CORPSE_DESCRIPTOR_SIZE).compare(rangeEnd) > 0)
            return result;

        result.readable = true;
        result.type = address.add(0x60).readU8();
        const registryIdValue = address.add(0x30).readU64();
        result.registry_id = normalizeRegistryId(registryIdValue.toString());
        result.registry_id_hex = registryIdValue.toString(16);
        result.x = address.add(0x20).readFloat();
        result.y = address.add(0x24).readFloat();
        result.z = address.add(0x28).readFloat();
        result.raw_hex = labHex(address, OLD_CORPSE_DESCRIPTOR_SIZE);
    } catch (_) {}
    return result;
}

function waypointHardwareSnapshot() {
    const result = {
        registry_header_end:pstr(waypointHardwareWatchHeader),
        initial_end:pstr(waypointHardwareWatchInitialEnd),
        current_end:"",
        initial_descriptor:waypointHardwareDescriptorState(
            waypointHardwareWatchDescriptor
        ),
        latest_descriptor:null,
        waypoint_complete:false
    };

    try {
        if (!waypointHardwareWatchHeader.isNull()) {
            const currentEnd = waypointHardwareWatchHeader.readPointer();
            result.current_end = pstr(currentEnd);
            const registry = waypointHardwareWatchHeader.sub(0x78);
            const begin = registry.add(0x70).readPointer();
            if (!begin.isNull() && !currentEnd.isNull() &&
                currentEnd.compare(begin) > 0) {
                result.latest_descriptor = waypointHardwareDescriptorState(
                    currentEnd.sub(OLD_CORPSE_DESCRIPTOR_SIZE)
                );
            }
        }
    } catch (_) {}

    const states = [result.initial_descriptor, result.latest_descriptor];
    for (let i=0; i<states.length; i++) {
        const state = states[i];
        if (state && state.readable === true &&
            Number(state.type) === MANUAL_WAYPOINT_TYPE &&
            normalizeRegistryId(state.registry_id) ===
                MANUAL_WAYPOINT_REGISTRY_ID) {
            result.waypoint_complete = true;
            break;
        }
    }
    return result;
}

function waypointHardwareBacktrace(context, backtracer, detailed) {
    let frames = [];
    try { frames = Thread.backtrace(context, backtracer); }
    catch (_) { return []; }

    return frames.map(function (address) {
        if (detailed)
            return waypointCodeContext(address);
        let rva = "";
        let inside = false;
        let symbol = "";
        try {
            const moduleEnd = BASE.add(Number(Process.mainModule.size));
            inside = address.compare(BASE) >= 0 &&
                address.compare(moduleEnd) < 0;
            if (inside) {
                rva = address.sub(BASE).toString();
                if (rva.indexOf("0x") !== 0)
                    rva = "0x" + rva;
            }
        } catch (_) {}
        try { symbol = String(DebugSymbol.fromAddress(address)); }
        catch (_) {}
        return {
            address:pstr(address),
            rva:rva,
            inside_game_module:inside,
            symbol:symbol
        };
    });
}

function waypointHardwareWatchSource(memoryAddress) {
    if (!memoryAddress || memoryAddress.isNull())
        return "hardware_single_step";
    try {
        if (waypointHardwareWatchMode === "clear" &&
            !waypointClearFlagAddress.isNull() &&
            memoryAddress.compare(waypointClearFlagAddress) === 0)
            return "map_waypoint_active_flag";
        if (!waypointHardwareWatchHeader.isNull() &&
            memoryAddress.compare(waypointHardwareWatchHeader) >= 0 &&
            memoryAddress.compare(waypointHardwareWatchHeader.add(8)) < 0)
            return "registry_end_pointer";
        if (!waypointHardwareWatchDescriptor.isNull() &&
            memoryAddress.compare(
                waypointHardwareWatchDescriptor.add(0x60)
            ) === 0)
            return "descriptor_type";
        if (!waypointHardwareWatchDescriptor.isNull() &&
            memoryAddress.compare(
                waypointHardwareWatchDescriptor.add(0x30)
            ) >= 0 &&
            memoryAddress.compare(
                waypointHardwareWatchDescriptor.add(0x38)
            ) < 0)
            return "descriptor_registry_id";
    } catch (_) {}
    return "hardware_single_step";
}

function unsetWaypointHardwareWatchpointsForThread(thread) {
    if (!thread)
        return;
    const ids = [
        WAYPOINT_HARDWARE_WATCH_HEADER_ID,
        WAYPOINT_HARDWARE_WATCH_TYPE_ID,
        WAYPOINT_HARDWARE_WATCH_REGISTRY_ID
    ];
    for (let i=0; i<ids.length; i++) {
        try { thread.unsetHardwareWatchpoint(ids[i]); }
        catch (_) {}
    }
}

function stopWaypointHardwareWatch(reason) {
    const wasActive = waypointHardwareWatchActive;
    waypointHardwareWatchTimer = clearTimeoutSafe(
        waypointHardwareWatchTimer
    );

    if (waypointHardwareWatchObserver !== null) {
        try { waypointHardwareWatchObserver.detach(); }
        catch (_) {}
        waypointHardwareWatchObserver = null;
    }

    const keys = Object.keys(waypointHardwareWatchThreads);
    for (let i=0; i<keys.length; i++) {
        const record = waypointHardwareWatchThreads[keys[i]];
        unsetWaypointHardwareWatchpointsForThread(
            record ? record.thread : null
        );
    }
    waypointHardwareWatchThreads = {};
    waypointHardwareWatchActive = false;
    const stoppedMode = waypointHardwareWatchMode;
    waypointHardwareWatchMode = "";
    if (stoppedMode === "clear") {
        waypointClearInstructionIssued = false;
        hudGpsActionRequired = false;
        if (!scriptStopping)
            updateHudState();
    }

    if (wasActive || waypointHardwareWatchHitCount > 0) {
        sendLabEvent("waypoint_hardware_watch_stopped", {
            reason:String(reason || "encerrado"),
            mode:stoppedMode,
            map_flag_address:pstr(waypointClearFlagAddress),
            map_flag_hex:labHex(waypointClearFlagAddress, 1),
            captured:waypointHardwareWatchCaptured,
            hit_count:waypointHardwareWatchHitCount,
            header_address:pstr(waypointHardwareWatchHeader),
            initial_descriptor:pstr(waypointHardwareWatchDescriptor),
            candidates:labSanitizeValue(waypointHardwareWatchHits, 0)
        });
    }
}

function completeWaypointHardwareCapture(hit) {
    waypointHardwareWatchCaptured = true;
    waypointWriterCapturedForSession = true;
    if (hit && hit.code)
        waypointWriterCandidates.push(hit.code);
    stopWaypointHardwareWatch("escritor do waypoint capturado");

    log(
        "✅ CÓDIGO DO WAYPOINT CAPTURADO em " +
        String(hit && hit.code
            ? (hit.code.rva || hit.code.address) : "endereço registrado") +
        ". Aguarde a marcação se mover; continue o teste e encerre quando quiser."
    );
    sendLabEvent("waypoint_hardware_writer_captured", {
        hit:labSanitizeValue(hit, 0),
        all_candidates:labSanitizeValue(waypointHardwareWatchHits, 0),
        instruction:
            "aguardar o GPS mover o ponto; continuar a caçar ou encerrar e enviar o ZIP"
    });

    labScheduleRegistryBurst("waypoint_hardware_capturado");
}

function installWaypointHardwareExceptionHandler() {
    if (waypointHardwareExceptionHandlerReady)
        return true;
    if (!WAYPOINT_HARDWARE_WATCH_ENABLED ||
        typeof Process.setExceptionHandler !== "function")
        return false;

    try {
        Process.setExceptionHandler(function (details) {
            if (!waypointHardwareWatchActive ||
                String(details.type || "") !== "single-step")
                return false;

            try {
                waypointHardwareWatchHitCount++;
                const context = details.context || {};
                const pc = context.pc || details.address || ptr(0);
                const memory = details.memory || null;
                const memoryAddress = memory && memory.address
                    ? memory.address : ptr(0);
                const snapshot = waypointHardwareSnapshot();
                const code = waypointCodeContext(pc);
                const hit = {
                    sequence:waypointHardwareWatchHitCount,
                    thread_id:Number(Process.getCurrentThreadId()),
                    exception_type:String(details.type || ""),
                    exception_address:pstr(details.address || pc),
                    memory_operation:memory
                        ? String(memory.operation || "") : "",
                    memory_address:pstr(memoryAddress),
                    watch_source:waypointHardwareWatchSource(memoryAddress),
                    code:code,
                    accurate_backtrace:waypointHardwareBacktrace(
                        context,
                        Backtracer.ACCURATE,
                        true
                    ),
                    fuzzy_backtrace:waypointHardwareBacktrace(
                        context,
                        Backtracer.FUZZY,
                        false
                    ),
                    registry:snapshot
                };
                if (waypointHardwareWatchMode === "clear") {
                    hit.map_flag_address = pstr(waypointClearFlagAddress);
                    hit.map_flag_hex = labHex(waypointClearFlagAddress, 1);
                    hit.map_waypoint_hex = labHex(
                        waypointClearFlagAddress.sub(0x53), 0x60
                    );
                }

                waypointHardwareWatchHits.push(hit);
                if (waypointHardwareWatchHits.length >
                    WAYPOINT_HARDWARE_WATCH_MAX_HITS)
                    waypointHardwareWatchHits.shift();

                sendLabEvent("waypoint_hardware_write", hit);

                if (waypointHardwareWatchMode === "clear") {
                    if (hit.map_flag_hex === "00" &&
                        !waypointClearFlagWriteCaptured) {
                        waypointClearFlagWriteCaptured = true;
                        sendLabEvent("waypoint_clear_flag_writer_captured", {
                            writer:labSanitizeValue(hit, 0),
                            instruction:"aguardar confirmação do catálogo"
                        });
                        setTimeout(function () {
                            confirmWaypointClearCapture(
                                "250 ms após escrita da flag"
                            );
                        }, 250);
                    }
                    if (waypointHardwareWatchHitCount >= 40 &&
                        !waypointClearFlagWriteCaptured) {
                        stopWaypointHardwareWatch(
                            "limite de acessos ao limpar waypoint"
                        );
                        log("🟠 LAB: CAPTURA DA LIMPEZA INCOMPLETA. " +
                            "PARE E ENVIE O ZIP DO LOG.");
                    }
                } else if (snapshot.waypoint_complete === true) {
                    completeWaypointHardwareCapture(hit);
                } else if (waypointHardwareWatchHitCount >=
                        WAYPOINT_HARDWARE_WATCH_MAX_HITS) {
                    stopWaypointHardwareWatch(
                        "limite de acessos atingido sem confirmar tipo 10"
                    );
                    log(
                        "🟠 LAB: limite da captura atingido. Clique em PARAR " +
                        "e envie o ZIP para analisar os candidatos."
                    );
                    setDeathSignalPhase(
                        DEATH_SIGNAL_PHASE_SEND_ZIP,
                        "captura cheia; clique em PARAR"
                    );
                }
            } catch (error) {
                sendLabEvent("waypoint_hardware_handler_error", {
                    error:String(error),
                    hit_count:waypointHardwareWatchHitCount
                });
            }

            // A exceção single-step foi gerada pelos registradores de depuração
            // instalados acima. Consumir somente enquanto a captura está ativa.
            return true;
        });
        waypointHardwareExceptionHandlerReady = true;
        return true;
    } catch (error) {
        sendLabEvent("waypoint_hardware_watch_failed", {
            stage:"exception_handler",
            error:String(error)
        });
        return false;
    }
}

function armWaypointHardwareWatchThread(thread) {
    if (!waypointHardwareWatchActive || !thread)
        return;
    const key = String(Number(thread.id));
    if (waypointHardwareWatchThreads[key])
        return;

    const armed = [];
    const errors = [];
    function arm(id, address, size, label) {
        if (!address || address.isNull())
            return;
        try {
            thread.setHardwareWatchpoint(id, address, size, "w");
            armed.push({
                id:id,
                address:pstr(address),
                size:size,
                label:label
            });
        } catch (error) {
            errors.push({id:id, label:label, error:String(error)});
        }
    }

    if (waypointHardwareWatchMode === "clear") {
        arm(0, waypointClearFlagAddress, 1, "flag ativa do waypoint");
        arm(1, waypointHardwareWatchHeader, 8, "fim do catálogo");
        if (armed.length > 0) {
            waypointHardwareWatchThreads[key] = {
                thread:thread,
                thread_id:Number(thread.id),
                thread_name:String(thread.name || ""),
                armed:armed
            };
        }
        if (errors.length > 0)
            sendLabEvent("waypoint_clear_watch_thread_partial", {
                thread_id:Number(thread.id), errors:errors
            });
        return;
    }

    arm(
        WAYPOINT_HARDWARE_WATCH_HEADER_ID,
        waypointHardwareWatchHeader,
        8,
        "ponteiro final do catálogo"
    );

    try {
        const descriptorRange = Process.findRangeByAddress(
            waypointHardwareWatchDescriptor
        );
        const writable = descriptorRange &&
            String(descriptorRange.protection || "").indexOf("w") >= 0;
        const rangeEnd = writable
            ? descriptorRange.base.add(Number(descriptorRange.size || 0))
            : ptr(0);
        if (writable &&
            waypointHardwareWatchDescriptor.add(0x61).compare(rangeEnd) <= 0) {
            arm(
                WAYPOINT_HARDWARE_WATCH_TYPE_ID,
                waypointHardwareWatchDescriptor.add(0x60),
                1,
                "tipo do próximo descritor"
            );
        }
        if (writable &&
            waypointHardwareWatchDescriptor.add(0x38).compare(rangeEnd) <= 0) {
            arm(
                WAYPOINT_HARDWARE_WATCH_REGISTRY_ID,
                waypointHardwareWatchDescriptor.add(0x30),
                8,
                "ID do próximo descritor"
            );
        }
    } catch (error) {
        errors.push({label:"próximo descritor", error:String(error)});
    }

    if (armed.length > 0) {
        waypointHardwareWatchThreads[key] = {
            thread:thread,
            thread_id:Number(thread.id),
            thread_name:String(thread.name || ""),
            armed:armed
        };
    }
    if (errors.length > 0) {
        sendLabEvent("waypoint_hardware_thread_partial", {
            thread_id:Number(thread.id),
            thread_name:String(thread.name || ""),
            armed:armed,
            errors:errors
        });
    }
}

function armWaypointHardwareWatch(reason) {
    if (!WAYPOINT_HARDWARE_WATCH_ENABLED || scriptStopping ||
        waypointHardwareWatchCaptured ||
        deathSignalPhase === DEATH_SIGNAL_PHASE_SEND_ZIP)
        return false;
    if (waypointHardwareWatchActive)
        return true;
    if (!installWaypointHardwareExceptionHandler())
        return false;

    const bounds = oldCorpseRegistryBounds();
    if (!bounds)
        return false;

    const header = bounds.registry.add(0x78);
    let headerRange = null;
    try { headerRange = Process.findRangeByAddress(header); }
    catch (_) {}
    if (!headerRange ||
        String(headerRange.protection || "").indexOf("w") < 0)
        return false;

    waypointHardwareWatchHeader = header;
    waypointHardwareWatchDescriptor = bounds.end;
    waypointHardwareWatchInitialEnd = bounds.end;
    waypointHardwareWatchThreads = {};
    waypointHardwareWatchHits = [];
    waypointHardwareWatchHitCount = 0;
    waypointHardwareWatchActive = true;

    try {
        if (typeof Process.attachThreadObserver === "function") {
            waypointHardwareWatchObserver = Process.attachThreadObserver({
                onAdded(thread) {
                    armWaypointHardwareWatchThread(thread);
                }
            });
        } else {
            const threads = Process.enumerateThreads();
            for (let i=0; i<threads.length; i++)
                armWaypointHardwareWatchThread(threads[i]);
        }
    } catch (error) {
        sendLabEvent("waypoint_hardware_watch_failed", {
            stage:"arm_threads",
            error:String(error)
        });
    }

    const armedThreads = Object.keys(waypointHardwareWatchThreads);
    if (!armedThreads.length) {
        stopWaypointHardwareWatch("nenhuma thread aceitou o watchpoint");
        return false;
    }

    sendLabEvent("waypoint_hardware_watch_started", {
        reason:String(reason || "aguardando ponto manual"),
        debugger_attached:Process.isDebuggerAttached(),
        registry:pstr(bounds.registry),
        registry_begin:pstr(bounds.begin),
        registry_initial_end:pstr(bounds.end),
        watched_header:pstr(waypointHardwareWatchHeader),
        watched_descriptor:pstr(waypointHardwareWatchDescriptor),
        watched_type:pstr(waypointHardwareWatchDescriptor.add(0x60)),
        watched_registry_id:pstr(waypointHardwareWatchDescriptor.add(0x30)),
        armed_thread_count:armedThreads.length,
        armed_threads:armedThreads.map(function (key) {
            const record = waypointHardwareWatchThreads[key];
            return {
                thread_id:record.thread_id,
                thread_name:record.thread_name,
                watchpoints:record.armed
            };
        }),
        timeout_ms:WAYPOINT_HARDWARE_WATCH_TIMEOUT_MS,
        instruction:"captura de hardware desativada nesta versão"
    });

    log("🧪 Captura de hardware do waypoint iniciada.");
    waypointHardwareWatchTimer = setTimeout(function () {
        if (!waypointHardwareWatchActive)
            return;
        stopWaypointHardwareWatch("tempo de 180 segundos esgotado");
        log(
            "🧪 CAPTURA DO WAYPOINT: janela inicial terminou sem ponto. " +
            "O laboratório continua aberto."
        );
    }, WAYPOINT_HARDWARE_WATCH_TIMEOUT_MS);
    return true;
}

function confirmWaypointClearCapture(reason) {
    if (scriptStopping || !waypointHardwareWatchActive ||
        waypointHardwareWatchMode !== "clear")
        return false;
    let snapshot = null;
    try { snapshot = labCollectRegistry(false); }
    catch (_) {}
    const entries = snapshot && Array.isArray(snapshot.waypoint_entries)
        ? snapshot.waypoint_entries : [];
    if (entries.length > 0)
        return false;
    const map = getMap();
    sendLabEvent("waypoint_clear_confirmed_by_game", {
        reason:String(reason || "catálogo sem ponto"),
        flag_hex:labHex(waypointClearFlagAddress, 1),
        map_pointer:pstr(map),
        previous_descriptor:waypointClearLastDescriptor,
        hardware_writer_captured:waypointClearFlagWriteCaptured,
        pending:labPendingSnapshot()
    });
    waypointHardwareWatchCaptured = true;
    stopWaypointHardwareWatch("ponto retirado do catálogo do jogo");
    waypointClearInstructionIssued = false;
    hudGpsActionRequired = false;
    updateHudState();
    resetMarkerState();
    finalClearDone = true;
    log("🟠 LAB: LIMPEZA DO PONTO CAPTURADA. " +
        "CLIQUE PARAR E ENVIE O ZIP PARA CORRIGIR A REMOÇÃO AUTOMÁTICA.");
    if (pending.length && !map.isNull() &&
        labHex(map.add(0x3d3), 1) === "00") {
        finalClearDone = false;
        scheduleNearestUpdate(300,
            "ponto removido pelo jogador; GPS automático retomado");
    }
    return true;
}

function armWaypointClearWatch(reason) {
    if (waypointHardwareWatchActive &&
        waypointHardwareWatchMode === "clear")
        return true;
    if (!WAYPOINT_HARDWARE_WATCH_ENABLED || scriptStopping ||
        !installWaypointHardwareExceptionHandler())
        return false;
    const map = getMap();
    const bounds = oldCorpseRegistryBounds();
    if (map.isNull() || !bounds)
        return false;
    const flag = map.add(0x3d3);
    const flagRange = Process.findRangeByAddress(flag);
    if (!flagRange || String(flagRange.protection).indexOf("w") < 0)
        return false;

    waypointClearFlagAddress = flag;
    waypointClearFlagWriteCaptured = false;
    waypointHardwareWatchHeader = bounds.registry.add(0x78);
    waypointHardwareWatchDescriptor = bounds.end;
    waypointHardwareWatchInitialEnd = bounds.end;
    waypointHardwareWatchThreads = {};
    waypointHardwareWatchHits = [];
    waypointHardwareWatchHitCount = 0;
    waypointHardwareWatchMode = "clear";
    waypointHardwareWatchActive = true;

    try {
        if (typeof Process.attachThreadObserver === "function") {
            waypointHardwareWatchObserver = Process.attachThreadObserver({
                onAdded(thread) {
                    armWaypointHardwareWatchThread(thread);
                }
            });
        } else {
            const threads = Process.enumerateThreads();
            for (let i=0; i<threads.length; i++)
                armWaypointHardwareWatchThread(threads[i]);
        }
    } catch (error) {
        sendLabEvent("waypoint_clear_watch_failed", {
            stage:"arm_threads", error:String(error)
        });
    }

    const armed = Object.keys(waypointHardwareWatchThreads).length;
    if (armed === 0) {
        stopWaypointHardwareWatch("nenhuma thread aceitou a captura");
        return false;
    }
    sendLabEvent("waypoint_clear_watch_started", {
        reason:String(reason || "ponto antigo persistiu"),
        map_pointer:pstr(map),
        map_flag_address:pstr(flag),
        map_flag_hex:labHex(flag, 1),
        map_waypoint_hex:labHex(map.add(0x380), 0x60),
        registry_header:pstr(waypointHardwareWatchHeader),
        previous_waypoint_descriptor:waypointClearLastDescriptor,
        armed_threads:armed,
        instruction:"após o abate marcado ser coletado, remover o ponto antigo uma vez no mapa"
    });
    waypointHardwareWatchTimer = setTimeout(function () {
        if (!waypointHardwareWatchActive ||
            waypointHardwareWatchMode !== "clear")
            return;
        stopWaypointHardwareWatch("180 segundos sem limpar o waypoint");
        waypointClearInstructionIssued = false;
        hudGpsActionRequired = false;
        updateHudState();
        log("🟠 LAB: CAPTURA DA LIMPEZA NÃO TERMINOU. " +
            "PARE E ENVIE O ZIP DO LOG.");
    }, WAYPOINT_HARDWARE_WATCH_TIMEOUT_MS);
    return true;
}

let waypointRecoverySerial = 0;
function requestWaypointClearCapture(reason, target) {
    if (scriptStopping || waypointClearInstructionIssued)
        return;
    const generation = ++waypointRecoverySerial;
    const expectedSwitch = switchSerial;
    let attempts = 0;
    function inspectAndRetry() {
        if (scriptStopping || generation !== waypointRecoverySerial ||
            switchSerial !== expectedSwitch)
            return;
        const map = getMap();
        if (map.isNull())
            return;
        const flag = labHex(map.add(0x3d3), 1);
        if (flag === "00") {
            waypointClearInstructionIssued = false;
            hudGpsActionRequired = false;
            resetMarkerState();
            if (pending.length)
                scheduleNearestUpdate(80, "mapa já limpo; retomar marcação");
            return;
        }
        let entries = [];
        try {
            const snapshot = labCollectRegistry(false);
            if (snapshot.ok !== true)
                return;
            entries = (snapshot.waypoint_entries || []).filter(function (entry) {
                return registryWaypointPointer(entry) !== null;
            });
        } catch (_) { return; }
        // Uma nova marcação correta não pertence a uma falha antiga.
        const live = pending.find(function (item) {
            return corpseKey(item) === currentKey;
        });
        if (live && entries.length === 1 &&
            distanceXZ(entries[0], live) < 3 &&
            Math.abs(Number(entries[0].y) - Number(live.y)) < 6)
            return;
        if (attempts++ < 2 && (!PROTECT_SETWAYPOINT || markerOwned)) {
            const cleared = clearMarkerInternal();
            sendLabEvent("waypoint_clear_retry", {
                attempt:attempts, succeeded:cleared,
                reason:String(reason || ""), current_key:currentKey
            });
            setTimeout(inspectAndRetry, 500);
            return;
        }
        waypointClearInstructionIssued = true;
        hudGpsActionRequired = true;
        updateHudState();
        sendLabEvent("waypoint_recovery_exhausted", {
            reason:String(reason || ""), target:labSanitizeValue(target, 0),
            waypoint_entries:labSanitizeValue(entries, 0),
            pending:labPendingSnapshot(), hardware_watch_started:false
        });
        log("🟠 LAB: NÃO CONSEGUI ATUALIZAR A MARCAÇÃO. " +
            "CLIQUE PARAR E ENVIE O ZIP DO LOG.");
    }
    inspectAndRetry();
}

function scanWaypointIdConstantReferences(reason) {
    if (!WAYPOINT_ID_CONSTANT_SCAN_ENABLED)
        return;

    if (waypointIdScanStarted || waypointIdScanFinished || scriptStopping)
        return;

    waypointIdScanStarted = true;
    const ranges = [];
    const moduleEnd = BASE.add(Number(Process.mainModule.size));

    function addRanges(protection) {
        let found = [];
        try {
            found = Process.enumerateRanges({
                protection:protection,
                coalesce:false
            });
        } catch (_) {}
        for (let i=0; i<found.length; i++) {
            const range = found[i];
            if (range.base.compare(BASE) >= 0 &&
                range.base.compare(moduleEnd) < 0)
                ranges.push(range);
        }
    }

    addRanges("r-x");
    addRanges("r--");
    addRanges("rw-");
    let rangeIndex = 0;
    const hits = [];
    const startedAt = Date.now();

    sendLabEvent("waypoint_id_constant_scan_started", {
        reason:String(reason || "localizar criação do waypoint"),
        pattern:WAYPOINT_ID_PATTERN,
        range_count:ranges.length
    });

    function finish() {
        waypointIdScanStarted = false;
        waypointIdScanFinished = true;
        // Confirma a função na build instalada antes de preparar uma chamada.
        let setterCandidate = null;
        const validated = new Set();
        for (let i=0; i<hits.length; i++) {
            if (String(hits[i].protection).indexOf("x") < 0)
                continue;
            try {
                const idAddress = ptr(hits[i].address);
                const entry = idAddress.sub(0x14);
                if (labHex(entry, 28) !==
                    "40534883ec3080b9d303000000488bd9752148b88967452301000000")
                    continue;
                validated.add(pstr(entry));
                setterCandidate = entry;
            } catch (_) {}
        }
        if (validated.size === 1 && setterCandidate !== null) {
            try {
                autoWaypointSetter = new NativeFunction(
                    setterCandidate, 'void', ['pointer', 'pointer']
                );
                waypointConfirmedSetterAddress = pstr(setterCandidate);
                waypointConfirmedSetterInterceptor = Interceptor.attach(
                    setterCandidate, {
                    onEnter(args) {
                        if (scriptStopping || waypointConfirmedSetterCaptures >= 6)
                            return;
                        const map = args[0];
                        const vector = args[1];
                        const target = readHookPosition(vector);
                        if (scriptStopping)
                            return;
                        waypointConfirmedSetterCaptures++;
                        sendLabEvent("waypoint_setter_arguments_captured", {
                            capture:waypointConfirmedSetterCaptures,
                            function_address:waypointConfirmedSetterAddress,
                            function_rva:setterCandidate.sub(BASE).toString(),
                            map_pointer:pstr(map),
                            vector_pointer:pstr(vector),
                            vector:target,
                            id_at_map_0x3a0:labHex(map.add(0x3a0), 8),
                            type_at_map_0x3d0:labHex(map.add(0x3d0), 8),
                            map_coordinates_hex:labHex(map.add(0x390), 12),
                            caller:waypointHardwareBacktrace(
                                this.context, Backtracer.ACCURATE, false
                            ),
                            pending:labPendingSnapshot()
                        });
                        if (waypointConfirmedSetterCaptures === 1) {
                            // Confirma se a referência global ainda é única.
                            rayXDiscoverMapSingletonSlots(
                                map, "setter_atual_0.9.50", false
                            );
                        }
                        if (!insideSet && PROTECT_SETWAYPOINT &&
                            pending.length && soloConfirmed) {
                            protectManualWaypoint();
                        }
                    }
                });
                sendLabEvent("waypoint_setter_hook_ready", {
                    function_address:waypointConfirmedSetterAddress,
                    candidate_count:validated.size,
                    signature:"0x123456789 e prologo de 20 bytes"
                });
                scheduleNearestUpdate(120, "assinatura do waypoint validada");
            } catch (error) {
                waypointConfirmedSetterAddress = "";
                autoWaypointSetter = null;
                sendLabEvent("waypoint_setter_hook_failed", {
                    error:String(error)
                });
            }
        } else {
            sendLabEvent("waypoint_setter_signature_unavailable", {
                candidates:validated.size,
                reason:"assinatura da build mudou ou não é única"
            });
        }
        sendLabEvent("waypoint_id_constant_scan_finished", {
            reason:String(reason || "localizar criação do waypoint"),
            elapsed_ms:Date.now() - startedAt,
            hit_count:hits.length,
            hits:hits
        });
        if (autoWaypointSetter === null && pending.length)
            log("🟠 LAB: ERRO DE MARCAÇÃO. PARE E ENVIE O ZIP DO LOG.");
    }

    function nextRange() {
        if (scriptStopping || rangeIndex >= ranges.length ||
            hits.length >= WAYPOINT_ID_MAX_HITS) {
            finish();
            return;
        }

        const range = ranges[rangeIndex++];
        try {
            Memory.scan(
                range.base,
                Number(range.size),
                WAYPOINT_ID_PATTERN,
                {
                    onMatch(address, size) {
                        let rva = "";
                        try {
                            rva = address.sub(BASE).toString();
                            if (rva.indexOf("0x") !== 0)
                                rva = "0x" + rva;
                        } catch (_) {}
                        let before = 32;
                        try {
                            before = Math.min(
                                before,
                                address.sub(range.base).toUInt32()
                            );
                        } catch (_) {
                            before = 0;
                        }
                        hits.push({
                            address:pstr(address),
                            rva:rva,
                            protection:String(range.protection || ""),
                            match_size:Number(size || 8),
                            surrounding_hex:labHex(
                                address.sub(before),
                                before + 96
                            ),
                            executable_context:
                                String(range.protection || "")
                                    .indexOf("x") >= 0
                                    ? waypointCodeContext(address) : null
                        });
                        if (hits.length >= WAYPOINT_ID_MAX_HITS)
                            return "stop";
                    },
                    onError(error) {
                        sendLabEvent(
                            "waypoint_id_constant_scan_range_error",
                            {
                                range_base:pstr(range.base),
                                range_size:Number(range.size),
                                error:String(error)
                            }
                        );
                    },
                    onComplete() {
                        setTimeout(nextRange, 0);
                    }
                }
            );
        } catch (error) {
            sendLabEvent("waypoint_id_constant_scan_range_error", {
                range_base:pstr(range.base),
                range_size:Number(range.size),
                error:String(error)
            });
            setTimeout(nextRange, 0);
        }
    }

    nextRange();
}

function stopWaypointWriterMonitor(reason) {
    if (!WAYPOINT_WRITER_MONITOR_ENABLED)
        return;

    const wasArmed = waypointWriterMonitorArmed;
    waypointWriterMonitorArmed = false;
    waypointWriterMonitorGeneration++;
    waypointWriterMonitorTimer = clearTimeoutSafe(
        waypointWriterMonitorTimer
    );
    try {
        if (typeof MemoryAccessMonitor !== "undefined")
            MemoryAccessMonitor.disable();
    } catch (_) {}

    if (wasArmed || waypointWriterCandidates.length > 0) {
        sendLabEvent("waypoint_writer_monitor_stopped", {
            reason:String(reason || "encerrado"),
            target_base:waypointWriterMonitorBase,
            target_size:waypointWriterMonitorSize,
            access_count:waypointWriterAccessCount,
            candidates:labSanitizeValue(waypointWriterCandidates, 0)
        });
    }
}

function enableWaypointWriterMonitorAt(target, size, generation) {
    if (scriptStopping || waypointWriterCapturedForSession ||
        generation !== waypointWriterMonitorGeneration ||
        typeof MemoryAccessMonitor === "undefined")
        return;

    try {
        waypointWriterMonitorArmed = true;
        MemoryAccessMonitor.enable([{
            base:target,
            size:size
        }], {
            onAccess(details) {
                if (generation !== waypointWriterMonitorGeneration)
                    return;

                waypointWriterAccessCount++;
                const operation = String(details.operation || "");
                const from = details.from || ptr(0);
                const address = details.address || ptr(0);
                const context = waypointCodeContext(from);
                const exactTarget = !address.isNull() &&
                    address.compare(target) >= 0 &&
                    address.compare(target.add(size)) < 0;
                const usefulWrite = operation === "write" && exactTarget &&
                    context.inside_game_module === true;

                try { MemoryAccessMonitor.disable(); }
                catch (_) {}
                waypointWriterMonitorArmed = false;

                sendLabEvent("waypoint_writer_memory_access", {
                    operation:operation,
                    accessed_address:pstr(address),
                    exact_target:exactTarget,
                    range_index:Number(details.rangeIndex || 0),
                    page_index:Number(details.pageIndex || 0),
                    pages_completed:Number(details.pagesCompleted || 0),
                    code:context,
                    access_count:waypointWriterAccessCount
                });

                if (usefulWrite) {
                    const key = String(context.address || "");
                    let duplicate = false;
                    for (let i=0; i<waypointWriterCandidates.length; i++) {
                        if (waypointWriterCandidates[i].address === key) {
                            duplicate = true;
                            break;
                        }
                    }
                    if (!duplicate)
                        waypointWriterCandidates.push(context);
                    waypointWriterCapturedForSession = true;
                    waypointWriterMonitorTimer = clearTimeoutSafe(
                        waypointWriterMonitorTimer
                    );
                    log(
                        "🧪 WAYPOINT AUTOMÁTICO: escritor novo capturado em " +
                        (context.rva || context.address) +
                        "; dados guardados no ZIP."
                    );
                    const timer = setTimeout(function () {
                        labCaptureRegistry(
                            "escritor_waypoint_capturado+250ms",
                            true,
                            true
                        );
                    }, 250);
                    labBurstTimers.push(timer);
                    return;
                }

                if (waypointWriterAccessCount >=
                        WAYPOINT_WRITER_MAX_ACCESSES ||
                    !rayXWaitingForManualWaypoint)
                    return;

                const timer = setTimeout(function () {
                    enableWaypointWriterMonitorAt(
                        target,
                        size,
                        generation
                    );
                }, 10);
                labBurstTimers.push(timer);
            }
        });
    } catch (error) {
        waypointWriterMonitorArmed = false;
        sendLabEvent("waypoint_writer_monitor_failed", {
            stage:"enable",
            target_base:pstr(target),
            target_size:size,
            error:String(error)
        });
    }
}

function armWaypointWriterMonitor(reason) {
    if (!WAYPOINT_WRITER_MONITOR_ENABLED ||
        waypointWriterMonitorArmed || waypointWriterCapturedForSession ||
        scriptStopping || typeof MemoryAccessMonitor === "undefined")
        return false;

    const bounds = oldCorpseRegistryBounds();
    if (!bounds)
        return false;

    const target = bounds.end;
    let range = null;
    try { range = Process.findRangeByAddress(target); }
    catch (_) {}
    if (!range || String(range.protection || "").indexOf("w") < 0)
        return false;

    let available = 0;
    try {
        available = range.base.add(Number(range.size)).sub(target).toUInt32();
    } catch (_) {
        return false;
    }
    const size = Math.min(OLD_CORPSE_DESCRIPTOR_SIZE, available);
    if (size < 64)
        return false;

    stopWaypointWriterMonitor("reinício do diagnóstico");
    waypointWriterMonitorGeneration++;
    const generation = waypointWriterMonitorGeneration;
    waypointWriterMonitorBase = pstr(target);
    waypointWriterMonitorSize = size;
    waypointWriterAccessCount = 0;
    waypointWriterCandidates = [];

    sendLabEvent("waypoint_writer_monitor_started", {
        reason:String(reason || "aguardando ponto manual"),
        target_base:waypointWriterMonitorBase,
        target_size:size,
        registry_begin:pstr(bounds.begin),
        registry_end:pstr(bounds.end),
        timeout_ms:WAYPOINT_WRITER_MONITOR_MS,
        instruction:"monitor legado desativado nesta versão"
    });
    enableWaypointWriterMonitorAt(target, size, generation);
    waypointWriterMonitorTimer = setTimeout(function () {
        if (generation !== waypointWriterMonitorGeneration)
            return;
        stopWaypointWriterMonitor("tempo de captura esgotado");
    }, WAYPOINT_WRITER_MONITOR_MS);
    return true;
}

function captureWaypointObjectDiagnostics(entry, descriptor, reason) {
    const descriptorText = pstr(descriptor);
    if (waypointObjectDiagnosticsSeen.has(descriptorText))
        return;
    waypointObjectDiagnosticsSeen.add(descriptorText);

    let object = ptr(0);
    let vtable = ptr(0);
    try { object = descriptor.add(0x48).readPointer(); }
    catch (_) {}
    if (stableReadablePointer(object)) {
        try { vtable = object.readPointer(); }
        catch (_) {}
    }

    const functions = [];
    if (stableReadablePointer(vtable)) {
        for (let i=0; i<WAYPOINT_VTABLE_FUNCTIONS; i++) {
            let target = ptr(0);
            try { target = vtable.add(i * Process.pointerSize).readPointer(); }
            catch (_) { continue; }
            if (!stableReadablePointer(target))
                continue;
            const context = waypointCodeContext(target);
            functions.push({
                index:i,
                address:pstr(target),
                code:context
            });
        }
    }

    sendLabEvent("waypoint_object_diagnostics", {
        reason:String(reason || "waypoint tipo 10 detectado"),
        entry:labSanitizeValue(entry, 0),
        descriptor:descriptorText,
        descriptor_raw_hex:labHex(
            descriptor,
            OLD_CORPSE_DESCRIPTOR_SIZE
        ),
        object_pointer:pstr(object),
        object_raw_hex:stableReadablePointer(object)
            ? labHex(object, 512) : "",
        object_pointer_previews:stableReadablePointer(object)
            ? labPointerPreviews(object) : [],
        vtable_pointer:pstr(vtable),
        vtable_raw_hex:stableReadablePointer(vtable)
            ? labHex(
                vtable,
                WAYPOINT_VTABLE_FUNCTIONS * Process.pointerSize
            ) : "",
        vtable_functions:functions,
        writer_candidates:labSanitizeValue(
            waypointWriterCandidates,
            0
        )
    });
}

function registryWaypointPointer(entry) {
    if (!entry ||
        Number(entry.type) !== MANUAL_WAYPOINT_TYPE ||
        normalizeRegistryId(entry.registry_id) !==
            MANUAL_WAYPOINT_REGISTRY_ID)
        return null;

    const bounds = oldCorpseRegistryBounds();
    if (!bounds)
        return null;

    try {
        const descriptor = ptr(String(entry.descriptor || ""));
        const index = Number(entry.index);
        if (!Number.isFinite(index) || index < 0)
            return null;

        const expected = bounds.begin.add(
            index * OLD_CORPSE_DESCRIPTOR_SIZE
        );
        if (descriptor.compare(expected) !== 0 ||
            descriptor.compare(bounds.begin) < 0 ||
            descriptor.add(OLD_CORPSE_DESCRIPTOR_SIZE).compare(bounds.end) > 0)
            return null;

        const range = Process.findRangeByAddress(descriptor);
        const protection = range ? String(range.protection || "") : "";
        if (!range || protection.indexOf("r") < 0 ||
            protection.indexOf("w") < 0)
            return null;

        if (descriptor.add(0x60).readU8() !== MANUAL_WAYPOINT_TYPE ||
            normalizeRegistryId(
                descriptor.add(0x30).readU64().toString()
            ) !== MANUAL_WAYPOINT_REGISTRY_ID)
            return null;

        return descriptor;
    } catch (_) {
        return null;
    }
}

function applyRegistryWaypointFallback(snapshot, reason) {
    if (!REGISTRY_WAYPOINT_FALLBACK || PROTECT_SETWAYPOINT ||
        multiplayerBlocked || scriptStopping || !soloConfirmed ||
        !pending.length || !capturedMap.isNull() ||
        autoWaypointAwaitingRegistry !== "" ||
        (autoWaypointSetter !== null &&
            autoMapVerifiedPointer === pstr(getSingletonMap())))
        return false;

    const entries = snapshot && Array.isArray(snapshot.waypoint_entries)
        ? snapshot.waypoint_entries : [];
    if (!entries.length)
        return false;

    const entry = entries[entries.length - 1];
    const descriptor = registryWaypointPointer(entry);
    if (descriptor === null)
        return false;

    const descriptorText = pstr(descriptor);
    let before = null;
    try {
        before = {
            x:descriptor.add(0x20).readFloat(),
            y:descriptor.add(0x24).readFloat(),
            z:descriptor.add(0x28).readFloat()
        };
    } catch (_) {
        return false;
    }

    const player = getPlayerPos();
    const navigationReference = player || lastNavigationAnchor || before;
    const navigationReferenceSource = player
        ? "posição real do jogador"
        : (lastNavigationAnchor
            ? "último corpo coletado"
            : "ponto manual criado sobre o jogador");
    const index = findNearestIndex(navigationReference);
    if (index < 0 || index >= pending.length)
        return false;

    const target = pending[index];
    const targetKey = corpseKey(target);

    if (!registryWaypointOriginalPosition ||
        registryWaypointLastDescriptor !== descriptorText) {
        registryWaypointOriginalPosition = {
            x:before.x,
            y:before.y,
            z:before.z
        };
    }

    const alreadyAtTarget = distanceXZ(before, target) <= 0.20 &&
        Math.abs(Number(before.y) - Number(target.y)) <= 0.50;
    let writeError = "";

    if (!alreadyAtTarget) {
        try {
            descriptor.add(0x20).writeFloat(Number(target.x));
            descriptor.add(0x24).writeFloat(Number(target.y));
            descriptor.add(0x28).writeFloat(Number(target.z));
        } catch (error) {
            writeError = String(error || "falha ao mover o waypoint");
        }
    }

    let after = before;
    try {
        after = {
            x:descriptor.add(0x20).readFloat(),
            y:descriptor.add(0x24).readFloat(),
            z:descriptor.add(0x28).readFloat()
        };
    } catch (error) {
        if (writeError === "")
            writeError = String(error || "falha ao confirmar o waypoint");
    }
    const verified = writeError === "" &&
        distanceXZ(after, target) <= 0.20 &&
        Math.abs(Number(after.y) - Number(target.y)) <= 0.50;

    const shouldReport = !alreadyAtTarget ||
        registryWaypointLastDescriptor !== descriptorText ||
        registryWaypointLastTargetKey !== targetKey || !verified;
    if (shouldReport) {
        sendLabEvent(
            verified
                ? "registry_waypoint_fallback_succeeded"
                : "registry_waypoint_fallback_failed",
            {
                capture_reason:String(reason || "monitor"),
                descriptor:descriptorText,
                waypoint_registry_id:MANUAL_WAYPOINT_REGISTRY_ID,
                target_index:index,
                target_key:targetKey,
                target:labSanitizeValue(target, 0),
                navigation_reference:navigationReference,
                navigation_reference_source:navigationReferenceSource,
                before:before,
                after:after,
                already_at_target:alreadyAtTarget,
                memory_write_performed:!alreadyAtTarget && writeError === "",
                verified:verified,
                error:writeError
            }
        );
    }

    if (!verified)
        return false;

    if (targetKey === preferredWaypointKey)
        preferredWaypointKey = "";

    switchTimer = clearTimeoutSafe(switchTimer);
    nearestUpdateTimer = clearTimeoutSafe(nearestUpdateTimer);
    switchSerial++;
    currentIndex = index;
    currentKey = targetKey;
    switchingKey = "";
    markerOwned = true;
    finalClearDone = false;
    manualWaypointOverride = false;
    rayXWaitingForManualWaypoint = false;
    hudGpsActionRequired = false;
    registryWaypointLastDescriptor = descriptorText;
    registryWaypointLastIndex = Number(entry.index);
    registryWaypointLastTargetKey = targetKey;
    registryWaypointLastApplyAt = Date.now();
    stopHudCountdown("GPS aplicado pelo catálogo do mapa");
    updateHudState();

    const logKey = descriptorText + "|" + targetKey;
    if (registryWaypointLastLogKey !== logKey) {
        registryWaypointLastLogKey = logKey;
        log(
            "📍 GPS PELO CATÁLOGO -> " + corpseDesc(target) +
            " | feche o mapa e siga a marcação | pendentes=" +
            pending.length
        );
        if (labRecoveryStage === 0)
            log("🟢 LAB: SEM AÇÃO NECESSÁRIA.");
    }

    return true;
}

function restoreRegistryWaypointFallback(reason) {
    if (!REGISTRY_WAYPOINT_FALLBACK ||
        !registryWaypointOriginalPosition ||
        registryWaypointLastDescriptor === "" ||
        registryWaypointLastIndex < 0)
        return false;

    const entry = {
        type:MANUAL_WAYPOINT_TYPE,
        registry_id:MANUAL_WAYPOINT_REGISTRY_ID,
        descriptor:registryWaypointLastDescriptor,
        index:registryWaypointLastIndex
    };
    const descriptor = registryWaypointPointer(entry);
    if (descriptor === null)
        return false;

    try {
        descriptor.add(0x20).writeFloat(
            Number(registryWaypointOriginalPosition.x)
        );
        descriptor.add(0x24).writeFloat(
            Number(registryWaypointOriginalPosition.y)
        );
        descriptor.add(0x28).writeFloat(
            Number(registryWaypointOriginalPosition.z)
        );
        sendLabEvent("registry_waypoint_original_restored", {
            reason:String(reason || "GPS encerrado"),
            descriptor:registryWaypointLastDescriptor,
            restored_position:registryWaypointOriginalPosition
        });
        log("🧭 Ponto manual devolvido à posição original.");
        registryWaypointLastTargetKey = "";
        return true;
    } catch (error) {
        sendLabEvent("registry_waypoint_original_restore_failed", {
            reason:String(reason || "GPS encerrado"),
            descriptor:registryWaypointLastDescriptor,
            error:String(error)
        });
        return false;
    }
}

function labCaptureRegistry(reason, deep, force) {
    const snapshot = labCollectRegistry(!!deep);
    const fingerprint = labRegistryFingerprint(snapshot);
    const now = Date.now();
    const changed = fingerprint !== labLastRegistryFingerprint;
    const heartbeat = now - labLastRegistryHeartbeatAt >=
        LAB_REGISTRY_HEARTBEAT_MS;

    labTraceRegistryDelta(snapshot, reason);
    applyRegistryWaypointFallback(snapshot, reason);
    if (waypointHardwareWatchActive &&
        waypointHardwareWatchMode === "clear" &&
        snapshot.ok === true &&
        Array.isArray(snapshot.waypoint_entries) &&
        snapshot.waypoint_entries.length === 0)
        confirmWaypointClearCapture(reason);
    if (snapshot.ok === true && markerOwned &&
        autoWaypointAwaitingRegistry === "" &&
        Array.isArray(snapshot.waypoint_entries) &&
        snapshot.waypoint_entries.length === 0) {
        const removedKey = currentKey;
        resetMarkerState();
        finalClearDone = false;
        sendLabEvent("waypoint_player_removed_marker", {
            reason:String(reason || "catálogo mudou"),
            former_target:removedKey,
            pending:labPendingSnapshot()
        });
        if (pending.length &&
            (!PROTECT_SETWAYPOINT || !manualWaypointOverride))
            scheduleNearestUpdate(350,
                "ponto removido; recriar marcador automaticamente");
    }

    if (force || changed || heartbeat) {
        labSnapshotSequence++;
        labLastRegistryFingerprint = fingerprint;
        labLastRegistryHeartbeatAt = now;
        snapshot.sequence = labSnapshotSequence;
        snapshot.capture_reason = reason || "monitor";
        snapshot.deep = !!deep;
        snapshot.changed = changed;
        snapshot.player = getPlayerPos();
        snapshot.pending = labPendingSnapshot();
        snapshot.current_key = currentKey;
        snapshot.current_index = currentIndex;
        snapshot.marker_owned = markerOwned;
        snapshot.initial_scan_started = initialOldCorpseScanStarted;
        snapshot.initial_scan_finished = initialOldCorpseScanFinished;
        snapshot.initial_scan_attempt = initialOldCorpseScanAttempts;
        sendLabEvent("registry_snapshot", snapshot);
    }

    return {
        ok:snapshot.ok === true,
        reason:snapshot.reason || "",
        sequence:labSnapshotSequence,
        changed:changed,
        type39_count:snapshot.ok === true
            ? snapshot.type39_entries.length
            : 0
    };
}

function labScheduleRegistryBurst(reason) {
    // A leitura periódica continua disponível. Cinco capturas extras por
    // morte/coleta/pista se acumulavam enquanto vários animais eram abatidos.
    // Dê primeiro tempo à atualização do GPS e faça só uma conferência leve.
    const quickEvent = reason === "morte_confirmada" ||
        reason === "coleta_confirmada" ||
        reason === "pista_de_animal_morto" || reason === "f6";
    const delays = quickEvent ? [1500] : [0, 250, 1000, 5000, 15000];

    for (let i=0; i<delays.length; i++) {
        const delay = delays[i];
        const timer = setTimeout(function () {
            const deep = !quickEvent &&
                (delay === 0 || delay === 1000 || delay === 5000);
            labCaptureRegistry(`${reason || "evento"}+${delay}ms`,
                deep, !quickEvent);
        }, delay);
        labBurstTimers.push(timer);
    }
}

// -----------------------------------------------------------
// LABORATÓRIO DE ID ESTÁVEL + DISPONIBILIDADE REAL
// -----------------------------------------------------------

function stableRegistryIdPattern(entry) {
    let hex = String(entry && entry.registry_id_hex || "")
        .replace(/[^0-9a-f]/gi, "")
        .toLowerCase();

    if (hex.length !== 16) {
        try {
            const descriptor = ptr(String(entry.descriptor || ""));
            hex = labHex(descriptor.add(0x30), 8);
        } catch (_) {
            hex = "";
        }
    }

    if (hex.length !== 16)
        return "";

    return hex.match(/.{2}/g).join(" ");
}

function stableReadablePointer(value) {
    if (!value || value.isNull())
        return false;

    try {
        const range = Process.findRangeByAddress(value);
        return !!range && String(range.protection || "").indexOf("r") >= 0;
    } catch (_) {
        return false;
    }
}

function inspectStableAvailabilityRecord(address, entry, rangeInfo) {
    try {
        const registryId = normalizeRegistryId(address.readU64().toString());
        const expectedId = normalizeRegistryId(entry.registry_id);

        if (registryId === "" || registryId !== expectedId)
            return null;

        const x = address.add(STABLE_RECORD_COORD_X_OFFSET).readFloat();
        const y = address.add(STABLE_RECORD_COORD_Y_OFFSET).readFloat();
        const z = address.add(STABLE_RECORD_COORD_Z_OFFSET).readFloat();

        if (!validCoord(x) || !validCoord(y) || !validCoord(z))
            return null;

        const dx = Math.abs(x - Number(entry.x));
        const dy = Math.abs(y - Number(entry.y));
        const dz = Math.abs(z - Number(entry.z));
        const coordinatesMatch =
            dx <= STABLE_RECORD_COORD_TOLERANCE_XZ &&
            dz <= STABLE_RECORD_COORD_TOLERANCE_XZ &&
            dy <= STABLE_RECORD_COORD_TOLERANCE_Y;

        if (!coordinatesMatch)
            return null;

        const availability = address.add(
            STABLE_RECORD_AVAILABLE_OFFSET
        ).readU32();
        // O estado ocupa o primeiro byte deste campo. Alguns animais usam os
        // bytes seguintes para outros dados, por isso readU32 confundia um
        // corpo real (07 00 xx xx) e também aceitava estruturas no estado 2.
        const lifecycle = address.add(
            STABLE_RECORD_LIFECYCLE_OFFSET
        ).readU8();
        const pointers = [];
        let nonzeroPointers = 0;
        let readablePointers = 0;

        for (let i=0; i<STABLE_RECORD_POINTER_OFFSETS.length; i++) {
            const offset = STABLE_RECORD_POINTER_OFFSETS[i];
            let value = ptr(0);
            try { value = address.add(offset).readPointer(); }
            catch (_) {}
            const nonzero = !!value && !value.isNull();
            const readable = nonzero && stableReadablePointer(value);
            if (nonzero)
                nonzeroPointers++;
            if (readable)
                readablePointers++;
            pointers.push({
                offset:labOffsetName(offset),
                value:pstr(value),
                nonzero:nonzero,
                readable:readable
            });
        }

        let weight = null;
        try {
            const value = address.add(STABLE_RECORD_WEIGHT_OFFSET).readFloat();
            weight = Number.isFinite(value) && value > 0 && value < 100000
                ? value : null;
        } catch (_) {}

        const active = availability === 1 &&
            lifecycle === STABLE_RECORD_ACTIVE_STATE &&
            nonzeroPointers >= 2 && readablePointers >= 1;
        const inactive = availability === 0 && nonzeroPointers === 0;
        const classification = active
            ? "available_body"
            : (inactive ? "collected_history" : "ambiguous");

        return Object.assign({}, entry, {
            stable_record_address:pstr(address),
            stable_record_range_base:String(rangeInfo.base || ""),
            stable_record_range_size:Number(rangeInfo.size || 0),
            stable_record_range_protection:String(
                rangeInfo.protection || ""
            ),
            stable_record_range_file:String(rangeInfo.file || ""),
            stable_record_x:x,
            stable_record_y:y,
            stable_record_z:z,
            stable_coordinate_delta:{x:dx, y:dy, z:dz},
            stable_availability:availability,
            stable_lifecycle:lifecycle,
            stable_nonzero_pointers:nonzeroPointers,
            stable_readable_pointers:readablePointers,
            stable_pointers:pointers,
            stable_weight:weight,
            stable_classification:classification,
            stable_available:active,
            stable_inactive:inactive,
            stable_raw_hex:labHex(address, STABLE_RECORD_RAW_SIZE)
        });
    } catch (_) {
        return null;
    }
}

function stableRangeInfo(range) {
    let filePath = "";
    try { filePath = range.file ? String(range.file.path || "") : ""; }
    catch (_) {}
    return {
        base:range.base,
        size:Number(range.size || 0),
        protection:String(range.protection || ""),
        file:filePath
    };
}

function stableCachedRange() {
    if (stableMetadataRangeBase === "" || stableMetadataRangeSize <= 0)
        return null;

    try {
        const base = ptr(stableMetadataRangeBase);
        const live = Process.findRangeByAddress(base);

        if (!live || live.base.compare(base) !== 0 ||
            Number(live.size || 0) !== stableMetadataRangeSize ||
            String(live.protection || "").indexOf("r") < 0)
            return null;

        return stableRangeInfo(live);
    } catch (_) {
        return null;
    }
}

function rememberStableRange(range) {
    stableMetadataRangeBase = pstr(range.base);
    stableMetadataRangeSize = Number(range.size || 0);
    stableMetadataRangeProtection = String(range.protection || "");
    stableMetadataRangeFile = String(range.file || "");
}

function forgetStableRange() {
    stableMetadataRangeBase = "";
    stableMetadataRangeSize = 0;
    stableMetadataRangeProtection = "";
    stableMetadataRangeFile = "";
}

function stableDiscoveryRanges() {
    let ranges = [];
    try {
        ranges = Process.enumerateRanges({
            protection:"rw-",
            coalesce:false
        });
    } catch (_) {
        return [];
    }

    const normalized = ranges.map(stableRangeInfo).filter(function (range) {
        return range.file === "" &&
            range.size >= STABLE_METADATA_FALLBACK_MIN_SIZE &&
            range.size <= STABLE_METADATA_FALLBACK_MAX_SIZE;
    });

    normalized.sort(function (left, right) {
        const leftExact = left.size === STABLE_METADATA_EXACT_RANGE_SIZE
            ? 0 : 1;
        const rightExact = right.size === STABLE_METADATA_EXACT_RANGE_SIZE
            ? 0 : 1;
        if (leftExact !== rightExact)
            return leftExact - rightExact;
        return left.size - right.size;
    });

    const selected = [];
    let bytes = 0;
    for (let i=0; i<normalized.length; i++) {
        const range = normalized[i];
        if (bytes + range.size > STABLE_METADATA_DISCOVERY_MAX_BYTES)
            continue;
        selected.push(range);
        bytes += range.size;
    }
    return selected;
}

function scanStableEntryInRange(entry, range, serial, callback) {
    const pattern = stableRegistryIdPattern(entry);
    if (pattern === "") {
        callback(null, "id_pattern_unavailable");
        return;
    }

    let found = null;
    let firstAmbiguous = null;
    let scanError = "";
    try {
        Memory.scan(range.base, range.size, pattern, {
            onMatch(address) {
                const inspected = inspectStableAvailabilityRecord(
                    address,
                    entry,
                    range
                );
                if (inspected) {
                    if (inspected.stable_available === true ||
                        inspected.stable_inactive === true) {
                        found = inspected;
                        return "stop";
                    }
                    if (!firstAmbiguous)
                        firstAmbiguous = inspected;
                }
            },
            onError(reason) {
                scanError = String(reason || "memory_scan_error");
            },
            onComplete() {
                if (serial !== stableAvailabilityScanSerial)
                    callback(null, "cancelled");
                else
                    callback(found || firstAmbiguous, scanError);
            }
        });
    } catch (error) {
        callback(null, String(error));
    }
}

function scanStableEntriesInRange(entries, range, serial, callback) {
    const results = [];
    const errors = [];
    let index = 0;

    function next() {
        if (scriptStopping || serial !== stableAvailabilityScanSerial) {
            callback(results, errors, true);
            return;
        }
        if (index >= entries.length) {
            callback(results, errors, false);
            return;
        }

        const entry = entries[index++];
        scanStableEntryInRange(entry, range, serial, function (found, error) {
            if (found)
                results.push(found);
            if (error !== "" && error !== "cancelled")
                errors.push({
                    registry_id:normalizeRegistryId(entry.registry_id),
                    error:error
                });
            setTimeout(next, 0);
        });
    }

    next();
}

function discoverStableMetadataRange(entries, serial, callback) {
    const ranges = stableDiscoveryRanges();
    const anchors = entries.slice(0, STABLE_METADATA_DISCOVERY_MAX_ANCHORS);
    let anchorIndex = 0;
    let rangeIndex = 0;
    let scannedBytes = 0;
    let scanErrors = 0;
    let nextProgress = 256 * 1024 * 1024;
    const plannedBytes = ranges.reduce(function (sum, range) {
        return sum + range.size;
    }, 0) * Math.max(1, anchors.length);

    sendLabEvent("stable_range_discovery_started", {
        anchor_count:anchors.length,
        range_count:ranges.length,
        planned_bytes_upper_bound:plannedBytes,
        exact_range_size:STABLE_METADATA_EXACT_RANGE_SIZE
    });

    function finish(range, record, reason) {
        sendLabEvent("stable_range_discovery_finished", {
            found:!!range,
            reason:reason || "",
            scanned_bytes:scannedBytes,
            scan_errors:scanErrors,
            range:range ? {
                base:pstr(range.base),
                size:range.size,
                protection:range.protection,
                file:range.file
            } : null,
            anchor_record:record ? labSanitizeValue(record, 0) : null
        });
        callback(range, record, reason || "");
    }

    function next() {
        if (scriptStopping || serial !== stableAvailabilityScanSerial) {
            finish(null, null, "cancelled");
            return;
        }
        if (!anchors.length || !ranges.length) {
            finish(null, null, "no_candidate_ranges_or_ids");
            return;
        }
        if (anchorIndex >= anchors.length) {
            finish(null, null, "stable_range_not_found");
            return;
        }
        if (rangeIndex >= ranges.length) {
            anchorIndex++;
            rangeIndex = 0;
            setTimeout(next, 0);
            return;
        }

        const entry = anchors[anchorIndex];
        const range = ranges[rangeIndex++];
        scanStableEntryInRange(entry, range, serial, function (found, error) {
            scannedBytes += range.size;
            if (error !== "" && error !== "cancelled")
                scanErrors++;
            if (scannedBytes >= nextProgress) {
                sendLabEvent("stable_range_discovery_progress", {
                    scanned_bytes:scannedBytes,
                    anchor_index:anchorIndex,
                    range_index:rangeIndex,
                    scan_errors:scanErrors
                });
                log(
                    "🧬 ID VIVO: " +
                    Math.floor(scannedBytes / (1024 * 1024)) +
                    " MB verificados na descoberta inicial."
                );
                nextProgress += 256 * 1024 * 1024;
            }
            if (found && (found.stable_available === true ||
                found.stable_inactive === true)) {
                rememberStableRange(range);
                finish(range, found, "record_with_matching_id_and_position");
                return;
            }
            setTimeout(next, 0);
        });
    }

    next();
}

function startStableAvailabilityScan(entries, reason, callback) {
    if (stableAvailabilityScanRunning)
        return {ok:false, reason:"stable_scan_in_progress"};

    const unique = [];
    const ids = new Set();
    for (let i=0; i<(entries || []).length; i++) {
        const entry = entries[i];
        const registryId = normalizeRegistryId(entry.registry_id);
        if (registryId === "" || ids.has(registryId) ||
            !validCoord(Number(entry.x)) || !validCoord(Number(entry.y)) ||
            !validCoord(Number(entry.z)))
            continue;
        ids.add(registryId);
        unique.push(entry);
    }

    if (!unique.length) {
        callback([], {
            ok:true,
            reason:"no_registry_candidates",
            all_records:[],
            ambiguous_records:[],
            inactive_records:[]
        });
        return {ok:true, reason:"no_registry_candidates", candidate_count:0};
    }

    stableAvailabilityScanRunning = true;
    updateHudState();
    const serial = ++stableAvailabilityScanSerial;
    const startedAt = Date.now();

    sendLabEvent("stable_availability_scan_started", {
        serial:serial,
        reason:reason || "validation",
        candidates:labSanitizeValue(unique, 0),
        cached_range:stableMetadataRangeBase,
        cached_range_size:stableMetadataRangeSize
    });
    log(
        "🧬 ID VIVO: confirmando " + unique.length +
        " registro(s) sem usar proximidade."
    );

    function complete(records, scanErrors, cancelled, source) {
        stableAvailabilityScanRunning = false;
        updateHudState();
        const active = records.filter(function (record) {
            return record.stable_available === true;
        });
        const inactive = records.filter(function (record) {
            return record.stable_inactive === true;
        });
        const ambiguous = records.filter(function (record) {
            return record.stable_available !== true &&
                record.stable_inactive !== true;
        });
        const metadata = {
            ok:cancelled !== true,
            serial:serial,
            reason:reason || "validation",
            source:source || "",
            cancelled:cancelled === true,
            elapsed_ms:Date.now() - startedAt,
            candidate_count:unique.length,
            matched_record_count:records.length,
            active_count:active.length,
            inactive_count:inactive.length,
            ambiguous_count:ambiguous.length,
            missing_count:Math.max(0, unique.length - records.length),
            scan_errors:scanErrors || [],
            metadata_range_base:stableMetadataRangeBase,
            metadata_range_size:stableMetadataRangeSize,
            all_records:records,
            inactive_records:inactive,
            ambiguous_records:ambiguous
        };
        sendLabEvent("stable_availability_scan_finished", metadata);
        log(
            "🧬 ID VIVO: " + active.length + " disponível(is), " +
            inactive.length + " já coletado(s), " +
            ambiguous.length + " ambíguo(s), " +
            metadata.missing_count + " sem estrutura confirmada."
        );
        callback(active, metadata);
    }

    function scanKnownRange(range, source, allowDiscoveryFallback) {
        scanStableEntriesInRange(
            unique,
            range,
            serial,
            function (records, errors, cancelled) {
                const confirmed = records.some(function (record) {
                    return record.stable_available === true ||
                        record.stable_inactive === true;
                });
                if (!cancelled && !confirmed && allowDiscoveryFallback) {
                    forgetStableRange();
                    discoverAndScan();
                    return;
                }
                complete(records, errors, cancelled, source);
            }
        );
    }

    function discoverAndScan() {
        discoverStableMetadataRange(
            unique,
            serial,
            function (range, anchorRecord, discoveryReason) {
                if (!range) {
                    complete([], [{error:discoveryReason}],
                        discoveryReason === "cancelled", "discovery_failed");
                    return;
                }
                scanKnownRange(range, "discovered_range", false);
            }
        );
    }

    const cached = stableCachedRange();
    if (cached)
        scanKnownRange(cached, "cached_range", true);
    else
        discoverAndScan();

    return {
        ok:true,
        serial:serial,
        reason:"started",
        candidate_count:unique.length,
        cached_range:cached ? pstr(cached.base) : ""
    };
}

// -----------------------------------------------------------
// RECUPERAÇÃO DIRETA PELO DNA CONHECIDO
// -----------------------------------------------------------
// A lista tipo 39 não publica todos os corpos carregados. Quando o laboratório
// já viu a morte, o peso float exato faz parte do DNA salvo e permite procurar
// a estrutura completa diretamente no bloco unificado de 16 MB. A posição não
// participa da identidade; ela só é lida depois para movimentar o waypoint.

function liveSavedDnaTargets() {
    const unique = [];
    const keys = new Set();

    for (let i=0; i<forensicTargets.length; i++) {
        const target = forensicTargets[i];
        const reserve = forensicReserve(target.reserve);
        const dna = strongCorpseDna(target);
        const scoped = reserve + "|" + dna;

        if (target.collected === true || reserve !== activeReserve ||
            dna === "" || harvestedDnas.has(scoped) || keys.has(scoped))
            continue;

        keys.add(scoped);
        unique.push(target);
    }

    return unique;
}

function uniqueSavedTargetForWeight(targets, weight) {
    const matches = [];

    for (let i=0; i<targets.length; i++) {
        if (sameWeight(targets[i].weight, weight))
            matches.push(targets[i]);
    }

    return matches.length === 1 ? matches[0] : null;
}

function inspectDirectDnaRecord(matchAddress, target, range) {
    try {
        const record = matchAddress.sub(STABLE_RECORD_WEIGHT_OFFSET);
        const rangeEnd = range.base.add(range.size);

        if (record.compare(range.base) < 0 ||
            record.add(STABLE_RECORD_RAW_SIZE).compare(rangeEnd) > 0)
            return null;

        const registryId = normalizeRegistryId(record.readU64().toString());
        const x = record.add(STABLE_RECORD_COORD_X_OFFSET).readFloat();
        const y = record.add(STABLE_RECORD_COORD_Y_OFFSET).readFloat();
        const z = record.add(STABLE_RECORD_COORD_Z_OFFSET).readFloat();

        if (registryId === "" || !validCoord(x) || !validCoord(y) ||
            !validCoord(z))
            return null;

        const entry = {
            descriptor:"",
            registry_id:registryId,
            registry_id_hex:labHex(record, 8),
            x:x,
            y:y,
            z:z,
            object_link:"",
            object_link_valid:false,
            rejection_reason:"recuperado_diretamente_pelo_dna",
            seen_count:1,
            consecutive_count:1,
            last_seen_attempt:initialOldCorpseScanAttempts
        };
        const inspected = inspectStableAvailabilityRecord(record, entry, range);

        if (!inspected || inspected.stable_available !== true ||
            !sameWeight(inspected.stable_weight, target.weight))
            return null;

        inspected.recovered_directly_by_dna = true;
        inspected.dna_target = forensicTargetSnapshot(target);
        return inspected;
    } catch (_) {
        return null;
    }
}

function inspectAlternativeDnaRecord(matchAddress, target, range) {
    try {
        const record = matchAddress.sub(ALTERNATIVE_RECORD_WEIGHT_OFFSET);
        const rangeEnd = range.base.add(range.size);

        if (record.compare(range.base) < 0 ||
            record.add(ALTERNATIVE_RECORD_RAW_SIZE).compare(rangeEnd) > 0)
            return null;

        const weight = record.add(
            ALTERNATIVE_RECORD_WEIGHT_OFFSET
        ).readFloat();
        const x = record.add(ALTERNATIVE_RECORD_COORD_X_OFFSET).readFloat();
        const y = record.add(ALTERNATIVE_RECORD_COORD_Y_OFFSET).readFloat();
        const z = record.add(ALTERNATIVE_RECORD_COORD_Z_OFFSET).readFloat();
        const expectedX = finiteNumber(target.x);
        const expectedY = finiteNumber(target.y);
        const expectedZ = finiteNumber(target.z);

        if (!sameWeight(weight, target.weight) ||
            !validCoord(x) || !validCoord(y) || !validCoord(z) ||
            expectedX === null || expectedY === null || expectedZ === null)
            return null;

        const dx = Math.abs(x - expectedX);
        const dy = Math.abs(y - expectedY);
        const dz = Math.abs(z - expectedZ);
        if (dx > ALTERNATIVE_COORD_TOLERANCE_XZ ||
            dz > ALTERNATIVE_COORD_TOLERANCE_XZ ||
            dy > ALTERNATIVE_COORD_TOLERANCE_Y)
            return null;

        const availability = record.add(
            ALTERNATIVE_RECORD_AVAILABLE_OFFSET
        ).readU32();
        const lifecycle = record.add(
            ALTERNATIVE_RECORD_LIFECYCLE_OFFSET
        ).readU8();
        const pointers = [];
        let nonzeroPointers = 0;
        let readablePointers = 0;

        for (let i=0; i<ALTERNATIVE_RECORD_POINTER_OFFSETS.length; i++) {
            const offset = ALTERNATIVE_RECORD_POINTER_OFFSETS[i];
            let value = ptr(0);
            try { value = record.add(offset).readPointer(); }
            catch (_) {}
            const nonzero = !!value && !value.isNull();
            const readable = nonzero && stableReadablePointer(value);
            if (nonzero)
                nonzeroPointers++;
            if (readable)
                readablePointers++;
            pointers.push({
                offset:labOffsetName(offset),
                value:pstr(value),
                nonzero:nonzero,
                readable:readable
            });
        }

        let objectPointer = ptr(0);
        try { objectPointer = record.readPointer(); }
        catch (_) {}
        const objectPointerReadable = stableReadablePointer(objectPointer);
        const active = availability === 1 &&
            lifecycle === STABLE_RECORD_ACTIVE_STATE &&
            nonzeroPointers >= 2 && readablePointers >= 1 &&
            objectPointerReadable;

        if (!active)
            return null;

        return {
            descriptor:"",
            registry_id:"",
            registry_id_hex:"",
            x:x,
            y:y,
            z:z,
            object_link:pstr(objectPointer),
            object_link_valid:objectPointerReadable,
            rejection_reason:"estrutura_alternativa_confirmada_pelo_dna",
            seen_count:1,
            consecutive_count:1,
            last_seen_attempt:initialOldCorpseScanAttempts,
            stable_record_x:x,
            stable_record_y:y,
            stable_record_z:z,
            stable_coordinate_delta:{x:dx, y:dy, z:dz},
            stable_availability:availability,
            stable_lifecycle:lifecycle,
            stable_nonzero_pointers:nonzeroPointers,
            stable_readable_pointers:readablePointers,
            stable_pointers:pointers,
            stable_weight:weight,
            stable_classification:"available_body_alternative_layout",
            stable_available:true,
            stable_inactive:false,
            stable_raw_hex:labHex(record, ALTERNATIVE_RECORD_RAW_SIZE),
            recovered_directly_by_dna:true,
            recovered_from_alternative_layout:true,
            alternative_record_address:pstr(record),
            alternative_record_range_base:pstr(range.base),
            alternative_record_range_size:Number(range.size || 0),
            alternative_object_pointer:pstr(objectPointer),
            alternative_object_pointer_readable:objectPointerReadable,
            dna_target:forensicTargetSnapshot(target)
        };
    } catch (_) {
        return null;
    }
}

// Catálogo unificado confirmado nos laboratórios anteriores. Agora cada
// registro recebe uma classificação independente: vivo ou cadáver disponível.
function inspectDeathSignalCatalogRecord(record, range) {
    try {
        const rangeEnd = range.base.add(range.size);
        if (record.compare(range.base) < 0 ||
            record.add(ALTERNATIVE_RECORD_RAW_SIZE).compare(rangeEnd) > 0)
            return null;

        const availability = record.add(
            ALTERNATIVE_RECORD_AVAILABLE_OFFSET
        ).readU32();
        const lifecycle = record.add(
            ALTERNATIVE_RECORD_LIFECYCLE_OFFSET
        ).readU8();
        if (availability !== 1 ||
            lifecycle !== STABLE_RECORD_ACTIVE_STATE)
            return null;

        const x = record.add(ALTERNATIVE_RECORD_COORD_X_OFFSET).readFloat();
        const y = record.add(ALTERNATIVE_RECORD_COORD_Y_OFFSET).readFloat();
        const z = record.add(ALTERNATIVE_RECORD_COORD_Z_OFFSET).readFloat();
        const weight = record.add(
            ALTERNATIVE_RECORD_WEIGHT_OFFSET
        ).readFloat();
        if (!validCoord(x) || !validCoord(y) || !validCoord(z) ||
            Math.abs(x) + Math.abs(z) < 1 ||
            !Number.isFinite(weight) || weight <= 0.05 || weight >= 5000)
            return null;

        let objectPointer = ptr(0);
        try { objectPointer = record.readPointer(); }
        catch (_) {}
        if (!stableReadablePointer(objectPointer))
            return null;

        const pointers = [];
        let readablePointers = 0;
        for (let i=0; i<ALTERNATIVE_RECORD_POINTER_OFFSETS.length; i++) {
            const offset = ALTERNATIVE_RECORD_POINTER_OFFSETS[i];
            let value = ptr(0);
            try { value = record.add(offset).readPointer(); }
            catch (_) {}
            const readable = stableReadablePointer(value);
            if (readable)
                readablePointers++;
            pointers.push({
                offset:labOffsetName(offset),
                value:pstr(value),
                readable:readable
            });
        }
        if (readablePointers !== ALTERNATIVE_RECORD_POINTER_OFFSETS.length)
            return null;

        const signal97 = record.add(0x97).readU8();
        const signal98 = record.add(0x98).readU32();
        const signal9c = record.add(0x9C).readU32();
        const signalf8 = record.add(0xF8).readU32();
        const signal100 = record.add(0x100).readU8();
        const signal101 = record.add(0x101).readU8();
        const signal1b2 = record.add(0x1B2).readU8();
        const confirmedDead = signal97 === 0 && signalf8 === 1 &&
            signal98 > 0 && signal98 === signal9c &&
            signal100 === 0x03 && signal101 === 0x03 &&
            signal1b2 === 0;
        const confirmedAlive = signal97 === 1 && signal98 === 0 &&
            signal9c === 0 && signalf8 === 0 &&
            signal100 === 0xFF && signal101 === 0xFF &&
            signal1b2 === 1;

        return {
            x:x,
            y:y,
            z:z,
            stable_record_x:x,
            stable_record_y:y,
            stable_record_z:z,
            stable_availability:availability,
            stable_lifecycle:lifecycle,
            stable_nonzero_pointers:pointers.length,
            stable_readable_pointers:readablePointers,
            stable_pointers:pointers,
            stable_weight:weight,
            stable_classification:confirmedDead
                ? "confirmed_dead_available"
                : (confirmedAlive ? "confirmed_alive" : "unknown_state"),
            death_signal_confirmed:confirmedDead,
            alive_signal_confirmed:confirmedAlive,
            death_signal_fields:{
                offset_97:signal97,
                offset_98:signal98,
                offset_9c:signal9c,
                offset_f8:signalf8,
                offset_100:signal100,
                offset_101:signal101,
                offset_1b2:signal1b2
            },
            stable_available:true,
            stable_inactive:false,
            stable_raw_hex:labHex(record, ALTERNATIVE_RECORD_RAW_SIZE),
            recovered_directly_by_dna:false,
            recovered_from_alternative_layout:true,
            alternative_record_address:pstr(record),
            alternative_record_range_base:pstr(range.base),
            alternative_record_range_size:Number(range.size || 0),
            alternative_object_pointer:pstr(objectPointer),
            alternative_object_pointer_readable:true,
            captured_at:Date.now(),
            dna_target:null
        };
    } catch (_) {
        return null;
    }
}

function startDeathSignalBaselineScan(callback) {
    const cached = stableCachedRange();
    const ranges = cached
        ? [cached]
        : stableDiscoveryRanges().filter(function (range) {
            return range.size === STABLE_METADATA_EXACT_RANGE_SIZE;
        }).slice(0, 32);
    const pattern = "01 00 00 00 " +
        new Array(28).fill("??").join(" ") + " 07";
    const entries = [];
    const addresses = new Set();
    let rangeIndex = 0;
    let scanError = "";

    sendLabEvent("death_signal_baseline_started", {
        range_count:ranges.length,
        cached_range:cached ? pstr(cached.base) : "",
        purpose:"classificar vivos e cadáveres disponíveis sem histórico"
    });

    function finish(reason) {
        sendLabEvent("death_signal_baseline_finished", {
            reason:String(reason || scanError || "completed"),
            entry_count:entries.length,
            scanned_ranges:rangeIndex,
            entries:labSanitizeValue(entries, 0)
        });
        callback(entries, {
            reason:String(reason || scanError || "completed"),
            scanned_ranges:rangeIndex,
            cached_range:cached ? pstr(cached.base) : ""
        });
    }

    function scanNextRange() {
        if (scriptStopping) {
            finish("cancelled");
            return;
        }
        if (rangeIndex >= ranges.length || entries.length >= 128) {
            finish("completed");
            return;
        }

        const range = ranges[rangeIndex++];
        try {
            Memory.scan(range.base, range.size, pattern, {
                onMatch(availabilityAddress) {
                    const record = availabilityAddress.sub(
                        ALTERNATIVE_RECORD_AVAILABLE_OFFSET
                    );
                    const key = pstr(record);
                    if (addresses.has(key))
                        return;
                    addresses.add(key);
                    const candidate = inspectDeathSignalCatalogRecord(
                        record,
                        range
                    );
                    if (candidate)
                        entries.push(candidate);
                    if (entries.length >= 128)
                        return "stop";
                },
                onError(reason) {
                    scanError = String(reason || "memory_scan_error");
                },
                onComplete() {
                    setTimeout(scanNextRange, 0);
                }
            });
        } catch (error) {
            scanError = String(error);
            setTimeout(scanNextRange, 0);
        }
    }

    if (!ranges.length) {
        finish("range_unavailable");
        return;
    }
    scanNextRange();
}

function startExpandedDnaRangeRecovery(targets, primaryRange, callback) {
    const pendingTargets = Array.isArray(targets) ? targets.slice() : [];
    const primaryBase = primaryRange ? pstr(primaryRange.base) : "";
    const ranges = stableDiscoveryRanges().filter(function (range) {
        return pstr(range.base) !== primaryBase;
    });
    const plannedPerTarget = ranges.reduce(function (sum, range) {
        return sum + Number(range.size || 0);
    }, 0);
    const recovered = [];
    const unresolved = [];
    let targetIndex = 0;
    let scannedBytes = 0;
    let nextProgress = 256 * 1024 * 1024;
    let scanErrors = 0;

    if (!pendingTargets.length || !ranges.length) {
        callback(recovered, pendingTargets, {
            planned_bytes:plannedPerTarget * pendingTargets.length,
            scanned_bytes:0,
            range_count:ranges.length,
            scan_errors:0
        });
        return;
    }

    forensicScanRunning = true;
    updateHudState();
    sendLabEvent("expanded_dna_scan_started", {
        target_count:pendingTargets.length,
        range_count:ranges.length,
        planned_bytes:plannedPerTarget * pendingTargets.length,
        primary_range_excluded:primaryBase,
        targets:pendingTargets.map(forensicTargetSnapshot)
    });
    log(
        "🧬 DNA AMPLIADO: procurando " + pendingTargets.length +
        " DNA(s) nos outros blocos. A busca para assim que encontrar."
    );

    function finishAll() {
        forensicScanRunning = false;
        updateHudState();
        sendLabEvent("expanded_dna_scan_finished", {
            recovered_count:recovered.length,
            unresolved_count:unresolved.length,
            scanned_bytes:scannedBytes,
            range_count:ranges.length,
            scan_errors:scanErrors,
            recovered:labSanitizeValue(recovered, 0),
            unresolved_targets:unresolved.map(forensicTargetSnapshot)
        });
        callback(recovered, unresolved, {
            planned_bytes:plannedPerTarget * pendingTargets.length,
            scanned_bytes:scannedBytes,
            range_count:ranges.length,
            scan_errors:scanErrors
        });
    }

    function scanTarget(target) {
        const pattern = forensicFloatPattern(target.weight);
        const rawCandidates = [];
        const rawAddresses = new Set();
        let rangeIndex = 0;

        if (pattern === "") {
            unresolved.push(target);
            setTimeout(nextTarget, 0);
            return;
        }

        function finishTarget(found) {
            const targetId = String(target.target_id || "");
            if (rawCandidates.length)
                forensicCandidates[targetId] = rawCandidates.slice(0, 32);

            if (found) {
                found.recovered_directly_by_dna = true;
                found.recovered_from_expanded_range = true;
                found.dna_target = forensicTargetSnapshot(target);
                recovered.push(found);
                sendLabEvent("expanded_dna_record_recovered", {
                    target:forensicTargetSnapshot(target),
                    record:labSanitizeValue(found, 0),
                    raw_candidate_count:rawCandidates.length
                });
            } else {
                unresolved.push(target);
                sendLabEvent("expanded_dna_record_unresolved", {
                    target:forensicTargetSnapshot(target),
                    raw_candidate_count:rawCandidates.length,
                    saved_candidates:rawCandidates.slice(0, 32)
                });
            }
            emitRuntimeState("busca DNA ampliada");
            setTimeout(nextTarget, 0);
        }

        function nextRange() {
            if (scriptStopping) {
                finishTarget(null);
                return;
            }
            if (rangeIndex >= ranges.length) {
                finishTarget(null);
                return;
            }

            const range = ranges[rangeIndex++];
            let found = null;

            try {
                Memory.scan(range.base, range.size, pattern, {
                    onMatch(address, matchSize) {
                        let inspected = inspectDirectDnaRecord(
                            address,
                            target,
                            range
                        );
                        if (!inspected) {
                            inspected = inspectAlternativeDnaRecord(
                                address,
                                target,
                                range
                            );
                        }
                        if (inspected) {
                            found = inspected;
                            return "stop";
                        }

                        if (rawCandidates.length < 32) {
                            const key = pstr(address);
                            if (!rawAddresses.has(key)) {
                                rawAddresses.add(key);
                                const candidate = forensicWindowAt(
                                    address,
                                    target,
                                    matchSize
                                );
                                if (candidate)
                                    rawCandidates.push(candidate);
                            }
                        }
                    },
                    onError(_) {
                        scanErrors++;
                    },
                    onComplete() {
                        scannedBytes += range.size;
                        if (scannedBytes >= nextProgress) {
                            sendLabEvent("expanded_dna_scan_progress", {
                                scanned_bytes:scannedBytes,
                                target:forensicTargetSnapshot(target),
                                range_index:rangeIndex,
                                range_count:ranges.length,
                                raw_candidates:rawCandidates.length
                            });
                            log(
                                "🧬 DNA AMPLIADO: " +
                                Math.floor(scannedBytes / (1024 * 1024)) +
                                " MB verificados."
                            );
                            nextProgress += 256 * 1024 * 1024;
                        }
                        if (found)
                            finishTarget(found);
                        else
                            setTimeout(nextRange, 0);
                    }
                });
            } catch (_) {
                scanErrors++;
                scannedBytes += range.size;
                setTimeout(nextRange, 0);
            }
        }

        nextRange();
    }

    function nextTarget() {
        if (targetIndex >= pendingTargets.length) {
            finishAll();
            return;
        }
        const target = pendingTargets[targetIndex++];
        scanTarget(target);
    }

    nextTarget();
}

function startDirectDnaStableRecovery(activeEntries, callback) {
    const entries = Array.isArray(activeEntries) ? activeEntries : [];
    const targets = liveSavedDnaTargets();
    const matchedTargetIds = new Set();

    // Primeiro associa os IDs que a lista publicou aos DNAs conhecidos.
    for (let i=0; i<entries.length; i++) {
        const target = uniqueSavedTargetForWeight(
            targets,
            entries[i].stable_weight
        );
        if (!target)
            continue;
        entries[i].dna_target = forensicTargetSnapshot(target);
        matchedTargetIds.add(String(target.target_id || ""));
    }

    const missingTargets = targets.filter(function (target) {
        return !matchedTargetIds.has(String(target.target_id || ""));
    });
    const range = stableCachedRange();

    sendLabEvent("direct_dna_recovery_started", {
        saved_dna_count:targets.length,
        matched_from_registry:matchedTargetIds.size,
        missing_dna_count:missingTargets.length,
        metadata_range:range ? {
            base:pstr(range.base),
            size:range.size,
            protection:range.protection
        } : null,
        missing_targets:missingTargets.map(forensicTargetSnapshot)
    });

    if (!missingTargets.length) {
        if (targets.length > 0 && !missingTargets.length &&
            COLLECTION_DEBUG_MODE) {
            log(
                "🧬 ESTRUTURA ALTERNATIVA CONFIRMADA. " +
                "Aguarde a autorização automática para coletar."
            );
        } else if (targets.length > 0 && !missingTargets.length) {
            log(
                "✅ TESTE: TODOS OS " + targets.length +
                " ABATES FORAM CONFIRMADOS. PODE COLETAR."
            );
        } else if (targets.length > 0 && COLLECTION_DEBUG_MODE) {
            log(
                "⏳ DEPURAÇÃO: " + matchedTargetIds.size + "/" +
                targets.length +
                " confirmados. Aguarde a autorização para coletar somente o alvo próximo."
            );
        } else if (targets.length > 0) {
            log(
                "⏳ TESTE: " + matchedTargetIds.size + "/" +
                targets.length +
                " confirmados. NÃO COLETE AINDA; envie o pacote do LAB."
            );
        }
        sendLabEvent("direct_dna_recovery_finished", {
            saved_dna_count:targets.length,
            matched_from_registry:matchedTargetIds.size,
            recovered_directly:0,
            unresolved:missingTargets.length,
            reason:"all_matched"
        });
        callback(entries, {
            saved_dna_count:targets.length,
            matched_from_registry:matchedTargetIds.size,
            recovered_directly:0,
            unresolved:missingTargets.length
        });
        return;
    }

    if (!range) {
        log(
            "🧬 DNA DIRETO: bloco principal ainda desconhecido; " +
            "procurando o registro nos blocos de dados."
        );
        startExpandedDnaRangeRecovery(
            missingTargets,
            null,
            function (expandedRecovered, expandedUnresolved, metadata) {
                sendLabEvent("direct_dna_recovery_finished", {
                    saved_dna_count:targets.length,
                    matched_from_registry:matchedTargetIds.size,
                    recovered_directly:expandedRecovered.length,
                    unresolved:expandedUnresolved.length,
                    reason:"expanded_without_cached_range",
                    expanded_scan:metadata
                });
                callback(entries.concat(expandedRecovered), {
                    saved_dna_count:targets.length,
                    matched_from_registry:matchedTargetIds.size,
                    recovered_directly:expandedRecovered.length,
                    unresolved:expandedUnresolved.length,
                    expanded_scan:metadata
                });
            }
        );
        return;
    }

    log(
        "🧬 DNA DIRETO: " + missingTargets.length +
        " animal(is) não apareceram na lista; procurando no bloco de 16 MB."
    );

    const recovered = [];
    const unresolved = [];
    let index = 0;
    let expandedStarted = false;
    let expandedMetadata = null;

    function finalizeRecovery() {
        const confirmedCount = matchedTargetIds.size + recovered.length;
        sendLabEvent("direct_dna_recovery_finished", {
            saved_dna_count:targets.length,
            matched_from_registry:matchedTargetIds.size,
            recovered_directly:recovered.length,
            unresolved:unresolved.length,
            recovered:labSanitizeValue(recovered, 0),
            unresolved_targets:unresolved.map(forensicTargetSnapshot),
            expanded_scan:expandedMetadata
        });
        log(
            "🧬 DNA DIRETO: " +
            confirmedCount + "/" +
            targets.length + " DNA(s) vivos confirmados."
        );
        if (targets.length > 0 && confirmedCount === targets.length &&
            COLLECTION_DEBUG_MODE) {
            log(
                "🧬 ESTRUTURA ALTERNATIVA CONFIRMADA. " +
                "Aguarde a autorização automática para coletar."
            );
        } else if (targets.length > 0 &&
            confirmedCount === targets.length) {
            log(
                "✅ TESTE: TODOS OS " + targets.length +
                " ABATES FORAM CONFIRMADOS. PODE COLETAR."
            );
        } else if (targets.length > 0 && COLLECTION_DEBUG_MODE) {
            log(
                "⏳ DEPURAÇÃO: " + confirmedCount + "/" + targets.length +
                " confirmados. Preparando a coleta controlada do alvo próximo."
            );
        } else if (targets.length > 0) {
            log(
                "⏳ TESTE: " + confirmedCount + "/" + targets.length +
                " confirmados. NÃO COLETE AINDA; envie o pacote do LAB."
            );
        }
        callback(entries.concat(recovered), {
            saved_dna_count:targets.length,
            matched_from_registry:matchedTargetIds.size,
            recovered_directly:recovered.length,
            unresolved:unresolved.length,
            expanded_scan:expandedMetadata
        });
    }

    function finish() {
        if (!COLLECTION_DEBUG_MODE && !scriptStopping &&
            unresolved.length > 0 && !expandedStarted) {
            expandedStarted = true;
            const targetsForExpansion = unresolved.slice();
            unresolved.splice(0, unresolved.length);
            startExpandedDnaRangeRecovery(
                targetsForExpansion,
                range,
                function (expandedRecovered, expandedUnresolved, metadata) {
                    for (let i=0; i<expandedRecovered.length; i++)
                        recovered.push(expandedRecovered[i]);
                    for (let i=0; i<expandedUnresolved.length; i++)
                        unresolved.push(expandedUnresolved[i]);
                    expandedMetadata = metadata;
                    finalizeRecovery();
                }
            );
            return;
        }
        finalizeRecovery();
    }

    function nextTarget() {
        if (scriptStopping) {
            finish();
            return;
        }
        if (index >= missingTargets.length) {
            finish();
            return;
        }

        const target = missingTargets[index++];
        const pattern = forensicFloatPattern(target.weight);
        const matches = [];
        const ids = new Set();
        const rawCandidates = [];
        const rawAddresses = new Set();
        let scanError = "";

        if (pattern === "") {
            unresolved.push(target);
            setTimeout(nextTarget, 0);
            return;
        }

        try {
            Memory.scan(range.base, range.size, pattern, {
                onMatch(address, matchSize) {
                    let inspected = inspectDirectDnaRecord(
                        address,
                        target,
                        range
                    );
                    if (!inspected) {
                        inspected = inspectAlternativeDnaRecord(
                            address,
                            target,
                            range
                        );
                    }
                    if (!inspected) {
                        if (rawCandidates.length < 32) {
                            const key = pstr(address);
                            if (!rawAddresses.has(key)) {
                                rawAddresses.add(key);
                                const candidate = forensicWindowAt(
                                    address,
                                    target,
                                    matchSize
                                );
                                if (candidate)
                                    rawCandidates.push(candidate);
                            }
                        }
                        return;
                    }
                    const registryId = normalizeRegistryId(
                        inspected.registry_id
                    );
                    const id = inspected.recovered_from_alternative_layout ===
                        true
                            ? "alternative|" + String(
                                inspected.alternative_record_address || ""
                            )
                            : "registry|" + registryId;
                    if (id === "registry|" || id === "alternative|" ||
                        ids.has(id))
                        return;
                    ids.add(id);
                    matches.push(inspected);
                },
                onError(reason) {
                    scanError = String(reason || "memory_scan_error");
                },
                onComplete() {
                    if (matches.length === 1) {
                        recovered.push(matches[0]);
                        sendLabEvent("direct_dna_record_recovered", {
                            target:forensicTargetSnapshot(target),
                            record:labSanitizeValue(matches[0], 0)
                        });
                    } else {
                        unresolved.push(target);
                        if (rawCandidates.length) {
                            forensicCandidates[String(
                                target.target_id || ""
                            )] = rawCandidates.slice(0, 32);
                        }
                        sendLabEvent("direct_dna_record_unresolved", {
                            target:forensicTargetSnapshot(target),
                            valid_match_count:matches.length,
                            raw_candidate_count:rawCandidates.length,
                            saved_candidates:rawCandidates.slice(0, 32),
                            scan_error:scanError
                        });
                    }
                    setTimeout(nextTarget, 0);
                }
            });
        } catch (error) {
            unresolved.push(target);
            sendLabEvent("direct_dna_record_unresolved", {
                target:forensicTargetSnapshot(target),
                valid_match_count:0,
                scan_error:String(error)
            });
            setTimeout(nextTarget, 0);
        }
    }

    nextTarget();
}

// -----------------------------------------------------------
// LABORATÓRIO FORENSE DE DNA NA MEMÓRIA
// -----------------------------------------------------------

function forensicReserve(value) {
    return normalizeReserve(value) || activeReserve;
}

function forensicPartialCompatible(left, right) {
    if (!left || !right)
        return false;

    const leftReserve = forensicReserve(left.reserve);
    const rightReserve = forensicReserve(right.reserve);

    if (leftReserve !== "" && rightReserve !== "" &&
        leftReserve !== rightReserve)
        return false;

    if (!sameSpecies(left.species, right.species) ||
        !sameWeight(left.weight, right.weight))
        return false;

    if (hasValue(left.gender) && hasValue(right.gender) &&
        !sameValue(left.gender, right.gender))
        return false;

    if (hasValue(left.difficulty) && hasValue(right.difficulty) &&
        !sameValue(left.difficulty, right.difficulty))
        return false;

    return true;
}

function forensicTargetId(c, source) {
    const reserve = forensicReserve(c ? c.reserve : "");
    const animalId = String(
        c && (c.recovered_animal_id ?? c.animal_id) || ""
    ).trim();

    if (animalId !== "" && animalId !== "0")
        return "animal|" + reserve + "|" + animalId;

    const dna = strongCorpseDna(c);
    if (dna !== "")
        return "dna|" + reserve + "|" + dna;

    const weight = finiteNumber(c ? c.weight : null);
    return [
        "parcial",
        reserve,
        c && c.species !== undefined ? c.species : "",
        weight !== null ? weight.toFixed(6) : "",
        c && c.gender !== undefined ? c.gender : "",
        Number(c && c.x || 0).toFixed(2),
        Number(c && c.z || 0).toFixed(2),
        source || "desconhecido",
        Date.now()
    ].join("|");
}

function forensicTargetSnapshot(target) {
    return {
        target_id:String(target.target_id || ""),
        reserve:target.reserve ?? "",
        species:target.species ?? null,
        weight:target.weight ?? null,
        gender:target.gender ?? null,
        difficulty:target.difficulty ?? null,
        x:target.x ?? null,
        y:target.y ?? null,
        z:target.z ?? null,
        animal_id:String(target.animal_id || ""),
        source:String(target.source || ""),
        collected:target.collected === true,
        collected_at:Number(target.collected_at || 0),
        created_at:Number(target.created_at || 0),
        updated_at:Number(target.updated_at || 0),
        full_dna:strongCorpseDna(target)
    };
}

function emitRuntimeState(reason) {
    const savedTargets = forensicTargets.slice(0, 128).map(
        forensicTargetSnapshot
    );
    const savedCandidates = {};
    const candidateKeys = Object.keys(forensicCandidates).slice(0, 128);

    for (let i=0; i<candidateKeys.length; i++) {
        const key = candidateKeys[i];
        const entries = Array.isArray(forensicCandidates[key])
            ? forensicCandidates[key]
            : [];
        savedCandidates[key] = entries.slice(
            0,
            FORENSIC_MAX_SAVED_CANDIDATES
        );
    }

    sendLabEvent("runtime_state_snapshot", {
        game_identity:GAME_IDENTITY,
        reason:reason || "atualização",
        harvested_dna_keys:Array.from(harvestedDnas),
        pending_corpses:labPendingSnapshot(),
        navigation_anchor:lastNavigationAnchor,
        forensic_targets:savedTargets,
        forensic_candidates:savedCandidates,
        stable_metadata_range_base:stableMetadataRangeBase,
        stable_metadata_range_size:stableMetadataRangeSize,
        stable_metadata_range_protection:stableMetadataRangeProtection,
        stable_metadata_range_file:stableMetadataRangeFile
    });
}

function registerForensicTarget(c, source) {
    if (!c || finiteNumber(c.weight) === null ||
        !hasValue(c.species))
        return null;

    const animalId = String(
        c.recovered_animal_id ?? c.animal_id ?? ""
    ).trim();
    let existing = null;

    for (let i=0; i<forensicTargets.length; i++) {
        const candidate = forensicTargets[i];

        if (animalId !== "" && animalId !== "0" &&
            String(candidate.animal_id || "") === animalId &&
            forensicReserve(candidate.reserve) ===
                forensicReserve(c.reserve)) {
            existing = candidate;
            break;
        }

        if (forensicPartialCompatible(candidate, c)) {
            existing = candidate;
            break;
        }
    }

    const now = Date.now();

    if (!existing) {
        existing = {
            target_id:forensicTargetId(c, source),
            reserve:c.reserve ?? activeReserve,
            species:c.species,
            weight:finiteNumber(c.weight),
            gender:c.gender ?? null,
            difficulty:c.difficulty ?? null,
            x:finiteNumber(c.x),
            y:finiteNumber(c.y),
            z:finiteNumber(c.z),
            animal_id:animalId,
            source:source || "evento",
            collected:false,
            collected_at:0,
            created_at:now,
            updated_at:now
        };
        forensicTargets.push(existing);
    } else {
        existing.reserve = c.reserve ?? existing.reserve;
        existing.species = hasValue(c.species)
            ? c.species : existing.species;
        existing.weight = finiteNumber(c.weight) !== null
            ? finiteNumber(c.weight) : existing.weight;
        existing.gender = hasValue(c.gender)
            ? c.gender : existing.gender;
        existing.difficulty = hasValue(c.difficulty)
            ? c.difficulty : existing.difficulty;
        existing.x = finiteNumber(c.x) !== null
            ? finiteNumber(c.x) : existing.x;
        existing.y = finiteNumber(c.y) !== null
            ? finiteNumber(c.y) : existing.y;
        existing.z = finiteNumber(c.z) !== null
            ? finiteNumber(c.z) : existing.z;
        existing.animal_id = animalId !== ""
            ? animalId : existing.animal_id;
        existing.source = existing.source || source || "evento";
        existing.updated_at = now;
    }

    sendLabEvent("forensic_target_registered", {
        source:source || "evento",
        target:forensicTargetSnapshot(existing),
        target_count:forensicTargets.length
    });
    emitRuntimeState("alvo forense registrado");
    return existing;
}

function findForensicTarget(c) {
    if (!c)
        return null;

    const animalId = String(
        c.recovered_animal_id ?? c.animal_id ?? ""
    ).trim();

    for (let i=0; i<forensicTargets.length; i++) {
        const target = forensicTargets[i];

        if (animalId !== "" && animalId !== "0" &&
            String(target.animal_id || "") === animalId &&
            forensicReserve(target.reserve) ===
                forensicReserve(c.reserve))
            return target;

        if (forensicPartialCompatible(target, c))
            return target;
    }

    return null;
}

function chooseForensicTarget() {
    const pp = getPlayerPos();
    let selectedCorpse = null;
    let selectedDistance = Number.POSITIVE_INFINITY;

    for (let i=0; i<pending.length; i++) {
        const corpse = pending[i];

        if (finiteNumber(corpse.weight) === null ||
            !hasValue(corpse.species))
            continue;

        const distance = pp &&
            finiteNumber(corpse.x) !== null &&
            finiteNumber(corpse.z) !== null
                ? distanceXZ(pp, corpse)
                : Number.POSITIVE_INFINITY;

        if (!selectedCorpse || distance < selectedDistance) {
            selectedCorpse = corpse;
            selectedDistance = distance;
        }
    }

    if (selectedCorpse)
        return registerForensicTarget(selectedCorpse, "fila_pendente");

    let selected = null;
    selectedDistance = Number.POSITIVE_INFINITY;

    for (let i=0; i<forensicTargets.length; i++) {
        const target = forensicTargets[i];

        if (target.collected === true ||
            finiteNumber(target.weight) === null)
            continue;

        const distance = pp &&
            finiteNumber(target.x) !== null &&
            finiteNumber(target.z) !== null
                ? distanceXZ(pp, target)
                : Number.POSITIVE_INFINITY;

        if (!selected || distance < selectedDistance) {
            selected = target;
            selectedDistance = distance;
        }
    }

    return selected;
}

// -----------------------------------------------------------
// DEPURAÇÃO CONTROLADA DE UMA COLETA
// -----------------------------------------------------------
// Este modo usa apenas o cadáver confirmado mais próximo. Ele conserva uma
// fotografia do registro vivo, acompanha o mesmo endereço após a coleta e
// registra também as janelas do DNA ainda não resolvido. Nenhuma dessas
// leituras muda a identidade ou a posição de outro cadáver.

function collectionDebugRecordSnapshot(corpse, phase) {
    const alternative = !!corpse &&
        corpse.recovered_from_alternative_layout === true;
    const addressText = String(
        alternative
            ? (corpse.recovered_alternative_record_address || "")
            : (corpse && corpse.recovered_stable_record_address || "")
    );
    const result = {
        phase:String(phase || "snapshot"),
        captured_at:Date.now(),
        address:addressText,
        expected_registry_id:normalizeRegistryId(
            corpse && corpse.recovered_registry_id
        ),
        layout:alternative ? "alternative" : "stable",
        readable:false,
        player:getPlayerPos(),
        pending_count:pending.length
    };

    if (addressText === "")
        return result;

    try {
        const address = ptr(addressText);
        const range = Process.findRangeByAddress(address);
        if (!range || String(range.protection || "").indexOf("r") < 0) {
            result.error = "stable_record_not_readable";
            return result;
        }

        result.readable = true;
        result.range_base = pstr(range.base);
        result.range_size = Number(range.size || 0);
        if (alternative) {
            let objectPointer = ptr(0);
            try { objectPointer = address.readPointer(); }
            catch (_) {}
            result.object_pointer = pstr(objectPointer);
            result.object_pointer_readable = stableReadablePointer(
                objectPointer
            );
            result.registry_id_now = "";
            result.x = address.add(
                ALTERNATIVE_RECORD_COORD_X_OFFSET
            ).readFloat();
            result.y = address.add(
                ALTERNATIVE_RECORD_COORD_Y_OFFSET
            ).readFloat();
            result.z = address.add(
                ALTERNATIVE_RECORD_COORD_Z_OFFSET
            ).readFloat();
            result.lifecycle = address.add(
                ALTERNATIVE_RECORD_LIFECYCLE_OFFSET
            ).readU8();
            result.availability = address.add(
                ALTERNATIVE_RECORD_AVAILABLE_OFFSET
            ).readU32();
            result.weight = address.add(
                ALTERNATIVE_RECORD_WEIGHT_OFFSET
            ).readFloat();
            result.raw_hex = labHex(
                address,
                ALTERNATIVE_RECORD_RAW_SIZE
            );
        } else {
            result.registry_id_now = normalizeRegistryId(
                address.readU64().toString()
            );
            result.x = address.add(STABLE_RECORD_COORD_X_OFFSET).readFloat();
            result.y = address.add(STABLE_RECORD_COORD_Y_OFFSET).readFloat();
            result.z = address.add(STABLE_RECORD_COORD_Z_OFFSET).readFloat();
            result.lifecycle = address.add(
                STABLE_RECORD_LIFECYCLE_OFFSET
            ).readU8();
            result.availability = address.add(
                STABLE_RECORD_AVAILABLE_OFFSET
            ).readU32();
            result.weight = address.add(
                STABLE_RECORD_WEIGHT_OFFSET
            ).readFloat();
            result.raw_hex = labHex(address, STABLE_RECORD_RAW_SIZE);
        }
        const pointerOffsets = alternative
            ? ALTERNATIVE_RECORD_POINTER_OFFSETS
            : STABLE_RECORD_POINTER_OFFSETS;
        result.pointers = pointerOffsets.map(
            function (offset) {
                let value = ptr(0);
                try { value = address.add(offset).readPointer(); }
                catch (_) {}
                return {
                    offset:labOffsetName(offset),
                    value:pstr(value),
                    readable:stableReadablePointer(value)
                };
            }
        );
    } catch (error) {
        result.error = String(error);
    }

    return result;
}

function collectionDebugCandidateSnapshots(phase) {
    const groups = [];

    for (let i=0; i<forensicTargets.length; i++) {
        const target = forensicTargets[i];
        const candidates = Array.isArray(
            forensicCandidates[String(target.target_id || "")]
        ) ? forensicCandidates[String(target.target_id || "")] : [];

        if (!candidates.length || target.collected === true)
            continue;

        const snapshot = snapshotKnownForensicCandidates(target, phase);
        groups.push({
            target:forensicTargetSnapshot(target),
            candidate_count:candidates.length,
            snapshot_count:Number(snapshot.snapshots || 0)
        });
    }

    return groups;
}

function collectionDebugCapture(phase, harvest) {
    if (!collectionDebugArmed)
        return null;

    const registry = scanOldCorpseRegistryOnce();
    const corpse = collectionDebugArmed.corpse;
    const record = collectionDebugRecordSnapshot(corpse, phase);
    const candidateGroups = collectionDebugCandidateSnapshots(phase);
    const data = {
        phase:String(phase || "snapshot"),
        armed:labSanitizeValue(collectionDebugArmed, 0),
        harvest:labSanitizeValue(harvest || null, 0),
        stable_record:record,
        registry_available:registry !== null,
        registry_entries:registry === null
            ? [] : labSanitizeValue(
                labCloneStableEntries(registry.candidates),
                0
            ),
        candidate_groups:candidateGroups,
        pending:labPendingSnapshot(),
        player:getPlayerPos()
    };
    sendLabEvent("collection_debug_snapshot", data);
    return data;
}

function armCollectionDebugTarget(reason) {
    if (!COLLECTION_DEBUG_MODE || scriptStopping ||
        collectionDebugCompleted || collectionDebugHarvestSeen)
        return {ok:false, reason:"debug_unavailable"};

    if (collectionDebugArmed)
        return {ok:true, reason:"already_armed"};

    const player = getPlayerPos();
    if (!player || !pending.length)
        return {ok:false, reason:"player_or_pending_unavailable"};

    let corpse = null;
    let distance = Number.POSITIVE_INFINITY;

    for (let i=0; i<pending.length; i++) {
        const candidate = pending[i];
        const candidateDna = strongCorpseDna(candidate);
        if (candidateDna !== COLLECTION_DEBUG_TARGET_DNA)
            continue;

        const alternative =
            candidate.recovered_from_alternative_layout === true;
        const recordAddress = String(
            alternative
                ? (candidate.recovered_alternative_record_address || "")
                : (candidate.recovered_stable_record_address || "")
        );
        const expectedActiveState = alternative
            ? 7 : STABLE_RECORD_ACTIVE_STATE;
        if (recordAddress === "" ||
            Number(candidate.recovered_availability) !== 1 ||
            Number(candidate.recovered_lifecycle) !==
                expectedActiveState)
            continue;

        const currentDistance = distanceXZ(player, candidate);
        if (!corpse || currentDistance < distance) {
            corpse = candidate;
            distance = currentDistance;
        }
    }

    if (!corpse)
        return {ok:false, reason:"no_confirmed_corpse"};

    if (!Number.isFinite(distance) ||
        distance > COLLECTION_DEBUG_MAX_DISTANCE) {
        return {
            ok:false,
            reason:"confirmed_corpse_too_far",
            distance:distance
        };
    }

    const target = findForensicTarget(corpse) ||
        registerForensicTarget(corpse, "depuracao_coleta");
    collectionDebugArmed = {
        armed_at:Date.now(),
        reason:String(reason || "alvo próximo confirmado"),
        dna:strongCorpseDna(corpse),
        target_id:target ? String(target.target_id || "") : "",
        registry_id:normalizeRegistryId(corpse.recovered_registry_id),
        stable_record_address:String(
            corpse.recovered_stable_record_address || ""
        ),
        layout:corpse.recovered_from_alternative_layout === true
            ? "alternative" : "stable",
        alternative_record_address:String(
            corpse.recovered_alternative_record_address || ""
        ),
        distance:distance,
        corpse:labSanitizeValue(corpse, 0),
        target:target ? forensicTargetSnapshot(target) : null
    };
    collectionDebugCapture("armado_antes_da_coleta", null);
    sendLabEvent("collection_debug_armed", {
        armed:labSanitizeValue(collectionDebugArmed, 0),
        pending:labPendingSnapshot()
    });
    log(
        "✅ DEPURAÇÃO PRONTA: DNA " + collectionDebugArmed.dna +
        " confirmado a " + distance.toFixed(1) +
        " m. PODE COLETAR AGORA somente este animal."
    );
    sendGpsStatus(
        "collection_debug_armed",
        "DNA confirmado; pode coletar o alvo próximo"
    );
    return {ok:true, reason:"armed", distance:distance};
}

function scheduleCollectionDebugArm(delay, reason) {
    if (!COLLECTION_DEBUG_MODE || scriptStopping || collectionDebugArmed ||
        collectionDebugHarvestSeen || collectionDebugCompleted)
        return;

    collectionDebugArmTimer = clearTimeoutSafe(collectionDebugArmTimer);
    collectionDebugArmTimer = setTimeout(function retryArm() {
        collectionDebugArmTimer = null;
        const result = armCollectionDebugTarget(reason);
        if (!result.ok && !scriptStopping && !collectionDebugArmed &&
            !collectionDebugHarvestSeen) {
            collectionDebugArmTimer = setTimeout(retryArm, 750);
        }
    }, Math.max(0, Number(delay || 0)));
}

function collectionDebugHarvestObserved(path, harvest) {
    if (!COLLECTION_DEBUG_MODE || !collectionDebugArmed ||
        collectionDebugHarvestSeen || collectionDebugCompleted)
        return;

    const observedDna = strongCorpseDna(harvest);
    const expectedDna = String(collectionDebugArmed.dna || "");
    const exact = observedDna !== "" && observedDna === expectedDna;

    sendLabEvent("collection_debug_harvest_observed", {
        path:String(path || ""),
        expected_dna:expectedDna,
        observed_dna:observedDna,
        exact_dna_match:exact,
        harvest:labSanitizeValue(harvest, 0),
        armed:labSanitizeValue(collectionDebugArmed, 0)
    });

    if (!exact) {
        log(
            "⚠️ DEPURAÇÃO: a coleta recebida não pertence ao DNA armado. " +
            "O alvo de depuração continua aguardando."
        );
        return;
    }

    collectionDebugHarvestSeen = true;
    collectionDebugCapture("evento_de_coleta", harvest);
    log(
        "🧬 DEPURAÇÃO: coleta exata detectada. Aguarde 15 segundos sem " +
        "fechar o jogo nem o laboratório."
    );

    for (let i=0; i<COLLECTION_DEBUG_AFTER_DELAYS.length; i++) {
        const delay = COLLECTION_DEBUG_AFTER_DELAYS[i];
        const timer = setTimeout(function () {
            collectionDebugCapture(
                "depois_da_coleta+" + delay + "ms",
                harvest
            );
            if (delay === 15000) {
                collectionDebugCompleted = true;
                sendLabEvent("collection_debug_completed", {
                    expected_dna:expectedDna,
                    pending:labPendingSnapshot(),
                    player:getPlayerPos()
                });
                log(
                    "✅ DEPURAÇÃO CONCLUÍDA. Agora clique em PARAR e envie " +
                    "o ZIP PACOTE_LAB_ABATES_SEM_HISTORICO."
                );
            }
        }, delay);
        labBurstTimers.push(timer);
    }

    const rescanTimer = setTimeout(function () {
        if (scriptStopping)
            return;
        const result = forceOldCorpseScan();
        sendLabEvent("collection_debug_automatic_rescan", {
            result:labSanitizeValue(result, 0),
            expected_dna:expectedDna
        });
        if (result.ok) {
            log(
                "🧬 DEPURAÇÃO: confirmando automaticamente a retirada " +
                "da estrutura alternativa após a coleta."
            );
        }
    }, 2600);
    labBurstTimers.push(rescanTimer);
}

function forensicFloatPattern(value) {
    const memory = Memory.alloc(4);
    memory.writeFloat(Number(value));
    const hex = labHex(memory, 4);

    if (hex.length !== 8)
        return "";

    return hex.match(/.{2}/g).join(" ");
}

function runForensicMemoryApiSelfTest() {
    try {
        const memory = Memory.alloc(8);
        memory.writeU32(0x12345678);
        memory.add(4).writeFloat(215.10275268554688);
        const raw = labHex(memory, 8);
        const pattern = forensicFloatPattern(215.10275268554688);
        const ok = raw.length === 16 &&
            raw.indexOf("78563412") === 0 &&
            pattern !== "";

        return {
            ok:ok,
            raw_hex:raw,
            float_pattern:pattern,
            expected_prefix:"78563412",
            error:ok ? "" : "leitura bruta ou padrão float inválido"
        };
    } catch (error) {
        return {
            ok:false,
            raw_hex:"",
            float_pattern:"",
            expected_prefix:"78563412",
            error:String(error)
        };
    }
}

function forensicWindowAt(address, target, hitSize) {
    const range = Process.findRangeByAddress(address);

    if (!range || String(range.protection || "").indexOf("r") < 0)
        return null;

    const rangeEnd = range.base.add(range.size);
    let start = address.sub(FORENSIC_WINDOW_BEFORE);

    if (start.compare(range.base) < 0)
        start = range.base;

    let end = start.add(FORENSIC_WINDOW_SIZE);
    if (end.compare(rangeEnd) > 0)
        end = rangeEnd;

    if (end.compare(start) <= 0)
        return null;

    const length = end.sub(start).toUInt32();
    const matches = [];
    const scored = new Set();
    let score = 100;

    function record(field, offset, value, points) {
        if (matches.length < 48) {
            matches.push({
                field:field,
                offset:labOffsetName(offset),
                value:value
            });
        }

        if (!scored.has(field)) {
            scored.add(field);
            score += points;
        }
    }

    const expected = {
        species:finiteNumber(target.species),
        gender:finiteNumber(target.gender),
        difficulty:finiteNumber(target.difficulty),
        animal_id:finiteNumber(target.animal_id),
        x:finiteNumber(target.x),
        y:finiteNumber(target.y),
        z:finiteNumber(target.z)
    };

    for (let offset=0; offset+4<=length; offset+=4) {
        const word = start.add(offset);

        try {
            const i32 = word.readS32();
            const f32 = word.readFloat();

            if (expected.species !== null && i32 === expected.species)
                record("species_i32", offset, i32, 25);
            if (expected.gender !== null && i32 === expected.gender)
                record("gender_i32", offset, i32, 5);
            if (expected.difficulty !== null && i32 === expected.difficulty)
                record("difficulty_i32", offset, i32, 10);
            if (expected.animal_id !== null && i32 === expected.animal_id)
                record("animal_id_i32", offset, i32, 80);

            if (Number.isFinite(f32)) {
                if (expected.x !== null && Math.abs(f32 - expected.x) <= 0.05)
                    record("x_f32", offset, f32, 45);
                if (expected.y !== null && Math.abs(f32 - expected.y) <= 0.05)
                    record("y_f32", offset, f32, 45);
                if (expected.z !== null && Math.abs(f32 - expected.z) <= 0.05)
                    record("z_f32", offset, f32, 45);
            }
        } catch (_) {}
    }

    let moduleName = "";
    let filePath = "";

    try {
        const module = Process.findModuleByAddress(address);
        moduleName = module ? String(module.name || "") : "";
    } catch (_) {}

    try {
        filePath = range.file ? String(range.file.path || "") : "";
    } catch (_) {}

    let decodeBase = address;
    try {
        const proposed = address.sub(0x80);
        decodeBase = proposed.compare(range.base) >= 0
            ? proposed
            : range.base;
    } catch (_) {}

    return {
        address:pstr(address),
        hit_size:Number(hitSize || 4),
        window_base:pstr(start),
        window_size:length,
        hit_offset:address.sub(start).toUInt32(),
        range_base:pstr(range.base),
        range_size:Number(range.size),
        range_protection:String(range.protection || ""),
        range_file:filePath,
        module:moduleName,
        score:score,
        matched_fields:matches,
        raw_hex:labHex(start, length),
        decoded_near_hit:labDecodedFields(decodeBase),
        pointer_previews:labPointerPreviews(decodeBase),
        captured_at:Date.now()
    };
}

function snapshotKnownForensicCandidates(target, phase) {
    if (!target || !target.target_id)
        return {ok:false, reason:"target_missing", snapshots:0};

    const candidates = Array.isArray(forensicCandidates[target.target_id])
        ? forensicCandidates[target.target_id]
        : [];
    const snapshots = [];

    for (let i=0;
        i<candidates.length && i<FORENSIC_MAX_SAVED_CANDIDATES;
        i++) {
        const candidate = candidates[i];
        let address = ptr(0);

        try { address = ptr(String(candidate.window_base || "")); }
        catch (_) { continue; }

        const range = Process.findRangeByAddress(address);
        const readable = !!range &&
            String(range.protection || "").indexOf("r") >= 0;
        const size = Math.max(
            0,
            Math.min(
                Number(candidate.window_size || 0),
                FORENSIC_WINDOW_SIZE
            )
        );
        const raw = readable && size > 0 ? labHex(address, size) : "";

        snapshots.push({
            address:String(candidate.address || ""),
            window_base:String(candidate.window_base || ""),
            window_size:size,
            readable:readable,
            changed:raw !== String(candidate.raw_hex || ""),
            before_raw_hex:String(candidate.raw_hex || ""),
            current_raw_hex:raw,
            original_score:Number(candidate.score || 0)
        });
    }

    sendLabEvent("forensic_candidate_recheck", {
        phase:phase || "verificação",
        target:forensicTargetSnapshot(target),
        snapshots:snapshots
    });

    return {ok:true, snapshots:snapshots.length};
}

function scheduleForensicAfterHarvest(target) {
    if (!target)
        return;

    const delays = [0, 100, 500, 1500, 5000];

    for (let i=0; i<delays.length; i++) {
        const delay = delays[i];
        const timer = setTimeout(function () {
            snapshotKnownForensicCandidates(
                target,
                "depois_coleta+" + delay + "ms"
            );
        }, delay);
        labBurstTimers.push(timer);
    }
}

function finishForensicTargetHarvest(target, harvest) {
    if (!target)
        return null;

    target.reserve = harvest.reserve ?? target.reserve;
    target.species = harvest.species ?? target.species;
    target.weight = finiteNumber(harvest.weight) !== null
        ? finiteNumber(harvest.weight) : target.weight;
    target.gender = harvest.gender ?? target.gender;
    target.difficulty = harvest.difficulty ?? target.difficulty;
    target.collected = true;
    target.collected_at = Date.now();
    target.updated_at = Date.now();
    sendLabEvent("forensic_target_collected", {
        target:forensicTargetSnapshot(target),
        harvest:labSanitizeValue(harvest, 0)
    });
    scheduleForensicAfterHarvest(target);
    emitRuntimeState("alvo forense coletado");
    return target;
}

function startForensicMemoryScan(reason, selectedTarget, callback) {
    const target = selectedTarget || chooseForensicTarget();

    if (!target)
        return {ok:false, reason:"no_known_target"};

    const weight = finiteNumber(target.weight);
    if (weight === null || weight <= 0)
        return {ok:false, reason:"target_without_weight"};

    if (forensicScanRunning)
        return {ok:false, reason:"scan_in_progress"};

    if (!forensicMemoryApiStatus.ok) {
        return {
            ok:false,
            reason:"memory_api_self_test_failed",
            error:String(forensicMemoryApiStatus.error || "autoteste falhou")
        };
    }

    const pattern = forensicFloatPattern(weight);
    if (pattern === "")
        return {ok:false, reason:"weight_pattern_failed"};

    let ranges = [];
    try {
        ranges = Process.enumerateRanges({
            protection:"rw-",
            coalesce:false
        });
    } catch (error) {
        return {
            ok:false,
            reason:"range_enumeration_failed",
            error:String(error)
        };
    }

    ranges = ranges.filter(function (range) {
        return Number(range.size) >= 4;
    });
    ranges.sort(function (left, right) {
        const leftAnonymous = left.file ? 1 : 0;
        const rightAnonymous = right.file ? 1 : 0;

        if (leftAnonymous !== rightAnonymous)
            return leftAnonymous - rightAnonymous;

        return Number(right.size) - Number(left.size);
    });

    const selectedRanges = [];
    let plannedBytes = 0;

    for (let i=0;
        i<ranges.length && plannedBytes<FORENSIC_SCAN_MAX_BYTES;
        i++) {
        const range = ranges[i];
        const allowed = Math.min(
            Number(range.size),
            FORENSIC_SCAN_MAX_BYTES - plannedBytes
        );

        if (allowed < 4)
            continue;

        let filePath = "";
        try {
            filePath = range.file ? String(range.file.path || "") : "";
        } catch (_) {}

        selectedRanges.push({
            base:range.base,
            size:allowed,
            protection:String(range.protection || ""),
            file:filePath
        });
        plannedBytes += allowed;
    }

    if (!selectedRanges.length)
        return {ok:false, reason:"no_readwrite_ranges"};

    forensicScanRunning = true;
    updateHudState();
    const serial = ++forensicScanSerial;
    const startedAt = Date.now();
    let rangeIndex = 0;
    let rangeOffset = 0;
    let scannedBytes = 0;
    let nextProgress = FORENSIC_SCAN_PROGRESS_BYTES;
    let nextConsoleProgress = FORENSIC_SCAN_CONSOLE_PROGRESS_BYTES;
    let scanErrors = 0;
    const candidates = [];
    const seenAddresses = new Set();

    sendLabEvent("forensic_memory_scan_started", {
        serial:serial,
        reason:reason || "manual",
        target:forensicTargetSnapshot(target),
        pattern_f32:pattern,
        range_count:selectedRanges.length,
        planned_bytes:plannedBytes,
        max_hits:FORENSIC_SCAN_MAX_HITS
    });
    log(
        "🧬 MEMÓRIA: procurando peso exato " + weight.toFixed(6) +
        " em " + (plannedBytes / (1024*1024)).toFixed(0) +
        " MB. Aguarde."
    );

    function finish(cancelled) {
        forensicScanRunning = false;
        updateHudState();
        candidates.sort(function (left, right) {
            return Number(right.score || 0) - Number(left.score || 0);
        });
        const saved = candidates.slice(0, FORENSIC_MAX_SAVED_CANDIDATES);
        forensicCandidates[target.target_id] = saved;

        sendLabEvent("forensic_memory_scan_finished", {
            serial:serial,
            cancelled:cancelled === true,
            reason:reason || "manual",
            target:forensicTargetSnapshot(target),
            elapsed_ms:Date.now() - startedAt,
            planned_bytes:plannedBytes,
            scanned_bytes:scannedBytes,
            scan_errors:scanErrors,
            total_hits:candidates.length,
            saved_candidates:saved
        });
        emitRuntimeState("varredura forense concluída");
        log(
            "🧬 MEMÓRIA: varredura concluída; " +
            candidates.length + " endereço(s) candidato(s), " +
            saved.length + " preservado(s)."
        );
        if (typeof callback === "function") {
            try {
                callback({
                    cancelled:cancelled === true,
                    target:target,
                    total_hits:candidates.length,
                    saved_candidates:saved,
                    scanned_bytes:scannedBytes,
                    scan_errors:scanErrors
                });
            } catch (error) {
                sendLabEvent("death_signal_callback_error", {
                    stage:"forensic_scan_finished",
                    error:String(error)
                });
            }
        }
    }

    function nextChunk() {
        if (scriptStopping || serial !== forensicScanSerial) {
            finish(true);
            return;
        }

        if (rangeIndex >= selectedRanges.length ||
            candidates.length >= FORENSIC_SCAN_MAX_HITS) {
            finish(false);
            return;
        }

        const range = selectedRanges[rangeIndex];
        const remaining = range.size - rangeOffset;

        if (remaining < 4) {
            rangeIndex++;
            rangeOffset = 0;
            setTimeout(nextChunk, 0);
            return;
        }

        const logicalSize = Math.min(
            FORENSIC_SCAN_CHUNK_BYTES,
            remaining
        );
        const overlap = remaining > logicalSize ? 3 : 0;
        const scanSize = logicalSize + overlap;
        const address = range.base.add(rangeOffset);

        try {
            Memory.scan(address, scanSize, pattern, {
                onMatch(matchAddress, matchSize) {
                    const key = pstr(matchAddress);

                    if (!seenAddresses.has(key)) {
                        seenAddresses.add(key);
                        const candidate = forensicWindowAt(
                            matchAddress,
                            target,
                            matchSize
                        );
                        if (candidate)
                            candidates.push(candidate);
                    }

                    if (candidates.length >= FORENSIC_SCAN_MAX_HITS)
                        return "stop";
                },
                onError(_) {
                    scanErrors++;
                },
                onComplete() {
                    rangeOffset += logicalSize;
                    scannedBytes += logicalSize;

                    if (scannedBytes >= nextProgress) {
                        sendLabEvent("forensic_memory_scan_progress", {
                            serial:serial,
                            scanned_bytes:scannedBytes,
                            planned_bytes:plannedBytes,
                            hits:candidates.length
                        });
                        nextProgress += FORENSIC_SCAN_PROGRESS_BYTES;
                    }

                    if (scannedBytes >= nextConsoleProgress) {
                        const percent = plannedBytes > 0
                            ? Math.min(
                                100,
                                Math.floor(scannedBytes * 100 / plannedBytes)
                            )
                            : 0;
                        log(
                            "🧬 MEMÓRIA: " + percent + "% analisado; " +
                            candidates.length + " candidato(s) até agora."
                        );
                        nextConsoleProgress +=
                            FORENSIC_SCAN_CONSOLE_PROGRESS_BYTES;
                    }

                    setTimeout(nextChunk, 0);
                }
            });
        } catch (_) {
            scanErrors++;
            rangeOffset += logicalSize;
            scannedBytes += logicalSize;
            setTimeout(nextChunk, 0);
        }
    }

    nextChunk();
    return {
        ok:true,
        serial:serial,
        target_id:target.target_id,
        planned_bytes:plannedBytes,
        range_count:selectedRanges.length
    };
}

function sanitizeRestoredCorpse(item) {
    if (!item)
        return null;

    const x = finiteNumber(item.x);
    const y = finiteNumber(item.y);
    const z = finiteNumber(item.z);

    if (x === null || y === null || z === null ||
        !validCoord(x) || !validCoord(y) || !validCoord(z))
        return null;

    return {
        reserve:item.reserve ?? "",
        species:item.species ?? null,
        weight:finiteNumber(item.weight),
        gender:item.gender ?? null,
        difficulty:item.difficulty ?? null,
        x:x,
        y:y,
        z:z,
        special_tag:String(item.special_tag || ""),
        recovered_animal_id:String(item.recovered_animal_id || ""),
        recovered_from_startup:item.recovered_from_startup === true,
        recovered_by_registry_catalog:
            item.recovered_by_registry_catalog === true,
        recovered_from_clue:item.recovered_from_clue === true,
        recovered_clue_association:String(
            item.recovered_clue_association || ""
        ),
        recovered_clue_x:finiteNumber(item.recovered_clue_x),
        recovered_clue_y:finiteNumber(item.recovered_clue_y),
        recovered_clue_z:finiteNumber(item.recovered_clue_z),
        recovered_registry_id:String(item.recovered_registry_id || ""),
        recovered_registry_id_history:Array.isArray(
            item.recovered_registry_id_history
        ) ? item.recovered_registry_id_history.slice(0, 16) : [],
        recovered_registry_descriptor:String(
            item.recovered_registry_descriptor || ""
        ),
        recovered_object_link:String(item.recovered_object_link || ""),
        recovered_stable_record_address:String(
            item.recovered_stable_record_address || ""
        ),
        recovered_stable_range_base:String(
            item.recovered_stable_range_base || ""
        ),
        recovered_from_alternative_layout:
            item.recovered_from_alternative_layout === true,
        recovered_alternative_record_address:String(
            item.recovered_alternative_record_address || ""
        ),
        recovered_alternative_range_base:String(
            item.recovered_alternative_range_base || ""
        ),
        recovered_availability:finiteNumber(item.recovered_availability),
        recovered_lifecycle:finiteNumber(item.recovered_lifecycle),
        recovered_live_pointer_count:finiteNumber(
            item.recovered_live_pointer_count
        ),
        recovered_readable_pointer_count:finiteNumber(
            item.recovered_readable_pointer_count
        ),
        restored_from_state:true,
        time:Number(item.time || Date.now())
    };
}

function restorePendingStateForActiveReserve() {
    if (activeReserve === "" || !restoredPendingBuffer.length)
        return 0;

    const buffered = restoredPendingBuffer;
    restoredPendingBuffer = [];

    // Somente estado do MESMO processo (verificado em Python) é importado.
    // IDs sem DNA vêm outra vez do catálogo vivo. Um animal com DNA/animal_id
    // capturado por pista ou morte é preservado enquanto não houver coleta
    // confirmada, mas fica identificado no log como não revalidado; sem o
    // evento de coleta fora do laboratório não há prova de que ainda existe.
    const restored = [];
    const rejected = [];
    const collectedAnimalIds = new Set(forensicTargets.filter(
        function (target) { return target.collected === true; }
    ).map(function (target) {
        return String(target.animal_id || "");
    }).filter(function (id) { return id !== ""; }));

    for (let i=0; i<buffered.length; i++) {
        const c = sanitizeRestoredCorpse(buffered[i]);
        const id = c ? String(c.recovered_animal_id || "") : "";
        const dna = c ? scopedStrongCorpseDna(c) : "";
        const harvestedPartial = c ? findHarvestedDnaByPartialFields(c) : "";
        if (!c || normalizeReserve(c.reserve) !==
                normalizeReserve(activeReserve) ||
            (dna === "" && id === "") ||
            (id !== "" && collectedAnimalIds.has(id)) ||
            (dna !== "" && harvestedDnas.has(dna)) ||
            (harvestedPartial !== "" &&
                harvestedDnas.has(harvestedPartial))) {
            rejected.push({key:String(buffered[i].key || ""),
                reason:"reserva, dados ou coleta incompatíveis"});
            continue;
        }

        // Um laboratório antigo pode ter associado um ID de catálogo a uma
        // pista só pela posição. Esse vínculo é descartado: o catálogo volta
        // a ser lido separadamente, sem remover o DNA salvo.
        c.recovered_registry_id = "";
        c.recovered_registry_id_history = [];
        c.recovered_by_registry_catalog = false;
        c.restored_unverified = true;

        const restoredIdentity = mergeObservedIdentity(c, "restore");
        if (restoredIdentity.index >= 0)
            continue;

        if (pendingMatchesRecoveredClue(c) ||
            (dna !== "" && pending.some(function (item) {
                return scopedStrongCorpseDna(item) === dna;
            })))
            continue;

        pending.push(c);
        restored.push(c);
    }

    sendLabEvent("runtime_pending_restored_same_game", {
        buffered_count:buffered.length,
        restored:labSanitizeValue(restored, 0),
        rejected:labSanitizeValue(rejected, 0),
        policy:"mesmo processo; sem vínculo de catálogo por posição; " +
            "restauração provisória até confirmação de coleta"
    });
    if (restored.length) {
        log("🧬 LAB: " + restored.length +
            " animal(is) visto(s) antes restaurado(s) nesta mesma partida. " +
            "A disponibilidade atual ainda não foi comprovada.");
        updateHudWarningForCount();
        finalClearDone = false;
        if (soloConfirmed)
            scheduleNearestUpdate(180, "animal salvo na mesma partida");
    }
    emitRuntimeState("histórico da mesma partida restaurado");
    return restored.length;
}

function importLabRuntimeState(state) {
    const data = state && typeof state === "object" ? state : {};
    const harvested = Array.isArray(data.harvested_dna_keys)
        ? data.harvested_dna_keys
        : [];
    let harvestedImported = 0;

    for (let i=0; i<harvested.length; i++) {
        const key = String(harvested[i] || "").trim();

        if (key === "" || key.length > 256 || harvestedDnas.has(key))
            continue;

        harvestedDnas.add(key);
        harvestedImported++;
    }

    forensicTargets = Array.isArray(data.forensic_targets)
        ? data.forensic_targets.slice(0, 128).filter(function (item) {
            return item && String(item.target_id || "") !== "";
        })
        : [];
    forensicCandidates = data.forensic_candidates &&
        typeof data.forensic_candidates === "object" &&
        !Array.isArray(data.forensic_candidates)
            ? data.forensic_candidates
            : {};
    restoredPendingBuffer = Array.isArray(data.pending_corpses)
        ? data.pending_corpses.slice(0, 128)
        : [];
    const savedAnchor = data.navigation_anchor;
    if (savedAnchor && finiteNumber(savedAnchor.x) !== null &&
        finiteNumber(savedAnchor.y) !== null &&
        finiteNumber(savedAnchor.z) !== null) {
        lastNavigationAnchor = {
            x:Number(savedAnchor.x),
            y:Number(savedAnchor.y),
            z:Number(savedAnchor.z),
            at:Number(savedAnchor.at || 0),
            reason:String(savedAnchor.reason || "estado restaurado"),
            key:String(savedAnchor.key || "")
        };
    }
    stableMetadataRangeBase = String(
        data.stable_metadata_range_base || ""
    );
    stableMetadataRangeSize = Number(
        data.stable_metadata_range_size || 0
    );
    stableMetadataRangeProtection = String(
        data.stable_metadata_range_protection || ""
    );
    stableMetadataRangeFile = String(
        data.stable_metadata_range_file || ""
    );
    if (!stableCachedRange())
        forgetStableRange();
    runtimeStateImported = true;
    const restored = restorePendingStateForActiveReserve();

    sendLabEvent("lab_runtime_state_imported", {
        game_identity:GAME_IDENTITY,
        harvested_imported:harvestedImported,
        harvested_total:harvestedDnas.size,
        pending_buffered:restoredPendingBuffer.length,
        pending_restored:restored,
        navigation_anchor:lastNavigationAnchor,
        forensic_targets:forensicTargets.length,
        forensic_candidate_groups:Object.keys(forensicCandidates).length,
        stable_metadata_range_base:stableMetadataRangeBase,
        stable_metadata_range_size:stableMetadataRangeSize
    });

    return {
        ok:true,
        harvested_imported:harvestedImported,
        harvested_total:harvestedDnas.size,
        pending_buffered:restoredPendingBuffer.length,
        pending_restored:restored,
        forensic_targets:forensicTargets.length,
        forensic_candidate_groups:Object.keys(forensicCandidates).length
    };
}

function labCloneStableEntries(entries) {
    const result = [];

    for (let i=0; i<(entries || []).length; i++) {
        const entry = entries[i] || {};
        result.push({
            descriptor:String(entry.descriptor || ""),
            registry_id:normalizeRegistryId(entry.registry_id),
            registry_id_hex:String(entry.registry_id_hex || ""),
            x:Number(entry.x),
            y:Number(entry.y),
            z:Number(entry.z),
            object_link:String(entry.object_link || ""),
            object_link_valid:entry.object_link_valid === true
        });
    }

    return result;
}

function labStableStateBeforeHarvest() {
    const now = Date.now();
    // A coleta pode acontecer entre duas leituras do monitor. Uma leitura
    // imediata desta lista pequena garante a fotografia exata do instante.
    const scan = scanOldCorpseRegistryOnce();
    return {
        source:scan === null ? "registry_unavailable" : "immediate_scan",
        captured_at:now,
        entries:scan === null
            ? []
            : labCloneStableEntries(scan.candidates)
    };
}

function finishLabCollectionTest(reason) {
    stopHudCountdown("todos os cadáveres foram confirmados como coletados");
    setDeathSignalPhase(
        DEATH_SIGNAL_PHASE_NORMAL,
        "todos os abates atuais foram coletados"
    );
    labRecoveryStage = 0;
    log("🟢 LAB: SEM AÇÃO NECESSÁRIA.");
    sendLabEvent("lab_collection_test_completed", {
        reason:String(reason || "contador chegou a zero"),
        pending:labPendingSnapshot()
    });
}

function removePendingByStableRegistryId(registryId, payload, delay) {
    const normalized = normalizeRegistryId(registryId);
    if (normalized === "")
        return null;

    const matches = [];
    for (let i=0; i<pending.length; i++) {
        if (normalizeRegistryId(pending[i].recovered_registry_id) ===
            normalized)
            matches.push(i);
    }

    if (matches.length !== 1) {
        sendLabEvent("stable_id_exact_removal_skipped", {
            registry_id:normalized,
            current_match_count:matches.length,
            delay_ms:delay,
            pending:labPendingSnapshot()
        });
        return null;
    }

    const hadOwnedMarker = markerOwned || currentKey !== "";
    cancelWaypointWork();
    const removed = pending.splice(matches[0], 1)[0];
    const aliases = removeStrongDnaAliases(removed);
    updateHudWarningForCount();
    // Coletar outro corpo não deve apagar e recriar a seta que já aponta
    // para um animal ainda pendente.
    const markedTargetStillPending = currentKey !== "" && pending.some(
        function (corpse) { return corpseKey(corpse) === currentKey; }
    );
    if (!markedTargetStillPending) {
        if (hadOwnedMarker)
            clearMarkerInternal();
        resetMarkerState();
    } else {
        currentIndex = pending.findIndex(function (corpse) {
            return corpseKey(corpse) === currentKey;
        });
    }
    rememberNavigationAnchor(
        removed,
        "coleta confirmada pelo desaparecimento do ID estável"
    );
    stopHudCountdown("coleta confirmada pelo ID estável");
    rememberHarvestedDna(payload, "ID estável desapareceu após coleta");
    if (pending.length > 0 &&
        deathSignalPhase === DEATH_SIGNAL_PHASE_STOP) {
        setDeathSignalPhase(
            DEATH_SIGNAL_PHASE_NORMAL,
            "a remoção atrasada confirmou a coleta"
        );
    }
    sendLabEvent("stable_id_exact_harvest_removed", {
        registry_id:normalized,
        delay_ms:delay,
        removed:labSanitizeValue(removed, 0),
        dna_aliases_removed:labSanitizeValue(aliases, 0),
        pending_after:labPendingSnapshot()
    });
    emitRuntimeState("coleta confirmada pelo desaparecimento do ID estável");
    log(
        "✅ COLETA CONFIRMADA PELO ID " + normalized +
        " em " + delay + " ms; restantes=" + pending.length + "."
    );
    sendGpsStatus(
        "harvest_stable_id",
        "ID " + normalized + "; restantes=" + pending.length
    );

    if (!pending.length) {
        clearOwn("último ID estável coletado", true);
        finishLabCollectionTest("último ID estável desapareceu");
        if (hadOwnedMarker) {
            const clearedSerial = switchSerial;
            setTimeout(function () {
                if (pending.length || switchSerial !== clearedSerial)
                    return;
                requestWaypointClearCapture(
                    "marcação restante após coleta por ID estável",
                    removed
                );
            }, 750);
        }
    } else {
        if (!PROTECT_SETWAYPOINT)
            manualWaypointOverride = false;
        finalClearDone = false;
        scheduleNearestUpdate(40, "ID coletado; marcar próximo cadáver");
    }

    return removed;
}

function labScheduleStableIdHarvestProbe(
    path,
    payload,
    beforeState,
    pendingBefore,
    match,
    removed
) {
    // harvest_animal2 confirma o Enter, mas o jogo ainda pode levar alguns
    // segundos para retirar o ID do catálogo. No teste 0.9.20 essa diferença
    // chegou a 2,7 s; a janela antiga de 2,2 s terminava cedo demais.
    // Quando a coleta já foi ligada a um DNA, o ID não pode descontar outro
    // animal. Uma leitura diagnóstica basta; preserve a série completa
    // somente para uma coleta antiga ainda sem associação segura.
    const delays = removed !== null ? [2500] :
        [150, 500, 1200, 2200, 3500, 5000, 8000];

    for (let i=0; i<delays.length; i++) {
        const delay = delays[i];
        const timer = setTimeout(function () {
            const scan = scanOldCorpseRegistryOnce();
            if (scan === null) {
                sendLabEvent("stable_id_harvest_probe_unavailable", {
                    delay_ms:delay,
                    path:path,
                    harvest:labSanitizeValue(payload, 0)
                });
                return;
            }
            const after = labCloneStableEntries(scan.candidates);
            const afterIds = new Set(after.map(function (entry) {
                return normalizeRegistryId(entry.registry_id);
            }));
            const disappeared = (beforeState.entries || []).filter(
                function (entry) {
                    const registryId = normalizeRegistryId(entry.registry_id);
                    return registryId !== "" && !afterIds.has(registryId);
                }
            );
            const pendingIds = new Set((pendingBefore || []).map(
                function (entry) {
                    return normalizeRegistryId(entry.recovered_registry_id);
                }
            ).filter(function (value) { return value !== ""; }));
            const disappearedPending = disappeared.filter(function (entry) {
                return pendingIds.has(normalizeRegistryId(entry.registry_id));
            });

            sendLabEvent("stable_id_harvest_probe", {
                delay_ms:delay,
                path:path,
                harvest:labSanitizeValue(payload, 0),
                before_source:beforeState.source,
                before_age_ms:Math.max(
                    0,
                    Date.now() - Number(beforeState.captured_at || Date.now())
                ),
                validated_before:labSanitizeValue(beforeState.entries, 0),
                validated_after:labSanitizeValue(after, 0),
                disappeared_ids:labSanitizeValue(disappeared, 0),
                disappeared_pending_ids:labSanitizeValue(
                    disappearedPending,
                    0
                ),
                exact_single_pending_disappeared:
                    disappearedPending.length === 1,
                existing_match:labSanitizeValue(match, 0),
                existing_removed:labSanitizeValue(removed, 0)
            });

            // Uma coleta já associada por DNA só pode retirar um item da
            // fila. Na 0.9.18, o desaparecimento simultâneo de um registro
            // tipo 39 retirou um segundo corpo e fez o contador cair duas
            // unidades. O ID continua sendo registrado para diagnóstico, mas
            // nunca substitui uma associação de DNA já confirmada.
            const mayRemoveByStableId = removed === null &&
                harvestWaitsForStableRegistryId(match);

            if (disappearedPending.length === 1 && mayRemoveByStableId) {
                removePendingByStableRegistryId(
                    disappearedPending[0].registry_id,
                    payload,
                    delay
                );
            } else if (disappearedPending.length > 0 && !mayRemoveByStableId) {
                sendLabEvent("stable_id_observed_without_second_removal", {
                    delay_ms:delay,
                    existing_match:labSanitizeValue(match, 0),
                    existing_removed:labSanitizeValue(removed, 0),
                    disappeared_pending_ids:labSanitizeValue(
                        disappearedPending,
                        0
                    ),
                    reason:"uma coleta confirmada não pode remover dois animais"
                });
            }
        }, delay);
        labBurstTimers.push(timer);
    }
}

function labStartRegistryMonitor() {
    if (labRegistryMonitorTimer !== null)
        return;

    setTimeout(function () {
        labCaptureRegistry("inicio_do_laboratorio", true, true);
    }, 1200);

    labRegistryMonitorTimer = setInterval(function () {
        if (!scriptStopping)
            labCaptureRegistry("monitor_automatico", false, false);
    }, LAB_REGISTRY_MONITOR_MS);
}

function labStopRegistryMonitor() {
    stopWaypointWriterMonitor("encerramento do laboratório");
    stopWaypointHardwareWatch("encerramento do laboratório");
    try { labCaptureRegistry("encerramento_do_laboratorio", true, true); }
    catch (_) {}

    if (labRegistryMonitorTimer !== null) {
        try { clearInterval(labRegistryMonitorTimer); }
        catch (_) {}
        labRegistryMonitorTimer = null;
    }

    for (let i=0; i<labBurstTimers.length; i++) {
        try { clearTimeout(labBurstTimers[i]); }
        catch (_) {}
    }
    labBurstTimers.length = 0;
}

function labRecordHttp(path, object) {
    const low = String(path || "").toLowerCase();

    if (low.indexOf("animal") < 0 && low.indexOf("weapon") < 0 &&
        low.indexOf("kill") < 0 && low.indexOf("clue") < 0)
        return;

    sendLabEvent("http_event", {
        path:String(path || ""),
        player:getPlayerPos(),
        payload:labSanitizeValue(object, 0)
    });
}

function resolveHudExport(moduleName, exportName) {
    let address = Module.findGlobalExportByName(exportName);

    if (address)
        return address;

    try { Module.load(moduleName); }
    catch (_) {}

    return Module.findGlobalExportByName(exportName);
}

function shouldEnableHud() {
    // O HUD dá retorno visual imediato, mas o núcleo do GPS continua
    // bloqueado até soloConfirmed. Multiplayer detectado desliga tudo.
    return !multiplayerBlocked && !scriptStopping &&
        hudUserVisible && hudGameVisible;
}

function stopHudCountdown(reason) {
    const previous = hudCountdownSeconds;
    hudCountdownSerial++;

    if (hudCountdownTimer !== null) {
        try { clearInterval(hudCountdownTimer); }
        catch (_) {}
        hudCountdownTimer = null;
    }

    hudCountdownSeconds = -1;
    hudCountdownReason = "";

    if (previous >= 0) {
        sendLabEvent("hud_countdown_stopped", {
            previous_seconds:previous,
            reason:String(reason || "ação concluída")
        });
        updateHudState();
    }
}

function startHudCountdown(seconds, reason, onComplete) {
    stopHudCountdown("nova contagem iniciada");

    const total = Math.max(1, Math.min(999, Math.ceil(Number(seconds) || 1)));
    const serial = ++hudCountdownSerial;
    hudCountdownSeconds = total;
    hudCountdownReason = String(reason || "AGUARDE");
    const deadlineMs = Date.now() + total * 1000;

    function announce() {
        updateHudState();
        if (hudCountdownSeconds === total)
            log("🟠 LAB: ANALISANDO...");
        sendLabEvent("hud_countdown_tick", {
            seconds:hudCountdownSeconds,
            reason:hudCountdownReason
        });
    }

    announce();
    hudCountdownTimer = setInterval(function () {
        if (serial !== hudCountdownSerial || scriptStopping)
            return;

        hudCountdownSeconds = Math.max(0,
            Math.ceil((deadlineMs - Date.now()) / 1000));
        if (hudCountdownSeconds > 0) {
            announce();
            return;
        }

        try { clearInterval(hudCountdownTimer); }
        catch (_) {}
        hudCountdownTimer = null;
        hudCountdownSeconds = -1;
        const completedReason = hudCountdownReason;
        hudCountdownReason = "";
        updateHudState();
        sendLabEvent("hud_countdown_finished", {
            reason:completedReason
        });

        if (typeof onComplete === "function") {
            try { onComplete(); }
            catch (error) {
                sendLabEvent("hud_countdown_callback_error", {
                    reason:completedReason,
                    error:String(error)
                });
            }
        }
    }, 1000);
}

function updateHudState() {
    if (hudSetStateNative === null)
        return;

    const enabled = shouldEnableHud();

    const normalMode = forensicScanRunning
        ? 2
        : ((stableAvailabilityScanRunning ||
            (initialOldCorpseScanStarted && !initialOldCorpseScanFinished))
            ? 1 : 0);
    const hudMode = playerProbeRunning ? 28 : hudCountdownSeconds >= 0
        ? 19
        : (playerProbePhase === 1 ? 21 :
            (playerProbePhase === 2 ? 22 :
        (deathSignalPhase > DEATH_SIGNAL_PHASE_NORMAL
            ? 9 + deathSignalPhase
            : (hudGpsActionRequired
                ? 18 : normalMode))));
    const displayedCount = playerProbeRunning
        ? playerProbeScanProgressPercent : hudCountdownSeconds >= 0
        ? hudCountdownSeconds : pending.length;

    try {
        hudSetStateNative(
            enabled ? 1 : 0,
            displayedCount,
            hudCorner,
            hudWarningActive ? 1 : 0,
            soloConfirmed ? 1 : 0,
            hudMode
        );
    } catch (_) {}
}

function hudCornerName(index) {
    return [
        "superior esquerdo",
        "superior direito",
        "inferior esquerdo",
        "inferior direito"
    ][index] || "superior direito";
}

function setHudCorner(index) {
    const value = Number(index);
    hudCorner = Number.isFinite(value)
        ? Math.max(0, Math.min(3, Math.trunc(value)))
        : 1;
    updateHudState();
    return {ok:true, corner:hudCorner, name:hudCornerName(hudCorner)};
}

function setHudUserVisible(visible) {
    hudUserVisible = !!visible;
    updateHudState();
    return {ok:true, visible:hudUserVisible};
}

function toggleHudUserVisible() {
    return setHudUserVisible(!hudUserVisible);
}

function bulkDeadEntryKey(entry) {
    return String(entry && entry.alternative_record_address || "");
}

function bulkRegistryEntryKey(entry) {
    return normalizeRegistryId(entry && entry.registry_id);
}

function bulkRegistryCandidates(scan) {
    const source = scan && Array.isArray(scan.candidates)
        ? scan.candidates : [];
    const result = [];
    const seen = new Set();

    for (let i=0; i<source.length; i++) {
        const entry = source[i];
        const key = bulkRegistryEntryKey(entry);

        if (key === "" || seen.has(key) ||
            rejectedCatalogRegistryIds.has(key) ||
            !validCoord(entry.x) || !validCoord(entry.y) ||
            !validCoord(entry.z))
            continue;

        seen.add(key);
        result.push(entry);

        if (result.length >= BULK_SCAN_MAX_BODIES)
            break;
    }

    return result;
}

function rayXCatalogAnalysis(entries, phase) {
    if (!RAY_X_TRACE)
        return [];

    const source = Array.isArray(entries) ? entries : [];
    const player = getPlayerPos();
    const result = [];

    for (let i=0; i<source.length; i++) {
        const entry = source[i];
        let nearestId = "";
        let nearestDistance = null;

        for (let j=0; j<source.length; j++) {
            if (i === j)
                continue;
            const distance = distanceXZ(entry, source[j]);
            if (nearestDistance === null || distance < nearestDistance) {
                nearestDistance = distance;
                nearestId = bulkRegistryEntryKey(source[j]);
            }
        }

        result.push({
            ordinal:i + 1,
            registry_id:bulkRegistryEntryKey(entry),
            descriptor:String(entry.descriptor || ""),
            x:Number(entry.x),
            y:Number(entry.y),
            z:Number(entry.z),
            object_link:String(entry.object_link || ""),
            object_link_valid:entry.object_link_valid === true,
            distance_to_player_m:player ? distanceXZ(player, entry) : null,
            nearest_other_registry_id:nearestId,
            nearest_other_distance_m:nearestDistance
        });
    }

    sendLabEvent("ray_x_catalog_analysis", {
        phase:String(phase || "catálogo"),
        catalog_count:source.length,
        player:player,
        entries:result,
        pending_before:labPendingSnapshot()
    });

    for (let i=0; i<result.length; i++) {
        const entry = result[i];
        log(
            "🧪 RAIO-X CATÁLOGO #" + entry.ordinal +
            " ID=" + entry.registry_id +
            " pos=(" + entry.x.toFixed(2) + "," +
            entry.y.toFixed(2) + "," + entry.z.toFixed(2) + ")" +
            (entry.distance_to_player_m !== null
                ? " jogador≈" + entry.distance_to_player_m.toFixed(1) + "m"
                : "") +
            (entry.nearest_other_distance_m !== null
                ? " vizinho≈" +
                    entry.nearest_other_distance_m.toFixed(1) + "m"
                : "")
        );
    }

    return result;
}

function bulkRegistryEntryToCorpse(entry) {
    const registryId = bulkRegistryEntryKey(entry);

    return {
        reserve:activeReserve,
        species:null,
        weight:null,
        gender:null,
        difficulty:null,
        x:Number(entry.x),
        y:Number(entry.y),
        z:Number(entry.z),
        special_tag:"",
        time:Date.now(),
        recovered_from_startup:true,
        recovered_by_registry_catalog:true,
        recovered_registry_id:registryId,
        recovered_registry_id_history:registryId !== ""
            ? [registryId] : [],
        recovered_registry_descriptor:String(entry.descriptor || ""),
        recovered_object_link:String(entry.object_link || ""),
        recovered_availability:null,
        recovered_lifecycle:null
    };
}

function bulkApplyConfirmedRegistryBodies(firstScan, secondScan, reason, manual) {
    const first = bulkRegistryCandidates(firstScan);
    const second = bulkRegistryCandidates(secondScan);
    const firstById = {};
    const knownRegistryIdsBefore = new Set(pending.map(function (corpse) {
        return normalizeRegistryId(corpse.recovered_registry_id);
    }).filter(function (id) { return id !== ""; }));

    for (let i=0; i<first.length; i++)
        firstById[bulkRegistryEntryKey(first[i])] = first[i];

    let confirmed = [];

    for (let i=0; i<second.length; i++) {
        const entry = second[i];
        const before = firstById[bulkRegistryEntryKey(entry)];

        if (!before || distanceXZ(before, entry) > 2.5 ||
            Math.abs(Number(before.y) - Number(entry.y)) > 6.0)
            continue;

        confirmed.push(entry);
    }

    rayXCatalogAnalysis(confirmed, "duas leituras confirmadas");

    // A análise não descarta IDs. Só o terceiro F6 descarta o alvo atual.
    // Um F6 preserva dados de DNA/pista dos IDs ainda presentes no catálogo.
    // Só descarta entradas antigas cujo ID sumiu do catálogo do jogo.
    const confirmedIds = new Set(confirmed.map(bulkRegistryEntryKey));
    const retained = pending.filter(function (corpse) {
        return corpse.recovered_by_registry_catalog !== true ||
            confirmedIds.has(normalizeRegistryId(corpse.recovered_registry_id));
    });
    const added = [];
    const deferred = [];
    const identitiesWithoutId = retained.filter(function (corpse) {
        return corpse.recovered_by_registry_catalog !== true &&
            normalizeRegistryId(corpse.recovered_registry_id) === "" &&
            (strongCorpseDna(corpse) !== "" ||
                String(corpse.recovered_animal_id || "") !== "");
    });

    for (let i=0; i<confirmed.length; i++) {
        const entry = confirmed[i];
        const corpse = bulkRegistryEntryToCorpse(entry);
        const registryId = bulkRegistryEntryKey(entry);
        let duplicate = false;

        for (let j=0; j<retained.length; j++) {
            const existing = retained[j];
            const existingRegistryId = normalizeRegistryId(
                existing.recovered_registry_id
            );
            const sameId = existingRegistryId !== "" &&
                existingRegistryId === registryId;

            // IDs diferentes representam animais diferentes mesmo se os
            // corpos caírem no mesmo ponto. Posição serve somente ao GPS.
            if (!sameId)
                continue;

            rememberRegistryId(existing, registryId);
            existing.recovered_registry_id = registryId;
            existing.recovered_registry_descriptor =
                corpse.recovered_registry_descriptor;
            existing.recovered_object_link = corpse.recovered_object_link;
            knownOldCorpseIds.add(registryId);
            duplicate = true;
            break;
        }

        if (!duplicate && identitiesWithoutId.length) {
            // O registro de catálogo não contém DNA. No ZIP de 19/09 o
            // único ID daqui e o único DNA da fila eram o MESMO animal;
            // somá-los produziu ABATES:2. Com vários animais na mesma
            // posição, posição nunca comprova identidade: guarde o
            // candidato no log até uma pista/DNA fornecer a ligação.
            deferred.push({
                registry_id:registryId,
                descriptor:corpse.recovered_registry_descriptor,
                identity_without_id:identitiesWithoutId.map(corpseKey)
            });
            continue;
        }

        if (!duplicate) {
            knownOldCorpseIds.add(registryId);
            retained.push(corpse);
            added.push(corpse);
        }
    }

    const newRegistryIds = added.filter(function (corpse) {
        return !knownRegistryIdsBefore.has(
            normalizeRegistryId(corpse.recovered_registry_id)
        );
    }).length;
    bulkDeferredRegistryIds = new Set(deferred.map(function (item) {
        return item.registry_id;
    }));
    bulkDeferredRegistryReserve = normalizeReserve(activeReserve);
    pending = retained;
    bulkDeadBodyScanRunning = false;
    bulkDeadBodyScanFinished = true;
    manualOldCorpseScanVisible = false;
    stopHudCountdown("catálogo confirmado");
    setDeathSignalPhase(DEATH_SIGNAL_PHASE_NORMAL, "catálogo confirmado");
    updateHudState();
    updateHudWarningForCount();
    finalClearDone = false;

    sendLabEvent("registry_bulk_startup_recovery_finished", {
        reason:String(reason || "duas leituras consecutivas do catálogo"),
        scan_attempt:bulkDeadBodyScanAttempt,
        first_count:first.length,
        second_count:second.length,
        confirmed_in_two_reads:confirmed.length,
        added_count:added.length,
        deferred_ambiguous_count:deferred.length,
        deferred_ambiguous:labSanitizeValue(deferred, 0),
        new_registry_ids:newRegistryIds,
        manual:!!manual,
        confirmed:labSanitizeValue(confirmed, 0),
        pending:labPendingSnapshot()
    });
    emitRuntimeState("busca de cadáveres pelo catálogo concluída");

    if (confirmed.length > 0)
        log(`🔎 CATÁLOGO: ${confirmed.length} ID(s); ` +
            added.length + " novo(s) e " + deferred.length +
            " sem ligação segura ao DNA.");
    else
        log("✅ BUSCA CONCLUÍDA: nenhum abate anterior disponível.");

    if (pending.length) {
        consecutiveEmptyManualScans = 0;
        log(
            "✅ " + pending.length +
            " ABATE(S) DISPONÍVEL(IS). COLETE EM QUALQUER ORDEM."
        );
        sendGpsStatus(
            "registry_bulk_ready",
            pending.length + " corpos confirmados pelo catálogo"
        );
        scheduleNearestUpdate(80, "cadáver mais próximo do catálogo");
    } else {
        clearOwn("catálogo sem cadáver disponível", false);
        sendGpsStatus("registry_bulk_empty", "nenhum cadáver disponível");
    }

    const secondF6ForCurrentTarget = manual && f6PressCount >= 2 &&
        f6TargetKey !== "" && pending.some(function (corpse) {
            return corpseKey(corpse) === f6TargetKey;
        });
    if (secondF6ForCurrentTarget) {
        labRecoveryStage = 1;
        setDeathSignalPhase(DEATH_SIGNAL_PHASE_SEE_CLUE,
            "segundo F6: examine uma pista de sangue deste animal");
        log("🩸 F6 2/3: análise concluída. Examine a pista de sangue " +
            "deste animal. Para ignorar só esta marcação, F6 novamente.");
    } else if (manual) {
        labRecoveryStage = 0;
        setDeathSignalPhase(DEATH_SIGNAL_PHASE_NORMAL,
            "primeiro F6: análise concluída; marcação atualizada");
        if (deferred.length > 0)
            log("🔎 F6 1/3: IDs sem DNA guardados para conferir na " +
                "próxima busca; marque o corpo identificado mais próximo.");
    } else if (deferred.length > 0) {
        labRecoveryStage = 0;
        log("🟠 LAB: " + deferred.length +
            " ID(s) sem ligação com a pista. COLETE O CORPO DA PISTA; " +
            "DEPOIS CONFERIREI AUTOMATICAMENTE OS IDs RESTANTES.");
    } else {
        labRecoveryStage = 0;
        log("🟢 LAB: SEM AÇÃO NECESSÁRIA.");
    }

    return {ok:true, found:confirmed.length, pending:pending.length};
}

function bulkConfirmedDeadEntries(entries) {
    const result = [];
    const seen = new Set();
    const source = Array.isArray(entries) ? entries : [];
    for (let i=0; i<source.length; i++) {
        const entry = source[i];
        const key = bulkDeadEntryKey(entry);
        if (key === "" || seen.has(key) ||
            entry.death_signal_confirmed !== true ||
            Number(entry.stable_availability) !== 1 ||
            Number(entry.stable_lifecycle) !== STABLE_RECORD_ACTIVE_STATE ||
            Number(entry.stable_readable_pointers) !==
                ALTERNATIVE_RECORD_POINTER_OFFSETS.length)
            continue;
        seen.add(key);
        result.push(entry);
        if (result.length >= BULK_SCAN_MAX_BODIES)
            break;
    }
    return result;
}

function bulkEntryToCorpse(entry) {
    return {
        reserve:activeReserve,
        species:null,
        weight:finiteNumber(entry.stable_weight),
        gender:null,
        difficulty:null,
        x:Number(entry.stable_record_x),
        y:Number(entry.stable_record_y),
        z:Number(entry.stable_record_z),
        special_tag:"",
        time:Date.now(),
        recovered_from_startup:true,
        recovered_by_bulk_signal:true,
        recovered_from_alternative_layout:true,
        recovered_alternative_record_address:String(
            entry.alternative_record_address || ""
        ),
        recovered_alternative_range_base:String(
            entry.alternative_record_range_base || ""
        ),
        recovered_availability:1,
        recovered_lifecycle:STABLE_RECORD_ACTIVE_STATE,
        recovered_live_pointer_count:Number(
            entry.stable_nonzero_pointers || 0
        ),
        recovered_readable_pointer_count:Number(
            entry.stable_readable_pointers || 0
        ),
        recovered_death_signal:labSanitizeValue(
            entry.death_signal_fields || {}, 0
        )
    };
}

function bulkApplyConfirmedBodies(firstEntries, secondEntries, reason) {
    const firstDead = bulkConfirmedDeadEntries(firstEntries);
    const secondDead = bulkConfirmedDeadEntries(secondEntries);
    const firstByKey = {};
    for (let i=0; i<firstDead.length; i++)
        firstByKey[bulkDeadEntryKey(firstDead[i])] = firstDead[i];

    const confirmed = [];
    for (let i=0; i<secondDead.length; i++) {
        const entry = secondDead[i];
        const key = bulkDeadEntryKey(entry);
        const before = firstByKey[key];
        if (!before || !sameWeight(before.stable_weight, entry.stable_weight))
            continue;
        confirmed.push(entry);
    }

    const retained = pending.filter(function (corpse) {
        return corpse.recovered_by_bulk_signal !== true;
    });
    const added = [];

    for (let i=0; i<confirmed.length; i++) {
        const corpse = bulkEntryToCorpse(confirmed[i]);
        let duplicate = false;

        for (let j=0; j<retained.length; j++) {
            const existing = retained[j];
            const existingRecord = String(
                existing.recovered_alternative_record_address || ""
            );
            const corpseRecord = String(
                corpse.recovered_alternative_record_address || ""
            );
            if (existingRecord === "" || existingRecord !== corpseRecord)
                continue;

            existing.recovered_from_alternative_layout = true;
            existing.recovered_alternative_record_address =
                corpse.recovered_alternative_record_address;
            existing.recovered_alternative_range_base =
                corpse.recovered_alternative_range_base;
            existing.recovered_availability = 1;
            existing.recovered_lifecycle = STABLE_RECORD_ACTIVE_STATE;
            existing.recovered_live_pointer_count =
                corpse.recovered_live_pointer_count;
            existing.recovered_readable_pointer_count =
                corpse.recovered_readable_pointer_count;
            duplicate = true;
            break;
        }

        if (!duplicate) {
            retained.push(corpse);
            added.push(corpse);
        }
    }

    const wasManualScan = manualOldCorpseScanVisible;
    pending = retained;
    bulkDeadBodyScanRunning = false;
    bulkDeadBodyScanFinished = true;
    manualOldCorpseScanVisible = false;
    stopHudCountdown("busca limpa concluída");
    setDeathSignalPhase(DEATH_SIGNAL_PHASE_NORMAL, "busca limpa concluída");
    updateHudState();
    updateHudWarningForCount();
    finalClearDone = false;

    if (secondEntries.length > 0) {
        try {
            const baseText = String(
                secondEntries[0].alternative_record_range_base || ""
            );
            if (baseText !== "") {
                const liveRange = Process.findRangeByAddress(ptr(baseText));
                if (liveRange)
                    rememberStableRange(stableRangeInfo(liveRange));
            }
        } catch (_) {}
    }

    const aliveIgnored = secondEntries.filter(function (entry) {
        return entry.alive_signal_confirmed === true;
    }).length;
    const unknownIgnored = secondEntries.length - secondDead.length -
        aliveIgnored;

    sendLabEvent("bulk_startup_recovery_finished", {
        reason:String(reason || "concluído"),
        scan_attempt:bulkDeadBodyScanAttempt,
        first_catalog_count:firstEntries.length,
        second_catalog_count:secondEntries.length,
        first_dead_count:firstDead.length,
        second_dead_count:secondDead.length,
        confirmed_in_two_reads:confirmed.length,
        added_count:added.length,
        alive_ignored:aliveIgnored,
        unknown_ignored:unknownIgnored,
        confirmed:labSanitizeValue(confirmed, 0),
        pending:labPendingSnapshot()
    });
    emitRuntimeState("busca limpa de cadáveres concluída");

    log(
        "🧬 BUSCA LIMPA: " + confirmed.length +
        " cadáver(es) disponível(is) confirmado(s); " +
        aliveIgnored + " animal(is) vivo(s) ignorado(s); " +
        unknownIgnored + " registro(s) incerto(s) ignorado(s)."
    );

    if (pending.length) {
        consecutiveEmptyManualScans = 0;
        log(
            "✅ " + pending.length +
            " ABATE(S) DISPONÍVEL(IS). COLETE EM QUALQUER ORDEM."
        );
        sendGpsStatus("bulk_ready", pending.length + " corpos confirmados");
        labRecoveryStage = 0;
        log("🟢 LAB: SEM AÇÃO NECESSÁRIA.");
        scheduleNearestUpdate(80, "cadáver mais próximo da busca limpa");
    } else {
        clearOwn("busca limpa sem cadáver disponível", false);
        sendGpsStatus("bulk_empty", "nenhum cadáver disponível confirmado");
        if (wasManualScan) {
            consecutiveEmptyManualScans++;
            if (consecutiveEmptyManualScans >= 2) {
                setDeathSignalPhase(DEATH_SIGNAL_PHASE_SEE_CLUE,
                    "duas buscas solicitadas sem abate encontrado");
                log("🟠 LAB: EXAMINE UMA PISTA DE SANGUE DO ANIMAL QUE FALTA.");
            } else {
                log("🟢 LAB: SEM AÇÃO NECESSÁRIA.");
            }
        }
    }

    return {ok:true, found:confirmed.length, pending:pending.length};
}

function bulkDeadBodyScanFailed(reason) {
    bulkDeadBodyScanRunning = false;
    bulkDeadBodyScanFinished = true;
    manualOldCorpseScanVisible = false;
    stopHudCountdown("busca de cadáveres falhou");
    setDeathSignalPhase(DEATH_SIGNAL_PHASE_SEND_ZIP,
        "o catálogo não ficou disponível; clique PARAR e envie o ZIP");
    updateHudState();
    sendLabEvent("bulk_startup_recovery_failed", {
        reason:String(reason || "memória indisponível"),
        attempts:bulkDeadBodyScanAttempt
    });
    log("❌ BUSCA LIMPA: " + String(reason || "memória indisponível") + ".");
    log("🟠 LAB: ERRO AO LER OS ABATES. NÃO REPITA F6; " +
        "CLIQUE PARAR E ENVIE O ZIP.");
}

function runBulkDeadBodyScan(manual, reason) {
    if (scriptStopping || multiplayerBlocked || !soloConfirmed ||
        bulkDeadBodyScanRunning)
        return;

    bulkDeadBodyScanRunning = true;
    manualOldCorpseScanVisible = !!manual;
    bulkDeadBodyScanAttempt++;
    const serial = ++bulkDeadBodyScanSerial;
    setDeathSignalPhase(
        DEATH_SIGNAL_PHASE_ANALYZING,
        "procurando cadáveres sem histórico"
    );
    // O HUD mostra ANALISANDO enquanto a leitura realmente estiver ativa.
    // Um temporizador de 18 s não mede a duração da leitura do jogo.
    updateHudState();

    sendLabEvent("bulk_startup_recovery_started", {
        manual:!!manual,
        reason:String(reason || "automático"),
        attempt:bulkDeadBodyScanAttempt,
        player:getPlayerPos()
    });

    const firstScan = scanOldCorpseRegistryOnce();

    if (firstScan === null) {
        bulkDeadBodyScanRunning = false;
        manualOldCorpseScanVisible = false;
        updateHudState();

        if (bulkDeadBodyScanAttempt < BULK_SCAN_MAX_ATTEMPTS) {
            bulkDeadBodyScanTimer = clearTimeoutSafe(bulkDeadBodyScanTimer);
            bulkDeadBodyScanTimer = setTimeout(function () {
                bulkDeadBodyScanTimer = null;
                runBulkDeadBodyScan(manual, "aguardando catálogo da reserva");
            }, BULK_SCAN_RETRY_MS);
            return;
        }

        bulkDeadBodyScanFailed(
            "o catálogo da reserva não ficou disponível"
        );
        return;
    }

    sendLabEvent("registry_bulk_first_read", {
        candidate_count:bulkRegistryCandidates(firstScan).length,
        candidates:labSanitizeValue(
            bulkRegistryCandidates(firstScan), 0
        )
    });

    bulkDeadBodyScanTimer = clearTimeoutSafe(bulkDeadBodyScanTimer);
    bulkDeadBodyScanTimer = setTimeout(function () {
        bulkDeadBodyScanTimer = null;

        if (scriptStopping || serial !== bulkDeadBodyScanSerial)
            return;

        const secondScan = scanOldCorpseRegistryOnce();

        if (secondScan === null) {
            bulkDeadBodyScanRunning = false;
            manualOldCorpseScanVisible = false;
            updateHudState();

            if (bulkDeadBodyScanAttempt >= BULK_SCAN_MAX_ATTEMPTS) {
                bulkDeadBodyScanFailed(
                    "o catálogo mudou durante todas as confirmações"
                );
                return;
            }

            scheduleBulkDeadBodyScan(
                BULK_SCAN_RETRY_MS,
                manual,
                "catálogo mudou durante a confirmação"
            );
            return;
        }

        bulkApplyConfirmedRegistryBodies(
            firstScan,
            secondScan,
            reason || "duas leituras consecutivas do catálogo",
            manual
        );
    }, BULK_SCAN_CONFIRM_DELAY_MS);
}

function scheduleBulkDeadBodyScan(delayMs, manual, reason) {
    if (scriptStopping || multiplayerBlocked || !soloConfirmed)
        return {ok:false, reason:"solo_not_ready"};
    if (bulkDeadBodyScanRunning)
        return {ok:false, reason:"search_in_progress"};

    if (!manual && bulkDeadBodyScanFinished)
        return {ok:false, reason:"search_finished"};

    if (!manual && bulkDeadBodyScanTimer !== null)
        return {ok:false, reason:"search_scheduled"};

    bulkDeadBodyScanTimer = clearTimeoutSafe(bulkDeadBodyScanTimer);
    bulkDeadBodyScanTimer = setTimeout(function () {
        bulkDeadBodyScanTimer = null;
        runBulkDeadBodyScan(!!manual, reason || "automático");
    }, Math.max(0, Number(delayMs || 0)));
    return {ok:true, pending:pending.length};
}

// Se uma pista sem ID impediu a inclusão de IDs estáveis, não perder todos
// os outros corpos depois que ela for coletada. Espere a remoção do catálogo
// terminar e releia cada ID duas vezes; nunca una o DNA à posição de um ID.
function scheduleDeferredCatalogRecheckAfterHarvest(sequence) {
    if (!bulkDeferredRegistryIds.size ||
        bulkDeferredRegistryReserve !== normalizeReserve(activeReserve))
        return false;
    const withoutId = pending.some(function (corpse) {
        return corpse.recovered_by_registry_catalog !== true &&
            normalizeRegistryId(corpse.recovered_registry_id) === "" &&
            (strongCorpseDna(corpse) !== "" ||
                String(corpse.recovered_animal_id || "") !== "");
    });
    if (withoutId)
        return false;

    const reserve = normalizeReserve(activeReserve);
    const previousCount = bulkDeferredRegistryIds.size;
    bulkDeadBodyScanTimer = clearTimeoutSafe(bulkDeadBodyScanTimer);
    bulkDeadBodyScanTimer = setTimeout(function () {
        bulkDeadBodyScanTimer = null;
        if (scriptStopping || normalizeReserve(activeReserve) !== reserve ||
            !bulkDeferredRegistryIds.size || bulkDeadBodyScanRunning ||
            pending.some(function (corpse) {
                return corpse.recovered_by_registry_catalog !== true &&
                    normalizeRegistryId(corpse.recovered_registry_id) === "" &&
                    (strongCorpseDna(corpse) !== "" ||
                        String(corpse.recovered_animal_id || "") !== "");
            }))
            return;
        runBulkDeadBodyScan(false,
            "revalidar IDs adiados após coleta de pista sem vínculo");
    }, 9000);
    sendLabEvent("deferred_catalog_recheck_scheduled", {
        sequence:Number(sequence || 0),
        deferred_ids:previousCount,
        wait_ms:9000,
        reason:"DNA coletado; IDs só serão recontados se permanecerem no catálogo"
    });
    log("🧪 LAB: " + previousCount +
        " ID(s) pendente(s) de validação; verificarei novamente em 9 s.");
    return true;
}

function forceBulkDeadBodyScan() {
    if (scriptStopping || multiplayerBlocked)
        return {ok:false, reason:"sessão indisponível"};
    if (!soloConfirmed || activeReserve === "")
        return {ok:false, reason:"reserve_not_ready"};
    if (bulkDeadBodyScanRunning || bulkDeadBodyScanTimer !== null)
        return {ok:false, reason:"search_in_progress"};

    const player = getPlayerPos();
    const index = pending.findIndex(function (corpse) {
        return corpseKey(corpse) === currentKey;
    });
    const selected = index >= 0 ? pending[index] : null;
    const selectedKey = selected ? corpseKey(selected) : "";
    if (selectedKey !== f6TargetKey) {
        f6TargetKey = selectedKey;
        f6PressCount = 0;
    }
    // Só a marcação que o jogador alcançou entra na sequência de três F6.
    // Longe dela, o F6 continua sendo uma busca manual comum.
    const nearTarget = selected && player &&
        distanceXZ(player, selected) <= 25.0;
    if (nearTarget) {
        if (f6PressCount >= 2) {
            const key = selectedKey;
            const registryId = normalizeRegistryId(selected.recovered_registry_id);
            const dna = scopedStrongCorpseDna(selected);
            skippedCorpseKeys.add(key);
            if (registryId !== "") rejectedCatalogRegistryIds.add(registryId);
            if (dna !== "") skippedStrongDnas.add(dna);
            pending.splice(index, 1);
            f6TargetKey = "";
            f6PressCount = 0;
            sendLabEvent("f6_current_target_skipped", {
                target:labSanitizeValue(selected, 0),
                key:key, registry_id:registryId, dna:dna,
                remaining:labPendingSnapshot()
            });
            log("⏭️ F6: somente esta marcação foi ignorada; " +
                "procurando o próximo cadáver. Restantes=" + pending.length + ".");
            emitRuntimeState("F6 ignorou apenas o alvo atual");
            updateHudState();
            updateHudWarningForCount();
            // A limpeza só toca o ponto criado pelo Turbo Hunter.
            if (markerOwned) clearOwn("F6 ignorou o alvo atual", true);
            else { currentKey = ""; switchingKey = ""; }
            if (pending.length) scheduleNearestUpdate(80, "F6: próximo cadáver");
            return {ok:true, skipped:true, pending:pending.length};
        }
        f6PressCount++;
    }
    waypointRecoverySerial++;
    for (const key of Object.keys(waypointInactiveRetries))
        delete waypointInactiveRetries[key];
    waypointClearInstructionIssued = false;
    hudGpsActionRequired = false;
    cancelWaypointWork();
    // Mantenha a marcação visível durante a análise; evita trocar o alvo.
    bulkDeadBodyScanAttempt = 0;
    bulkDeadBodyScanFinished = false;
    // Uma busca manual passa a ser a nova verdade do catálogo. Não deixe uma
    // coleta antiga, ainda na janela de associação, afetar o resultado dela.
    labRecentRemovedRegistryEntries = [];
    labMostRecentConfirmedHarvest = null;
    labDeferredStableHarvests = [];
    // F6 é a recuperação manual: a leitura substitui a fila antiga somente
    // quando dois retratos confirmam os IDs reais. Enquanto isso, mostre o
    // estado de busca; nunca remova pelo corpo espacialmente mais próximo.
    const result = scheduleBulkDeadBodyScan(0, true, "F6");
    if (result.ok) {
        setDeathSignalPhase(DEATH_SIGNAL_PHASE_ANALYZING,
            "F6: conferindo IDs e coletas");
        sendLabEvent("f6_target_analysis_started", {
            target_key:selectedKey, press:nearTarget ? f6PressCount : 0,
            distance_m:nearTarget ? distanceXZ(player, selected) : null
        });
    }
    return result;
}

function deathSignalPhaseName(phase) {
    return ({
        0:"ABATES",
        1:"ATAQUE UM ANIMAL",
        2:"NÃO COLETE",
        3:"ANALISANDO",
        4:"PODE COLETAR",
        5:"VEJA UMA PISTA",
        6:"ERRO ENCONTRADO - NÃO COLETE",
        7:"HORA DE PARAR",
        8:"ENVIAR ZIP LOG"
    })[Number(phase)] || "ABATES";
}

function setDeathSignalPhase(phase, reason) {
    const next = Math.max(
        DEATH_SIGNAL_PHASE_NORMAL,
        Math.min(DEATH_SIGNAL_PHASE_SEND_ZIP, Number(phase || 0))
    );
    if (deathSignalPhase === next)
        return;

    deathSignalPhase = next;
    updateHudState();
    const name = deathSignalPhaseName(next);
    // ABATES é o estado silencioso do jogo; não transforme uma coleta ou
    // atualização normal do catálogo em uma nova instrução para o jogador.
    if (next !== DEATH_SIGNAL_PHASE_NORMAL)
        log("🟠 LAB: " + name + (reason ? " | " + reason : ""));
    sendLabEvent("death_signal_phase", {
        phase:next,
        name:name,
        reason:String(reason || ""),
        target:labSanitizeValue(deathSignalTarget, 0)
    });
}

function deathSignalReset(reason) {
    deathSignalPhase = DEATH_SIGNAL_PHASE_NORMAL;
    labRecoveryStage = 0;
    deathSignalBaselineRunning = false;
    deathSignalBaselineFinished = false;
    deathSignalBaselineEntries = [];
    deathSignalTarget = null;
    deathSignalTargetBaseline = null;
    deathSignalSnapshots = [];
    deathSignalDeathCaptureRunning = false;
    deathSignalHarvestObserved = false;
    deathSignalClueRequested = false;
    deathSignalRecordCorpse = null;
    deathSignalAnalysisAttempt = 0;
    deathSignalBaselineRecordCorpse = null;
    deathSignalBaselineAttempt = 0;
    bulkDeadBodyScanSerial++;
    bulkDeadBodyScanRunning = false;
    bulkDeadBodyScanFinished = false;
    bulkDeadBodyScanAttempt = 0;
    bulkDeadBodyScanTimer = clearTimeoutSafe(bulkDeadBodyScanTimer);
    updateHudState();
    sendLabEvent("death_signal_reset", {
        reason:String(reason || "nova sessão controlada"),
        reserve:activeReserve
    });
}

function deathSignalPrepare(reason) {
    if (BULK_STARTUP_RECOVERY) {
        scheduleBulkDeadBodyScan(500, false, reason || "automático");
        return;
    }
    if (scriptStopping || multiplayerBlocked || !soloConfirmed ||
        activeReserve === "" || !initialOldCorpseScanFinished ||
        deathSignalTarget || deathSignalHarvestObserved ||
        deathSignalPhase === DEATH_SIGNAL_PHASE_SEND_ZIP)
        return;

    if (deathSignalBaselineRunning)
        return;

    if (deathSignalBaselineFinished) {
        setDeathSignalPhase(
            DEATH_SIGNAL_PHASE_ATTACK,
            reason || "laboratório pronto para acompanhar um animal"
        );
        return;
    }

    deathSignalBaselineRunning = true;
    deathSignalBaselineAttempt++;
    setDeathSignalPhase(
        DEATH_SIGNAL_PHASE_ANALYZING,
        "fotografando os animais carregados antes do disparo"
    );
    startDeathSignalBaselineScan(function (entries, metadata) {
        deathSignalBaselineRunning = false;
        deathSignalBaselineEntries = Array.isArray(entries)
            ? entries : [];
        if (deathSignalBaselineEntries.length <= 0 &&
            deathSignalBaselineAttempt < 3 && !scriptStopping) {
            const retry = setTimeout(function () {
                deathSignalPrepare("nova tentativa da fotografia inicial");
            }, 1500);
            labBurstTimers.push(retry);
            return;
        }

        deathSignalBaselineFinished =
            deathSignalBaselineEntries.length > 0;
        sendLabEvent("death_signal_baseline_ready", {
            attempt:deathSignalBaselineAttempt,
            entry_count:deathSignalBaselineEntries.length,
            metadata:labSanitizeValue(metadata || {}, 0),
            counter_changed:false
        });
        if (!deathSignalBaselineFinished) {
            deathSignalFail(
                "não encontrei o catálogo inicial; não abata nem colete"
            );
            return;
        }
        setDeathSignalPhase(
            DEATH_SIGNAL_PHASE_ATTACK,
            deathSignalBaselineEntries.length +
            " registro(s) fotografado(s); derrube somente um animal"
        );
    });
}

function deathSignalEntryMatchesTarget(entry, target) {
    if (!entry || !target)
        return false;

    const dnaTarget = entry.dna_target || null;
    if (dnaTarget && String(dnaTarget.target_id || "") !== "" &&
        String(dnaTarget.target_id || "") ===
            String(target.target_id || ""))
        return true;

    const entryWeight = finiteNumber(entry.stable_weight);
    if (entryWeight === null || !sameWeight(entryWeight, target.weight))
        return false;

    const ex = finiteNumber(entry.stable_record_x) !== null
        ? finiteNumber(entry.stable_record_x) : finiteNumber(entry.x);
    const ey = finiteNumber(entry.stable_record_y) !== null
        ? finiteNumber(entry.stable_record_y) : finiteNumber(entry.y);
    const ez = finiteNumber(entry.stable_record_z) !== null
        ? finiteNumber(entry.stable_record_z) : finiteNumber(entry.z);
    const tx = finiteNumber(target.x);
    const ty = finiteNumber(target.y);
    const tz = finiteNumber(target.z);

    if (ex === null || ey === null || ez === null ||
        tx === null || ty === null || tz === null)
        return false;

    return Math.abs(ex - tx) <= STABLE_RECORD_COORD_TOLERANCE_XZ &&
        Math.abs(ez - tz) <= STABLE_RECORD_COORD_TOLERANCE_XZ &&
        Math.abs(ey - ty) <= STABLE_RECORD_COORD_TOLERANCE_Y;
}

function deathSignalRecordFromEntry(entry) {
    if (!entry)
        return null;

    const alternative = entry.recovered_from_alternative_layout === true;
    return {
        recovered_from_alternative_layout:alternative,
        recovered_registry_id:normalizeRegistryId(entry.registry_id),
        recovered_stable_record_address:alternative
            ? "" : String(entry.stable_record_address || ""),
        recovered_alternative_record_address:alternative
            ? String(entry.alternative_record_address || "") : "",
        recovered_availability:Number(entry.stable_availability),
        recovered_lifecycle:Number(entry.stable_lifecycle),
        weight:finiteNumber(entry.stable_weight),
        x:finiteNumber(entry.stable_record_x) !== null
            ? finiteNumber(entry.stable_record_x) : finiteNumber(entry.x),
        y:finiteNumber(entry.stable_record_y) !== null
            ? finiteNumber(entry.stable_record_y) : finiteNumber(entry.y),
        z:finiteNumber(entry.stable_record_z) !== null
            ? finiteNumber(entry.stable_record_z) : finiteNumber(entry.z)
    };
}

function deathSignalHexDiff(beforeHex, afterHex) {
    const before = String(beforeHex || "").replace(/[^0-9a-f]/gi, "");
    const after = String(afterHex || "").replace(/[^0-9a-f]/gi, "");
    const bytes = Math.min(
        Math.floor(before.length / 2),
        Math.floor(after.length / 2)
    );
    const changed = [];

    for (let i=0; i<bytes; i++) {
        const left = before.slice(i * 2, i * 2 + 2).toLowerCase();
        const right = after.slice(i * 2, i * 2 + 2).toLowerCase();
        if (left !== right) {
            changed.push({
                offset:"0x" + i.toString(16).toUpperCase().padStart(3, "0"),
                before:left,
                after:right
            });
        }
    }

    return {
        compared_bytes:bytes,
        changed_count:changed.length,
        changed_offsets:changed
    };
}

function deathSignalCapture(label) {
    if (!deathSignalTarget)
        return null;

    let record = null;
    if (deathSignalRecordCorpse)
        record = collectionDebugRecordSnapshot(
            deathSignalRecordCorpse,
            label
        );
    let baselineRecordNow = null;
    if (deathSignalBaselineRecordCorpse)
        baselineRecordNow = collectionDebugRecordSnapshot(
            deathSignalBaselineRecordCorpse,
            "endereco_vivo_" + String(label || "captura")
        );

    const target = findForensicTarget(deathSignalTarget) ||
        deathSignalTarget;
    const candidateResult = snapshotKnownForensicCandidates(
        target,
        "sinal_morte_" + String(label || "captura")
    );
    const baselineRaw = deathSignalTargetBaseline &&
        deathSignalTargetBaseline.record
            ? String(deathSignalTargetBaseline.record.raw_hex || "") : "";
    const currentRaw = record ? String(record.raw_hex || "") : "";
    const capture = {
        label:String(label || "captura"),
        captured_at:Date.now(),
        phase:deathSignalPhase,
        target:labSanitizeValue(deathSignalTarget, 0),
        record:labSanitizeValue(record, 0),
        live_catalog_record_now:labSanitizeValue(baselineRecordNow, 0),
        diff_from_first_record:deathSignalHexDiff(
            baselineRaw,
            currentRaw
        ),
        live_address_diff_from_before_death:deathSignalHexDiff(
            baselineRaw,
            baselineRecordNow ? baselineRecordNow.raw_hex : ""
        ),
        forensic_candidate_snapshots:Number(
            candidateResult.snapshots || 0
        ),
        pending:labPendingSnapshot(),
        player:getPlayerPos()
    };

    if (!deathSignalTargetBaseline && record && record.readable === true)
        deathSignalTargetBaseline = {label:capture.label, record:record};

    deathSignalSnapshots.push(capture);
    sendLabEvent("death_signal_snapshot", capture);
    return capture;
}

function deathSignalScheduleRecordBurst(prefix, delays, done) {
    const list = Array.isArray(delays) ? delays : [];

    for (let i=0; i<list.length; i++) {
        const delay = Number(list[i] || 0);
        const timer = setTimeout(function () {
            deathSignalCapture(String(prefix || "captura") + "+" +
                delay + "ms");
            if (i === list.length - 1 && typeof done === "function")
                done();
        }, Math.max(0, delay));
        labBurstTimers.push(timer);
    }
}

function deathSignalFail(reason) {
    deathSignalDeathCaptureRunning = false;
    setDeathSignalPhase(
        DEATH_SIGNAL_PHASE_ERROR,
        reason || "não foi possível isolar um registro único"
    );
    sendLabEvent("death_signal_error", {
        reason:String(reason || "erro desconhecido"),
        target:labSanitizeValue(deathSignalTarget, 0),
        snapshots:labSanitizeValue(deathSignalSnapshots, 0)
    });
}

function deathSignalFinishAnalysis(entries, reason, afterClue) {
    const target = findForensicTarget(deathSignalTarget) ||
        deathSignalTarget;
    const matches = (Array.isArray(entries) ? entries : []).filter(
        function (entry) {
            return deathSignalEntryMatchesTarget(entry, target);
        }
    );

    sendLabEvent("death_signal_direct_analysis", {
        reason:String(reason || "análise automática"),
        after_clue:afterClue === true,
        match_count:matches.length,
        matches:labSanitizeValue(matches, 0),
        target:labSanitizeValue(target, 0)
    });

    if (matches.length === 1) {
        deathSignalRecordCorpse = deathSignalRecordFromEntry(matches[0]);
        deathSignalCapture("morte_registro_isolado");
        deathSignalScheduleRecordBurst(
            "depois_morte",
            DEATH_SIGNAL_AFTER_DEATH_DELAYS,
            function () {
                deathSignalDeathCaptureRunning = false;
                setDeathSignalPhase(
                    DEATH_SIGNAL_PHASE_CAN_COLLECT,
                    "registro único preservado; colete somente este animal"
                );
            }
        );
        return;
    }

    if (matches.length > 1) {
        deathSignalFail(
            "mais de um registro corresponde ao mesmo DNA; não colete"
        );
        return;
    }

    const started = startForensicMemoryScan(
        "sinal_morte_sem_registro_direto",
        target,
        function (result) {
            const saved = result && Array.isArray(result.saved_candidates)
                ? result.saved_candidates : [];
            deathSignalDeathCaptureRunning = false;
            if (saved.length > 0) {
                deathSignalCapture("morte_candidatos_forenses");
                setDeathSignalPhase(
                    DEATH_SIGNAL_PHASE_CAN_COLLECT,
                    saved.length +
                    " candidato(s) preservado(s) para comparar na coleta"
                );
            } else if (!afterClue && !deathSignalClueRequested) {
                deathSignalClueRequested = true;
                setDeathSignalPhase(
                    DEATH_SIGNAL_PHASE_SEE_CLUE,
                    "examine uma pista deste animal e aguarde"
                );
            } else {
                deathSignalFail(
                    "nem o DNA direto nem a pista isolaram o registro"
                );
            }
        }
    );

    if (!started.ok) {
        deathSignalDeathCaptureRunning = false;
        if (!afterClue && !deathSignalClueRequested) {
            deathSignalClueRequested = true;
            setDeathSignalPhase(
                DEATH_SIGNAL_PHASE_SEE_CLUE,
                "a varredura não iniciou; examine uma pista"
            );
        } else {
            deathSignalFail(
                "varredura de memória não iniciou: " +
                String(started.reason || "motivo desconhecido")
            );
        }
    }
}

function deathSignalAnalyze(reason, afterClue) {
    if (!deathSignalTarget || scriptStopping ||
        deathSignalHarvestObserved)
        return;

    if (deathSignalDeathCaptureRunning)
        return;

    if (stableAvailabilityScanRunning || forensicScanRunning) {
        const retry = setTimeout(function () {
            deathSignalAnalyze(reason, afterClue);
        }, 500);
        labBurstTimers.push(retry);
        return;
    }

    deathSignalDeathCaptureRunning = true;
    deathSignalAnalysisAttempt++;
    setDeathSignalPhase(
        DEATH_SIGNAL_PHASE_ANALYZING,
        "leitura automática " + deathSignalAnalysisAttempt
    );

    const started = startStableAvailabilityScan(
        [],
        "lab_sinal_morte_" + String(reason || "automático"),
        function (activeEntries, stableMetadata) {
            if (scriptStopping || deathSignalHarvestObserved)
                return;
            startDirectDnaStableRecovery(
                activeEntries || [],
                function (recoveredEntries, dnaMetadata) {
                    sendLabEvent("death_signal_analysis_metadata", {
                        reason:String(reason || "automático"),
                        stable:labSanitizeValue(stableMetadata || {}, 0),
                        dna:labSanitizeValue(dnaMetadata || {}, 0)
                    });
                    deathSignalFinishAnalysis(
                        recoveredEntries || [],
                        reason,
                        afterClue
                    );
                }
            );
        }
    );

    if (!started.ok) {
        deathSignalDeathCaptureRunning = false;
        const retry = setTimeout(function () {
            deathSignalAnalyze(reason, afterClue);
        }, started.reason === "stable_scan_in_progress" ? 500 : 1200);
        labBurstTimers.push(retry);
    }
}

function deathSignalOnDeath(c, path, payload) {
    if (BULK_STARTUP_RECOVERY)
        return;
    if (!c || deathSignalHarvestObserved)
        return;

    if (deathSignalTarget) {
        if (strongCorpseDna(deathSignalTarget) !== strongCorpseDna(c))
            deathSignalFail(
                "mais de um animal foi abatido durante o teste controlado"
            );
        return;
    }

    deathSignalTarget = registerForensicTarget(
        c,
        "laboratorio_sinal_morte"
    ) || c;
    if (!deathSignalBaselineFinished ||
        deathSignalPhase !== DEATH_SIGNAL_PHASE_ATTACK) {
        deathSignalFail(
            "o animal foi abatido antes de aparecer ATAQUE 1 ANIMAL"
        );
        return;
    }

    const baselineMatches = deathSignalBaselineEntries.filter(
        function (entry) {
            return sameWeight(entry.stable_weight, deathSignalTarget.weight);
        }
    );
    sendLabEvent("death_signal_live_catalog_match", {
        target:labSanitizeValue(deathSignalTarget, 0),
        match_count:baselineMatches.length,
        matches:labSanitizeValue(baselineMatches, 0)
    });
    if (baselineMatches.length > 1) {
        deathSignalFail(
            "o peso apareceu em mais de um registro vivo; não colete"
        );
        return;
    }
    if (baselineMatches.length === 1) {
        const liveEntry = baselineMatches[0];
        deathSignalBaselineRecordCorpse =
            deathSignalRecordFromEntry(liveEntry);
        deathSignalTargetBaseline = {
            label:"antes_morte_catalogo_vivo",
            record:{
                address:String(liveEntry.alternative_record_address || ""),
                raw_hex:String(liveEntry.stable_raw_hex || ""),
                availability:Number(liveEntry.stable_availability),
                lifecycle:Number(liveEntry.stable_lifecycle),
                weight:finiteNumber(liveEntry.stable_weight),
                x:finiteNumber(liveEntry.x),
                y:finiteNumber(liveEntry.y),
                z:finiteNumber(liveEntry.z)
            }
        };
    }
    setDeathSignalPhase(
        DEATH_SIGNAL_PHASE_DONT_COLLECT,
        "morte detectada; mantenha o corpo no chão"
    );
    sendLabEvent("death_signal_target_selected", {
        path:String(path || ""),
        target:labSanitizeValue(deathSignalTarget, 0),
        payload:labSanitizeValue(payload || {}, 0)
    });

    const timer = setTimeout(function () {
        deathSignalAnalyze("após morte", false);
    }, 1200);
    labBurstTimers.push(timer);
}

function deathSignalOnClue(c, path, payload) {
    if (BULK_STARTUP_RECOVERY)
        return;
    if (!deathSignalTarget || deathSignalHarvestObserved || !c)
        return;

    const target = findForensicTarget(deathSignalTarget) ||
        deathSignalTarget;
    if (!forensicPartialCompatible(target, c))
        return;

    sendLabEvent("death_signal_clue_confirmed", {
        path:String(path || ""),
        target:labSanitizeValue(target, 0),
        clue:labSanitizeValue(c, 0),
        payload:labSanitizeValue(payload || {}, 0)
    });
    deathSignalClueRequested = true;
    deathSignalDeathCaptureRunning = false;
    deathSignalAnalyze("pista confirmada", true);
}

function deathSignalOnHarvest(path, harvest) {
    if (BULK_STARTUP_RECOVERY)
        return;
    if (!deathSignalTarget || deathSignalHarvestObserved || !harvest)
        return;

    const expected = strongCorpseDna(deathSignalTarget);
    const observed = strongCorpseDna(harvest);
    if (expected === "" || observed === "" || expected !== observed) {
        deathSignalFail(
            "a coleta não corresponde ao DNA do animal acompanhado"
        );
        return;
    }

    deathSignalHarvestObserved = true;
    deathSignalCapture("instante_da_coleta");
    setDeathSignalPhase(
        DEATH_SIGNAL_PHASE_ANALYZING,
        "coleta confirmada; comparando os mesmos endereços"
    );
    sendLabEvent("death_signal_harvest_confirmed", {
        path:String(path || ""),
        expected_dna:expected,
        observed_dna:observed,
        harvest:labSanitizeValue(harvest, 0)
    });

    deathSignalScheduleRecordBurst(
        "depois_coleta",
        DEATH_SIGNAL_AFTER_HARVEST_DELAYS,
        function () {
            setDeathSignalPhase(
                DEATH_SIGNAL_PHASE_STOP,
                "comparação concluída; clique em PARAR"
            );
            const timer = setTimeout(function () {
                setDeathSignalPhase(
                    DEATH_SIGNAL_PHASE_SEND_ZIP,
                    "clique em PARAR e envie o pacote criado"
                );
            }, 1800);
            labBurstTimers.push(timer);
            sendLabEvent("death_signal_test_completed", {
                target:labSanitizeValue(deathSignalTarget, 0),
                snapshot_count:deathSignalSnapshots.length,
                snapshots:labSanitizeValue(deathSignalSnapshots, 0)
            });
        }
    );
}

function cancelHudWarning(resetShown) {
    hudWarningTimer = clearTimeoutSafe(hudWarningTimer);

    if (hudWarningActive) {
        hudWarningActive = false;
        updateHudState();
    }

    if (resetShown)
        hudWarningShown = false;
}

function updateHudWarningForCount() {
    // O laboratório deixa o jogador caçar quantos animais quiser. Durante
    // uma caça normal a única informação do HUD é ABATES: N.
    cancelHudWarning(true);
}

function readHudGameVisibility() {
    if (!hudAutoVisibilitySupported)
        return true;

    try {
        const foreground = GetForegroundWindowNative();

        if (!foreground || foreground.isNull())
            return false;

        hudForegroundPidMemory.writeU32(0);
        GetWindowThreadProcessIdNative(foreground, hudForegroundPidMemory);

        if (hudForegroundPidMemory.readU32() !== Process.id)
            return false;

        const cursorInfoSize = Process.pointerSize === 8 ? 24 : 20;
        hudCursorInfoMemory.writeU32(cursorInfoSize);

        if (GetCursorInfoNative(hudCursorInfoMemory) !== 0) {
            const cursorFlags = hudCursorInfoMemory.add(4).readU32();

            if ((cursorFlags & CURSOR_SHOWING) !== 0)
                return false;
        }

        return true;
    } catch (_) {
        return true;
    }
}

function pollHudGameVisibility() {
    const visible = readHudGameVisibility();

    if (visible === hudGameVisible)
        return;

    hudGameVisible = visible;
    updateHudState();

    if (visible && !hudLoggedGameVisible) {
        hudLoggedGameVisible = true;
        log("🎮 HUD automático: jogabilidade detectada.");
    } else if (!visible && soloConfirmed && !hudLoggedGameHidden) {
        hudLoggedGameHidden = true;
        log("🎮 HUD automático: menu/cursor detectado; contador oculto.");
    }
}

function initHudAutoVisibility() {
    try {
        const foregroundAddress = resolveHudExport(
            "user32.dll", "GetForegroundWindow"
        );
        const foregroundPidAddress = resolveHudExport(
            "user32.dll", "GetWindowThreadProcessId"
        );
        const cursorInfoAddress = resolveHudExport(
            "user32.dll", "GetCursorInfo"
        );

        if (!foregroundAddress || !foregroundPidAddress || !cursorInfoAddress)
            throw new Error("APIs de visibilidade do Windows não encontradas");

        GetForegroundWindowNative = new NativeFunction(
            foregroundAddress, "pointer", []
        );
        GetWindowThreadProcessIdNative = new NativeFunction(
            foregroundPidAddress, "uint", ["pointer", "pointer"]
        );
        GetCursorInfoNative = new NativeFunction(
            cursorInfoAddress, "int", ["pointer"]
        );
        hudForegroundPidMemory = Memory.alloc(4);
        hudCursorInfoMemory = Memory.alloc(Process.pointerSize === 8 ? 24 : 20);
        hudCursorInfoMemory.writeByteArray(
            new Uint8Array(Process.pointerSize === 8 ? 24 : 20).buffer
        );
        hudAutoVisibilitySupported = true;
        hudGameVisible = readHudGameVisibility();
        hudVisibilityTimer = setInterval(pollHudGameVisibility, 100);
        log("🎮 HUD automático preparado: oculta em menus e fora do jogo.");
    } catch (error) {
        hudAutoVisibilitySupported = false;
        hudGameVisible = true;
        log(
            `⚠️ HUD automático indisponível (${error}). ` +
            `F9 continua disponível para ocultar manualmente.`
        );
    }
}

function reportHudStatus() {
    if (hudGetStatusNative === null)
        return;

    let status;

    try { status = Number(hudGetStatusNative()); }
    catch (_) { return; }

    if (status === hudLastReportedStatus)
        return;

    hudLastReportedStatus = status;

    if (status === 1) {
        if (soloConfirmed) {
            log(
                `✅ HUD DIRECTX ATIVO dentro do jogo: ABATES: ${pending.length} ` +
                `| canto=${hudCornerName(hudCorner)}`
            );
        } else {
            log(
                `⏳ HUD DIRECTX ATIVO: AGUARDANDO SOLO ` +
                `| canto=${hudCornerName(hudCorner)}`
            );
        }
    } else if (status < 0) {
        log(
            `⚠️ HUD DIRECTX falhou (codigo ${status}). ` +
            `O GPS continua funcionando normalmente.`
        );
    }
}

function initHud() {
    hudInitTimer = null;

    if (scriptStopping || multiplayerBlocked || hudModule !== null)
        return;

    if (typeof CModule === "undefined") {
        log("⚠️ HUD DIRECTX indisponível: esta versão do Frida não possui CModule.");
        return;
    }

    try {
        const d3dCreate = resolveHudExport(
            "d3d11.dll", "D3D11CreateDeviceAndSwapChain"
        );
        const createWindow = resolveHudExport("user32.dll", "CreateWindowExW");
        const destroyWindow = resolveHudExport("user32.dll", "DestroyWindow");
        const systemClock = resolveHudExport("kernel32.dll", "GetTickCount64");
        let d3dCompile = resolveHudExport("d3dcompiler_47.dll", "D3DCompile");

        if (!d3dCompile)
            d3dCompile = resolveHudExport("d3dcompiler_43.dll", "D3DCompile");

        if (!d3dCreate || !createWindow || !destroyWindow ||
            !d3dCompile || !systemClock)
            throw new Error("APIs DirectX 11 necessárias não foram encontradas");

        const hudStateBytes = 256 * 1024;
        hudStateMemory = Memory.alloc(hudStateBytes);
        hudStateMemory.writeByteArray(new Uint8Array(hudStateBytes).buffer);

        hudModule = new CModule(HUD_C_SOURCE, {
            hud_state:hudStateMemory,
            D3D11CreateDeviceAndSwapChain:d3dCreate,
            CreateWindowExW:createWindow,
            DestroyWindow:destroyWindow,
            D3DCompile:d3dCompile,
            th_get_tickcount64:systemClock
        });

        const probe = new NativeFunction(
            hudModule.hud_probe_addresses,
            "void",
            ["pointer", "pointer", "pointer"]
        );
        const slots = Memory.alloc(Process.pointerSize * 3);
        const presentSlot = slots;
        const resizeSlot = slots.add(Process.pointerSize);
        const resizeTargetSlot = slots.add(Process.pointerSize * 2);
        presentSlot.writePointer(ptr(0));
        resizeSlot.writePointer(ptr(0));
        resizeTargetSlot.writePointer(ptr(0));
        probe(presentSlot, resizeSlot, resizeTargetSlot);

        const presentAddress = presentSlot.readPointer();
        const resizeAddress = resizeSlot.readPointer();
        const resizeTargetAddress = resizeTargetSlot.readPointer();

        if (presentAddress.isNull())
            throw new Error("IDXGISwapChain::Present não foi localizado");

        hudSetStateNative = new NativeFunction(
            hudModule.hud_set_state,
            "void",
            ["int", "int", "int", "int", "int", "int"]
        );
        hudGetStatusNative = new NativeFunction(
            hudModule.hud_get_status, "int", []
        );
        hudShutdownNative = new NativeFunction(
            hudModule.hud_shutdown, "void", []
        );

        hudHooks.push(Interceptor.attach(presentAddress, {
            onEnter:hudModule.hud_present_on_enter
        }));

        if (!resizeAddress.isNull()) {
            hudHooks.push(Interceptor.attach(resizeAddress, {
                onEnter:hudModule.hud_resize_on_enter
            }));
        }

        if (!resizeTargetAddress.isNull() &&
            !resizeTargetAddress.equals(resizeAddress)) {
            hudHooks.push(Interceptor.attach(resizeTargetAddress, {
                onEnter:hudModule.hud_resize_on_enter
            }));
        }

        updateHudState();
        hudStatusTimer = setInterval(reportHudStatus, 1500);
        log(
            `🎮 HUD DIRECTX preparado. Aguardando primeiro quadro do jogo ` +
            `| canto=${hudCornerName(hudCorner)}.`
        );

    } catch (error) {
        log(`⚠️ HUD DIRECTX não iniciou: ${error}. GPS preservado.`);
        shutdownHud();
    }
}

function shutdownHud() {
    if (hudInitTimer !== null) {
        try { clearTimeout(hudInitTimer); }
        catch (_) {}
        hudInitTimer = null;
    }

    if (hudStatusTimer !== null) {
        try { clearInterval(hudStatusTimer); }
        catch (_) {}
        hudStatusTimer = null;
    }

    if (hudVisibilityTimer !== null) {
        try { clearInterval(hudVisibilityTimer); }
        catch (_) {}
        hudVisibilityTimer = null;
    }

    cancelHudWarning(false);

    if (hudSetStateNative !== null) {
        try { hudSetStateNative(0, 0, hudCorner, 0, 0); }
        catch (_) {}
    }

    for (let i=hudHooks.length - 1; i>=0; i--) {
        try { hudHooks[i].detach(); }
        catch (_) {}
    }
    hudHooks = [];

    try { Interceptor.flush(); }
    catch (_) {}

    if (hudShutdownNative !== null) {
        try { hudShutdownNative(); }
        catch (_) {}
    }

    hudSetStateNative = null;
    hudGetStatusNative = null;
    hudShutdownNative = null;
    hudModule = null;
    hudStateMemory = null;
    GetForegroundWindowNative = null;
    GetWindowThreadProcessIdNative = null;
    GetCursorInfoNative = null;
    hudForegroundPidMemory = null;
    hudCursorInfoMemory = null;
    hudAutoVisibilitySupported = false;
    hudGameVisible = true;
}

function pstr(p) {
    try { return p.toString(); }
    catch (_) { return "<erro>"; }
}

function rayXPointerPattern(pointer) {
    try {
        const storage = Memory.alloc(Process.pointerSize);
        storage.writePointer(pointer);
        const hex = labHex(storage, Process.pointerSize);
        return hex.match(/.{2}/g).join(" ");
    } catch (_) {
        return "";
    }
}

function rayXDiscoverMapSingletonSlots(mapPointer, trigger, readOnly) {
    if (!RAY_X_TRACE || !mapPointer || mapPointer.isNull())
        return;

    const pointerText = pstr(mapPointer);
    if (rayXMapSlotDiscoveryRunning ||
        rayXMapSlotDiscoveryDoneForPointer === pointerText)
        return;

    const pattern = rayXPointerPattern(mapPointer);
    if (pattern === "")
        return;

    let ranges = [];
    try {
        const moduleStart = BASE;
        const moduleEnd = BASE.add(Number(Process.mainModule.size));
        ranges = Process.enumerateRanges({
            protection:"rw-",
            coalesce:false
        }).filter(function (range) {
            return range.base.compare(moduleStart) >= 0 &&
                range.base.compare(moduleEnd) < 0;
        });
    } catch (error) {
        sendLabEvent("ray_x_map_slot_scan_failed", {
            stage:"enumerate_ranges",
            trigger:String(trigger || ""),
            map_pointer:pointerText,
            error:String(error)
        });
        return;
    }

    rayXMapSlotDiscoveryRunning = true;
    const startedAt = Date.now();
    const hits = [];
    let rangeIndex = 0;

    sendLabEvent("ray_x_map_slot_scan_started", {
        trigger:String(trigger || ""),
        map_pointer:pointerText,
        pattern:pattern,
        range_count:ranges.length,
        reference_slot:pstr(MAP_SLOT),
        reference_rva:"0x" + RVA_MAP_SINGLETON.toString(16)
    });

    function finish() {
        rayXMapSlotDiscoveryRunning = false;
        rayXMapSlotDiscoveryDoneForPointer = pointerText;

        if (hits.length === 1 && readOnly !== true) {
            try {
                activeMapSlot = ptr(hits[0].address);
                autoMapVerifiedPointer = pointerText;
                log(
                    "🧪 MAPA: referência única validada em " +
                    hits[0].rva + "."
                );
                scheduleNearestUpdate(
                    80,
                    "slot do mapa descoberto pelo raio-X"
                );
            } catch (_) {}
        } else {
            log(
                "🧪 RAIO-X MAPA: " + hits.length +
                " possível(is) referência(s) registrada(s) no ZIP."
            );
        }

        sendLabEvent("ray_x_map_slot_scan_finished", {
            trigger:String(trigger || ""),
            map_pointer:pointerText,
            elapsed_ms:Date.now() - startedAt,
            hit_count:hits.length,
            unique_slot_selected:hits.length === 1 && readOnly !== true,
            read_only:readOnly === true,
            selected_slot:hits.length === 1 ? hits[0] : null,
            hits:hits
        });
    }

    function scanNextRange() {
        if (scriptStopping || rangeIndex >= ranges.length) {
            finish();
            return;
        }

        const range = ranges[rangeIndex++];
        try {
            Memory.scan(range.base, Number(range.size), pattern, {
                onMatch(address, size) {
                    let rva = "";
                    try {
                        rva = address.sub(BASE).toString();
                        if (rva.indexOf("0x") !== 0)
                            rva = "0x" + rva;
                    } catch (_) {}
                    hits.push({
                        address:pstr(address),
                        rva:rva,
                        match_size:Number(size || Process.pointerSize),
                        protection:String(range.protection || ""),
                        surrounding_hex:labHex(address.sub(32), 80)
                    });
                },
                onError(error) {
                    sendLabEvent("ray_x_map_slot_scan_range_error", {
                        range_base:pstr(range.base),
                        range_size:Number(range.size),
                        error:String(error)
                    });
                },
                onComplete() {
                    setTimeout(scanNextRange, 0);
                }
            });
        } catch (error) {
            sendLabEvent("ray_x_map_slot_scan_range_error", {
                range_base:pstr(range.base),
                range_size:Number(range.size),
                error:String(error)
            });
            setTimeout(scanNextRange, 0);
        }
    }

    scanNextRange();
}

function rayXMapStatus(reason, resolved, singleton) {
    if (!RAY_X_TRACE)
        return;

    const now = Date.now();
    const key = [
        pstr(activeMapSlot),
        pstr(singleton),
        pstr(capturedMap),
        pstr(resolved)
    ].join("|");
    if (key === rayXLastMapStatusKey &&
        now - rayXLastMapStatusAt < RAY_X_STATUS_INTERVAL_MS)
        return;

    rayXLastMapStatusKey = key;
    rayXLastMapStatusAt = now;
    sendLabEvent("ray_x_map_status", {
        reason:String(reason || "getMap"),
        reference_slot:pstr(MAP_SLOT),
        active_slot:pstr(activeMapSlot),
        singleton_pointer:pstr(singleton),
        captured_map_pointer:pstr(capturedMap),
        resolved_map_pointer:pstr(resolved),
        source:!singleton.isNull() ? "slot_global_validado" : "indisponivel"
    });
}

function getSingletonMap() {
    if (autoMapSlotAddressReady) {
        const candidate = checkedAutoMapFromSlot(AUTO_MAP_SLOT);
        if (!candidate.isNull())
            return candidate;
    }
    if (activeMapSlot.compare(AUTO_MAP_SLOT) !== 0 &&
        mapSingletonAddressReady) {
        const candidate = checkedAutoMapFromSlot(activeMapSlot);
        if (!candidate.isNull())
            return candidate;
    }
    return ptr(0);
}

function getMap() {
    const singleton = getSingletonMap();
    // O ponteiro capturado no clique anterior pode envelhecer em teleporte.
    const resolved = singleton;
    rayXMapStatus("getMap", resolved, singleton);
    return resolved;
}

function checkedAutoMapFromSlot(slot) {
    try {
        const slotRange = Process.findRangeByAddress(slot);
        if (!slotRange || String(slotRange.protection).indexOf("w") < 0 ||
            slot.compare(BASE) < 0 ||
            slot.compare(BASE.add(Number(Process.mainModule.size))) >= 0)
            return ptr(0);
        const map = slot.readPointer();
        if (map.isNull())
            return ptr(0);
        const range = Process.findRangeByAddress(map);
        if (!range || String(range.protection).indexOf("w") < 0 ||
            map.add(0x3d8).compare(range.base.add(Number(range.size))) > 0)
            return ptr(0);
        const kind = map.add(0x3d0).readU16();
        const state = map.add(0x3d3).readU8();
        const id = normalizeRegistryId(map.add(0x3a0).readU64().toString());
        if (kind !== 0x43 && kind !== 0x20a)
            return ptr(0);
        if (state > 1 || (id !== "" &&
            id !== MANUAL_WAYPOINT_REGISTRY_ID))
            return ptr(0);
        return map;
    } catch (_) {
        return ptr(0);
    }
}

function logAutoWaypointPreflight(reason, force) {
    if (scriptStopping)
        return;
    let slotPointer = ptr(0);
    try {
        if (autoMapSlotAddressReady)
            slotPointer = AUTO_MAP_SLOT.readPointer();
    } catch (_) {}
    const map = getSingletonMap();
    const state = [pstr(slotPointer), pstr(map),
        autoWaypointSetter !== null, autoMapVerifiedPointer].join("|");
    const now = Date.now();
    if (!force && autoWaypointLastPreflight === state &&
        now - autoWaypointLastPreflightAt < 15000)
        return;
    autoWaypointLastPreflight = state;
    autoWaypointLastPreflightAt = now;
    sendLabEvent("waypoint_auto_preflight", {
        reason:String(reason || "verificação do mapa"),
        reference_rva:"0x" + RVA_AUTO_MAP_SLOT.toString(16),
        slot_bytes_hex:labHex(AUTO_MAP_SLOT, 16),
        raw_map_pointer:pstr(slotPointer),
        validated_map_pointer:pstr(map),
        map_type_hex:slotPointer.isNull() ? "" :
            labHex(slotPointer.add(0x3d0), 8),
        map_id_hex:slotPointer.isNull() ? "" :
            labHex(slotPointer.add(0x3a0), 8),
        unique_reference_validated:autoMapVerifiedPointer === pstr(map) &&
            !map.isNull(),
        setter_signature_validated:autoWaypointSetter !== null,
        pending:labPendingSnapshot()
    });
}

function safeUtf16(p) {
    if (!p || p.isNull()) return "";
    try { return p.readUtf16String() || ""; }
    catch (_) { return ""; }
}

function safeBytes(p, len) {
    if (!p || p.isNull() || len <= 0) return null;
    try { return p.readByteArray(len); }
    catch (_) { return null; }
}

function bytesToText(buf) {
    if (buf === null) return "";

    try {
        const a = new Uint8Array(buf);
        let s = "";
        const max = Math.min(a.length, 524288);

        for (let i=0; i<max; i++) {
            const c = a[i];
            s += ((c >= 32 && c <= 126) || c === 9 || c === 10 || c === 13)
                ? String.fromCharCode(c)
                : ".";
        }

        return s;
    } catch (_) {
        return "";
    }
}

function parseJson(text) {
    if (!text) return null;

    const a = text.indexOf("{");
    const b = text.lastIndexOf("}");

    if (a < 0 || b <= a)
        return null;

    const jsonText = text.slice(a, b + 1);

    try {
        const object = JSON.parse(jsonText);
        const exactTag = /"special_tag"\s*:\s*(?:"(-?\d+)"|(-?\d+))/.exec(jsonText);

        if (exactTag)
            object.special_tag = exactTag[1] !== undefined ? exactTag[1] : exactTag[2];

        return object;
    } catch (_) {
        return null;
    }
}

function corpseKey(o) {
    const registryId = normalizeRegistryId(o.recovered_registry_id);

    if (o.recovered_from_startup === true && registryId !== "")
        return `registry|${o.reserve ?? ""}|${registryId}`;

    const animalId = String(o.recovered_animal_id ?? "").trim();

    if (animalId !== "" && animalId !== "0")
        return `animal|${o.reserve ?? ""}|${animalId}`;

    const dna = strongCorpseDna(o);

    if (dna !== "")
        return `dna|${o.reserve ?? ""}|${dna}`;

    if (registryId !== "")
        return `registry|${o.reserve ?? ""}|${registryId}`;

    return [
        o.reserve ?? "",
        o.species ?? "",
        Number(o.x ?? 0).toFixed(3),
        Number(o.y ?? 0).toFixed(3),
        Number(o.z ?? 0).toFixed(3),
        String(o.special_tag ?? "")
    ].join("|");
}

function hasValue(value) {
    return value !== undefined && value !== null && value !== "";
}

function finiteNumber(value) {
    if (!hasValue(value))
        return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
}

function sameValue(a, b) {
    return hasValue(a) && hasValue(b) && String(a) === String(b);
}

function sameSpecies(a, b) {
    return sameValue(a, b);
}

function sameWeight(a, b) {
    const left = finiteNumber(a);
    const right = finiteNumber(b);

    return left !== null && right !== null && left > 0 && right > 0 &&
        Math.abs(left - right) <= WEIGHT_TOLERANCE;
}

function samePartialDnaIdentity(a, b) {
    if (!a || !b || !sameSpecies(a.species, b.species) ||
        !sameWeight(a.weight, b.weight) ||
        !sameValue(a.gender, b.gender))
        return false;

    const leftReserve = normalizeReserve(a.reserve);
    const rightReserve = normalizeReserve(b.reserve);
    if (leftReserve !== "" && rightReserve !== "" &&
        leftReserve !== rightReserve)
        return false;

    if (hasValue(a.difficulty) && hasValue(b.difficulty) &&
        !sameValue(a.difficulty, b.difficulty))
        return false;

    return true;
}

function usefulTag(value) {
    const tag = String(value ?? "");
    return tag !== "" && tag !== "0" ? tag : "";
}

function corpseDna(c) {
    return [
        c.species ?? "",
        finiteNumber(c.weight) !== null ? Number(c.weight).toFixed(6) : "",
        c.gender ?? "",
        c.difficulty ?? ""
    ].join("|");
}

function strongCorpseDna(c) {
    if (!c || !hasValue(c.species) || !hasValue(c.gender) ||
        !hasValue(c.difficulty))
        return "";

    const weight = finiteNumber(c.weight);

    if (weight === null || weight <= 0)
        return "";

    return corpseDna(c);
}

function scopedStrongCorpseDna(c) {
    const dna = strongCorpseDna(c);

    if (dna === "")
        return "";

    const payloadReserve = normalizeReserve(c ? c.reserve : "");
    const reserve = payloadReserve !== "" ? payloadReserve : activeReserve;
    return `${reserve}|${dna}`;
}

function findHarvestedDnaByPartialFields(c) {
    if (!c || !hasValue(c.species) || !hasValue(c.gender))
        return "";

    const weight = finiteNumber(c.weight);

    if (weight === null || weight <= 0)
        return "";

    const payloadReserve = normalizeReserve(c.reserve);
    const reserve = payloadReserve !== "" ? payloadReserve : activeReserve;
    const prefix = [
        reserve,
        c.species,
        weight.toFixed(6),
        c.gender,
        ""
    ].join("|");

    for (const key of harvestedDnas) {
        if (String(key).indexOf(prefix) === 0)
            return String(key);
    }

    return "";
}

function rememberHarvestedDna(c, reason) {
    const key = scopedStrongCorpseDna(c);

    if (key === "" || harvestedDnas.has(key))
        return false;

    harvestedDnas.add(key);
    sendLabEvent("harvest_dna_committed", {
        game_identity:GAME_IDENTITY,
        harvest_key:key,
        dna:strongCorpseDna(c),
        reserve:normalizeReserve(c ? c.reserve : "") || activeReserve,
        reason:reason || "coleta confirmada",
        harvested_dna_count:harvestedDnas.size
    });
    return true;
}

function chooseCanonicalDnaIndex(indices) {
    if (!indices.length)
        return -1;

    // A posição registrada diretamente no evento de morte é a referência
    // principal. Registros recuperados por memória ou pista são apenas aliases.
    for (let i=0; i<indices.length; i++) {
        const corpse = pending[indices[i]];

        if (corpse.recovered_from_startup !== true &&
            corpse.recovered_from_clue !== true)
            return indices[i];
    }

    for (let i=0; i<indices.length; i++) {
        if (pending[indices[i]].recovered_from_clue === true)
            return indices[i];
    }

    return indices[0];
}

function sameStrongCorpseDna(a, b) {
    const left = strongCorpseDna(a);
    const right = strongCorpseDna(b);

    return left !== "" && left === right;
}

function rememberRegistryId(corpse, registryId) {
    const normalized = normalizeRegistryId(registryId);

    if (!corpse || normalized === "")
        return;

    if (!Array.isArray(corpse.recovered_registry_id_history))
        corpse.recovered_registry_id_history = [];

    if (corpse.recovered_registry_id_history.indexOf(normalized) < 0)
        corpse.recovered_registry_id_history.push(normalized);
}

function applyStableCandidateToCorpse(corpse, candidate) {
    if (!corpse || !candidate)
        return;

    const registryId = normalizeRegistryId(candidate.registry_id);
    if (registryId !== "") {
        corpse.recovered_registry_id = registryId;
        rememberRegistryId(corpse, registryId);
    }

    corpse.recovered_registry_descriptor = String(
        candidate.descriptor || corpse.recovered_registry_descriptor || ""
    );
    corpse.recovered_object_link = String(
        candidate.object_link || corpse.recovered_object_link || ""
    );
    corpse.recovered_stable_record_address = String(
        candidate.stable_record_address || ""
    );
    corpse.recovered_stable_range_base = String(
        candidate.stable_record_range_base || ""
    );
    corpse.recovered_availability = finiteNumber(
        candidate.stable_availability
    );
    corpse.recovered_lifecycle = finiteNumber(candidate.stable_lifecycle);
    corpse.recovered_live_pointer_count = finiteNumber(
        candidate.stable_nonzero_pointers
    );
    corpse.recovered_readable_pointer_count = finiteNumber(
        candidate.stable_readable_pointers
    );

    const stableWeight = finiteNumber(candidate.stable_weight);
    if (stableWeight !== null && stableWeight > 0 &&
        finiteNumber(corpse.weight) === null)
        corpse.weight = stableWeight;

    const stableX = finiteNumber(candidate.stable_record_x);
    const stableY = finiteNumber(candidate.stable_record_y);
    const stableZ = finiteNumber(candidate.stable_record_z);
    const preserveDeathPosition =
        candidate.recovered_directly_by_dna === true &&
        strongCorpseDna(corpse) !== "";
    if (!preserveDeathPosition && candidate.stable_available === true &&
        stableX !== null && stableY !== null && stableZ !== null &&
        validCoord(stableX) && validCoord(stableY) && validCoord(stableZ)) {
        corpse.x = stableX;
        corpse.y = stableY;
        corpse.z = stableZ;
    }
}

function removeStrongDnaAliases(primary) {
    const removedDna = strongCorpseDna(primary);
    const aliases = [];

    if (removedDna === "")
        return aliases;

    for (let i=pending.length - 1; i>=0; i--) {
        const candidate = pending[i];

        if (String(candidate.reserve ?? "") !==
                String(primary.reserve ?? "") ||
            explicitAnimalIdsConflict(candidate, primary) ||
            !sameStrongCorpseDna(candidate, primary))
            continue;

        const alias = pending.splice(i, 1)[0];
        const aliasRegistryId = normalizeRegistryId(
            alias.recovered_registry_id
        );

        if (aliasRegistryId !== "")
            knownOldCorpseIds.add(aliasRegistryId);

        if (Array.isArray(alias.recovered_registry_id_history)) {
            for (let j=0; j<alias.recovered_registry_id_history.length; j++) {
                const historicalId = normalizeRegistryId(
                    alias.recovered_registry_id_history[j]
                );
                if (historicalId !== "")
                    knownOldCorpseIds.add(historicalId);
            }
        }

        aliases.push(alias);
    }

    // A pista não informa a dificuldade, mas fornece espécie, peso float exato
    // e sexo. Se uma única entrada incompleta do catálogo já foi enriquecida
    // por essa pista, ela é apenas um alias do DNA completo coletado.
    const partialAliases = [];
    for (let i=0; i<pending.length; i++) {
        const candidate = pending[i];
        if (candidate.recovered_from_clue !== true ||
            strongCorpseDna(candidate) !== "" ||
            explicitAnimalIdsConflict(candidate, primary) ||
            finiteNumber(candidate.weight) !== finiteNumber(primary.weight) ||
            !samePartialDnaIdentity(candidate, primary))
            continue;
        partialAliases.push(i);
    }

    if (partialAliases.length === 1) {
        const alias = pending.splice(partialAliases[0], 1)[0];
        const aliasRegistryId = normalizeRegistryId(
            alias.recovered_registry_id
        );
        if (aliasRegistryId !== "")
            knownOldCorpseIds.add(aliasRegistryId);
        if (Array.isArray(alias.recovered_registry_id_history)) {
            for (let i=0; i<alias.recovered_registry_id_history.length; i++) {
                const historicalId = normalizeRegistryId(
                    alias.recovered_registry_id_history[i]
                );
                if (historicalId !== "")
                    knownOldCorpseIds.add(historicalId);
            }
        }
        aliases.push(alias);
    }

    return aliases;
}

function normalizeRegistryId(value) {
    const text = String(value ?? "").trim();
    return text !== "" && text !== "0" ? text : "";
}

function corpseDesc(c) {
    return (
        `species=${c.species} ` +
        `weight=${finiteNumber(c.weight) !== null ? Number(c.weight).toFixed(3) : "?"} ` +
        `gender=${c.gender ?? "?"} ` +
        `x=${Number(c.x).toFixed(2)} ` +
        `y=${Number(c.y).toFixed(2)} ` +
        `z=${Number(c.z).toFixed(2)}`
    );
}

function clearTimeoutSafe(timer) {
    if (timer !== null) {
        try { clearTimeout(timer); }
        catch (_) {}
    }
    return null;
}

function cancelWaypointWork() {
    switchTimer = clearTimeoutSafe(switchTimer);
    externalReapplyTimer = clearTimeoutSafe(externalReapplyTimer);
    nearestUpdateTimer = clearTimeoutSafe(nearestUpdateTimer);
    staleWaypointCleanupTimer = clearTimeoutSafe(staleWaypointCleanupTimer);
    switchingKey = "";
    switchSerial++;
}

function cancelReserveCandidate() {
    reserveCandidateTimer = clearTimeoutSafe(reserveCandidateTimer);
    reserveCandidate = "";
}

function resetMarkerState() {
    currentKey = "";
    currentIndex = -1;
    switchingKey = "";
    markerOwned = false;
}

function beginInternalHookWindow() {
    internalHookIgnoreUntil = Math.max(
        internalHookIgnoreUntil,
        Date.now() + INTERNAL_HOOK_COOLDOWN_MS
    );
}

function buildNumberFromVersion(value) {
    const text = String(value || "");
    const match = /(?:final|build)[_\- ]?(\d{6,})/i.exec(text);
    return match ? match[1] : "";
}

function warnUpdatedBuild(detectedBuild, fullVersion) {
    detectedGameBuild = detectedBuild || "desconhecida";

    if (gameBuildWarningSent || scriptStopping)
        return;

    gameBuildWarningSent = true;
    const reason =
        `JOGO ATUALIZADO: build detectada ${detectedGameBuild}; ` +
        `build de referência ${TARGET_GAME_BUILD}` +
        (fullVersion ? ` (${fullVersion})` : "") + ".";
    log("⚠️ " + reason);
    log("🧩 MODO COMPATÍVEL: o Turbo Hunter continuará funcionando e registrará no ZIP qualquer recurso alterado.");
    sendLabEvent("game_build_changed_compatibility_mode", {
        detected_build:detectedGameBuild,
        reference_build:TARGET_GAME_BUILD,
        full_version:String(fullVersion || ""),
        blocked:false
    });
    sendGpsStatus("build_warning", reason + " Continuando sem bloqueio.");
}

function inspectGameBuild(o) {
    if (!o || typeof o !== "object" ||
        !Object.prototype.hasOwnProperty.call(o, "version"))
        return !unsupportedBuildBlocked;

    const fullVersion = String(o.version || "");
    const build = buildNumberFromVersion(fullVersion);

    if (build === "")
        return !unsupportedBuildBlocked;

    detectedGameBuild = build;

    if (build !== TARGET_GAME_BUILD) {
        warnUpdatedBuild(build, fullVersion);
        return true;
    }

    return true;
}

// -----------------------------------------------------------
// SOLO ONLY
// -----------------------------------------------------------

function blockMultiplayer(reason) {
    if (multiplayerBlocked)
        return;

    multiplayerBlocked = true;
    blockReason = reason;
    soloConfirmed = false;

    const shouldClear = markerOwned || currentKey !== "";
    pending = [];
    labDeferredStableHarvests = [];
    manualWaypointOverride = false;
    initialOldCorpseScanTimer = clearTimeoutSafe(initialOldCorpseScanTimer);
    initialOldCorpseScanFinished = true;
    cancelHudWarning(true);
    cancelReserveCandidate();
    cancelWaypointWork();

    if (shouldClear)
        clearMarkerInternal();

    resetMarkerState();
    finalClearDone = true;

    if (nearestInterval !== null) {
        try { clearInterval(nearestInterval); }
        catch (_) {}
        nearestInterval = null;
    }

    log("⛔ MULTIPLAYER DETECTADO/BLOQUEADO: " + reason);
    log("⛔ GPS desativado. O programa vai se desconectar do jogo.");
    sendGpsStatus("multiplayer", reason);
    notifyBlock(reason);
}

function normalizeReserve(value) {
    if (value === undefined || value === null || typeof value === "object")
        return "";

    return String(value).trim();
}

function commitReserveChange(nextReserve) {
    if (scriptStopping || multiplayerBlocked || nextReserve === "" ||
        nextReserve === activeReserve)
        return;

    const previousReserve = activeReserve;
    const discarded = pending.length;
    stopWaypointWriterMonitor("troca de reserva");
    stopWaypointHardwareWatch("troca de reserva");
    waypointWriterCapturedForSession = false;
    waypointHardwareWatchCaptured = false;
    waypointHardwareWatchHits = [];
    waypointHardwareWatchHitCount = 0;
    waypointWriterCandidates = [];
    waypointWriterAccessCount = 0;
    waypointObjectDiagnosticsSeen.clear();
    deathSignalReset("troca de reserva");
    sendLabEvent("reserve_change_confirmed", {
        previous_reserve:previousReserve,
        next_reserve:nextReserve,
        pending_before_clear:labPendingSnapshot()
    });
    labScheduleRegistryBurst("troca_de_reserva");
    const hadWaypointWork = markerOwned || currentKey !== "" ||
        switchingKey !== "" || switchTimer !== null ||
        externalReapplyTimer !== null || nearestUpdateTimer !== null;

    cancelReserveCandidate();
    activeReserve = nextReserve;
    pending = [];
    restoredPendingBuffer = [];
    rejectedCatalogRegistryIds.clear();
    catalogNearManualChecks = {};
    skippedCorpseKeys.clear();
    skippedStrongDnas.clear();
    f6TargetKey = "";
    f6PressCount = 0;
    labRecentRemovedRegistryEntries = [];
    labDeferredStableHarvests = [];
    forensicScanSerial++;
    stableAvailabilityScanSerial++;
    stableAvailabilityScanRunning = false;
    const retainedTargets = [];
    for (let i=0; i<forensicTargets.length; i++) {
        const target = forensicTargets[i];
        if (target.collected === true) {
            retainedTargets.push(target);
        } else {
            delete forensicCandidates[String(target.target_id || "")];
        }
    }
    forensicTargets = retainedTargets;
    initialOldCorpseScanTimer = clearTimeoutSafe(initialOldCorpseScanTimer);
    initialOldCorpseScanStarted = false;
    initialOldCorpseScanFinished = false;
    initialOldCorpseScanAttempts = 0;
    initialOldCorpseScanReserve = "";
    initialOldCorpseCandidates = [];
    initialOldCorpseLastReadableAttempt = 0;
    knownOldCorpseIds.clear();
    labTrackedRegistrySlots.clear();
    cancelHudWarning(true);

    // Cancela qualquer Set/Clear atrasado antes de esquecer os ponteiros antigos.
    clearOwn(
        `troca de reserva confirmada ${previousReserve} -> ${activeReserve}`,
        discarded > 0 || hadWaypointWork
    );

    // Um waypoint manual da reserva anterior nunca pode bloquear a nova.
    manualWaypointOverride = false;

    seenDeaths.clear();
    seenDeathDnas.clear();
    seenHarvests.clear();
    seenRecoveredClues.clear();
    lastNavigationAnchor = null;
    preferredWaypointKey = "";
    capturedMap = ptr(0);
    rayXWaitingForManualWaypoint = false;
    autoMapVerifiedPointer = "";
    rayXMapSlotDiscoveryDoneForPointer = "";
    autoWaypointAttemptedKey = "";
    autoWaypointAwaitingRegistry = "";
    autoWaypointMissingSince = 0;
    hudGpsActionRequired = false;
    cachedEntity = ptr(0);
    playerPosConfirmed = false;
    lastExternalHookKind = "";
    lastExternalHookAt = 0;

    log(
        `🗺️ RESERVA ALTERADA E CONFIRMADA ${previousReserve} -> ${activeReserve}. ` +
        `GPS e ${discarded} cadáver(es) antigo(s) limpos.`
    );
    sendGpsStatus("reserve_change", `${previousReserve} -> ${activeReserve}`);
    emitRuntimeState("troca de reserva");

    if (soloConfirmed)
        scheduleInitialOldCorpseScan();
}

function handleReserveSignal(o) {
    if (!o || typeof o !== "object" ||
        !Object.prototype.hasOwnProperty.call(o, "reserve"))
        return;

    const nextReserve = normalizeReserve(o.reserve);

    if (nextReserve === "")
        return;

    // Durante teleporte entre barracas o jogo publica reserve=0 por poucos
    // segundos. Isso é estado de carregamento, não troca de reserva. A 0.9.18
    // confirmou 1 -> 0 e apagou dois DNA antes de receber 0 -> 1.
    if (nextReserve === "0") {
        if (reserveCandidate === "0")
            cancelReserveCandidate();
        sendLabEvent("reserve_zero_loading_ignored", {
            active_reserve:activeReserve,
            pending_preserved:labPendingSnapshot()
        });
        return;
    }

    if (activeReserve === "") {
        cancelReserveCandidate();
        activeReserve = nextReserve;
        rejectedCatalogRegistryIds.clear();
        catalogNearManualChecks = {};
        skippedCorpseKeys.clear();
        skippedStrongDnas.clear();
        f6TargetKey = "";
        f6PressCount = 0;
        log(`🗺️ RESERVA ATIVA detectada: ${activeReserve}.`);
        restorePendingStateForActiveReserve();
        return;
    }

    if (nextReserve === activeReserve) {
        if (reserveCandidate !== "") {
            const rejected = reserveCandidate;
            cancelReserveCandidate();
            log(
                `🗺️ Sinal transitório de reserva ${rejected} ignorado; ` +
                `a reserva ${activeReserve} permaneceu ativa.`
            );
        }
        return;
    }

    if (reserveCandidate === nextReserve && reserveCandidateTimer !== null)
        return;

    cancelReserveCandidate();
    reserveCandidate = nextReserve;
    log(
        `🗺️ Possível troca de reserva ${activeReserve} -> ${nextReserve}; ` +
        `aguardando ${RESERVE_CHANGE_CONFIRM_MS / 1000}s para confirmar.`
    );

    const expectedReserve = nextReserve;
    reserveCandidateTimer = setTimeout(function () {
        reserveCandidateTimer = null;

        if (reserveCandidate !== expectedReserve)
            return;

        commitReserveChange(expectedReserve);
    }, RESERVE_CHANGE_CONFIRM_MS);
}

function inspectSession(path, o) {
    const low = (path || "").toLowerCase();

    if (SOLO_ONLY_PROTECTION && low.indexOf("animaldeathmpevent") >= 0) {
        blockMultiplayer("AnimalDeathMpEvent");
        return false;
    }

    if (!o || typeof o !== "object")
        return !multiplayerBlocked;

    if (!inspectGameBuild(o))
        return false;

    if (SOLO_ONLY_PROTECTION &&
        (o.is_multiplayer === true || o.is_multiplayer === 1)) {
        blockMultiplayer("is_multiplayer=true");
        return false;
    }

    if (Object.prototype.hasOwnProperty.call(o, "network_session_uuid")) {
        const n = String(o.network_session_uuid ?? "");

        if (SOLO_ONLY_PROTECTION && n.length > 0) {
            blockMultiplayer("network_session_uuid ativo");
            return false;
        }

        if (SOLO_ONLY_PROTECTION && !multiplayerBlocked && !soloConfirmed) {
            soloConfirmed = true;
            hudLastReportedStatus = null;
            log("🔒 MODO SOLO confirmado pela sessão. GPS autorizado.");
            sendGpsStatus("ready", "modo solo confirmado");
        }
    }

    if (!multiplayerBlocked) {
        handleReserveSignal(o);

        if (soloConfirmed && activeReserve !== "")
            scheduleInitialOldCorpseScan();
    }

    return !multiplayerBlocked;
}

// -----------------------------------------------------------
// POSIÇÃO DO JOGADOR - CORRIGIDA
// -----------------------------------------------------------

function rayXPlayerStatus(reason, entity, coordinates, error) {
    if (!RAY_X_TRACE)
        return;

    const now = Date.now();
    const entityText = entity ? pstr(entity) : "0x0";
    const coordText = coordinates
        ? [coordinates.x, coordinates.y, coordinates.z].join("|")
        : "";
    const key = entityText + "|" + coordText + "|" + String(error || "");
    if (key === rayXLastPlayerStatusKey &&
        now - rayXLastPlayerStatusAt < RAY_X_STATUS_INTERVAL_MS)
        return;

    rayXLastPlayerStatusKey = key;
    rayXLastPlayerStatusAt = now;
    sendLabEvent("ray_x_player_status", {
        reason:String(reason || ""),
        get_player_entity_address:pstr(GET_ENTITY),
        get_player_entity_rva:"0x" + RVA_GET_PLAYER_ENTITY.toString(16),
        function_ready:GetPlayerEntity !== null,
        entity_pointer:entityText,
        coordinates:coordinates || null,
        coordinate_offsets:["0x2294", "0x2298", "0x229C"],
        error:String(error || "")
    });
}

function refreshEntity(force) {
    // Resolve uma vez e mantém o ponteiro cacheado.
    // Só chama a função nativa novamente se a leitura ficar inválida.
    if (!force && !cachedEntity.isNull())
        return cachedEntity;

    if (GetPlayerEntity === null || getPlayerEntityDisabledAfterFailure) {
        cachedEntity = ptr(0);
        rayXPlayerStatus(
            getPlayerEntityDisabledAfterFailure
                ? "GetPlayerEntity desativada após falha incompatível"
                : "função GetPlayerEntity indisponível",
            cachedEntity,
            null,
            getPlayerEntityDisabledAfterFailure
                ? "disabled_after_native_failure"
                : "native_function_null"
        );
        return cachedEntity;
    }

    try {
        const e = GetPlayerEntity();

        if (e && !e.isNull()) {
            cachedEntity = e;
            rayXPlayerStatus(
                force ? "função retornou ponteiro (forçado)" :
                    "função retornou ponteiro",
                cachedEntity,
                null,
                ""
            );
            return cachedEntity;
        }
        rayXPlayerStatus("função retornou nulo", ptr(0), null, "");
    } catch (error) {
        getPlayerEntityDisabledAfterFailure = true;
        rayXPlayerStatus(
            "erro ao chamar GetPlayerEntity",
            ptr(0),
            null,
            String(error)
        );
    }

    cachedEntity = ptr(0);
    return cachedEntity;
}

function validCoord(v) {
    return Number.isFinite(v) && Math.abs(v) < 1000000;
}

// Confirmado na build 3331010 por caminhada e dois tiros em outra sessão:
// cada slot do módulo aponta a uma estrutura com coordenadas atualizadas.
// Os offsets são relativos ao módulo; nunca usar endereços absolutos do heap.
const PLAYER_POSITION_MODULE_SIZE = 44097536;
const PLAYER_POSITION_SLOTS = [
    {rva:0x027C3A78, offset:0x10},
    {rva:0x027C5D30, offset:0x1D0},
    {rva:0x027A4090, offset:0x410}
];
let playerGlobalPositionReported = false;
let playerGlobalPositionLastWarningAt = 0;

function readGlobalPlayerPosition() {
    // Após uma atualização, não aceitar as posições de uma estrutura que
    // por acaso permaneça legível no mesmo endereço relativo.
    if (Number(Process.mainModule.size) !== PLAYER_POSITION_MODULE_SIZE)
        return null;
    const vectors = [];
    try {
        for (const candidate of PLAYER_POSITION_SLOTS) {
            const base = BASE.add(candidate.rva).readPointer();
            if (!base || base.isNull()) return null;
            const address = base.add(candidate.offset);
            const v = {x:address.readFloat(),
                y:address.add(4).readFloat(),
                z:address.add(8).readFloat()};
            if (!validCoord(v.x) || !validCoord(v.y) || !validCoord(v.z))
                return null;
            vectors.push(v);
        }
    } catch (_) {return null;}
    const first = vectors[0];
    if (vectors.some(function (v) {
        return distanceXZ(v, first)>1 || Math.abs(v.y-first.y)>2;
    })) return null;
    const recent = playerProbeLastSample;
    if (recent && Date.now()-recent.at<2500 &&
        (distanceXZ(first,recent.position)>10 ||
            Math.abs(first.y-recent.position.y)>12)) {
        if (Date.now()-playerGlobalPositionLastWarningAt>15000) {
            playerGlobalPositionLastWarningAt=Date.now();
            sendLabEvent("player_global_position_disagrees_with_shot", {
                source:"three_module_slots",player:first,
                shot:recent.position
            });
        }
        return null;
    }
    if (!playerGlobalPositionReported) {
        playerGlobalPositionReported=true;
        log("✅ POSIÇÃO DO JOGADOR IDENTIFICADA AUTOMATICAMENTE.");
        sendLabEvent("player_global_position_ready", {
            source:"three_module_slots",game_size:Process.mainModule.size,
            slot_rvas:PLAYER_POSITION_SLOTS.map(function (c) {
                return "0x"+c.rva.toString(16);
            }),position:first
        });
    }
    return first;
}

// LAB: encontre o vetor de posição por dois disparos em lugares distintos.
// O endereço só é usado após a mesma tripla acompanhar o jogador andando e
// coincidir com uma segunda posição informada pelo próprio jogo.
const PLAYER_PROBE_MAX_BYTES = 8 * 1024 * 1024 * 1024;
const PLAYER_PROBE_CHUNK_BYTES = 8 * 1024 * 1024;
const PLAYER_PROBE_MAX_HITS = 320;
const PLAYER_PROBE_MAX_WALK_SECONDS = 90;
let playerProbePhase = 0; // 0=ocioso, 1=primeiro disparo, 2=movimento/disparo
let playerProbeAutoAttempts = 0;
let playerProbeAutoLastAttemptAt = 0;
let playerProbeRunning = false;
let playerProbeFirst = null;
let playerProbeCandidates = [];
let playerProbeMoved = new Set();
let playerProbeMonitor = null;
let playerProbeAddress = null;
let playerProbePeers = [];
let playerProbePeerMismatchCount = 0;
let playerProbeLastShortShotLogAt = 0;
let playerProbeMovementDuringScan = 0;
let playerProbeLastSample = null;
let playerProbeScanStartedAt = 0;
let playerProbeScanHudTimer = null;
let playerProbeScanProgressPercent = 0;
let playerProbeAttempt = 0;
let playerProbeSerial = 0;
let playerProbeTrace = new Map();
let playerProbeTraceTicks = 0;
let playerProbeMotionReady = false;
let playerProbeMonitorStartedAt = 0;
let playerProbeLabStop = false;
let playerProbeEmptyScans = 0;
let playerProbeSecondShotCount = 0;
let playerProbeLiveWatch = null;
let playerProbeConfirmedStart = null;

function playerProbeTraceSnapshot() {
    return playerProbeCandidates.map(function (item) {
        const stats = playerProbeTrace.get(item.address);
        return {
            address:item.address,
            quiet_changes:stats ? stats.quiet_changes : 0,
            displacement_m:stats ? stats.displacement_m : 0,
            current:stats ? stats.last : null,
            rejected:stats ? (stats.rejected || "") : ""
        };
    });
}

function resetPlayerProbeTrace() {
    playerProbeTrace = new Map();
    playerProbeTraceTicks = 0;
    playerProbeMotionReady = false;
    updateHudState();
}

function playerProbeVectorAt(address) {
    try {
        // A faixa foi validada no scan. Consultá-la para cada leitura tornava
        // um monitor de 1 segundo 16 segundos mais lento no teste anterior.
        const x = address.readFloat();
        const y = address.add(4).readFloat();
        const z = address.add(8).readFloat();
        return validCoord(x) && validCoord(y) && validCoord(z)
            ? {x:x,y:y,z:z} : null;
    } catch (_) { return null; }
}

function playerProbePattern(pos) {
    const vector = Memory.alloc(12);
    vector.writeFloat(pos.x);
    vector.add(4).writeFloat(pos.y);
    vector.add(8).writeFloat(pos.z);
    return Array.from(new Uint8Array(vector.readByteArray(12)))
        .map(function (v) { return v.toString(16).padStart(2,"0"); })
        .join(" ");
}

function stopPlayerProbeMonitor() {
    if (playerProbeMonitor !== null) {
        try { clearInterval(playerProbeMonitor); } catch (_) {}
        playerProbeMonitor = null;
    }
}

function startPlayerProbeMonitor() {
    stopPlayerProbeMonitor();
    playerProbeMonitorStartedAt=Date.now();
    playerProbeMonitor = setInterval(function () {
        if (scriptStopping || playerProbePhase !== 2) {
            stopPlayerProbeMonitor();
            return;
        }
        const time=Date.now();
        const observed=[];
        for (const item of playerProbeCandidates) {
            const now = playerProbeVectorAt(ptr(item.address));
            let stats=playerProbeTrace.get(item.address);
            if (!stats) {
                stats={last:now,last_at:time,quiet_changes:0,
                    displacement_m:0,rejected:""};
                playerProbeTrace.set(item.address,stats);
                continue;
            }
            if (stats.rejected) continue;
            if (now === null) {
                stats.rejected="endereco ilegivel";
                continue;
            }
            const displacement=distanceXZ(now,playerProbeFirst);
            if (displacement > 1000 ||
                Math.abs(now.y-playerProbeFirst.y)>120) {
                stats.rejected="salto fora da area do jogador";
                continue;
            }
            const diff=distanceXZ(now,stats.last);
            const interval=Math.max(1,(time-stats.last_at)/1000);
            if (diff>Math.max(12,15*interval) ||
                Math.abs(now.y-stats.last.y)>Math.max(8,8*interval)) {
                stats.rejected="salto descontinuo";
                continue;
            }
            if (diff>=0.2 &&
                (!playerProbeLastSample || time-playerProbeLastSample.at>=1200)) {
                stats.quiet_changes++;
                stats.displacement_m=displacement;
                observed.push(item.address);
                if (displacement>12) playerProbeMoved.add(item.address);
            }
            stats.last=now;
            stats.last_at=time;
            if (stats.quiet_changes>=3 && stats.displacement_m>=20 &&
                !playerProbeMotionReady) {
                playerProbeMotionReady=true;
                updateHudState();
                log("🔎 LAB POSIÇÃO: 20 M REGISTRADOS. ATIRE PARADO AGORA.");
            }
        }
        if (POSITION_ONLY_LAB && (++playerProbeTraceTicks % 5) === 0)
            sendLabEvent("position_only_monitor_tick", {
                step:playerProbeSecondShotCount+1,
                observed_addresses:observed,
                shot_quiet_ms:playerProbeLastSample
                    ? time-playerProbeLastSample.at : null,
                candidates:playerProbeTraceSnapshot()
            });
        if (POSITION_ONLY_LAB &&
            time-playerProbeMonitorStartedAt>=PLAYER_PROBE_MAX_WALK_SECONDS*1000)
            finishPositionOnlyProbe("a etapa de movimento excedeu 90 s " +
                "sem uma confirmação segura");
    }, 1000);
}

function armPlayerProbe() {
    if (!POSITION_ONLY_LAB && readGlobalPlayerPosition() !== null)
        return {ok:true, source:"global",reason:"position_available"};
    if (POSITION_ONLY_LAB && playerProbeLabStop)
        return {ok:false, reason:"diagnostic_finished_send_zip"};
    if (POSITION_ONLY_LAB && playerProbeAddress !== null)
        return {ok:false, reason:"position_confirmed_send_zip"};
    if (scriptStopping || multiplayerBlocked || !soloConfirmed)
        return {ok:false, reason:"session_not_ready"};
    if (playerProbeRunning)
        return {ok:false, reason:"scan_running"};
    playerProbeSerial++;
    stopPlayerProbeMonitor();
    playerProbePhase = 1;
    playerProbeFirst = null;
    playerProbeCandidates = [];
    playerProbeMoved.clear();
    playerProbeAddress = null;
    playerProbePeers = [];
    playerProbePeerMismatchCount = 0;
    playerProbeMovementDuringScan = 0;
    playerProbeAttempt = 0;
    // Conte buscas vazias ao longo da sessão. F6 não deve zerar esse limite.
    if (!POSITION_ONLY_LAB) playerProbeEmptyScans = 0;
    playerProbeSecondShotCount=0;
    playerProbeConfirmedStart=null;
    resetPlayerProbeTrace();
    updateHudState();
    log(POSITION_ONLY_LAB
        ? "🟠 LAB POSIÇÃO: ATIRE UMA VEZ, FIQUE PARADO ATÉ A BUSCA " +
          "TERMINAR; DEPOIS CAMINHE SEM ATIRAR ATÉ O HUD PEDIR O DISPARO."
        : "🟠 LAB POSIÇÃO: ATIRE UMA VEZ E FIQUE PARADO ATÉ A BUSCA " +
          "TERMINAR. DEPOIS CAMINHE 20 M E ATIRE OUTRA VEZ.");
    sendLabEvent("player_position_probe_armed", {
        max_scan_bytes:PLAYER_PROBE_MAX_BYTES
    });
    return {ok:true};
}

function runPlayerProbeScan(sample) {
    const pattern = playerProbePattern(sample);
    let ranges = [];
    try {
        ranges = Process.enumerateRanges({protection:"rw-",coalesce:false});
    } catch (error) {
        sendLabEvent("player_position_probe_error", {
            stage:"ranges",error:String(error)
        });
        playerProbePhase = 0;
        updateHudState();
        return;
    }
    ranges = ranges.filter(function (range) {
        return !range.file && Number(range.size) >= 12;
    }).sort(function (a,b) {
        return Number(b.size) - Number(a.size);
    });
    const selected = [];
    let planned = 0;
    for (const range of ranges) {
        if (planned >= PLAYER_PROBE_MAX_BYTES)
            break;
        const size = Math.min(Number(range.size),
            PLAYER_PROBE_MAX_BYTES - planned);
        if (size < 12) continue;
        selected.push({base:range.base,size:size});
        planned += size;
    }
    const serial = ++playerProbeSerial;
    let rangeIndex = 0;
    let offset = 0;
    let scanned = 0;
    let errors = 0;
    const hits = new Set();
    playerProbeRunning = true;
    playerProbeFirst = sample;
    playerProbeMovementDuringScan = 0;
    playerProbeScanStartedAt = Date.now();
    playerProbeScanProgressPercent = 0;
    if (playerProbeScanHudTimer !== null)
        clearInterval(playerProbeScanHudTimer);
    playerProbeScanHudTimer=setInterval(updateHudState,1000);
    updateHudState();
    log("🟠 LAB POSIÇÃO: BUSCA 0%. FIQUE PARADO; " +
        "AGUARDE APARECER CAMINHE 20M OU PARE E ENVIE ZIP.");
    sendLabEvent("player_position_probe_started", {
        planned_bytes:planned, ranges:selected.length, attempt:playerProbeAttempt,
        hunter_position:sample
    });
    function finish(cancelled) {
        playerProbeRunning = false;
        if (playerProbeScanHudTimer !== null) {
            clearInterval(playerProbeScanHudTimer);
            playerProbeScanHudTimer=null;
        }
        if (cancelled) {
            playerProbePhase = 0;
        } else {
            const movedDuringScan = playerProbeMovementDuringScan > 2;
            const candidateAddresses=movedDuringScan ? [] : Array.from(hits);
            // Uma leitura igual durante o scan não basta: vetores antigos e
            // blocos reciclados deixaram 251 falsos candidatos no ZIP anterior.
            playerProbeCandidates=candidateAddresses.filter(function (address) {
                const current=playerProbeVectorAt(ptr(address));
                return current && distanceXZ(current,sample)<=4 &&
                    Math.abs(current.y-sample.y)<=6;
            }).map(function (address) {return {address:address};});
            playerProbeMoved.clear();
            playerProbePhase = playerProbeCandidates.length ? 2 : 0;
            if (playerProbePhase === 2) {
                playerProbeEmptyScans=0;
                resetPlayerProbeTrace();
                startPlayerProbeMonitor();
            } else if (POSITION_ONLY_LAB && !movedDuringScan &&
                ++playerProbeEmptyScans >= 2)
                playerProbeLabStop=true;
        }
        updateHudState();
        sendLabEvent("player_position_probe_finished", {
            cancelled:cancelled, scanned_bytes:scanned,
            planned_bytes:planned, error_count:errors,
            elapsed_ms:Date.now()-playerProbeScanStartedAt,
            raw_candidate_count:hits.size,
            raw_candidate_addresses:cancelled ? Array.from(hits) : undefined,
            hit_limit_reached:hits.size>=PLAYER_PROBE_MAX_HITS,
            candidate_count:playerProbeCandidates.length,
            candidates:playerProbeCandidates,
            movement_observed_during_scan_m:playerProbeMovementDuringScan,
            needs_stationary_retry:playerProbeMovementDuringScan > 2,
            stationary_empty_scans:playerProbeEmptyScans
        });
        log(cancelled && POSITION_ONLY_LAB
            ? "🟠 LAB POSIÇÃO: BUSCA INTERROMPIDA EM " +
              playerProbeScanProgressPercent + "%; " + hits.size +
              " CANDIDATO(S) BRUTOS SEM VALIDAÇÃO. CLIQUE PARAR E ENVIE ZIP."
            : POSITION_ONLY_LAB && playerProbeLabStop
            ? "🟠 LAB POSIÇÃO: DUAS BUSCAS SEM COORDENADAS. " +
              "PARE E ENVIE O ZIP; NÃO REPITA F6."
            : playerProbeMovementDuringScan > 2
            ? "🟠 LAB POSIÇÃO: VOCÊ ANDOU DURANTE A BUSCA. " +
              "APERTE F6 NOVAMENTE; ATIRE E FIQUE PARADO ATÉ TERMINAR."
            : playerProbePhase === 2
            ? (POSITION_ONLY_LAB
                ? "🟠 LAB POSIÇÃO: CAMINHE 20 M SEM ATIRAR; " +
                  "AGUARDE ATIRE AGORA NO HUD."
                : "🟠 LAB POSIÇÃO: CAMINHE 20 M E ATIRE OUTRA VEZ. " +
                  "A POSIÇÃO SERÁ VALIDADA PELO MOVIMENTO.")
            : (POSITION_ONLY_LAB
                ? "🟠 LAB POSIÇÃO: NENHUM VETOR NESTA BUSCA. " +
                  "APERTE F6 MAIS UMA VEZ E FIQUE PARADO."
                : "🟠 LAB POSIÇÃO: NÃO ACHEI VETOR SEGURO. " +
                  "CLIQUE PARAR E ENVIE O ZIP DO LOG."));
    }
    function step() {
        if (serial !== playerProbeSerial || scriptStopping) {
            finish(true);return;
        }
        if (rangeIndex >= selected.length ||
            hits.size >= PLAYER_PROBE_MAX_HITS) {
            finish(false);return;
        }
        const range = selected[rangeIndex];
        if (offset >= range.size) {
            rangeIndex++;offset=0;setTimeout(step,0);return;
        }
        const size = Math.min(PLAYER_PROBE_CHUNK_BYTES, range.size-offset);
        const overlap = size < range.size-offset ? 11 : 0;
        try {
            Memory.scan(range.base.add(offset),size+overlap,pattern,{
                onMatch(address) {
                    const key = pstr(address);
                    if (playerProbeVectorAt(address)) hits.add(key);
                    if (hits.size >= PLAYER_PROBE_MAX_HITS) return "stop";
                },
                onError() { errors++; },
                onComplete() {
                    offset += size;
                    scanned += size;
                    if (planned > 0) {
                        const percent=Math.min(99,Math.floor(scanned*100/planned));
                        if (percent>=playerProbeScanProgressPercent+10) {
                            playerProbeScanProgressPercent=percent;
                            updateHudState();
                            log("🟠 LAB POSIÇÃO: BUSCA " + percent +
                                "%. FIQUE PARADO ATÉ A INSTRUÇÃO MUDAR.");
                        }
                    }
                    setTimeout(step,0);
                }
            });
        } catch (_) {
            errors++;
            offset += size;
            scanned += size;
            setTimeout(step,0);
        }
    }
    setTimeout(step,0);
}

// O jogo mantém cópias da posição em estruturas distintas. O ZIP anterior
// mostrou dezenas delas andando em conjunto nos três disparos; exigir apenas
// uma estrutura de até 0x100 bytes descartava todas. Escolher três cópias
// separadas, com movimento observado e concordância com a telemetria do jogo.
function playerProbeMatchingCluster(addresses, sample) {
    const sorted = Array.from(new Set(addresses)).sort(function (a,b) {
        return ptr(a).compare(ptr(b));
    });
    const peers = [];
    for (const address of sorted) {
        const pos = playerProbeVectorAt(ptr(address));
        if (!pos || distanceXZ(pos,sample)>1 ||
            Math.abs(pos.y-sample.y)>2)
            continue;
        if (peers.length && ptr(address).compare(
                ptr(peers[peers.length-1]).add(0x10000))<0)
            continue;
        peers.push(address);
        if (peers.length===3) break;
    }
    return peers.length===3 ? peers : [];
}

function finishPositionOnlyProbe(reason) {
    const traces=playerProbeTraceSnapshot();
    playerProbeLabStop=true;
    playerProbePhase=0;
    stopPlayerProbeMonitor();
    updateHudState();
    sendLabEvent("position_only_probe_finished_unresolved", {
        reason:String(reason || "candidatos ambíguos"),
        steps:playerProbeSecondShotCount,
        trace:traces
    });
    log("🟠 LAB POSIÇÃO: " + String(reason || "não foi possível " +
        "isolar a posição") + ". PARE E ENVIE O ZIP; " +
        "NÃO REPITA F6 NESTA PARTIDA.");
}

function watchConfirmedPlayerPosition() {
    if (playerProbeLiveWatch !== null)
        clearInterval(playerProbeLiveWatch);
    let tick=0;
    playerProbeLiveWatch=setInterval(function () {
        if (scriptStopping || playerProbeAddress === null) {
            clearInterval(playerProbeLiveWatch);
            playerProbeLiveWatch=null;
            if (!scriptStopping && playerProbeAddress === null)
                finishPositionOnlyProbe("o endereço deixou de acompanhar o jogador");
            return;
        }
        const live=getPlayerPos();
        if (++tick % 2 === 0)
            sendLabEvent("position_only_confirmed_live", {
                primary:pstr(playerProbeAddress),
                peers:playerProbePeers,
                live:live,
                seconds:tick
            });
        if (live && playerProbeConfirmedStart &&
                distanceXZ(live,playerProbeConfirmedStart)>=20) {
            sendLabEvent("position_only_confirmed_walk_finished", {
                first:playerProbeConfirmedStart,live:live,
                walked_m:distanceXZ(live,playerProbeConfirmedStart),
                seconds:tick,peers:playerProbePeers
            });
            playerProbeLabStop=true;
            updateHudState();
            clearInterval(playerProbeLiveWatch);
            playerProbeLiveWatch=null;
            log("✅ LAB POSIÇÃO: MOVIMENTO CONFIRMADO. " +
                "PARE, CLIQUE PARAR NO PROGRAMA E ENVIE O ZIP.");
            return;
        }
        if (tick % 5 === 0 && live)
            log(`📍 POSIÇÃO ATUAL: X=${live.x.toFixed(1)} ` +
                `Y=${live.y.toFixed(1)} Z=${live.z.toFixed(1)}`);
    },1000);
}

function observePlayerProbeShot(sample) {
    if (playerProbeRunning) {
        if (playerProbePhase === 1 && playerProbeFirst !== null)
            playerProbeMovementDuringScan = Math.max(
                playerProbeMovementDuringScan,
                distanceXZ(sample,playerProbeFirst)
            );
        return;
    }
    if (playerProbePhase === 0)
        return;
    if (playerProbePhase === 1) {
        runPlayerProbeScan(sample);
        return;
    }
    const d = distanceXZ(sample,playerProbeFirst);
    if (d < 15) {
        if (POSITION_ONLY_LAB) resetPlayerProbeTrace();
        if (Date.now()-playerProbeLastShortShotLogAt >= 10000) {
            playerProbeLastShortShotLogAt=Date.now();
            log("🟠 LAB POSIÇÃO: CAMINHE PELO MENOS 20 M E ATIRE OUTRA VEZ.");
        }
        return;
    }
    const matching = [];
    for (const item of playerProbeCandidates) {
        if (!playerProbeMoved.has(item.address)) continue;
        const pos = playerProbeVectorAt(ptr(item.address));
        if (pos && distanceXZ(pos,sample) <= 3 &&
            Math.abs(pos.y-sample.y) <= 5)
            matching.push(item.address);
    }
    const trace=playerProbeTraceSnapshot();
    const eligible=matching.filter(function (address) {
            const stats=playerProbeTrace.get(address);
            return stats && stats.quiet_changes>=3 &&
                stats.displacement_m>=12;
        });
    playerProbeSecondShotCount++;
    sendLabEvent("player_position_probe_second_shot", {
        first:playerProbeFirst,second:sample,
        distance_m:d, candidate_count:playerProbeCandidates.length,
        moved_count:playerProbeMoved.size, matching_addresses:matching,
        quiet_motion_addresses:eligible,
        trace:trace,
        step:playerProbeSecondShotCount
    });
    const cluster = playerProbeMatchingCluster(eligible,sample);
    // Duas caminhadas e três disparos evitam validar uma cópia temporária.
    if (cluster.length && playerProbeSecondShotCount>=2) {
        playerProbeAddress = ptr(cluster[0]);
        playerProbePeers = cluster.slice(1);
        playerProbePeerMismatchCount = 0;
        playerProbePhase = 0;
        stopPlayerProbeMonitor();
        updateHudState();
        sendLabEvent("player_position_probe_cluster_confirmed", {
            selected:cluster[0],peer_addresses:cluster.slice(1),
            first:playerProbeFirst,second:sample,
            displacement_m:d,mutual_distance_max_m:1,
            movement_confirmed_before_second_shot:true
        });
        if (POSITION_ONLY_LAB) {
            playerProbeConfirmedStart=sample;
            watchConfirmedPlayerPosition();
            log("✅ LAB POSIÇÃO CONFIRMADA. CAMINHE MAIS 20 M " +
                "SEM ATIRAR E DEPOIS PARE E ENVIE O ZIP.");
        } else {
            scheduleNearestUpdate(80,"posição real do jogador confirmada");
            log("✅ LAB POSIÇÃO CONFIRMADA EM MOVIMENTO. " +
                "GPS VAI ESCOLHER O CORPO MAIS PRÓXIMO A VOCÊ.");
        }
        return;
    }
    if (POSITION_ONLY_LAB && playerProbeSecondShotCount >= 3) {
        finishPositionOnlyProbe("três deslocamentos ainda deram " +
            "candidatos inconclusivos");
        return;
    }
    if (matching.length > 0) {
        playerProbeCandidates = playerProbeCandidates.filter(function (item) {
            return matching.indexOf(item.address) >= 0;
        });
        playerProbeFirst=sample;
        playerProbeMoved.clear();
        resetPlayerProbeTrace();
        log(cluster.length
            ? "🟠 LAB POSIÇÃO: PRIMEIRO DESLOCAMENTO VALIDADO. " +
              "CAMINHE MAIS 20 M SEM ATIRAR E AGUARDE O HUD."
            : "🟠 LAB POSIÇÃO: FALTAM PROVAS DE MOVIMENTO CONTÍNUO. " +
              "CAMINHE MAIS 20 M SEM ATIRAR E AGUARDE O HUD.");
        return;
    }
    if (matching.length > 1) {
        playerProbeCandidates = playerProbeCandidates.filter(function (item) {
            return matching.indexOf(item.address) >= 0;
        });
        playerProbeFirst = sample;
        playerProbeMoved.clear();
        log("🟠 LAB POSIÇÃO: AINDA HÁ MAIS DE UM ENDEREÇO. " +
            "CAMINHE 20 M E ATIRE NOVAMENTE.");
        return;
    }
    if (++playerProbeAttempt < 2) {
        stopPlayerProbeMonitor();
        runPlayerProbeScan(sample);
        return;
    }
    if (POSITION_ONLY_LAB) {
        finishPositionOnlyProbe("nenhum endereço acompanhou o jogador");
        return;
    }
    playerProbePhase = 0;
    stopPlayerProbeMonitor();
    updateHudState();
    log("🟠 LAB POSIÇÃO: NÃO CONSEGUI VALIDAR O MOVIMENTO. " +
        "CLIQUE PARAR E ENVIE O ZIP DO LOG.");
}

function observePlayerTelemetry(path,payload) {
    if (!payload || multiplayerBlocked || scriptStopping)
        return;
    const low=String(path||"").toLowerCase();
    if (low.indexOf("weaponfired") < 0)
        return;
    const x=finiteNumber(payload.hunter_x);
    const y=finiteNumber(payload.hunter_y);
    const z=finiteNumber(payload.hunter_z);
    if (x===null || y===null || z===null ||
        !validCoord(x) || !validCoord(y) || !validCoord(z) ||
        (normalizeReserve(payload.reserve) || activeReserve) !==
            normalizeReserve(activeReserve)) return;
    const sample={x:x,y:y,z:z};
    playerProbeLastSample={position:sample,at:Date.now()};
    // O primeiro disparo calibra automaticamente a posição. F6 continua
    // disponível como segunda tentativa se a calibração perder amostras.
    if (PUBLIC_BETA && !playerGlobalPositionReported &&
        readGlobalPlayerPosition() === null &&
        playerProbePhase === 0 &&
        playerProbeAddress === null && !playerProbeRunning &&
        playerProbeAutoAttempts < 2 &&
        Date.now() - playerProbeAutoLastAttemptAt > 90000) {
        const result = armPlayerProbe();
        if (result.ok) {
            playerProbeAutoAttempts++;
            playerProbeAutoLastAttemptAt = Date.now();
            log("CALIBRAÇÃO AUTOMÁTICA: fique parado durante a busca; " +
                "depois caminhe 20 m e atire mais duas vezes.");
        }
    }
    if (POSITION_ONLY_LAB)
        sendLabEvent("position_only_game_shot_coordinates", {
            position:sample,phase:playerProbePhase,
            scan_running:playerProbeRunning
        });
    if (POSITION_ONLY_LAB)
        log(`📍 POSIÇÃO NO DISPARO: X=${x.toFixed(1)} ` +
            `Y=${y.toFixed(1)} Z=${z.toFixed(1)}`);
    observePlayerProbeShot(sample);
    if (playerProbeAddress !== null) {
        if (POSITION_ONLY_LAB) {
            const live=getPlayerPos();
            sendLabEvent("position_only_shot_pointer_check", {
                shot:sample,
                live:live,
                distance_m:live ? distanceXZ(live,sample) : null
            });
        } else
            scheduleNearestUpdate(80,"posição confirmada pelo novo disparo");
    }
}

function getPlayerPos() {
    if (multiplayerBlocked)
        return null;

    const globalPosition = readGlobalPlayerPosition();
    if (globalPosition !== null)
        return globalPosition;

    if (playerProbeAddress !== null) {
        const live = playerProbeVectorAt(playerProbeAddress);
        if (live !== null) {
            const peersOk = playerProbePeers.every(function (address) {
                const peer = playerProbeVectorAt(ptr(address));
                return peer && distanceXZ(peer,live)<=1 &&
                    Math.abs(peer.y-live.y)<=2;
            });
            if (!peersOk) {
                if (++playerProbePeerMismatchCount >= 3) {
                    sendLabEvent("player_position_probe_lost", {
                        reason:"same_object_vectors_disagree",
                        primary:pstr(playerProbeAddress),
                        peer_addresses:playerProbePeers
                    });
                    playerProbeAddress=null;
                    playerProbePeers=[];
                }
                return null;
            }
            playerProbePeerMismatchCount=0;
            const sample = playerProbeLastSample;
            if (sample && Date.now()-sample.at < 2500 &&
                (distanceXZ(live,sample.position)>10 ||
                 Math.abs(live.y-sample.position.y)>12)) {
                sendLabEvent("player_position_probe_lost", {
                    reason:"live_pointer_disagrees_with_weapon_event",
                    live:live,shot:sample.position
                });
                playerProbeAddress = null;
                playerProbePeers = [];
            } else return live;
        } else {
            playerProbeAddress = null;
            playerProbePeers = [];
        }
    }

    let entity = refreshEntity(false);

    if (entity.isNull())
        return null;

    try {
        // CONFIRMADO no código CreatePlayerIcon desta build.
        const x = entity.add(0x2294).readFloat();
        const y = entity.add(0x2298).readFloat();
        const z = entity.add(0x229C).readFloat();

        if (!validCoord(x) || !validCoord(y) || !validCoord(z)) {
            rayXPlayerStatus(
                "coordenadas inválidas no ponteiro cacheado",
                entity,
                {x:x, y:y, z:z},
                "invalid_coordinates"
            );
            cachedEntity = ptr(0);
            entity = refreshEntity(true);

            if (entity.isNull())
                return null;

            const x2 = entity.add(0x2294).readFloat();
            const y2 = entity.add(0x2298).readFloat();
            const z2 = entity.add(0x229C).readFloat();

            if (!validCoord(x2) || !validCoord(y2) || !validCoord(z2)) {
                rayXPlayerStatus(
                    "coordenadas inválidas após nova resolução",
                    entity,
                    {x:x2, y:y2, z:z2},
                    "invalid_coordinates_after_refresh"
                );
                return null;
            }

            rayXPlayerStatus(
                "posição válida após nova resolução",
                entity,
                {x:x2, y:y2, z:z2},
                ""
            );

            if (!playerPosConfirmed) {
                playerPosConfirmed = true;
                log(
                    `✅ POSICAO REAL DO JOGADOR -> ` +
                    `(${x2.toFixed(2)}, ${y2.toFixed(2)}, ${z2.toFixed(2)})`
                );
            }

            return {x:x2, y:y2, z:z2};
        }

        if (!playerPosConfirmed) {
            playerPosConfirmed = true;
            log(
                `✅ POSICAO REAL DO JOGADOR -> ` +
                `(${x.toFixed(2)}, ${y.toFixed(2)}, ${z.toFixed(2)})`
            );
        }

        rayXPlayerStatus(
            "posição válida",
            entity,
            {x:x, y:y, z:z},
            ""
        );

        return {x:x, y:y, z:z};

    } catch (error) {
        rayXPlayerStatus(
            "erro lendo posição do jogador",
            entity,
            null,
            String(error)
        );
        cachedEntity = ptr(0);
        return null;
    }
}

function distSqXZ(a, b) {
    const dx = Number(a.x) - Number(b.x);
    const dz = Number(a.z) - Number(b.z);
    return dx*dx + dz*dz;
}

function distanceXZ(a, b) {
    return Math.sqrt(distSqXZ(a, b));
}

// -----------------------------------------------------------
// BUSCA INICIAL DE ABATES ANTERIORES
// -----------------------------------------------------------
// A lista e pequena e lida somente durante uma curta janela inicial.
// Depois disso, esta parte para completamente.

function registryBoundsFromSlot(slot) {
    if (!slot)
        return null;
    try {
        const registry = slot.readPointer();

        if (!registry || registry.isNull())
            return null;

        const registryRange = Process.findRangeByAddress(registry);
        if (!registryRange ||
            String(registryRange.protection || "").indexOf("r") < 0)
            return null;

        const begin = registry.add(0x70).readPointer();
        let end = registry.add(0x78).readPointer();

        if (!begin || !end || begin.isNull() || end.isNull() ||
            begin.compare(end) >= 0)
            return null;

        const range = Process.findRangeByAddress(begin);

        if (!range || String(range.protection || "").indexOf("r") < 0)
            return null;

        const readableEnd = range.base.add(range.size);

        if (end.compare(readableEnd) > 0)
            end = readableEnd;

        if (begin.compare(end) >= 0)
            return null;

        return {slot:slot, registry:registry, begin:begin, end:end};
    } catch (_) {
        return null;
    }
}

function inspectRegistrySlotCandidate(slot) {
    const bounds = registryBoundsFromSlot(slot);
    if (!bounds)
        return null;

    const maximumEnd = bounds.begin.add(
        OLD_CORPSE_DESCRIPTOR_SIZE * OLD_CORPSE_MAX_SCAN
    );
    if (bounds.end.compare(maximumEnd) > 0)
        return null;

    let byteLength = 0;
    try { byteLength = bounds.end.sub(bounds.begin).toUInt32(); }
    catch (_) { return null; }

    if (byteLength < OLD_CORPSE_DESCRIPTOR_SIZE * 16 ||
        byteLength % OLD_CORPSE_DESCRIPTOR_SIZE !== 0)
        return null;

    const descriptorCount = Math.floor(
        byteLength / OLD_CORPSE_DESCRIPTOR_SIZE
    );
    const wantedSamples = Math.min(64, descriptorCount);
    const step = Math.max(1, Math.floor(descriptorCount / wantedSamples));
    const types = {};
    let sampled = 0;
    let typeHits = 0;
    let coordinateHits = 0;
    let idHits = 0;
    let type39Hits = 0;

    for (let index=0;
        index<descriptorCount && sampled<wantedSamples;
        index+=step) {
        const entry = bounds.begin.add(index * OLD_CORPSE_DESCRIPTOR_SIZE);
        sampled++;
        try {
            const type = entry.add(0x60).readU8();
            const x = entry.add(0x20).readFloat();
            const y = entry.add(0x24).readFloat();
            const z = entry.add(0x28).readFloat();
            const registryId = normalizeRegistryId(
                entry.add(0x30).readU64().toString()
            );

            if (type >= 1 && type <= 127) {
                typeHits++;
                types[String(type)] = Number(types[String(type)] || 0) + 1;
                if (type === OLD_CORPSE_TYPE)
                    type39Hits++;
            }
            if (validCoord(x) && validCoord(y) && validCoord(z) &&
                Math.abs(x) + Math.abs(y) + Math.abs(z) > 1)
                coordinateHits++;
            if (registryId !== "")
                idHits++;
        } catch (_) {}
    }

    if (sampled < 8)
        return null;

    const typeRatio = typeHits / sampled;
    const coordinateRatio = coordinateHits / sampled;
    const idRatio = idHits / sampled;
    const distinctTypes = Object.keys(types).length;

    if (typeRatio < 0.70 || coordinateRatio < 0.55 ||
        idRatio < 0.55 || distinctTypes < 2)
        return null;

    const score = Math.round(
        typeRatio * 4000 + coordinateRatio * 3000 + idRatio * 2000 +
        Math.min(500, distinctTypes * 25) +
        Math.min(500, type39Hits * 100)
    );

    return {
        slot:pstr(slot),
        registry:pstr(bounds.registry),
        begin:pstr(bounds.begin),
        end:pstr(bounds.end),
        descriptor_count:descriptorCount,
        sample_count:sampled,
        type_hits:typeHits,
        coordinate_hits:coordinateHits,
        id_hits:idHits,
        distinct_types:distinctTypes,
        type39_hits:type39Hits,
        score:score,
        types:types
    };
}

function discoverRegistrySlot() {
    if (registryDiscoveryRunning || registryDiscoverySelected)
        return registryDiscoverySelected;
    if (!soloConfirmed || activeReserve === "")
        return false;
    if (registryDiscoveryAttempts >= REGISTRY_DISCOVERY_MAX_ATTEMPTS ||
        Date.now() < registryDiscoveryNextAt)
        return false;

    registryDiscoveryRunning = true;
    registryDiscoveryAttempts++;
    registryDiscoveryNextAt = Date.now() + REGISTRY_DISCOVERY_RETRY_MS;
    const startedAt = Date.now();
    let ranges = [];
    let scannedBytes = 0;
    let pointerHits = 0;
    const candidates = [];
    const seenRegistries = new Set();

    try {
        ranges = Process.enumerateRanges({
            protection:"rw-",
            coalesce:false
        });
    } catch (error) {
        registryDiscoveryRunning = false;
        sendLabEvent("registry_slot_discovery_failed", {
            attempt:registryDiscoveryAttempts,
            reason:"range_enumeration_failed",
            error:String(error)
        });
        return false;
    }

    const moduleEnd = BASE.add(Process.mainModule.size);
    ranges = ranges.filter(function (range) {
        return range.base.compare(BASE) >= 0 &&
            range.base.compare(moduleEnd) < 0;
    });

    for (let rangeIndex=0;
        rangeIndex<ranges.length &&
        scannedBytes<REGISTRY_DISCOVERY_MAX_MODULE_BYTES &&
        candidates.length<REGISTRY_DISCOVERY_MAX_CANDIDATES;
        rangeIndex++) {
        const range = ranges[rangeIndex];
        const rangeSize = Math.min(
            Number(range.size || 0),
            REGISTRY_DISCOVERY_MAX_MODULE_BYTES - scannedBytes
        );

        for (let chunkOffset=0; chunkOffset<rangeSize;
            chunkOffset+=REGISTRY_DISCOVERY_CHUNK_BYTES) {
            const chunkBase = range.base.add(chunkOffset);
            const chunkSize = Math.min(
                REGISTRY_DISCOVERY_CHUNK_BYTES,
                rangeSize - chunkOffset
            );
            let bytes = null;
            try {
                const buffer = chunkBase.readByteArray(chunkSize);
                if (buffer !== null)
                    bytes = new Uint8Array(buffer);
            } catch (_) {}
            scannedBytes += chunkSize;
            if (bytes === null)
                continue;

            for (let offset=0;
                offset + Process.pointerSize <= bytes.length;
                offset+=Process.pointerSize) {
                if (Process.pointerSize === 8) {
                    // Ponteiros de heap do Windows observados no jogo ficam
                    // abaixo de 0x00007f0000000000. O filtro evita milhões de
                    // leituras nativas sem descartar os endereços reais.
                    if (bytes[offset + 7] !== 0 || bytes[offset + 6] !== 0 ||
                        bytes[offset + 5] === 0 || bytes[offset + 5] >= 0x7f)
                        continue;
                }

                const slot = chunkBase.add(offset);
                let registry = ptr(0);
                try { registry = slot.readPointer(); }
                catch (_) { continue; }
                if (!registry || registry.isNull())
                    continue;

                const registryKey = pstr(registry);
                if (seenRegistries.has(registryKey))
                    continue;
                seenRegistries.add(registryKey);

                const registryRange = Process.findRangeByAddress(registry);
                if (!registryRange ||
                    String(registryRange.protection || "").indexOf("r") < 0)
                    continue;

                pointerHits++;
                const inspected = inspectRegistrySlotCandidate(slot);
                if (inspected)
                    candidates.push(inspected);
            }
        }
    }

    candidates.sort(function (left, right) {
        if (right.score !== left.score)
            return right.score - left.score;
        return right.descriptor_count - left.descriptor_count;
    });
    registryDiscoveryLastCandidates = candidates.slice(0, 16);

    const best = candidates.length ? candidates[0] : null;
    if (best) {
        activeRegistrySlot = ptr(best.slot);
        oldCorpseSearchAddressReady = true;
        registryDiscoverySelected = true;
        log(
            "✅ CATÁLOGO RECUPERADO APÓS ATUALIZAÇÃO: slot " + best.slot +
            " | descritores=" + best.descriptor_count +
            " | pontuação=" + best.score + "."
        );
        sendLabEvent("registry_slot_discovery_succeeded", {
            attempt:registryDiscoveryAttempts,
            elapsed_ms:Date.now() - startedAt,
            scanned_bytes:scannedBytes,
            pointer_hits:pointerHits,
            selected:best,
            candidates:registryDiscoveryLastCandidates
        });
    } else {
        log(
            "🧩 MODO COMPATÍVEL: procurando o novo catálogo do jogo " +
            "(tentativa " + registryDiscoveryAttempts + "/" +
            REGISTRY_DISCOVERY_MAX_ATTEMPTS + ")."
        );
        sendLabEvent("registry_slot_discovery_empty", {
            attempt:registryDiscoveryAttempts,
            elapsed_ms:Date.now() - startedAt,
            scanned_bytes:scannedBytes,
            pointer_hits:pointerHits,
            writable_module_ranges:ranges.map(function (range) {
                return {
                    base:pstr(range.base),
                    size:Number(range.size || 0),
                    protection:String(range.protection || ""),
                    file:String(range.file || "")
                };
            }),
            candidates:registryDiscoveryLastCandidates
        });
    }

    registryDiscoveryRunning = false;
    return registryDiscoverySelected;
}

function oldCorpseRegistryBounds() {
    let bounds = registryBoundsFromSlot(activeRegistrySlot);
    if (bounds)
        return bounds;

    discoverRegistrySlot();
    bounds = registryBoundsFromSlot(activeRegistrySlot);
    return bounds;
}

function readOldCorpseEntry(pointer) {
    const inspected = inspectOldCorpseDescriptor(pointer);

    if (!inspected || inspected.registry_id === "" ||
        !validCoord(inspected.x) || !validCoord(inspected.y) ||
        !validCoord(inspected.z))
        return null;

    // O object_link antigo varia entre cadáveres reais e fica apenas no log.
    // A disponibilidade será confirmada depois na estrutura unificada do ID.
    return inspected;
}

function sameOldCorpsePosition(a, b) {
    return distanceXZ(a, b) <= 1.0 &&
        Math.abs(Number(a.y) - Number(b.y)) <= 5.0;
}

function pendingHasAlternativeDnaCandidate(candidate) {
    if (!candidate || candidate.recovered_from_alternative_layout !== true)
        return false;

    const target = candidate.dna_target || null;
    const dna = strongCorpseDna(target);
    if (dna === "")
        return false;

    for (let i=0; i<pending.length; i++) {
        const corpse = pending[i];
        if (strongCorpseDna(corpse) !== dna)
            continue;

        corpse.recovered_from_alternative_layout = true;
        corpse.recovered_alternative_record_address = String(
            candidate.alternative_record_address || ""
        );
        corpse.recovered_alternative_range_base = String(
            candidate.alternative_record_range_base || ""
        );
        corpse.recovered_availability = finiteNumber(
            candidate.stable_availability
        );
        corpse.recovered_lifecycle = finiteNumber(
            candidate.stable_lifecycle
        );
        corpse.recovered_live_pointer_count = finiteNumber(
            candidate.stable_nonzero_pointers
        );
        corpse.recovered_readable_pointer_count = finiteNumber(
            candidate.stable_readable_pointers
        );
        return true;
    }

    return false;
}

function pendingHasOldCorpseCandidate(candidate, presentRegistryIds) {
    const registryId = normalizeRegistryId(candidate.registry_id);

    if (registryId === "" || knownOldCorpseIds.has(registryId))
        return true;

    for (let i=0; i<pending.length; i++) {
        const corpse = pending[i];
        const pendingRegistryId = normalizeRegistryId(
            corpse.recovered_registry_id
        );

        if (pendingRegistryId === registryId) {
            applyStableCandidateToCorpse(corpse, candidate);
            rememberRegistryId(corpse, registryId);
            knownOldCorpseIds.add(registryId);
            return true;
        }
    }

    // Um animal visto morrer pelo Turbo Hunter já possui DNA completo. Aves e
    // corpos na água podem sair do ponto da morte antes do F6. Se o peso float
    // exato da estrutura viva coincide com um único DNA pendente, liga o ID a
    // esse mesmo animal e atualiza apenas a posição usada pelo waypoint.
    const stableWeightMatches = [];
    const candidateWeight = finiteNumber(candidate.stable_weight);
    if (candidateWeight !== null && candidateWeight > 0) {
        for (let i=0; i<pending.length; i++) {
            const corpse = pending[i];
            if (normalizeRegistryId(corpse.recovered_registry_id) !== "" ||
                strongCorpseDna(corpse) === "" ||
                !sameWeight(corpse.weight, candidateWeight))
                continue;
            stableWeightMatches.push(i);
        }
    }

    if (stableWeightMatches.length === 1) {
        const corpse = pending[stableWeightMatches[0]];
        applyStableCandidateToCorpse(corpse, candidate);
        knownOldCorpseIds.add(registryId);
        sendLabEvent("stable_id_linked_to_unique_dna_weight", {
            registry_id:registryId,
            dna:strongCorpseDna(corpse),
            stable_weight:candidateWeight,
            candidate:labSanitizeValue(candidate, 0),
            corpse:labSanitizeValue(corpse, 0)
        });
        log(
            "🧬 DNA + ID: peso exato ligou o registro " + registryId +
            " ao animal já contado."
        );
        return true;
    }

    for (let i=0; i<pending.length; i++) {
        const corpse = pending[i];
        const pendingRegistryId = normalizeRegistryId(
            corpse.recovered_registry_id
        );

        // Dois cadáveres antigos podem ocupar quase o mesmo ponto. Se ambos
        // já possuem identidade própria, coordenadas próximas não os juntam.
        if (pendingRegistryId !== "")
            continue;

        // Une o registro à morte que o Turbo Hunter acabou de observar, sem
        // criar um segundo ABATE para o mesmo animal.
        if (sameOldCorpsePosition(candidate, corpse)) {
            applyStableCandidateToCorpse(corpse, candidate);
            knownOldCorpseIds.add(registryId);
            return true;
        }
    }

    // Se o jogo trocou apenas o ID do registro, o ID anterior deixa de estar
    // na lista. Um único cadáver com DNA completo e a mesma posição pode então
    // receber o novo ID sem virar um segundo ABATE.
    const dnaAnchoredMatches = [];

    for (let i=0; i<pending.length; i++) {
        const corpse = pending[i];
        const oldRegistryId = normalizeRegistryId(
            corpse.recovered_registry_id
        );

        if (oldRegistryId === "" ||
            (presentRegistryIds && presentRegistryIds.has(oldRegistryId)) ||
            strongCorpseDna(corpse) === "" ||
            !sameOldCorpsePosition(candidate, corpse))
            continue;

        dnaAnchoredMatches.push(i);
    }

    if (dnaAnchoredMatches.length === 1) {
        const corpse = pending[dnaAnchoredMatches[0]];
        const oldRegistryId = normalizeRegistryId(
            corpse.recovered_registry_id
        );
        const dna = strongCorpseDna(corpse);

        rememberRegistryId(corpse, oldRegistryId);
        rememberRegistryId(corpse, registryId);
        applyStableCandidateToCorpse(corpse, candidate);
        knownOldCorpseIds.add(oldRegistryId);
        knownOldCorpseIds.add(registryId);

        sendLabEvent("registry_id_changed_dna_confirmed", {
            old_registry_id:oldRegistryId,
            new_registry_id:registryId,
            dna:dna,
            corpse:labSanitizeValue(corpse, 0),
            candidate:labSanitizeValue(candidate, 0)
        });
        log(
            `🧬 DNA CONFIRMOU O MESMO ANIMAL: ID ${oldRegistryId} -> ` +
            `${registryId}.`
        );
        return true;
    }

    return false;
}

function mergeInitialOldCorpseCandidates(entries) {
    if (entries === null)
        return;

    initialOldCorpseLastReadableAttempt = initialOldCorpseScanAttempts;
    const seenThisAttempt = new Set();

    for (let i=0; i<entries.length; i++) {
        const candidate = entries[i];
        const registryId = normalizeRegistryId(candidate.registry_id);

        if (registryId === "" || seenThisAttempt.has(registryId))
            continue;

        seenThisAttempt.add(registryId);

        let existingIndex = -1;

        for (let j=0; j<initialOldCorpseCandidates.length; j++) {
            if (normalizeRegistryId(
                initialOldCorpseCandidates[j].registry_id
            ) === registryId) {
                existingIndex = j;
                break;
            }
        }

        if (existingIndex < 0) {
            candidate.seen_count = 1;
            candidate.consecutive_count = 1;
            candidate.last_seen_attempt = initialOldCorpseScanAttempts;
            initialOldCorpseCandidates.push(candidate);
        } else {
            const existing = initialOldCorpseCandidates[existingIndex];
            candidate.seen_count = Number(existing.seen_count || 0) + 1;
            candidate.consecutive_count =
                Number(existing.last_seen_attempt) ===
                initialOldCorpseScanAttempts - 1
                    ? Number(existing.consecutive_count || 0) + 1
                    : 1;
            candidate.last_seen_attempt = initialOldCorpseScanAttempts;
            initialOldCorpseCandidates[existingIndex] = candidate;
        }
    }
}

function confirmedInitialOldCorpseCandidates() {
    if (initialOldCorpseLastReadableAttempt <= 0)
        return [];

    return initialOldCorpseCandidates.filter(function (candidate) {
        return Number(candidate.last_seen_attempt) ===
                initialOldCorpseLastReadableAttempt &&
            Number(candidate.consecutive_count || 0) >=
                INITIAL_SCAN_REQUIRED_CONSECUTIVE;
    });
}

function scanOldCorpseRegistryOnce() {
    const bounds = oldCorpseRegistryBounds();

    if (!bounds)
        return null;

    const accepted = [];
    const candidates = [];
    const rejected = [];
    let pointer = bounds.begin;
    let scanned = 0;

    while (pointer.compare(bounds.end) < 0 && scanned < OLD_CORPSE_MAX_SCAN) {
        const inspected = inspectOldCorpseDescriptor(pointer);

        if (inspected) {
            if (inspected.registry_id !== "" &&
                validCoord(inspected.x) && validCoord(inspected.y) &&
                validCoord(inspected.z))
                candidates.push(inspected);
            if (inspected.object_link_valid === true)
                accepted.push(inspected);
            else
                rejected.push(inspected);
        }

        pointer = pointer.add(OLD_CORPSE_DESCRIPTOR_SIZE);
        scanned++;
    }

    return {
        accepted:accepted,
        candidates:candidates,
        rejected:rejected
    };
}

function finishInitialOldCorpseScan(entries) {
    initialOldCorpseScanTimer = null;
    const expectedReserve = initialOldCorpseScanReserve;

    const started = startStableAvailabilityScan(
        entries || [],
        "busca_inicial_id_vivo",
        function (activeEntries, metadata) {
            if (scriptStopping || activeReserve !== expectedReserve)
                return;
            startDirectDnaStableRecovery(
                activeEntries,
                function (recoveredEntries, dnaMetadata) {
                    if (scriptStopping || activeReserve !== expectedReserve)
                        return;
                    metadata.direct_dna_recovery = dnaMetadata;
                    applyConfirmedOldCorpseEntries(
                        recoveredEntries,
                        metadata
                    );
                }
            );
        }
    );

    if (!started.ok && started.reason === "stable_scan_in_progress") {
        initialOldCorpseScanTimer = setTimeout(function () {
            finishInitialOldCorpseScan(entries);
        }, 500);
    }
}

function applyConfirmedOldCorpseEntries(entries, validationMetadata) {
    initialOldCorpseScanTimer = null;
    initialOldCorpseScanFinished = true;
    sendLabEvent("old_corpse_scan_finished", {
        confirmed_entries:labSanitizeValue(entries, 0),
        stable_validation:labSanitizeValue(validationMetadata || {}, 0),
        every_candidate:labSanitizeValue(initialOldCorpseCandidates, 0),
        rejected_in_last_read:labSanitizeValue(
            initialOldCorpseLastRejected,
            0
        ),
        last_readable_attempt:initialOldCorpseLastReadableAttempt,
        total_attempts:initialOldCorpseScanAttempts,
        known_registry_ids:Array.from(knownOldCorpseIds)
    });
    labScheduleRegistryBurst("busca_antiga_finalizada");
    initialOldCorpseCandidates = [];
    initialOldCorpseLastReadableAttempt = 0;
    initialOldCorpseLastRejected = [];

    let added = 0;
    const presentRegistryIds = new Set(entries.map(function (entry) {
        return normalizeRegistryId(entry.registry_id);
    }).filter(function (value) { return value !== ""; }));

    for (let i=0; i<entries.length; i++) {
        const found = entries[i];
        const registryId = normalizeRegistryId(found.registry_id);

        if (found.recovered_from_alternative_layout === true
                ? pendingHasAlternativeDnaCandidate(found)
                : pendingHasOldCorpseCandidate(found, presentRegistryIds))
            continue;

        knownOldCorpseIds.add(registryId);
        const dnaTarget = found.dna_target &&
            strongCorpseDna(found.dna_target) !== ""
                ? found.dna_target : null;
        const preserveDeathPosition =
            found.recovered_directly_by_dna === true && dnaTarget &&
            finiteNumber(dnaTarget.x) !== null &&
            finiteNumber(dnaTarget.y) !== null &&
            finiteNumber(dnaTarget.z) !== null &&
            validCoord(Number(dnaTarget.x)) &&
            validCoord(Number(dnaTarget.y)) &&
            validCoord(Number(dnaTarget.z));
        const recoveredCorpse = {
            reserve:activeReserve,
            species:dnaTarget ? dnaTarget.species : null,
            weight:dnaTarget
                ? finiteNumber(dnaTarget.weight)
                : finiteNumber(found.stable_weight),
            gender:dnaTarget ? dnaTarget.gender : null,
            difficulty:dnaTarget ? dnaTarget.difficulty : null,
            x:preserveDeathPosition
                ? finiteNumber(dnaTarget.x)
                : (finiteNumber(found.stable_record_x) !== null
                    ? finiteNumber(found.stable_record_x) : found.x),
            y:preserveDeathPosition
                ? finiteNumber(dnaTarget.y)
                : (finiteNumber(found.stable_record_y) !== null
                    ? finiteNumber(found.stable_record_y) : found.y),
            z:preserveDeathPosition
                ? finiteNumber(dnaTarget.z)
                : (finiteNumber(found.stable_record_z) !== null
                    ? finiteNumber(found.stable_record_z) : found.z),
            special_tag:"",
            recovered_from_startup:true,
            recovered_registry_id:registryId,
            recovered_registry_id_history:registryId !== ""
                ? [registryId] : [],
            recovered_registry_descriptor:found.descriptor,
            recovered_object_link:found.object_link || "",
            recovered_stable_record_address:String(
                found.recovered_from_alternative_layout === true
                    ? "" : (found.stable_record_address || "")
            ),
            recovered_stable_range_base:String(
                found.recovered_from_alternative_layout === true
                    ? "" : (found.stable_record_range_base || "")
            ),
            recovered_from_alternative_layout:
                found.recovered_from_alternative_layout === true,
            recovered_alternative_record_address:String(
                found.alternative_record_address || ""
            ),
            recovered_alternative_range_base:String(
                found.alternative_record_range_base || ""
            ),
            recovered_availability:Number(
                found.stable_availability
            ),
            recovered_lifecycle:Number(found.stable_lifecycle),
            recovered_live_pointer_count:Number(
                found.stable_nonzero_pointers || 0
            ),
            recovered_readable_pointer_count:Number(
                found.stable_readable_pointers || 0
            ),
            recovered_directly_by_dna:
                found.recovered_directly_by_dna === true,
            time:Date.now()
        };
        pending.push(recoveredCorpse);
        sendLabEvent("old_corpse_added_to_pending", {
            registry_id:registryId,
            descriptor:found.descriptor,
            object_link:found.object_link || "",
            x:found.x,
            y:found.y,
            z:found.z,
            dna:dnaTarget ? strongCorpseDna(dnaTarget) : "",
            recovered_directly_by_dna:
                found.recovered_directly_by_dna === true,
            stable_record:labSanitizeValue(found, 0),
            pending:labPendingSnapshot()
        });
        added++;
    }

    emitRuntimeState("busca de cadáveres antigos concluída");

    if (added <= 0) {
        if (pending.length > 0) {
            finalClearDone = false;
            if (!PROTECT_SETWAYPOINT)
                manualWaypointOverride = false;
            updateHudWarningForCount();
            updateHudState();
            scheduleNearestUpdate(100, "IDs existentes reconfirmados");
            log(
                "✅ ID VIVO: nenhum abate duplicado; " + pending.length +
                " pendente(s) reconfirmado(s)."
            );
            sendGpsStatus(
                "stable_reconfirmed",
                pending.length + " abate(s) reconfirmado(s)"
            );
            scheduleCollectionDebugArm(
                700,
                "IDs existentes reconfirmados"
            );
        } else {
            log("✅ TURBO HUNTER PRONTO.");
            sendGpsStatus("ready", "busca inicial concluida");
        }
        deathSignalPrepare("busca inicial concluída");
        return;
    }

    finalClearDone = false;

    if (!PROTECT_SETWAYPOINT)
        manualWaypointOverride = false;

    updateHudWarningForCount();
    updateHudState();
    log(`🔎 ABATES ANTERIORES ENCONTRADOS: ${added}.`);
    sendGpsStatus("recovered_previous", `${added} abate(s) anterior(es)`);
    scheduleNearestUpdate(220, "abate anterior encontrado");
    scheduleCollectionDebugArm(700, "abate próximo confirmado");
    deathSignalPrepare("busca inicial concluída");
}

function runInitialOldCorpseScanAttempt() {
    initialOldCorpseScanTimer = null;

    if (scriptStopping || multiplayerBlocked || !soloConfirmed) {
        initialOldCorpseScanFinished = true;
        return;
    }

    if (activeReserve === "") {
        finishInitialOldCorpseScan([]);
        return;
    }

    // Se o jogo terminou de trocar/carregar a reserva durante a busca,
    // descarta apenas as leituras provisórias e começa a janela de novo.
    if (activeReserve !== initialOldCorpseScanReserve) {
        initialOldCorpseScanReserve = activeReserve;
        initialOldCorpseScanAttempts = 0;
        initialOldCorpseCandidates = [];
        initialOldCorpseLastReadableAttempt = 0;
        initialOldCorpseLastRejected = [];
        initialOldCorpseScanTimer = setTimeout(
            runInitialOldCorpseScanAttempt,
            INITIAL_SCAN_START_DELAY_MS
        );
        return;
    }

    initialOldCorpseScanAttempts++;
    const scan = scanOldCorpseRegistryOnce();
    const entries = scan === null ? null : scan.candidates;

    if (scan !== null)
        initialOldCorpseLastRejected = scan.rejected;

    // O mesmo ID precisa aparecer em leituras consecutivas e continuar presente
    // na última lista válida. Isso ignora valores transitórios durante uma
    // reorganização e também evita guardar um animal coletado no meio da busca.
    mergeInitialOldCorpseCandidates(entries);

    if (initialOldCorpseScanAttempts < INITIAL_SCAN_MAX_ATTEMPTS) {
        initialOldCorpseScanTimer = setTimeout(
            runInitialOldCorpseScanAttempt,
            INITIAL_SCAN_INTERVAL_MS
        );
        return;
    }

    finishInitialOldCorpseScan(confirmedInitialOldCorpseCandidates());
}

function scheduleInitialOldCorpseScan() {
    if (BULK_STARTUP_RECOVERY) {
        scheduleBulkDeadBodyScan(1000, false, "reserva pronta");
        return;
    }
    if (initialOldCorpseScanStarted || initialOldCorpseScanFinished ||
        scriptStopping || multiplayerBlocked || !soloConfirmed ||
        activeReserve === "")
        return;

    initialOldCorpseScanStarted = true;
    initialOldCorpseScanAttempts = 0;
    initialOldCorpseScanReserve = activeReserve;
    initialOldCorpseCandidates = [];
    initialOldCorpseLastReadableAttempt = 0;
    initialOldCorpseLastRejected = [];
    updateHudState();
    log("🔎 PROCURANDO ABATES ANTERIORES...");
    sendLabEvent("old_corpse_scan_started", {
        reserve:activeReserve,
        automatic:true,
        pending_before:labPendingSnapshot()
    });
    labScheduleRegistryBurst("busca_antiga_iniciada");
    initialOldCorpseScanTimer = setTimeout(
        runInitialOldCorpseScanAttempt,
        INITIAL_SCAN_START_DELAY_MS
    );
}

function forceOldCorpseScan() {
    if (BULK_STARTUP_RECOVERY)
        return forceBulkDeadBodyScan();
    if (scriptStopping)
        return {ok:false, reason:"stopped"};

    if (multiplayerBlocked)
        return {ok:false, reason:"multiplayer_blocked"};

    if (!soloConfirmed)
        return {ok:false, reason:"solo_not_ready"};

    if (activeReserve === "")
        return {ok:false, reason:"reserve_not_ready"};

    if (initialOldCorpseScanStarted && !initialOldCorpseScanFinished)
        return {ok:false, reason:"search_in_progress"};

    initialOldCorpseScanTimer = clearTimeoutSafe(initialOldCorpseScanTimer);
    initialOldCorpseScanStarted = false;
    initialOldCorpseScanFinished = false;
    initialOldCorpseScanAttempts = 0;
    initialOldCorpseScanReserve = "";
    initialOldCorpseCandidates = [];
    initialOldCorpseLastReadableAttempt = 0;
    initialOldCorpseLastRejected = [];
    waypointRecoverySerial++;
    waypointClearInstructionIssued = false;
    hudGpsActionRequired = false;
    cancelWaypointWork();
    switchingKey = "";
    currentKey = "";
    sendLabEvent("f6_force_scan", {
        pending_before:labPendingSnapshot(),
        player:getPlayerPos()
    });
    labScheduleRegistryBurst("f6");
    scheduleInitialOldCorpseScan();

    return {ok:true, pending:pending.length};
}

function findNearestIndex(pp) {
    if (!pending.length)
        return -1;

    // Um ID que acaba de desaparecer do catálogo não deve voltar ao mapa
    // durante a revalidação. Se o jogo devolver o ID, o monitor o libera.
    const selectable = [];
    for (let i=0; i<pending.length; i++) {
        const id = normalizeRegistryId(pending[i].recovered_registry_id);
        if (pending[i].recovered_by_registry_catalog === true &&
            labRecentlyDisappearedCatalogId(id))
            continue;
        selectable.push(i);
    }
    if (!selectable.length)
        return -1;

    if (preferredWaypointKey !== "") {
        for (let i=0; i<selectable.length; i++) {
            const index = selectable[i];
            if (corpseKey(pending[index]) === preferredWaypointKey)
                return index;
        }
        preferredWaypointKey = "";
    }

    if (COLLECTION_DEBUG_MODE) {
        for (let i=0; i<selectable.length; i++) {
            const index = selectable[i];
            if (pending[index].recovered_from_alternative_layout === true &&
                strongCorpseDna(pending[index]) === COLLECTION_DEBUG_TARGET_DNA)
                return index;
        }
    }

    // IDs do catálogo sem espécie/DNA podem apontar para pista antiga. Quando
    // há um corpo com atributos confirmados, indique esse corpo primeiro;
    // os IDs incertos continuam no contador para análise posterior.
    const confirmed = [];
    for (let i=0; i<selectable.length; i++) {
        const index = selectable[i];
        if (pending[index].species !== null &&
            pending[index].species !== undefined &&
            finiteNumber(pending[index].weight) !== null &&
            pending[index].gender !== null &&
            pending[index].gender !== undefined)
            confirmed.push(index);
    }
    const choices = confirmed.length ? confirmed : selectable;
    if (!pp && currentKey !== "") {
        for (const index of choices) {
            if (corpseKey(pending[index]) === currentKey)
                return index;
        }
    }
    const reference = pp || lastNavigationAnchor;

    if (!reference)
        return choices[choices.length - 1];

    let best = choices[0];
    let bestD = distSqXZ(reference, pending[best]);
    for (let i=1; i<choices.length; i++) {
        const candidate = choices[i];
        const d = distSqXZ(reference, pending[candidate]);

        if (d < bestD) {
            bestD = d;
            best = candidate;
        }
    }

    // Preserve a marcação atual só quando a diferença é imperceptível.
    // A margem anterior mantinha um corpo a 80 m mesmo com outro a 60 m.
    if (pp && currentKey !== "") {
        const current = choices.find(function (index) {
            return corpseKey(pending[index]) === currentKey;
        });
        if (current !== undefined && current !== best) {
            const currentD = Math.sqrt(distSqXZ(pp, pending[current]));
            const alternativeD = Math.sqrt(bestD);
            if (currentD - alternativeD < 2)
                return current;
        }
    }

    return best;
}

function rememberNavigationAnchor(corpse, reason) {
    if (!corpse || finiteNumber(corpse.x) === null ||
        finiteNumber(corpse.y) === null ||
        finiteNumber(corpse.z) === null)
        return false;

    lastNavigationAnchor = {
        x:Number(corpse.x),
        y:Number(corpse.y),
        z:Number(corpse.z),
        at:Date.now(),
        reason:String(reason || "último corpo coletado"),
        key:corpseKey(corpse)
    };
    sendLabEvent("nearest_navigation_anchor_updated", {
        anchor:lastNavigationAnchor,
        real_player_position_available:getPlayerPos() !== null
    });
    return true;
}

// -----------------------------------------------------------
// MARCADOR
// -----------------------------------------------------------

function clearMarkerInternal() {
    if (!capturedClearCheckDone) {
        capturedClearCheckDone = true;
        let reason = "";
        try {
            validateNativeRva("ClearWaypoint capturado",
                RVA_CAPTURED_CLEAR_WAYPOINT, CAPTURED_CLEAR_ADDR, true);
            if (labHex(CAPTURED_CLEAR_ADDR,
                    CAPTURED_CLEAR_SIGNATURE.length / 2) !==
                    CAPTURED_CLEAR_SIGNATURE)
                reason = "assinatura do código não coincide com a captura";
            else
                capturedClearWaypoint = new NativeFunction(
                    CAPTURED_CLEAR_ADDR, 'void', ['pointer']
                );
        } catch (error) {
            reason = String(error);
        }
        capturedClearRejected = capturedClearWaypoint === null;
        sendLabEvent("waypoint_captured_clear_preflight", {
            ready:!capturedClearRejected,
            address:pstr(CAPTURED_CLEAR_ADDR),
            rva:"0x" + RVA_CAPTURED_CLEAR_WAYPOINT.toString(16),
            expected_hex:CAPTURED_CLEAR_SIGNATURE,
            actual_hex:labHex(CAPTURED_CLEAR_ADDR,
                CAPTURED_CLEAR_SIGNATURE.length / 2),
            error:reason
        });
    }

    if (capturedClearRejected || capturedClearWaypoint === null) {
        sendLabEvent("waypoint_captured_clear_skipped", {
            reason:"assinatura da função mudou nesta build",
            current_key:currentKey,
            pending:labPendingSnapshot()
        });
        return false;
    }

    const map = getMap();

    if (map.isNull() || autoMapVerifiedPointer !== pstr(map))
        return false;

    let snapshot = null;
    try { snapshot = labCollectRegistry(false); }
    catch (_) {}
    const waypointEntries = snapshot && snapshot.ok === true &&
        Array.isArray(snapshot.waypoint_entries)
        ? snapshot.waypoint_entries.filter(function (entry) {
            return registryWaypointPointer(entry) !== null;
        }) : [];
    const mapFlag = labHex(map.add(0x3d3), 1);
    if (mapFlag === "00")
        return true;
    if (waypointEntries.length !== 1 || mapFlag !== "01")
        return false;

    try {
        beginInternalHookWindow();
        insideClear = true;
        capturedClearWaypoint(map);
        const cleared = labHex(map.add(0x3d3), 1) === "00";
        sendLabEvent("waypoint_captured_clear_called", {
            address:pstr(CAPTURED_CLEAR_ADDR),
            map_pointer:pstr(map),
            previous_waypoint:labSanitizeValue(waypointEntries[0], 0),
            map_flag_zero:cleared,
            current_key:currentKey,
            pending:labPendingSnapshot()
        });
        return cleared;
    } catch (error) {
        capturedClearRejected = true;
        sendLabEvent("waypoint_captured_clear_failed", {
            address:pstr(CAPTURED_CLEAR_ADDR), error:String(error)
        });
        return false;
    } finally {
        insideClear = false;
        beginInternalHookWindow();
    }
}

function finishSet(index, wantedKey, serial, reason) {
    if (multiplayerBlocked || scriptStopping)
        return;

    if (serial !== switchSerial)
        return;

    switchTimer = null;

    index = pending.findIndex(function (item) {
        return corpseKey(item) === wantedKey;
    });
    if (index < 0) {
        switchingKey = "";
        scheduleNearestUpdate(40, "destino removido durante troca de marcação");
        return;
    }
    const c = pending[index];

    const map = getMap();
    logAutoWaypointPreflight("cadáver pendente", false);

    sendLabEvent("ray_x_waypoint_attempt", {
        requested_index:index,
        requested_key:wantedKey,
        reason:String(reason || ""),
        target:labSanitizeValue(c, 0),
        target_source:rayXPendingSource(c),
        target_has_animal_data:c.species !== null &&
            c.species !== undefined &&
            finiteNumber(c.weight) !== null,
        map_pointer:pstr(map),
        captured_map_pointer:pstr(capturedMap),
        singleton_pointer:pstr(getSingletonMap()),
        reference_map_slot:pstr(MAP_SLOT),
        active_map_slot:pstr(activeMapSlot),
        set_waypoint_address:waypointConfirmedSetterAddress,
        set_waypoint_function_ready:autoWaypointSetter !== null,
        map_unique_reference_confirmed:autoMapVerifiedPointer === pstr(map),
        player:getPlayerPos(),
        pending:labPendingSnapshot()
    });

    if (map.isNull() || autoWaypointSetter === null ||
        autoMapVerifiedPointer !== pstr(map)) {
        switchingKey = "";
        if (autoWaypointMissingSince === 0)
            autoWaypointMissingSince = Date.now();
        if (!map.isNull() &&
            autoMapVerifiedPointer !== pstr(map))
            rayXDiscoverMapSingletonSlots(
                map, "confirmar mapa sem clique manual", false
            );
        scanWaypointIdConstantReferences(
            "confirmar função de criação sem clique manual"
        );
        if (waypointIdScanFinished && autoWaypointSetter === null &&
            Date.now() - rayXLastMapNullLogAt > 15000) {
            rayXLastMapNullLogAt = Date.now();
            log("🟠 LAB: ERRO DE MARCAÇÃO. PARE E ENVIE O ZIP DO LOG.");
        } else if (waypointIdScanFinished &&
            Date.now() - autoWaypointMissingSince > 35000 &&
            Date.now() - rayXLastMapNullLogAt > 15000) {
            rayXLastMapNullLogAt = Date.now();
            sendLabEvent("waypoint_auto_map_unavailable", {
                validated_map:pstr(map),
                raw_map_slot:labHex(AUTO_MAP_SLOT, 16),
                unique_reference_pointer:autoMapVerifiedPointer,
                setter_ready:autoWaypointSetter !== null
            });
            log("🟠 LAB: ERRO DE MARCAÇÃO. PARE E ENVIE O ZIP DO LOG.");
        }
        return;
    }

    // O setter confirmado só cria um ponto quando a flag está livre. Um
    // marcador herdado de outra execução fica preso no mapa até ser limpo.
    // Usar exclusivamente a rotina capturada com assinatura exata.
    let oldWaypoint = null;
    try {
        const snapshot = labCollectRegistry(false);
        if (snapshot.ok === true &&
            Array.isArray(snapshot.waypoint_entries) &&
            snapshot.waypoint_entries.length === 1)
            oldWaypoint = snapshot.waypoint_entries[0];
    } catch (_) {}
    if (labHex(map.add(0x3d3), 1) === "01" &&
        ((!PROTECT_SETWAYPOINT && markerOwned) ||
         (currentKey !== "" && currentKey !== wantedKey) ||
         (oldWaypoint && registryWaypointPointer(oldWaypoint) !== null &&
          (distanceXZ(oldWaypoint, c) > 3 ||
           Math.abs(Number(oldWaypoint.y) - Number(c.y)) > 6) &&
          (!PROTECT_SETWAYPOINT || markerOwned)))) {
        const cleared = clearMarkerInternal();
        sendLabEvent("waypoint_stale_before_set", {
            old_waypoint:labSanitizeValue(oldWaypoint, 0),
            target:labSanitizeValue(c, 0),
            native_clear_succeeded:cleared
        });
        if (cleared) {
            switchTimer = setTimeout(function () {
                finishSet(index, wantedKey, serial, reason);
            }, 350);
            return;
        }
    }

    autoWaypointMissingSince = 0;
    rayXWaitingForManualWaypoint = false;
    hudGpsActionRequired = false;
    autoWaypointAttemptedKey = wantedKey;
    autoWaypointAwaitingRegistry = wantedKey;

    try {
        hudGpsActionRequired = false;
        updateHudState();
        WAYPOINT_VECTOR.writeFloat(Number(c.x));
        WAYPOINT_VECTOR.add(4).writeFloat(Number(c.y));
        WAYPOINT_VECTOR.add(8).writeFloat(Number(c.z));

        // Preserva a assinatura espacial mesmo se o cadáver for coletado
        // antes de um callback atrasado do próprio SetWaypoint chegar.
        lastInternalSetPosition = {
            x:Number(c.x),
            y:Number(c.y),
            z:Number(c.z)
        };
        lastInternalSetAt = Date.now();

        beginInternalHookWindow();
        insideSet = true;

        try {
            // Chamada da assinatura da build atual, nunca do RVA antigo.
            autoWaypointSetter(map, WAYPOINT_VECTOR);
        } finally {
            insideSet = false;
            beginInternalHookWindow();
        }

        currentIndex = index;
        currentKey = wantedKey;
        if (wantedKey === preferredWaypointKey)
            preferredWaypointKey = "";
        switchingKey = "";
        markerOwned = true;

        const pp = getPlayerPos();
        const d = pp ? distanceXZ(pp, c) : null;

        log(`📍 GPS SOLICITADO -> ${corpseDesc(c)}` +
            (d !== null ? ` | distância≈${d.toFixed(1)}m` : "") +
            ` | pendentes=${pending.length} | ${reason}`);
        stopHudCountdown("GPS do cadáver aplicado");

        sendLabEvent("waypoint_auto_native_called", {
            requested_index:index,
            requested_key:wantedKey,
            reason:String(reason || ""),
            target:labSanitizeValue(c, 0),
            target_source:rayXPendingSource(c),
            map_pointer:pstr(map),
            player:pp,
            distance_to_player_m:d,
            pending:labPendingSnapshot()
        });
        // A função pode retornar mesmo quando a UI ainda não criou o pin.
        // O catálogo confirmará a posição após o próximo quadro do jogo.
        setTimeout(function () {
            verifyAutoWaypointInRegistry(wantedKey, c, "após 1500 ms", serial);
        }, 1500);

    } catch (e) {
        autoWaypointAwaitingRegistry = "";
        switchingKey = "";
        // Uma exceção nativa invalida a chamada nesta sessão; só o próximo
        // ZIP poderá determinar se a assinatura mudou.
        autoWaypointSetter = null;
        log("🟠 LAB: ERRO DE MARCAÇÃO. PARE E ENVIE O ZIP DO LOG.");
        sendLabEvent("ray_x_waypoint_set_failed", {
            requested_index:index,
            requested_key:wantedKey,
            reason:String(reason || ""),
            target:labSanitizeValue(c, 0),
            map_pointer:pstr(map),
            error:String(e),
            set_waypoint_code_hex:labHex(
                ptr(waypointConfirmedSetterAddress), 64
            )
        });
    }
}

// O catálogo pode manter uma entrada antiga depois de limpar o ponto.
// Nunca confundir posição no catálogo com a marcação ativa na interface.
const waypointInactiveRetries = {};
function verifyAutoWaypointInRegistry(key, target, reason, serial) {
    if (scriptStopping || serial !== switchSerial || currentKey !== key ||
        !pending.some(function (item) { return corpseKey(item) === key; }) ||
        autoWaypointAwaitingRegistry !== key)
        return;
    autoWaypointAwaitingRegistry = "";
    autoWaypointMissingSince = 0;
    const map = getMap();
    const mapValid = !map.isNull() &&
        autoMapVerifiedPointer === pstr(map);
    const flag = mapValid ? labHex(map.add(0x3d3), 1) : "";
    let entries = [];
    let error = "";
    try {
        const snapshot = labCollectRegistry(false);
        entries = snapshot.ok === true &&
            Array.isArray(snapshot.waypoint_entries)
            ? snapshot.waypoint_entries : [];
    } catch (e) {
        error = String(e);
    }
    const matches = [];
    for (let i=0; i<entries.length; i++) {
        const entry = entries[i];
        if (registryWaypointPointer(entry) === null)
            continue;
        if (distanceXZ(entry, target) < 3 &&
            Math.abs(Number(entry.y) - Number(target.y)) < 6)
            matches.push(entry);
    }
    const entryMatched = matches.length === 1;
    const active = mapValid && flag === "01";
    const verified = entryMatched && active;
    sendLabEvent("waypoint_auto_registry_verified", {
        reason:reason,
        requested_key:key,
        target:labSanitizeValue(target, 0),
        target_source:rayXPendingSource(target),
        matches:labSanitizeValue(matches, 0),
        waypoint_entries:labSanitizeValue(entries, 0),
        error:error,
        map_pointer:pstr(map),
        map_pointer_validated:mapValid,
        map_flag_hex:flag,
        catalog_position_matched:entryMatched,
        map_active:active,
        visual_icon_confirmed:false,
        verified:verified
    });
    if (verified) {
        delete waypointInactiveRetries[key];
        return;
    }
    if (mapValid && flag === "00" && entryMatched) {
        const retries = waypointInactiveRetries[key] || 0;
        if (retries < 2) {
            waypointInactiveRetries[key] = retries + 1;
            sendLabEvent("waypoint_inactive_retry_scheduled", {
                target_key:key, attempt:retries + 1,
                map_flag_hex:flag, catalog_position_matched:true
            });
            resetMarkerState();
            scheduleNearestUpdate(500, "ponto inativo; reaplicar uma vez");
            return;
        }
        waypointClearInstructionIssued = true;
        hudGpsActionRequired = true;
        updateHudState();
        sendLabEvent("waypoint_inactive_retry_exhausted", {
            target_key:key, attempts:retries,
            map_flag_hex:flag, catalog_position_matched:true
        });
        log("🟠 LAB: MARCAÇÃO INATIVA APÓS DUAS TENTATIVAS. " +
            "CLIQUE PARAR E ENVIE O ZIP DO LOG.");
        return;
    }
    if (entries.length) {
        requestWaypointClearCapture(
            "o mapa não confirmou a nova marcação ativa", target
        );
    } else {
        log("🟠 LAB: ERRO DE MARCAÇÃO. PARE E ENVIE O ZIP DO LOG.");
    }
}

function switchToIndex(index, reason) {
    if (multiplayerBlocked || scriptStopping || !soloConfirmed)
        return;

    if (index < 0 || index >= pending.length)
        return;

    const wantedKey = corpseKey(pending[index]);

    if (wantedKey === currentKey) {
        if (switchingKey !== "") {
            ++switchSerial;
            switchTimer = clearTimeoutSafe(switchTimer);
            switchingKey = "";
        }
        currentIndex = index;
        return;
    }
    if (wantedKey === switchingKey) {
        currentIndex = index;
        return;
    }

    switchingKey = wantedKey;
    const serial = ++switchSerial;
    switchTimer = clearTimeoutSafe(switchTimer);

    // Preserve o ponto anterior enquanto validamos o alvo e o mapa.
    // finishSet limpa o pin somente quando o novo alvo estiver pronto.
    switchTimer = setTimeout(function () {
        finishSet(index, wantedKey, serial, reason);
    }, 140);
}

function clearOwn(reason, forceLog) {
    const hadMarker = markerOwned || currentKey !== "";
    const hadWork = hadMarker || switchingKey !== "" || switchTimer !== null ||
        externalReapplyTimer !== null || nearestUpdateTimer !== null;

    if (!hadWork && finalClearDone)
        return;

    cancelWaypointWork();

    let markerCleared = !hadMarker;
    if (hadMarker && !finalClearDone) {
        const restored = restoreRegistryWaypointFallback(reason);
        markerCleared = restored || clearMarkerInternal();
        if (!markerCleared) {
            sendLabEvent("waypoint_auto_clear_unavailable", {
                reason:String(reason || ""),
                pending:labPendingSnapshot(),
                current_key:currentKey,
                captured_clear_function_validated:
                    capturedClearCheckDone && !capturedClearRejected
            });
        }
    }

    resetMarkerState();
    finalClearDone = true;

    if (pending.length === 0 && lastInternalSetPosition !== null) {
        try {
            const snapshot = labCollectRegistry(false);
            if (snapshot.ok === true &&
                Array.isArray(snapshot.waypoint_entries) &&
                snapshot.waypoint_entries.some(function (entry) {
                    return registryWaypointPointer(entry) !== null;
                })) {
                markerCleared = false;
                const map = getMap();
                const flag = map.isNull() ? "" :
                    labHex(map.add(0x3d3), 1);
                sendLabEvent(flag === "00"
                    ? "waypoint_clear_registry_settling"
                    : "waypoint_clear_still_visible", {
                    reason:String(reason || ""),
                    map_flag_hex:flag,
                    waypoint_entries:labSanitizeValue(
                        snapshot.waypoint_entries, 0
                    )
                });
                if (flag !== "00" && !scriptStopping)
                    log("🟠 LAB: ERRO AO LIMPAR MARCAÇÃO. " +
                        "PARE E ENVIE O ZIP DO LOG.");
            } else if (snapshot.ok === true) {
                markerCleared = true;
            }
        } catch (_) {}
    }

    if ((hadWork || forceLog) && markerCleared)
        log(`🧹 GPS limpo (${reason}).`);
}

function scheduleNearestUpdate(delayMs, reason) {
    if (multiplayerBlocked || scriptStopping)
        return;

    nearestUpdateTimer = clearTimeoutSafe(nearestUpdateTimer);
    nearestUpdateTimer = setTimeout(function () {
        nearestUpdateTimer = null;
        updateNearest(reason);
    }, delayMs);
}

function updateNearest(reason) {
    if (multiplayerBlocked || scriptStopping || !soloConfirmed)
        return;

    // Esperar a limpeza manual capturada antes de pedir novo ponto ao jogo.
    if (waypointClearInstructionIssued)
        return;

    // Com a proteção de waypoint ATIVA, um waypoint manual pertence ao jogador.
    // O Turbo Hunter só volta a mover o GPS depois que o jogador limpar esse point.
    if (PROTECT_SETWAYPOINT && manualWaypointOverride)
        return;

    if (!pending.length) {
        clearOwn("sem cadáver pendente");
        return;
    }

    const pp = getPlayerPos();
    // Sem posição real não escolher por ordem de registro nem pela localização
    // de outra coleta: isso pode marcar um corpo distante como "mais próximo".
    if (pp === null)
        return;
    const idx = findNearestIndex(pp);

    if (idx >= 0)
        switchToIndex(idx, reason || "mais proximo mudou");
}

// Mais leve que a 0.3.
// Apenas leitura de 3 floats do ponteiro cacheado na maioria das vezes.
nearestInterval = setInterval(function () {
    try {
        updateNearest("mais proximo mudou ao caminhar");
    } catch (_) {}
}, 1000);

// -----------------------------------------------------------
// MORTE / COLETA
// -----------------------------------------------------------

function rayXPendingSource(corpse) {
    if (corpse.recovered_by_registry_catalog === true)
        return "catalogo_tipo39";
    if (corpse.recovered_from_clue === true)
        return "pista_morta";
    if (corpse.recovered_from_startup === true)
        return "memoria_inicial";
    return "morte_observada";
}

function rayXAssociationMatrix(candidate, player) {
    const rows = [];
    const candidateHasPosition = candidate &&
        finiteNumber(candidate.x) !== null &&
        finiteNumber(candidate.y) !== null &&
        finiteNumber(candidate.z) !== null;

    for (let i=0; i<pending.length; i++) {
        const corpse = pending[i];
        const distance = candidateHasPosition
            ? distanceXZ(candidate, corpse) : null;
        const deltaY = candidateHasPosition
            ? Math.abs(Number(candidate.y) - Number(corpse.y)) : null;
        const radiusFlags = {};

        for (let j=0; j<RAY_X_CLUE_RADII_M.length; j++) {
            const radius = RAY_X_CLUE_RADII_M[j];
            radiusFlags["within_" + radius + "m"] =
                distance !== null && distance <= radius &&
                deltaY !== null && deltaY <= 6.0;
        }

        rows.push({
            pending_index:i,
            key:corpseKey(corpse),
            source:rayXPendingSource(corpse),
            registry_id:normalizeRegistryId(corpse.recovered_registry_id),
            animal_id:String(corpse.recovered_animal_id || ""),
            x:Number(corpse.x),
            y:Number(corpse.y),
            z:Number(corpse.z),
            distance_to_candidate_xz_m:distance,
            delta_y_m:deltaY,
            distance_to_player_m:player
                ? distanceXZ(player, corpse) : null,
            same_species:sameSpecies(corpse.species, candidate.species),
            same_weight:sameWeight(corpse.weight, candidate.weight),
            same_gender:sameValue(corpse.gender, candidate.gender),
            same_difficulty:sameValue(
                corpse.difficulty,
                candidate.difficulty
            ),
            strong_dna:String(strongCorpseDna(corpse) || ""),
            strict_old_position_match:
                corpse.recovered_from_startup === true &&
                candidateHasPosition &&
                sameOldCorpsePosition(corpse, candidate),
            radius_flags:radiusFlags
        });
    }

    rows.sort(function (left, right) {
        const leftDistance = left.distance_to_candidate_xz_m;
        const rightDistance = right.distance_to_candidate_xz_m;
        if (leftDistance === null && rightDistance === null)
            return left.pending_index - right.pending_index;
        if (leftDistance === null)
            return 1;
        if (rightDistance === null)
            return -1;
        return leftDistance - rightDistance;
    });
    return rows;
}

function rayXClueHypothesis(candidate, matrix, strictMatch) {
    const nearest = matrix.length ? matrix[0] : null;
    const second = matrix.length > 1 ? matrix[1] : null;
    const nearestDistance = nearest
        ? finiteNumber(nearest.distance_to_candidate_xz_m) : null;
    const secondDistance = second
        ? finiteNumber(second.distance_to_candidate_xz_m) : null;
    const uniqueNearbyCatalog = !!(
        nearest && nearest.source === "catalogo_tipo39" &&
        nearestDistance !== null &&
        nearestDistance <= RAY_X_PROBABLE_DUPLICATE_MAX_M &&
        finiteNumber(nearest.delta_y_m) !== null &&
        Number(nearest.delta_y_m) <= 6.0 &&
        (secondDistance === null || secondDistance - nearestDistance >= 5.0)
    );

    return {
        current_code_matched:strictMatch === true,
        nearest:nearest,
        second_nearest:second,
        unique_nearby_catalog:uniqueNearbyCatalog,
        probable_duplicate_missed_by_tolerance:
            !strictMatch && uniqueNearbyCatalog,
        predicted_current_action:strictMatch
            ? "usar entrada existente"
            : "adicionar nova entrada",
        note:!strictMatch && uniqueNearbyCatalog
            ? "pista muito próxima de um único ID do catálogo; possível duplicação"
            : "sem conclusão automática"
    };
}

function rawClueCatalogAssociation(payload) {
    const cluePosition = {
        x:finiteNumber(payload ? payload.x : null),
        y:finiteNumber(payload ? payload.y : null),
        z:finiteNumber(payload ? payload.z : null)
    };
    const comparisons = [];
    const matches = [];

    if (cluePosition.x === null || cluePosition.y === null ||
        cluePosition.z === null || !validCoord(cluePosition.x) ||
        !validCoord(cluePosition.y) || !validCoord(cluePosition.z)) {
        return {
            valid:false,
            position:cluePosition,
            comparisons:comparisons,
            match_count:0,
            unique_index:-1,
            reason:"coordenadas da pista ausentes ou inválidas"
        };
    }

    const payloadReserve = normalizeReserve(
        payload ? payload.reserve : ""
    );

    for (let i=0; i<pending.length; i++) {
        const corpse = pending[i];

        if (corpse.recovered_by_registry_catalog !== true ||
            normalizeRegistryId(corpse.recovered_registry_id) === "")
            continue;

        const corpseReserve = normalizeReserve(corpse.reserve);
        if (payloadReserve !== "" && corpseReserve !== "" &&
            payloadReserve !== corpseReserve)
            continue;

        const distance = distanceXZ(cluePosition, corpse);
        const deltaY = Math.abs(
            Number(cluePosition.y) - Number(corpse.y)
        );
        const matched = distance <= RAW_CLUE_CATALOG_MAX_XZ_M &&
            deltaY <= RAW_CLUE_CATALOG_MAX_Y_M;
        const comparison = {
            pending_index:i,
            key:corpseKey(corpse),
            registry_id:normalizeRegistryId(
                corpse.recovered_registry_id
            ),
            catalog_position:{
                x:Number(corpse.x),
                y:Number(corpse.y),
                z:Number(corpse.z)
            },
            distance_xz_m:distance,
            delta_y_m:deltaY,
            matched:matched
        };
        comparisons.push(comparison);
        if (matched)
            matches.push(comparison);
    }

    matches.sort(function (left, right) {
        if (left.distance_xz_m !== right.distance_xz_m)
            return left.distance_xz_m - right.distance_xz_m;
        return left.delta_y_m - right.delta_y_m;
    });

    const nearest = matches.length ? matches[0] : null;
    const second = matches.length > 1 ? matches[1] : null;
    const separation = nearest && second
        ? Number(second.distance_xz_m) - Number(nearest.distance_xz_m)
        : null;
    const ratio = nearest && second && Number(second.distance_xz_m) > 0
        ? Number(nearest.distance_xz_m) / Number(second.distance_xz_m)
        : null;
    const confidentNearest = !!nearest && (
        second === null || (
            separation >= RAW_CLUE_CATALOG_MIN_GAP_M &&
            ratio <= RAW_CLUE_CATALOG_MAX_RATIO
        )
    );

    return {
        valid:true,
        position:cluePosition,
        comparisons:comparisons,
        match_count:matches.length,
        unique_index:confidentNearest
            ? Number(nearest.pending_index) : -1,
        unique_match:confidentNearest ? nearest : null,
        second_nearby_match:second,
        nearest_gap_m:separation,
        nearest_ratio:ratio,
        reason:confidentNearest
            ? (second === null
                ? "pista coincide com exatamente um ID do catálogo"
                : "ID mais próximo separado com confiança dos vizinhos")
            : (matches.length > 1
                ? "IDs próximos demais; associação recusada por segurança"
                : "pista não coincide com ID pendente do catálogo")
    };
}

function enrichCatalogPendingFromClue(index, clue, association) {
    if (index < 0 || index >= pending.length)
        return null;

    const corpse = pending[index];
    const before = {
        key:corpseKey(corpse),
        x:Number(corpse.x),
        y:Number(corpse.y),
        z:Number(corpse.z),
        species:corpse.species ?? null,
        weight:corpse.weight ?? null,
        gender:corpse.gender ?? null,
        difficulty:corpse.difficulty ?? null,
        registry_id:normalizeRegistryId(corpse.recovered_registry_id)
    };

    if (hasValue(clue.reserve))
        corpse.reserve = clue.reserve;
    if (hasValue(clue.species))
        corpse.species = clue.species;
    if (finiteNumber(clue.weight) !== null)
        corpse.weight = finiteNumber(clue.weight);
    if (hasValue(clue.gender))
        corpse.gender = clue.gender;
    if (hasValue(clue.difficulty))
        corpse.difficulty = clue.difficulty;
    if (usefulTag(clue.special_tag) !== "")
        corpse.special_tag = clue.special_tag;

    corpse.x = Number(clue.x);
    corpse.y = Number(clue.y);
    corpse.z = Number(clue.z);
    corpse.recovered_animal_id = String(
        clue.recovered_animal_id || corpse.recovered_animal_id || ""
    );
    corpse.recovered_from_clue = true;
    corpse.recovered_clue_association =
        "raw_clue_position_to_existing_catalog_id";
    corpse.recovered_clue_x = finiteNumber(association.position.x);
    corpse.recovered_clue_y = finiteNumber(association.position.y);
    corpse.recovered_clue_z = finiteNumber(association.position.z);
    corpse.recovered_clue_at = Date.now();

    return {
        before:before,
        after:corpse,
        registry_id:normalizeRegistryId(corpse.recovered_registry_id)
    };
}

function uniquePendingStrongDnaIndex(candidate, excludedIndex) {
    const matches = [];

    for (let i=0; i<pending.length; i++) {
        if (i === excludedIndex || strongCorpseDna(pending[i]) === "" ||
            !samePartialDnaIdentity(pending[i], candidate))
            continue;
        matches.push(i);
    }

    return matches.length === 1 ? matches[0] : -1;
}

function mergeCatalogPendingIntoUniqueDna(catalogIndex, clue, association) {
    if (catalogIndex < 0 || catalogIndex >= pending.length)
        return null;

    const dnaIndex = uniquePendingStrongDnaIndex(clue, catalogIndex);
    if (dnaIndex < 0)
        return null;

    const catalog = pending[catalogIndex];
    const dna = pending[dnaIndex];
    const registryId = normalizeRegistryId(catalog.recovered_registry_id);
    if (registryId === "")
        return null;

    const before = {
        count:pending.length,
        catalog:labSanitizeValue(catalog, 0),
        dna:labSanitizeValue(dna, 0)
    };

    if (Array.isArray(catalog.recovered_registry_id_history)) {
        for (let i=0; i<catalog.recovered_registry_id_history.length; i++)
            rememberRegistryId(dna, catalog.recovered_registry_id_history[i]);
    }
    rememberRegistryId(dna, registryId);
    dna.recovered_registry_id = registryId;
    dna.recovered_registry_descriptor = String(
        catalog.recovered_registry_descriptor ||
        dna.recovered_registry_descriptor || ""
    );
    dna.recovered_object_link = String(
        catalog.recovered_object_link || dna.recovered_object_link || ""
    );
    dna.recovered_animal_id = String(
        clue.recovered_animal_id || dna.recovered_animal_id || ""
    );
    dna.recovered_from_clue = true;
    dna.recovered_clue_association =
        "catalog_id_merged_into_unique_partial_dna";
    dna.recovered_clue_x = finiteNumber(association.position.x);
    dna.recovered_clue_y = finiteNumber(association.position.y);
    dna.recovered_clue_z = finiteNumber(association.position.z);
    dna.recovered_clue_at = Date.now();
    dna.recovered_catalog_alias_merged = true;

    // animal_x/y/z é a posição atual do corpo informada pela pista.
    dna.x = Number(clue.x);
    dna.y = Number(clue.y);
    dna.z = Number(clue.z);

    pending.splice(catalogIndex, 1);
    knownOldCorpseIds.add(registryId);

    return {
        before:before,
        after_count:pending.length,
        registry_id:registryId,
        merged:labSanitizeValue(dna, 0),
        key:corpseKey(dna)
    };
}

// A pista não fornece dificuldade. Reutilizar uma observação de morte
// somente com campos exatos e candidato único; coordenadas não participam.
function findObservedIdentity(candidate) {
    const reserve = normalizeReserve(candidate.reserve) || activeReserve;
    const id = String(candidate.recovered_animal_id || "");
    const exactIds = [];
    const signatures = [];
    for (let i=0; i<pending.length; i++) {
        const item = pending[i];
        if ((normalizeReserve(item.reserve) || activeReserve) !== reserve)
            continue;
        const otherId = String(item.recovered_animal_id || "");
        if (id !== "" && otherId === id) {
            for (const field of ["species", "gender", "difficulty", "weight"]) {
                if (hasValue(item[field]) && hasValue(candidate[field]) &&
                    Number(item[field]) !== Number(candidate[field]))
                    return {index:-1, method:"animal_id_conflict", ambiguous:true};
            }
            exactIds.push(i);
            continue;
        }
        if (!sameSpecies(item.species, candidate.species) ||
            !sameValue(item.gender, candidate.gender) ||
            finiteNumber(candidate.weight) === null ||
            Number(candidate.weight) <= 0 ||
            finiteNumber(item.weight) !== Number(candidate.weight))
            continue;
        if (hasValue(item.difficulty) && hasValue(candidate.difficulty) &&
            !sameValue(item.difficulty, candidate.difficulty))
            continue;
        signatures.push(i);
    }
    if (exactIds.length)
        return {index:exactIds.length === 1 ? exactIds[0] : -1,
            method:"animal_id", ambiguous:exactIds.length !== 1};
    if (signatures.length !== 1)
        return {index:-1, method:"exact_fields",
            ambiguous:signatures.length > 1};
    const index = signatures[0];
    const other = pending[index];
    const otherId = String(other.recovered_animal_id || "");
    // IDs explícitos diferentes nunca se fundem, mesmo com pesos iguais.
    if (id !== "" && otherId !== "" && id !== otherId)
        return {index:-1, method:"different_animal_ids", ambiguous:false};
    if (strongCorpseDna(other) === "" && strongCorpseDna(candidate) === "")
        return {index:-1, method:"partial_fields_only", ambiguous:false};
    return {index:index, method:"unique_exact_fields", ambiguous:false};
}

function mergeObservedIdentity(candidate, source) {
    const match = findObservedIdentity(candidate);
    if (match.index < 0)
        return match;
    const item = pending[match.index];
    const oldKey = corpseKey(item);
    const fromClue = candidate.recovered_from_clue === true;
    for (const field of ["species", "weight", "gender", "difficulty"])
        if (!hasValue(item[field]) && hasValue(candidate[field]))
            item[field] = candidate[field];
    if (String(candidate.recovered_animal_id || "") !== "")
        item.recovered_animal_id = String(candidate.recovered_animal_id);
    if (fromClue) {
        item.x = Number(candidate.x);
        item.y = Number(candidate.y);
        item.z = Number(candidate.z);
        item.recovered_from_clue = true;
        item.restored_unverified = source === "restore";
    }
    item.observed_identity_method = match.method;
    sendLabEvent("observed_identity_reused", {
        source:source, match:match, previous_key:oldKey,
        corpse:labSanitizeValue(item, 0), pending_count:pending.length,
        position_used_for_identity:false
    });
    if (source !== "restore") {
        cancelWaypointWork();
        waypointClearInstructionIssued = false;
        hudGpsActionRequired = false;
        if (preferredWaypointKey === oldKey)
            preferredWaypointKey = corpseKey(item);
        updateHudWarningForCount();
        emitRuntimeState("observações do mesmo animal reunidas");
        scheduleNearestUpdate(80, "dados do animal atualizados pela identidade");
    }
    return match;
}

function pendingMatchesRecoveredClue(candidate) {
    const animalId = String(candidate.recovered_animal_id || "");
    const dna = strongCorpseDna(candidate);
    for (let i=0; i<pending.length; i++) {
        const corpse = pending[i];
        const existingId = String(corpse.recovered_animal_id || "");
        if (animalId !== "" && existingId === animalId)
            return true;
        if (dna !== "" && strongCorpseDna(corpse) === dna &&
            !explicitAnimalIdsConflict(corpse, candidate) &&
            scopedStrongCorpseDna(corpse) === scopedStrongCorpseDna(candidate))
            return true;
    }

    return false;
}

function onRecoveredClue(path, o) {
    if (multiplayerBlocked || scriptStopping || !o)
        return;

    // state 7 foi confirmado nos testes como animal morto. Os campos
    // animal_x/y/z apontam para o cadáver, não para a pista examinada.
    if (Number(o.state) !== 7)
        return;

    if (!inspectSession(path, o) || !soloConfirmed)
        return;

    const animalId = String(o.animal_id ?? "").trim();

    if (animalId === "" || animalId === "0" ||
        !hasValue(o.animal_x) || !hasValue(o.animal_y) ||
        !hasValue(o.animal_z))
        return;

    const x = finiteNumber(o.animal_x);
    const y = finiteNumber(o.animal_y);
    const z = finiteNumber(o.animal_z);

    if (x === null || y === null || z === null)
        return;

    const reserve = o.reserve ?? activeReserve;
    const recoveryKey = `${reserve ?? ""}|${animalId}`;
    const alreadyExamined = seenRecoveredClues.has(recoveryKey);
    if (alreadyExamined && labRecoveryStage !== 1)
        return;

    // Sempre confirme visualmente que a pista foi recebida. A recuperação
    // normal costuma terminar muito rápido e antes não aparecia nada no HUD.
    const guidedClueRecovery = labRecoveryStage === 1;
    const feedbackSerial = ++clueFeedbackSerial;
    setDeathSignalPhase(
        DEATH_SIGNAL_PHASE_ANALYZING,
        "pista de sangue detectada"
    );
    const feedbackTimer = setTimeout(function () {
        if (scriptStopping || feedbackSerial !== clueFeedbackSerial ||
            deathSignalPhase !== DEATH_SIGNAL_PHASE_ANALYZING ||
            labRecoveryStage === 1)
            return;
        setDeathSignalPhase(
            DEATH_SIGNAL_PHASE_NORMAL,
            "pista de sangue analisada"
        );
        log("✅ PISTA DE SANGUE ANALISADA.");
    }, guidedClueRecovery ? 3000 : 2200);
    labBurstTimers.push(feedbackTimer);

    sendLabEvent("dead_clue_observed", {
        path:path,
        player:getPlayerPos(),
        payload:labSanitizeValue(o, 0)
    });
    if (!alreadyExamined)
        labScheduleRegistryBurst("pista_de_animal_morto");
    if (labRecoveryStage === 1) {
        const countBeforeClue = pending.length;
        const checkClue = setTimeout(function () {
            if (scriptStopping || labRecoveryStage !== 1)
                return;
            if (pending.length > countBeforeClue) {
                labRecoveryStage = 0;
                setDeathSignalPhase(DEATH_SIGNAL_PHASE_NORMAL,
                    "pista de sangue recuperou um abate");
                log("🟢 LAB: SEM AÇÃO NECESSÁRIA.");
                return;
            }
            labRecoveryStage = 2;
            setDeathSignalPhase(DEATH_SIGNAL_PHASE_NORMAL,
                "pista examinada; aguarde novos abates normalmente");
            log("🟠 LAB: ESTA PISTA NÃO RECUPEROU UM NOVO CORPO. " +
                "SE AINDA FALTA UM ABATE, ABATA OUTRO ANIMAL E " +
                "AGUARDE O CONTADOR. SE ELE NÃO MUDAR, APERTE F6.");
        }, 2500);
        labBurstTimers.push(checkClue);
    }

    if (alreadyExamined) {
        sendLabEvent("already_examined_blood_clue_ignored", {
            animal_id:animalId, guided:guidedClueRecovery
        });
        return;
    }

    seenRecoveredClues.add(recoveryKey);

    const c = {
        reserve:reserve,
        species:o.species,
        weight:finiteNumber(o.weight),
        gender:o.gender,
        difficulty:null,
        x:x,
        y:y,
        z:z,
        special_tag:"",
        recovered_animal_id:animalId,
        recovered_from_clue:true,
        time:Date.now()
    };
    if (skippedCorpseKeys.has(corpseKey(c)) ||
        skippedStrongDnas.has(scopedStrongCorpseDna(c))) {
        sendLabEvent("f6_skipped_clue_ignored", {
            corpse:labSanitizeValue(c, 0)
        });
        return;
    }
    const cluePlayer = getPlayerPos();
    const clueAssociationMatrix = rayXAssociationMatrix(c, cluePlayer);
    const clueStrictMatch = pendingMatchesRecoveredClue(c);
    const rawCatalogAssociation = rawClueCatalogAssociation(o);
    const clueHypothesis = rayXClueHypothesis(
        c,
        clueAssociationMatrix,
        clueStrictMatch
    );
    sendLabEvent("ray_x_clue_association", {
        path:path,
        clue:labSanitizeValue(c, 0),
        raw_payload:labSanitizeValue(o, 0),
        player:cluePlayer,
        pending_before:labPendingSnapshot(),
        comparisons:labSanitizeValue(clueAssociationMatrix, 0),
        hypothesis:labSanitizeValue(clueHypothesis, 0),
        raw_clue_catalog_association:labSanitizeValue(
            rawCatalogAssociation,
            0
        ),
        position_semantics:{
            raw_x_y_z:"posição da pista examinada",
            animal_x_y_z:"posição atual do corpo",
            collection_identity:"DNA ou ID estável; posição nunca coleta"
        }
    });

    if (clueHypothesis.probable_duplicate_missed_by_tolerance) {
        const nearest = clueHypothesis.nearest;
        log(
            "🧪 RAIO-X PISTA: possível duplicação; pista ficou a " +
            Number(nearest.distance_to_candidate_xz_m).toFixed(2) +
            "m do ID " + String(nearest.registry_id || "?") +
            ", mas a regra atual não associou."
        );
    } else {
        log(
            "🧪 RAIO-X PISTA: decisão=" +
            clueHypothesis.predicted_current_action +
            (clueHypothesis.nearest &&
                clueHypothesis.nearest.distance_to_candidate_xz_m !== null
                ? " | mais próximo=" +
                    Number(
                        clueHypothesis.nearest.distance_to_candidate_xz_m
                    ).toFixed(2) + "m"
                : "")
        );
    }
    registerForensicTarget(c, "pista_morta");
    deathSignalOnClue(c, path, o);

    const recoveredHarvestKey = scopedStrongCorpseDna(c) ||
        findHarvestedDnaByPartialFields(c);

    if (recoveredHarvestKey !== "" &&
        harvestedDnas.has(recoveredHarvestKey)) {
        sendLabEvent("recovered_clue_ignored_already_harvested_dna", {
            harvest_key:recoveredHarvestKey,
            corpse:labSanitizeValue(c, 0)
        });
        return;
    }

    const identityReuse = mergeObservedIdentity(c, "clue");
    if (identityReuse.index >= 0) {
        labRecoveryStage = 0;
        return;
    }
    if (identityReuse.ambiguous) {
        sendLabEvent("clue_identity_ambiguous", {
            clue:labSanitizeValue(c, 0), pending:labPendingSnapshot()
        });
        log("🟠 LAB: PISTA COM IDENTIDADE AMBÍGUA. CONTADOR PRESERVADO; ENVIE O ZIP.");
        return;
    }

    // A pista de 2577 estava 6 m do registro que depois coincidiu com
    // o corpo de 2575. Proximidade nunca comprova que são o mesmo animal.
    // O ID do catálogo continua separado até um vínculo de identidade real;
    // guardar a distância apenas no ZIP para investigar aliases possíveis.
    sendLabEvent("ray_x_clue_catalog_identity_unproven", {
        clue:labSanitizeValue(c, 0),
        catalog_candidates:labSanitizeValue(rawCatalogAssociation, 0),
        reason:"pista/corpo próximos não vinculam ID ao DNA",
        pending_before:labPendingSnapshot()
    });

    // Se o AnimalDeathEvent já registrou este mesmo animal, a pista não
    // cria uma segunda entrada para o mesmo cadáver.
    if (clueStrictMatch) {
        const matchedSaved = pending.filter(function (item) {
            return item.restored_unverified === true &&
                String(item.recovered_animal_id || "") === animalId &&
                normalizeReserve(item.reserve) ===
                    normalizeReserve(reserve);
        });
        if (matchedSaved.length === 1) {
            const saved = matchedSaved[0];
            saved.x = Number(c.x);
            saved.y = Number(c.y);
            saved.z = Number(c.z);
            saved.restored_unverified = false;
            saved.recovered_from_clue = true;
            emitRuntimeState("animal salvo reconfirmado pela pista");
            sendLabEvent("restored_clue_revalidated", {
                animal_id:animalId,
                corpse:labSanitizeValue(saved, 0),
                pending:labPendingSnapshot()
            });
            scheduleNearestUpdate(80, "pista reconfirmou corpo salvo");
        }
        sendLabEvent("ray_x_clue_reused_existing_pending", {
            clue:labSanitizeValue(c, 0),
            hypothesis:labSanitizeValue(clueHypothesis, 0),
            pending:labPendingSnapshot()
        });
        return;
    }

    pending.push(c);
    consecutiveEmptyManualScans = 0;
    if (rawCatalogAssociation.match_count > 0) {
        log("🟠 LAB: PISTA PERTO DE ID SEM DNA; CONTAGEM PROVISÓRIA. " +
            "COLETE UM CORPO CONFIRMANDO COM ENTER E ENVIE O ZIP. " +
            "A POSIÇÃO NÃO VINCULA ANIMAIS.");
    }
    if (labRecoveryStage !== 0) {
        labRecoveryStage = 0;
        log("🟢 LAB: SEM AÇÃO NECESSÁRIA.");
    }
    sendLabEvent("ray_x_clue_added_new_pending", {
        clue:labSanitizeValue(c, 0),
        hypothesis:labSanitizeValue(clueHypothesis, 0),
        pending_after:labPendingSnapshot()
    });
    emitRuntimeState("cadáver recuperado por pista");
    finalClearDone = false;

    if (!PROTECT_SETWAYPOINT)
        manualWaypointOverride = false;

    log(
        `☠️ CADAVER GUARDADO #${pending.length} (RECUPERADO POR PISTA) -> ` +
        `${corpseDesc(c)} | animal_id=${animalId} | dna=${corpseDna(c)}`
    );
    updateHudWarningForCount();
    sendGpsStatus("death", `recuperado por pista | ${corpseDesc(c)}`);

    scheduleNearestUpdate(220, "cadáver recuperado por pista");
}

function onDeath(path, o) {
    if (multiplayerBlocked || scriptStopping)
        return;

    // Fail-closed: sem confirmação explícita de solo, não toca no mapa.
    if (!inspectSession(path, o))
        return;

    if (!soloConfirmed) {
        log("⚠️ Morte ignorada: sessão ainda não confirmou modo SOLO.");
        return;
    }

    if (!o || o.x === undefined || o.y === undefined || o.z === undefined)
        return;

    sendLabEvent("animal_death_observed", {
        path:path,
        player:getPlayerPos(),
        payload:labSanitizeValue(o, 0),
        pending_before:labPendingSnapshot()
    });
    const deathDna = strongCorpseDna(o);
    const deathHarvestKey = scopedStrongCorpseDna(o);
    if (skippedCorpseKeys.has(corpseKey(o)) ||
        (deathHarvestKey !== "" && skippedStrongDnas.has(deathHarvestKey))) {
        sendLabEvent("f6_skipped_death_ignored", {
            payload:labSanitizeValue(o, 0)
        });
        return;
    }

    // Uma confirmação de coleta pode chegar antes de um evento de morte
    // atrasado. O DNA já coletado nunca volta para a fila de cadáveres.
    if (deathHarvestKey !== "" && harvestedDnas.has(deathHarvestKey)) {
        sendLabEvent("death_ignored_already_harvested_dna", {
            harvest_key:deathHarvestKey,
            payload:labSanitizeValue(o, 0)
        });
        log(`🧬 MORTE ATRASADA IGNORADA: DNA já coletado ${deathDna}.`);
        return;
    }

    if (deathHarvestKey !== "" && seenDeathDnas.has(deathHarvestKey))
        return;

    const k = corpseKey(o);

    if (seenDeaths.has(k))
        return;

    seenDeaths.add(k);

    if (labRecoveryStage !== 0) {
        labRecoveryStage = 0;
        log("🟢 LAB: SEM AÇÃO NECESSÁRIA.");
    }

    if (deathHarvestKey !== "")
        seenDeathDnas.add(deathHarvestKey);

    const c = {
        reserve:o.reserve,
        species:o.species,
        weight:finiteNumber(o.weight),
        gender:o.gender,
        difficulty:o.difficulty,
        x:Number(o.x),
        y:Number(o.y),
        z:Number(o.z),
        special_tag:String(o.special_tag ?? ""),
        time:Date.now()
    };
    registerForensicTarget(c, "morte_confirmada");
    deathSignalOnDeath(c, path, o);
    const identityReuse = mergeObservedIdentity(c, "death");
    if (identityReuse.index >= 0)
        return;


    // O catálogo não expõe DNA. Dois cadáveres podem cair na mesma posição;
    // associar uma morte a um ID apenas pelas coordenadas trocaria animais.
    sendLabEvent("death_catalog_identity_unproven", {
        dna:deathDna,
        pending_before:labPendingSnapshot(),
        position_only_links_disabled:true
    });

    pending.push(c);
    consecutiveEmptyManualScans = 0;
    emitRuntimeState("morte adicionada à fila");
    sendLabEvent("animal_death_added_to_pending", {
        corpse:labSanitizeValue(c, 0),
        pending:labPendingSnapshot()
    });
    finalClearDone = false;
    // Se a proteção está ativa e o jogador colocou um waypoint manual,
    // nem um novo abate pode furar essa proteção. Ele precisa limpar o point.
    if (!PROTECT_SETWAYPOINT)
        manualWaypointOverride = false;

    log(
        `☠️ CADAVER GUARDADO #${pending.length} -> ` +
        `${corpseDesc(c)} | dna=${corpseDna(c)}`
    );
    updateHudWarningForCount();
    sendGpsStatus("death", corpseDesc(c));

    scheduleNearestUpdate(40, "novo abate");
    labScheduleRegistryBurst("morte_confirmada");
}

function nearestCandidate(indices, pp) {
    if (!pp || !indices.length)
        return null;

    let bestIndex = indices[0];
    let bestDistanceSq = distSqXZ(pp, pending[bestIndex]);

    for (let i=1; i<indices.length; i++) {
        const index = indices[i];
        const distanceSq = distSqXZ(pp, pending[index]);

        if (distanceSq < bestDistanceSq) {
            bestDistanceSq = distanceSq;
            bestIndex = index;
        }
    }

    return {index:bestIndex, distance:Math.sqrt(bestDistanceSq)};
}

function isLegacyCorpseWithoutDna(corpse) {
    return strongCorpseDna(corpse) === "" &&
        (corpse.recovered_from_startup === true ||
         corpse.recovered_from_clue === true);
}

function explicitAnimalIdsConflict(a, b) {
    const left = String(a.recovered_animal_id || a.animal_id || "");
    const right = String(b.recovered_animal_id || b.animal_id || "");
    return left !== "" && right !== "" && left !== right;
}

function legacyPartialDnaCompatible(corpse, harvest) {
    return isLegacyCorpseWithoutDna(corpse) &&
        !explicitAnimalIdsConflict(corpse, harvest) &&
        samePartialDnaIdentity(corpse, harvest) &&
        finiteNumber(corpse.weight) === finiteNumber(harvest.weight);
}

function chooseHarvestMatch(o) {
    if (!pending.length)
        return {index:-1, method:"sem_pendentes", distance:null};

    const pp = getPlayerPos();
    const harvestDna = strongCorpseDna(o);
    const harvestKey = scopedStrongCorpseDna(o);

    // Animal normal: o DNA é a identidade. A distância é calculada somente
    // para diagnóstico e jamais participa da escolha do cadáver removido.
    if (harvestDna !== "") {
        const exactDnaMatches = [];

        for (let i=0; i<pending.length; i++) {
            if (scopedStrongCorpseDna(pending[i]) === harvestKey &&
                !explicitAnimalIdsConflict(pending[i], o))
                exactDnaMatches.push(i);
        }

        if (exactDnaMatches.length > 0) {
            const explicitIds = new Set(exactDnaMatches.map(function (i) {
                return String(pending[i].recovered_animal_id || "");
            }).filter(Boolean));
            if (explicitIds.size > 1)
                return {index:-1, method:"dna_com_ids_distintos", distance:null};
            const index = chooseCanonicalDnaIndex(exactDnaMatches);
            return {
                index:index,
                method:exactDnaMatches.length === 1
                    ? "dna_exato_mesmo_animal"
                    : "dna_exato_com_aliases",
                distance:pp ? distanceXZ(pp, pending[index]) : null,
                alias_count:Math.max(0, exactDnaMatches.length - 1)
            };
        }

        // Exceção aprovada: cadáveres antigos podem não ter DNA completo.
        // Se a pista já forneceu pelo menos dois campos coincidentes, eles
        // identificam o registro antigo sem tocar em nenhum animal normal.
        const legacyPartialMatches = [];

        for (let i=0; i<pending.length; i++) {
            if (legacyPartialDnaCompatible(pending[i], o))
                legacyPartialMatches.push(i);
        }

        if (legacyPartialMatches.length === 1) {
            const index = legacyPartialMatches[0];
            return {
                index:index,
                method:"dna_parcial_cadaver_antigo_unico",
                distance:pp ? distanceXZ(pp, pending[index]) : null
            };
        }

        if (legacyPartialMatches.length > 1) {
            return {
                index:-1,
                method:"dna_parcial_ambiguo_aguardando_id_estavel",
                distance:null
            };
        }

        const legacyWithoutDna = [];

        for (let i=0; i<pending.length; i++) {
            if (isLegacyCorpseWithoutDna(pending[i]))
                legacyWithoutDna.push(i);
        }

        const legacyWithStableId = legacyWithoutDna.filter(function (index) {
            return normalizeRegistryId(
                pending[index].recovered_registry_id
            ) !== "";
        });

        // Para cadáver recuperado, a posição não escolhe mais qual animal foi
        // coletado. O evento aguarda o ID que desaparecer da lista do jogo.
        if (legacyWithStableId.length > 0) {
            return {
                index:-1,
                method:"aguardando_desaparecimento_do_id_estavel",
                distance:null
            };
        }



        // DNA completo sem correspondência nunca remove um animal normal pela
        // posição do jogador. O evento fica no log para investigação.
        return {
            index:-1,
            method:"dna_completo_sem_correspondencia",
            distance:null
        };
    }

    // Sem DNA, aguardar identidade confirmada; posição nunca autoriza remoção.
    const tag = usefulTag(o.special_tag);

    if (tag) {
        const legacyTagMatches = [];

        for (let i=0; i<pending.length; i++) {
            if (isLegacyCorpseWithoutDna(pending[i]) &&
                usefulTag(pending[i].special_tag) === tag)
                legacyTagMatches.push(i);
        }

        if (legacyTagMatches.length === 1) {
            const index = legacyTagMatches[0];
            return {
                index:index,
                method:"special_tag_cadaver_antigo_unico",
                distance:pp ? distanceXZ(pp, pending[index]) : null
            };
        }
    }

    const recoveredIndices = [];

    for (let i=0; i<pending.length; i++) {
        if (isLegacyCorpseWithoutDna(pending[i]))
            recoveredIndices.push(i);
    }

    const recoveredWithStableId = recoveredIndices.filter(function (index) {
        return normalizeRegistryId(
            pending[index].recovered_registry_id
        ) !== "";
    });

    if (recoveredWithStableId.length > 0) {
        return {
            index:-1,
            method:"coleta_sem_dna_aguardando_id_estavel",
            distance:null
        };
    }



    return {index:-1, method:"coleta_sem_dna_sem_associacao", distance:null};
}

function onHarvest(path, o, sequence) {
    if (multiplayerBlocked || scriptStopping)
        return;

    if (!inspectSession(path, o))
        return;

    deathSignalOnHarvest(path, o);
    collectionDebugHarvestObserved(path, o);

    const pendingBefore = labPendingSnapshot();
    const stableIdBefore = labStableStateBeforeHarvest();
    let forensicTargetBefore = findForensicTarget(o);

    if (!forensicTargetBefore)
        forensicTargetBefore = registerForensicTarget(
            o,
            "coleta_sem_historico"
        );

    if (forensicTargetBefore) {
        snapshotKnownForensicCandidates(
            forensicTargetBefore,
            "antes_processar_coleta"
        );
        const knownCandidates = Array.isArray(
            forensicCandidates[forensicTargetBefore.target_id]
        ) ? forensicCandidates[forensicTargetBefore.target_id] : [];

        // A 0.9.18 iniciou varreduras automáticas de quase 6 GB durante as
        // coletas. Elas não resolveram o corpo antigo e deixaram o laboratório
        // pesado. Mantemos os dados já conhecidos e reservamos a varredura
        // grande somente para uma análise diagnóstica dirigida.
        if (!knownCandidates.length && !COLLECTION_DEBUG_MODE) {
            sendLabEvent("forensic_auto_scan_skipped", {
                reason:"coleta não deve iniciar varredura pesada automática",
                target:forensicTargetSnapshot(forensicTargetBefore)
            });
        }
    }

    sendLabEvent("harvest_observed", {
        sequence:sequence || 0,
        path:path,
        player:getPlayerPos(),
        payload:labSanitizeValue(o, 0),
        pending_before:pendingBefore,
        stable_ids_before:labSanitizeValue(stableIdBefore, 0)
    });
    labScheduleRegistryBurst("coleta_confirmada");

    const harvestDna = strongCorpseDna(o);
    const harvestKey = scopedStrongCorpseDna(o);
    const alreadyHarvestedKey = harvestKey ||
        findHarvestedDnaByPartialFields(o);

    // O jogo pode repetir a confirmação de coleta alterando apenas game_time.
    // Depois da primeira coleta, o mesmo DNA é uma repetição e não toca na fila.
    if (alreadyHarvestedKey !== "" &&
        harvestedDnas.has(alreadyHarvestedKey)) {
        sendLabEvent("harvest_duplicate_dna_ignored", {
            sequence:sequence || 0,
            harvest_key:alreadyHarvestedKey,
            dna:harvestDna,
            payload:labSanitizeValue(o, 0),
            pending:labPendingSnapshot()
        });
        log(`🧬 COLETA REPETIDA IGNORADA: DNA ${harvestDna}.`);
        labScheduleStableIdHarvestProbe(
            path,
            o,
            stableIdBefore,
            pendingBefore,
            {index:-1, method:"dna_ja_coletado", distance:null},
            null
        );
        return;
    }

    if (!soloConfirmed)
        return;

    if (!pending.length) {
        rememberHarvestedDna(o, "coleta confirmada sem cadáver pendente");
        finishForensicTargetHarvest(forensicTargetBefore, o);
        sendLabEvent("harvest_without_pending", {
            sequence:sequence || 0,
            harvest_key:harvestKey,
            payload:labSanitizeValue(o, 0)
        });
        labScheduleStableIdHarvestProbe(
            path,
            o,
            stableIdBefore,
            pendingBefore,
            {index:-1, method:"sem_pendentes", distance:null},
            null
        );
        return;
    }

    const match = chooseHarvestMatch(o);
    const idx = match.index;
    const harvestPlayer = getPlayerPos();
    const harvestComparisons = rayXAssociationMatrix(o, harvestPlayer);
    sendLabEvent("ray_x_harvest_association", {
        sequence:sequence || 0,
        path:path,
        harvest:labSanitizeValue(o, 0),
        harvest_dna:harvestDna,
        player:harvestPlayer,
        chosen_match:labSanitizeValue(match, 0),
        comparisons:labSanitizeValue(harvestComparisons, 0),
        pending_before:pendingBefore,
        stable_ids_before:labSanitizeValue(stableIdBefore, 0)
    });

    log(
        "🧪 RAIO-X COLETA: associação=" + String(match.method || "?") +
        " | índice=" + idx +
        " | pendentes antes=" + pending.length
    );

    if (idx < 0) {
        // Uma coleta nova sem DNA vinculado não deve herdar a janela temporal
        // de uma coleta anterior e aposentar um ID por engano.
        labMostRecentConfirmedHarvest = null;
        const pp = getPlayerPos();
        // O jogo elimina o ID do catálogo antes de publicar ConfirmKillEvent.
        // Nos testes reais a diferença chegou a oito segundos. Se exatamente
        // um ID pendente desapareceu há pouco, essa é a confirmação mais forte
        // disponível para um cadáver antigo que não possui DNA no catálogo.
        const recentStableRemoval = labConsumeRecentRemovedRegistry(o, match);
        if (recentStableRemoval !== null) {
            finishForensicTargetHarvest(forensicTargetBefore, o);
            sendLabEvent("harvest_matched_by_recent_registry_removal", {
                sequence:sequence || 0,
                match:labSanitizeValue(match, 0),
                harvest_key:harvestKey,
                player:pp,
                removed:labSanitizeValue(recentStableRemoval, 0),
                pending_after:labPendingSnapshot()
            });
            labScheduleStableIdHarvestProbe(
                path,
                o,
                stableIdBefore,
                pendingBefore,
                match,
                recentStableRemoval
            );
            return;
        }

        // A coleta confirma que este DNA já saiu do jogo, mesmo quando a morte
        // não chegou ao Turbo Hunter. Ele fica guardado para bloquear eventos
        // de morte ou pista atrasados, mas nenhum outro cadáver é removido.
        rememberHarvestedDna(o, "coleta sem correspondência na fila");
        finishForensicTargetHarvest(forensicTargetBefore, o);
        sendLabEvent("harvest_without_safe_match", {
            sequence:sequence || 0,
            match:labSanitizeValue(match, 0),
            harvest_key:harvestKey,
            player:pp,
            payload:labSanitizeValue(o, 0),
            pending:labPendingSnapshot()
        });
        labQueueDeferredStableHarvest(
            path,
            o,
            sequence,
            match,
            pendingBefore
        );
        log(
            `⚠️ Coleta detectada sem associação segura: ` +
            `species=${o.species ?? "?"} weight=${o.weight ?? "?"} ` +
            `gender=${o.gender ?? "?"} pendentes=${pending.length}` +
            (pp ? ` jogador=(${pp.x.toFixed(2)},${pp.y.toFixed(2)},${pp.z.toFixed(2)})` : "")
        );
        const pendingBeforeConfirmation = pending.length;
        startHudCountdown(
            DEFERRED_STABLE_HARVEST_WAIT_SECONDS,
            "ENTER CONFIRMADO; CONFERINDO O ID NA MEMÓRIA",
            function () {
                if (scriptStopping ||
                    pending.length < pendingBeforeConfirmation ||
                    deathSignalPhase === DEATH_SIGNAL_PHASE_STOP ||
                    deathSignalPhase === DEATH_SIGNAL_PHASE_SEND_ZIP)
                    return;

                // DNA não pertence a nenhum pendente: ele pode ter sido
                // coletado imediatamente após cair. Não interrompa a caça
                // nem desconte um ID diferente pelo simples tempo decorrido.
                setDeathSignalPhase(DEATH_SIGNAL_PHASE_NORMAL,
                    "coleta sem correspondência; contador preservado");
                log("🧬 COLETA SEM ID PENDENTE CORRESPONDENTE; " +
                    "CONTADOR PRESERVADO. O CATÁLOGO CONTINUA SENDO LIDO.");
                sendLabEvent("harvest_confirmation_timeout", {
                    waited_seconds:DEFERRED_STABLE_HARVEST_WAIT_SECONDS,
                    pending_before:pendingBeforeConfirmation,
                    pending_after:pending.length,
                    harvest:labSanitizeValue(o, 0)
                });
            }
        );
        labScheduleStableIdHarvestProbe(
            path,
            o,
            stableIdBefore,
            pendingBefore,
            match,
            null
        );
        return;
    }

    // Cancela toda marcação atrasada ANTES de alterar a fila. Assim nenhum
    // callback antigo consegue recolocar o animal que está sendo coletado.
    const hadOwnedMarker = markerOwned || currentKey !== "";
    cancelWaypointWork();

    const removed = pending.splice(idx, 1)[0];
    const dnaAliasesRemoved = removeStrongDnaAliases(removed);
    updateHudWarningForCount();
    sendLabEvent("harvest_counter_updated", {
        sequence:sequence || 0, pending_count:pending.length,
        game_confirm_age_ms:finiteNumber(o.confirm_ts) === null ? null :
            Math.max(0, Date.now() - Number(o.confirm_ts) * 1000)
    });
    const markedTargetStillPending = currentKey !== "" && pending.some(
        function (corpse) { return corpseKey(corpse) === currentKey; }
    );
    if (!markedTargetStillPending) {
        if (hadOwnedMarker)
            clearMarkerInternal();
        resetMarkerState();
    } else {
        currentIndex = pending.findIndex(function (corpse) {
            return corpseKey(corpse) === currentKey;
        });
    }
    rememberNavigationAnchor(removed, "coleta confirmada pelo DNA");
    stopHudCountdown("coleta associada ao DNA");
    const removedKey = corpseKey(removed);
    const removedDna = strongCorpseDna(removed);
    rememberHarvestedDna(o, "cadáver correspondente removido da fila");
    // Aliases compatíveis já removidos antes da atualização do HUD.
    // IDs de animal explicitamente diferentes permanecem separados.
    finishForensicTargetHarvest(forensicTargetBefore, o);
    labMostRecentConfirmedHarvest = {
        at:Date.now(),
        sequence:sequence || 0,
        reserve:normalizeReserve(activeReserve),
        removed:labSanitizeValue(removed, 0),
        recheck_scheduled:false
    };
    const catalogRecheckScheduled = labScheduleAbsentCatalogRecheck(
        removed, sequence
    );
    labMostRecentConfirmedHarvest.recheck_scheduled =
        catalogRecheckScheduled;

    updateHudWarningForCount();

    sendLabEvent("harvest_matched_and_removed", {
        sequence:sequence || 0,
        match:labSanitizeValue(match, 0),
        harvest_key:harvestKey,
        removed:labSanitizeValue(removed, 0),
        removed_dna:removedDna,
        dna_aliases_removed:labSanitizeValue(dnaAliasesRemoved, 0),
        catalog_recheck_scheduled:catalogRecheckScheduled,
        removed_key:removedKey,
        location_policy:"posição original preservada; localização usada somente no waypoint",
        player:getPlayerPos(),
        pending_after:labPendingSnapshot()
    });
    labScheduleStableIdHarvestProbe(
        path,
        o,
        stableIdBefore,
        pendingBefore,
        match,
        removed
    );
    scheduleDeferredCatalogRecheckAfterHarvest(sequence);

    if (dnaAliasesRemoved.length > 0) {
        sendLabEvent("dna_duplicate_entries_corrected", {
            dna:removedDna,
            primary_removed:labSanitizeValue(removed, 0),
            aliases_removed:labSanitizeValue(dnaAliasesRemoved, 0),
            pending_after:labPendingSnapshot()
        });
        log(
            `🧬 DNA COINCIDENTE: ${dnaAliasesRemoved.length} entrada(s) ` +
            `com ID diferente corrigida(s).`
        );
    }

    log(
        `✅ CADAVER COLETADO -> ${corpseDesc(removed)} ` +
        `| associação=${match.method}` +
        (match.distance !== null ? ` (${match.distance.toFixed(1)}m)` : "") +
        ` | restantes=${pending.length}`
    );
    sendGpsStatus("harvest", corpseDesc(removed));

    if (!pending.length) {
        clearOwn("ultimo cadáver coletado", true);
        finishLabCollectionTest("último DNA foi coletado");
        if (hadOwnedMarker) {
            const completedSerial = switchSerial;
            setTimeout(function () {
                if (scriptStopping || pending.length ||
                    switchSerial !== completedSerial)
                    return;
                requestWaypointClearCapture(
                    "marcação restante após coletar o último animal",
                    removed
                );
            }, 750);
        }
        return;
    }

    if (!PROTECT_SETWAYPOINT)
        manualWaypointOverride = false;
    finalClearDone = false;
    scheduleNearestUpdate(40, "DNA coletado; marcar próximo cadáver");
}

function noDnaHarvestFingerprint(o) {
    return [
        normalizeReserve(o.reserve) || activeReserve,
        o.species ?? "",
        finiteNumber(o.weight) !== null ? Number(o.weight).toFixed(6) : "",
        o.gender ?? "",
        o.difficulty ?? "",
        o.score ?? "",
        o.medal ?? "",
        o.cash_reward ?? "",
        o.xp_reward ?? "",
        usefulTag(o.special_tag)
    ].join("|");
}

function normalizeConfirmedHarvestPayload(o) {
    if (!o || typeof o !== "object")
        return null;

    const normalized = Object.assign({}, o);
    if (!hasValue(normalized.difficulty) &&
        hasValue(normalized.animal_difficulty)) {
        // harvest_animal2 usa dificuldade começando em zero; os eventos de
        // morte usam o mesmo valor começando em um.
        normalized.difficulty = Number(normalized.animal_difficulty) + 1;
    }
    if (!hasValue(normalized.score) && hasValue(normalized.trophy_score))
        normalized.score = normalized.trophy_score;
    if (!hasValue(normalized.game_time) &&
        hasValue(normalized.game_time_of_day)) {
        normalized.game_time = normalized.game_time_of_day;
    }
    if (!hasValue(normalized.special_tag))
        normalized.special_tag = "0";
    normalized.collection_confirmed_after_enter = true;
    return normalized;
}

function observeHarvestScreen(path, o) {
    sendLabEvent("harvest_screen_event_observed", {
        path:path,
        dna:strongCorpseDna(o),
        payload:labSanitizeValue(o, 0),
        pending:labPendingSnapshot(),
        policy:"não descontar; aguardar harvest_animal2 e desaparecimento do ID"
    });
    log(
        "🧪 TELA DO ANIMAL DETECTADA: ainda não desconta; " +
        "aguardando confirmação real da coleta."
    );
}

function enqueueHarvest(path, o) {
    if (!o || multiplayerBlocked || scriptStopping)
        return;

    // Eventos sem DNA completo ainda recebem uma proteção de transporte. Para
    // DNA completo, a própria lista harvestedDnas é a autoridade definitiva.
    if (strongCorpseDna(o) === "") {
        const transportKey = noDnaHarvestFingerprint(o);

        if (seenHarvests.has(transportKey))
            return;

        seenHarvests.add(transportKey);
    }

    const sequence = ++harvestSequence;
    harvestQueue.push({path:path, payload:o, sequence:sequence});
    sendLabEvent("harvest_queued", {
        sequence:sequence,
        dna:strongCorpseDna(o),
        queue_length:harvestQueue.length
    });

    if (harvestQueueProcessing)
        return;

    harvestQueueProcessing = true;
    try {
        while (harvestQueue.length > 0) {
            const item = harvestQueue.shift();

            try {
                onHarvest(item.path, item.payload, item.sequence);
            } catch (error) {
                sendLabEvent("harvest_queue_error", {
                    sequence:item.sequence,
                    error:String(error),
                    payload:labSanitizeValue(item.payload, 0)
                });
                log(`❌ ERRO NA FILA DE COLETA #${item.sequence}: ${error}`);
            }
        }
    } finally {
        harvestQueueProcessing = false;
    }
}

function runDnaRuleSelfTest() {
    const originalPending = pending;
    let checks = 0;

    function check(condition, message) {
        checks++;
        if (!condition)
            throw new Error(message);
    }

    try {
        const reserve = 999999;
        pending = [];

        // Dez corpos empilhados, cada um com DNA próprio.
        for (let i=0; i<10; i++) {
            pending.push({
                reserve:reserve,
                species:i % 2 === 0 ? 50 : 12,
                weight:100 + i / 1000,
                gender:i % 2 === 0 ? 2 : 1,
                difficulty:(i % 5) + 1,
                x:500,
                y:1000,
                z:500,
                time:i
            });
        }

        const collectionOrder = [7, 2, 9, 0, 5, 1, 8, 3, 6, 4];
        for (let i=0; i<collectionOrder.length; i++) {
            const number = collectionOrder[i];
            const harvest = {
                reserve:reserve,
                species:number % 2 === 0 ? 50 : 12,
                weight:100 + number / 1000,
                gender:number % 2 === 0 ? 2 : 1,
                difficulty:(number % 5) + 1
            };
            const match = chooseHarvestMatch(harvest);
            check(match.index >= 0, `DNA ${number} não foi encontrado`);
            check(
                strongCorpseDna(pending[match.index]) === strongCorpseDna(harvest),
                `DNA ${number} escolheu outro animal`
            );
            pending.splice(match.index, 1);
        }
        check(pending.length === 0, "fila empilhada não terminou vazia");

        // DNA desconhecido perto de outro corpo normal não pode removê-lo.
        pending = [{
            reserve:reserve,
            species:12,
            weight:407.0854187011719,
            gender:2,
            difficulty:1,
            x:0,
            y:0,
            z:0
        }];
        const unknown = chooseHarvestMatch({
            reserve:reserve,
            species:50,
            weight:4.405018329620361,
            gender:2,
            difficulty:1
        });
        check(
            unknown.index === -1 &&
            unknown.method === "dna_completo_sem_correspondencia",
            "DNA desconhecido usou proximidade em animal normal"
        );

        // Duas entradas do mesmo DNA escolhem a posição original da morte,
        // nunca a mais próxima do jogador.
        pending = [
            {
                reserve:reserve,
                species:20,
                weight:2.432873249053955,
                gender:1,
                difficulty:1,
                x:0,
                y:0,
                z:0,
                recovered_from_startup:true
            },
            {
                reserve:reserve,
                species:20,
                weight:2.432873249053955,
                gender:1,
                difficulty:1,
                x:900,
                y:0,
                z:900
            }
        ];
        const alias = chooseHarvestMatch({
            reserve:reserve,
            species:20,
            weight:2.432873249053955,
            gender:1,
            difficulty:1
        });
        check(
            alias.index === 1 && alias.method === "dna_exato_com_aliases",
            "alias de memória venceu a posição original da morte"
        );

        check(
            forensicPartialCompatible(
                {reserve:reserve, species:27, weight:230.802719,
                 gender:1, difficulty:null},
                {reserve:reserve, species:27, weight:230.802719,
                 gender:1, difficulty:3}
            ),
            "pista sem dificuldade não combinou com DNA completo"
        );
        check(
            !forensicPartialCompatible(
                {reserve:reserve, species:27, weight:230.802719,
                 gender:1, difficulty:null},
                {reserve:reserve, species:27, weight:231.802719,
                 gender:1, difficulty:3}
            ),
            "DNA parcial aceitou peso diferente"
        );

        const confirmedHarvest = normalizeConfirmedHarvestPayload({
            reserve:reserve,
            species:8,
            weight:72.4249267578125,
            gender:1,
            animal_difficulty:2,
            trophy_score:119.17540740966797,
            game_time_of_day:5.805691719055176
        });
        check(
            confirmedHarvest !== null &&
            confirmedHarvest.difficulty === 3 &&
            confirmedHarvest.score === 119.17540740966797 &&
            confirmedHarvest.collection_confirmed_after_enter === true,
            "harvest_animal2 não foi normalizado como coleta definitiva"
        );
        check(
            strongCorpseDna(confirmedHarvest) === "8|72.424927|1|3",
            "DNA da coleta definitiva ficou diferente do ConfirmKillEvent"
        );

        return {ok:true, checks:checks};
    } catch (error) {
        return {ok:false, checks:checks, error:String(error)};
    } finally {
        pending = originalPending;
    }
}

// -----------------------------------------------------------
// OBSERVA WAYPOINT
// -----------------------------------------------------------

function readHookPosition(p) {
    if (!p || p.isNull())
        return null;

    try {
        const x = p.readFloat();
        const y = p.add(4).readFloat();
        const z = p.add(8).readFloat();

        if (!validCoord(x) || !validCoord(y) || !validCoord(z))
            return null;

        return {x:x, y:y, z:z};
    } catch (_) {
        return null;
    }
}

function findPendingAtPosition(position) {
    if (!position)
        return -1;

    for (let i=0; i<pending.length; i++) {
        if (distanceXZ(position, pending[i]) <= 0.75 &&
            Math.abs(Number(position.y) - Number(pending[i].y)) <= 3)
            return i;
    }

    return -1;
}

function matchesLastInternalSet(position) {
    if (!position || !lastInternalSetPosition ||
        Date.now() - lastInternalSetAt > INTERNAL_HOOK_COOLDOWN_MS)
        return false;

    return distanceXZ(position, lastInternalSetPosition) <= 0.75 &&
        Math.abs(Number(position.y) - Number(lastInternalSetPosition.y)) <= 3;
}

function protectManualWaypoint() {
    manualWaypointOverride = true;
    cancelWaypointWork();
    resetMarkerState();
    finalClearDone = true;
    log("🧭 WAYPOINT DO JOGADOR PROTEGIDO: limpe o point para liberar o GPS automático.");
}

function releaseManualWaypointProtection() {
    if (!manualWaypointOverride)
        return false;

    manualWaypointOverride = false;
    cancelWaypointWork();
    resetMarkerState();
    finalClearDone = false;
    log("🧭 Waypoint do jogador limpo: GPS automático liberado.");

    if (pending.length)
        scheduleNearestUpdate(180, "retomada apos jogador limpar waypoint");

    return true;
}

function scheduleExternalReapply(kind, markerPresent, reason, message) {
    if (multiplayerBlocked || scriptStopping || !soloConfirmed || !pending.length)
        return;

    const now = Date.now();

    if (externalReapplyTimer !== null)
        return;

    if (lastExternalHookKind === kind &&
        now - lastExternalHookAt < EXTERNAL_HOOK_DEBOUNCE_MS)
        return;

    lastExternalHookKind = kind;
    lastExternalHookAt = now;

    switchTimer = clearTimeoutSafe(switchTimer);
    nearestUpdateTimer = clearTimeoutSafe(nearestUpdateTimer);
    switchSerial++;
    currentKey = "";
    currentIndex = -1;
    switchingKey = "";
    markerOwned = markerPresent;

    log(message);

    externalReapplyTimer = setTimeout(function () {
        externalReapplyTimer = null;

        if (!multiplayerBlocked && !scriptStopping && soloConfirmed && pending.length)
            updateNearest(reason);
    }, EXTERNAL_REAPPLY_DELAY_MS);
}

function attachOptionalInterceptor(name, address, enabled, callbacks) {
    if (!enabled || !address)
        return null;

    try {
        return Interceptor.attach(address, callbacks);
    } catch (error) {
        const detail = String(error || "erro desconhecido");
        const codeHex = labHex(address, 32);
        log(
            "⚠️ MODO COMPATÍVEL: gancho " + name +
            " indisponível após atualização; continuando sem bloquear. " +
            detail
        );
        sendLabEvent("optional_hook_unavailable", {
            name:String(name || ""),
            address:pstr(address),
            code_hex:codeHex,
            error:detail,
            blocked:false
        });
        return null;
    }
}

const setWaypointInterceptor = attachOptionalInterceptor(
    "SetWaypoint", SET_ADDR, setWaypointAddressReady, {
    onEnter(args) {
        capturedMap = args[0];
        rayXWaitingForManualWaypoint = false;
        hudGpsActionRequired = false;
        updateHudState();
        const rayXHookTarget = readHookPosition(args[1]);

        sendLabEvent("ray_x_native_setwaypoint_enter", {
            inside_turbo_hunter:insideSet === true,
            map_pointer:pstr(capturedMap),
            target:labSanitizeValue(rayXHookTarget, 0),
            set_waypoint_address:pstr(SET_ADDR),
            set_waypoint_code_hex:labHex(SET_ADDR, 64),
            reference_map_slot:pstr(MAP_SLOT),
            active_map_slot:pstr(activeMapSlot),
            pending:labPendingSnapshot()
        });
        rayXDiscoverMapSingletonSlots(
            capturedMap,
            insideSet ? "waypoint_do_turbo_hunter" : "waypoint_do_jogador"
        );

        // insideSet é uma prova absoluta de que a chamada veio do próprio mod.
        if (insideSet || scriptStopping || multiplayerBlocked || !soloConfirmed)
            return;

        const targetPosition = rayXHookTarget;
        const matchingIndex = pending.length ? findPendingAtPosition(targetPosition) : -1;
        const nearestIndex = pending.length ? findNearestIndex(getPlayerPos()) : -1;
        const matchingKey = matchingIndex >= 0
            ? corpseKey(pending[matchingIndex])
            : "";
        const matchesExpectedGpsTarget = matchingIndex >= 0 &&
            (matchingIndex === nearestIndex || matchingKey === currentKey ||
             matchingKey === switchingKey);

        // Algumas chamadas nativas podem retornar depois da coleta. Se elas
        // tentarem recriar exatamente a posição de um cadáver que já saiu da
        // fila, limpamos esse eco e recalculamos o waypoint atual.
        const staleInternalTarget = matchingIndex < 0 &&
            matchesLastInternalSet(targetPosition);

        if (staleInternalTarget) {
            sendLabEvent("stale_waypoint_callback_detected", {
                target:labSanitizeValue(targetPosition, 0),
                pending:labPendingSnapshot()
            });
            cancelWaypointWork();
            currentKey = "";
            currentIndex = -1;
            switchingKey = "";
            markerOwned = true;
            staleWaypointCleanupTimer = setTimeout(function () {
                staleWaypointCleanupTimer = null;

                if (pending.length) {
                    updateNearest("corrigir waypoint atrasado após coleta");
                } else {
                    clearOwn("waypoint atrasado após última coleta", true);
                }
            }, 40);
            return;
        }

        // Mesmo se um callback interno chegar atrasado, a posição denuncia
        // que este SetWaypoint já é exatamente o alvo correto do GPS.
        if (matchesExpectedGpsTarget) {
            currentKey = matchingKey;
            currentIndex = matchingIndex;
            switchingKey = "";
            markerOwned = true;
            manualWaypointOverride = false;
            return;
        }

        // Durante a janela interna, callback sem posição ou apontando para algum
        // cadáver conhecido ainda pode ser eco atrasado do jogo. Um alvo válido
        // e realmente diferente, porém, é waypoint novo do jogador e continua.
        if (Date.now() <= internalHookIgnoreUntil &&
            (targetPosition === null || matchingIndex >= 0 ||
             matchesLastInternalSet(targetPosition)))
            return;

        // 1 = protege o waypoint DO JOGADOR. O Turbo Hunter espera o jogador
        // limpar o point antes de voltar a mover o GPS, mesmo com novo abate.
        if (PROTECT_SETWAYPOINT) {
            protectManualWaypoint();
            return;
        }

        // 0 = sem proteção: se houver cadáver pendente o Turbo Hunter pode
        // reassumir/mover o waypoint automaticamente.
        manualWaypointOverride = false;
        if (pending.length) {
            startHudCountdown(
                Math.ceil(EXTERNAL_REAPPLY_DELAY_MS / 1000),
                "PONTO CAPTURADO; PREPARANDO O GPS DO CADÁVER"
            );
            scheduleExternalReapply(
                "set",
                true,
                "reassumindo GPS sem protecao de waypoint",
                "🧭 Waypoint externo detectado; proteção desativada, GPS pode reassumir."
            );
        }
    }
    }
);
setWaypointHookOperational = setWaypointInterceptor !== null;

const clearWaypointInterceptor = attachOptionalInterceptor(
    "ClearWaypoint", CLEAR_ADDR, clearWaypointAddressReady, {
    onEnter(args) {
        capturedMap = args[0];

        if (insideClear || scriptStopping || multiplayerBlocked || !soloConfirmed)
            return;

        // Se havia um waypoint manual protegido, o Clear do jogador é justamente
        // o sinal que libera novamente o GPS automático.
        if (PROTECT_SETWAYPOINT && manualWaypointOverride) {
            releaseManualWaypointProtection();
            return;
        }

        // Sem estado manual para liberar, preserva o cooldown contra ecos do
        // próprio ClearWaypoint e evita ciclos de limpar/reaplicar.
        if (Date.now() <= internalHookIgnoreUntil)
            return;

        if (!pending.length)
            return;

        // Sem waypoint manual protegido, um Clear externo pode ser seguido da
        // reaplicação normal do GPS caso existam cadáveres pendentes.
        scheduleExternalReapply(
            "clear",
            false,
            "reaplicacao apos ClearWaypoint externo",
            PROTECT_SETWAYPOINT
                ? "🧭 Waypoint limpo; GPS automático continua disponível."
                : "⚠️ ClearWaypoint externo detectado; proteção desativada, GPS pode reaplicar."
        );
    }
    }
);
clearWaypointHookOperational = clearWaypointInterceptor !== null;

// -----------------------------------------------------------
// HTTP
// -----------------------------------------------------------

function keyFromPtr(p) {
    return p.toString();
}

function requestBodySignature(text) {
    return `${text.length}|${text.slice(0, 96)}|${text.slice(-160)}`;
}

// Copiar bytes dentro do hook; interpretar e analisar fora da chamada de
// rede do jogo. O objeto permanece vivo mesmo após CloseHandle ou reuso.
const deferredRequestStates = {};
function appendRequestBody(key, text) {
    if (!text || scriptStopping)
        return;
    let state = deferredRequestStates[key];
    if (!state) {
        state = {path:reqPaths[key] || "", body:"", signature:"",
            queued:false, captured_at:Date.now()};
        deferredRequestStates[key] = state;
    }
    const combined = state.body + text;
    state.body = combined.length <= 1024 * 1024
        ? combined : combined.slice(-1024 * 1024);
    state.captured_at = Date.now();
    if (state.queued)
        return;
    state.queued = true;
    setImmediate(function () {
        state.queued = false;
        if (scriptStopping || multiplayerBlocked)
            return;
        const body = state.body;
        if (!parseJson(body))
            return;
        const signature = requestBodySignature(body);
        if (signature === state.signature)
            return;
        state.signature = signature;
        const began = Date.now();
        try {
            handleBody(state.path, body);
        } catch (error) {
            sendLabEvent("deferred_request_error", {
                path:state.path, error:String(error)
            });
        } finally {
            if (state.path.toLowerCase().indexOf("harvest_animal2") >= 0)
                sendLabEvent("harvest_pipeline_timing", {
                    deferred_wait_ms:Math.max(0, began - state.captured_at),
                    handler_ms:Math.max(0, Date.now() - began),
                    pending_count:pending.length
                });
        }
    });
}

function handleBody(path, body) {
    if (!body || multiplayerBlocked || scriptStopping)
        return;

    const low = (path || "").toLowerCase();
    const o = parseJson(body);

    if (o) {
        inspectSession(path, o);
        observePlayerTelemetry(path, o);
        labRecordHttp(path, o);
    }

    if (multiplayerBlocked)
        return;

    if (low.indexOf("eventanimalclueinteraction") >= 0) {
        if (o)
            onRecoveredClue(path, o);
        return;
    }

    if (
        low.indexOf("animaldeathevent") >= 0 ||
        low.indexOf("animaldeathmpevent") >= 0
    ) {
        if (o)
            onDeath(path, o);
        return;
    }

    if (low.indexOf("harvest_animal2") >= 0) {
        if (!o)
            return;

        const confirmedHarvest = normalizeConfirmedHarvestPayload(o);
        if (confirmedHarvest !== null)
            enqueueHarvest(path, confirmedHarvest);
        return;
    }

    if (low.indexOf("confirmkillevent") >= 0) {
        if (o)
            observeHarvestScreen(path, o);
        return;
    }
}

const openReq = Module.findGlobalExportByName("WinHttpOpenRequest");

if (openReq) {
    attachOptionalInterceptor("WinHttpOpenRequest", openReq, true, {
        onEnter(args) {
            this.path = safeUtf16(args[2]);
        },

        onLeave(retval) {
            if (!retval.isNull()) {
                const k = keyFromPtr(retval);
                reqPaths[k] = this.path || "";
                reqBodies[k] = "";
                reqProcessed[k] = "";
                delete deferredRequestStates[k];
            }
        }
    });
}

const sendReq = Module.findGlobalExportByName("WinHttpSendRequest");

if (sendReq) {
    attachOptionalInterceptor("WinHttpSendRequest", sendReq, true, {
        onEnter(args) {
            const k = keyFromPtr(args[0]);
            const path = reqPaths[k] || "";
            const len = args[4].toUInt32();

            if (len > 0 && len < 8 * 1024 * 1024) {
                const text = bytesToText(safeBytes(args[3], len));

                if (text)
                    appendRequestBody(k, text);
            }
        }
    });
}

const writeData = Module.findGlobalExportByName("WinHttpWriteData");

if (writeData) {
    attachOptionalInterceptor("WinHttpWriteData", writeData, true, {
        onEnter(args) {
            const k = keyFromPtr(args[0]);
            const path = reqPaths[k] || "";
            const len = args[2].toUInt32();

            if (len > 0 && len < 8 * 1024 * 1024) {
                const text = bytesToText(safeBytes(args[1], len));

                if (text)
                    appendRequestBody(k, text);
            }
        }
    });
}

const closeHandle = Module.findGlobalExportByName("WinHttpCloseHandle");

if (closeHandle) {
    attachOptionalInterceptor("WinHttpCloseHandle", closeHandle, true, {
        onEnter(args) {
            const k = keyFromPtr(args[0]);
            delete reqPaths[k];
            delete reqBodies[k];
            delete reqProcessed[k];
            delete deferredRequestStates[k];
        }
    });
}

function shutdownGps(reason) {
    playerProbeSerial++;
    playerProbePhase = 0;
    stopPlayerProbeMonitor();
    if (scriptStopping)
        return {ok:true, already_stopped:true};

    sendLabEvent("laboratory_shutdown_requested", {
        reason:reason || "programa fechado",
        player:getPlayerPos(),
        pending:labPendingSnapshot()
    });
    emitRuntimeState("encerramento do laboratório");
    forensicScanSerial++;
    stableAvailabilityScanSerial++;
    stableAvailabilityScanRunning = false;
    bulkDeadBodyScanSerial++;
    bulkDeadBodyScanRunning = false;
    bulkDeadBodyScanTimer = clearTimeoutSafe(bulkDeadBodyScanTimer);
    collectionDebugArmTimer = clearTimeoutSafe(collectionDebugArmTimer);
    labStopRegistryMonitor();
    stopHudCountdown("laboratório encerrado");
    scriptStopping = true;
    const shouldClear = markerOwned || currentKey !== "";
    pending = [];
    labDeferredStableHarvests = [];
    manualWaypointOverride = false;
    initialOldCorpseScanTimer = clearTimeoutSafe(initialOldCorpseScanTimer);
    initialOldCorpseScanFinished = true;
    cancelHudWarning(true);
    cancelReserveCandidate();
    cancelWaypointWork();

    if (nearestInterval !== null) {
        try { clearInterval(nearestInterval); }
        catch (_) {}
        nearestInterval = null;
    }

    if (shouldClear) {
        const restored = restoreRegistryWaypointFallback(
            reason || "programa fechado"
        );
        if (!restored)
            clearMarkerInternal();
    }

    resetMarkerState();
    finalClearDone = true;
    log(`🧹 Laboratório encerrado (${reason || "programa fechado"}).`);
    sendGpsStatus("stopped", reason || "programa fechado");
    shutdownHud();
    return {ok:true};
}

rpc.exports = {
    stopgps() {
        return shutdownGps("solicitado pelo programa");
    },

    sethudcorner(index) {
        return setHudCorner(index);
    },

    cyclehudcorner() {
        return setHudCorner((hudCorner + 1) % 4);
    },

    togglehudvisible() {
        return toggleHudUserVisible();
    },

    sethudvisible(visible) {
        return setHudUserVisible(visible);
    },

    forceoldcorpsescan() {
        // Uma única tecla confere a posição e os IDs. Não execute a função
        // antiga de GetPlayerEntity em builds que não foram verificadas.
        const player = getPlayerPos();
        const position = player === null ? armPlayerProbe() :
            {ok:true,source:"position_available"};
        const corpses = forceOldCorpseScan();
        return Object.assign({}, corpses, {position:position});
    },

    armplayerpositionprobe() {
        return armPlayerProbe();
    },

    labmanualsnapshot() {
        const now = Date.now();

        if (now - forensicLastTriggerAt < FORENSIC_TRIGGER_DEBOUNCE_MS) {
            return {
                ok:false,
                reason:"trigger_debounced",
                scan_running:stableAvailabilityScanRunning ||
                    (initialOldCorpseScanStarted && !initialOldCorpseScanFinished)
            };
        }

        forensicLastTriggerAt = now;
        sendLabEvent("stable_id_manual_requested", {
            player:getPlayerPos(),
            pending:labPendingSnapshot(),
            scan_running:stableAvailabilityScanRunning ||
                (initialOldCorpseScanStarted && !initialOldCorpseScanFinished)
        });
        const registry = labCaptureRegistry(
            "stable_id_manual_imediato",
            true,
            true
        );
        const scan = forceOldCorpseScan();
        return {
            ok:scan.ok === true,
            reason:scan.reason || "",
            type39_count:registry.type39_count,
            registry_sequence:registry.sequence
        };
    },

    getgameidentity() {
        return {
            identity:GAME_IDENTITY,
            pid:Number(Process.id),
            main_module_base:BASE.toString()
        };
    },

    importharvesteddnas(keys) {
        const imported = Array.isArray(keys) ? keys : [];
        let accepted = 0;

        for (let i=0; i<imported.length; i++) {
            const key = String(imported[i] ?? "").trim();

            if (key === "" || key.length > 256 || harvestedDnas.has(key))
                continue;

            harvestedDnas.add(key);
            accepted++;
        }

        sendLabEvent("harvest_dna_state_imported", {
            game_identity:GAME_IDENTITY,
            imported:accepted,
            total:harvestedDnas.size
        });
        return {ok:true, imported:accepted, total:harvestedDnas.size};
    },

    importlabstate(state) {
        return importLabRuntimeState(state);
    },

    status() {
        return {
            pending:pending.length,
            current_key:currentKey,
            current_index:currentIndex,
            switching_key:switchingKey,
            marker_owned:markerOwned,
            solo_confirmed:soloConfirmed,
            multiplayer_blocked:multiplayerBlocked,
            active_reserve:activeReserve,
            reserve_candidate:reserveCandidate,
            reserve_candidate_pending:reserveCandidateTimer !== null,
            target_game_build:TARGET_GAME_BUILD,
            detected_game_build:detectedGameBuild,
            unsupported_build_blocked:unsupportedBuildBlocked,
            registry_slot:pstr(activeRegistrySlot),
            registry_slot_reference:pstr(REFERENCE_REGISTRY_SLOT),
            registry_discovered:registryDiscoverySelected,
            registry_discovery_attempts:registryDiscoveryAttempts,
            registry_discovery_candidates:registryDiscoveryLastCandidates,
            map_slot_reference:pstr(MAP_SLOT),
            map_slot_active:pstr(activeMapSlot),
            map_pointer:pstr(getMap()),
            map_slot_discovery_running:rayXMapSlotDiscoveryRunning,
            map_slot_discovery_done_for_pointer:
                rayXMapSlotDiscoveryDoneForPointer,
            registry_waypoint_fallback:REGISTRY_WAYPOINT_FALLBACK,
            waypoint_setter_hook_ready:
                waypointConfirmedSetterInterceptor !== null,
            waypoint_setter_address:waypointConfirmedSetterAddress,
            waypoint_setter_captures:waypointConfirmedSetterCaptures,
            registry_waypoint_descriptor:registryWaypointLastDescriptor,
            registry_waypoint_target_key:registryWaypointLastTargetKey,
            registry_waypoint_last_apply_at:registryWaypointLastApplyAt,
            player_entity_pointer:pstr(cachedEntity),
            hud_corner:hudCorner,
            hud_corner_name:hudCornerName(hudCorner),
            hud_loaded:hudModule !== null,
            hud_user_visible:hudUserVisible,
            hud_game_visible:hudGameVisible,
            hud_auto_visibility:hudAutoVisibilitySupported,
            hud_warning_active:hudWarningActive,
            hud_warning_shown:hudWarningShown,
            protect_setwaypoint:PROTECT_SETWAYPOINT,
            manual_waypoint_override:manualWaypointOverride,
            harvested_dna_count:harvestedDnas.size,
            restored_pending_buffer:restoredPendingBuffer.length,
            runtime_state_imported:runtimeStateImported,
            forensic_target_count:forensicTargets.length,
            forensic_candidate_groups:Object.keys(forensicCandidates).length,
            forensic_scan_running:forensicScanRunning,
            stable_availability_scan_running:stableAvailabilityScanRunning,
            stable_metadata_range_base:stableMetadataRangeBase,
            stable_metadata_range_size:stableMetadataRangeSize,
            harvest_queue_length:harvestQueue.length,
            lab_version:LAB_VERSION,
            lab_snapshot_sequence:labSnapshotSequence
        };
    }
};

// -----------------------------------------------------------
// INICIAL
// -----------------------------------------------------------

const dnaRuleSelfTest = runDnaRuleSelfTest();
sendLabEvent("dna_rule_self_test", dnaRuleSelfTest);
if (!dnaRuleSelfTest.ok)
    log(`❌ AUTOTESTE DE DNA FALHOU: ${dnaRuleSelfTest.error}`);

forensicMemoryApiStatus = runForensicMemoryApiSelfTest();
sendLabEvent("forensic_memory_api_self_test", forensicMemoryApiStatus);
if (forensicMemoryApiStatus.ok) {
    log(
        "✅ AUTOTESTE DA MEMÓRIA: leitura bruta e padrão do peso preparados."
    );
} else {
    log(
        "❌ AUTOTESTE DA MEMÓRIA FALHOU: " +
        String(forensicMemoryApiStatus.error || "motivo desconhecido")
    );
}

setTimeout(function () {
    const map = getSingletonMap();
    log(`Mapa singleton: ${pstr(map)}`);

    const pp = getPlayerPos();

    if (pp) {
        log(
            `Jogador inicial corrigido: ` +
            `(${pp.x.toFixed(2)}, ${pp.y.toFixed(2)}, ${pp.z.toFixed(2)})`
        );
    } else {
        log("Jogador ainda nao disponivel; tentarei novamente automaticamente.");
    }
}, 250);

sendGpsStatus(
    "loaded",
    SOLO_ONLY_PROTECTION ? "aguardando confirmação solo" : "proteção solo desativada"
);
initHudAutoVisibility();
hudInitTimer = setTimeout(initHud, 700);
labStartRegistryMonitor();
sendLabEvent("ray_x_native_addresses", {
    set_waypoint:{
        rva:"0x" + RVA_SET_WAYPOINT.toString(16),
        address:pstr(SET_ADDR),
        ready:setWaypointAddressReady,
        function_ready:SetWaypoint !== null,
        code_hex:labHex(SET_ADDR, 96)
    },
    clear_waypoint:{
        rva:"0x" + RVA_CLEAR_WAYPOINT.toString(16),
        address:pstr(CLEAR_ADDR),
        ready:clearWaypointAddressReady,
        function_ready:ClearWaypoint !== null,
        code_hex:labHex(CLEAR_ADDR, 96)
    },
    captured_clear_waypoint:{
        rva:"0x" + RVA_CAPTURED_CLEAR_WAYPOINT.toString(16),
        address:pstr(CAPTURED_CLEAR_ADDR),
        signature_matches:labHex(CAPTURED_CLEAR_ADDR,
            CAPTURED_CLEAR_SIGNATURE.length / 2) ===
            CAPTURED_CLEAR_SIGNATURE,
        code_hex:labHex(CAPTURED_CLEAR_ADDR, 96)
    },
    map_singleton:{
        rva:"0x" + RVA_MAP_SINGLETON.toString(16),
        reference_slot:pstr(MAP_SLOT),
        active_slot:pstr(activeMapSlot),
        ready:mapSingletonAddressReady,
        slot_bytes_hex:labHex(MAP_SLOT, 64),
        pointer:pstr(getSingletonMap())
    },
    automatic_map_slot:{
        rva:"0x" + RVA_AUTO_MAP_SLOT.toString(16),
        reference_slot:pstr(AUTO_MAP_SLOT),
        ready:autoMapSlotAddressReady,
        slot_bytes_hex:labHex(AUTO_MAP_SLOT, 32),
        validated_map_pointer:pstr(getSingletonMap())
    },
    get_player_entity:{
        rva:"0x" + RVA_GET_PLAYER_ENTITY.toString(16),
        address:pstr(GET_ENTITY),
        ready:getPlayerEntityAddressReady,
        function_ready:GetPlayerEntity !== null,
        code_hex:labHex(GET_ENTITY, 96)
    }
});
sendLabEvent("laboratory_loaded", {
    base_version:"0.5.1",
    target_game_build:TARGET_GAME_BUILD,
    game_update_lock:false,
    compatibility_mode:true,
    set_waypoint_address_ready:setWaypointAddressReady,
    set_waypoint_hook_operational:setWaypointHookOperational,
    clear_waypoint_address_ready:clearWaypointAddressReady,
    clear_waypoint_hook_operational:clearWaypointHookOperational,
    captured_clear_waypoint_rva:
        "0x" + RVA_CAPTURED_CLEAR_WAYPOINT.toString(16),
    get_player_entity_address_ready:getPlayerEntityAddressReady,
    map_singleton_address_ready:mapSingletonAddressReady,
    corpse_registry_address_ready:oldCorpseSearchAddressReady,
    registry_slot_reference:pstr(REFERENCE_REGISTRY_SLOT),
    registry_auto_discovery:true,
    ray_x_trace:true,
    ray_x_catalog_lifecycle:true,
    ray_x_clue_association:true,
    raw_clue_catalog_merge:true,
    raw_clue_catalog_max_xz_m:RAW_CLUE_CATALOG_MAX_XZ_M,
    raw_clue_catalog_max_y_m:RAW_CLUE_CATALOG_MAX_Y_M,
    raw_clue_catalog_min_gap_m:RAW_CLUE_CATALOG_MIN_GAP_M,
    raw_clue_catalog_max_ratio:RAW_CLUE_CATALOG_MAX_RATIO,
    clue_body_waypoint_priority:true,
    clue_catalog_alias_merges_into_unique_partial_dna:true,
    harvest_removes_unique_enriched_partial_dna_alias:true,
    ray_x_harvest_association:true,
    ray_x_waypoint_trace:true,
    ray_x_map_slot_discovery_automatic:true,
    automatic_first_waypoint_requires_manual_click:false,
    automatic_first_waypoint_requires_unique_signature:true,
    automatic_first_waypoint_requires_unique_map_slot:true,
    automatic_first_waypoint_verifies_registry:true,
    registry_waypoint_fallback:REGISTRY_WAYPOINT_FALLBACK,
    waypoint_writer_monitor:false,
    waypoint_writer_monitor_ms:WAYPOINT_WRITER_MONITOR_MS,
    waypoint_hardware_watch:WAYPOINT_HARDWARE_WATCH_ENABLED,
    waypoint_hardware_watch_timeout_ms:WAYPOINT_HARDWARE_WATCH_TIMEOUT_MS,
    waypoint_hardware_watch_targets:[
        "registry_end_pointer",
        "next_descriptor_type",
        "next_descriptor_registry_id"
    ],
    waypoint_object_vtable_diagnostics:false,
    waypoint_id_constant_scan:WAYPOINT_ID_CONSTANT_SCAN_ENABLED,
    registry_waypoint_type:MANUAL_WAYPOINT_TYPE,
    registry_waypoint_id:MANUAL_WAYPOINT_REGISTRY_ID,
    memory_writes_added_by_lab:true,
    memory_write_scope:"função nativa validada do jogo; sem escrita direta no catálogo de waypoints",
    registry_monitor_ms:LAB_REGISTRY_MONITOR_MS,
    strict_dna_collection:true,
    death_signal_automatic_workflow:true,
    collection_debug_mode:COLLECTION_DEBUG_MODE,
    collection_debug_target_dna:COLLECTION_DEBUG_TARGET_DNA,
    collection_debug_max_distance:COLLECTION_DEBUG_MAX_DISTANCE,
    collection_debug_tracks_fixed_record:true,
    collection_debug_rescans_after_harvest:true,
    location_used_only_for_waypoint:true,
    strict_registry_id_identity:true,
    registry_position_deduplication:false,
    nearest_fallback_uses_last_collected_body:true,
    harvested_dna_persistence:true,
    pending_corpse_persistence:true,
    stable_id_availability_scan:true,
    stable_record_exact_range_size:STABLE_METADATA_EXACT_RANGE_SIZE,
    stable_record_availability_offset:STABLE_RECORD_AVAILABLE_OFFSET,
    alternative_layout_scan:true,
    alternative_layout_weight_offset:ALTERNATIVE_RECORD_WEIGHT_OFFSET,
    alternative_layout_availability_offset:
        ALTERNATIVE_RECORD_AVAILABLE_OFFSET,
    alternative_layout_lifecycle_offset:
        ALTERNATIVE_RECORD_LIFECYCLE_OFFSET,
    collection_waits_for_exact_registry_id:true,
    deferred_stable_harvest_window_ms:
        DEFERRED_STABLE_HARVEST_WINDOW_MS,
    deferred_stable_harvest_wait_seconds:
        DEFERRED_STABLE_HARVEST_WAIT_SECONDS,
    proximity_used_for_harvest_identity:false,
    direct_dna_stable_range_scan:true,
    expanded_dna_scan_on_unresolved:!COLLECTION_DEBUG_MODE,
    expanded_dna_scan_max_bytes_per_target:
        STABLE_METADATA_DISCOVERY_MAX_BYTES,
    direct_dna_preserves_death_position:true,
    forensic_max_scan_bytes:FORENSIC_SCAN_MAX_BYTES,
    forensic_saved_candidate_limit:FORENSIC_MAX_SAVED_CANDIDATES,
    forensic_memory_api_self_test_ok:forensicMemoryApiStatus.ok,
    dna_self_test_ok:dnaRuleSelfTest.ok,
    dna_self_test_checks:dnaRuleSelfTest.checks
});
log("Turbo Hunter 0.5.7 carregado.");
log("🧭 TURBO HUNTER 0.5.7: busca e marcação automáticas; F6 atualiza IDs quando necessário.");
log("🧭 Você pode caçar livremente; a marcação é automática quando validada.");
log("🧭 Posição do jogador: leitura direta de três referências validadas; F6 também recupera a posição.");
log("🧬 DNA: pista identifica o animal; distância entre pista e ID não prova identidade.");
log(`🧩 SEM TRAVA DE ATUALIZAÇÃO: build ${TARGET_GAME_BUILD} é somente referência; mudanças geram aviso e o mod continua.`);
if (SOLO_ONLY_PROTECTION)
    log("🔒 PROTEÇÃO SOLO ATIVA: multiplayer será bloqueado.");
else
    log("⚠️ PROTEÇÃO SOLO DESATIVADA: multiplayer permitido por conta e risco.");
if (PROTECT_SETWAYPOINT)
    log("🧭 PROTEÇÃO DE WAYPOINT ATIVA: waypoint do jogador será respeitado até ele limpar o point.");
else
    log("🧭 PROTEÇÃO DE WAYPOINT DESATIVADA: Turbo Hunter pode mover/reassumir o waypoint automaticamente.");
setTimeout(function () {
    logAutoWaypointPreflight("início sem marcador manual", true);
    scanWaypointIdConstantReferences(
        "localizar assinatura do waypoint sem clique manual"
    );
}, 1500);
"""

def prepare_log_files():
    """Guarda somente a sessão anterior; o log continua pequeno e previsível."""
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 0:
            PREVIOUS_LOG_FILE.write_bytes(LOG_FILE.read_bytes())
    except Exception as exc:
        print_console("AVISO: nao consegui preservar o log anterior: " + str(exc))

    LOG_FILE.write_text("", encoding="utf-8")


def log(line):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    text = f"[{stamp}] {line}"

    with log_lock:
        with LOG_FILE.open("a", encoding="utf-8") as file:
            file.write(text + "\n")
        print_console(text)


def save_lab_event(event, data=None):
    """Append one machine-readable record without risking the main tracker."""
    record = {
        "saved_at": datetime.now().isoformat(timespec="milliseconds"),
        "lab_version": LAB_VERSION,
        "base_version": "0.5.1",
        "session_id": lab_session_id,
        "machine_label": MACHINE_LABEL,
        "event": str(event or "unknown"),
        "data": data if isinstance(data, dict) else {"value": data},
    }

    try:
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with lab_lock:
            with LAB_LIVE_FILE.open("a", encoding="utf-8") as file:
                file.write(line + "\n")
    except Exception as exc:
        print_console("AVISO LAB: nao consegui gravar evento: " + str(exc))


def _read_lab_state_unlocked():
    sources = [LAB_STATE_FILE]
    if LEGACY_LAB_STATE_FILE != LAB_STATE_FILE:
        sources.append(LEGACY_LAB_STATE_FILE)
    for source in sources:
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, ValueError, TypeError):
            continue
    return {}


def _empty_lab_state(game_identity):
    return {
        "format_version": 2,
        "lab_version": LAB_VERSION,
        "game_identity": str(game_identity),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "harvested_dna_keys": [],
        "pending_corpses": [],
        "navigation_anchor": None,
        "forensic_targets": [],
        "forensic_candidates": {},
        "stable_metadata_range_base": "",
        "stable_metadata_range_size": 0,
        "stable_metadata_range_protection": "",
        "stable_metadata_range_file": "",
    }


def _write_lab_state_unlocked(payload):
    data = dict(payload) if isinstance(payload, dict) else {}
    data["format_version"] = 2
    data["lab_version"] = LAB_VERSION
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    LAB_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = LAB_STATE_FILE.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(LAB_STATE_FILE)


def initialize_lab_state(game_identity):
    """Reuse the forensic state only while attached to the same running game."""
    identity = str(game_identity or "").strip()
    if not identity:
        return _empty_lab_state("")

    with harvest_state_lock:
        data = _read_lab_state_unlocked()
        if str(data.get("game_identity", "")) != identity:
            data = _empty_lab_state(identity)
        else:
            raw_keys = data.get("harvested_dna_keys", [])
            if not isinstance(raw_keys, list):
                raw_keys = []
            data["harvested_dna_keys"] = sorted(
                {str(key) for key in raw_keys if str(key)}
            )
            if not isinstance(data.get("pending_corpses"), list):
                data["pending_corpses"] = []
            if not isinstance(data.get("navigation_anchor"), dict):
                data["navigation_anchor"] = None
            if not isinstance(data.get("forensic_targets"), list):
                data["forensic_targets"] = []
            if not isinstance(data.get("forensic_candidates"), dict):
                data["forensic_candidates"] = {}
            if not isinstance(data.get("stable_metadata_range_base"), str):
                data["stable_metadata_range_base"] = ""
            if not isinstance(data.get("stable_metadata_range_size"), int):
                data["stable_metadata_range_size"] = 0
            if not isinstance(data.get("stable_metadata_range_protection"), str):
                data["stable_metadata_range_protection"] = ""
            if not isinstance(data.get("stable_metadata_range_file"), str):
                data["stable_metadata_range_file"] = ""

        _write_lab_state_unlocked(data)
        return data


def persist_harvested_dna(data):
    """Persist one confirmed DNA so restarting only Turbo Hunter is safe."""
    if not isinstance(data, dict):
        return

    identity = str(data.get("game_identity", "")).strip()
    harvest_key = str(data.get("harvest_key", "")).strip()
    if not identity or not harvest_key or len(harvest_key) > 256:
        return

    try:
        with harvest_state_lock:
            state = _read_lab_state_unlocked()
            if str(state.get("game_identity", "")) == identity:
                raw_keys = state.get("harvested_dna_keys", [])
                keys = {str(key) for key in raw_keys if str(key)} if isinstance(raw_keys, list) else set()
            else:
                state = _empty_lab_state(identity)
                keys = set()

            keys.add(harvest_key)
            state["harvested_dna_keys"] = sorted(keys)
            _write_lab_state_unlocked(state)
    except Exception as exc:
        print_console("AVISO LAB: nao consegui preservar DNA coletado: " + str(exc))


def persist_runtime_state(data):
    """Persist pending corpses and memory candidates for the same game process."""
    if not isinstance(data, dict):
        return

    identity = str(data.get("game_identity", "")).strip()
    if not identity:
        return

    incoming_keys = data.get("harvested_dna_keys", [])
    pending = data.get("pending_corpses", [])
    navigation_anchor = data.get("navigation_anchor")
    targets = data.get("forensic_targets", [])
    candidates = data.get("forensic_candidates", {})
    stable_range_base = str(data.get("stable_metadata_range_base", ""))
    stable_range_size = data.get("stable_metadata_range_size", 0)
    stable_range_protection = str(
        data.get("stable_metadata_range_protection", "")
    )
    stable_range_file = str(data.get("stable_metadata_range_file", ""))

    if not isinstance(incoming_keys, list):
        incoming_keys = []
    if not isinstance(pending, list):
        pending = []
    if not isinstance(navigation_anchor, dict):
        navigation_anchor = None
    if not isinstance(targets, list):
        targets = []
    if not isinstance(candidates, dict):
        candidates = {}
    if not isinstance(stable_range_size, int):
        stable_range_size = 0

    try:
        with harvest_state_lock:
            state = _read_lab_state_unlocked()
            if str(state.get("game_identity", "")) != identity:
                state = _empty_lab_state(identity)

            current_keys = state.get("harvested_dna_keys", [])
            if not isinstance(current_keys, list):
                current_keys = []
            keys = {str(key) for key in current_keys if str(key)}
            keys.update(str(key) for key in incoming_keys if str(key))
            state["harvested_dna_keys"] = sorted(keys)
            state["pending_corpses"] = pending[:128]
            state["navigation_anchor"] = navigation_anchor
            state["forensic_targets"] = targets[:128]
            state["forensic_candidates"] = {
                str(key): value[:64]
                for key, value in list(candidates.items())[:128]
                if isinstance(value, list)
            }
            state["stable_metadata_range_base"] = stable_range_base
            state["stable_metadata_range_size"] = stable_range_size
            state["stable_metadata_range_protection"] = stable_range_protection
            state["stable_metadata_range_file"] = stable_range_file
            _write_lab_state_unlocked(state)
    except Exception as exc:
        print_console(
            "AVISO LAB: nao consegui preservar o estado forense: " + str(exc)
        )


def build_lab_package():
    """Create one ready-to-send ZIP containing both runs and installer evidence."""
    root_dir = Path(__file__).resolve().parents[2]
    runtime_dir = Path(__file__).resolve().parents[1] / "runtime"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destination = root_dir / (
        f"{LAB_PACKAGE_PREFIX}_{MACHINE_LABEL}_{stamp}.zip"
    )
    serial = 1

    while destination.exists():
        destination = root_dir / (
            f"{LAB_PACKAGE_PREFIX}_{MACHINE_LABEL}_{stamp}_{serial:02d}.zip"
        )
        serial += 1

    files = (
        (LOG_FILE, "logs/turbo_hunter_log.txt"),
        (PREVIOUS_LOG_FILE, "logs/turbo_hunter_log_anterior.txt"),
        (LAB_LIVE_FILE, "logs/lab_id_vivo_live.jsonl"),
        (LAB_STATE_FILE, "logs/lab_id_vivo_state.json"),
        (HUD_CONFIG_FILE, "hud_config.json"),
        (runtime_dir / "instalacao.log", "instalacao/instalacao.log"),
        (runtime_dir / "install_status.json", "instalacao/install_status.json"),
        (runtime_dir / "install_ok.txt", "instalacao/install_ok.txt"),
    )

    summary = (
        "Turbo Hunter 0.5.7\r\n"
        "Base oficial: 0.5.1\r\n"
        f"Computador: {MACHINE_LABEL}\r\n"
        f"Sessao mais recente: {lab_session_id}\r\n"
        "\r\n"
        "Este pacote foi criado automaticamente ao encerrar o mod.\r\n"
        "Envie o ZIP inteiro para analise; nao apague nem edite os arquivos internos.\r\n"
    )

    try:
        with zipfile.ZipFile(
            destination,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            archive.writestr("LEIA-ME DA CAPTURA.txt", summary)
            for source, archive_name in files:
                if source.exists() and source.is_file():
                    archive.write(source, arcname=archive_name)
        return destination
    except Exception as exc:
        print_console("AVISO LAB: nao consegui montar o pacote final: " + str(exc))
        return None


HUD_CORNERS = {
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


def detect_windows_language():
    if sys.platform == "win32":
        try:
            buffer = ctypes.create_unicode_buffer(85)
            if ctypes.windll.kernel32.GetUserDefaultLocaleName(buffer, len(buffer)):
                return "pt-BR" if buffer.value.lower().startswith("pt") else "zh-CN" if buffer.value.lower().startswith("zh") else "en"
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


def load_hud_config():
    data = {
        "corner": 3,
        "name": HUD_CORNERS["pt-BR"][3],
        "solo_only": 1,
        "protect_setwaypoint": 0,
        "language": "auto",
        "lab_config_version": LAB_VERSION,
    }

    try:
        loaded = json.loads(HUD_CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data.update(loaded)
    except Exception:
        pass

    try:
        corner = int(data.get("corner", 1))
    except Exception:
        corner = 1

    if not 0 <= corner <= 3:
        corner = 1

    try:
        solo_only = 1 if int(data.get("solo_only", 1)) != 0 else 0
    except Exception:
        solo_only = 1

    try:
        protect_setwaypoint = 1 if int(data.get("protect_setwaypoint", 0)) != 0 else 0
    except Exception:
        protect_setwaypoint = 0

    language = str(data.get("language", "auto") or "auto")
    if language.lower() not in ("auto", "en", "pt-br", "pt_br", "pt", "zh-cn", "zh_cn", "zh"):
        language = "auto"
    resolved_language = resolve_language(language)

    return {
        "corner": corner,
        "name": HUD_CORNERS[resolved_language][corner],
        "solo_only": solo_only,
        "protect_setwaypoint": protect_setwaypoint,
        "language": language,
        "resolved_language": resolved_language,
        "lab_config_version": LAB_VERSION,
    }


def save_hud_config(config):
    try:
        corner = int(config.get("corner", 1))
        if not 0 <= corner <= 3:
            corner = 1

        solo_only = 1 if int(config.get("solo_only", 0)) != 0 else 0
        protect_setwaypoint = 1 if int(config.get("protect_setwaypoint", 0)) != 0 else 0
        language = str(config.get("language", "auto") or "auto")
        resolved_language = resolve_language(language)

        HUD_CONFIG_FILE.write_text(
            json.dumps(
                {
                    "corner": corner,
                    "name": HUD_CORNERS[resolved_language][corner],
                    "solo_only": solo_only,
                    "protect_setwaypoint": protect_setwaypoint,
                    "language": language,
                    "lab_config_version": LAB_VERSION,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception as exc:
        log("AVISO: nao consegui salvar hud_config.json: " + str(exc))


def build_js_source(solo_only, protect_setwaypoint, language):
    if not HUD_C_FILE.exists():
        raise FileNotFoundError("Arquivo do HUD ausente: " + HUD_C_FILE.name)

    resolved_language = resolve_language(language)
    hud_source = HUD_C_FILE.read_text(encoding="utf-8")
    if resolved_language == "pt-BR":
        waiting_text = "AGUARDANDO SOLO"
        searching_text = "PROCURANDO ABATES"
        memory_text = "ANALISANDO MEMORIA"
        counter_prefix = "ABATES: "
        warning_text = "RECOLHA OS ANIMAIS"
        lab_texts = {
            "__HUD_LAB_ATTACK_TEXT__": "ATAQUE 1 ANIMAL",
            "__HUD_LAB_DONT_COLLECT_TEXT__": "NAO COLETE",
            "__HUD_LAB_ANALYZING_TEXT__": "ANALISANDO...",
            "__HUD_LAB_CAN_COLLECT_TEXT__": "PODE COLETAR",
            "__HUD_LAB_SEE_CLUE_TEXT__": "EXAMINE PISTA DE SANGUE",
            "__HUD_LAB_ERROR_TEXT__": "ERRO - NAO COLETE",
            "__HUD_LAB_STOP_TEXT__": "HORA DE PARAR",
            "__HUD_LAB_SEND_ZIP_TEXT__": "ENVIAR ZIP LOG",
            "__HUD_LAB_GPS_ACTION_TEXT__": "REMOVA PONTO NO MAPA",
            "__HUD_LAB_COUNTDOWN_TEXT__": "AGUARDE ",
            "__HUD_LAB_COLLECT_TEXT__": "COLETE UM ANIMAL | RESTAM ",
            "__HUD_LAB_SHOT_TEXT__": "ATIRE E FIQUE PARADO",
            "__HUD_LAB_MOVE_TEXT__": "CAMINHE 20M E ATIRE",
            "__HUD_POSITION_START_TEXT__": "APERTE F6 PARA POSICAO",
            "__HUD_POSITION_WALK_TEXT__": "CAMINHE 20M SEM ATIRAR",
            "__HUD_POSITION_SHOOT_TEXT__": "AGORA ATIRE PARADO",
            "__HUD_POSITION_READY_TEXT__": "POSICAO CONFIRMADA",
            "__HUD_POSITION_ZIP_TEXT__": "PARE E ENVIE ZIP",
            "__HUD_POSITION_SCANNING_TEXT__": "BUSCA POSICAO ",
        }
    else:
        waiting_text = "WAITING SOLO"
        searching_text = "SEARCHING KILLS"
        memory_text = "SCANNING MEMORY"
        counter_prefix = "KILLS: "
        warning_text = "COLLECT ANIMALS"
        lab_texts = {
            "__HUD_LAB_ATTACK_TEXT__": "SHOOT 1 ANIMAL",
            "__HUD_LAB_DONT_COLLECT_TEXT__": "DO NOT COLLECT",
            "__HUD_LAB_ANALYZING_TEXT__": "ANALYZING...",
            "__HUD_LAB_CAN_COLLECT_TEXT__": "YOU MAY COLLECT",
            "__HUD_LAB_SEE_CLUE_TEXT__": "CHECK BLOOD CLUE",
            "__HUD_LAB_ERROR_TEXT__": "ERROR - DO NOT COLLECT",
            "__HUD_LAB_STOP_TEXT__": "TIME TO STOP",
            "__HUD_LAB_SEND_ZIP_TEXT__": "SEND ZIP LOG",
            "__HUD_LAB_GPS_ACTION_TEXT__": "REMOVE MAP WAYPOINT",
            "__HUD_LAB_COUNTDOWN_TEXT__": "WAIT ",
            "__HUD_LAB_COLLECT_TEXT__": "COLLECT ONE ANIMAL | LEFT ",
            "__HUD_LAB_SHOT_TEXT__": "SHOOT AND STAND STILL",
            "__HUD_LAB_MOVE_TEXT__": "MOVE 20M AND SHOOT",
            "__HUD_POSITION_START_TEXT__": "PRESS F6 FOR POSITION",
            "__HUD_POSITION_WALK_TEXT__": "WALK 20M WITHOUT SHOOTING",
            "__HUD_POSITION_SHOOT_TEXT__": "NOW SHOOT AND STAND STILL",
            "__HUD_POSITION_READY_TEXT__": "POSITION CONFIRMED",
            "__HUD_POSITION_ZIP_TEXT__": "STOP AND SEND ZIP",
            "__HUD_POSITION_SCANNING_TEXT__": "POSITION SCAN ",
        }
    hud_source = hud_source.replace("__HUD_WAITING_TEXT__", waiting_text)
    hud_source = hud_source.replace("__HUD_SEARCHING_TEXT__", searching_text)
    hud_source = hud_source.replace("__HUD_MEMORY_TEXT__", memory_text)
    hud_source = hud_source.replace("__HUD_COUNTER_PREFIX__", counter_prefix)
    hud_source = hud_source.replace("__HUD_WARNING_TEXT__", warning_text)
    for placeholder, value in lab_texts.items():
        hud_source = hud_source.replace(placeholder, value)
    hud_placeholder = '"__HUD_C_SOURCE_PLACEHOLDER__"'
    solo_placeholder = "__SOLO_ONLY_PLACEHOLDER__"
    protect_waypoint_placeholder = "__PROTECT_SETWAYPOINT_PLACEHOLDER__"

    if hud_placeholder not in JS:
        raise RuntimeError("Marcador interno do HUD nao foi encontrado")
    if solo_placeholder not in JS:
        raise RuntimeError("Marcador interno da protecao solo nao foi encontrado")
    if protect_waypoint_placeholder not in JS:
        raise RuntimeError("Marcador interno da protecao de waypoint nao foi encontrado")

    source = JS.replace(hud_placeholder, json.dumps(hud_source))
    source = source.replace(solo_placeholder, "true" if int(solo_only) != 0 else "false")
    return source.replace(
        protect_waypoint_placeholder,
        "true" if int(protect_setwaypoint) != 0 else "false",
    )


def load_hud_corner():
    return int(load_hud_config()["corner"])


def save_hud_corner(corner):
    config = load_hud_config()
    config["corner"] = int(corner)
    config["name"] = HUD_CORNERS[config["resolved_language"]][int(corner)]
    save_hud_config(config)


def key_pressed(virtual_key):
    if sys.platform != "win32":
        return False

    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(virtual_key) & 1)
    except Exception:
        return False


def f8_pressed():
    return key_pressed(0x77)


def memory_scan_pressed():
    # VK_MULTIPLY: asterisco do teclado numérico (NumPad *).
    return key_pressed(0x6A)


def f6_pressed():
    return key_pressed(0x75)


def f9_pressed():
    return key_pressed(0x78)


def consume_memory_scan_request():
    """Return the source of one scan request and consume the GUI trigger."""
    requested_by_button = False
    if MEMORY_SCAN_FILE.exists():
        try:
            MEMORY_SCAN_FILE.unlink()
            requested_by_button = True
        except OSError:
            pass

    if requested_by_button:
        return "botão VALIDAR IDS"
    if memory_scan_pressed():
        return "NumPad *"
    return ""


def on_message(message, data):
    if message.get("type") == "error":
        save_lab_event("frida_error", {"message": message})
        log("FRIDA ERRO: " + str(message))
        stop_event.set()
        return

    payload = message.get("payload", {})

    if not isinstance(payload, dict):
        return

    payload_type = str(payload.get("type", "message"))
    if payload_type == "lab_event":
        event_name = str(payload.get("event", "lab_event"))
        event_data = payload.get("data", {})
        save_lab_event(event_name, event_data)
        if event_name == "harvest_dna_committed":
            persist_harvested_dna(event_data)
        elif event_name == "runtime_state_snapshot":
            persist_runtime_state(event_data)
    else:
        save_lab_event("frida_" + payload_type, payload)

    if payload.get("type") == "log":
        log(payload.get("text", ""))

    elif payload.get("type") == "multiplayer_block":
        reason = payload.get("reason", "multiplayer")
        log("PROTECAO SOLO acionada: " + reason)
        stop_event.set()

    elif payload.get("type") == "fatal_block":
        reason = payload.get("reason", "falha de compatibilidade")
        log("ERRO DE COMPATIBILIDADE: " + reason)
        stop_event.set()


def on_session_detached(reason, crash=None):
    if expected_detach_event.is_set():
        return

    if reason == "process-terminated" and crash is None:
        log("Jogo encerrado normalmente. Script finalizado.")
        stop_event.set()
        return

    details = ""
    if crash is not None:
        details = " | detalhes=" + str(crash)

    log(
        "ERRO: jogo ou instrumentacao desconectou inesperadamente "
        f"(motivo={reason}){details}"
    )
    stop_event.set()


def run_console_loop(script, initial_corner):
    corner = initial_corner
    last_memory_scan_at = 0.0

    while not stop_event.is_set():
        if GUI_STOP_FILE.exists():
            try:
                GUI_STOP_FILE.unlink()
            except Exception:
                pass
            log("Encerrado pela interface.")
            stop_event.set()
            break

        memory_scan_source = consume_memory_scan_request()
        if memory_scan_source:
            now = time.monotonic()
            if now - last_memory_scan_at < 2.5:
                time.sleep(0.10)
                continue
            last_memory_scan_at = now
            try:
                result = script.exports_sync.forceoldcorpsescan()
                if result.get("ok"):
                    log(
                        "BUSCA LIMPA: nova leitura iniciada por "
                        f"{memory_scan_source}. Aguarde ANALISANDO sumir do HUD."
                    )
                else:
                    reason = str(result.get("reason", "indisponivel"))
                    messages = {
                        "search_in_progress": (
                            "a busca anterior ainda está trabalhando."
                        ),
                        "stable_scan_in_progress": (
                            "a busca da memória ainda está trabalhando."
                        ),
                        "trigger_debounced": "aguarde antes de solicitar novamente.",
                        "solo_not_ready": "a sessão SOLO ainda não foi confirmada.",
                        "reserve_not_ready": "a reserva ainda não terminou de carregar.",
                        "multiplayer_blocked": "a sessão foi bloqueada pela proteção.",
                        "stopped": "o laboratório está encerrando.",
                    }
                    detail = messages.get(
                        reason,
                        "varredura recusada: " + reason,
                    )
                    error = str(result.get("error", "")).strip()
                    if error:
                        detail += " Detalhe: " + error
                    log("BUSCA LIMPA: " + detail)
            except Exception as exc:
                log(
                    "AVISO LAB: nao consegui repetir a busca limpa: " +
                    str(exc)
                )

        if f8_pressed():
            try:
                result = script.exports_sync.cyclehudcorner()
                corner = int(result.get("corner", corner))
                save_hud_corner(corner)
                log("HUD movido para o canto " + HUD_CORNERS[load_hud_config()["resolved_language"]][corner] + ".")
            except Exception as exc:
                log("AVISO: nao consegui mudar o canto do HUD: " + str(exc))

        if f6_pressed():
            try:
                result = script.exports_sync.forceoldcorpsescan()
                if result.get("ok"):
                    if result.get("skipped"):
                        log("F6: alvo atual ignorado; passando ao próximo.")
                    else:
                        log("F6: conferindo cadáveres. Aguarde terminar.")
                else:
                    reason = str(result.get("reason", "indisponivel"))
                    messages = {
                        "search_in_progress": "A busca já está em andamento. Aguarde terminar.",
                        "solo_not_ready": "A sessão SOLO ainda não foi confirmada.",
                        "reserve_not_ready": "A reserva ainda não terminou de carregar.",
                        "multiplayer_blocked": "A sessão foi bloqueada pela proteção contra multiplayer.",
                        "stopped": "O Turbo Hunter está encerrando.",
                    }
                    log("F6: " + messages.get(reason, "busca manual indisponível agora."))
            except Exception as exc:
                log("AVISO: nao consegui iniciar a busca manual por F6: " + str(exc))

        if f9_pressed():
            try:
                result = script.exports_sync.togglehudvisible()
                visible = bool(result.get("visible", True))
                log(
                    "HUD mostrado manualmente por F9."
                    if visible
                    else "HUD ocultado manualmente por F9."
                )
            except Exception as exc:
                log("AVISO: nao consegui alternar o HUD: " + str(exc))

        time.sleep(0.10)

    return corner



def gui_stop_requested():
    if not GUI_STOP_FILE.exists():
        return False
    try:
        GUI_STOP_FILE.unlink()
    except Exception:
        pass
    return True


def wait_for_game():
    log("AGUARDANDO JOGO: abra theHunter: Call of the Wild.")
    last_attach_error = ""
    last_attach_error_at = 0.0

    while not stop_event.is_set():
        if gui_stop_requested():
            log("Encerrado pela interface enquanto aguardava o jogo.")
            stop_event.set()
            return None

        try:
            session = frida.attach(PROCESS_NAME)
            log("JOGO DETECTADO: conectando Turbo Hunter.")
            return session
        except frida.ProcessNotFoundError:
            time.sleep(1.5)
        except Exception as exc:
            now = time.monotonic()
            error_text = str(exc).strip() or exc.__class__.__name__

            if error_text != last_attach_error or now - last_attach_error_at >= 15.0:
                log(
                    "ERRO DE CONEXAO: o jogo parece estar aberto, mas o Turbo Hunter "
                    "nao conseguiu conectar. Feche ambos, execute o Turbo Hunter como "
                    "administrador e tente novamente. Detalhe: " + error_text
                )
                last_attach_error = error_text
                last_attach_error_at = now

            time.sleep(2.0)

    return None

def main():
    global lab_session_id
    lab_session_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    stop_event.clear()
    expected_detach_event.clear()
    try:
        if GUI_STOP_FILE.exists():
            GUI_STOP_FILE.unlink()
    except Exception:
        pass
    prepare_log_files()
    save_lab_event(
        "python_session_started",
        {
            "python_version": sys.version,
            "process_name": PROCESS_NAME,
            "lab_live_file": LAB_LIVE_FILE.name,
            "machine_label": MACHINE_LABEL,
        },
    )

    config = load_hud_config()
    solo_only = int(config["solo_only"])
    protect_setwaypoint = int(config["protect_setwaypoint"])
    language = config.get("language", "auto")
    resolved_language = config.get("resolved_language", resolve_language(language))
    save_hud_config(config)

    print("=" * 72)
    print(" TURBO HUNTER 0.5.7")
    print("=" * 72)
    print(" COMPUTADOR:", MACHINE_LABEL)
    print()
    if resolved_language == "pt-BR":
        print("PROTECAO SOLO:", "ATIVA" if solo_only else "DESATIVADA")
        print("PROTECAO DE WAYPOINT:", "ATIVA" if protect_setwaypoint else "DESATIVADA")
        print("COMO USAR:")
        if solo_only:
            print("1) Abra o jogo e entre em uma sessão SOLO.")
            print("2) Cace normalmente; o Turbo Hunter acompanha os abates.")
            print("3) Se faltar um abate, aperte F6 uma vez e aguarde.")
        else:
            print("1) Abra o jogo e entre na sessão desejada.")
            print("2) Cace normalmente; o Turbo Hunter acompanha os abates.")
            print("3) Se faltar um abate, aperte F6 uma vez e aguarde.")
        print("TECLAS: F6/NUMPAD * = busca | F8 = canto | F9 = HUD")
        print("ALTERNATIVA: botão PROCURAR NOVAMENTE na janela.")
    else:
        print("SOLO PROTECTION:", "ON" if solo_only else "OFF")
        print("WAYPOINT PROTECTION:", "ON" if protect_setwaypoint else "OFF")
        print("HOW TO USE:")
        if solo_only:
            print("1) Turbo Hunter can stay open before the game.")
            print("2) Open the game and enter a SOLO session.")
            print("3) Wait for the HUD to change from WAITING SOLO to KILLS: 0.")
        else:
            print("1) Turbo Hunter can stay open before the game.")
            print("2) Open the game and enter the desired session.")
            print("3) The HUD starts as KILLS: 0.")
        print("KEYS: F6/NUMPAD * = search | F8 = move HUD | F9 = HUD")
        print("ALTERNATIVE: SEARCH AGAIN button in the Turbo Hunter window.")
    print()

    session = wait_for_game()
    if session is None:
        return

    session.on("detached", on_session_detached)

    try:
        js_source = build_js_source(solo_only, protect_setwaypoint, language)
    except Exception as exc:
        log("Nao consegui preparar o HUD: " + str(exc))
        expected_detach_event.set()
        try:
            session.detach()
        except Exception:
            pass
        
        if sys.stdin is not None and sys.stdin.isatty():
            input("ENTER para sair...")
        return

    script = session.create_script(js_source)
    script.on("message", on_message)

    try:
        script.load()
    except Exception as exc:
        log("Erro carregando script: " + str(exc))
        expected_detach_event.set()
        try:
            session.detach()
        except Exception:
            pass
        
        if sys.stdin is not None and sys.stdin.isatty():
            input("ENTER para sair...")
        return

    try:
        game_info = script.exports_sync.getgameidentity()
        game_identity = str(game_info.get("identity", ""))
        saved_state = initialize_lab_state(game_identity)
        imported = script.exports_sync.importlabstate(saved_state)
        save_lab_event(
            "python_lab_runtime_state_loaded",
            {
                "game_identity": game_identity,
                "game_pid": game_info.get("pid"),
                "main_module_base": game_info.get("main_module_base"),
                "saved_harvested_count": len(
                    saved_state.get("harvested_dna_keys", [])
                ),
                "saved_pending_count": len(
                    saved_state.get("pending_corpses", [])
                ),
                "saved_forensic_target_count": len(
                    saved_state.get("forensic_targets", [])
                ),
                "harvested_imported": imported.get(
                    "harvested_imported", 0
                ),
                "harvested_total": imported.get("harvested_total", 0),
                "pending_buffered": imported.get("pending_buffered", 0),
                "pending_restored": imported.get("pending_restored", 0),
                "forensic_targets": imported.get("forensic_targets", 0),
                "forensic_candidate_groups": imported.get(
                    "forensic_candidate_groups", 0
                ),
            },
        )
        if saved_state.get("harvested_dna_keys"):
            log(
                "DNA: " +
                str(len(saved_state.get("harvested_dna_keys", []))) +
                " coleta(s) anterior(es) desta mesma partida restaurada(s)."
            )
        if saved_state.get("pending_corpses"):
            log(
                "MEMÓRIA LAB: " +
                str(len(saved_state.get("pending_corpses", []))) +
                " cadáver(es) pendente(s) preservado(s) para restauração."
            )
    except Exception as exc:
        log("AVISO LAB: nao consegui restaurar o estado forense: " + str(exc))

    try:
        corner = load_hud_corner()

        try:
            result = script.exports_sync.sethudcorner(corner)
            corner = int(result.get("corner", corner))
            save_hud_corner(corner)
            log(
                "Canto inicial do HUD: " + HUD_CORNERS[resolved_language][corner] +
                ". Pressione F8 para mudar."
            )
        except Exception as exc:
            log("AVISO: nao consegui aplicar o canto inicial: " + str(exc))

        run_console_loop(script, corner)

    except KeyboardInterrupt:
        log("Encerrado pelo usuario.")

    except Exception:
        log("ERRO FATAL NO PROGRAMA:\n" + traceback.format_exc())

    finally:
        try:
            if MEMORY_SCAN_FILE.exists():
                MEMORY_SCAN_FILE.unlink()
        except Exception:
            pass

        try:
            script.exports_sync.stopgps()
        except Exception:
            pass

        # Give Frida's final registry snapshot time to reach the JSONL writer.
        time.sleep(0.35)

        expected_detach_event.set()

        try:
            session.detach()
        except Exception:
            pass

        save_lab_event("python_session_finished", {"normal_shutdown": True})
        log("Sessão encerrada; preparando o pacote de suporte.")
        package = build_lab_package()
        if package is not None:
            print_console("PACOTE DE LOG CRIADO: " + str(package))


if __name__ == "__main__":
    main()
