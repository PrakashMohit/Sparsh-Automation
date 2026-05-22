import asyncio
import csv
import random
import logging
import os
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

# ─── CONFIG ────────────────────────────────────────────────────────────────
STORE_URL    = os.getenv("STORE_URL", "https://ekacosmetics.myshopify.com")
CATALOG_URL  = os.getenv("CATALOG_URL", "/collections/all")   # e.g. /collections/all — auto-scraped if set
PRODUCT_URL  = os.getenv("PRODUCT_URL", "")   # fallback single product
TOTAL_ORDERS = int(os.getenv("TOTAL_ORDERS", "1000"))
SPREAD_HOURS = int(os.getenv("SPREAD_HOURS", "24"))
CONCURRENCY  = int(os.getenv("CONCURRENCY", "1"))
LOG_FILE     = os.getenv("LOG_FILE", "orders_log.csv")
HEADLESS     = os.getenv("HEADLESS", "true").lower() == "true"

DELAY_BETWEEN = (SPREAD_HOURS * 3600) / TOTAL_ORDERS  # seconds between each order

# Populated at startup by scrape_product_urls()
PRODUCT_URLS: list[str] = []

# ─── LOGGING ───────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── INDIAN TEST DATA ──────────────────────────────────────────────────────
FIRST_NAMES = [
    "Aarav","Vivaan","Aditya","Vihaan","Arjun","Sai","Reyansh","Ayaan","Krishna",
    "Ishaan","Shaurya","Atharv","Pranav","Dhruv","Kabir","Ritvik","Rohan",
    "Priya","Ananya","Pooja","Divya","Sneha","Kavya","Meera","Nisha","Riya",
    "Simran","Deepika","Shruti","Neha","Anjali","Swati","Lakshmi","Durga","Preeti",
]
LAST_NAMES = [
    "Sharma","Verma","Patel","Shah","Singh","Kumar","Gupta","Joshi","Mehta","Rao",
    "Nair","Reddy","Iyer","Pillai","Menon","Chandra","Bhat","Das","Mishra","Tiwari",
    "Dubey","Pandey","Agarwal","Bansal","Garg","Saxena","Yadav","Shukla","Malhotra",
]
LOCATIONS = [
    ("110001","New Delhi","Delhi"),
    ("400001","Mumbai","Maharashtra"),
    ("700001","Kolkata","West Bengal"),
    ("600001","Chennai","Tamil Nadu"),
    ("560001","Bengaluru","Karnataka"),
    ("500001","Hyderabad","Telangana"),
    ("380001","Ahmedabad","Gujarat"),
    ("411001","Pune","Maharashtra"),
    ("302001","Jaipur","Rajasthan"),
    ("226001","Lucknow","Uttar Pradesh"),
    ("160017","Chandigarh","Punjab"),
    ("682001","Kochi","Kerala"),
    ("751001","Bhubaneswar","Odisha"),
    ("781001","Guwahati","Assam"),
    ("800001","Patna","Bihar"),
    ("492001","Raipur","Chhattisgarh"),
    ("248001","Dehradun","Uttarakhand"),
    ("440001","Nagpur","Maharashtra"),
    ("641001","Coimbatore","Tamil Nadu"),
    ("530001","Visakhapatnam","Andhra Pradesh"),
    ("452001","Indore","Madhya Pradesh"),
    ("395001","Surat","Gujarat"),
    ("834001","Ranchi","Jharkhand"),
    ("462001","Bhopal","Madhya Pradesh"),
    ("201301","Noida","Uttar Pradesh"),
]
STREETS = [
    "MG Road","Anna Salai","Park Street","FC Road","SV Road",
    "Ring Road","Brigade Road","Linking Road","Civil Lines","Model Town",
    "Rajpur Road","Gandhi Nagar","Nehru Place","Sector 17","Koramangala",
]
AREAS = ["Nagar","Colony","Vihar","Enclave","Extension","Layout","Residency","Heights"]


def make_customer() -> dict:
    first  = random.choice(FIRST_NAMES)
    last   = random.choice(LAST_NAMES)
    pin, city, state = random.choice(LOCATIONS)
    house  = random.randint(1, 999)
    street = random.choice(STREETS)
    area   = random.choice(AREAS)
    prefix = random.choice(["6","7","8","9"])
    phone  = prefix + str(random.randint(100_000_000, 999_999_999))
    email  = f"test.{first.lower()}{random.randint(1,9999)}@mailtest.dev"
    return {
        "full_name": f"{first} {last}",
        "phone":     phone,
        "email":     email,
        "pincode":   pin,
        "address":   f"{house}, {street}, {area}",
        "city":      city,
        "state":     state,
    }


