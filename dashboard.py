# -*- coding: utf-8 -*-
"""
Generador del Cuadro de Mando Visual (HTML) para Deals de Hardware en Dinamarca.
Soporta arquitectura multi-categoria:
  - Monitores 4K 240Hz (OLED, QD-OLED, WOLED, Mini-LED)
  - Tarjetas Graficas (NVIDIA RTX 5080/5070Ti/4080S/4070TiS, AMD RX 7900 XTX)
  - Equipos Completos / Desktops (MM-Vision, SharkGaming, Proshop DUTZO, Komplett)
Produce un dashboard interactivo moderno con Tailwind CSS, desplegable directamente en Vercel.
"""
import os
import glob
import json
import sqlite3
import html
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
REPORTS = os.path.join(BASE, "reports")
CAT_DIR = os.path.join(BASE, "categories")
DB_PATH = os.path.join(DATA, "history.db")

def fmt_dkk(v):
    if v is None:
        return "n/d"
    return f"{v:,.0f}".replace(",", ".") + " DKK"

def load_all_candidates():
    if os.path.isdir(CAT_DIR):
        cat_files = glob.glob(os.path.join(CAT_DIR, "*.json"))
        if cat_files:
            combined = {
                "categories": {},
                "models": []
            }
            for cf in sorted(cat_files):
                try:
                    with open(cf, encoding="utf-8") as f:
                        cdata = json.load(f)
                        cid = cdata.get("category_id") or os.path.splitext(os.path.basename(cf))[0]
                        cname = cdata.get("category_name") or cid.capitalize()
                        cicon = cdata.get("category_icon", "cube")
                        cdesc = cdata.get("description", "")
                        combined["categories"][cid] = {
                            "id": cid,
                            "name": cname,
                            "icon": cicon,
                            "description": cdesc,
                            "count": len(cdata.get("models", []))
                        }
                        for m in cdata.get("models", []):
                            m.setdefault("category", cid)
                            m.setdefault("category_name", cname)
                            combined["models"].append(m)
                except Exception as e:
                    print(f"Error cargando {cf}: {e}")
            return combined

    # Fallback candidates.json
    cand_path = os.path.join(BASE, "candidates.json")
    if os.path.exists(cand_path):
        with open(cand_path, encoding="utf-8") as f:
            data = json.load(f)
            for m in data.get("models", []):
                m.setdefault("category", "monitors")
                m.setdefault("category_name", "Monitores 4K")
            return {
                "categories": {
                    "monitors": {"id": "monitors", "name": "Monitores 4K", "icon": "desktop-computer", "count": len(data.get("models", []))}
                },
                "models": data.get("models", [])
            }
    return {"categories": {}, "models": []}

