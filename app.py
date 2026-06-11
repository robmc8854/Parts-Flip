import os, re, json, time, logging
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# ── Discount code cache (refreshed every 6 hours) ──────────────────
_code_cache = {}
_code_ts    = {}
CODE_TTL    = 6 * 3600

def get_cached_codes(supplier_key):
    now = time.time()
    if supplier_key in _code_cache and now - _code_ts.get(supplier_key, 0) < CODE_TTL:
        return _code_cache[supplier_key]
    codes = fetch_discount_codes(supplier_key)
    _code_cache[supplier_key] = codes
    _code_ts[supplier_key]    = now
    return codes

def fetch_discount_codes(supplier_key):
    """Scrape promo/discount codes from each supplier's own site."""
    scrapers = {
        "gsf":          scrape_codes_gsf,
        "eurocarparts": scrape_codes_ecp,
        "halfords":     scrape_codes_halfords,
        "autodoc":      scrape_codes_autodoc,
    }
    try:
        fn = scrapers.get(supplier_key)
        if fn:
            codes = fn()
            log.info(f"Fetched {len(codes)} codes for {supplier_key}: {codes}")
            return codes
    except Exception as e:
        log.warning(f"Code fetch failed for {supplier_key}: {e}")
    return []

def scrape_codes_gsf():
    codes = []
    try:
        r = SESSION.get("https://www.gsfcarparts.com/offers", timeout=10)
        soup = BeautifulSoup(r.text, "lxml")
        # Look for promo code patterns in page text
        text = soup.get_text()
        found = re.findall(r'\b([A-Z]{2,}[0-9]{0,4})\b', text)
        # Filter to likely promo codes (3-12 chars, mixed alpha)
        for f in found:
            if 4 <= len(f) <= 12 and not f.isdigit() and f not in codes:
                codes.append(f)
        # Also check for percentage off mentions near code patterns
    except Exception as e:
        log.warning(f"GSF code scrape error: {e}")
    return codes[:10]

def scrape_codes_ecp():
    codes = []
    try:
        for url in ["https://www.eurocarparts.com/offers", "https://www.eurocarparts.com/discount-codes"]:
            try:
                r = SESSION.get(url, timeout=10)
                soup = BeautifulSoup(r.text, "lxml")
                # ECP often shows codes in specific elements
                for el in soup.find_all(["span", "p", "div", "strong"]):
                    text = el.get_text(strip=True)
                    found = re.findall(r'\b([A-Z0-9]{4,12})\b', text)
                    for f in found:
                        if f not in codes and not f.isdigit():
                            codes.append(f)
            except:
                pass
    except Exception as e:
        log.warning(f"ECP code scrape error: {e}")
    return codes[:10]

def scrape_codes_halfords():
    codes = []
    try:
        r = SESSION.get("https://www.halfords.com/offers/", timeout=10)
        soup = BeautifulSoup(r.text, "lxml")
        text = soup.get_text()
        found = re.findall(r'\b([A-Z]{2,}[0-9]{2,})\b', text)
        for f in found:
            if 4 <= len(f) <= 12 and f not in codes:
                codes.append(f)
    except Exception as e:
        log.warning(f"Halfords code scrape error: {e}")
    return codes[:10]

def scrape_codes_autodoc():
    codes = []
    try:
        r = SESSION.get("https://www.autodoc.co.uk/promo-codes", timeout=10)
        soup = BeautifulSoup(r.text, "lxml")
        text = soup.get_text()
        found = re.findall(r'\b([A-Z0-9]{4,12})\b', text)
        for f in found:
            if f not in codes and not f.isdigit():
                codes.append(f)
    except Exception as e:
        log.warning(f"Autodoc code scrape error: {e}")
    return codes[:10]


# ── Price scrapers ─────────────────────────────────────────────────