# ─── CSV LOGGER ────────────────────────────────────────────────────────────
def init_csv():
    with open(LOG_FILE, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["order_num","status","name","phone","city","pincode","product","timestamp","note"])


def log_csv(order_num, status, c, note=""):
    product_handle = c.get("product","").split("/products/")[-1].split("?")[0]
    with open(LOG_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            order_num, status, c["full_name"], c["phone"],
            c["city"], c["pincode"], product_handle,
            datetime.now().isoformat(), note,
        ])


# ─── PRODUCT SCRAPER ──────────────────────────────────────────────────────
async def scrape_product_urls(browser) -> list[str]:
    """Scrape all product URLs from the store catalog page."""
    base   = STORE_URL.rstrip("/")
    cat    = CATALOG_URL or "/collections/all"
    target = base + cat

    ctx  = await browser.new_context()
    page = await ctx.new_page()
    urls = set()

    try:
        log.info(f"Scraping product URLs from {target} ...")
        await page.goto(target, wait_until="domcontentloaded", timeout=40_000)
        await page.wait_for_timeout(2000)

        # Scroll to load lazy-loaded products
        for _ in range(5):
            await page.keyboard.press("End")
            await page.wait_for_timeout(800)

        # Grab all /products/ links
        links = await page.eval_on_selector_all(
            'a[href*="/products/"]',
            "els => els.map(e => e.href)"
        )
        for link in links:
            # Strip query params, keep clean product URL
            clean = link.split("?")[0].split("#")[0]
            if "/products/" in clean and not clean.endswith("/products/"):
                urls.add(clean)

        log.info(f"Found {len(urls)} products")
    except Exception as e:
        log.error(f"Scraping failed: {e}")
    finally:
        await ctx.close()

    return list(urls)


