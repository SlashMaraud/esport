
import re
import time
import requests
from urllib.parse import quote

# ============================================================
# CONFIGURACIÓN
# ============================================================

API_URL = "https://search.acestream.net/"

API_KEY = "test_api_key"
API_VERSION = 1

SEARCH_QUERIES = [
    "movistar",
    "dazn",
    "champions",
    "laliga",
    "tennis",
    "tenis",
    "nba",
    "m+",
    "sport tv",
    "eurosport",
    "sky sports",
    "formula 1",
]

PAGE_SIZE = 200
OUTPUT_FILE = "lista.m3u"

# Formato de los enlaces:
# acestream://HASH
#
# Alternativa para reproducir mediante Engine local:
# http://127.0.0.1:6878/ace/getstream?id=HASH

STREAM_TEMPLATE = "acestream://{hash}"

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
# API
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "Mozilla/5.0 SportTV-M3U-Updater/1.0",
    "Accept": "application/json",
})


def search_api(query, page=0):
    """Consulta la API de Ace Stream."""

    params = {
        "method": "search",
        "api_version": API_VERSION,
        "api_key": API_KEY,
        "query": query,
        "page": page,
        "page_size": PAGE_SIZE,
    }

    response = session.get(
        API_URL,
        params=params,
        timeout=30,
    )

    response.raise_for_status()

    data = response.json()

    if data.get("error"):
        raise RuntimeError(str(data["error"]))

    result = data.get("result", {})

    if not isinstance(result, dict):
        return [], 0

    results = result.get("results", [])

    if not isinstance(results, list):
        results = []

    total = result.get("total", len(results))

    try:
        total = int(total)
    except (TypeError, ValueError):
        total = len(results)

    return results, total


def get_all_results(query):
    """Recorre las páginas de resultados."""

    all_results = []
    page = 0

    while True:
        results, total = search_api(query, page)

        if not results:
            break

        all_results.extend(results)

        print(
            f"    Página {page + 1}: "
            f"{len(results)} resultados "
            f"(total anunciado: {total})"
        )

        if len(all_results) >= total:
            break

        if len(results) < PAGE_SIZE:
            break

        page += 1

        # Evita realizar peticiones demasiado seguidas
        time.sleep(0.5)

    return all_results


# ============================================================
# EXTRACCIÓN DE CANALES
# ============================================================

def extract_channels(results):
    """
    Extrae canales de las respuestas JSON.
    Soporta tanto resultados planos como agrupados.
    """

    channels = []

    for item in results:

        # Caso de resultados agrupados por canal
        nested_items = item.get("items")

        if isinstance(nested_items, list):
            for subitem in nested_items:
                name = (
                    subitem.get("name")
                    or item.get("name")
                    or "Canal desconocido"
                )

                infohash = (
                    subitem.get("infohash")
                    or subitem.get("content_id")
                )

                if infohash:
                    channels.append({
                        "name": name,
                        "hash": infohash,
                    })

        # Caso de resultados normales
        else:
            name = item.get("name", "Canal desconocido")

            infohash = (
                item.get("infohash")
                or item.get("content_id")
            )

            if infohash:
                channels.append({
                    "name": name,
                    "hash": infohash,
                })

    return channels


# ============================================================
# CLASIFICACIÓN
# ============================================================

def classify_channel(name):
    name_lower = name.lower()

    if "dazn" in name_lower:
        return "DAZN"

    if "champions" in name_lower or "campeones" in name_lower:
        return "LIGA DE CAMPEONES"

    if "eurosport" in name_lower:
        return "EUROSPORT"

    if "sky sports" in name_lower:
        return "SKY SPORTS"

    if "formula 1" in name_lower or "f1" in name_lower:
        return "FORMULA 1"

    if "tennis" in name_lower or "tenis" in name_lower:
        return "TENIS"

    if "nba" in name_lower or "basket" in name_lower:
        return "NBA"

    if any(x in name_lower for x in [
        "movistar",
        "m+",
        "vamos",
        "liga",
    ]):
        return "MOVISTAR"

    return "FUTBOL INT"


# ============================================================
# LIMPIEZA
# ============================================================

def clean_name(name):
    name = str(name).strip()

    # Elimina saltos de línea y caracteres de control
    name = re.sub(r"[\r\n\t]+", " ", name)

    # Evita comas que puedan complicar la lectura de M3U
    name = name.replace(",", " - ")

    # Elimina espacios repetidos
    name = re.sub(r"\s+", " ", name)

    return name.strip()


def valid_hash(hash_id):
    """
    AceStream suele utilizar hashes de 40 caracteres hexadecimales.
    """

    hash_id = str(hash_id).strip().lower()

    return bool(
        re.fullmatch(r"[a-f0-9]{40}", hash_id)
    )


# ============================================================
# GENERACIÓN M3U
# ============================================================

def generate_m3u():

    all_channels = []
    seen_hashes = set()

    print("Iniciando búsqueda AceStream...\n")

    for query in SEARCH_QUERIES:

        print(f"Buscando: {query}")

        try:
            results = get_all_results(query)

            channels = extract_channels(results)

            print(f"    Canales extraídos: {len(channels)}")

            for channel in channels:

                hash_id = channel["hash"]

                if not valid_hash(hash_id):
                    continue

                hash_id = hash_id.lower()

                if hash_id in seen_hashes:
                    continue

                seen_hashes.add(hash_id)

                name = clean_name(channel["name"])

                if not name:
                    name = f"Canal {query}"

                group = classify_channel(name)

                all_channels.append({
                    "name": name,
                    "hash": hash_id,
                    "group": group,
                })

        except Exception as error:
            print(f"    ERROR en '{query}': {error}")

        time.sleep(1)

    # --------------------------------------------------------
    # No borrar la lista anterior si la búsqueda falla
    # --------------------------------------------------------

    if not all_channels:
        print("\nERROR: No se ha encontrado ningún canal.")
        print("No se sobrescribe la lista existente.")
        return

    # --------------------------------------------------------
    # Ordenar canales por grupo y nombre
    # --------------------------------------------------------

    all_channels.sort(
        key=lambda channel: (
            channel["group"],
            channel["name"].lower()
        )
    )

    m3u_entries = [HEADER_M3U.strip()]

    current_group = None

    for channel in all_channels:

        group = channel["group"]

        if group != current_group:
            m3u_entries.append(
                f'\n#EXTGRP: group-title="{group}"'
            )

            current_group = group

        name = channel["name"]
        hash_id = channel["hash"]

        stream_url = STREAM_TEMPLATE.format(
            hash=hash_id
        )

        m3u_entries.append(
            f'#EXTINF:-1 group-title="{group}",{name}\n'
            f'{stream_url}'
        )

    playlist = "\n".join(m3u_entries) + "\n"

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8"
    ) as file:
        file.write(playlist)

    print("\n====================================")
    print("LISTA M3U GENERADA CORRECTAMENTE")
    print(f"Canales únicos: {len(all_channels)}")
    print(f"Archivo: {OUTPUT_FILE}")
    print("====================================")


if __name__ == "__main__":
    generate_m3u()
