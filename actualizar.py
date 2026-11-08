#!/usr/bin/env python3
"""
Genera lista.m3u SOLO con: M. y M+ (Movistar), DAZN, Tennis y NBA,
incluyendo lo que vaya detras (M+ LaLiga, M+ Liga de Campeones, DAZN 1...).

Fuente: https://search-ace.stream/

Estrategia:
  1) Descarga su playlist de canales publicos y filtra por nombre.
  2) Si no se puede, abre la web con Playwright y busca cada termino.
  3) Renombra "M. LaLiga" -> "Movistar LaLiga" (M. = Movistar) para que
     tu app haga match con los eventos. Los "M+" se dejan como estan.
  4) Los canales que vengan con infohash los convierte a content id
     (el "id" que abre en TODOS los reproductores) usando un engine de
     Ace Stream temporal en Docker. Si no se puede, ver INFOHASH_FALLBACK.

Los enlaces usan el engine de Ace Stream:
    http://127.0.0.1:6878/ace/getstream?id=CONTENT_ID

Escribe debug.txt (y debug.png si usa la web). El workflow ya no los guarda
en el repo, pero los ves en el log de Actions.
"""

import re
import sys
import time
import shutil
import datetime
import subprocess
import requests

# ============================================================
# CONFIGURACION
# ============================================================

SITE_URL = "https://search-ace.stream/"
PLAYLIST_URL = "https://search-ace.stream/playlist"

# Engine de Ace Stream que reproducira los enlaces.
#   Movil con Ace Serve / engine local : "http://127.0.0.1:6878"
#   Engine en casa (ejemplo)           : "http://192.168.1.128:6878"
#   Vacio ("")                         : usa enlaces acestream://
ENGINE_URL = "http://127.0.0.1:6878"

USE_WEB_FALLBACK = True   # si la playlist falla, buscar en la web (Playwright)
FORCE_WEB = False         # True = usar SIEMPRE la web ademas de la playlist

# True = solo guarda canales que encajen en GROUP_RULES
ONLY_MATCHING = True

# Cambios de nombre para que tu app haga match con futbolenlatv.es
# (regex, sustitucion). Se aplican ANTES de clasificar el canal.
NAME_FIXES = [
    # "M. LaLiga" -> "Movistar LaLiga"
    # "M. Liga de Campeones 2" -> "Movistar Liga de Campeones 2"
    (r"(?<![a-z0-9.])m\.\s+", "Movistar "),
]

# Grupos: (nombre, regex sobre el nombre en minusculas). Gana el primero.
GROUP_RULES = [
    ("DAZN", r"\bdazn"),
    ("MOVISTAR", r"(?<![a-z0-9.])m\+|(?<![a-z0-9.])m\.\s+\S"),
    ("NBA", r"\bnba\b"),
    ("TENIS", r"\b(tennis|tenis)\b"),
]

# Terminos que busca la fase web (solo se usa si la playlist falla)
WEB_QUERIES = [
    "M.",
    "M+",
    "Dazn",
    "Tennis",
    "Nba",
    "M. LaLiga",
    "M+ Liga de Campeones",
    "Dazn 1",
]

# --- Conversion infohash -> content id --------------------------------
# Los enlaces con infohash no los abren todos los reproductores. Si hay
# alguno, el script levanta un engine de Ace Stream en Docker (solo en
# GitHub Actions), pregunta el content id y lo apaga.
CONVERT_INFOHASH = True
ACE_DOCKER_IMAGE = "ghcr.io/martinbjeldbak/acestream-http-proxy"
CONVERT_PORT = 6878
CONVERT_START_WAIT = 90       # segundos esperando a que arranque el engine
CONVERT_TIMEOUT_EACH = 20     # segundos maximos por canal
CONVERT_TIMEOUT_TOTAL = 360   # segundos maximos en total

# Si un infohash no se puede convertir:
#   "keep" = lo deja con infohash (Ace Stream lo abre, otros reproductores no)
#   "drop" = lo quita de la lista (solo quedan enlaces con id)
INFOHASH_FALLBACK = "keep"

OUTPUT_FILE = "lista.m3u"
DEBUG_FILE = "debug.txt"
SCREENSHOT_FILE = "debug.png"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

