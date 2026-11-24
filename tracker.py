# -*- coding: utf-8 -*-
"""
Monitor deal tracker (Dinamarca) - seguimiento diario de precios de monitores 32" 4K.

Fuentes automaticas (HTTP GET simple, sin eludir protecciones):
  * PriceRunner.dk  -> ofertas actuales de todas las tiendas danesas (precio, envio, stock, tienda)
  * Prisjagt.dk     -> historial de precios (ultimos ~3 meses) por tienda
  * Feeds de reviews -> TFTCentral, TechPowerUp, RTINGS (lista), YouTube (Monitors/Hardware Unboxed)

Almacenamiento: SQLite en data/history.db (append-only; claves unicas evitan duplicados).
Uso:
  python tracker.py               ejecucion normal (programada a las 09:00 Europe/Copenhagen)
  python tracker.py --dry-run     ejecuta todo sin enviar notificaciones
  python tracker.py --test-notify envia una notificacion de prueba
  python tracker.py --status      muestra estado de la base de datos y ultimas ejecuciones
"""
import argparse
import csv
import glob
import json
import os
import re
import sqlite3
import statistics
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, date

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
REPORTS = os.path.join(BASE, "reports")
LOGS = os.path.join(BASE, "logs")
for d in (DATA, REPORTS, LOGS):
    os.makedirs(d, exist_ok=True)

DB_PATH = os.path.join(DATA, "history.db")
LOCK_PATH = os.path.join(DATA, "run.lock")

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA,
           "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
           "Accept-Language": "da-DK,da;q=0.9,en;q=0.8"}

ORDERABLE = {"IN_STOCK", "BACKORDER", "PREORDER", "SPECIAL_ORDER", "COLLECT_IN_STORE"}

REVIEW_FEEDS = [
    ("TFTCentral", "rss", "https://tftcentral.co.uk/feed"),
    ("TechPowerUp", "rss", "https://www.techpowerup.com/rss/reviews"),
    ("YouTube UCI8iQa1hv7oV_Z8D35vVuSg", "atom",
     "https://www.youtube.com/feeds/videos.xml?channel_id=UCI8iQa1hv7oV_Z8D35vVuSg"),
    ("YouTube UCDKLZBNM9XZ7pHPZF9D8xDQ", "atom",
     "https://www.youtube.com/feeds/videos.xml?channel_id=UCDKLZBNM9XZ7pHPZF9D8xDQ"),
    ("RTINGS", "rtings", "https://www.rtings.com/monitor/reviews"),
]


# --------------------------------------------------------------------------- utils
def now_cph():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Copenhagen"))
    except Exception:
        # Sin base tzdata: el sistema esta en 'Romance Standard Time' (= Copenhague)
        return datetime.now().astimezone()


RUN_TS = now_cph()
RUN_DATE = RUN_TS.date().isoformat()
LOG_FILE = os.path.join(LOGS, f"run_{RUN_DATE}.log")


def log(msg):
    line = f"[{now_cph().strftime('%Y-%m-%d %H:%M:%S %Z')}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_json(name):
    with open(os.path.join(BASE, name), encoding="utf-8") as f:
        return json.load(f)


SANDBOX_TRACKER = os.path.join(
    os.path.dirname(BASE), "agent_sandbox", "workspace", "projects", "hardware-tracker"
)


def sync_categories_from_sandbox():
    sandbox_cat = os.path.join(SANDBOX_TRACKER, "categories")
    host_cat = os.path.join(BASE, "categories")
    if not os.path.isdir(sandbox_cat) or not os.path.isdir(host_cat):
        return
    import shutil
    for sf in glob.glob(os.path.join(sandbox_cat, "*.json")):
        hf = os.path.join(host_cat, os.path.basename(sf))
        if not os.path.exists(hf) or os.path.getmtime(sf) > os.path.getmtime(hf) + 1:
            shutil.copy2(sf, hf)
            log(f"Synced updated category file from OpenClaw sandbox: {os.path.basename(sf)}")


def load_candidates():
    sync_categories_from_sandbox()
    enabled = set(CFG.get("enabled_categories") or [])
    cat_dir = os.path.join(BASE, "categories")
    if os.path.isdir(cat_dir):
        cat_files = glob.glob(os.path.join(cat_dir, "*.json"))
        if cat_files:
            combined = {
                "models": [],
                "discovery_queries": [],
                "discovery_ignore_ids": []
            }
            for cf in sorted(cat_files):
                try:
                    with open(cf, encoding="utf-8") as f:
                        cdata = json.load(f)
                        cat_id = cdata.get("category_id") or os.path.splitext(os.path.basename(cf))[0]
                        if enabled and cat_id not in enabled:
                            continue
                        cat_name = cdata.get("category_name") or cat_id.capitalize()
                        for m in cdata.get("models", []):
                            m.setdefault("category", cat_id)
                            m.setdefault("category_name", cat_name)
                            combined["models"].append(m)
                        combined["discovery_queries"].extend(cdata.get("discovery_queries", []))
                        combined["discovery_ignore_ids"].extend(cdata.get("discovery_ignore_ids", []))
                except Exception as e:
                    log(f"Error cargando categoria {cf}: {e}")
            combined["discovery_queries"] = list(dict.fromkeys(combined["discovery_queries"]))
            combined["discovery_ignore_ids"] = list(dict.fromkeys(combined["discovery_ignore_ids"]))
            return combined
    data = load_json("candidates.json")
    for m in data.get("models", []):
        m.setdefault("category", "monitors")
        m.setdefault("category_name", "Monitores 4K")
    return data


CFG = load_json("config.json")


def norm(s):
    return re.sub(r"[\s\-_/\"'.]", "", (s or "").upper())


def title_matches(title, model):
    t_raw = (title or "").upper()
    t = norm(title)
    if not any(norm(tok) in t for tok in model.get("match_tokens", [])):
        return False
    for tok in model.get("exclude_tokens", []):
        tok_norm = norm(tok)
        if len(tok_norm) <= 2:
            # Token corto como PC: comprobar limites de palabra exactos para evitar falsos positivos con PCI / PCIe
            if re.search(r"\b" + re.escape(tok.upper()) + r"\b", t_raw):
                return False
        else:
            if tok_norm in t:
                return False
    return True


def fmt_dkk(v):
    if v is None:
        return "n/d"
    return f"{v:,.0f}".replace(",", ".") + " DKK"


class FetchError(Exception):
    pass