def scrape_gsf(query):
    results = []
    try:
        url = f"https://www.gsfcarparts.com/search?q={requests.utils.quote(query)}"
        r = SESSION.get(url, timeout=12)
        soup = BeautifulSoup(r.text, "lxml")

        items = soup.select(".product-tile, .product-item, [data-product-id], .product-card")
        if not items:
            # Try alternative selectors
            items = soup.select("li.item, .product")

        for item in items[:6]:
            try:
                title_el = item.select_one(".product-tile__name, .product-name, h2, h3, .name")
                price_el = item.select_one(".product-tile__price, .price, .special-price, [class*='price']")
                part_el  = item.select_one(".product-tile__part-number, .part-number, [class*='part']")

                if not title_el or not price_el:
                    continue

                title = title_el.get_text(strip=True)[:80]
                price_text = price_el.get_text(strip=True)
                price = parse_price(price_text)
                if not price:
                    continue

                part_num = part_el.get_text(strip=True) if part_el else ""
                link_el  = item.select_one("a[href]")
                link     = "https://www.gsfcarparts.com" + link_el["href"] if link_el and link_el["href"].startswith("/") else (link_el["href"] if link_el else url)

                results.append({
                    "supplier": "GSF Car Parts",
                    "supplierKey": "gsf",
                    "title": title,
                    "brand": extract_brand(title),
                    "partNumber": part_num,
                    "price": price,
                    "url": link,
                    "inStock": True,
                    "deliveryDays": 1,
                })
            except Exception as e:
                log.debug(f"GSF item parse error: {e}")

    except Exception as e:
        log.warning(f"GSF scrape error: {e}")
    return results


def scrape_ecp(query):
    results = []
    try:
        url = f"https://www.eurocarparts.com/search?q={requests.utils.quote(query)}"
        r = SESSION.get(url, timeout=12)
        soup = BeautifulSoup(r.text, "lxml")

        items = soup.select(".product-card, .product-item, [class*='product-list'] li, .search-results .item")
        if not items:
            items = soup.select("article, .product")

        for item in items[:6]:
            try:
                title_el = item.select_one("h2, h3, .product-title, .product-name, [class*='title']")
                price_el = item.select_one(".price, [class*='price'], .now-price, .sale-price")
                if not title_el or not price_el:
                    continue

                title = title_el.get_text(strip=True)[:80]
                price = parse_price(price_el.get_text(strip=True))
                if not price:
                    continue

                part_el  = item.select_one("[class*='part'], [class*='sku'], .part-number")
                part_num = part_el.get_text(strip=True) if part_el else ""
                link_el  = item.select_one("a[href]")
                link     = "https://www.eurocarparts.com" + link_el["href"] if link_el and link_el.get("href","").startswith("/") else (link_el["href"] if link_el else url)

                results.append({
                    "supplier": "Euro Car Parts",
                    "supplierKey": "eurocarparts",
                    "title": title,
                    "brand": extract_brand(title),
                    "partNumber": part_num,
                    "price": price,
                    "url": link,
                    "inStock": True,
                    "deliveryDays": 1,
                })
            except Exception as e:
                log.debug(f"ECP item parse error: {e}")

    except Exception as e:
        log.warning(f"ECP scrape error: {e}")
    return results


def scrape_halfords(query):
    results = []
    try:
        url = f"https://www.halfords.com/search?term={requests.utils.quote(query)}"
        r = SESSION.get(url, timeout=12)
        soup = BeautifulSoup(r.text, "lxml")

        items = soup.select(".product-tile, .product-item, .search-product, [class*='product']")
        for item in items[:6]:
            try:
                title_el = item.select_one("h2, h3, .product-name, [class*='title'], [class*='name']")
                price_el = item.select_one(".price, [class*='price']")
                if not title_el or not price_el:
                    continue
                title = title_el.get_text(strip=True)[:80]
                price = parse_price(price_el.get_text(strip=True))
                if not price:
                    continue
                link_el = item.select_one("a[href]")
                link    = "https://www.halfords.com" + link_el["href"] if link_el and link_el.get("href","").startswith("/") else url
                results.append({
                    "supplier": "Halfords",
                    "supplierKey": "halfords",
                    "title": title,
                    "brand": extract_brand(title),
                    "partNumber": "",
                    "price": price,
                    "url": link,
                    "inStock": True,
                    "deliveryDays": 1,
                })
            except Exception as e:
                log.debug(f"Halfords item parse error: {e}")
    except Exception as e:
        log.warning(f"Halfords scrape error: {e}")
    return results


