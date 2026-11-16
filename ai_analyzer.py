# -*- coding: utf-8 -*-
"""
Modulo de analisis inteligente con Google AI Studio (Gemini 3.8 Flash).

En una sola llamada estructurada por ejecucion genera:
  1. deal_audits        -> Auditoria de titulo/specs/stock frente al catalogo (anti falsos positivos)
  2. top_radar_picks    -> Curador del Radar Abierto Black Friday (Top 6-8 chollos reales)
  3. executive_briefing -> Resumen diario y recomendacion #1 en stock por categoria
  4. desktop_valuations -> Tasacion de PCs prearmados frente al costo por piezas en Dinamarca
"""
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SANDBOX_ENV = os.path.join(os.path.dirname(BASE), "agent_sandbox", ".env")
MODEL_NAME = "gemini-3.8-flash"

AI_INSIGHTS_SCHEMA = {
    "type": "object",
    "properties": {
        "deal_audits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "model_key": {"type": "string"},
                    "verdict": {
                        "type": "string",
                        "enum": ["VERIFIED", "SPEC_WARNING", "FALSE_POSITIVE"],
                    },
                    "audit_note": {"type": "string"},
                },
                "required": ["model_key", "verdict", "audit_note"],
            },
        },
        "top_radar_picks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "string"},
                    "ai_badge": {"type": "string"},
                    "ai_reason": {"type": "string"},
                },
                "required": ["product_id", "ai_badge", "ai_reason"],
            },
        },
        "executive_briefing": {
            "type": "object",
            "properties": {
                "headline": {"type": "string"},
                "market_summary": {"type": "string"},
                "category_picks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "category": {
                                "type": "string",
                                "enum": ["monitors", "gpus", "desktops", "coffee"],
                            },
                            "best_buy_key": {"type": "string"},
                            "best_buy_name": {"type": "string"},
                            "price_dkk": {"type": "number"},
                            "merchant": {"type": "string"},
                            "verdict_title": {"type": "string"},
                            "rationale": {"type": "string"},
                        },
                        "required": [
                            "category",
                            "best_buy_key",
                            "best_buy_name",
                            "price_dkk",
                            "merchant",
                            "verdict_title",
                            "rationale",
                        ],
                    },
                },
            },
            "required": ["headline", "market_summary", "category_picks"],
        },
        "desktop_valuations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "model_key": {"type": "string"},
                    "diy_estimate_dkk": {"type": "number"},
                    "delta_dkk": {"type": "number"},
                    "verdict": {
                        "type": "string",
                        "enum": ["BARGAIN", "FAIR", "OVERPRICED"],
                    },
                    "breakdown": {"type": "string"},
                },
                "required": [
                    "model_key",
                    "diy_estimate_dkk",
                    "delta_dkk",
                    "verdict",
                    "breakdown",
                ],
            },
        },
    },
    "required": [
        "deal_audits",
        "top_radar_picks",
        "executive_briefing",
        "desktop_valuations",
    ],
}


def get_gemini_api_key(config=None):
    """Obtiene la API key desde variables de entorno o archivo .env configurado."""
    for env_var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        val = (os.environ.get(env_var) or "").strip()
        if val:
            return val

    cfg_path = (config or {}).get("gemini_env_path")
    candidates = [p for p in (cfg_path, os.path.join(BASE, ".env"), DEFAULT_SANDBOX_ENV) if p]
    for path in candidates:
        if not os.path.isfile(path):
            continue
        try:
            content = open(path, encoding="utf-8", errors="ignore").read()
            m = re.search(r"(?m)^(?:GEMINI_API_KEY|GOOGLE_API_KEY)=(.+)$", content)
            if m:
                key = m.group(1).strip().strip('"').strip("'")
                if key:
                    return key
        except Exception:
            continue
    return None


def _ensure_ai_table(con):
    con.execute(
        """CREATE TABLE IF NOT EXISTS ai_insights(
           check_date TEXT PRIMARY KEY,
           model_used TEXT,
           created_at TEXT,
           payload_json TEXT
        )"""
    )


