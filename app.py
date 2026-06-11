import os, re, json, time, logging
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Rotate user agents to avoid blocks
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

def make_session():
    import random
    s = requests.Session()
    s.headers.update({
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    })
    return s

# ── Price helpers ──────────────────────────────────────────────────
def parse_price(text):
    if not text:
        return None
    text = text.replace(",", "").replace("\xa3", "").strip()
    m = re.search(r"(\d+\.\d{2})", text)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+)", text)
    if m:
        v = float(m.group(1))
        return v if v < 10000 else None
    return None

def extract_brand(title):
    brands = ["Bosch","Mann","Febi","Valeo","SKF","NGK","Delphi","Brembo","TRW","FAG",
              "LUK","INA","Pierburg","Denso","Meyle","Lemforder","Gates","Dayco","Hella",
              "Mintex","Pagid","Ferodo","EBC","Lucas","Osram","Philips","Mahle","Purflux",
              "Champion","Fram","WIX","Knecht","Filtron","Blue Print","Quinton Hazell"]
    t = title.lower()
    for b in brands:
        if b.lower() in t:
            return b
    return title.split()[0] if title.split() else ""

def apply_best_discount(price, codes):
    best_price = price
    best_code  = None
    for code in codes:
        m = re.search(r"(\d{1,2})", code)
        if m:
            pct = int(m.group(1))
            if 2 <= pct <= 50:
                disc = round(price * (1 - pct/100), 2)
                if disc < best_price:
                    best_price = disc
                    best_code  = code
    return best_price, best_code

# ── Discount code scrapers ─────────────────────────────────────────
_code_cache = {}
_code_ts    = {}
CODE_TTL    = 6 * 3600

def get_codes(supplier):
    now = time.time()
    if supplier in _code_cache and now - _code_ts.get(supplier, 0) < CODE_TTL:
        return _code_cache[supplier]
    fns = {
        "gsf":          lambda: scrape_codes("https://www.gsfcarparts.com/offers"),
        "eurocarparts": lambda: scrape_codes("https://www.eurocarparts.com/offers"),
        "halfords":     lambda: scrape_codes("https://www.halfords.com/offers/"),
        "autodoc":      lambda: scrape_codes("https://www.autodoc.co.uk/promo-codes"),
    }
    codes = []
    try:
        fn = fns.get(supplier)
        if fn:
            codes = fn()
    except Exception as e:
        log.warning(f"Code scrape failed for {supplier}: {e}")
    _code_cache[supplier] = codes
    _code_ts[supplier]    = now
    return codes

def scrape_codes(url):
    codes = []
    try:
        s = make_session()
        r = s.get(url, timeout=10)
        text = r.text
        # Find patterns like CODE10, SAVE15, 10OFF, ECP20 etc
        found = re.findall(r"\b([A-Z]{2,}[0-9]{1,2}|[A-Z]{2,}[0-9]{2,}[A-Z]*)\b", text)
        seen = set()
        for f in found:
            if 4 <= len(f) <= 12 and f not in seen:
                seen.add(f)
                codes.append(f)
    except Exception as e:
        log.warning(f"scrape_codes {url}: {e}")
    return codes[:10]

# ── GSF Car Parts ──────────────────────────────────────────────────
def scrape_gsf(query):
    results = []
    try:
        s = make_session()
        # GSF has a JSON search API
        api_url = f"https://www.gsfcarparts.com/search?q={requests.utils.quote(query)}&format=json"
        r = s.get(api_url, timeout=12)
        
        # Try JSON first
        try:
            data = r.json()
            products = data.get("products", {}).get("results", []) or data.get("results", [])
            for p in products[:6]:
                price = p.get("price", {})
                if isinstance(price, dict):
                    price_val = price.get("min") or price.get("current") or price.get("base")
                else:
                    price_val = price
                price_val = parse_price(str(price_val)) if price_val else None
                if not price_val:
                    continue
                results.append({
                    "supplier": "GSF Car Parts",
                    "supplierKey": "gsf",
                    "title": (p.get("title") or p.get("name",""))[:80],
                    "brand": p.get("brand","") or extract_brand(p.get("title","")),
                    "partNumber": p.get("sku") or p.get("part_number",""),
                    "price": price_val,
                    "url": "https://www.gsfcarparts.com" + (p.get("url","") or ""),
                    "inStock": p.get("available", True),
                    "deliveryDays": 1,
                })
        except:
            pass

        # HTML fallback
        if not results:
            r2 = s.get(f"https://www.gsfcarparts.com/search?q={requests.utils.quote(query)}", timeout=12)
            soup = BeautifulSoup(r2.text, "lxml")
            for sel in [".product-tile", ".product-item", ".product-card", "li.item"]:
                items = soup.select(sel)
                if items:
                    for item in items[:6]:
                        try:
                            t_el = item.select_one("h2,h3,.product-tile__name,.product-name,.name")
                            p_el = item.select_one(".product-tile__price,.price,.special-price,[class*=price]")
                            if not t_el or not p_el:
                                continue
                            price = parse_price(p_el.get_text())
                            if not price:
                                continue
                            link = item.select_one("a[href]")
                            href = link["href"] if link else ""
                            if href.startswith("/"):
                                href = "https://www.gsfcarparts.com" + href
                            results.append({
                                "supplier": "GSF Car Parts",
                                "supplierKey": "gsf",
                                "title": t_el.get_text(strip=True)[:80],
                                "brand": extract_brand(t_el.get_text(strip=True)),
                                "partNumber": "",
                                "price": price,
                                "url": href,
                                "inStock": True,
                                "deliveryDays": 1,
                            })
                        except:
                            pass
                    break

    except Exception as e:
        log.warning(f"GSF error: {e}")
    return results

