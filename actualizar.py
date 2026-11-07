import re
from playwright.sync_api import sync_playwright

BASE_URL = "https://asesearch.vercel.app/"
SEARCH_TERMS = ["movistar", "dazn", "campeones", "laliga", "m+", "tennis", "tenis", "nba", "basket", "deportes"]

HEADER_M3U = """#EXTM3U url-tvg="https://raw.githubusercontent.com/davidmuma/EPG_dobleM/refs/heads/master/guiatv.xml,https://epgshare01.online/epgshare01/epg_ripper_NL1.xml.gz,https://raw.githubusercontent.com/davidmuma/EPG_dobleM/master/guiatv.xml" refresh="3600"
#EXTVLCOPT:network-caching=1000

#EXTGRP: group-title="MOVISTAR" group-logo="https://upload.wikimedia.org/wikipedia/commons/thumb/c/c5/Movistar_logo_2020.svg/512px-Movistar_logo_2020.svg.png"
#EXTGRP: group-title="DAZN" group-logo="https://upload.wikimedia.org/wikipedia/commons/thumb/d/d6/Dazn-logo.png/601px-Dazn-logo.png"
#EXTGRP: group-title="LIGA DE CAMPEONES" group-logo="https://upload.wikimedia.org/wikipedia/commons/thumb/f/f3/UEFA_Champions_League_logo_2021.svg/512px-UEFA_Champions_League_logo_2021.svg.png"
#EXTGRP: group-title="TENIS" group-logo="https://www.soyvisual.org/sites/default/files/styles/twitter_card/public/images/photos/dep_0002.jpg"
#EXTGRP: group-title="NBA" group-logo="https://upload.wikimedia.org/wikipedia/commons/thumb/7/7a/Basketball.png/480px-Basketball.png"
"""

def generate_m3u():
    m3u_entries = [HEADER_M3U]
    seen_hashes = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(BASE_URL, timeout=60000)
        page.wait_for_load_state("networkidle")

        for term in SEARCH_TERMS:
            try:
                # Localizar la barra de búsqueda y escribir el término
                search_input = page.locator("input[type='text'], input").first
                search_input.click()
                search_input.fill("")
                search_input.fill(term)
                
                # Hacer clic en el botón de búsqueda
                search_btn = page.locator("button").filter(has_text=re.compile("Search", re.IGNORECASE)).first
                if search_btn.count() > 0:
                    search_btn.click()
                else:
                    search_input.press("Enter")

                # Esperar a que la página renderice los resultados
                page.wait_for_timeout(4000)
                
                html_content = page.content()
                
                # Buscar hashes de AceStream y nombres asociados
                pattern = re.compile(r'([^<>"\n]{3,80})["\'].*?acestream://([a-fA-F0-9]{40})', re.IGNORECASE)
                matches = pattern.findall(html_content)

                hash_pattern = re.compile(r'acestream://([a-fA-F0-9]{40})', re.IGNORECASE)
                all_hashes = hash_pattern.findall(html_content)
                
                for h in all_hashes:
                    matches.append((f"Canal {term.capitalize()}", h))

                for raw_name, hash_id in matches:
                    if hash_id in seen_hashes:
                        continue
                    seen_hashes.add(hash_id)

                    clean_name = raw_name.strip()
                    clean_name = re.sub(r'[\n\t]', ' ', clean_name).strip()
                    if not clean_name or len(clean_name) > 60 or "search" in clean_name.lower():
                        clean_name = f"Canal {term.capitalize()}"

                    name_lower = clean_name.lower()

                    # Clasificar en su grupo correspondiente
                    if "dazn" in name_lower:
                        group = "DAZN"
                    elif "campeones" in name_lower or "champions" in name_lower:
                        group = "LIGA DE CAMPEONES"
                    elif "tennis" in name_lower or "tenis" in name_lower:
                        group = "TENIS"
                    elif "nba" in name_lower or "basketball" in name_lower or "baloncesto" in name_lower:
                        group = "NBA"
                    else:
                        group = "MOVISTAR"

                    entry = f'#EXTINF:-1 group-title="{group}", {clean_name}\nacestream://{hash_id}\n'
                    m3u_entries.append(entry)

            except Exception as e:
                print(f"Error buscando {term}: {e}")

        browser.close()

    with open("lista.m3u", "w", encoding="utf-8") as f:
        f.write("\n".join(m3u_entries))
    
    print(f"¡Lista generada con {len(seen_hashes)} canales!")

if __name__ == "__main__":
    generate_m3u()