HEADER_M3U = """#EXTM3U url-tvg="https://raw.githubusercontent.com/davidmuma/EPG_dobleM/refs/heads/master/guiatv.xml,https://epgshare01.online/epgshare01/epg_ripper_NL1.xml.gz,https://raw.githubusercontent.com/davidmuma/EPG_dobleM/master/guiatv.xml" refresh="3600"
#EXTVLCOPT:network-caching=1000

#EXTGRP: group-title="MOVISTAR" group-logo="https://upload.wikimedia.org/wikipedia/commons/thumb/c/c5/Movistar_logo_2020.svg/512px-Movistar_logo_2020.svg.png"
#EXTGRP: group-title="DAZN" group-logo="https://upload.wikimedia.org/wikipedia/commons/thumb/d/d6/Dazn-logo.png/601px-Dazn-logo.png"
#EXTGRP: group-title="TENIS" group-logo="https://www.soyvisual.org/sites/default/files/styles/twitter_card/public/images/photos/dep_0002.jpg"
#EXTGRP: group-title="NBA" group-logo="https://upload.wikimedia.org/wikipedia/commons/thumb/7/7a/Basketball.png/480px-Basketball.png"
"""

# ============================================================
# LOG (se guarda tambien en debug.txt)
# ============================================================

LOG = []


def log(msg=""):
    print(msg, flush=True)
    LOG.append(str(msg))


def write_debug():
    try:
        text = "\n".join(LOG)
        with open(DEBUG_FILE, "w", encoding="utf-8") as f:
            f.write(text[:60000] + "\n")
    except Exception as error:
        print(f"No se pudo escribir {DEBUG_FILE}: {error}")


# ============================================================
# UTILIDADES
# ============================================================

ACE_RE = re.compile(r"acestream://([a-fA-F0-9]{40})")
ID_RE = re.compile(
    r"(?:[?&](id|content_id|infohash)=|acestream://\??(?:infohash=)?)"
    r"([a-fA-F0-9]{40})"
)


def valid_hash(hash_id):
    """Los hashes de AceStream son 40 caracteres hexadecimales."""
    return bool(re.fullmatch(r"[a-f0-9]{40}", str(hash_id).strip().lower()))


def clean_name(name):
    name = str(name or "").strip()
    name = re.sub(r"[\r\n\t]+", " ", name)
    name = name.replace(",", " - ")
    name = re.sub(r"\s+", " ", name)
    return name.strip()


def fix_name(name):
    """Aplica NAME_FIXES (p. ej. 'M. LaLiga' -> 'Movistar LaLiga')."""
    for pattern, replacement in NAME_FIXES:
        name = re.sub(pattern, replacement, name, flags=re.IGNORECASE)
    return name


def classify_channel(name):
    """Devuelve el grupo del canal, o None si no nos interesa."""
    n = name.lower()
    for group, pattern in GROUP_RULES:
        if re.search(pattern, n):
            return group
    return None


def natural_key(text):
    """Orden natural: DAZN 2 antes que DAZN 10."""
    return [
        int(part) if part.isdigit() else part
        for part in re.split(r"(\d+)", text.lower())
    ]


def stream_url(hash_id, kind):
    """
    kind = "content_id" -> ID de contenido (abre en todos los reproductores)
    kind = "infohash"   -> hash de torrent (solo lo entiende Ace Stream)
    """
    if ENGINE_URL:
        param = "infohash" if kind == "infohash" else "id"
        return f"{ENGINE_URL.rstrip('/')}/ace/getstream?{param}={hash_id}"

    if kind == "infohash":
        return f"acestream://?infohash={hash_id}"
    return f"acestream://{hash_id}"


def add_channels(store, channels, query):
    """
    Guarda en store {hash: info} solo los canales que encajan en los
    grupos. Devuelve (nuevos, descartados).
    """
    new = 0
    skipped = 0

    for channel in channels:
        hash_id = str(channel.get("hash", "")).strip().lower()

        if not valid_hash(hash_id) or hash_id in store:
            continue

        name = clean_name(channel.get("name")) or f"Canal {query}"
        group = classify_channel(name)   # sobre el nombre original
        name = fix_name(name)            # y despues se renombra

        if group is None:
            if ONLY_MATCHING:
                skipped += 1
                continue
            group = "OTROS"

        store[hash_id] = {
            "name": name,
            "kind": channel.get("kind", "content_id"),
            "group": group,
        }
        new += 1

    return new, skipped


