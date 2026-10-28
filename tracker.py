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


CFG = load_json("config.json")


def norm(s):
    return re.sub(r"[\s\-_/\"'.]", "", (s or "").upper())


def title_matches(title, model):
    t = norm(title)
    if not any(norm(tok) in t for tok in model["match_tokens"]):
        return False
    if any(norm(tok) in t for tok in model.get("exclude_tokens", [])):
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
CREATE TABLE IF NOT EXISTS reviews_seen(
  url TEXT PRIMARY KEY, source TEXT, title TEXT, first_seen TEXT, baseline INTEGER, relevant INTEGER);
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
                        rating = m.get("rating") or {}
                        reg = (o.get("price") or {}).get("amount")
                        camp = o.get("campaignPrice") or {}
                        camp_p = (camp.get("price") or {}).get("amount")
                        eff = (camp.get("salePriceEffectiveDate") or {})
                        ship = (o.get("shippingCost") or {}).get("amount")
                        out["offers"].append({
                            "merchant": m.get("name") or f"merchant {o.get('merchantId')}",
                            "rating": float(rating["average"]) if rating.get("average") else None,
                            "reviews": rating.get("count"),
                            "regular": float(reg) if reg else None,
                            "campaign": float(camp_p) if camp_p else None,
                            "camp_start": eff.get("start"), "camp_end": eff.get("end"),
                            "shipping": float(ship) if ship is not None else None,
                            "stock": o.get("stockStatus") or "UNKNOWN",
                            "title": o.get("name") or "",
                            "url": o.get("url"),
                        })
            if out["name"] is None and not out["offers"]:
                raise FetchError("estado PriceRunner sin producto ni ofertas")
            return out
    raise FetchError("no se encontro __DEHYDRATED_QUERY_STATE__ en PriceRunner")


def parse_prisjagt(html):
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, re.S)
    joined = ""
    for c in chunks:
        try:
            joined += json.loads('"' + c + '"')
        except Exception:
            pass
    i = joined.find('"historyItems":[')
    if i < 0:
        raise FetchError("historyItems no encontrado en Prisjagt")
    start = joined.index("[", i)
    depth, j = 0, start
    while j < len(joined):
        ch = joined[j]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                break
        j += 1
    items = json.loads(joined[start:j + 1])
    oldest = re.search(r'"oldestHistoryItem":"([^"]+)"', joined)
    return {"items": items, "tracked_since": oldest.group(1)[:10] if oldest else None}


