#!/usr/bin/env python3
"""
Genera lista.m3u con canales AceStream deportivos.

Estrategia:
  1) API oficial search.acestream.net (rapida).
  2) Si no devuelve nada (por ejemplo, bloqueo de IP en GitHub Actions),
     abre https://asesearch.vercel.app/ con Playwright (Chromium headless),
     busca cada termino y captura los resultados.

Siempre escribe debug.txt (y debug.png si usa la web) para poder ver
desde el movil que ha pasado, sin abrir los logs de Actions.
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

WEB_URL = "https://asesearch.vercel.app/"
USE_WEB_FALLBACK = True   # si la API no da nada, usar la web con Playwright
FORCE_WEB = False         # True = usar SIEMPRE la web ademas de la API

SEARCH_QUERIES = [
    "movistar",
    "dazn",
    "champions",
    "champions league",
    "liga de campeones",
    "laliga",
    "tennis",
    "tenis",
    "nba",
    "m+",
    "sport tv",
    "eurosport",
    "sky sports",
    "formula 1",
    "f1",
]

PAGE_SIZE = 200
MAX_PAGES = 10
OUTPUT_FILE = "lista.m3u"
DEBUG_FILE = "debug.txt"
SCREENSHOT_FILE = "debug.png"

# Formato de los enlaces. Alternativa con Engine local:
# http://127.0.0.1:6878/ace/getstream?id={hash}
STREAM_TEMPLATE = "acestream://{hash}"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

HEADER_M3U = """#EXTM3U url-tvg="https://raw.githubusercontent.com/davidmuma/EPG_dobleM/refs/heads/master/guiatv.xml,https://epgshare01.online/epgshare01/epg_ripper_NL1.xml.gz,https://raw.githubusercontent.com/davidmuma/EPG_dobleM/master/guiatv.xml" refresh="3600"
#EXTVLCOPT:network-caching=1000

#EXTGRP: group-title="MOVISTAR" group-logo="https://upload.wikimedia.org/wikipedia/commons/thumb/c/c5/Movistar_logo_2020.svg/512px-Movistar_logo_2020.svg.png"
#EXTGRP: group-title="DAZN" group-logo="https://upload.wikimedia.org/wikipedia/commons/thumb/d/d6/Dazn-logo.png/601px-Dazn-logo.png"
#EXTGRP: group-title="LIGA DE CAMPEONES" group-logo="https://upload.wikimedia.org/wikipedia/commons/thumb/f/f3/UEFA_Champions_League_logo_2021.svg/512px-UEFA_Champions_League_logo_2021.svg.png"
#EXTGRP: group-title="TENIS" group-logo="https://www.soyvisual.org/sites/default/files/styles/twitter_card/public/images/photos/dep_0002.jpg"
#EXTGRP: group-title="NBA" group-logo="https://upload.wikimedia.org/wikipedia/commons/thumb/7/7a/Basketball.png/480px-Basketball.png"
#EXTGRP: group-title="EUROSPORT"
#EXTGRP: group-title="SKY SPORTS"
#EXTGRP: group-title="FORMULA 1"
#EXTGRP: group-title="FUTBOL INT"
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
    """AceStream usa hashes de 40 caracteres hexadecimales."""
    return bool(re.fullmatch(r"[a-f0-9]{40}", str(hash_id).strip().lower()))


def clean_name(name):
    name = str(name or "").strip()
    name = re.sub(r"[\r\n\t]+", " ", name)
    name = name.replace(",", " - ")
    name = re.sub(r"\s+", " ", name)
    return name.strip()


def classify_channel(name):
    n = name.lower()

    if "dazn" in n:
        return "DAZN"
    if "champions" in n or "campeones" in n:
        return "LIGA DE CAMPEONES"
    if "eurosport" in n:
        return "EUROSPORT"
    if "sky sports" in n:
        return "SKY SPORTS"
    if "formula 1" in n or "f1" in n:
        return "FORMULA 1"
    if "tennis" in n or "tenis" in n:
        return "TENIS"
    if "nba" in n or "basket" in n:
        return "NBA"
    if any(x in n for x in ["movistar", "m+", "vamos", "liga"]):
        return "MOVISTAR"
    return "FUTBOL INT"