def _gather_context(con, cand, run_date):
    row_date = con.execute(
        "SELECT MAX(check_date) FROM model_daily WHERE check_date <= ?", (run_date,)
    ).fetchone()
    eff_date = (row_date and row_date[0]) or run_date

    daily_rows = con.execute(
        """SELECT model_key, best_total, best_price, best_merchant, best_stock,
                  best_in_stock_total, best_in_stock_merchant, grade, ref_min, ref_median
           FROM model_daily WHERE check_date=?""",
        (eff_date,),
    ).fetchall()
    daily_map = {r[0]: r for r in daily_rows}

    top_offers = {}
    for mk, merch, title, tot, stock, trusted in con.execute(
        """SELECT model_key, merchant, offer_title, COALESCE(total, price), stock, trusted
           FROM offers
           WHERE check_date=? AND title_match=1 AND stock != 'OUT_OF_STOCK'
           ORDER BY CASE WHEN trusted=1 THEN 0 ELSE 1 END ASC,
                    COALESCE(total, price) ASC""",
        (eff_date,),
    ).fetchall():
        lst = top_offers.setdefault(mk, [])
        if len(lst) < 2:
            lst.append({
                "merchant": merch,
                "title": (title or "")[:120],
                "total": round(tot, 2) if tot is not None else None,
                "stock": stock,
                "trusted": bool(trusted),
            })

    tracked_summary = []
    gpu_ref_prices = []
    for m in cand.get("models", []):
        mk = m["key"]
        d = daily_map.get(mk)
        if not d:
            continue
        best_total = d[1]
        grade = d[7] or "-"
        cat = m.get("category", "monitors")
        if cat == "gpus" and best_total is not None:
            gpu_ref_prices.append({"key": mk, "name": m.get("name"), "best_dkk": round(best_total), "stock": d[4]})
        if best_total is None and grade == "-":
            continue
        tracked_summary.append({
            "key": mk,
            "category": cat,
            "tier": m.get("tier", "alternative"),
            "expected_name": m.get("name", mk),
            "key_specs": (m.get("pros") or "")[:140],
            "target_dkk": m.get("target_price"),
            "excellent_dkk": m.get("excellent_price"),
            "grade": grade,
            "best_dkk": round(best_total) if best_total is not None else None,
            "best_merchant": d[3],
            "best_stock": d[4],
            "in_stock_dkk": round(d[5]) if d[5] is not None else None,
            "in_stock_merchant": d[6],
            "ref_min_3m": round(d[8]) if d[8] is not None else None,
            "ref_median_3m": round(d[9]) if d[9] is not None else None,
            "top_offers": top_offers.get(mk, []),
        })

    cd_date_row = con.execute(
        "SELECT MAX(check_date) FROM category_deals WHERE check_date <= ?", (run_date,)
    ).fetchone()
    cd_date = (cd_date_row and cd_date_row[0]) or eff_date
    radar_deals = [
        {
            "product_id": r[0],
            "category": r[1],
            "name": r[2],
            "price_dkk": round(r[3]),
            "old_price_dkk": round(r[4]),
            "drop_pct": r[5],
            "is_tracked": bool(r[6]),
        }
        for r in con.execute(
            """SELECT product_id, category, name, price, old_price, drop_pct, is_tracked
               FROM category_deals WHERE check_date=?
               ORDER BY drop_pct DESC, (old_price - price) DESC""",
            (cd_date,),
        ).fetchall()
    ]

    return eff_date, tracked_summary, gpu_ref_prices, radar_deals


def _build_prompt(eff_date, tracked_summary, gpu_ref_prices, radar_deals):
    return f"""Eres un analista experto en hardware en Dinamarca (precios en DKK con 25% moms incluido; tambien analizamos Amazon.de con envio/moms ya convertidos a DKK).
Fecha de analisis: {eff_date}. Responde siempre en espanol claro y conciso.

Analiza los datos reales del mercado hoy y devuelve un JSON estricto cumpliendo el esquema:

1. deal_audits:
   - Audita TODOS los modelos de 'tracked_models' que tengan 'grade' en ['A', 'B'] o categoria 'desktops', mas cualquier otro modelo donde veas posible discrepancia entre 'expected_name'/'key_specs' y 'top_offers[].title' (ej. 16GB vs 32GB RAM, Demo/Outlet/Refurbished, GPU distinta como RX 9070 sin XT o Ti vs normal, accesorio/cable suelto).
   - verdict: 'VERIFIED' si coincide correctamente con specs y tienda fiable; 'SPEC_WARNING' si hay duda o detalle menor a vigilar (ej. bajo pedido, variante ligera, nota importante de garantia); 'FALSE_POSITIVE' SOLO si el titulo de la mejor oferta claramente NO corresponde al modelo/specs esperados o es reacondicionado/demo/accesorio.
   - audit_note: 1 frase breve (max 140 caracteres).

2. top_radar_picks:
   - Selecciona entre 6 y 8 chollos reales de 'radar_deals' (equilibrando entre las categorias disponibles) filtrando ruido, modelos viejos o rebajas engañosas sobre MSRP inflado.
   - ai_badge: etiqueta corta (max 24 caracteres, ej. 'Chollo Real OLED', 'Top Calidad/Precio').
   - ai_reason: 1 frase breve (max 130 caracteres) explicando por que merece la pena hoy.

3. executive_briefing:
   - headline: titular ejecutivo breve del dia (max 90 caracteres).
   - market_summary: resumen practico en 2 frases (max 260 caracteres) sobre donde estan hoy las mejores oportunidades en Dinamarca y Amazon.de.
   - category_picks: exactamente 4 entradas, una para cada categoria ('monitors', 'gpus', 'desktops', 'coffee'). Para cada categoria elige la mejor compra REALMENTE EN STOCK ('IN_STOCK') hoy de 'tracked_models' comparandola brevemente en 'rationale' (max 190 caracteres) contra la alternativa mas cercana. Si un modelo no tiene 'IN_STOCK', elige el mejor disponible de esa categoria aclarandolo.

4. desktop_valuations:
   - Evalua TODOS los modelos con category == 'desktops' que tengan 'best_dkk' no nulo.
   - Estima 'diy_estimate_dkk' usando los precios actuales en Dinamarca de 'gpu_ref_prices' para la GPU correspondiente mas precios realistas de mercado danes (con 25% moms) para CPU, placa base, 16GB/32GB DDR5, NVMe SSD, PSU, refrigeracion y caja.
   - Calcula 'delta_dkk' = round(best_dkk - diy_estimate_dkk). (Negativo = mas barato que por piezas; positivo = sobreprecio de montaje/tienda).
   - verdict: 'BARGAIN' si delta_dkk <= 500, 'FAIR' si 500 < delta_dkk <= 1800, 'OVERPRICED' si delta_dkk > 1800.
   - breakdown: desglose ultra breve de componentes estimados (max 145 caracteres).

=== gpu_ref_prices ===
{json.dumps(gpu_ref_prices, ensure_ascii=False)}

=== tracked_models ===
{json.dumps(tracked_summary, ensure_ascii=False)}

=== radar_deals ===
{json.dumps(radar_deals, ensure_ascii=False)}
"""