def scrape_autodoc(query):
    results = []
    try:
        url = f"https://www.autodoc.co.uk/search?search={requests.utils.quote(query)}"
        r = SESSION.get(url, timeout=12)
        soup = BeautifulSoup(r.text, "lxml")
        items = soup.select(".product-card, .listing__item, [class*='product']")
        for item in items[:6]:
            try:
                title_el = item.select_one("h2, h3, .product-title, [class*='title']")
                price_el = item.select_one(".price, [class*='price']")
                if not title_el or not price_el:
                    continue
                title = title_el.get_text(strip=True)[:80]
                price = parse_price(price_el.get_text(strip=True))
                if not price:
                    continue
                link_el = item.select_one("a[href]")
                link    = "https://www.autodoc.co.uk" + link_el["href"] if link_el and link_el.get("href","").startswith("/") else url
                results.append({
                    "supplier": "Autodoc",
                    "supplierKey": "autodoc",
                    "title": title,
                    "brand": extract_brand(title),
                    "partNumber": "",
                    "price": price,
                    "url": link,
                    "inStock": True,
                    "deliveryDays": 3,
                })
            except Exception as e:
                log.debug(f"Autodoc item parse error: {e}")
    except Exception as e:
        log.warning(f"Autodoc scrape error: {e}")
    return results


def scrape_ebay_lowest(query):
    """Get lowest Buy It Now price from eBay UK search."""
    try:
        url = f"https://www.ebay.co.uk/sch/i.html?_nkw={requests.utils.quote(query)}&LH_BIN=1&_sop=15"
        r = SESSION.get(url, timeout=12)
        soup = BeautifulSoup(r.text, "lxml")

        prices = []
        listing_count = 0

        # Count listings
        count_el = soup.select_one(".srp-controls__count-heading, h1.srp-controls__count")
        if count_el:
            m = re.search(r'[\d,]+', count_el.get_text())
            if m:
                listing_count = int(m.group().replace(",",""))

        # Get prices
        for el in soup.select(".s-item__price, .x-price-primary"):
            text = el.get_text(strip=True)
            p = parse_price(text)
            if p and p > 0.50:
                prices.append(p)

        if prices:
            return {"lowest": round(min(prices), 2), "count": listing_count or len(prices)}
    except Exception as e:
        log.warning(f"eBay scrape error: {e}")
    return {"lowest": None, "count": 0}


# ── Helpers ────────────────────────────────────────────────────────

def parse_price(text):
    """Extract a GBP price from a string."""
    text = text.replace(",","")
    m = re.search(r'[\d]+\.[\d]{2}', text)
    if m:
        return float(m.group())
    m = re.search(r'[\d]+', text)
    if m:
        return float(m.group())
    return None

def extract_brand(title):
    brands = ["Bosch","Mann","Febi","Valeo","SKF","NGK","Delphi","Brembo","TRW","FAG",
              "LUK","INA","Pierburg","Denso","ACDelco","Meyle","Lemforder","Gates","Dayco",
              "OEM","Genuine","Mintex","Pagid","Ferodo","EBC","Lucas","Hella","Osram","Philips"]
    for b in brands:
        if b.lower() in title.lower():
            return b
    words = title.split()
    return words[0] if words else ""

def apply_discount(price, codes, supplier_key):
    """
    Apply the best available discount code.
    We try common patterns: percentage off codes like 10OFF, 15OFF etc.
    In production this would test codes against the supplier's cart API.
    """
    if not codes or not price:
        return price, None

    best_price = price
    best_code  = None

    for code in codes:
        # Extract percentage from code name pattern e.g. "10OFF", "SAVE15", "20PERCENT"
        m = re.search(r'(\d{1,2})(?:OFF|SAVE|PCT|PERCENT)?', code, re.IGNORECASE)
        if m:
            pct = int(m.group(1))
            if 2 <= pct <= 40:  # sanity check
                discounted = round(price * (1 - pct/100), 2)
                if discounted < best_price:
                    best_price = discounted
                    best_code  = code

    return best_price, best_code