def walk_json(obj, out, parent_name=None):
    """Recorre cualquier JSON y recoge canales {name, hash, kind}."""
    if isinstance(obj, dict):
        raw_name = (
            obj.get("name") or obj.get("title") or obj.get("channel")
        )
        name = raw_name if isinstance(raw_name, str) and raw_name else parent_name

        for key, kind in (
            ("content_id", "content_id"),
            ("contentId", "content_id"),
            ("id", "content_id"),
            ("infohash", "infohash"),
            ("hash", "infohash"),
        ):
            value = obj.get(key)
            if isinstance(value, str) and valid_hash(value):
                out.append({
                    "name": name or "Canal desconocido",
                    "hash": value.lower(),
                    "kind": kind,
                })
                break

        for value in obj.values():
            if isinstance(value, str):
                match = ACE_RE.search(value)
                if match:
                    out.append({
                        "name": name or "Canal desconocido",
                        "hash": match.group(1).lower(),
                        "kind": "content_id",
                    })
            elif isinstance(value, (dict, list)):
                walk_json(value, out, name)

    elif isinstance(obj, list):
        for value in obj:
            walk_json(value, out, parent_name)


# ============================================================
# FASE 1: PLAYLIST DE search-ace.stream
# ============================================================

def extinf_name(line):
    """Nombre de una linea #EXTINF (texto tras la primera coma fuera de comillas)."""
    in_quotes = False
    for index, char in enumerate(line):
        if char == '"':
            in_quotes = not in_quotes
        elif char == "," and not in_quotes:
            return line[index + 1:].strip()
    return ""


def parse_m3u(text):
    """Lee una playlist M3U y devuelve [{name, hash, kind}]."""
    channels = []
    name = None

    for raw in text.splitlines():
        line = raw.strip()

        if not line:
            continue

        if line.startswith("#EXTINF"):
            name = extinf_name(line)
            continue

        if line.startswith("#"):
            continue

        match = ID_RE.search(line)
        if match:
            kind = "infohash" if match.group(1) == "infohash" else "content_id"
            channels.append({
                "name": name or "Canal desconocido",
                "hash": match.group(2).lower(),
                "kind": kind,
            })
        name = None

    return channels


def run_playlist_stage(store):
    log("--- Fase 1: playlist de search-ace.stream ---")

    try:
        response = requests.get(
            PLAYLIST_URL,
            headers={
                "User-Agent": UA,
                "Accept": "*/*",
                "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
            },
            timeout=90,
        )
        log(
            f"    HTTP {response.status_code} | "
            f"{len(response.content)} bytes | "
            f"{response.headers.get('content-type')}"
        )

        response.raise_for_status()
        text = response.text

        lines = [l for l in text.splitlines() if l.strip()]
        for line in lines[:6]:
            log(f"    > {line[:160]}")

        channels = parse_m3u(text)
        kinds = {"content_id": 0, "infohash": 0}
        for channel in channels:
            kinds[channel["kind"]] = kinds.get(channel["kind"], 0) + 1
        log(
            f"    Enlaces en la playlist: con id {kinds['content_id']} | "
            f"con infohash {kinds['infohash']}"
        )

        new, skipped = add_channels(store, channels, "playlist")
        log(
            f"    Guardados: {new} | "
            f"descartados (no son de tus grupos): {skipped}"
        )

    except Exception as error:
        log(f"    ERROR descargando la playlist: {error!r}")


# ============================================================
# FASE 2: WEB search-ace.stream CON PLAYWRIGHT
# ============================================================

# Busca en el DOM cualquier hash de 40 caracteres y el texto de su "fila"
JS_FIND_HASHES = """
() => {
  const re = /[a-f0-9]{40}/i;
  const rows = [];
  for (const el of document.querySelectorAll('*')) {
    const attrs = Array.from(el.attributes).map(a => a.value).join(' ');
    const own = Array.from(el.childNodes)
      .filter(n => n.nodeType === 3).map(n => n.textContent).join(' ');
    const m = (attrs + ' ' + own).match(re);
    if (m) {
      const box = el.closest(
        'tr, li, article, .card, .result, [class*=item], [class*=row]'
      ) || el.parentElement;
      rows.push({hash: m[0].toLowerCase(), text: (box ? box.innerText : '') || ''});
    }
  }
  return rows;
}
"""