def generate_dashboard_html(output_paths=None):
    con = sqlite3.connect(DB_PATH)
    
    # 1. Obtener ultimo run
    last_run = con.execute("SELECT run_id, started_at, run_date, status, ok_sources, failed_sources FROM runs ORDER BY run_id DESC LIMIT 1").fetchone()
    if not last_run:
        run_id, started_at, run_date, status, ok_src, fail_src = 1, datetime.now().isoformat(), datetime.now().strftime("%Y-%m-%d"), "ok", 0, 0
    else:
        run_id, started_at, run_date, status, ok_src, fail_src = last_run
    
    # 2. Cargar candidatos multi-categoria
    cand_info = load_all_candidates()
    models_cfg = {m["key"]: m for m in cand_info["models"]}
    
    # 3. Modelos del dia
    daily_rows = con.execute("""
        SELECT model_key, best_total, best_price, best_merchant, best_stock,
               best_in_stock_total, best_in_stock_merchant, grade, ref_min, ref_median, n_offers
        FROM model_daily WHERE check_date=?
    """, (run_date,)).fetchall()
    
    # 4. Todas las ofertas del dia
    offers_rows = con.execute("""
        SELECT model_key, merchant, offer_title, price, regular_price, campaign_price,
               campaign_end, shipping, total, stock, trusted, title_match, product_url, offer_url
        FROM offers WHERE check_date=? ORDER BY total ASC, price ASC
    """, (run_date,)).fetchall()
    
    offers_by_model = {}
    for r in offers_rows:
        mkey = r[0]
        offers_by_model.setdefault(mkey, []).append({
            "merchant": r[1],
            "title": r[2],
            "price": r[3],
            "regular": r[4],
            "campaign": r[5],
            "campaign_end": r[6],
            "shipping": r[7],
            "total": r[8],
            "stock": r[9],
            "trusted": bool(r[10]),
            "match": bool(r[11]),
            "product_url": r[12],
            "offer_url": r[13],
        })
        
    # 5. Descubrimientos y reviews recientes
    discovered = con.execute("SELECT product_id, name, path, first_seen FROM discovered_products ORDER BY first_seen DESC LIMIT 8").fetchall()
    reviews = con.execute("SELECT url, source, title, first_seen FROM reviews_seen WHERE relevant=1 ORDER BY first_seen DESC LIMIT 6").fetchall()
    
    con.close()
    
    # Construir lista combinada de modelos
    models_data = []
    order_grade = {"A": 0, "B": 1, "C": 2, "D": 3, "-": 4, "?": 5, "WATCH": 6}
    order_tier = {"top": 0, "alternative": 1, "watch": 2, "not_recommended": 3}
    
    daily_map = {r[0]: r for r in daily_rows}
    
    for m in cand_info["models"]:
        mkey = m["key"]
        row = daily_map.get(mkey)
        offers = offers_by_model.get(mkey, [])
        cat = m.get("category", "monitors")
        cat_name = m.get("category_name", "Monitores 4K")
        
        if row:
            grade_val = row[7] or "-"
            b_total = row[1]
            b_price = row[2]
            b_merch = row[3]
            b_stock = row[4]
            bis_total = row[5]
            bis_merch = row[6]
            r_min = row[8]
            r_med = row[9]
            n_off = row[10]
        else:
            grade_val = "WATCH"
            b_total = None
            b_price = None
            b_merch = "Seguimiento diario (09:00)"
            b_stock = "WATCH"
            bis_total = None
            bis_merch = None
            r_min = None
            r_med = None
            n_off = 0
            
        models_data.append({
            "key": mkey,
            "name": m.get("name", mkey),
            "category": cat,
            "category_name": cat_name,
            "tier": m.get("tier", "watch"),
            "grade": grade_val,
            "best_total": b_total,
            "best_price": b_price,
            "best_merchant": b_merch,
            "best_stock": b_stock,
            "best_in_stock_total": bis_total,
            "best_in_stock_merchant": bis_merch,
            "ref_min": r_min,
            "ref_median": r_med,
            "n_offers": n_off,
            "target_price": m.get("target_price"),
            "excellent_price": m.get("excellent_price"),
            "burnin": m.get("burnin_warranty", ""),
            "review_summary": m.get("review_summary", ""),
            "pros": m.get("pros", ""),
            "cons": m.get("cons", ""),
            "reviews": m.get("reviews", []),
            "pricerunner": m.get("pricerunner", []),
            "offers": offers
        })
        
    models_data.sort(key=lambda x: (order_grade.get(x["grade"], 9), order_tier.get(x["tier"], 9), x["best_total"] or 999999))
    
    # Estadisticas generales
    total_models = len(models_data)
    count_deals = sum(1 for m in models_data if m["grade"] in ("A", "B"))
    count_c = sum(1 for m in models_data if m["grade"] == "C")
    count_d = sum(1 for m in models_data if m["grade"] == "D")
    count_monitors = sum(1 for m in models_data if m["category"] == "monitors")
    count_gpus = sum(1 for m in models_data if m["category"] == "gpus")
    count_desktops = sum(1 for m in models_data if m["category"] == "desktops")
    total_active_offers = sum(len(m["offers"]) for m in models_data)
    best_deal = next((m for m in models_data if m["grade"] in ("A", "B")), None)
    
    # Formatear fecha
    try:
        dt_obj = datetime.fromisoformat(started_at)
        dt_str = dt_obj.strftime("%d de %B de %Y, %H:%M:%S")
    except Exception:
        dt_str = run_date
        
    js_models_json = json.dumps(models_data, ensure_ascii=False)
    
    html_content = f"""<!DOCTYPE html>
<html lang="es" class="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Hardware Deals Danmark | Monitores 4K, GPUs & Desktops</title>
  <script src="https://www.gstatic.com/antigravity/web/dev/tailwindcss.min.js"></script>
  <style>
    body {{
      background-color: #0b1120;
      color: #f1f5f9;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }}
    .custom-scrollbar::-webkit-scrollbar {{
      width: 6px;
      height: 6px;
    }}
    .custom-scrollbar::-webkit-scrollbar-track {{
      background: rgba(15, 23, 42, 0.6);
    }}
    .custom-scrollbar::-webkit-scrollbar-thumb {{
      background: rgba(51, 65, 85, 0.8);
      border-radius: 9999px;
    }}
    .cat-pill.active {{
      background: linear-gradient(135deg, #2563eb, #1d4ed8);
      color: #ffffff;
      box-shadow: 0 4px 12px rgba(37, 99, 235, 0.35);
      border-color: #3b82f6;
    }}
  </style>
</head>
<body class="min-h-screen p-3 sm:p-6 lg:p-8">

  <div class="max-w-7xl mx-auto space-y-6">

    <!-- ENCABEZADO -->
    <header class="bg-slate-900/90 border border-slate-800 rounded-2xl p-5 sm:p-6 shadow-xl backdrop-blur-md">
      <div class="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div class="flex items-center gap-2.5">
            <span class="inline-flex items-center justify-center w-9 h-9 rounded-xl bg-gradient-to-tr from-blue-600 to-indigo-500 text-white shadow-md text-lg font-bold">🇩🇰</span>
            <h1 class="text-xl sm:text-2xl font-black text-white tracking-tight">Centro de Control de Hardware & Deals DK</h1>
          </div>
          <p class="text-xs sm:text-sm text-slate-400 mt-1">
            Seguimiento de precios en tiempo real para <strong class="text-slate-200">Monitores 4K 240Hz</strong>, <strong class="text-purple-300">GPUs</strong> y <strong class="text-emerald-300">Desktops Prebuilt</strong> en Dinamarca.
          </p>
        </div>
        <div class="flex flex-wrap items-center gap-2 text-xs">
          <div class="px-3 py-1.5 rounded-xl bg-slate-800/80 border border-slate-700/80 text-slate-300 font-medium">
            Último escaneo: <span class="text-white font-semibold">{dt_str}</span>
          </div>
          <div class="px-2.5 py-1.5 rounded-xl bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 flex items-center gap-1.5 font-semibold">
            <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
            Automático 09:00 CPH
          </div>
          <button onclick="triggerUpdate()" id="btn-update" class="px-3.5 py-1.5 rounded-xl bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 active:scale-95 text-white font-bold text-xs shadow-md transition flex items-center gap-2 cursor-pointer border border-blue-400/30">
            <svg id="update-spinner" class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
            <span id="update-text">Actualizar Precios Ahora</span>
          </button>
        </div>
      </div>

      <!-- PESTAÑAS DE CATEGORIAS -->
      <div class="mt-5 pt-4 border-t border-slate-800/80 flex items-center gap-2 overflow-x-auto pb-1 custom-scrollbar">
        <button onclick="setCategory('all')" id="tab-all" class="cat-pill active px-4 py-2 rounded-xl text-xs font-bold border border-slate-700/80 bg-slate-800/80 text-slate-300 hover:text-white transition flex items-center gap-2 shrink-0">
          <span>✨ Todos los Deals</span>
          <span class="text-[11px] px-1.5 py-0.2 rounded-full bg-slate-900/60 font-semibold" id="count-badge-all">{total_models}</span>
        </button>
        <button onclick="setCategory('monitors')" id="tab-monitors" class="cat-pill px-4 py-2 rounded-xl text-xs font-bold border border-slate-700/80 bg-slate-800/80 text-slate-300 hover:text-white transition flex items-center gap-2 shrink-0">
          <span>🖥️ Monitores 4K 240Hz</span>
          <span class="text-[11px] px-1.5 py-0.2 rounded-full bg-slate-900/60 font-semibold">{count_monitors}</span>
        </button>
        <button onclick="setCategory('gpus')" id="tab-gpus" class="cat-pill px-4 py-2 rounded-xl text-xs font-bold border border-slate-700/80 bg-slate-800/80 text-slate-300 hover:text-white transition flex items-center gap-2 shrink-0">
          <span>🎮 Tarjetas Gráficas (GPUs)</span>
          <span class="text-[11px] px-1.5 py-0.2 rounded-full bg-purple-900/60 text-purple-200 font-semibold">{count_gpus}</span>
        </button>
        <button onclick="setCategory('desktops')" id="tab-desktops" class="cat-pill px-4 py-2 rounded-xl text-xs font-bold border border-slate-700/80 bg-slate-800/80 text-slate-300 hover:text-white transition flex items-center gap-2 shrink-0">
          <span>💻 Equipos Completos (Desktops)</span>
          <span class="text-[11px] px-1.5 py-0.2 rounded-full bg-emerald-900/60 text-emerald-200 font-semibold">{count_desktops}</span>
        </button>
      </div>
    </header>

    <!-- BANNER DE ESTADO DE ACTUALIZACION -->
    <div id="toast-banner" class="hidden transition-all duration-300 rounded-2xl p-4 text-xs font-semibold flex items-center justify-between border shadow-lg">
      <div class="flex items-center gap-2.5">
        <span id="toast-icon" class="text-base">🚀</span>
        <span id="toast-msg">Mensaje</span>
      </div>
      <button onclick="hideToast()" class="text-slate-400 hover:text-white px-2 py-1 cursor-pointer">✕</button>
    </div>

    <!-- METRICAS RESUMEN -->
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4">
      <div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-4 shadow-sm">
        <p class="text-xs font-medium text-slate-400 uppercase tracking-wider">Ofertas Destacadas (A/B)</p>
        <div class="mt-2 flex items-baseline gap-2">
          <span class="text-3xl font-black text-emerald-400" id="stat-deals">{count_deals}</span>
          <span class="text-xs text-slate-400">objetivo superado</span>
        </div>
        <p class="text-xs text-slate-400 mt-1">Monitoreadas con notificación push</p>
      </div>

      <div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-4 shadow-sm">
        <p class="text-xs font-medium text-slate-400 uppercase tracking-wider">Modelos en Seguimiento</p>
        <div class="mt-2 flex items-baseline gap-2">
          <span class="text-3xl font-black text-white" id="stat-total">{total_models}</span>
          <span class="text-xs text-slate-400">hardware verificado</span>
        </div>
        <p class="text-xs text-slate-400 mt-1">{count_monitors} monitores · {count_gpus} GPUs · {count_desktops} PCs</p>
      </div>

      <div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-4 shadow-sm">
        <p class="text-xs font-medium text-slate-400 uppercase tracking-wider">Tiendas Indexadas en DK</p>
        <div class="mt-2 flex items-baseline gap-2">
          <span class="text-3xl font-black text-blue-400">{total_active_offers}</span>
          <span class="text-xs text-slate-400">ofertas vivas</span>
        </div>
        <p class="text-xs text-slate-400 mt-1">Komplett, Proshop, MM-Vision, CS...</p>
      </div>

      <div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-4 shadow-sm">
        <p class="text-xs font-medium text-slate-400 uppercase tracking-wider">Mejor Ocasión Hoy</p>
        <div class="mt-2">
          <span class="text-sm font-bold text-emerald-300 truncate block">
            {html.escape(best_deal["name"].split("(")[0].strip()) if best_deal else "Sin ofertas B hoy"}
          </span>
          <div class="flex items-center gap-2 mt-1">
            <span class="text-xl font-extrabold text-white">{fmt_dkk(best_deal["best_total"]) if best_deal else "n/d"}</span>
            <span class="text-xs px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 font-semibold">{html.escape(best_deal["best_merchant"]) if best_deal else ""}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- BARRA DE FILTROS Y BUSQUEDA -->
    <div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-4 space-y-3">
      <div class="flex flex-col md:flex-row md:items-center justify-between gap-3">
        <!-- Filtro por Calificacion -->
        <div class="flex flex-wrap items-center gap-1.5" id="grade-filters">
          <span class="text-xs font-semibold text-slate-400 mr-1">Estado:</span>
          <button onclick="setFilter('all')" id="btn-all" class="filter-btn px-3 py-1.5 rounded-xl text-xs font-medium bg-blue-600 text-white shadow-sm transition">Todos</button>
          <button onclick="setFilter('deals')" id="btn-deals" class="filter-btn px-3 py-1.5 rounded-xl text-xs font-medium bg-slate-800 text-slate-300 hover:bg-slate-700 transition">Solo Ofertas A/B</button>
          <button onclick="setFilter('normal')" id="btn-normal" class="filter-btn px-3 py-1.5 rounded-xl text-xs font-medium bg-slate-800 text-slate-300 hover:bg-slate-700 transition">Precio Normal (C)</button>
          <button onclick="setFilter('watch')" id="btn-watch" class="filter-btn px-3 py-1.5 rounded-xl text-xs font-medium bg-slate-800 text-slate-300 hover:bg-slate-700 transition">En Radar / Próx. Escaneo</button>
        </div>

        <!-- Busqueda rapida -->
        <div class="relative w-full md:w-72">
          <input type="text" id="search-input" oninput="applyFilters()" placeholder="Buscar modelo, tienda, GPU, CPU..." class="w-full bg-slate-950/90 border border-slate-700/80 rounded-xl px-3.5 py-1.5 text-xs text-white placeholder-slate-400 focus:outline-none focus:border-blue-500 transition">
          <svg class="w-4 h-4 text-slate-400 absolute right-3 top-2 pointer-events-none" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
        </div>
      </div>

      <!-- Filtros secundarios -->
      <div class="flex flex-wrap items-center gap-2 pt-2 border-t border-slate-800 text-xs">
        <label class="inline-flex items-center gap-1.5 cursor-pointer text-slate-300 mr-2">
          <input type="checkbox" id="stock-only" onchange="applyFilters()" class="rounded bg-slate-900 border-slate-700 text-blue-600 focus:ring-0">
          <span>Solo en stock inmediato (In Stock)</span>
        </label>
        <span class="text-slate-600">|</span>
        <label class="inline-flex items-center gap-1.5 cursor-pointer text-slate-300">
          <input type="checkbox" id="top-tier-only" onchange="applyFilters()" class="rounded bg-slate-900 border-slate-700 text-blue-600 focus:ring-0">
          <span>Solo modelos recomendados (Tier TOP)</span>
        </label>
      </div>
    </div>

    <!-- LISTADO DE TARJETAS DE HARDWARE -->
    <div id="hardware-container" class="space-y-4">
      <!-- Inyectado dinamicamente por JS -->
    </div>

    <!-- SECCION INFERIOR: DESCUBRIMIENTOS & REVIEWS -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 pt-4">
      <!-- Nuevos modelos detectados -->
      <div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-5 shadow-sm">
        <div class="flex items-center justify-between mb-3">
          <h2 class="text-sm font-bold text-white flex items-center gap-2">
            <span class="text-amber-400">✨</span> Nuevos Productos Detectados en Dinamarca
          </h2>
          <span class="text-xs text-slate-400">{len(discovered)} registros</span>
        </div>
        <div class="space-y-2 max-h-56 overflow-y-auto custom-scrollbar pr-1">
          {"".join(f'''
          <div class="bg-slate-950/70 border border-slate-800 rounded-xl p-3 flex items-center justify-between text-xs">
            <div class="truncate mr-2">
              <p class="font-semibold text-slate-200 truncate">{html.escape(d[1])}</p>
              <p class="text-slate-400 text-[11px] mt-0.5">Detectado: {html.escape(d[3])}</p>
            </div>
            <a href="https://www.pricerunner.dk{html.escape(d[2])}" target="_blank" class="px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-blue-400 border border-slate-700 shrink-0 font-medium">Ver en PriceRunner</a>
          </div>
          ''' for d in discovered) if discovered else '<p class="text-xs text-slate-400 italic">No hay productos nuevos sin clasificar.</p>'}
        </div>
      </div>

      <!-- Reviews recientes -->
      <div class="bg-slate-900/80 border border-slate-800 rounded-2xl p-5 shadow-sm">
        <div class="flex items-center justify-between mb-3">
          <h2 class="text-sm font-bold text-white flex items-center gap-2">
            <span class="text-blue-400">📝</span> Feeds de Análisis Técnicos (RTINGS, TechPowerUp, TFTCentral...)
          </h2>
          <span class="text-xs text-slate-400">{len(reviews)} artículos</span>
        </div>
        <div class="space-y-2 max-h-56 overflow-y-auto custom-scrollbar pr-1">
          {"".join(f'''
          <div class="bg-slate-950/70 border border-slate-800 rounded-xl p-3 flex items-center justify-between text-xs">
            <div class="truncate mr-2">
              <p class="font-semibold text-slate-200 truncate">{html.escape(r[2])}</p>
              <p class="text-slate-400 text-[11px] mt-0.5">{html.escape(r[1])} · {html.escape(r[3])}</p>
            </div>
            <a href="{html.escape(r[0])}" target="_blank" class="px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-blue-400 border border-slate-700 shrink-0 font-medium">Leer análisis</a>
          </div>
          ''' for r in reviews) if reviews else '<p class="text-xs text-slate-400 italic">No se han registrado análisis nuevos recientemente.</p>'}
        </div>
      </div>
    </div>

    <!-- PIE DE PAGINA -->
    <footer class="pt-6 pb-8 border-t border-slate-800/80 text-center text-xs text-slate-500 space-y-1.5">
      <p>Hardware Deals DK · Automatización programada diariamente en Windows Task Scheduler · Sincronizado en Vercel</p>
      <p>Precios rastreados vía PriceRunner DK y Prisjagt DK incluyendo IVA (moms) y costes reales de entrega.</p>
    </footer>

  </div>

  <!-- SCRIPT CLIENTE PARA FILTROS DINAMICOS -->
  <script>
    const ITEMS = {js_models_json};
    let currentCategory = 'all';
    let currentFilter = 'all';
    let inStockOnly = false;
    let topTierOnly = false;

    function fmtDkk(v) {{
      if (v === null || v === undefined) return 'n/d';
      return Math.round(v).toLocaleString('es-ES') + ' DKK';
    }}

    function setCategory(cat) {{
      currentCategory = cat;
      document.querySelectorAll('.cat-pill').forEach(btn => btn.classList.remove('active'));
      const activeBtn = document.getElementById('tab-' + cat);
      if (activeBtn) activeBtn.classList.add('active');
      applyFilters();
    }}

    function setFilter(flt) {{
      currentFilter = flt;
      document.querySelectorAll('.filter-btn').forEach(btn => {{
        btn.classList.remove('bg-blue-600', 'text-white');
        btn.classList.add('bg-slate-800', 'text-slate-300');
      }});
      const b = document.getElementById('btn-' + flt);
      if (b) {{
        b.classList.remove('bg-slate-800', 'text-slate-300');
        b.classList.add('bg-blue-600', 'text-white');
      }}
      applyFilters();
    }}

    function getGradeBadge(grade) {{
      switch(grade) {{
        case 'A':
          return '<span class="px-2.5 py-1 rounded-xl text-xs font-black bg-emerald-500/20 text-emerald-300 border border-emerald-500/40">CLASE A · CHOLLO</span>';
        case 'B':
          return '<span class="px-2.5 py-1 rounded-xl text-xs font-black bg-teal-500/20 text-teal-300 border border-teal-500/40">CLASE B · OFERTA</span>';
        case 'C':
          return '<span class="px-2.5 py-1 rounded-xl text-xs font-semibold bg-slate-700/50 text-slate-300 border border-slate-600">CLASE C · PRECIO NORMAL</span>';
        case 'D':
          return '<span class="px-2.5 py-1 rounded-xl text-xs font-semibold bg-amber-500/10 text-amber-300 border border-amber-500/30">CLASE D · CARO</span>';
        case 'WATCH':
          return '<span class="px-2.5 py-1 rounded-xl text-xs font-semibold bg-indigo-500/20 text-indigo-300 border border-indigo-500/40">EN RADAR · PRÓX. ESCANEO</span>';
        default:
          return '<span class="px-2.5 py-1 rounded-xl text-xs font-semibold bg-slate-800 text-slate-400 border border-slate-700">SIN DATOS HOY</span>';
      }}
    }}

    function getCategoryBadge(cat) {{
      switch(cat) {{
        case 'gpus':
          return '<span class="text-xs px-2.5 py-0.5 rounded-xl bg-purple-500/10 text-purple-300 border border-purple-500/30 font-semibold flex items-center gap-1"><span>🎮</span> GPU Gaming & IA</span>';
        case 'desktops':
          return '<span class="text-xs px-2.5 py-0.5 rounded-xl bg-emerald-500/10 text-emerald-300 border border-emerald-500/30 font-semibold flex items-center gap-1"><span>💻</span> Desktop Prebuilt DK</span>';
        default:
          return '<span class="text-xs px-2.5 py-0.5 rounded-xl bg-blue-500/10 text-blue-300 border border-blue-500/30 font-semibold flex items-center gap-1"><span>🖥️</span> Monitor 4K 240Hz</span>';
      }}
    }}

    function toggleOffers(key) {{
      const el = document.getElementById('offers-' + key);
      const icon = document.getElementById('icon-' + key);
      if (!el) return;
      if (el.classList.contains('hidden')) {{
        el.classList.remove('hidden');
        if (icon) icon.style.transform = 'rotate(180deg)';
      }} else {{
        el.classList.add('hidden');
        if (icon) icon.style.transform = 'rotate(0deg)';
      }}
    }}

    function applyFilters() {{
      const q = document.getElementById('search-input').value.toLowerCase().trim();
      inStockOnly = document.getElementById('stock-only').checked;
      topTierOnly = document.getElementById('top-tier-only').checked;
      const container = document.getElementById('hardware-container');
      container.innerHTML = '';

      const filtered = ITEMS.filter(m => {{
        // Categoria
        if (currentCategory !== 'all' && m.category !== currentCategory) return false;

        // Estado / Calificacion
        if (currentFilter === 'deals' && !(m.grade === 'A' || m.grade === 'B')) return false;
        if (currentFilter === 'normal' && m.grade !== 'C') return false;
        if (currentFilter === 'watch' && m.grade !== 'WATCH') return false;

        // Filtro tier
        if (topTierOnly && m.tier !== 'top') return false;

        // Filtro stock
        if (inStockOnly && m.best_stock !== 'IN_STOCK') return false;

        // Busqueda texto
        if (q) {{
          const txt = (m.name + ' ' + (m.best_merchant || '') + ' ' + m.pros + ' ' + m.cons + ' ' + m.category_name).toLowerCase();
          if (!txt.includes(q)) return false;
        }}

        return true;
      }});

      // Actualizar contador dinamico
      const statDeals = filtered.filter(m => m.grade === 'A' || m.grade === 'B').length;
      document.getElementById('stat-deals').textContent = statDeals;
      document.getElementById('stat-total').textContent = filtered.length;

      if (filtered.length === 0) {{
        container.innerHTML = '<div class="bg-slate-900/50 border border-slate-800 rounded-2xl p-8 text-center text-slate-400 text-sm">No se encontraron productos con los filtros seleccionados en esta categoría.</div>';
        return;
      }}

      filtered.forEach(m => {{
        const isDeal = m.grade === 'A' || m.grade === 'B';
        const cardBorder = isDeal ? 'border-emerald-500/50 shadow-lg shadow-emerald-500/5 bg-slate-900/95' : 'border-slate-800 bg-slate-900/75';
        
        const bestOffer = m.offers && m.offers.length > 0 ? m.offers[0] : null;
        const buyUrl = bestOffer ? (bestOffer.offer_url || bestOffer.product_url) : (m.pricerunner.length ? 'https://www.pricerunner.dk' + m.pricerunner[0] : '#');
        const prUrl = m.pricerunner.length ? 'https://www.pricerunner.dk' + m.pricerunner[0] : '#';

        // Reviews HTML
        let reviewsHtml = '';
        if (m.reviews && m.reviews.length > 0) {{
          reviewsHtml = m.reviews.map(r => {{
            let site = 'Review';
            if (r.includes('rtings.com')) site = 'RTINGS';
            else if (r.includes('tftcentral.co.uk')) site = 'TFTCentral';
            else if (r.includes('techpowerup.com')) site = 'TechPowerUp';
            return `<a href="${{r}}" target="_blank" class="px-2.5 py-1 rounded-lg bg-slate-950 hover:bg-blue-600/20 text-blue-400 border border-slate-700/80 text-xs font-semibold inline-flex items-center gap-1 transition">
              <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/></svg>
              ${{site}}
            </a>`;
          }}).join(' ');
        }}

        // Tabla de ofertas
        let offersTable = '<div class="text-xs text-slate-400 p-3 italic">Las tiendas danesas se escanearán en la próxima ronda de las 09:00 o puedes consultar el comparador directo.</div>';
        if (m.offers && m.offers.length > 0) {{
          offersTable = `
            <div class="overflow-x-auto">
              <table class="w-full text-left text-xs">
                <thead class="bg-slate-950 text-slate-400 border-b border-slate-800 uppercase text-[10px] tracking-wider">
                  <tr>
                    <th class="py-2.5 px-3">Tienda</th>
                    <th class="py-2.5 px-3">Precio</th>
                    <th class="py-2.5 px-3">Envío</th>
                    <th class="py-2.5 px-3">Total</th>
                    <th class="py-2.5 px-3">Stock / Disponibilidad</th>
                    <th class="py-2.5 px-3 text-right">Acción</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-slate-800/80">
                  ${{m.offers.map(o => {{
                    const isBest = o === bestOffer;
                    const stockColor = o.stock === 'IN_STOCK' ? 'text-emerald-400' : 'text-amber-400';
                    const trustBadge = !o.trusted ? '<span class="text-[10px] px-1.5 py-0.2 rounded bg-rose-500/10 text-rose-400 border border-rose-500/20 ml-1">Marketplace / Dudoso</span>' : '';
                    return `
                      <tr class="${{isBest ? 'bg-blue-500/5 font-semibold text-white' : 'hover:bg-slate-800/40 text-slate-300'}}">
                        <td class="py-2 px-3">${{o.merchant}} ${{trustBadge}}</td>
                        <td class="py-2 px-3">${{fmtDkk(o.price)}}</td>
                        <td class="py-2 px-3 text-slate-400">${{o.shipping !== null ? fmtDkk(o.shipping) : 'n/d'}}</td>
                        <td class="py-2 px-3 font-bold text-white">${{fmtDkk(o.total || o.price)}}</td>
                        <td class="py-2 px-3 ${{stockColor}}">${{o.stock}}</td>
                        <td class="py-2 px-3 text-right">
                          <a href="${{o.offer_url || o.product_url}}" target="_blank" class="px-2.5 py-1 rounded bg-blue-600 hover:bg-blue-500 text-white font-medium inline-block text-[11px] transition">Ir a tienda</a>
                        </td>
                      </tr>
                    `;
                  }}).join('')}}
                </tbody>
              </table>
            </div>
          `;
        }}

        const card = document.createElement('div');
        card.className = `border rounded-2xl p-5 sm:p-6 transition shadow-sm ${{cardBorder}}`;
        card.innerHTML = `
          <div class="flex flex-col lg:flex-row lg:items-start justify-between gap-5">
            
            <!-- Columna Izquierda: Especificaciones y analisis -->
            <div class="space-y-3 flex-1">
              <div class="flex flex-wrap items-center gap-2">
                ${{getCategoryBadge(m.category)}}
                ${{getGradeBadge(m.grade)}}
                <span class="text-xs px-2.5 py-0.5 rounded-xl bg-slate-800 text-slate-300 font-medium">Tier: ${{m.tier.toUpperCase()}}</span>
                ${{m.best_stock === 'IN_STOCK' ? '<span class="text-xs px-2.5 py-0.5 rounded-xl bg-emerald-500/10 text-emerald-300 border border-emerald-500/20 font-medium">En stock</span>' : ''}}
              </div>

              <div>
                <h3 class="text-lg sm:text-xl font-bold text-white tracking-tight">${{m.name}}</h3>
                <p class="text-xs text-slate-400 mt-1">${{m.review_summary || ''}}</p>
              </div>

              <!-- Pros y Cons -->
              <div class="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs pt-1">
                <div class="bg-slate-950/70 border border-slate-800 rounded-xl p-3">
                  <span class="text-emerald-400 font-bold block mb-1">✓ Puntos fuertes:</span>
                  <p class="text-slate-300 leading-relaxed">${{m.pros}}</p>
                </div>
                <div class="bg-slate-950/70 border border-slate-800 rounded-xl p-3">
                  <span class="text-amber-400 font-bold block mb-1">⚠ Consideraciones clave:</span>
                  <p class="text-slate-300 leading-relaxed">${{m.cons}}</p>
                </div>
              </div>

              <!-- Garantia / Puertos / Reviews Links -->
              <div class="flex flex-wrap items-center justify-between gap-2 text-xs pt-1 text-slate-400">
                <div class="flex items-center gap-1.5">
                  <span class="text-blue-400">🛡️</span>
                  <span><strong>Garantía / Soporte DK:</strong> ${{m.burnin || 'Garantía oficial'}}</span>
                </div>
                <div class="flex items-center gap-1.5">
                  ${{reviewsHtml}}
                </div>
              </div>
            </div>

            <!-- Columna Derecha: Precios y Tiendas -->
            <div class="bg-slate-950/90 border border-slate-800 rounded-2xl p-4 sm:p-5 lg:w-80 shrink-0 flex flex-col justify-between space-y-4">
              <div>
                <div class="flex items-center justify-between">
                  <span class="text-xs font-semibold text-slate-400 uppercase tracking-wider">Mejor Precio Actual</span>
                  <span class="text-xs px-2 py-0.5 rounded font-semibold bg-blue-500/10 text-blue-300 border border-blue-500/20">${{m.best_merchant || 'n/d'}}</span>
                </div>
                
                <div class="mt-2 flex items-baseline gap-2">
                  <span class="text-3xl font-black text-white tracking-tight">${{fmtDkk(m.best_total || m.best_price)}}</span>
                </div>
                <p class="text-xs text-slate-500 mt-0.5">IVA danés (moms) + envío incluido</p>

                <div class="mt-4 space-y-1.5 text-xs border-t border-slate-800/80 pt-3">
                  <div class="flex justify-between">
                    <span class="text-slate-400">Precio Objetivo:</span>
                    <strong class="text-slate-200 font-semibold">${{fmtDkk(m.target_price)}}</strong>
                  </div>
                  <div class="flex justify-between">
                    <span class="text-slate-400">Mínimo ~3m observado:</span>
                    <strong class="text-slate-200 font-semibold">${{fmtDkk(m.ref_min)}}</strong>
                  </div>
                  <div class="flex justify-between">
                    <span class="text-slate-400">Mediana de mercado:</span>
                    <strong class="text-slate-200 font-semibold">${{fmtDkk(m.ref_median)}}</strong>
                  </div>
                </div>
              </div>

              <!-- Botones de accion -->
              <div class="space-y-2 pt-2">
                <a href="${{buyUrl}}" target="_blank" class="w-full py-2.5 px-4 rounded-xl font-bold text-xs text-center bg-blue-600 hover:bg-blue-500 text-white transition shadow-sm flex items-center justify-center gap-2 cursor-pointer">
                  <span>Ir a la tienda (${{m.best_merchant || 'Ver tienda'}})</span>
                  <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"/></svg>
                </a>
                
                <div class="flex items-center gap-2">
                  <a href="${{prUrl}}" target="_blank" class="flex-1 py-1.5 px-3 rounded-lg text-center bg-slate-900 hover:bg-slate-800 text-slate-300 text-xs font-medium border border-slate-700/80 transition">PriceRunner</a>
                  <button onclick="toggleOffers('${{m.key}}')" class="flex-1 py-1.5 px-3 rounded-lg text-center bg-slate-900 hover:bg-slate-800 text-slate-300 text-xs font-medium border border-slate-700/80 transition flex items-center justify-center gap-1 cursor-pointer">
                    <span>${{m.offers ? m.offers.length : 0}} ofertas</span>
                    <svg id="icon-${{m.key}}" class="w-3 h-3 transition-transform" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
                  </button>
                </div>
              </div>

            </div>

          </div>

          <!-- Desglose de ofertas -->
          <div id="offers-${{m.key}}" class="hidden mt-4 pt-4 border-t border-slate-800 bg-slate-950/60 rounded-xl overflow-hidden">
            <div class="px-4 py-2 bg-slate-950 border-b border-slate-800 flex items-center justify-between text-xs">
              <span class="font-bold text-slate-300">Detalle de ofertas en tiendas de Dinamarca</span>
              <span class="text-slate-500">${{m.offers ? m.offers.length : 0}} registradas</span>
            </div>
            ${{offersTable}}
          </div>
        `;

        container.appendChild(card);
      }});
    }}

    function showNotification(msg, type = 'info') {{
      const banner = document.getElementById('toast-banner');
      const icon = document.getElementById('toast-icon');
      const text = document.getElementById('toast-msg');
      if (!banner) return;
      banner.classList.remove('hidden', 'bg-blue-500/10', 'border-blue-500/40', 'text-blue-200', 'bg-emerald-500/10', 'border-emerald-500/40', 'text-emerald-200', 'bg-rose-500/10', 'border-rose-500/40', 'text-rose-200');
      if (type === 'success') {{
        banner.classList.add('bg-emerald-500/10', 'border-emerald-500/40', 'text-emerald-200');
        icon.textContent = '✅';
      }} else if (type === 'error') {{
        banner.classList.add('bg-rose-500/10', 'border-rose-500/40', 'text-rose-200');
        icon.textContent = '❌';
      }} else {{
        banner.classList.add('bg-blue-500/10', 'border-blue-500/40', 'text-blue-200');
        icon.textContent = '🚀';
      }}
      text.textContent = msg;
    }}

    function hideToast() {{
      const banner = document.getElementById('toast-banner');
      if (banner) banner.classList.add('hidden');
    }}

    async function triggerUpdate() {{
      const btn = document.getElementById('btn-update');
      const txt = document.getElementById('update-text');
      const spinner = document.getElementById('update-spinner');
      if (!btn || btn.disabled) return;

      btn.disabled = true;
      btn.classList.add('opacity-80');
      spinner.classList.add('animate-spin');
      txt.textContent = 'Enviando orden...';

      try {{
        const res = await fetch('https://ntfy.sh/mt-dk-trigger-xpfrsrpcjylx2prx', {{
          method: 'POST',
          body: JSON.stringify({{ action: 'refresh', timestamp: new Date().toISOString() }})
        }});
        if (res.ok) {{
          showNotification('¡Búsqueda lanzada en tiempo real! Tu equipo local está consultando PriceRunner y Prisjagt DK. El dashboard se actualizará automáticamente en ~60-90 segundos.', 'info');
          let secs = 90;
          const interval = setInterval(() => {{
            secs--;
            if (secs > 0) {{
              txt.textContent = `Actualizando (${{secs}}s)...`;
            }} else {{
              clearInterval(interval);
              txt.textContent = 'Recargando...';
              window.location.reload();
            }}
          }}, 1000);
        }} else {{
          throw new Error('HTTP ' + res.status);
        }}
      }} catch (err) {{
        console.error(err);
        txt.textContent = 'Error al enviar';
        showNotification('No se pudo enviar la orden remota. Si tu máquina está apagada, ejecuta el workflow en GitHub Actions.', 'error');
        setTimeout(() => {{
          btn.disabled = false;
          btn.classList.remove('opacity-80');
          spinner.classList.remove('animate-spin');
          txt.textContent = 'Actualizar Precios Ahora';
        }}, 4000);
      }}
    }}

    // Inicializar filtros al cargar
    applyFilters();
  </script>
</body>
</html>
"""

    if not output_paths:
        public_dir = os.path.join(BASE, "public")
        os.makedirs(public_dir, exist_ok=True)
        output_paths = [
            os.path.join(REPORTS, "dashboard.html"),
            os.path.join(REPORTS, "latest.html"),
            os.path.join(REPORTS, f"{run_date}.html"),
            os.path.join(public_dir, "index.html"),
            os.path.join(BASE, "index.html")
        ]
        
    for p in output_paths:
        with open(p, "w", encoding="utf-8") as f:
            f.write(html_content)
            
    return output_paths

if __name__ == "__main__":
    paths = generate_dashboard_html()
    print("Dashboard multi-categoria generado con exito en:", paths)