# ── Euro Car Parts ─────────────────────────────────────────────────
def scrape_ecp(query):
    results = []
    try:
        s = make_session()
        s.headers.update({"Referer": "https://www.eurocarparts.com/"})

        # ECP search
        url = f"https://www.eurocarparts.com/search?q={requests.utils.quote(query)}"
        r = s.get(url, timeout=12)
        soup = BeautifulSoup(r.text, "lxml")

        for sel in [".product-card","article.product",".search-results .item","[class*=product-list] li",".product"]:
            items = soup.select(sel)
            if len(items) >= 2:
                for item in items[:6]:
                    try:
                        t_el = item.select_one("h2,h3,[class*=title],[class*=name],.product-title")
                        p_el = item.select_one("[class*=price],.price,.now,.sale")
                        if not t_el or not p_el:
                            continue
                        price = parse_price(p_el.get_text())
                        if not price:
                            continue
                        pn_el = item.select_one("[class*=part],[class*=sku],.reference")
                        link  = item.select_one("a[href]")
                        href  = link["href"] if link else ""
                        if href.startswith("/"):
                            href = "https://www.eurocarparts.com" + href
                        results.append({
                            "supplier": "Euro Car Parts",
                            "supplierKey": "eurocarparts",
                            "title": t_el.get_text(strip=True)[:80],
                            "brand": extract_brand(t_el.get_text(strip=True)),
                            "partNumber": pn_el.get_text(strip=True) if pn_el else "",
                            "price": price,
                            "url": href,
                            "inStock": True,
                            "deliveryDays": 1,
                        })
                    except:
                        pass
                break

    except Exception as e:
        log.warning(f"ECP error: {e}")
    return results

# ── Halfords ───────────────────────────────────────────────────────
def scrape_halfords(query):
    results = []
    try:
        s = make_session()
        url = f"https://www.halfords.com/search?term={requests.utils.quote(query)}"
        r   = s.get(url, timeout=12)
        soup = BeautifulSoup(r.text, "lxml")

        # Halfords often loads via JS but some results in HTML
        for sel in [".product-tile",".product-item","[class*=product]","article"]:
            items = soup.select(sel)
            if len(items) >= 2:
                for item in items[:6]:
                    try:
                        t_el = item.select_one("h2,h3,[class*=name],[class*=title]")
                        p_el = item.select_one("[class*=price],.price")
                        if not t_el or not p_el:
                            continue
                        price = parse_price(p_el.get_text())
                        if not price:
                            continue
                        link = item.select_one("a[href]")
                        href = link["href"] if link else ""
                        if href.startswith("/"):
                            href = "https://www.halfords.com" + href
                        results.append({
                            "supplier": "Halfords",
                            "supplierKey": "halfords",
                            "title": t_el.get_text(strip=True)[:80],
                            "brand": extract_brand(t_el.get_text(strip=True)),
                            "partNumber": "",
                            "price": price,
                            "url": href,
                            "inStock": True,
                            "deliveryDays": 1,
                        })
                    except:
                        pass
                break

    except Exception as e:
        log.warning(f"Halfords error: {e}")
    return results

# ── Autodoc ────────────────────────────────────────────────────────
def scrape_autodoc(query):
    results = []
    try:
        s = make_session()
        url = f"https://www.autodoc.co.uk/search?search={requests.utils.quote(query)}"
        r   = s.get(url, timeout=12)
        soup = BeautifulSoup(r.text, "lxml")

        for sel in [".product-card",".listing__item","[class*=product]","article"]:
            items = soup.select(sel)
            if len(items) >= 2:
                for item in items[:6]:
                    try:
                        t_el = item.select_one("h2,h3,[class*=title],[class*=name]")
                        p_el = item.select_one("[class*=price],.price")
                        if not t_el or not p_el:
                            continue
                        price = parse_price(p_el.get_text())
                        if not price:
                            continue
                        link = item.select_one("a[href]")
                        href = link["href"] if link else ""
                        if href.startswith("/"):
                            href = "https://www.autodoc.co.uk" + href
                        results.append({
                            "supplier": "Autodoc",
                            "supplierKey": "autodoc",
                            "title": t_el.get_text(strip=True)[:80],
                            "brand": extract_brand(t_el.get_text(strip=True)),
                            "partNumber": "",
                            "price": price,
                            "url": href,
                            "inStock": True,
                            "deliveryDays": 3,
                        })
                    except:
                        pass
                break

    except Exception as e:
        log.warning(f"Autodoc error: {e}")
    return results

