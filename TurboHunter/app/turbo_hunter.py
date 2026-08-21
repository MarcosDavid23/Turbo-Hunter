# Turbo Hunter 0.4.1
# theHunter: Call of the Wild
# Kill Locator / Localizador de Abates
# Configurações de segurança e waypoint: hud_config.json

import ctypes
import json
import locale
import sys
import time
import threading
import traceback
from datetime import datetime
from pathlib import Path

try:
    import frida
except ImportError:
    print("ERRO: Frida nao instalado.")
    print("Execute INSTALAR_TURBO_HUNTER.vbs para preparar o mod.")
    input("ENTER para sair...")
    raise SystemExit(1)

PROCESS_NAME = "theHunterCotW_F.exe"
LOG_FILE = Path(__file__).with_name("turbo_hunter_log.txt")
PREVIOUS_LOG_FILE = Path(__file__).with_name("turbo_hunter_log_anterior.txt")
HUD_C_FILE = Path(__file__).with_name("hud_directx11.c")
HUD_CONFIG_FILE = Path(__file__).with_name("hud_config.json")
GUI_STOP_FILE = Path(__file__).with_name(".turbo_hunter_stop")

stop_event = threading.Event()
expected_detach_event = threading.Event()
log_lock = threading.Lock()

JS = r"""
'use strict';

const HUD_C_SOURCE = "__HUD_C_SOURCE_PLACEHOLDER__";
const SOLO_ONLY_PROTECTION = __SOLO_ONLY_PLACEHOLDER__;
const PROTECT_SETWAYPOINT = __PROTECT_SETWAYPOINT_PLACEHOLDER__;

const BASE = Process.mainModule.base;

const RVA_SET_WAYPOINT      = 0x00BE53B0;
const RVA_CLEAR_WAYPOINT    = 0x00B9CB60;
const RVA_MAP_SINGLETON     = 0x028086C0;

// 0x67CCF0 retorna diretamente o objeto usado pelo jogo em player+0x68.
const RVA_GET_PLAYER_ENTITY = 0x0067CCF0;

const SET_ADDR     = BASE.add(RVA_SET_WAYPOINT);
const CLEAR_ADDR   = BASE.add(RVA_CLEAR_WAYPOINT);
const MAP_SLOT     = BASE.add(RVA_MAP_SINGLETON);
const GET_ENTITY   = BASE.add(RVA_GET_PLAYER_ENTITY);

const SetWaypoint   = new NativeFunction(SET_ADDR, 'void', ['pointer', 'pointer']);
const ClearWaypoint = new NativeFunction(CLEAR_ADDR, 'void', ['pointer']);
const GetPlayerEntity = new NativeFunction(GET_ENTITY, 'pointer', []);
const WAYPOINT_VECTOR = Memory.alloc(12);

const INTERNAL_HOOK_COOLDOWN_MS = 1200;
const EXTERNAL_HOOK_DEBOUNCE_MS = 800;
const EXTERNAL_REAPPLY_DELAY_MS = 450;
const HARVEST_SAME_SPECIES_MAX_M = 15;
const HARVEST_ANY_CORPSE_MAX_M = 8;
const WEIGHT_TOLERANCE = 0.01;
const RESERVE_CHANGE_CONFIRM_MS = 2000;
const HUD_WARNING_THRESHOLD = 20;
const HUD_WARNING_DURATION_MS = 10000;
const CURSOR_SHOWING = 0x00000001;

let capturedMap = ptr(0);

let pending = [];
let currentKey = "";
let currentIndex = -1;
let switchingKey = "";
let switchSerial = 0;
let markerOwned = false;
let finalClearDone = false;
let manualWaypointOverride = false;
let scriptStopping = false;

let insideSet = false;
let insideClear = false;
let internalHookIgnoreUntil = 0;
let lastExternalHookKind = "";
let lastExternalHookAt = 0;

let switchTimer = null;
let externalReapplyTimer = null;
let nearestUpdateTimer = null;
let nearestInterval = null;

let cachedEntity = ptr(0);
let playerPosConfirmed = false;

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
const seenHarvests = new Set();

function log(text) {
    send({type:"log", text:text});
}

function notifyBlock(reason) {
    send({type:"multiplayer_block", reason:reason});
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

function updateHudState() {
    if (hudSetStateNative === null)
        return;

    const enabled = shouldEnableHud();

    try {
        hudSetStateNative(
            enabled ? 1 : 0,
            pending.length,
            hudCorner,
            hudWarningActive ? 1 : 0,
            soloConfirmed ? 1 : 0
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
    if (pending.length < HUD_WARNING_THRESHOLD) {
        cancelHudWarning(true);
        return;
    }

    if (hudWarningShown)
        return;

    hudWarningShown = true;
    hudWarningActive = true;
    updateHudState();

    log(
        `⚠️ AVISO: ${pending.length} cadáveres aguardando coleta. ` +
        `Nenhum foi apagado, mas é recomendado recolher antes de continuar abatendo.`
    );

    hudWarningTimer = setTimeout(function () {
        hudWarningTimer = null;
        hudWarningActive = false;
        updateHudState();
    }, HUD_WARNING_DURATION_MS);
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
        let d3dCompile = resolveHudExport("d3dcompiler_47.dll", "D3DCompile");

        if (!d3dCompile)
            d3dCompile = resolveHudExport("d3dcompiler_43.dll", "D3DCompile");

        if (!d3dCreate || !createWindow || !destroyWindow || !d3dCompile)
            throw new Error("APIs DirectX 11 necessárias não foram encontradas");

        const hudStateBytes = 256 * 1024;
        hudStateMemory = Memory.alloc(hudStateBytes);
        hudStateMemory.writeByteArray(new Uint8Array(hudStateBytes).buffer);

        hudModule = new CModule(HUD_C_SOURCE, {
            hud_state:hudStateMemory,
            D3D11CreateDeviceAndSwapChain:d3dCreate,
            CreateWindowExW:createWindow,
            DestroyWindow:destroyWindow,
            D3DCompile:d3dCompile
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
            ["int", "int", "int", "int", "int"]
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

function getSingletonMap() {
    try { return MAP_SLOT.readPointer(); }
    catch (_) { return ptr(0); }
}

function getMap() {
    if (!capturedMap.isNull())
        return capturedMap;
    return getSingletonMap();
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

function internalHookActive(kind) {
    return scriptStopping || Date.now() <= internalHookIgnoreUntil ||
        (kind === "set" ? insideSet : insideClear);
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
    const hadWaypointWork = markerOwned || currentKey !== "" ||
        switchingKey !== "" || switchTimer !== null ||
        externalReapplyTimer !== null || nearestUpdateTimer !== null;

    cancelReserveCandidate();
    activeReserve = nextReserve;
    pending = [];
    cancelHudWarning(true);

    // Cancela qualquer Set/Clear atrasado antes de esquecer os ponteiros antigos.
    clearOwn(
        `troca de reserva confirmada ${previousReserve} -> ${activeReserve}`,
        discarded > 0 || hadWaypointWork
    );

    seenDeaths.clear();
    seenHarvests.clear();
    capturedMap = ptr(0);
    cachedEntity = ptr(0);
    playerPosConfirmed = false;
    lastExternalHookKind = "";
    lastExternalHookAt = 0;

    log(
        `🗺️ RESERVA ALTERADA E CONFIRMADA ${previousReserve} -> ${activeReserve}. ` +
        `GPS e ${discarded} cadáver(es) antigo(s) limpos.`
    );
    sendGpsStatus("reserve_change", `${previousReserve} -> ${activeReserve}`);
}

function handleReserveSignal(o) {
    if (!o || typeof o !== "object" ||
        !Object.prototype.hasOwnProperty.call(o, "reserve"))
        return;

    const nextReserve = normalizeReserve(o.reserve);

    if (nextReserve === "")
        return;

    if (activeReserve === "") {
        cancelReserveCandidate();
        activeReserve = nextReserve;
        log(`🗺️ RESERVA ATIVA detectada: ${activeReserve}.`);
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

    if (!multiplayerBlocked)
        handleReserveSignal(o);

    return !multiplayerBlocked;
}

// -----------------------------------------------------------
// POSIÇÃO DO JOGADOR - CORRIGIDA
// -----------------------------------------------------------

function refreshEntity(force) {
    // Resolve uma vez e mantém o ponteiro cacheado.
    // Só chama a função nativa novamente se a leitura ficar inválida.
    if (!force && !cachedEntity.isNull())
        return cachedEntity;

    try {
        const e = GetPlayerEntity();

        if (e && !e.isNull()) {
            cachedEntity = e;
            return cachedEntity;
        }
    } catch (_) {}

    cachedEntity = ptr(0);
    return cachedEntity;
}

function validCoord(v) {
    return Number.isFinite(v) && Math.abs(v) < 1000000;
}

function getPlayerPos() {
    if (multiplayerBlocked)
        return null;

    let entity = refreshEntity(false);

    if (entity.isNull())
        return null;

    try {
        // CONFIRMADO no código CreatePlayerIcon desta build.
        const x = entity.add(0x2294).readFloat();
        const y = entity.add(0x2298).readFloat();
        const z = entity.add(0x229C).readFloat();

        if (!validCoord(x) || !validCoord(y) || !validCoord(z)) {
            cachedEntity = ptr(0);
            entity = refreshEntity(true);

            if (entity.isNull())
                return null;

            const x2 = entity.add(0x2294).readFloat();
            const y2 = entity.add(0x2298).readFloat();
            const z2 = entity.add(0x229C).readFloat();

            if (!validCoord(x2) || !validCoord(y2) || !validCoord(z2))
                return null;

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

        return {x:x, y:y, z:z};

    } catch (_) {
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

function findNearestIndex(pp) {
    if (!pending.length)
        return -1;

    if (!pp)
        return pending.length - 1;

    let best = 0;
    let bestD = distSqXZ(pp, pending[0]);

    for (let i=1; i<pending.length; i++) {
        const d = distSqXZ(pp, pending[i]);

        if (d < bestD) {
            bestD = d;
            best = i;
        }
    }

    return best;
}

// -----------------------------------------------------------
// MARCADOR
// -----------------------------------------------------------

function clearMarkerInternal() {
    const map = getMap();

    if (map.isNull())
        return false;

    try {
        beginInternalHookWindow();
        insideClear = true;
        ClearWaypoint(map);
        return true;
    } catch (_) {
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

    if (index < 0 || index >= pending.length)
        return;

    const c = pending[index];

    if (corpseKey(c) !== wantedKey)
        return;

    const map = getMap();

    if (map.isNull()) {
        switchingKey = "";
        return;
    }

    try {
        WAYPOINT_VECTOR.writeFloat(Number(c.x));
        WAYPOINT_VECTOR.add(4).writeFloat(Number(c.y));
        WAYPOINT_VECTOR.add(8).writeFloat(Number(c.z));

        beginInternalHookWindow();
        insideSet = true;

        try {
            SetWaypoint(map, WAYPOINT_VECTOR);
        } finally {
            insideSet = false;
            beginInternalHookWindow();
        }

        currentIndex = index;
        currentKey = wantedKey;
        switchingKey = "";
        markerOwned = true;

        const pp = getPlayerPos();
        const d = pp ? distanceXZ(pp, c) : null;

        log(
            `📍 ALVO GPS -> ${corpseDesc(c)}` +
            (d !== null ? ` | distancia≈${d.toFixed(1)}m` : "") +
            (pp ? ` | jogador=(${pp.x.toFixed(2)},${pp.y.toFixed(2)},${pp.z.toFixed(2)})` : "") +
            ` | pendentes=${pending.length} | ${reason}`
        );

    } catch (e) {
        switchingKey = "";
        log("❌ ERRO SetWaypoint: " + e);
    }
}

function switchToIndex(index, reason) {
    if (multiplayerBlocked || scriptStopping || !soloConfirmed)
        return;

    if (index < 0 || index >= pending.length)
        return;

    const wantedKey = corpseKey(pending[index]);

    if (wantedKey === currentKey || wantedKey === switchingKey) {
        currentIndex = index;
        return;
    }

    switchingKey = wantedKey;
    const serial = ++switchSerial;
    switchTimer = clearTimeoutSafe(switchTimer);

    // Força o jogo a destruir o marcador 3D antigo.
    if (markerOwned || currentKey !== "")
        clearMarkerInternal();

    currentKey = "";
    currentIndex = -1;
    markerOwned = false;

    // Um pequeno intervalo permite à UI finalizar o Hide/Clear
    // antes de criar o novo pin no mundo.
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

    if (hadMarker && !finalClearDone)
        clearMarkerInternal();

    resetMarkerState();
    finalClearDone = true;

    if (hadWork || forceLog)
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

    // Com a proteção de waypoint ATIVA, um waypoint manual pertence ao jogador.
    // O Turbo Hunter só volta a mover o GPS depois que o jogador limpar esse point.
    if (PROTECT_SETWAYPOINT && manualWaypointOverride)
        return;

    if (!pending.length) {
        clearOwn("sem cadáver pendente");
        return;
    }

    const pp = getPlayerPos();
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

    const k = corpseKey(o);

    if (seenDeaths.has(k))
        return;

    seenDeaths.add(k);

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

    pending.push(c);
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

    scheduleNearestUpdate(220, "novo abate");
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

function chooseHarvestMatch(o) {
    if (!pending.length)
        return {index:-1, method:"sem_pendentes", distance:null};

    const pp = getPlayerPos();
    const speciesIndices = [];

    for (let i=0; i<pending.length; i++) {
        if (sameSpecies(pending[i].species, o.species))
            speciesIndices.push(i);
    }

    // Confirmado pela depuração 0.2: morte e coleta repetem estes dados.
    const dnaMatches = speciesIndices.filter(function (index) {
        const corpse = pending[index];

        if (!sameWeight(corpse.weight, o.weight))
            return false;

        if (hasValue(corpse.gender) && hasValue(o.gender) &&
            !sameValue(corpse.gender, o.gender))
            return false;

        if (hasValue(corpse.difficulty) && hasValue(o.difficulty) &&
            !sameValue(corpse.difficulty, o.difficulty))
            return false;

        return true;
    });

    if (dnaMatches.length === 1) {
        const index = dnaMatches[0];
        return {
            index:index,
            method:"dna_species_weight_gender_difficulty",
            distance:pp ? distanceXZ(pp, pending[index]) : null
        };
    }

    if (dnaMatches.length > 1) {
        const nearestDna = nearestCandidate(dnaMatches, pp);

        if (nearestDna) {
            return {
                index:nearestDna.index,
                method:"dna_empatado_mais_proximo",
                distance:nearestDna.distance
            };
        }

        return {
            index:dnaMatches[dnaMatches.length - 1],
            method:"dna_empatado_sem_posicao",
            distance:null
        };
    }

    // special_tag só vale se for não-zero e apontar para um único cadáver.
    const tag = usefulTag(o.special_tag);

    if (tag) {
        const tagMatches = [];

        for (let i=0; i<pending.length; i++) {
            if (usefulTag(pending[i].special_tag) === tag)
                tagMatches.push(i);
        }

        if (tagMatches.length === 1) {
            const index = tagMatches[0];
            return {
                index:index,
                method:"special_tag_unico",
                distance:pp ? distanceXZ(pp, pending[index]) : null
            };
        }
    }

    // Fallback físico: uma coleta normal acontece em cima do corpo.
    const nearestSpecies = nearestCandidate(speciesIndices, pp);

    if (nearestSpecies && nearestSpecies.distance <= HARVEST_SAME_SPECIES_MAX_M) {
        return {
            index:nearestSpecies.index,
            method:"jogador_proximo_mesma_especie",
            distance:nearestSpecies.distance
        };
    }

    const allIndices = pending.map(function (_, index) { return index; });
    const nearestAny = nearestCandidate(allIndices, pp);

    if (nearestAny && nearestAny.distance <= HARVEST_ANY_CORPSE_MAX_M) {
        return {
            index:nearestAny.index,
            method:"jogador_em_cima_do_cadaver",
            distance:nearestAny.distance
        };
    }

    if (!pp && speciesIndices.length === 1) {
        return {
            index:speciesIndices[0],
            method:"unica_especie_sem_posicao_jogador",
            distance:null
        };
    }

    return {index:-1, method:"sem_associacao_segura", distance:null};
}

function onHarvest(path, o) {
    if (multiplayerBlocked || scriptStopping)
        return;

    if (!inspectSession(path, o))
        return;

    if (!soloConfirmed || !pending.length)
        return;

    const match = chooseHarvestMatch(o);
    const idx = match.index;

    if (idx < 0) {
        const pp = getPlayerPos();
        log(
            `⚠️ Coleta detectada sem associação segura: ` +
            `species=${o.species ?? "?"} weight=${o.weight ?? "?"} ` +
            `gender=${o.gender ?? "?"} pendentes=${pending.length}` +
            (pp ? ` jogador=(${pp.x.toFixed(2)},${pp.y.toFixed(2)},${pp.z.toFixed(2)})` : "")
        );
        return;
    }

    const removed = pending.splice(idx, 1)[0];
    const removedKey = corpseKey(removed);
    updateHudWarningForCount();

    log(
        `✅ CADAVER COLETADO -> ${corpseDesc(removed)} ` +
        `| associação=${match.method}` +
        (match.distance !== null ? ` (${match.distance.toFixed(1)}m)` : "") +
        ` | restantes=${pending.length}`
    );
    sendGpsStatus("harvest", corpseDesc(removed));

    if (!pending.length) {
        clearOwn("ultimo cadáver coletado", true);
        return;
    }

    const removedWasCurrent = currentKey === removedKey;
    const removedWasSwitching = switchingKey === removedKey;
    cancelWaypointWork();

    if (removedWasCurrent) {
        currentKey = "";
        currentIndex = -1;
        // markerOwned continua true: o próximo switch limpará o pin antigo.
    } else if (removedWasSwitching) {
        resetMarkerState();
    } else if (currentKey !== "") {
        currentIndex = pending.findIndex(function (corpse) {
            return corpseKey(corpse) === currentKey;
        });

        if (currentIndex < 0)
            resetMarkerState();
    }

    if (!PROTECT_SETWAYPOINT)
        manualWaypointOverride = false;
    scheduleNearestUpdate(180, "apos coleta");
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

Interceptor.attach(SET_ADDR, {
    onEnter(args) {
        capturedMap = args[0];

        if (internalHookActive("set") || multiplayerBlocked || !soloConfirmed)
            return;

        const targetPosition = readHookPosition(args[1]);
        const matchingIndex = pending.length ? findPendingAtPosition(targetPosition) : -1;
        const nearestIndex = pending.length ? findNearestIndex(getPlayerPos()) : -1;

        // Mesmo se um callback interno chegar atrasado, a posição denuncia
        // que este SetWaypoint já é exatamente o alvo correto do GPS.
        if (matchingIndex >= 0 && matchingIndex === nearestIndex) {
            currentKey = corpseKey(pending[matchingIndex]);
            currentIndex = matchingIndex;
            switchingKey = "";
            markerOwned = true;
            manualWaypointOverride = false;
            return;
        }

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
            scheduleExternalReapply(
                "set",
                true,
                "reassumindo GPS sem protecao de waypoint",
                "🧭 Waypoint externo detectado; proteção desativada, GPS pode reassumir."
            );
        }
    }
});

Interceptor.attach(CLEAR_ADDR, {
    onEnter(args) {
        capturedMap = args[0];

        if (internalHookActive("clear") || multiplayerBlocked || !soloConfirmed)
            return;

        // Se havia um waypoint manual protegido, o Clear do jogador é justamente
        // o sinal que libera novamente o GPS automático.
        if (PROTECT_SETWAYPOINT && manualWaypointOverride) {
            releaseManualWaypointProtection();
            return;
        }

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
});

// -----------------------------------------------------------
// HTTP
// -----------------------------------------------------------

function keyFromPtr(p) {
    return p.toString();
}

function requestBodySignature(text) {
    return `${text.length}|${text.slice(0, 96)}|${text.slice(-160)}`;
}

function processRequestBody(key) {
    if (multiplayerBlocked)
        return;

    const body = reqBodies[key] || "";

    if (!body || !parseJson(body))
        return;

    const signature = requestBodySignature(body);

    if (reqProcessed[key] === signature)
        return;

    reqProcessed[key] = signature;
    handleBody(reqPaths[key] || "", body);
}

function appendRequestBody(key, text) {
    if (!text)
        return;

    const combined = (reqBodies[key] || "") + text;
    reqBodies[key] = combined.length <= 1024 * 1024
        ? combined
        : combined.slice(combined.length - 1024 * 1024);
    processRequestBody(key);
}

function handleBody(path, body) {
    if (!body || multiplayerBlocked || scriptStopping)
        return;

    const low = (path || "").toLowerCase();
    const o = parseJson(body);

    if (o)
        inspectSession(path, o);

    if (multiplayerBlocked)
        return;

    if (
        low.indexOf("animaldeathevent") >= 0 ||
        low.indexOf("animaldeathmpevent") >= 0
    ) {
        if (o)
            onDeath(path, o);
        return;
    }

    if (low.indexOf("confirmkillevent") >= 0) {
        if (!o)
            return;

        const hk = [
            o.reserve ?? "",
            o.species ?? "",
            finiteNumber(o.weight) !== null ? Number(o.weight).toFixed(6) : "",
            o.gender ?? "",
            o.difficulty ?? "",
            o.game_time ?? "",
            o.cash_reward ?? ""
        ].join("|");

        if (seenHarvests.has(hk))
            return;

        seenHarvests.add(hk);
        onHarvest(path, o);
    }
}

const openReq = Module.findGlobalExportByName("WinHttpOpenRequest");

if (openReq) {
    Interceptor.attach(openReq, {
        onEnter(args) {
            this.path = safeUtf16(args[2]);
        },

        onLeave(retval) {
            if (!retval.isNull()) {
                const k = keyFromPtr(retval);
                reqPaths[k] = this.path || "";
                reqBodies[k] = "";
                reqProcessed[k] = "";
            }
        }
    });
}

const sendReq = Module.findGlobalExportByName("WinHttpSendRequest");

if (sendReq) {
    Interceptor.attach(sendReq, {
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
    Interceptor.attach(writeData, {
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
    Interceptor.attach(closeHandle, {
        onEnter(args) {
            const k = keyFromPtr(args[0]);
            delete reqPaths[k];
            delete reqBodies[k];
            delete reqProcessed[k];
        }
    });
}

function shutdownGps(reason) {
    if (scriptStopping)
        return {ok:true, already_stopped:true};

    scriptStopping = true;
    const shouldClear = markerOwned || currentKey !== "";
    pending = [];
    cancelHudWarning(true);
    cancelReserveCandidate();
    cancelWaypointWork();

    if (nearestInterval !== null) {
        try { clearInterval(nearestInterval); }
        catch (_) {}
        nearestInterval = null;
    }

    if (shouldClear)
        clearMarkerInternal();

    resetMarkerState();
    finalClearDone = true;
    log(`🧹 GPS encerrado e limpo (${reason || "programa fechado"}).`);
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
            hud_corner:hudCorner,
            hud_corner_name:hudCornerName(hudCorner),
            hud_loaded:hudModule !== null,
            hud_user_visible:hudUserVisible,
            hud_game_visible:hudGameVisible,
            hud_auto_visibility:hudAutoVisibilitySupported,
            hud_warning_active:hudWarningActive,
            hud_warning_shown:hudWarningShown,
            protect_setwaypoint:PROTECT_SETWAYPOINT,
            manual_waypoint_override:manualWaypointOverride
        };
    }
};

// -----------------------------------------------------------
// INICIAL
// -----------------------------------------------------------

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
log("Turbo Hunter Kill Locator 0.4.1 carregado.");
if (SOLO_ONLY_PROTECTION)
    log("🔒 PROTEÇÃO SOLO ATIVA: multiplayer será bloqueado.");
else
    log("⚠️ PROTEÇÃO SOLO DESATIVADA: multiplayer permitido por conta e risco.");
if (PROTECT_SETWAYPOINT)
    log("🧭 PROTEÇÃO DE WAYPOINT ATIVA: waypoint do jogador será respeitado até ele limpar o point.");
else
    log("🧭 PROTEÇÃO DE WAYPOINT DESATIVADA: Turbo Hunter pode mover/reassumir o waypoint automaticamente.");
"""

