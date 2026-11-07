#!/usr/bin/env python3
"""
Genera lista.m3u SOLO con: Movistar, M+, DAZN (y sus variantes), tennis y NBA.

Estrategia:
  1) API oficial search.acestream.net (rapida).
  2) Si no devuelve nada (p. ej. bloqueo de IP en GitHub Actions),
     abre https://asesearch.vercel.app/ con Playwright y busca ahi.

IMPORTANTE sobre los enlaces:
  La API devuelve el "infohash" del canal, que NO es lo mismo que el
  "content id". Un enlace acestream://INFOHASH no abre. Por eso, para
  los infohash se genera:
      http://127.0.0.1:6878/ace/getstream?infohash=HASH
  (hace falta el engine de Ace Stream en marcha en ese equipo).
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

API_URL = "https://search.acestream.net/"
API_KEY = "test_api_key"
API_VERSION = 1

# Engine de Ace Stream que reproducira los enlaces.
#   Movil con Ace Serve / engine local : "http://127.0.0.1:6878"
#   Engine en casa (ejemplo)           : "http://192.168.1.128:6878"
#   Vacio ("")                         : usa enlaces acestream://
ENGINE_URL = "http://127.0.0.1:6878"

WEB_URL = "https://asesearch.vercel.app/"
USE_WEB_FALLBACK = True   # si la API no da nada, usar la web (Playwright)
FORCE_WEB = False         # True = usar SIEMPRE la web ademas de la API

# True = solo guarda canales que encajen en GROUP_RULES
ONLY_MATCHING = True

# Grupos: (nombre, regex sobre el nombre en minusculas). Gana el primero.
GROUP_RULES = [
    ("DAZN", r"\bdazn"),
    ("MOVISTAR", r"movistar|\bm\+|^m\.\s|^#?\s?vamos\b"),
    ("NBA", r"\bnba\b"),
    ("TENIS", r"\b(tennis|tenis)\b"),
]

SEARCH_QUERIES = [
    # Movistar
    "movistar",
    "movistar plus",
    "movistar liga",
    "movistar liga de campeones",
    "movistar deportes",
    "vamos",
    # M+
    "m+",
    "m+ 1",
    "m+ liga",
    "m+ laliga",
    "m+ liga de campeones",
    "m+ deportes",
    "m. laliga",
    "m. liga de campeones",
    "laliga",
    "liga de campeones",
    "champions",
    # DAZN
    "dazn",
    "dazn 1",
    "dazn 2",
    "dazn 3",
    "dazn 4",
    "dazn laliga",
    "dazn f1",
    "dazn motogp",
    "dazn moto gp",
    # Tenis y NBA
    "tennis",
    "tenis",
    "nba",
]

# Busquedas que usa la fase web (mas lenta, solo si la API falla)
WEB_QUERIES = [
    "movistar", "m+", "laliga", "liga de campeones",
    "dazn", "tennis", "nba",
]

PAGE_SIZE = 200
MAX_PAGES = 10
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
    kind = "infohash"    -> hash de torrent (lo que da la API de busqueda)
    kind = "content_id"  -> ID de contenido (el de acestream://)
    """
    if ENGINE_URL:
        param = "infohash" if kind == "infohash" else "id"
        return f"{ENGINE_URL.rstrip('/')}/ace/getstream?{param}={hash_id}"

    if kind == "infohash":
        return f"acestream://?infohash={hash_id}"
    return f"acestream://{hash_id}"


def walk_json(obj, out, parent_name=None):
    """
    Recorre cualquier JSON (API o web) y recoge canales:
    {name, hash, kind, availability}. Si hay content_id lo prefiere
    sobre el infohash.
    """
    if isinstance(obj, dict):
        raw_name = obj.get("name") or obj.get("title") or obj.get("channel")
        name = raw_name if isinstance(raw_name, str) and raw_name else parent_name

        found = None
        for key, kind in (
            ("content_id", "content_id"),
            ("contentId", "content_id"),
            ("acestream_id", "content_id"),
            ("ace_id", "content_id"),
            ("infohash", "infohash"),
            ("hash", "infohash"),
            ("id", "content_id"),
        ):
            value = obj.get(key)
            if isinstance(value, str) and valid_hash(value):
                found = (value.lower(), kind)
                break

        if found:
            avail = obj.get("availability")
            out.append({
                "name": name or "Canal desconocido",
                "hash": found[0],
                "kind": found[1],
                "availability": (
                    avail if isinstance(avail, (int, float)) else None
                ),
            })

        for value in obj.values():
            if isinstance(value, str):
                match = ACE_RE.search(value)
                if match:
                    out.append({
                        "name": name or "Canal desconocido",
                        "hash": match.group(1).lower(),
                        "kind": "content_id",
                        "availability": None,
                    })
            elif isinstance(value, (dict, list)):
                walk_json(value, out, name)

    elif isinstance(obj, list):
        for value in obj:
            walk_json(value, out, parent_name)


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
            "availability": channel.get("availability"),
        }
        new += 1

    return new, skipped