def run_web_stage(store):
    log("\n--- Fase 2: web search-ace.stream (Playwright) ---")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("Playwright no esta instalado; se omite la fase web.")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(
            user_agent=UA,
            locale="es-ES",
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()
        captured = []

        def on_response(resp):
            try:
                ctype = resp.headers.get("content-type") or ""
                if "json" in ctype:
                    captured.append((resp.url, resp.json()))
            except Exception:
                pass

        page.on("response", on_response)

        for index, query in enumerate(WEB_QUERIES):
            log(f"Buscando en web: {query}")
            captured.clear()

            try:
                page.goto(SITE_URL, wait_until="networkidle", timeout=60000)

                box = page.locator("input:visible").first
                box.wait_for(state="visible", timeout=15000)
                box.fill(query)
                box.press("Enter")

                page.wait_for_timeout(3000)
                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass

                found = []

                # a) JSON que ha cargado la propia pagina
                for url, data in captured:
                    log(f"    JSON: {url[:110]}")
                    walk_json(data, found)

                # b) Hashes que aparecen en la pagina, con el texto de su fila
                try:
                    for row in page.evaluate(JS_FIND_HASHES)[:2000]:
                        lines = [
                            l.strip() for l in (row["text"] or "").split("\n")
                            if l.strip() and row["hash"] not in l.lower()
                        ]
                        found.append({
                            "name": lines[0] if lines else "",
                            "hash": row["hash"],
                            "kind": "content_id",
                        })
                except Exception as error:
                    log(f"    (dom) {error!r}")

                new, skipped = add_channels(store, found, query)
                log(
                    f"    Encontrados: {len(found)} | nuevos: {new} "
                    f"| descartados: {skipped}"
                )

                if index == 0:
                    try:
                        page.screenshot(path=SCREENSHOT_FILE)
                    except Exception:
                        pass
                    if not found:
                        body = page.inner_text("body")
                        log(f"    Texto de la pagina: {body[:300]!r}")

            except Exception as error:
                log(f"    ERROR web en '{query}': {error!r}")

            time.sleep(0.5)

        browser.close()


# ============================================================
# FASE 3: CONVERTIR infohash -> content id (engine temporal en Docker)
# ============================================================

ENGINE_CONTAINER = "ace-engine-temp"


def start_engine():
    """Arranca un engine de Ace Stream en Docker. Devuelve la URL base o None."""
    if not shutil.which("docker"):
        log("    Docker no esta disponible en este equipo.")
        return None

    try:
        subprocess.run(
            ["docker", "rm", "-f", ENGINE_CONTAINER],
            capture_output=True, text=True, timeout=60,
        )
        result = subprocess.run(
            [
                "docker", "run", "-d", "--name", ENGINE_CONTAINER,
                "-p", f"{CONVERT_PORT}:6878", ACE_DOCKER_IMAGE,
            ],
            capture_output=True, text=True, timeout=400,
        )
    except Exception as error:
        log(f"    Error lanzando Docker: {error!r}")
        return None

    if result.returncode != 0:
        log(f"    No arranco el contenedor: {result.stderr.strip()[:300]}")
        return None

    base = f"http://127.0.0.1:{CONVERT_PORT}"
    deadline = time.time() + CONVERT_START_WAIT

    while time.time() < deadline:
        try:
            response = requests.get(
                f"{base}/server/api",
                params={"api_version": 3, "method": "get_version"},
                timeout=5,
            )
            if response.status_code < 500:
                log("    Engine temporal listo.")
                return base
        except Exception:
            pass
        time.sleep(2)

    log("    El engine temporal no respondio a tiempo.")
    stop_engine()
    return None


def stop_engine():
    try:
        subprocess.run(
            ["docker", "rm", "-f", ENGINE_CONTAINER],
            capture_output=True, text=True, timeout=60,
        )
    except Exception:
        pass


def get_content_id(base, infohash):
    """Pregunta al engine el content id de un infohash (o None)."""
    response = requests.get(
        f"{base}/server/api",
        params={
            "api_version": 3,
            "method": "get_content_id",
            "infohash": infohash,
        },
        timeout=CONVERT_TIMEOUT_EACH,
    )
    data = response.json()

    if data.get("error"):
        return None

    result = data.get("result")
    content_id = result.get("content_id") if isinstance(result, dict) else None

    if isinstance(content_id, str) and valid_hash(content_id):
        return content_id.lower()
    return None


def convert_infohashes(store):
    pending = [h for h, info in store.items() if info["kind"] == "infohash"]

    if not pending:
        return

    log(f"\n--- Fase 3: convertir infohash -> id ({len(pending)} canales) ---")

    if not CONVERT_INFOHASH:
        log("    Conversion desactivada (CONVERT_INFOHASH = False).")
        return

    base = start_engine()
    if not base:
        log("    No se pudo convertir; se aplica INFOHASH_FALLBACK.")
        return

    converted = 0
    started = time.time()

    try:
        for infohash in pending:
            if time.time() - started > CONVERT_TIMEOUT_TOTAL:
                log("    Tiempo maximo de conversion alcanzado.")
                break

            try:
                content_id = get_content_id(base, infohash)
            except Exception as error:
                log(f"    {infohash[:8]}...: {type(error).__name__}")
                continue

            if not content_id:
                continue

            info = store.pop(infohash)
            if content_id not in store:
                info["kind"] = "content_id"
                store[content_id] = info
                converted += 1
    finally:
        stop_engine()

    log(f"    Convertidos a id: {converted} de {len(pending)}")


# ============================================================
# GENERACION M3U
# ============================================================

def generate_m3u():
    now = datetime.datetime.now(datetime.timezone.utc)
    log(f"Inicio: {now.isoformat()}")
    log(f"Motor de reproduccion: {ENGINE_URL or 'acestream://'}")

    store = {}  # hash -> info

    run_playlist_stage(store)
    log(f"\nCanales tras la playlist: {len(store)}")

    if FORCE_WEB or (not store and USE_WEB_FALLBACK):
        try:
            run_web_stage(store)
        except Exception as error:
            log(f"ERROR en fase web: {error!r}")
        log(f"Canales tras la web: {len(store)}")

    if store:
        try:
            convert_infohashes(store)
        except Exception as error:
            log(f"ERROR en la conversion: {error!r}")
            stop_engine()

    # Lo que siga con infohash tras la conversion
    leftovers = [h for h, info in store.items() if info["kind"] == "infohash"]
    if leftovers:
        if INFOHASH_FALLBACK == "drop":
            for h in leftovers:
                store.pop(h, None)
            log(f"\nQuitados {len(leftovers)} canales que solo tienen infohash.")
        else:
            log(
                f"\nAviso: {len(leftovers)} canales siguen con infohash "
                "(otros reproductores pueden no abrirlos)."
            )

    if not store:
        log("\nERROR: no se ha encontrado ningun canal de tus grupos.")
        log("No se sobrescribe la lista existente.")
        return

    group_order = [g for g, _ in GROUP_RULES] + ["OTROS"]

    def sort_key(item):
        hash_id, info = item
        return (
            group_order.index(info["group"]),
            natural_key(info["name"]),
        )

    channels = sorted(store.items(), key=sort_key)

    entries = [HEADER_M3U.strip()]
    current_group = None
    counts = {}

    for hash_id, info in channels:
        group = info["group"]
        counts[group] = counts.get(group, 0) + 1

        if group != current_group:
            entries.append(f'\n#EXTGRP: group-title="{group}"')
            current_group = group

        url = stream_url(hash_id, info["kind"])
        entries.append(
            f'#EXTINF:-1 group-title="{group}",{info["name"]}\n{url}'
        )

    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        file.write("\n".join(entries) + "\n")

    with_id = sum(1 for _, i in channels if i["kind"] == "content_id")

    log("\n====================================")
    log("LISTA M3U GENERADA CORRECTAMENTE")
    log(f"Canales unicos: {len(channels)} (con id: {with_id})")
    for group, count in counts.items():
        log(f"  {group}: {count}")
    log(f"Archivo: {OUTPUT_FILE}")
    log("====================================")


if __name__ == "__main__":
    try:
        generate_m3u()
    except Exception as error:
        log(f"ERROR inesperado: {error!r}")
    finally:
        write_debug()

    # Salir siempre bien para que el workflow guarde la lista
    sys.exit(0)