def prepare_log_files():
    """Guarda somente a sessão anterior; o log continua pequeno e previsível."""
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 0:
            PREVIOUS_LOG_FILE.write_bytes(LOG_FILE.read_bytes())
    except Exception as exc:
        print("AVISO: nao consegui preservar o log anterior: " + str(exc), flush=True)

    LOG_FILE.write_text("", encoding="utf-8")


def log(line):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    text = f"[{stamp}] {line}"

    with log_lock:
        print(text, flush=True)
        with LOG_FILE.open("a", encoding="utf-8") as file:
            file.write(text + "\n")


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
}


def detect_windows_language():
    if sys.platform == "win32":
        try:
            buffer = ctypes.create_unicode_buffer(85)
            if ctypes.windll.kernel32.GetUserDefaultLocaleName(buffer, len(buffer)):
                return "pt-BR" if buffer.value.lower().startswith("pt") else "en"
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


def load_hud_config():
    data = {
        "corner": 3,
        "name": HUD_CORNERS["pt-BR"][3],
        "solo_only": 1,
        "protect_setwaypoint": 1,
        "language": "auto",
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
        protect_setwaypoint = 1 if int(data.get("protect_setwaypoint", 1)) != 0 else 0
    except Exception:
        protect_setwaypoint = 1

    language = str(data.get("language", "auto") or "auto")
    if language.lower() not in ("auto", "en", "pt-br", "pt_br", "pt"):
        language = "auto"
    resolved_language = resolve_language(language)

    return {
        "corner": corner,
        "name": HUD_CORNERS[resolved_language][corner],
        "solo_only": solo_only,
        "protect_setwaypoint": protect_setwaypoint,
        "language": language,
        "resolved_language": resolved_language,
    }


def save_hud_config(config):
    try:
        corner = int(config.get("corner", 1))
        if not 0 <= corner <= 3:
            corner = 1

        solo_only = 1 if int(config.get("solo_only", 1)) != 0 else 0
        protect_setwaypoint = 1 if int(config.get("protect_setwaypoint", 1)) != 0 else 0
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
        counter_prefix = "ABATES: "
        warning_text = "RECOLHA OS ANIMAIS"
    else:
        waiting_text = "WAITING SOLO"
        counter_prefix = "KILLS: "
        warning_text = "COLLECT ANIMALS"
    hud_source = hud_source.replace("__HUD_WAITING_TEXT__", waiting_text)
    hud_source = hud_source.replace("__HUD_COUNTER_PREFIX__", counter_prefix)
    hud_source = hud_source.replace("__HUD_WARNING_TEXT__", warning_text)
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


def f9_pressed():
    return key_pressed(0x78)


def on_message(message, data):
    if message.get("type") == "error":
        log("FRIDA ERRO: " + str(message))
        stop_event.set()
        return

    payload = message.get("payload", {})

    if not isinstance(payload, dict):
        return

    if payload.get("type") == "log":
        log(payload.get("text", ""))

    elif payload.get("type") == "multiplayer_block":
        reason = payload.get("reason", "multiplayer")
        log("PROTECAO SOLO acionada: " + reason)
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

    while not stop_event.is_set():
        if GUI_STOP_FILE.exists():
            try:
                GUI_STOP_FILE.unlink()
            except Exception:
                pass
            log("Encerrado pela interface.")
            stop_event.set()
            break

        if f8_pressed():
            try:
                result = script.exports_sync.cyclehudcorner()
                corner = int(result.get("corner", corner))
                save_hud_corner(corner)
                log("HUD movido para o canto " + HUD_CORNERS[load_hud_config()["resolved_language"]][corner] + ".")
            except Exception as exc:
                log("AVISO: nao consegui mudar o canto do HUD: " + str(exc))

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

    while not stop_event.is_set():
        if gui_stop_requested():
            log("Encerrado pela interface enquanto aguardava o jogo.")
            stop_event.set()
            return None

        try:
            session = frida.attach(PROCESS_NAME)
            log("JOGO DETECTADO: conectando Turbo Hunter.")
            return session
        except Exception:
            time.sleep(1.5)

    return None

def main():
    stop_event.clear()
    expected_detach_event.clear()
    try:
        if GUI_STOP_FILE.exists():
            GUI_STOP_FILE.unlink()
    except Exception:
        pass
    prepare_log_files()

    config = load_hud_config()
    solo_only = int(config["solo_only"])
    protect_setwaypoint = int(config["protect_setwaypoint"])
    language = config.get("language", "auto")
    resolved_language = config.get("resolved_language", resolve_language(language))
    save_hud_config(config)

    print("=" * 72)
    print(" TURBO HUNTER KILL LOCATOR 0.4.1")
    print("=" * 72)
    print()
    if resolved_language == "pt-BR":
        print("PROTECAO SOLO:", "ATIVA" if solo_only else "DESATIVADA")
        print("PROTECAO DE WAYPOINT:", "ATIVA" if protect_setwaypoint else "DESATIVADA")
        print("COMO USAR:")
        if solo_only:
            print("1) O Turbo Hunter pode ficar aberto antes do jogo.")
            print("2) Abra o jogo e entre em uma partida SOLO.")
            print("3) Aguarde o HUD mudar de AGUARDANDO SOLO para ABATES: 0.")
        else:
            print("1) O Turbo Hunter pode ficar aberto antes do jogo.")
            print("2) Abra o jogo e entre na partida desejada.")
            print("3) O HUD inicia como ABATES: 0.")
        print("TECLAS: F8 = mudar canto | F9 = ocultar/mostrar HUD")
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
        print("KEYS: F8 = move HUD corner | F9 = show/hide HUD")
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
            script.exports_sync.stopgps()
        except Exception:
            pass

        expected_detach_event.set()

        try:
            session.detach()
        except Exception:
            pass


if __name__ == "__main__":
    main()
