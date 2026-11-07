
import json
from playwright.sync_api import sync_playwright

BASE_URL = "https://asesearch.vercel.app/"

SEARCH_QUERIES = [
    "movistar",
    "dazn",
    "champions",
    "laliga",
    "tennis",
    "tenis",
    "nba",
    "sport tv",
]


def log_request(request):
    print("\n========== REQUEST ==========")
    print("Método:", request.method)
    print("URL:", request.url)

    if request.post_data:
        print("POST DATA:", request.post_data[:3000])


def log_response(response):
    request = response.request

    # Solo nos interesan las peticiones de datos
    if request.resource_type not in ["xhr", "fetch"]:
        return

    print("\n========== RESPONSE ==========")
    print("Status:", response.status)
    print("URL:", response.url)

    try:
        body = response.text()
        print("Respuesta:")
        print(body[:5000])
    except Exception as error:
        print("No se pudo leer la respuesta:", error)


def diagnose():

    with sync_playwright() as p:

        browser = p.chromium.launch(headless=True)

        page = browser.new_page()

        page.on("request", log_request)
        page.on("response", log_response)

        print("\nAbriendo AceSearch...")

        page.goto(
            BASE_URL,
            wait_until="domcontentloaded",
            timeout=60000
        )

        page.wait_for_timeout(5000)

        print("\n========== URL INICIAL ==========")
        print(page.url)

        print("\n========== INPUTS ==========")

        inputs = page.locator("input").evaluate_all("""
            elements => elements.map(element => ({
                type: element.type,
                name: element.name,
                placeholder: element.placeholder,
                value: element.value,
                id: element.id
            }))
        """)

        print(json.dumps(inputs, indent=2, ensure_ascii=False))

        print("\n========== BOTONES ==========")

        buttons = page.locator("button").evaluate_all("""
            elements => elements.map(element => ({
                text: element.innerText,
                type: element.type,
                id: element.id
            }))
        """)

        print(json.dumps(buttons, indent=2, ensure_ascii=False))

        print("\n========== TEXTO INICIAL ==========")

        print(page.locator("body").inner_text()[:5000])

        # Intentar realizar una búsqueda
        for query in SEARCH_QUERIES:

            print("\n\n################################")
            print("BUSCANDO:", query)
            print("################################")

            try:

                inputs_locator = page.locator("input")

                if inputs_locator.count() == 0:
                    print("No se encuentra ningún input.")
                    continue

                # Buscar el input de texto
                search_input = page.locator(
                    "input[type='text']"
                ).first

                if search_input.count() == 0:
                    search_input = inputs_locator.first

                search_input.fill(query)

                # Localizar botón de búsqueda
                search_button = page.get_by_role(
                    "button",
                    name="Search",
                    exact=True
                )

                if search_button.count() > 0:
                    search_button.click()
                else:
                    print("No se encontró botón Search.")
                    continue

                page.wait_for_timeout(5000)

                print("\nURL tras búsqueda:")
                print(page.url)

                print("\nTexto tras búsqueda:")
                print(page.locator("body").inner_text()[:5000])

                # Guardar HTML para inspección
                filename = (
                    "debug_"
                    + query.replace(" ", "_")
                    + ".html"
                )

                with open(
                    filename,
                    "w",
                    encoding="utf-8"
                ) as file:

                    file.write(page.content())

                print("HTML guardado:", filename)

            except Exception as error:

                print("ERROR:", query, error)

        browser.close()


if __name__ == "__main__":
    diagnose()