def walk_json(obj, out, parent_name=None):
    """
    Recorre cualquier JSON (API o web) y recoge {name, hash}.
    Soporta resultados planos, agrupados ("items") y enlaces acestream://.
    """
    if isinstance(obj, dict):
        raw_name = (
            obj.get("name") or obj.get("title") or obj.get("channel")
        )
        name = raw_name if isinstance(raw_name, str) and raw_name else parent_name

        for key in (
            "infohash", "content_id", "contentId", "hash",
            "id", "ace_id", "acestream_id",
        ):
            value = obj.get(key)
            if isinstance(value, str) and valid_hash(value):
                out.append({
                    "name": name or "Canal desconocido",
                    "hash": value.lower(),
                })
                break

        for value in obj.values():
            if isinstance(value, str):
                match = ACE_RE.search(value)
                if match:
                    out.append({
                        "name": name or "Canal desconocido",
                        "hash": match.group(1).lower(),
                    })
            elif isinstance(value, (dict, list)):
                walk_json(value, out, name)

    elif isinstance(obj, list):
        for value in obj:
            walk_json(value, out, parent_name)


def add_channels(store, channels, query):
    """Anade al diccionario {hash: nombre} sin duplicar. Devuelve nuevos."""
    new = 0
    for channel in channels:
        hash_id = str(channel.get("hash", "")).strip().lower()

        if not valid_hash(hash_id) or hash_id in store:
            continue

        name = clean_name(channel.get("name")) or f"Canal {query}"
        store[hash_id] = name
        new += 1
    return new


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

    for query in SEARCH_QUERIES:
        log(f"Buscando: {query}")

        try:
            results = get_all_results(query)
            channels = []
            walk_json(results, channels)
            new = add_channels(store, channels, query)
            log(f"    Canales extraidos: {len(channels)} | nuevos: {new}")
        except Exception as error:
            log(f"    ERROR en '{query}': {error!r}")

        time.sleep(1)


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
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox"],
        )
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

        for index, query in enumerate(SEARCH_QUERIES):
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
                        })
                except Exception as error:
                    log(f"    (enlaces) {error!r}")

                # c) Cualquier acestream:// en el HTML
                for match in ACE_RE.finditer(page.content()):
                    found.append({
                        "name": f"Canal {query}",
                        "hash": match.group(1).lower(),
                    })

                new = add_channels(store, found, query)
                log(f"    Encontrados: {len(found)} | nuevos: {new}")

                if index == 0:
                    try:
                        page.screenshot(path=SCREENSHOT_FILE)
                    except Exception:
                        pass
                    if new == 0:
                        body = page.inner_text("body")
                        log(f"    Texto de la pagina: {body[:300]!r}")

            except Exception as error:
                log(f"    ERROR web en '{query}': {error!r}")

        browser.close()


# ============================================================
# GENERACION M3U
# ============================================================

def generate_m3u():
    now = datetime.datetime.now(datetime.timezone.utc)
    log(f"Inicio: {now.isoformat()}")

    store = {}  # hash -> nombre

    run_api_stage(store)
    log(f"\nCanales tras la API: {len(store)}")

    if FORCE_WEB or (not store and USE_WEB_FALLBACK):
        try:
            run_web_stage(store)
        except Exception as error:
            log(f"ERROR en fase web: {error!r}")
        log(f"Canales tras la web: {len(store)}")

    if not store:
        log("\nERROR: no se ha encontrado ningun canal.")
        log("No se sobrescribe la lista existente.")
        return

    all_channels = [
        {"name": name, "hash": hash_id, "group": classify_channel(name)}
        for hash_id, name in store.items()
    ]
    all_channels.sort(key=lambda c: (c["group"], c["name"].lower()))

    entries = [HEADER_M3U.strip()]
    current_group = None

    for channel in all_channels:
        group = channel["group"]

        if group != current_group:
            entries.append(f'\n#EXTGRP: group-title="{group}"')
            current_group = group

        url = STREAM_TEMPLATE.format(hash=channel["hash"])
        entries.append(
            f'#EXTINF:-1 group-title="{group}",{channel["name"]}\n{url}'
        )

    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        file.write("\n".join(entries) + "\n")

    log("\n====================================")
    log("LISTA M3U GENERADA CORRECTAMENTE")
    log(f"Canales unicos: {len(all_channels)}")
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