# --------------------------------------------------------------------------- core
def merchant_trusted(off):
    name = off["merchant"] or ""
    if any(name.lower().startswith(x.lower()) for x in CFG["low_trust_merchants"]):
        return False
    if off["rating"] is not None and off["reviews"] is not None:
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

    # ---- historial Prisjagt
    pj_prices = []
    tracked_since = None
    for pid in model.get("prisjagt", []):
        url = f"https://prisjagt.dk/product.php?p={pid}"
        try:
            html = http_get(url, min_len=20000, expect="historyItems")
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

    res = {"model": model, "pr_ok": pr_ok, "offers": offers, "trusted": trusted, "best": best,
           "best_in_stock": best_is, "ref_min": ref_min, "median": median, "n_hist": len(pj_prices),
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
        res["grade"], res["reason"] = grade(model, best, ref_min, median)

    con.execute("""INSERT OR REPLACE INTO model_daily VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (RUN_DATE, key, best and best["total"], best and best["price"], best and best["merchant"],
                 best and best["stock"], best_is and best_is["total"], best_is and best_is["merchant"],
                 res["grade"], ref_min, median, len(valid))) if pr_ok else None
    ctx.model_results[key] = res
    return res


def grade(model, best, ref_min, median):
    p = best["total"] if best["total"] is not None else best["price"]
    tier = model["tier"]
    tgt, exc = model["target_price"], model["excellent_price"]
    new_low = ref_min is not None and best["price"] < ref_min * 0.99
    if tier == "not_recommended":
        if p <= exc:
            return "C", f"Precio muy bajo ({fmt_dkk(p)}), pero el modelo no esta recomendado para tu perfil."
        return "D", "Modelo no recomendado (alternativas mejores por precio/garantia/pruebas)."
    if p <= exc or (p <= tgt and new_low):
        g = "A"
        r = f"{fmt_dkk(p)} <= precio excelente {fmt_dkk(exc)}" if p <= exc else \
            f"{fmt_dkk(p)} bajo objetivo y nuevo minimo del periodo observado"
    elif p <= tgt:
        g, r = "B", f"{fmt_dkk(p)} <= precio objetivo {fmt_dkk(tgt)}"
    elif ref_min and best["price"] <= ref_min * 1.02 and tier in ("top", "alternative"):
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
    for key, res in ctx.model_results.items():
        m, b = res["model"], res["best"]
        if not res["pr_ok"]:
            continue
        # Oferta A/B (objetivo alcanzado / excelente)
        if b and res["grade"] in ("A", "B"):
            msg, link = build_deal_message(res, "precio objetivo/oferta")
            title = f"[{res['grade']}] {m['name'].split('(')[0].strip()} {fmt_dkk(b['total'])} @ {b['merchant']}"
            maybe_alert(ctx, f"deal|{key}", "DEAL", key, b["price"], title, msg,
                        priority=5 if res["grade"] == "A" else 4, click=link)
        else:
            # salio de la zona objetivo -> permitir alertar de nuevo si vuelve a entrar
            con.execute("DELETE FROM alert_state WHERE alert_key=?", (f"deal|{key}",))
        # Nuevo minimo verificable del periodo (no en el primer run; solo modelos recomendables)
        if (b and not ctx.first_run and m["tier"] != "not_recommended" and res["ref_min"]
                and b["price"] < res["ref_min"] * 0.99 and res["grade"] not in ("A", "B")):
            msg, link = build_deal_message(res, "nuevo minimo del periodo observado")
            title = f"Minimo ~3 meses: {m['name'].split('(')[0].strip()} {fmt_dkk(b['total'])}"
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
def discover(ctx, cand):
    con = ctx.con
    tracked = set()
    for m in cand["models"]:
        for p in m["pricerunner"]:
            mm = re.search(r"/pl/25-(\d+)/", p)
            if mm:
                tracked.add(mm.group(1))
    baseline = con.execute("SELECT COUNT(*) FROM discovered_products").fetchone()[0] == 0
    new_items = []
    for q in cand["discovery_queries"]:
        url = "https://www.pricerunner.dk/results?q=" + urllib.parse.quote(q)
        try:
            html = http_get(url, min_len=20000)
            ctx.ok += 1
            for pid, slug in re.findall(r"/pl/25-(\d+)/Skaerme/([^\"'\s<>?]+)", html):
                if pid in tracked:
                    continue
                s = slug.lower()
                if "oled" not in s:
                    continue
                if not re.search(r"(^|-)(32|31[.,]?5|315)(-|$|inch|zoll|\")|-32-|32-inch|31\.5", s) and not re.search(r"32[a-z]", s):
                    continue
                if re.search(r"(^|-)(27|34|39|42|45|48|49)(-|$)", s):
                    continue
                path = f"/pl/25-{pid}/Skaerme/{slug}"
                cur = con.execute("INSERT OR IGNORE INTO discovered_products VALUES(?,?,?,?,?)",
                                  (pid, slug.replace("-Sammenlign-Priser", "").replace("-", " "), path, RUN_DATE, int(baseline)))
                if cur.rowcount and not baseline:
                    new_items.append((pid, slug, path))
        except Exception as e:
            ctx.err("discovery", "pricerunner.dk", url, e)
        time.sleep(CFG["request_delay_seconds"])
    if new_items:
        msg = "✨ " + "\n".join(f"• {s.replace('-Sammenlign-Priser','').replace('-',' ')}" for _, s, _ in new_items[:3])
        maybe_alert(ctx, "new|" + ",".join(sorted(i[0] for i in new_items)), "NEW_MODEL", None, None,
                    f"{len(new_items)} monitor(es) 32\" OLED nuevo(s) en DK", msg, priority=3,
                    click="https://www.pricerunner.dk" + new_items[0][2], require_drop=False)
    ctx.notes.append(f"discovery: {len(new_items)} nuevos" + (" (linea base inicial)" if baseline else ""))


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
    if not CFG.get("windows_toast"):
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


# --------------------------------------------------------------------------- report
def write_report(ctx, new_reviews):
    order = {"A": 0, "B": 1, "C": 2, "D": 3, "-": 4, "?": 5}
    res_sorted = sorted(ctx.model_results.values(), key=lambda r: (order.get(r["grade"], 9),
                        {"top": 0, "alternative": 1, "watch": 2, "not_recommended": 3}[r["model"]["tier"]]))
    L = [f"# Informe de monitores - {RUN_TS.strftime('%Y-%m-%d %H:%M %Z')}", "",
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

    # Generar Dashboard Visual HTML interactivo
    try:
        import dashboard
        dash_paths = dashboard.generate_dashboard_html()
        log(f"Dashboard visual HTML generado con éxito ({len(dash_paths)} archivos)")
    except Exception as e:
        log(f"WARN al generar dashboard visual HTML: {e}")

    # Sincronización automática con Git / Vercel (si hay repositorio remoto configurado)
    try:
        r_rem = subprocess.run(["git", "remote"], cwd=BASE, capture_output=True, text=True)
        if r_rem.stdout.strip():
            subprocess.run(["git", "add", "index.html", "public/", "reports/", "data/offers_history.csv"], cwd=BASE, capture_output=True)
            subprocess.run(["git", "commit", "-m", f"Auto-update deals {RUN_DATE}"], cwd=BASE, capture_output=True)
            r_push = subprocess.run(["git", "push"], cwd=BASE, capture_output=True, text=True)
            if r_push.returncode == 0:
                log("Sincronización con Git / Vercel completada con éxito.")
            else:
                log(f"Git push pendiente o no configurado: {r_push.stderr.strip()[:150]}")
    except Exception as e:
        log(f"Aviso en sync_to_git: {e}")


# --------------------------------------------------------------------------- main
def status():
    con = db()
    print("BD:", DB_PATH)
    for t in ("runs", "offers", "prisjagt_history", "model_daily", "errors", "alerts_log", "alert_state",
              "discovered_products", "reviews_seen"):
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
    ap.add_argument("--force", action="store_true", help="ignorar fecha de fin")
    a = ap.parse_args()

    if a.status:
        return status()
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

    # Bloqueo para evitar ejecuciones simultaneas
    if os.path.exists(LOCK_PATH) and time.time() - os.path.getmtime(LOCK_PATH) < 3 * 3600:
        log("Otra ejecucion en curso (lock). Saliendo.")
        return
    open(LOCK_PATH, "w").write(str(os.getpid()))
    con = db()
    try:
        cur = con.execute("INSERT INTO runs(started_at,run_date,status) VALUES(?,?,?)",
                          (RUN_TS.isoformat(), RUN_DATE, "running"))
        run_id = cur.lastrowid
        con.commit()
        ctx = Ctx(con, run_id, a.dry_run)
        cand = load_json("candidates.json")
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
            best = sorted([r for r in ctx.model_results.values() if r["best"] and r["model"]["tier"] in ("top", "alternative")],
                          key=lambda r: r["best"]["total"] or 1e9)[:5]
            body = "Sistema activo. Mejores precios fiables hoy:\n" + "\n".join(
                f"- {r['model']['name'].split('(')[0].strip()}: {fmt_dkk(r['best']['total'])} ({r['best']['merchant']}) clase {r['grade']}"
                for r in best)
            maybe_alert(ctx, f"hb|{RUN_DATE}", "HEARTBEAT", None, None, "Resumen semanal monitores DK", body,
                        priority=1, require_drop=False)
        dispatch(ctx)
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