def http_get(url, min_len=5000, expect=None):
    """GET con reintentos. Nunca devuelve una pagina vacia/challenge como valida."""
    last = None
    for attempt in range(1, CFG["max_retries"] + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read().decode("utf-8", errors="ignore")
                if r.status != 200:
                    raise FetchError(f"HTTP {r.status} (len={len(body)})")
                if len(body) < min_len:
                    raise FetchError(f"respuesta demasiado corta len={len(body)} (posible challenge anti-bot)")
                if expect and expect not in body:
                    raise FetchError(f"marcador '{expect}' ausente (estructura cambiada o challenge)")
                return body
        except urllib.error.HTTPError as e:
            last = FetchError(f"HTTP {e.code}")
            if e.code in (403, 404):
                break
        except FetchError as e:
            last = e
        except Exception as e:
            last = FetchError(f"{type(e).__name__}: {e}")
        time.sleep(CFG["request_delay_seconds"] * attempt * 2)
    raise last


def resolve_redirect(url):
    """Resuelve el enlace de redireccion de PriceRunner a la URL final de la tienda."""
    try:
        req = urllib.request.Request(url, headers=HEADERS, method="GET")
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.geturl()
    except urllib.error.HTTPError as e:
        return e.geturl() or url
    except Exception:
        return url


# --------------------------------------------------------------------------- DB
SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(
  run_id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT,
  run_date TEXT, status TEXT, ok_sources INTEGER, failed_sources INTEGER, notes TEXT);
CREATE TABLE IF NOT EXISTS offers(
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, check_date TEXT, checked_at TEXT,
  model_key TEXT, source TEXT, merchant TEXT, offer_title TEXT, price REAL, regular_price REAL,
  campaign_price REAL, campaign_end TEXT, shipping REAL, total REAL, stock TEXT,
  trusted INTEGER, title_match INTEGER, product_url TEXT, offer_url TEXT,
  UNIQUE(check_date, model_key, source, merchant, offer_title, price, stock));
CREATE TABLE IF NOT EXISTS prisjagt_history(
  model_key TEXT, prisjagt_id INTEGER, shop TEXT, date TEXT, price REAL, offer_name TEXT,
  membership INTEGER, first_seen TEXT,
  UNIQUE(model_key, prisjagt_id, shop, date, price, offer_name));
CREATE TABLE IF NOT EXISTS model_daily(
  check_date TEXT, model_key TEXT, best_total REAL, best_price REAL, best_merchant TEXT,
  best_stock TEXT, best_in_stock_total REAL, best_in_stock_merchant TEXT, grade TEXT,
  ref_min REAL, ref_median REAL, n_offers INTEGER, PRIMARY KEY(check_date, model_key));
CREATE TABLE IF NOT EXISTS errors(
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, checked_at TEXT, model_key TEXT,
  source TEXT, url TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS alert_state(
  alert_key TEXT PRIMARY KEY, kind TEXT, model_key TEXT, last_price REAL, last_sent_at TEXT);
CREATE TABLE IF NOT EXISTS alerts_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, sent_at TEXT, alert_key TEXT, kind TEXT, model_key TEXT,
  price REAL, title TEXT, message TEXT, channels TEXT, dry_run INTEGER);
CREATE TABLE IF NOT EXISTS discovered_products(
  product_id TEXT PRIMARY KEY, name TEXT, path TEXT, first_seen TEXT, baseline INTEGER);
CREATE TABLE IF NOT EXISTS category_deals(
  check_date TEXT, category TEXT, product_id TEXT, name TEXT,
  price REAL, old_price REAL, drop_pct REAL, path TEXT, is_tracked INTEGER,
  PRIMARY KEY(check_date, category, product_id));
CREATE TABLE IF NOT EXISTS reviews_seen(
  url TEXT PRIMARY KEY, source TEXT, title TEXT, first_seen TEXT, baseline INTEGER, relevant INTEGER);
CREATE TABLE IF NOT EXISTS ai_insights(
  check_date TEXT PRIMARY KEY, model_used TEXT, created_at TEXT, payload_json TEXT);
"""


def db():
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    return con


# --------------------------------------------------------------------------- parsers
def parse_pricerunner(html):
    blocks = re.findall(r"<script[^>]*>(.*?)</script>", html, re.S)
    for s in blocks:
        s = s.strip()
        if s.startswith("{") and "__DEHYDRATED_QUERY_STATE__" in s:
            data = json.loads(s)
            out = {"name": None, "offers": [], "price_change": None}
            for q in data["__DEHYDRATED_QUERY_STATE__"].get("queries", []):
                key = q.get("queryKey", [None])[0]
                qd = (q.get("state") or {}).get("data") or {}
                if key == "product-detail-initial":
                    out["name"] = (qd.get("product") or {}).get("name")
                elif key == "product-price-level":
                    out["price_change"] = qd.get("priceChange")
                elif key == "product-detail-offers":
                    merchants = qd.get("merchants") or {}
                    for o in qd.get("offers") or []:
                        m = merchants.get(str(o.get("merchantId"))) or {}
                        icc = m.get("internationalCountryCode")
                        mname = m.get("name") or f"merchant {o.get('merchantId')}"
                        is_amazon = "amazon" in mname.lower()
                        if icc:
                            if icc == "DE" and is_amazon:
                                mname = "Amazon.de"
                            else:
                                continue
                        elif is_amazon:
                            mname = "Amazon.de"
                        rating = m.get("rating") or {}
                        reg = (o.get("price") or {}).get("amount")
                        camp = o.get("campaignPrice") or {}
                        camp_p = (camp.get("price") or {}).get("amount")
                        eff = (camp.get("salePriceEffectiveDate") or {})
                        ship = (o.get("shippingCost") or {}).get("amount")
                        ship_val = float(ship) if ship is not None else (0.0 if is_amazon else None)
                        out["offers"].append({
                            "merchant": mname,
                            "rating": float(rating["average"]) if rating.get("average") else None,
                            "reviews": rating.get("count"),
                            "regular": float(reg) if reg else None,
                            "campaign": float(camp_p) if camp_p else None,
                            "camp_start": eff.get("start"), "camp_end": eff.get("end"),
                            "shipping": ship_val,
                            "stock": o.get("stockStatus") or "UNKNOWN",
                            "title": o.get("name") or "",
                            "url": o.get("url"),
                        })
            if out["name"] is None and not out["offers"]:
                raise FetchError("estado PriceRunner sin producto ni ofertas")
            return out
    raise FetchError("no se encontro __DEHYDRATED_QUERY_STATE__ en PriceRunner")


_PJ_SHOP_MAP = {
    "Proshop": "Proshop.dk",
    "Komplett": "Komplett.dk",
    "Power": "POWER",
    "Happii": "happii.dk",
    "Goblue": "GoBlue.dk",
}


def _extract_json_array(text, marker):
    """Extrae un arreglo JSON balanceado a partir de un marcador literal."""
    i = text.find(marker)
    if i < 0:
        return None
    start = text.index("[", i)
    depth, j = 0, start
    while j < len(text):
        ch = text[j]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                break
        j += 1
    try:
        return json.loads(text[start:j + 1])
    except Exception:
        return None


def parse_prisjagt(html):
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, re.S)
    joined = ""
    for c in chunks:
        try:
            joined += json.loads('"' + c + '"')
        except Exception:
            pass
    items = _extract_json_array(joined, '"historyItems":[')
    offer_rows = _extract_json_array(joined, '"offerRows":[')
    if items is None and offer_rows is None:
        raise FetchError("ni historyItems ni offerRows encontrados en Prisjagt")
    items = items or []
    offer_rows = offer_rows or []

    stock_map = {
        "InStock": "IN_STOCK",
        "in_stock": "IN_STOCK",
        "Backorder": "BACKORDER",
        "Incoming": "BACKORDER",
        "incoming": "BACKORDER",
        "PreOrder": "BACKORDER",
        "OutOfStock": "OUT_OF_STOCK",
        "out_of_stock": "OUT_OF_STOCK",
    }
    live_offers = []
    for r in offer_rows:
        if not isinstance(r, dict):
            continue
        if r.get("isPaidMembership"):
            continue
        cond = r.get("condition")
        if cond not in (None, "New", "$undefined"):
            continue
        price_obj = r.get("price") or {}
        if not isinstance(price_obj, dict) or price_obj.get("amount") is None:
            continue
        try:
            price = float(price_obj["amount"])
        except Exception:
            continue
        orig_obj = r.get("originalPrice")
        regular = float(orig_obj["amount"]) if isinstance(orig_obj, dict) and orig_obj.get("amount") is not None else price

        ship = 0.0
        ship_obj = r.get("shipping")
        if isinstance(ship_obj, dict):
            nodes = ship_obj.get("nodes") or []
            if nodes and isinstance(nodes[0], dict):
                amt = (nodes[0].get("price") or {}).get("amount")
                if amt is not None:
                    try:
                        ship = float(amt)
                    except Exception:
                        pass

        raw_shop = (r.get("shop") or {}).get("name") or ""
        shop_name = _PJ_SHOP_MAP.get(raw_shop, raw_shop)

        rev = r.get("shopUserReviews") or {}
        rev_count = rev.get("numberOfReviewsInScore") or 0
        if isinstance(rev, dict) and rev_count >= 5 and rev.get("score") is not None:
            rating = float(rev["score"])
            reviews = int(rev_count)
        else:
            rating = None
            reviews = None

        offer_id = str(r.get("shopOfferId") or "")
        if shop_name == "Elgiganten" and offer_id.isdigit():
            offer_url = f"https://www.elgiganten.dk/product/{offer_id}"
        elif shop_name in ("Proshop", "Proshop.dk") and offer_id.isdigit():
            offer_url = f"https://www.proshop.dk/{offer_id}"
        elif shop_name in ("Komplett", "Komplett.dk") and offer_id.isdigit():
            offer_url = f"https://www.komplett.dk/product/{offer_id}"
        else:
            offer_url = r.get("clickoutUrl") or f"https://prisjagt.dk/product.php?p={r.get('productId') or ''}"

        live_offers.append({
            "merchant": shop_name,
            "rating": rating,
            "reviews": reviews,
            "regular": regular,
            "campaign": price if regular > price else None,
            "camp_start": None,
            "camp_end": None,
            "price": price,
            "shipping": ship,
            "stock": stock_map.get(r.get("stockStatus"), "UNKNOWN"),
            "title": r.get("name") or "",
            "url": offer_url,
        })

    oldest = re.search(r'"oldestHistoryItem":"([^"]+)"', joined)
    return {
        "items": items,
        "offers": live_offers,
        "tracked_since": oldest.group(1)[:10] if oldest else None,
    }


_DOMAIN_MERCHANT_MAP = (
    ("proshop.dk", "Proshop.dk"),
    ("mm-vision.dk", "MM-Vision"),
    ("elgiganten.dk", "Elgiganten"),
    ("power.dk", "POWER"),
    ("komplett.dk", "Komplett.dk"),
    ("fcomputer.dk", "Føniks Computer"),
    ("topdata.dk", "Topdata"),
    ("computersalg.dk", "CS MEGASTORE"),
    ("compumail.dk", "CompuMail"),
    ("avxperten.dk", "avXperten"),
)


def _merchant_from_url(url, default_merchant=None, brand=None):
    u_low = url.lower()
    for dom, merch in _DOMAIN_MERCHANT_MAP:
        if dom in u_low:
            return merch
    return default_merchant or brand or urllib.parse.urlparse(url).netloc.replace("www.", "")


def parse_direct_store(html, url, default_merchant=None):
    """Parsea Schema.org Product JSON-LD de tiendas danesas directas (Proshop, MM-Vision, Elgiganten, etc.)."""
    m = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL)
    for j in m:
        try:
            d = json.loads(j)
            if isinstance(d, dict) and d.get("@type") == "Product":
                name = d.get("name")
                brand = d.get("brand")
                if isinstance(brand, dict):
                    brand = brand.get("name")
                merchant = _merchant_from_url(url, default_merchant=default_merchant, brand=brand)
                offers = d.get("offers", {})
                if isinstance(offers, list) and offers:
                    offers = offers[0]
                price = float(offers.get("price", 0))
                avail = str(offers.get("availability", ""))
                stock = "IN_STOCK" if ("InStock" in avail or "InStoreOnly" in avail) else ("BACKORDER" if "PreOrder" in avail else "UNKNOWN")
                return {
                    "merchant": merchant,
                    "title": name,
                    "price": price,
                    "regular": price,
                    "campaign": None,
                    "camp_end": None,
                    "shipping": 0.0,
                    "stock": stock,
                    "url": offers.get("url") or url
                }
        except Exception:
            continue
    # Fallback para MM-Vision si no hay JSON-LD
    if "mm-vision.dk" in url:
        h1_m = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
        title = h1_m.group(1).strip() if h1_m else "MM-Vision PC"
        p_m = re.search(r'([0-9]{1,2}\.[0-9]{3})\s*(?:kr|,-)', html)
        if p_m:
            price_val = float(p_m.group(1).replace(".", "").replace(",", "."))
            return {
                "merchant": "MM-Vision",
                "title": title,
                "price": price_val,
                "regular": price_val,
                "campaign": None,
                "camp_end": None,
                "shipping": 0.0,
                "stock": "IN_STOCK",
                "url": url
            }
    # Fallback para Microdata / OpenGraph (Topdata, Føniks, etc.)
    p_meta = re.search(r'<meta\s+(?:itemprop=["\']price["\']|property=["\']product:price:amount["\'])\s+content=["\']([0-9.]+)["\']', html, re.I)
    if not p_meta:
        p_meta = re.search(r'<meta\s+content=["\']([0-9.]+)["\']\s+(?:itemprop=["\']price["\']|property=["\']product:price:amount["\'])', html, re.I)
    if p_meta:
        price_val = float(p_meta.group(1))
        title_m = re.search(r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']+)["\']', html, re.I)
        if not title_m:
            title_m = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
        title_val = title_m.group(1).strip() if title_m else "Store Product"
        merchant = _merchant_from_url(url, default_merchant=default_merchant)
        return {
            "merchant": merchant,
            "title": title_val,
            "price": price_val,
            "regular": price_val,
            "campaign": None,
            "camp_end": None,
            "shipping": 0.0,
            "stock": "IN_STOCK",
            "url": url
        }
    return None


# --------------------------------------------------------------------------- core
TRUSTED_MERCHANTS_ALLOWLIST = (
    "proshop", "komplett", "elgiganten", "power", "punkt1",
    "mm-vision", "mm vision", "føniks", "foeniks", "fcomputer", "topdata",
    "sharkgaming", "shark gaming", "maxgaming", "magasin",
    "compumail", "computersalg", "cs megastore", "avxperten",
    "happii", "merlin", "skousen", "whiteaway", "føtex", "foetex",
    "bilka", "salling", "imerco", "kitchenone", "rigtig kaffe",
    "house of barista", "moccatime", "coolshop", "goblue",
    "asus store", "samsung", "acer", "lenovo", "dell", "lg",
    "siemens", "sharkninja", "taurus gaming", "amazon.de", "amazon",
)


def merchant_trusted(off):
    name = (off.get("merchant") or "").strip()
    n_low = name.lower()
    if any(n_low.startswith(x.lower()) for x in CFG["low_trust_merchants"]):
        return False
    if any(re.search(r"\b" + re.escape(w) + r"\b", n_low) for w in TRUSTED_MERCHANTS_ALLOWLIST):
        return True
    if off.get("rating") is not None and off.get("reviews") is not None:
        if off["reviews"] < CFG["min_merchant_reviews"] or off["rating"] < CFG["min_merchant_rating"]:
            return False
    return True


def effective_price(off, now_utc):
    """Usa el precio de campana solo si la fecha de vigencia incluye hoy."""
    if off["campaign"] and off["camp_start"] and off["camp_end"]:
        try:
            s = datetime.fromisoformat(off["camp_start"].replace("Z", "+00:00"))
            e = datetime.fromisoformat(off["camp_end"].replace("Z", "+00:00"))
            if s <= now_utc <= e:
                return off["campaign"], off["camp_end"][:10]
        except Exception:
            pass
    return off["regular"], None


class Ctx:
    def __init__(self, con, run_id, dry_run):
        self.con, self.run_id, self.dry_run = con, run_id, dry_run
        self.ok = 0
        self.failed = 0
        self.alerts = []          # (key, kind, model_key, price, title, message, priority, click)
        self.model_results = {}
        self.notes = []
        self.ai_insights = None
        self.first_run = con.execute("SELECT COUNT(*) FROM runs WHERE status LIKE 'ok%' OR status LIKE 'partial%'").fetchone()[0] == 0

    def err(self, model_key, source, url, e):
        self.failed += 1
        log(f"ERROR {model_key} {source}: {e}")
        self.con.execute("INSERT INTO errors(run_id,checked_at,model_key,source,url,error) VALUES(?,?,?,?,?,?)",
                         (self.run_id, now_cph().isoformat(), model_key, source, url, str(e)[:500]))


def process_model(ctx, model):
    con = ctx.con
    key = model["key"]
    now_utc = datetime.now().astimezone().astimezone(tz=None)
    from datetime import timezone
    now_utc = datetime.now(timezone.utc)
    offers = []
    pr_ok = False
    for path in model.get("pricerunner", []):
        url = "https://www.pricerunner.dk" + path
        if "origin=" not in url:
            url += ("&origin=ALL" if "?" in url else "?origin=ALL")
        try:
            html = http_get(url, min_len=20000, expect="__DEHYDRATED_QUERY_STATE__")
            parsed = parse_pricerunner(html)
            pr_ok = True
            ctx.ok += 1
            for o in parsed["offers"]:
                price, camp_end = effective_price(o, now_utc)
                if price is None:
                    continue
                total = price + o["shipping"] if o["shipping"] is not None else None
                o2 = dict(o, price=price, camp_end_used=camp_end, total=total,
                          trusted=merchant_trusted(o), match=title_matches(o["title"], model),
                          product_url=url)
                offers.append(o2)
                con.execute("""INSERT OR IGNORE INTO offers(run_id,check_date,checked_at,model_key,source,merchant,
                    offer_title,price,regular_price,campaign_price,campaign_end,shipping,total,stock,trusted,title_match,
                    product_url,offer_url) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (ctx.run_id, RUN_DATE, now_cph().isoformat(), key, "pricerunner.dk", o["merchant"],
                             o["title"], price, o["regular"], o["campaign"], o["camp_end"], o["shipping"], total,
                             o["stock"], int(o2["trusted"]), int(o2["match"]), url, o["url"]))
        except Exception as e:
            ctx.err(key, "pricerunner.dk", url, e)
        time.sleep(CFG["request_delay_seconds"])

    # ---- Ofertas directas de tiendas danesas (Proshop, MM-Vision, etc.)
    for d_url in model.get("direct_urls", []):
        try:
            html = http_get(d_url, min_len=4000)
            d_offer = parse_direct_store(html, d_url)
            if d_offer and d_offer.get("price"):
                pr_ok = True
                ctx.ok += 1
                total = d_offer["price"] + (d_offer["shipping"] or 0)
                o2 = dict(d_offer, total=total, trusted=True, match=True, product_url=d_url)
                offers.append(o2)
                con.execute("""INSERT OR IGNORE INTO offers(run_id,check_date,checked_at,model_key,source,merchant,
                    offer_title,price,regular_price,campaign_price,campaign_end,shipping,total,stock,trusted,title_match,
                    product_url,offer_url) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (ctx.run_id, RUN_DATE, now_cph().isoformat(), key, "direct_store", d_offer["merchant"],
                             d_offer["title"], d_offer["price"], d_offer["regular"], None, None, d_offer["shipping"], total,
                             d_offer["stock"], 1, 1, d_url, d_url))
        except Exception as e:
            ctx.err(key, "direct_store", d_url, e)
        time.sleep(CFG.get("request_delay_seconds", 3))

    # ---- historial y ofertas en vivo Prisjagt
    pj_prices = []
    tracked_since = None
    for pid in model.get("prisjagt", []):
        url = f"https://prisjagt.dk/product.php?p={pid}"
        try:
            html = http_get(url, min_len=15000)
            ph = parse_prisjagt(html)
            ctx.ok += 1
            tracked_since = ph["tracked_since"] or tracked_since
            for it in ph["items"]:
                price = it.get("price")
                if price is None:
                    continue
                price = price / 100.0
                d = (it.get("date") or "")[:10]
                name = it.get("name") or ""
                shop = it.get("shopName") or ""
                con.execute("""INSERT OR IGNORE INTO prisjagt_history(model_key,prisjagt_id,shop,date,price,offer_name,
                    membership,first_seen) VALUES(?,?,?,?,?,?,?,?)""",
                            (key, pid, shop, d, price, name, int(bool(it.get("isMembershipPrice"))), RUN_DATE))
            for o in ph.get("offers", []):
                pr_ok = True
                ship = o["shipping"] if o["shipping"] is not None else 0.0
                total = o["price"] + ship
                o2 = dict(o, camp_end_used=None, total=total,
                          trusted=merchant_trusted(o), match=title_matches(o["title"], model),
                          product_url=url)
                offers.append(o2)
                con.execute("""INSERT OR IGNORE INTO offers(run_id,check_date,checked_at,model_key,source,merchant,
                    offer_title,price,regular_price,campaign_price,campaign_end,shipping,total,stock,trusted,title_match,
                    product_url,offer_url) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (ctx.run_id, RUN_DATE, now_cph().isoformat(), key, "prisjagt.dk", o["merchant"],
                             o["title"], o["price"], o["regular"], o["campaign"], o["camp_end"], ship, total,
                             o["stock"], int(o2["trusted"]), int(o2["match"]), url, o["url"]))
        except Exception as e:
            ctx.err(key, "prisjagt.dk", url, e)
        time.sleep(CFG["request_delay_seconds"])

    # Historial de referencia (Prisjagt, ultimos 92 dias, solo tiendas fiables y nombre coincidente, sin precios de socio)
    since = (RUN_TS.date() - timedelta(days=92)).isoformat()
    rows = con.execute("""SELECT shop,date,price,offer_name,membership FROM prisjagt_history
                          WHERE model_key=? AND date>=?""", (key, since)).fetchall()
    for shop, d, price, name, memb in rows:
        fake = {"merchant": shop, "rating": None, "reviews": None}
        if memb or not merchant_trusted(fake) or not title_matches(name, model):
            continue
        pj_prices.append(price)

    # Historial propio (dias anteriores, mejor precio fiable por dia)
    own = [r[0] for r in con.execute("""SELECT best_price FROM model_daily WHERE model_key=? AND check_date<?
                                         AND best_price IS NOT NULL""", (key, RUN_DATE)).fetchall()]
    prev = con.execute("""SELECT best_price, best_merchant, check_date FROM model_daily WHERE model_key=? AND
                          check_date<? ORDER BY check_date DESC LIMIT 1""", (key, RUN_DATE)).fetchone()

    median = statistics.median(pj_prices) if len(pj_prices) >= 3 else (statistics.median(own) if len(own) >= 3 else None)
    # descartar outliers (posibles errores de precio) del minimo de referencia
    clean = [p for p in pj_prices + own if not median or p >= median * CFG["anomaly_low_ratio"]]
    ref_min = min(clean) if clean else None

    valid = [o for o in offers if o["match"] and o["stock"] in ORDERABLE]
    trusted = [o for o in valid if o["trusted"]]
    sort_key = lambda o: (o["total"] if o["total"] is not None else o["price"] + 99)
    trusted.sort(key=sort_key)
    in_stock = [o for o in trusted if o["stock"] == "IN_STOCK"]
    best = trusted[0] if trusted else None
    best_is = in_stock[0] if in_stock else None

    # Historial de observaciones para madurez del modelo
    n_days_tracked = len(own) + (1 if best else 0)

    res = {"model": model, "pr_ok": pr_ok, "offers": offers, "trusted": trusted, "best": best,
           "best_in_stock": best_is, "ref_min": ref_min, "median": median, "n_hist": len(pj_prices),
           "n_days_tracked": n_days_tracked,
           "tracked_since": tracked_since, "prev": prev, "grade": None, "reason": "", "anomalies": []}

    # Anomalias: precios muy por debajo de la mediana (posible error o listado erroneo)
    if median:
        for o in valid:
            if o["price"] < median * CFG["anomaly_low_ratio"]:
                res["anomalies"].append(o)

    if not pr_ok:
        res["grade"] = "?"
        res["reason"] = "Sin datos hoy (error de consulta). No se interpreta como agotado ni como cambio de precio."
    elif not best:
        res["grade"] = "-"
        res["reason"] = "Sin ofertas fiables disponibles hoy en PriceRunner (consulta correcta)."
    else:
        res["grade"], res["reason"] = grade(model, best, ref_min, median, n_days_tracked=n_days_tracked)

    # Una pasada posterior sin ofertas fiables no debe borrar el dato bueno ya registrado hoy
    # (PriceRunner a veces devuelve la pagina incompleta). Se conserva el anterior y no se alerta.
    if pr_ok and not best:
        _prev_today = con.execute("SELECT best_price FROM model_daily WHERE check_date=? AND model_key=? "
                                  "AND best_price IS NOT NULL", (RUN_DATE, key)).fetchone()
        if _prev_today:
            res["grade"] = "?"
            res["reason"] = "Sin oferta fiable en esta pasada (posible fallo transitorio de PriceRunner); se conserva el dato anterior de hoy."
            res["pr_ok"] = False
            pr_ok = False
    con.execute("""INSERT OR REPLACE INTO model_daily VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (RUN_DATE, key, best and best["total"], best and best["price"], best and best["merchant"],
                 best and best["stock"], best_is and best_is["total"], best_is and best_is["merchant"],
                 res["grade"], ref_min, median, len(valid))) if pr_ok else None
    ctx.model_results[key] = res
    return res


def grade(model, best, ref_min, median, n_days_tracked=1):
    p = best["total"] if best["total"] is not None else best["price"]
    tier = model.get("tier", "alternative")
    tgt, exc = model.get("target_price"), model.get("excellent_price")
    new_low = ref_min is not None and best["price"] < ref_min * 0.99

    # Si el modelo es muy nuevo y aún no tiene historial comprobado (menos de 3 días) ni referencia de Prisjagt,
    # no asignamos A directa para evitar falsas alarmas con errores de scraping o targets iniciales no calibrados.
    has_mature_baseline = (median is not None) or (ref_min is not None) or (n_days_tracked >= 3)

    if tier == "not_recommended":
        if exc and p <= exc:
            return "C", f"Precio muy bajo ({fmt_dkk(p)}), pero el modelo no esta recomendado para tu perfil."
        return "D", "Modelo no recomendado (alternativas mejores por precio/garantia/pruebas)."
    if exc and p <= exc:
        if not has_mature_baseline:
            g = "B"
            r = f"{fmt_dkk(p)} bajo precio excelente estimado {fmt_dkk(exc)} (en calibración inicial: requiere 3 días de datos para clase A)"
        else:
            g = "A"
            r = f"{fmt_dkk(p)} <= precio excelente {fmt_dkk(exc)}"
    elif tgt and new_low and p <= tgt:
        g = "A"
        r = f"{fmt_dkk(p)} bajo objetivo y nuevo minimo del periodo observado"
    elif tgt and p <= tgt:
        g, r = "B", f"{fmt_dkk(p)} <= precio objetivo {fmt_dkk(tgt)}"
    elif ref_min and median and ref_min < median * 0.95 and best["price"] <= ref_min * 1.02 and tier in ("top", "alternative"):
        # Solo califica como B si el mínimo es un descuento real frente a la mediana histórica, no un precio plano estático
        g, r = "B", f"Precio en el minimo observado (~3 meses: {fmt_dkk(ref_min)}), aunque sobre el objetivo {fmt_dkk(tgt)}"
    elif median and best["price"] <= median * 1.03:
        g, r = "C", f"Precio habitual (mediana ~3 meses {fmt_dkk(median)}); objetivo {fmt_dkk(tgt)}"
    else:
        g, r = "D", f"Por encima del precio habitual (mediana {fmt_dkk(median)}) y del objetivo {fmt_dkk(tgt)}"
    if tier == "watch" and g == "A":
        g, r = "B", r + " (limitado a B: sin reviews independientes verificadas del modelo exacto)"
    return g, r


# --------------------------------------------------------------------------- alerts
def confidence(res):
    m = res["model"]
    b = res["best"]
    score = 0
    score += 1 if m["reviews"] else 0
    score += 1 if b and b["stock"] == "IN_STOCK" else 0
    score += 1 if res["n_hist"] >= 5 else 0
    score += 1 if "no verificado" not in m["burnin_warranty"].lower() else 0
    return ["Baja", "Baja", "Media", "Media-alta", "Alta"][score]


def unverified_list(res):
    m, b = res["model"], res["best"]
    u = []
    if not m["reviews"]:
        u.append("sin review independiente del modelo exacto")
    if "no verificad" in m["burnin_warranty"].lower():
        u.append("garantia burn-in en DK no verificada en documento oficial danes")
    if b and b.get("camp_end_used"):
        u.append(f"precio de campana reportado por el feed de la tienda (hasta {b['camp_end_used']}), confirmar en la tienda")
    if b and b["shipping"] is None:
        u.append("gastos de envio desconocidos")
    if res["n_hist"] < 5:
        u.append("historial de precios escaso")
    u.append("precio final y stock deben confirmarse en la tienda antes de comprar")
    return u


def build_deal_message(res, kind_label):
    m, b = res["model"], res["best"]
    link = resolve_redirect(b["url"]) if b.get("url") else res["offers"][0]["product_url"]
    ship_str = f"envío {fmt_dkk(b['shipping'])}" if b.get("shipping") is not None else "envío n/d"
    short_name = m["name"].split("(")[0].strip()
    
    # Mensaje conciso y limpio para el móvil (ntfy)
    lines = [
        f"💰 **{fmt_dkk(b['price'])}** ({ship_str}) | **{b['merchant']}**",
        f"📦 Stock: **{b['stock']}** · Clase **{res['grade']}** ({kind_label})",
        f"🎯 Objetivo: {fmt_dkk(m['target_price'])} · Mín ~3m: {fmt_dkk(res['ref_min'])}",
        f"🔗 Enlace directo: {link}"
    ]
    if CFG.get("vercel_url"):
        lines.append(f"📊 Dashboard web: {CFG['vercel_url']}")
    return "\n".join(lines), link


def maybe_alert(ctx, alert_key, kind, model_key, price, title, message, priority=4, click=None, require_drop=True):
    con = ctx.con
    st = con.execute("SELECT last_price,last_sent_at FROM alert_state WHERE alert_key=?", (alert_key,)).fetchone()
    if st:
        if not require_drop or price is None or st[0] is None:
            return False
        if price > st[0] * (1 - CFG["realert_min_drop_pct"] / 100.0):
            return False  # sin cambio relevante -> no repetir
    ctx.alerts.append((alert_key, kind, model_key, price, title, message, priority, click))
    return True


def evaluate_alerts(ctx):
    con = ctx.con
    ai_audits = {}
    if ctx.ai_insights:
        ai_audits = {a.get("model_key"): a for a in ctx.ai_insights.get("deal_audits", []) if a.get("model_key")}
    for key, res in ctx.model_results.items():
        m, b = res["model"], res["best"]
        if not res["pr_ok"]:
            continue
        cat = m.get("category", "monitors")
        cat_badge = {"gpus": "[GPU] ", "desktops": "[PC] ", "coffee": "[Café] ", "monitors": "[Monitor] "}.get(cat, f"[{cat}] ")
        short_n = m["name"].split("(")[0].strip()
        # Oferta A/B (objetivo alcanzado / excelente)
        bi = res.get("best_in_stock")
        gi_, ri_ = (grade(m, bi, res["ref_min"], res["median"], n_days_tracked=res.get("n_days_tracked", 1))
                    if bi else (None, ""))
        audit = ai_audits.get(key) or {}
        if audit.get("verdict") == "FALSE_POSITIVE" and ((bi and gi_ in ("A", "B")) or (b and res["grade"] in ("A", "B"))):
            log(f"AI SUPPRESS [{key}]: alerta DEAL omitida por falso positivo -> {audit.get('audit_note')}")
            ctx.notes.append(f"ai_suppress:{key}")
        elif bi and gi_ in ("A", "B"):
            # Alerta principal: solo ofertas con stock real
            r2 = dict(res, best=bi, grade=gi_, reason=ri_)
            msg, link = build_deal_message(r2, "precio objetivo/oferta, en stock")
            if audit.get("verdict") == "SPEC_WARNING" and audit.get("audit_note"):
                msg += f"\n⚠️ Auditoría IA: {audit['audit_note']}"
            title = f"{cat_badge}[{gi_}] {short_n} {fmt_dkk(bi['total'])} @ {bi['merchant']}"
            maybe_alert(ctx, f"deal|{key}", "DEAL", key, bi["price"], title, msg,
                        priority=5 if gi_ == "A" else 4, click=link)
        elif b and res["grade"] in ("A", "B"):
            # Oferta buena pero solo bajo pedido / sin stock inmediato: aviso de baja prioridad
            msg, link = build_deal_message(res, f"BAJO PEDIDO ({b['stock']}), sin stock inmediato")
            if audit.get("verdict") == "SPEC_WARNING" and audit.get("audit_note"):
                msg += f"\n⚠️ Auditoría IA: {audit['audit_note']}"
            title = f"{cat_badge}[{res['grade']}*] {short_n} {fmt_dkk(b['total'])} @ {b['merchant']} (bajo pedido)"
            maybe_alert(ctx, f"deal|{key}", "DEAL", key, b["price"], title, msg, priority=2, click=link)
        else:
            # salio de la zona objetivo -> permitir alertar de nuevo si vuelve a entrar
            con.execute("DELETE FROM alert_state WHERE alert_key=?", (f"deal|{key}",))
        # Nuevo minimo verificable del periodo (no en el primer run; solo modelos recomendables)
        if (b and not ctx.first_run and m["tier"] != "not_recommended" and res["ref_min"]
                and b["price"] < res["ref_min"] * 0.99 and res["grade"] not in ("A", "B")):
            msg, link = build_deal_message(res, "nuevo minimo del periodo observado")
            title = f"{cat_badge}Mínimo ~3m: {short_n} {fmt_dkk(b['total'])}"
            maybe_alert(ctx, f"low|{key}", "LOW", key, b["price"], title, msg, priority=3, click=link)
        # Anomalias de precio (posible error): solo informativas
        for o in res["anomalies"]:
            akey = f"anom|{key}|{o['merchant']}|{round(o['price'])}"
            msg = (f"Precio anomalo detectado (posible error de precio o listado incorrecto) para {m['name']}:\n"
                   f"{o['merchant']}: {fmt_dkk(o['price'])} (mediana ~3 meses {fmt_dkk(res['median'])}). "
                   f"Tienda fiable: {'si' if o['trusted'] else 'NO'}. Stock: {o['stock']}.\nTitulo: {o['title']}\n"
                   f"Comparador: {o['product_url']}\nNo se trata como oferta confirmada.")
            if o["trusted"]:
                maybe_alert(ctx, akey, "ANOMALY", key, o["price"], f"Precio anomalo: {m['name'].split('(')[0].strip()}",
                            msg, priority=3, click=o["product_url"], require_drop=False)


# --------------------------------------------------------------------------- discovery & reviews
CATEGORY_DEAL_FEEDS = [
    ("laptops", "/cl/27/Baerbar?sort=PRICE_DROP"),
    ("laptops", "/cl/27/Baerbar"),
    ("monitors", "/cl/25/Skaerme?attr_60394085=60394174&attr_60394205=60531614&sort=PRICE_DROP"),
    ("monitors", "/cl/25/Skaerme?attr_60394085=60394174&attr_60394205=60531614"),
    ("monitors", "/cl/25/Skaerme?attr_60394085=60394174"),
    ("gpus", "/cl/37/Grafikkort?attr_60534931=100018433,100018426,100018432,100019366"),
    ("gpus", "/cl/37/Grafikkort?attr_60535069=100019067,100019066"),
    ("desktops", "/cl/223/Stationaere-computere?attr_60480813=100018440,100018441,100018438"),
    ("desktops", "/cl/223/Stationaere-computere"),
    ("coffee", "/cl/82/Kaffemaskiner?attr_BRAND=1032,46,2524,156798"),
    ("coffee", "/cl/82/Kaffemaskiner"),
]


def _extract_pr_category_products(html):
    m = re.search(r'<script id="initial_payload"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except Exception:
        return []
    for q in data.get("__DEHYDRATED_QUERY_STATE__", {}).get("queries", []):
        qk = q.get("queryKey")
        if isinstance(qk, list) and qk and qk[0] == "cl-list-data-query":
            pages = (q.get("state") or {}).get("data", {}).get("pages") or []
            if pages and isinstance(pages[0], dict):
                return pages[0].get("products") or []
    return []


def discover(ctx, cand):
    con = ctx.con
    tracked_pids = set()
    for m in cand["models"]:
        for p in m.get("pricerunner", []):
            mm = re.search(r"/pl/\d+-(\d+)/", p)
            if mm:
                tracked_pids.add(mm.group(1))
    baseline = con.execute("SELECT COUNT(*) FROM discovered_products").fetchone()[0] == 0
    con.execute("DELETE FROM category_deals WHERE check_date=?", (RUN_DATE,))
    new_items = []
    seen_disc = set(tracked_pids)
    deals_count = 0

    enabled_cats = set(CFG.get("enabled_categories") or [])
    for cat, feed_path in CATEGORY_DEAL_FEEDS:
        if enabled_cats and cat not in enabled_cats:
            continue
        url = "https://www.pricerunner.dk" + feed_path
        try:
            html = http_get(url, min_len=15000)
            ctx.ok += 1
            products = _extract_pr_category_products(html)
            for p in products:
                pid = str(p.get("id") or "")
                name = (p.get("name") or "").strip()
                path = p.get("url") or ""
                if not pid or not name or not path:
                    continue

                if cat == "monitors" and pid not in seen_disc:
                    low = (name + " " + path).lower()
                    if ("oled" in low
                            and (re.search(r"(^|[\s\-\"])(32|31[.,]?5|315)($|[\s\-\"]|inch|zoll)", low) or re.search(r"32[a-z]", low))
                            and not re.search(r"(^|[\s\-])(27|34|39|42|45|48|49)($|[\s\-\"])", low)):
                        seen_disc.add(pid)
                        cur = con.execute("INSERT OR IGNORE INTO discovered_products VALUES(?,?,?,?,?)",
                                          (pid, name, path, RUN_DATE, int(baseline)))
                        if cur.rowcount and not baseline:
                            new_items.append((pid, name, path))

                lp_raw = (p.get("lowestPrice") or {}).get("amount")
                pd = p.get("priceDrop") or {}
                if lp_raw is None or not isinstance(pd, dict) or not pd:
                    continue
                try:
                    price = float(lp_raw)
                    old_price = float((pd.get("oldPrice") or {}).get("amount") or price)
                    drop_pct = float(pd.get("percent") or 0.0)
                except Exception:
                    continue
                if drop_pct < 5.0 or (old_price - price) < 150 or price < 800:
                    continue
                if cat == "monitors":
                    if re.search(r"\b(24|25)\b", name):
                        continue
                    if re.search(r'(?:signage|videovæg|videovaeg|viewboard|smart\s*board|interaktiv|skiltning|\b(?:43|55|65|75|85|98|110)(?:"|-inch|\s*tommer|\b))', name, re.I):
                        continue
                if cat == "laptops" and price < 4500:
                    continue
                is_trk = int(pid in tracked_pids)
                con.execute("""INSERT OR REPLACE INTO category_deals
                    (check_date, category, product_id, name, price, old_price, drop_pct, path, is_tracked)
                    VALUES(?,?,?,?,?,?,?,?,?)""",
                    (RUN_DATE, cat, pid, name, price, old_price, round(drop_pct, 1), path, is_trk))
                deals_count += 1
        except Exception as e:
            ctx.err("discovery", "pricerunner.dk", url, e)
        time.sleep(CFG["request_delay_seconds"])

    if new_items:
        msg = "✨ " + "\n".join(f"• {nm}" for _, nm, _ in new_items[:3])
        maybe_alert(ctx, "new|" + ",".join(sorted(i[0] for i in new_items)), "NEW_MODEL", None, None,
                    f"{len(new_items)} monitor(es) 32\" OLED nuevo(s) en DK", msg, priority=3,
                    click="https://www.pricerunner.dk" + new_items[0][2], require_drop=False)
    ctx.notes.append(f"discovery: {len(new_items)} nuevos, {deals_count} rebajas categoria" + (" (linea base inicial)" if baseline else ""))


def review_relevant(title, url, cand):
    t = norm(title + " " + url)
    toks = [tok for m in cand["models"] for tok in m["match_tokens"]]
    if any(norm(tok) in t for tok in toks):
        return True
    low = (title + " " + url).lower()
    return ("oled" in low and re.search(r"\b(32|31\.5)\b|32-inch|32\"|4k", low) is not None
            and "monitor" in low or re.search(r"(pg32|fo32|mo32|321u|322u|32gs|32gx|aw32|g8\b)", low) is not None)


def check_reviews(ctx, cand):
    con = ctx.con
    baseline = con.execute("SELECT COUNT(*) FROM reviews_seen").fetchone()[0] == 0
    new_rel = []
    for name, kind, url in REVIEW_FEEDS:
        try:
            body = http_get(url, min_len=1000)
            ctx.ok += 1
            items = []
            if kind == "rss":
                root = ET.fromstring(body.encode("utf-8"))
                for it in root.iter("item"):
                    items.append(((it.findtext("title") or "").strip(), (it.findtext("link") or "").strip()))
            elif kind == "atom":
                ns = {"a": "http://www.w3.org/2005/Atom"}
                root = ET.fromstring(body.encode("utf-8"))
                for e in root.findall("a:entry", ns):
                    l = e.find("a:link", ns)
                    items.append(((e.findtext("a:title", default="", namespaces=ns)).strip(),
                                  l.get("href") if l is not None else ""))
            else:
                for path in set(re.findall(r"/monitor/reviews/[a-z0-9-]+/[a-z0-9-]+", body)):
                    if "/best/" in path or "/tools/" in path:
                        continue
                    items.append((path.split("/")[-2] + " " + path.split("/")[-1].replace("-", " "),
                                  "https://www.rtings.com" + path))
            for title, link in items:
                if not link:
                    continue
                rel = review_relevant(title, link, cand)
                cur = con.execute("INSERT OR IGNORE INTO reviews_seen VALUES(?,?,?,?,?,?)",
                                  (link, name, title, RUN_DATE, int(baseline), int(rel)))
                if cur.rowcount and rel and not baseline:
                    new_rel.append((name, title, link))
        except Exception as e:
            ctx.err("reviews", name, url, e)
        time.sleep(2)
    if new_rel:
        msg = "📝 " + "\n".join(f"• [{n}] {t}" for n, t, _ in new_rel[:3])
        maybe_alert(ctx, "rev|" + RUN_DATE + "|" + str(len(new_rel)), "NEW_REVIEW", None, None,
                    f"{len(new_rel)} review(s) nueva(s) relevante(s)", msg, priority=3, click=new_rel[0][2],
                    require_drop=False)
    ctx.notes.append(f"reviews: {len(new_rel)} nuevas relevantes" + (" (linea base inicial)" if baseline else ""))
    return new_rel


# --------------------------------------------------------------------------- notify
def send_ntfy(title, message, priority=3, click=None, tags=None):
    n = CFG["ntfy"]
    if not n.get("enabled"):
        return False
    payload = {"topic": n["topic"], "title": title[:250], "message": message[:3900],
               "priority": int(priority), "markdown": True}
    if click:
        payload["click"] = click
    if tags:
        payload["tags"] = tags
    req = urllib.request.Request(n["server"], data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "User-Agent": "monitor-tracker/1.0"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read().decode("utf-8", "ignore")
        if r.status != 200:
            raise FetchError(f"ntfy HTTP {r.status}: {body[:200]}")
        return json.loads(body).get("id")


def send_toast(title, message):
    if not CFG.get("windows_toast") or os.name != "nt":
        return False
    ps = r"""
$ErrorActionPreference='Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$t=[System.Security.SecurityElement]::Escape($env:MT_TITLE); $b=[System.Security.SecurityElement]::Escape($env:MT_BODY)
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml("<toast><visual><binding template='ToastGeneric'><text>$t</text><text>$b</text></binding></visual></toast>")
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe').Show($toast)
"""
    env = dict(os.environ, MT_TITLE=title[:120], MT_BODY=message[:300])
    r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps], env=env,
                       capture_output=True, text=True, timeout=60, creationflags=0x08000000)
    if r.returncode != 0:
        raise FetchError("toast: " + (r.stderr or r.stdout)[:300])
    return True


def dispatch(ctx):
    con = ctx.con
    for (akey, kind, mkey, price, title, message, prio, click) in ctx.alerts:
        channels = []
        if not ctx.dry_run:
            try:
                if send_ntfy(title, message, prio, click, tags=["desktop_computer"]):
                    channels.append("ntfy")
            except Exception as e:
                ctx.err(mkey or kind, "ntfy", CFG["ntfy"]["server"], e)
            try:
                if send_toast(title, message.split("\n")[1] if "\n" in message else message):
                    channels.append("toast")
            except Exception as e:
                ctx.err(mkey or kind, "toast", "local", e)
        log(f"ALERTA [{kind}] {title} -> {channels or ('dry-run' if ctx.dry_run else 'SIN CANAL')}")
        con.execute("INSERT INTO alerts_log(sent_at,alert_key,kind,model_key,price,title,message,channels,dry_run) VALUES(?,?,?,?,?,?,?,?,?)",
                    (now_cph().isoformat(), akey, kind, mkey, price, title, message, ",".join(channels), int(ctx.dry_run)))
        if channels:  # solo se marca como enviada si salio por algun canal
            con.execute("INSERT OR REPLACE INTO alert_state VALUES(?,?,?,?,?)",
                        (akey, kind, mkey, price, now_cph().isoformat()))


def render_and_sync_dashboard():
    """Regenera el dashboard HTML y sincroniza reportes con Git/Vercel y OpenClaw sandbox."""
    try:
        import dashboard
        dash_paths = dashboard.generate_dashboard_html()
        log(f"Dashboard visual HTML generado con éxito ({len(dash_paths)} archivos)")
    except Exception as e:
        log(f"WARN al generar dashboard visual HTML: {e}")

    try:
        r_rem = subprocess.run(["git", "remote"], cwd=BASE, capture_output=True, text=True)
        if r_rem.stdout.strip():
            subprocess.run(["git", "add", "index.html", "public/", "reports/", "data/offers_history.csv", "candidates.json", "categories/"], cwd=BASE, capture_output=True)
            subprocess.run(["git", "commit", "-m", f"Auto-update deals {RUN_DATE}"], cwd=BASE, capture_output=True)
            r_push = subprocess.run(["git", "push"], cwd=BASE, capture_output=True, text=True)
            if r_push.returncode == 0:
                log("Sincronización con Git / Vercel completada con éxito.")
            else:
                log(f"Git push pendiente o no configurado: {r_push.stderr.strip()[:150]}")
    except Exception as e:
        log(f"Aviso en sync_to_git: {e}")

    try:
        if os.path.isdir(SANDBOX_TRACKER):
            import shutil
            for rel in ("data/history.db", "data/offers_history.csv", "reports/latest.md", "reports/dashboard.html", "index.html"):
                src_p = os.path.join(BASE, rel)
                dst_p = os.path.join(SANDBOX_TRACKER, rel)
                if os.path.exists(src_p):
                    os.makedirs(os.path.dirname(dst_p), exist_ok=True)
                    shutil.copy2(src_p, dst_p)
            log("Sincronización de base de datos y reportes con OpenClaw sandbox completada.")
    except Exception as e:
        log(f"Aviso en sync_to_sandbox: {e}")


# --------------------------------------------------------------------------- report
def write_report(ctx, new_reviews):
    order = {"A": 0, "B": 1, "C": 2, "D": 3, "-": 4, "?": 5}
    tier_order = {"top": 0, "alternative": 1, "watch": 2, "not_recommended": 3, "budget": 1}
    res_sorted = sorted(ctx.model_results.values(), key=lambda r: (order.get(r["grade"], 9),
                        tier_order.get(r["model"].get("tier", "alternative"), 1)))
    L = [f"# Informe de ofertas - {RUN_TS.strftime('%Y-%m-%d %H:%M %Z')}", "",
         f"Fuentes OK: {ctx.ok} | fallidas: {ctx.failed} | alertas nuevas: {len(ctx.alerts)}" +
         (" (dry-run, no enviadas)" if ctx.dry_run else ""), "",
         "| Clase | Modelo | Mejor oferta fiable (total) | Tienda / stock | Mejor en stock | Objetivo | Min ~3m | Mediana ~3m | Motivo |",
         "|---|---|---|---|---|---|---|---|---|"]
    for r in res_sorted:
        m, b, bi = r["model"], r["best"], r["best_in_stock"]
        L.append("| {g} | {n} | {p} | {t} | {bi} | {tg} | {mn} | {md} | {rs} |".format(
            g=r["grade"], n=m["name"], p=fmt_dkk(b["total"]) if b else "n/d",
            t=f"{b['merchant']} / {b['stock']}" if b else "-",
            bi=f"{fmt_dkk(bi['total'])} ({bi['merchant']})" if bi else "-",
            tg=fmt_dkk(m["target_price"]), mn=fmt_dkk(r["ref_min"]), md=fmt_dkk(r["median"]),
            rs=r["reason"].replace("|", "/")))
    L += ["", "Clases: A excelente, B buena, C normal, D no recomendada, - sin ofertas fiables, ? error de consulta.",
          "Min/mediana ~3m: historial Prisjagt (ultimos 92 dias, tiendas fiables, titulo coincidente, sin precios de socio) + registros propios. No es un minimo historico absoluto.", ""]
    L.append("## Detalle de ofertas (todas, incluidas no fiables)")
    for r in res_sorted:
        L.append(f"\n### {r['model']['name']}  ")
        L.append(f"Comparador: {', '.join('https://www.pricerunner.dk' + p for p in r['model']['pricerunner'])}  ")
        L.append(f"Garantia burn-in: {r['model']['burnin_warranty']}  ")
        if r["tracked_since"]:
            L.append(f"Prisjagt sigue el producto desde {r['tracked_since']}; historial accesible: ~3 meses.  ")
        for o in sorted(r["offers"], key=lambda o: o["price"])[:12]:
            flags = []
            if not o["trusted"]:
                flags.append("tienda poco fiable/marketplace")
            if not o["match"]:
                flags.append("titulo no coincide (otra variante)")
            if o.get("camp_end_used"):
                flags.append(f"precio campana hasta {o['camp_end_used']}")
            L.append(f"- {o['merchant']}: {fmt_dkk(o['price'])} + envio {fmt_dkk(o['shipping'])} [{o['stock']}]"
                     + (f" ({'; '.join(flags)})" if flags else "") + f" - {o['title'][:90]}")
    if new_reviews:
        L += ["", "## Reviews nuevas relevantes"] + [f"- [{n}] {t}: {l}" for n, t, l in new_reviews]
    errs = ctx.con.execute("SELECT model_key,source,error FROM errors WHERE run_id=?", (ctx.run_id,)).fetchall()
    if errs:
        L += ["", "## Errores de consulta (no interpretados como cambios de precio/stock)"] + \
             [f"- {a} / {b}: {c}" for a, b, c in errs]
    if ctx.alerts:
        L += ["", "## Alertas de esta ejecucion"] + [f"- [{a[1]}] {a[4]}" for a in ctx.alerts]
    txt = "\n".join(L) + "\n"
    for p in (os.path.join(REPORTS, f"{RUN_DATE}.md"), os.path.join(REPORTS, "latest.md")):
        with open(p, "w", encoding="utf-8") as f:
            f.write(txt)
    # CSV completo regenerado desde la BD (la BD es la fuente de verdad, append-only)
    with open(os.path.join(DATA, "offers_history.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        cols = ["check_date", "checked_at", "model_key", "source", "merchant", "offer_title", "price", "regular_price",
                "campaign_price", "campaign_end", "shipping", "total", "stock", "trusted", "title_match", "product_url", "offer_url"]
        w.writerow(cols)
        for row in ctx.con.execute(f"SELECT {','.join(cols)} FROM offers ORDER BY check_date, model_key, total"):
            w.writerow(row)

    render_and_sync_dashboard()


# --------------------------------------------------------------------------- main
def status():
    con = db()
    print("BD:", DB_PATH)
    for t in ("runs", "offers", "prisjagt_history", "model_daily", "errors", "alerts_log", "alert_state",
              "discovered_products", "category_deals", "reviews_seen", "ai_insights"):
        print(f"  {t}: {con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]} filas")
    print("Ultimas ejecuciones:")
    for r in con.execute("SELECT run_id,started_at,finished_at,status,ok_sources,failed_sources FROM runs ORDER BY run_id DESC LIMIT 7"):
        print("  ", r)
    print("Ultimas alertas:")
    for r in con.execute("SELECT sent_at,kind,title,channels,dry_run FROM alerts_log ORDER BY id DESC LIMIT 7"):
        print("  ", r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--test-notify", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--ai-only", action="store_true", help="ejecutar solo analisis Gemini 3.8 Flash sobre datos de hoy y regenerar dashboard")
    ap.add_argument("--force", action="store_true", help="ignorar fecha de fin")
    a = ap.parse_args()

    if a.status:
        return status()
    if a.ai_only:
        con = db()
        try:
            import ai_analyzer
            cand = load_candidates()
            log("Inicio analisis IA (--ai-only)...")
            ai_analyzer.run_ai_analysis(con, cand, RUN_DATE, log, config=CFG)
        finally:
            con.close()
        render_and_sync_dashboard()
        return
    if a.test_notify:
        title = "[B] MSI MPG 321URX: 5.690 DKK @ Komplett.dk"
        msg = ("💰 **5.690 DKK** (envío 0 DKK) | **Komplett.dk**\n"
               "📦 Stock: **IN_STOCK** · Clase **B** (Oferta objetivo)\n"
               "🎯 Objetivo: 5.500 DKK · Mín ~3m: 6.490 DKK\n"
               "🔗 Enlace directo: https://www.komplett.dk/product/1302665")
        nid = send_ntfy(title, msg, 4, "https://www.komplett.dk/product/1302665", tags=["desktop_computer"])
        print("ntfy OK (formato compacto enviado), id:", nid)
        try:
            send_toast(title, "5.690 DKK en Komplett.dk (En stock). Haz clic para abrir el dashboard.")
            print("toast OK")
        except Exception as e:
            print("toast ERROR:", e)
        return

    if RUN_TS.date() > date.fromisoformat(CFG["end_date"]) and not a.force:
        log(f"Fecha de fin {CFG['end_date']} superada: no se ejecuta. Deshabilitando tarea programada.")
        subprocess.run(["schtasks", "/Change", "/TN", "MonitorTrackerDK", "/DISABLE"], capture_output=True)
        return

    # Bloqueo para evitar ejecuciones simultaneas (si el proceso anterior murio o supero 1h, limpiar)
    if os.path.exists(LOCK_PATH):
        lock_age = time.time() - os.path.getmtime(LOCK_PATH)
        if lock_age < 3600:
            log("Otra ejecucion en curso (lock activo). Saliendo.")
            return
        else:
            log(f"Lock huerfano detectado ({int(lock_age)}s). Limpiando lock previo...")
            try:
                os.remove(LOCK_PATH)
            except Exception:
                pass
    open(LOCK_PATH, "w").write(str(os.getpid()))
    con = db()
    # Limpiar ejecuciones que quedaron en 'running' por cortes previos
    con.execute("UPDATE runs SET status='interrupted', finished_at=? WHERE status='running'", (now_cph().isoformat(),))
    con.commit()
    try:
        cur = con.execute("INSERT INTO runs(started_at,run_date,status) VALUES(?,?,?)",
                          (RUN_TS.isoformat(), RUN_DATE, "running"))
        run_id = cur.lastrowid
        con.commit()
        ctx = Ctx(con, run_id, a.dry_run)
        cand = load_candidates()
        log(f"Inicio run {run_id} ({'dry-run' if a.dry_run else 'normal'}), primer run: {ctx.first_run}")
        for m in cand["models"]:
            try:
                r = process_model(ctx, m)
                b = r["best"]
                log(f"{m['key']}: clase {r['grade']} | mejor {fmt_dkk(b['total']) if b else 'n/d'} "
                    f"{b['merchant'] if b else ''} | min3m {fmt_dkk(r['ref_min'])} | mediana {fmt_dkk(r['median'])}")
            except Exception as e:
                ctx.err(m["key"], "process", "", f"{e}\n{traceback.format_exc()[-800:]}")
            con.commit()
        try:
            discover(ctx, cand)
        except Exception as e:
            ctx.err("discovery", "process", "", e)
        try:
            import ai_analyzer
            ctx.ai_insights = ai_analyzer.run_ai_analysis(con, cand, RUN_DATE, log, config=CFG)
        except Exception as e:
            log(f"AI WARN en run_ai_analysis: {e}")
        new_rev = []
        try:
            new_rev = check_reviews(ctx, cand)
        except Exception as e:
            ctx.err("reviews", "process", "", e)
        evaluate_alerts(ctx)
        # Aviso de sistema degradado (max. 1 por dia)
        if ctx.failed > ctx.ok:
            maybe_alert(ctx, f"health|{RUN_DATE}", "HEALTH", None, None, "Monitor tracker: muchas consultas fallidas",
                        f"{ctx.failed} fuentes fallaron y {ctx.ok} funcionaron. Revisa logs/run_{RUN_DATE}.log.",
                        priority=2, require_drop=False)
        # Latido semanal (lunes) con resumen, prioridad minima
        if RUN_TS.weekday() == CFG.get("weekly_heartbeat_weekday", 0):
            best = sorted([r for r in ctx.model_results.values() if r["best"] and r["model"].get("tier") != "not_recommended"],
                          key=lambda r: r["best"]["total"] or 1e9)[:5]
            body = "Sistema activo. Mejores precios fiables hoy:\n" + "\n".join(
                f"- {r['model']['name'].split('(')[0].strip()}: {fmt_dkk(r['best']['total'])} ({r['best']['merchant']}) clase {r['grade']}"
                for r in best)
            maybe_alert(ctx, f"hb|{RUN_DATE}", "HEARTBEAT", None, None, "Resumen semanal ofertas DK", body,
                        priority=1, require_drop=False)
        dispatch(ctx)
        con.commit()
        write_report(ctx, new_rev)
        st = "ok" if ctx.failed == 0 else ("partial" if ctx.ok > 0 else "failed")
        con.execute("UPDATE runs SET finished_at=?,status=?,ok_sources=?,failed_sources=?,notes=? WHERE run_id=?",
                    (now_cph().isoformat(), st, ctx.ok, ctx.failed, "; ".join(ctx.notes), run_id))
        con.commit()
        log(f"Fin run {run_id}: {st}, ok={ctx.ok}, fallidas={ctx.failed}, alertas={len(ctx.alerts)}")
    finally:
        con.close()
        try:
            os.remove(LOCK_PATH)
        except Exception:
            pass


if __name__ == "__main__":
    main()