# ── Routes ─────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return jsonify({"status": "ok", "version": "1.0"})


@app.route("/search")
def search():
    query     = request.args.get("q", "").strip()
    suppliers = request.args.get("suppliers", "gsf,eurocarparts,halfords,autodoc").split(",")

    if not query:
        return jsonify({"error": "Missing query parameter q"}), 400

    all_results = []
    scraper_map = {
        "gsf":          scrape_gsf,
        "eurocarparts": scrape_ecp,
        "halfords":     scrape_halfords,
        "autodoc":      scrape_autodoc,
    }

    for sup in suppliers:
        sup = sup.strip()
        fn  = scraper_map.get(sup)
        if not fn:
            continue

        items = fn(query)

        # Fetch discount codes for this supplier
        codes = get_cached_codes(sup)

        for item in items:
            # Apply best discount code
            disc_price, best_code = apply_discount(item["price"], codes, sup)
            item["originalPrice"]  = item["price"]
            item["price"]          = disc_price
            item["discountCode"]   = best_code
            item["discountCodes"]  = codes[:5]  # return top 5 codes found
            all_results.append(item)

    # Sort cheapest first
    all_results.sort(key=lambda x: x["price"])

    return jsonify({"results": all_results, "query": query, "count": len(all_results)})


@app.route("/ebay-price")
def ebay_price():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "Missing query"}), 400
    data = scrape_ebay_lowest(query)
    return jsonify(data)


@app.route("/scan")
def scan():
    """
    Scan endpoint: takes a vehicle + category, returns parts list with
    real source prices and real eBay lowest prices.
    """
    vehicle  = request.args.get("vehicle", "").strip()
    category = request.args.get("category", "").strip()
    parts    = request.args.get("parts", "").strip()  # comma-separated part names

    if not parts:
        return jsonify({"error": "Missing parts list"}), 400

    part_list = [p.strip() for p in parts.split("|") if p.strip()]
    results   = []

    for part in part_list[:30]:  # cap at 30
        query = f"{part} {vehicle}".strip()

        # Scrape all suppliers
        items = []
        for fn in [scrape_gsf, scrape_ecp, scrape_halfords, scrape_autodoc]:
            try:
                items.extend(fn(query))
            except Exception as e:
                log.warning(f"Scrape error for {query}: {e}")

        if not items:
            continue

        # Apply discount codes
        for item in items:
            codes = get_cached_codes(item["supplierKey"])
            disc_price, best_code = apply_discount(item["price"], codes, item["supplierKey"])
            item["originalPrice"] = item["price"]
            item["price"]         = disc_price
            item["discountCode"]  = best_code

        # Pick cheapest
        items.sort(key=lambda x: x["price"])
        best = items[0]

        # Get eBay lowest price
        ebay_data = scrape_ebay_lowest(query)
        best["ebayLowestPrice"]  = ebay_data.get("lowest")
        best["ebayListingCount"] = ebay_data.get("count", 0)
        best["fitment"]          = vehicle
        best["category"]         = category
        best["allSources"]       = items[:3]  # top 3 cheapest

        results.append(best)

        time.sleep(0.5)  # be polite to servers

    return jsonify({"results": results, "count": len(results)})


@app.route("/codes")
def codes():
    """Return currently cached discount codes for all suppliers."""
    sup = request.args.get("supplier", "")
    if sup:
        return jsonify({"supplier": sup, "codes": get_cached_codes(sup)})
    all_codes = {}
    for s in ["gsf","eurocarparts","halfords","autodoc"]:
        all_codes[s] = get_cached_codes(s)
    return jsonify(all_codes)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