# ============================================================
# FASE 1: API OFICIAL
# ============================================================

session = requests.Session()
session.headers.update({
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
})


def search_api(query, page=0):
    params = {
        "method": "search",
        "api_version": API_VERSION,
        "api_key": API_KEY,
        "query": query,
        "page": page,
        "page_size": PAGE_SIZE,
    }

    response = session.get(API_URL, params=params, timeout=30)

    if page == 0:
        log(f"    HTTP {response.status_code} | {response.text[:150]!r}")

    response.raise_for_status()
    data = response.json()

    if data.get("error"):
        raise RuntimeError(str(data["error"]))

    result = data.get("result")
    if not isinstance(result, dict):
        # La API real responde sin el envoltorio "result":
        # {"total": 7, "results": [...]}
        result = data
    if not isinstance(result, dict):
        return [], 0

    results = result.get("results") or []
    if not isinstance(results, list):
        results = []

    try:
        total = int(result.get("total", len(results)))
    except (TypeError, ValueError):
        total = len(results)

    return results, total


def get_all_results(query):
    all_results = []

    for page in range(MAX_PAGES):
        results, total = search_api(query, page)

        if not results:
            break

        all_results.extend(results)
        log(
            f"    Pagina {page + 1}: {len(results)} resultados "
            f"(total anunciado: {total})"
        )

        if len(all_results) >= total or len(results) < PAGE_SIZE:
            break

        time.sleep(0.5)

    return all_results


def run_api_stage(store):
    log("--- Fase 1: API oficial search.acestream.net ---")
    keys_logged = False

    for query in SEARCH_QUERIES:
        log(f"Buscando: {query}")

        try:
            results = get_all_results(query)

            if results and not keys_logged and isinstance(results[0], dict):
                log(f"    Campos de un resultado: {sorted(results[0].keys())}")
                keys_logged = True

            channels = []
            walk_json(results, channels)
            new, skipped = add_channels(store, channels, query)
            log(
                f"    Extraidos: {len(channels)} | nuevos: {new} "
                f"| descartados (no son de tus grupos): {skipped}"
            )
        except Exception as error:
            log(f"    ERROR en '{query}': {error!r}")

        time.sleep(0.5)


# ============================================================
# FASE 2: WEB asesearch CON PLAYWRIGHT
# ============================================================

def run_web_stage(store):
    log("\n--- Fase 2: web asesearch (Playwright) ---")

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
                page.goto(WEB_URL, wait_until="networkidle", timeout=60000)

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

                # b) Enlaces acestream:// visibles, con su nombre
                try:
                    links = page.locator('a[href^="acestream://"]').all()
                    for link in links[:300]:
                        href = link.get_attribute("href") or ""
                        match = ACE_RE.search(href)
                        if not match:
                            continue
                        text = link.evaluate(
                            "el => ((el.closest('li, tr, article, div')"
                            " || el).innerText || '')"
                        )
                        first_line = (text or "").strip().split("\n")[0]
                        found.append({
                            "name": first_line,
                            "hash": match.group(1).lower(),
                            "kind": "content_id",
                            "availability": None,
                        })
                except Exception as error:
                    log(f"    (enlaces) {error!r}")

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

            except Exception as error:
                log(f"    ERROR web en '{query}': {error!r}")

        browser.close()


# ============================================================
# GENERACION M3U
# ============================================================

def generate_m3u():
    now = datetime.datetime.now(datetime.timezone.utc)
    log(f"Inicio: {now.isoformat()}")
    log(f"Motor de reproduccion: {ENGINE_URL or 'acestream://'}")

    store = {}  # hash -> info

    run_api_stage(store)
    log(f"\nCanales tras la API: {len(store)}")

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
            -(info["availability"] or 0),
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
