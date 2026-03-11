import asyncio
import pandas as pd
import os
import json
from datetime import datetime
from fake_useragent import UserAgent
from playwright.async_api import async_playwright

# --- CONFIGURAZIONE ---
CATEGORIE = ["frutta-e-verdura"] # Puoi aggiungere "articoli-per-la-casa" ecc.
FILE_OUTPUT = "estrazione_carrefour_genova.csv"

class CarrefourGenovaScraper:
    def __init__(self):
        self.results = []
        self.processed_stores = set()
        self.ua = UserAgent()
        self.current_store = "N/D"

    async def handle_cookies(self, page):
        """Accetta i cookie all'avvio."""
        try:
            btn = page.locator("#onetrust-accept-btn-handler")
            if await btn.is_visible(timeout=5000):
                await btn.click()
                print("[✓] Cookie accettati.")
        except: pass

    async def scrape_products_from_dom(self, page):
        """Estrae i prodotti usando la gerarchia di classi fornita."""
        # Attendiamo la griglia prodotti
        try:
            await page.wait_for_selector("div.product-grid.js-search-content", timeout=15000)
        except:
            print(f"      [!] Errore: Griglia prodotti non trovata.")
            return

        # Singoli prodotti
        items = await page.locator("div.product-item article.product-tile").all()
        
        count = 0
        for item in items:
            try:
                # 1. Dati dal JSON (Metodo primario)
                raw_json = await item.get_attribute("data-product-json")
                p_data = json.loads(raw_json) if raw_json else {}

                # 2. Nome Prodotto
                # Cerchiamo span.tile-description dentro h3 > a
                name_loc = item.locator("span.tile-description, .tile-description")
                name = await name_loc.first.inner_text() if await name_loc.count() > 0 else p_data.get('name', 'N/D')

                # 3. Brand
                brand_loc = item.locator("span.brand")
                brand = await brand_loc.inner_text() if await brand_loc.count() > 0 else "N/D"

                # 4. Prezzi (Scontati vs Normali)
                # Se è scontato cerchiamo span.value.discounted
                price_loc = item.locator("span.value.discounted")
                if await price_loc.count() == 0:
                    price_loc = item.locator("span.value") # Prezzo normale
                
                price_val = await price_loc.first.get_attribute("content") if await price_loc.count() > 0 else "N/D"

                # 5. Prezzo unitario (kg/pz/lt)
                unit_loc = item.locator("span.unit-price")
                unit_price = await unit_loc.first.inner_text() if await unit_loc.count() > 0 else "N/D"

                # 6. Verifica Offerta
                is_promo = "Sì" if await item.locator("div.discount-percentage").count() > 0 else "No"

                if name != "N/D" and price_val != "N/D":
                    self.results.append({
                        "data_estrazione": datetime.now().strftime("%d/%m/%Y"),
                        "negozio": self.current_store,
                        "brand": brand.strip(),
                        "prodotto": name.strip(),
                        "prezzo": price_val,
                        "prezzo_unita": unit_price.strip(),
                        "offerta": is_promo,
                        "id": p_data.get('id') or await item.get_attribute("data-pid")
                    })
                    count += 1
            except: continue
        
        if count > 0:
            print(f"      [✓] Trovati {count} prodotti.")

    async def open_and_setup_sidebar(self, page):
        """Apre la sidebar e imposta il metodo di consegna."""
        # Clicca sulla barra metodo di consegna
        await page.get_by_text("Imposta metodo e indirizzo").first.click()
        await asyncio.sleep(2)

        input_box = page.locator("#sidebarAddressAutocomplete").first
        if await input_box.is_visible():
            await input_box.fill("Genova")
            await asyncio.sleep(2)
            await page.locator(".pac-item").filter(has_text="Genova, Italia").first.click() #
            
            # Selettore CAP
            select_el = page.locator("select#storelist")
            await select_el.wait_for(state="attached", timeout=10000)
            await select_el.select_option(index=1)
            await select_el.dispatch_event("change")
            await page.locator(".btn-submit-store").filter(has_text="OK").click(force=True)
            await asyncio.sleep(2)

        # Selezione Ritiro in Negozio
        pickup_btn = page.locator('button.select-service[data-option-service="pickup_in_store"]')
        await pickup_btn.wait_for(state="visible")
        await pickup_btn.click()
        
        # Attesa lista card negozi
        await page.locator("ul.stores-list li.store-card").first.wait_for(state="visible")
        await asyncio.sleep(1)

    async def run(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context(user_agent=self.ua.random)
            page = await context.new_page()

            await page.goto("https://www.carrefour.it/spesa-online/", wait_until="load")
            await self.handle_cookies(page)
            await self.open_and_setup_sidebar(page)

            stores_locator = page.locator("ul.stores-list li.store-card")
            num_stores = await stores_locator.count()
            print(f"[*] Trovati {num_stores} punti di ritiro a Genova.")

            for i in range(num_stores):
                current_cards = await page.locator("ul.stores-list li.store-card").all()
                card = current_cards[i]
                
                info_text = await card.locator("p.store-details").inner_text()
                store_name = info_text.splitlines()[0].strip()
                
                if store_name in self.processed_stores: continue

                print(f"\n>>> NEGOZIO ({i+1}/{num_stores}): {store_name}")
                self.current_store = store_name
                self.processed_stores.add(store_name)

                # Click sullo store
                await card.click()
                await asyncio.sleep(3)
                
                # --- CLICK "SCEGLI PIÙ TARDI" ---
                later_btn = page.locator('button:has-text("Scegli più tardi"), button.btn-secondary.small').first
                try:
                    if await later_btn.is_visible(timeout=5000):
                        print("      [*] Azione: Scegli più tardi.")
                        await later_btn.click()
                        await asyncio.sleep(3)
                except: pass
                
                for cat in CATEGORIE:
                    print(f"    - Categoria: {cat}")
                    await page.goto(f"https://www.carrefour.it/spesa-online/{cat}/", wait_until="domcontentloaded")
                    
                    # Scroll dinamico per caricare i prodotti (lazy loading)
                    for _ in range(5):
                        await page.mouse.wheel(0, 1500)
                        await asyncio.sleep(2)
                    
                    # Scraping dal DOM
                    await self.scrape_products_from_dom(page)
                
                self.save_data()

                # Reset per il prossimo negozio
                await page.goto("https://www.carrefour.it/spesa-online/")
                await asyncio.sleep(2)
                await self.open_and_setup_sidebar(page)

            await browser.close()

    def save_data(self):
        if self.results:
            df = pd.DataFrame(self.results).drop_duplicates(subset=['id', 'negozio'])
            path = os.path.join(os.getcwd(), FILE_OUTPUT)
            df.to_csv(path, index=False, encoding='utf-8-sig')
            print(f"    [OK] CSV aggiornato: {path} (Totale: {len(df)} prodotti)")

if __name__ == "__main__":
    scraper = CarrefourGenovaScraper()
    asyncio.run(scraper.run())
