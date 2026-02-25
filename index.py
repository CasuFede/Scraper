import asyncio
import pandas as pd
import random
import os
from datetime import datetime
from fake_useragent import UserAgent
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async

# --- CONFIGURAZIONE GENOVA ---
# Lista estesa per coprire capillarmente la città
CAP_GENOVA = [
    "16121", "16129", "16142", "16146", "16149", 
    "16151", "16154", "16156", "16161", "16167"
]

CATEGORIE = ["freschi", "dispensa", "bevande"]
FILE_OUTPUT = f"carrefour_genova_completo_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"

class CarrefourGenovaScraper:
    def __init__(self):
        self.results = []
        self.seen_ids = set() # Per evitare duplicati prodotti nello stesso negozio
        self.processed_stores = set() # Per evitare di scansionare due volte lo stesso negozio
        self.current_cap = ""
        self.current_store = "N/D"
        self.ua = UserAgent()

    async def handle_cookies(self, page):
        try:
            btn = page.locator("#onetrust-accept-btn-handler")
            if await btn.is_visible(timeout=3000):
                await btn.click()
                print("[✓] Cookie accettati")
        except:
            pass

    async def intercept_api(self, response):
        """Intercetta i dati JSON direttamente dai server Carrefour."""
        if "Search-UpdateGrid" in response.url or "search-api" in response.url:
            try:
                content_type = await response.header_value("content-type")
                if content_type and "application/json" in content_type:
                    data = await response.json()
                    products = data.get('productSearch', {}).get('products', []) or data.get('products', [])
                    
                    for p in products:
                        p_id = p.get('id')
                        if p_id and p_id not in self.seen_ids:
                            self.results.append({
                                "data_estrazione": datetime.now().strftime("%d/%m/%Y %H:%M"),
                                "citta": "Genova",
                                "cap_inserito": self.current_cap,
                                "punto_vendita": self.current_store,
                                "brand": p.get('brand'),
                                "prodotto": p.get('productName'),
                                "prezzo_finito": p.get('price', {}).get('sales', {}).get('value'),
                                "prezzo_unita": p.get('price', {}).get('sales', {}).get('formattedUnitPrice'),
                                "in_offerta": "Sì" if p.get('price', {}).get('promo') else "No",
                                "id_prodotto": p_id
                            })
                            self.seen_ids.add(p_id)
            except:
                pass

    async def set_location_and_get_stores(self, page, cap):
        """Imposta il CAP e restituisce la lista dei negozi disponibili."""
        try:
            print(f"\n[*] Ricerca negozi per CAP {cap}...")
            await page.goto("https://www.carrefour.it/", wait_until="networkidle")
            await self.handle_cookies(page)

            # Clicca sul selettore di consegna/ritiro
            await page.click(".header-checkout-button", timeout=10000)
            
            # Seleziona "Ritiro in negozio"
            await page.get_by_role("tab", name="Ritiro in negozio").click()

            # Inserisce il CAP
            input_sel = "input[name='postalCode']"
            await page.wait_for_selector(input_sel)
            await page.fill(input_sel, cap)
            await asyncio.sleep(2)
            
            # Seleziona il primo suggerimento dell'indirizzo con la tastiera
            await page.keyboard.press("ArrowDown")
            await page.keyboard.press("Enter")
            
            # Attende il caricamento della lista negozi
            await page.wait_for_selector(".store-list-item", timeout=15000)
            
            # Restituisce i selettori dei negozi trovati
            return await page.locator(".store-list-item").all()
        except Exception as e:
            print(f"[X] Errore posizione per {cap}: {e}")
            return []

    async def scroll_page(self, page):
        """Scrolla per caricare i prodotti."""
        for _ in range(4):
            await page.mouse.wheel(0, 1500)
            await asyncio.sleep(1.5)

    def save_data(self):
        if self.results:
            df = pd.DataFrame(self.results)
            # UTF-8-SIG per compatibilità perfetta con Excel
            df.to_csv(FILE_OUTPUT, index=False, encoding='utf-8-sig')
            print(f"[!] Database aggiornato: {len(self.results)} prodotti totali.")

    async def run(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False) # Mantieni False per evitare bot-detection
            context = await browser.new_context(
                user_agent=self.ua.random,
                viewport={'width': 1280, 'height': 900}
            )
            page = await context.new_page()
            await stealth_async(page)
            page.on("response", self.intercept_api)

            for cap in CAP_GENOVA:
                self.current_cap = cap
                stores = await self.set_location_and_get_stores(page, cap)
                
                for i in range(len(stores)):
                    # Ricarichiamo la lista negozi a ogni ciclo perché il DOM cambia
                    stores_refresh = await page.locator(".store-list-item").all()
                    current_s = stores_refresh[i]
                    
                    # Estrai nome e indirizzo per identificare il negozio univocamente
                    s_name = await current_s.locator("h3").inner_text()
                    s_address = await current_s.locator(".store-address").inner_text()
                    store_id = f"{s_name} - {s_address}".strip()

                    if store_id in self.processed_stores:
                        print(f"[-] Salto {s_name}: già scansionato.")
                        continue

                    print(f"[→] Scansione negozio: {s_name}")
                    self.current_store = s_name
                    self.processed_stores.add(store_id)
                    self.seen_ids.clear() # Reset prodotti per nuovo negozio

                    # Clicca "Scegli"
                    try:
                        btn_scegli = current_s.get_by_role("button", name="Scegli")
                        await btn_scegli.click()
                        await page.wait_for_load_state("networkidle")
                        await asyncio.sleep(2)
                    except:
                        continue

                    # Navigazione Categorie
                    for cat in CATEGORIE:
                        print(f"    Scansione categoria: {cat}...")
                        url = f"https://www.carrefour.it/spesa-online/{cat}/"
                        await page.goto(url, wait_until="domcontentloaded")
                        await self.scroll_page(page)
                        await asyncio.sleep(random.uniform(1, 3))
                    
                    self.save_data()
                    
                    # Torna alla home per cambiare negozio
                    await page.goto("https://www.carrefour.it/")
                    await page.click(".header-checkout-button")
                    await page.get_by_role("tab", name="Ritiro in negozio").click()
                    await page.fill("input[name='postalCode']", cap)
                    await page.keyboard.press("ArrowDown")
                    await page.keyboard.press("Enter")
                    await page.wait_for_selector(".store-list-item")

            await browser.close()
            print(f"\n--- FINE OPERAZIONE ---")

if __name__ == "__main__":
    scraper = CarrefourGenovaScraper()
    asyncio.run(scraper.run())