# ── eBay UK ────────────────────────────────────────────────────────
def scrape_ebay(query):
    try:
        s = make_session()
        # BIN only, sorted by lowest price
        url = f"https://www.ebay.co.uk/sch/i.html?_nkw={requests.utils.quote(query)}&LH_BIN=1&_sop=15&LH_PrefLoc=1"
        r   = s.get(url, timeout=12)
        soup = BeautifulSoup(r.text, "lxml")

        prices = []
        count  = 0

        c_el = soup.select_one(".srp-controls__count-heading")
        if c_el:
            m = re.search(r"[\d,]+", c_el.get_text())
            if m:
                count = int(m.group().replace(",",""))

        for el in soup.select(".s-item__price"):
            text = el.get_text(strip=True)
            if "to" in text.lower():
                continue
            p = parse_price(text)
            if p and 0.99 < p < 5000:
                prices.append(p)

        if prices:
            prices.sort()
            # Skip suspiciously low outliers (under £1)
            valid = [p for p in prices if p >= 1.0]
            if valid:
                return {"lowest": valid[0], "count": count or len(valid), "median": sorted(valid)[len(valid)//2]}

    except Exception as e:
        log.warning(f"eBay scrape error: {e}")
    return {"lowest": None, "count": 0, "median": None}

# ── Routes ─────────────────────────────────────────────────────────
@app.route("/health")
def health():
    return jsonify({"status": "ok", "version": "2.0"})

@app.route("/search")
def search():
    query     = request.args.get("q","").strip()
    suppliers = request.args.get("suppliers","gsf,eurocarparts,halfords,autodoc").split(",")
    if not query:
        return jsonify({"error": "Missing q"}), 400

    scraper_map = {
        "gsf":          scrape_gsf,
        "eurocarparts": scrape_ecp,
        "halfords":     scrape_halfords,
        "autodoc":      scrape_autodoc,
    }

    all_results = []
    for sup in suppliers:
        sup = sup.strip()
        fn  = scraper_map.get(sup)
        if not fn:
            continue
        try:
            items = fn(query)
            codes = get_codes(sup)
            for item in items:
                orig = item["price"]
                disc, code = apply_best_discount(orig, codes)
                item["originalPrice"] = orig
                item["price"]         = disc
                item["discountCode"]  = code
                item["availableCodes"]= codes[:5]
                all_results.append(item)
        except Exception as e:
            log.warning(f"Supplier {sup} error: {e}")

    all_results.sort(key=lambda x: x["price"])
    return jsonify({"results": all_results, "query": query, "count": len(all_results)})

@app.route("/ebay-price")
def ebay_price():
    query = request.args.get("q","").strip()
    if not query:
        return jsonify({"error": "Missing q"}), 400
    return jsonify(scrape_ebay(query))

@app.route("/scan")
def scan():
    vehicle  = request.args.get("vehicle","").strip()
    category = request.args.get("category","").strip()
    parts    = request.args.get("parts","").strip()
    if not parts:
        return jsonify({"error": "Missing parts"}), 400

    part_list = [p.strip() for p in parts.split("|") if p.strip()]
    results   = []

    for part in part_list[:30]:
        query = f"{part} {vehicle}".strip()
        items = []

        for fn in [scrape_gsf, scrape_ecp, scrape_halfords, scrape_autodoc]:
            try:
                found = fn(query)
                sup_key = found[0]["supplierKey"] if found else ""
                codes   = get_codes(sup_key) if sup_key else []
                for item in found:
                    orig = item["price"]
                    disc, code = apply_best_discount(orig, codes)
                    item["originalPrice"] = orig
                    item["price"]         = disc
                    item["discountCode"]  = code
                items.extend(found)
            except Exception as e:
                log.warning(f"Scan item error: {e}")

        if not items:
            continue

        items.sort(key=lambda x: x["price"])
        best = items[0].copy()

        ebay = scrape_ebay(query)
        best["ebayLowestPrice"]  = ebay.get("lowest")
        best["ebayMedianPrice"]  = ebay.get("median")
        best["ebayListingCount"] = ebay.get("count", 0)
        best["fitment"]          = vehicle
        best["category"]         = category
        best["allSources"]       = items[:3]
        results.append(best)

        time.sleep(0.8)

    return jsonify({"results": results, "count": len(results)})

@app.route("/codes")
def codes():
    sup = request.args.get("supplier","")
    if sup:
        return jsonify({"supplier": sup, "codes": get_codes(sup)})
    return jsonify({s: get_codes(s) for s in ["gsf","eurocarparts","halfords","autodoc"]})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
