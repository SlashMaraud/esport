def generate_m3u():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Registrar peticiones que realiza la página
        page.on("request", lambda req: print(
            "[REQUEST]", req.method, req.url
        ))

        page.on("response", lambda res: print(
            "[RESPONSE]", res.status, res.url
        ) if any(x in res.url.lower() for x in
                 ["api", "search", "ace", "broadcast"]) else None)

        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)

        print("\n--- URL ---")
        print(page.url)

        print("\n--- INPUTS ---")
        print(page.locator("input").evaluate_all(
            "(els) => els.map(e => ({type:e.type, name:e.name, "
            "placeholder:e.placeholder, value:e.value}))"
        ))

        print("\n--- BOTONES ---")
        print(page.locator("button").evaluate_all(
            "(els) => els.map(e => e.innerText)"
        ))

        print("\n--- TEXTO DE LA PÁGINA ---")
        print(page.locator("body").inner_text()[:5000])

        browser.close()