# ─── CORE ORDER FUNCTION ───────────────────────────────────────────────────
async def place_order(browser, order_num: int, semaphore: asyncio.Semaphore):
    async with semaphore:
        c = make_customer()
        # Pick a random product each time
        product = random.choice(PRODUCT_URLS) if PRODUCT_URLS else PRODUCT_URL
        c["product"] = product
        ctx  = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = await ctx.new_page()

        try:
            # ── 1. Product page (random from catalog) ─────────────────────
            handle = product.split('/products/')[-1].split('?')[0]
            log.info(f"[{order_num}] Product: {handle}")
            await page.goto(product, wait_until="domcontentloaded", timeout=40_000)
            await page.wait_for_timeout(1500)

            # ── 2. Click BUY NOW ──────────────────────────────────────────
            buy_btn = page.locator(
                'button:has-text("BUY NOW"), '
                'a:has-text("BUY NOW"), '
                'button:has-text("Buy Now")'
            ).first
            await buy_btn.wait_for(state="visible", timeout=15_000)
            await buy_btn.click()
            log.info(f"[{order_num}] Clicked BUY NOW")

            # ── 3. Find the fastrr/pickrr checkout iframe ─────────────────
            # The checkout form is in fastrr-boost-ui.pickrr.com (not sr-cdn.shiprocket.in)
            await page.wait_for_timeout(5000)

            sr_frame = None
            for attempt in range(15):
                for frame in page.frames:
                    url_f = frame.url or ""
                    if any(k in url_f for k in ["pickrr", "fastrr"]):
                        sr_frame = frame
                        log.info(f"[{order_num}] Found checkout frame: {url_f[:60]}")
                        break
                if sr_frame:
                    break
                await page.wait_for_timeout(500)

            if sr_frame is None:
                frames_info = [f.url[:60] for f in page.frames]
                raise Exception(f"Checkout iframe not found. Frames: {frames_info}")

            f = sr_frame  # shorthand

            # ── 4. Fill phone (unlocks the rest of the form) ──────────────
            phone_field = f.locator('#contactNumber')
            await phone_field.wait_for(state="visible", timeout=10_000)
            await phone_field.click()
            await phone_field.fill(c["phone"])
            log.info(f"[{order_num}] Filled phone: {c['phone']}")
            await page.wait_for_timeout(800)

            # ── 5. Fill pincode (triggers city/state autofill) ────────────
            pin_field = f.locator('#pincode')
            await pin_field.wait_for(state="visible", timeout=5_000)
            await pin_field.click()
            await pin_field.fill(c["pincode"])
            log.info(f"[{order_num}] Filled pincode: {c['pincode']}")
            await page.wait_for_timeout(1500)   # wait for city/state to autofill

            # ── 6. Full name ──────────────────────────────────────────────
            name_field = f.locator('#name')
            await name_field.wait_for(state="visible", timeout=5_000)
            await name_field.click()
            await name_field.fill(c["full_name"])

            # ── 7. Address ────────────────────────────────────────────────
            addr_field = f.locator('#line1')
            await addr_field.wait_for(state="visible", timeout=5_000)
            await addr_field.click()
            await addr_field.fill(c["address"])

            # ── 8. Email (optional) ───────────────────────────────────────
            email_field = f.locator('#email')
            if await email_field.is_visible():
                await email_field.click()
                await email_field.fill(c["email"])

            # ── 9. City — fill only if not auto-populated ─────────────────
            city_field = f.locator('#city')
            if await city_field.is_visible():
                city_val = await city_field.input_value()
                if not city_val:
                    await city_field.click()
                    await city_field.fill(c["city"])

            # ── 10. State — fill only if not auto-populated ───────────────
            state_field = f.locator('#state')
            if await state_field.is_visible():
                state_val = await state_field.input_value()
                if not state_val:
                    await state_field.click()
                    await state_field.fill(c["state"])

            await page.wait_for_timeout(3000)  # wait for fields to enable after pincode autofill

            # ── 11. Place order ───────────────────────────────────────────
            place_btn = f.locator('#src-place-order-number-btn')
            try:
                await place_btn.wait_for(state="visible", timeout=8_000)
            except Exception:
                place_btn = f.locator('button:has-text("Place order"), button:has-text("Place Order")').first
                await place_btn.wait_for(state="visible", timeout=8_000)
            await place_btn.scroll_into_view_if_needed()
            await place_btn.click()
            log.info(f"[{order_num}] Clicked Place order")

            # ── 12. Confirm success ───────────────────────────────────────
            await page.wait_for_timeout(5000)
            url = page.url
            try:
                frame_html = await f.content()
            except Exception:
                frame_html = ""
            page_html  = await page.content()
            combined   = url.lower() + page_html.lower() + frame_html.lower()
            success_signals = ["thank", "success", "order confirmed", "order placed", "order_id", "your order"]
            success = any(s in combined for s in success_signals)

            if success:
                log.info(f"[{order_num}] ✅  {c['full_name']} | {c['city']} | {c['pincode']}")
                log_csv(order_num, "SUCCESS", c)
            else:
                log.warning(f"[{order_num}] ⚠️  Unknown result — {url}")
                log_csv(order_num, "UNKNOWN", c, url)

        except PWTimeout as e:
            log.error(f"[{order_num}] ❌  Timeout — {e}")
            log_csv(order_num, "TIMEOUT", c, str(e)[:120])
        except Exception as e:
            log.error(f"[{order_num}] ❌  Error — {e}")
            log_csv(order_num, "FAILED", c, str(e)[:120])
        finally:
            await ctx.close()


# ─── ENSURE BROWSER INSTALLED ─────────────────────────────────────────────
def ensure_browser():
    import subprocess, sys
    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        log.error(f"Browser install failed: {result.stderr}")
    else:
        log.info("Chromium ready")