def _call_gemini_sdk(api_key, prompt):
    from google import genai

    client = genai.Client(api_key=api_key)
    interaction = client.interactions.create(
        model=MODEL_NAME,
        input=prompt,
        response_format={
            "type": "text",
            "mime_type": "application/json",
            "schema": AI_INSIGHTS_SCHEMA,
        },
    )
    text = interaction.output_text
    if not text:
        raise RuntimeError(f"Respuesta vacia de Gemini SDK (status={getattr(interaction, 'status', None)})")
    return json.loads(text)


def _call_gemini_rest(api_key, prompt):
    url = f"https://generativelanguage.googleapis.com/v1beta/interactions?key={api_key}"
    payload = {
        "model": MODEL_NAME,
        "input": prompt,
        "response_format": {
            "type": "text",
            "mime_type": "application/json",
            "schema": AI_INSIGHTS_SCHEMA,
        },
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Api-Revision": "2026-05-20",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    for step in reversed(data.get("steps") or []):
        if step.get("type") == "model_output":
            for part in step.get("content") or []:
                if part.get("type") == "text" and part.get("text"):
                    return json.loads(part["text"])
    raise RuntimeError("No se encontro texto JSON en la respuesta REST de Gemini")


def run_ai_analysis(con, cand, run_date, log_fn=print, config=None):
    """Ejecuta el analisis IA con Gemini 3.8 Flash y guarda el resultado en SQLite."""
    _ensure_ai_table(con)
    api_key = get_gemini_api_key(config=config)
    if not api_key:
        log_fn("AI WARN: GEMINI_API_KEY no encontrada; se omite analisis IA.")
        return None

    eff_date, tracked_summary, gpu_ref_prices, radar_deals = _gather_context(con, cand, run_date)
    if not tracked_summary:
        log_fn("AI WARN: Sin modelos activos hoy en BD para analizar.")
        return None

    prompt = _build_prompt(eff_date, tracked_summary, gpu_ref_prices, radar_deals)
    result = None
    try:
        result = _call_gemini_sdk(api_key, prompt)
    except Exception as sdk_err:
        log_fn(f"AI INFO: SDK fallback a REST ({str(sdk_err)[:140]})")
        try:
            result = _call_gemini_rest(api_key, prompt)
        except Exception as rest_err:
            log_fn(f"AI ERROR: fallo al consultar {MODEL_NAME}: {rest_err}")
            return None

    now_iso = datetime.now().astimezone().isoformat()
    con.execute(
        "INSERT OR REPLACE INTO ai_insights(check_date, model_used, created_at, payload_json) VALUES(?,?,?,?)",
        (eff_date, MODEL_NAME, now_iso, json.dumps(result, ensure_ascii=False)),
    )
    con.commit()
    log_fn(
        f"AI OK ({MODEL_NAME}, fecha={eff_date}): "
        f"{len(result.get('deal_audits', []))} auditorias | "
        f"{len(result.get('top_radar_picks', []))} radar picks | "
        f"{len(result.get('desktop_valuations', []))} tasaciones PC"
    )
    return result
