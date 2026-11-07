
import re
import requests

SEARCH_URL = "https://asesearch.vercel.app/"

HEADER_M3U = """#EXTM3U url-tvg="https://raw.githubusercontent.com/davidmuma/EPG_dobleM/refs/heads/master/guiatv.xml,https://epgshare01.online/epgshare01/epg_ripper_NL1.xml.gz,https://raw.githubusercontent.com/davidmuma/EPG_dobleM/master/guiatv.xml" refresh="3600"
#EXTVLCOPT:network-caching=1000

#EXTGRP: group-title="MOVISTAR" group-logo="https://upload.wikimedia.org/wikipedia/commons/thumb/c/c5/Movistar_logo_2020.svg/512px-Movistar_logo_2020.svg.png"
#EXTGRP: group-title="DAZN" group-logo="https://upload.wikimedia.org/wikipedia/commons/thumb/d/d6/Dazn-logo.png/601px-Dazn-logo.png"
#EXTGRP: group-title="LIGA DE CAMPEONES" group-logo="https://upload.wikimedia.org/wikipedia/commons/thumb/f/f3/UEFA_Champions_League_logo_2021.svg/512px-UEFA_Champions_League_logo_2021.svg.png"
"""

def generate_m3u():
    try:
        response = requests.get(SEARCH_URL, headers={"User-Agent": "Mozilla/5.0"})
        if response.status_code != 200:
            print("Error al conectar con la web de búsqueda.")
            return

        html_content = response.text
        
        # Expresión regular para capturar el nombre completo del canal y su hash de 40 caracteres
        pattern = re.compile(r'([^<>"\n]*?(?:movistar|m\+|dazn|campeones)[^<>"\n]*?)["\'].*?acestream://([a-fA-F0-9]{40})', re.IGNORECASE)
        matches = pattern.findall(html_content)

        m3u_entries = [HEADER_M3U]
        seen_hashes = set()

        for raw_name, hash_id in matches:
            if hash_id in seen_hashes:
                continue
            seen_hashes.add(hash_id)

            clean_name = raw_name.strip()
            name_lower = clean_name.lower()

            # Clasificación automática del grupo según el nombre detectado
            if "dazn" in name_lower:
                group = "DAZN"
            elif "campeones" in name_lower or "champions" in name_lower:
                group = "LIGA DE CAMPEONES"
            else:
                group = "MOVISTAR"

            entry = f'#EXTINF:-1 group-title="{group}", {clean_name}\nacestream://{hash_id}\n'
            m3u_entries.append(entry)

        with open("lista.m3u", "w", encoding="utf-8") as f:
            f.write("\n".join(m3u_entries))
            
        print("Lista M3U generada y actualizada con éxito.")

    except Exception as e:
        print(f"Ocurrió un error en la ejecución: {e}")

if __name__ == "__main__":
    generate_m3u()