# ─── MAIN ──────────────────────────────────────────────────────────────────
async def main():
    global PRODUCT_URLS

    log.info(f"🚀  Starting {TOTAL_ORDERS} test orders over {SPREAD_HOURS}h")
    log.info(f"    Concurrency={CONCURRENCY} | Gap={DELAY_BETWEEN:.1f}s | Headless={HEADLESS}")

    init_csv()
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=HEADLESS,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-setuid-sandbox",
                "--single-process",
            ],
        )

        # ── Scrape all product URLs at startup ────────────────────────
        PRODUCT_URLS = await scrape_product_urls(browser)
        if not PRODUCT_URLS:
            if PRODUCT_URL:
                log.warning("Scraping returned 0 products, falling back to PRODUCT_URL")
                PRODUCT_URLS = [PRODUCT_URL]
            else:
                raise Exception("No products found and no PRODUCT_URL fallback set.")
        log.info(f"    Rotating across {len(PRODUCT_URLS)} products randomly")
        for u in PRODUCT_URLS:
            handle = u.split("/products/")[-1].split("?")[0]
            log.info(f"      • {handle}")

        # Run orders sequentially with a fresh browser every 50 orders
        # to avoid memory buildup on Render's 512MB starter plan
        completed = 0
        while completed < TOTAL_ORDERS:
            try:
                await place_order(browser, completed + 1, semaphore)
            except Exception as e:
                msg = str(e)
                if "closed" in msg or "crashed" in msg or "disconnected" in msg:
                    log.warning(f"Browser crashed at order {completed+1}, restarting...")
                    try:
                        await browser.close()
                    except Exception:
                        pass
                    browser = await pw.chromium.launch(
                        headless=HEADLESS,
                        args=[
                            "--no-sandbox",
                            "--disable-dev-shm-usage",
                            "--disable-gpu",
                            "--disable-setuid-sandbox",
                            "--single-process",
                        ],
                    )
                    log.info("Browser restarted, continuing...")
                else:
                    log.error(f"Order {completed+1} error: {e}")

            completed += 1
            if completed < TOTAL_ORDERS:
                await asyncio.sleep(DELAY_BETWEEN)

            # Restart browser every 50 orders to free memory
            if completed % 50 == 0 and completed < TOTAL_ORDERS:
                log.info(f"Restarting browser after {completed} orders (memory management)...")
                try:
                    await browser.close()
                except Exception:
                    pass
                browser = await pw.chromium.launch(
                    headless=HEADLESS,
                    args=[
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--disable-setuid-sandbox",
                        "--single-process",
                    ],
                )

        try:
            await browser.close()
        except Exception:
            pass

    log.info(f"✅  All done! Log saved → {LOG_FILE}")


# ─── DEBUG HELPER ──────────────────────────────────────────────────────────
async def debug_fields():
    """Run with --debug to dump all input fields in the modal and iframes."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        ctx  = await browser.new_context(viewport={"width": 1280, "height": 800})
        page = await ctx.new_page()
        await page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=40_000)
        await page.wait_for_timeout(2000)

        buy_btn = page.locator('button:has-text("BUY NOW"), a:has-text("BUY NOW"), button:has-text("Buy Now")').first
        await buy_btn.click()
        await page.wait_for_timeout(6000)  # wait for iframe to fully render its form

        print("\n===== FRAMES =====")
        for frame in page.frames:
            print(f"  Frame URL: {frame.url}  Name: {frame.name}")
        print("\n===== INPUTS on main frame =====")
        inputs = await page.locator("input").all()
        for inp in inputs:
            ph   = await inp.get_attribute("placeholder") or ""
            name = await inp.get_attribute("name") or ""
            id_  = await inp.get_attribute("id") or ""
            typ  = await inp.get_attribute("type") or ""
            vis  = await inp.is_visible()
            print(f"  visible={vis}  type={typ}  name={name}  id={id_}  placeholder={ph}")

        for i, frame in enumerate(page.frames[1:], 1):
            print(f"\n===== INPUTS in frame[{i}] ({frame.url}) =====")
            try:
                inputs = await frame.locator("input").all()
                for inp in inputs:
                    ph   = await inp.get_attribute("placeholder") or ""
                    name = await inp.get_attribute("name") or ""
                    id_  = await inp.get_attribute("id") or ""
                    typ  = await inp.get_attribute("type") or ""
                    vis  = await inp.is_visible()
                    print(f"  visible={vis}  type={typ}  name={name}  id={id_}  placeholder={ph}")
            except Exception as e:
                print(f"  Could not read frame: {e}")

        print("\n===== Done — check output above =====")
        await page.wait_for_timeout(20_000)
        await browser.close()


if __name__ == "__main__":
    import sys, traceback
    if len(sys.argv) > 1 and sys.argv[1] == "--debug":
        asyncio.run(debug_fields())
    else:
        ensure_browser()
        try:
            asyncio.run(main())
        except Exception as e:
            log.error(f"FATAL: {e}")
            traceback.print_exc()
            sys.exit(1)
