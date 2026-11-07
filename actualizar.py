#!/usr/bin/env python3
"""
Genera lista.m3u SOLO con: M. y M+ (Movistar), DAZN, Tennis y NBA,
incluyendo lo que vaya detras (M. LaLiga, M+ Liga de Campeones, DAZN 1...).

Fuente: https://search-ace.stream/

Estrategia:
  1) Descarga su playlist de canales publicos
     (https://search-ace.stream/playlist) y filtra por nombre.
  2) Si no se puede (bloqueo, vacio...), abre la web con Playwright,
     busca cada termino y captura los resultados.

Los enlaces usan el engine de Ace Stream:
    http://127.0.0.1:6878/ace/getstream?id=CONTENT_ID
Si tu engine esta en otra IP, cambia ENGINE_URL.

Escribe debug.txt (y debug.png si usa la web) para ver que paso.
"""

import re
import sys
import time
import datetime
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

# Grupos: (nombre, regex sobre el nombre en minusculas). Gana el primero.
#   M.   -> "M. LaLiga", "M. Liga de Campeones"...
#   M+   -> "M+ LaLiga", "M+ Liga de Campeones", "M+ 1"...
# Para anadir otro nombre, agrega aqui una linea o amplia una regex.
GROUP_RULES = [
    ("DAZN", r"\bdazn"),
    ("MOVISTAR", r"(?<![a-z0-9.])m\+|(?<![a-z0-9.])m\.\s+[a-z0-9]"),
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
    kind = "content_id" -> ID de contenido (lo normal en search-ace.stream)
    kind = "infohash"   -> hash de torrent
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
        group = classify_channel(name)

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
        new, skipped = add_channels(store, channels, "playlist")
        log(
            f"    Canales en la playlist: {len(channels)} | "
            f"guardados: {new} | descartados (no son de tus grupos): {skipped}"
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

    log("\n====================================")
    log("LISTA M3U GENERADA CORRECTAMENTE")
    log(f"Canales unicos: {len(channels)}")
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

    # Salir siempre bien para que el workflow guarde debug.txt
    sys.exit(0)
