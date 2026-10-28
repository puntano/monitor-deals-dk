# -*- coding: utf-8 -*-
"""
Generador del Cuadro de Mando Visual (HTML) para el Monitor Tracker DK.
Produce un archivo HTML interactivo y autocontenido con Tailwind CSS,
diseñado para verse tanto en navegador local como en el panel de Antigravity.
"""
import os
import json
import sqlite3
import html
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
REPORTS = os.path.join(BASE, "reports")
DB_PATH = os.path.join(DATA, "history.db")

def fmt_dkk(v):
    if v is None:
        return "n/d"
    return f"{v:,.0f}".replace(",", ".") + " DKK"

def generate_dashboard_html(output_paths=None):
    con = sqlite3.connect(DB_PATH)
    
    # 1. Obtener ultimo run
    last_run = con.execute("SELECT run_id, started_at, run_date, status, ok_sources, failed_sources FROM runs ORDER BY run_id DESC LIMIT 1").fetchone()
    if not last_run:
        return None
    run_id, started_at, run_date, status, ok_src, fail_src = last_run
    
    # 2. Cargar candidatos y config
    with open(os.path.join(BASE, "candidates.json"), encoding="utf-8") as f:
        cand_data = json.load(f)
    models_cfg = {m["key"]: m for m in cand_data["models"]}
    
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
    order_grade = {"A": 0, "B": 1, "C": 2, "D": 3, "-": 4, "?": 5}
    order_tier = {"top": 0, "alternative": 1, "watch": 2, "not_recommended": 3}
    
    for row in daily_rows:
        mkey = row[0]
        cfg = models_cfg.get(mkey, {})
        offers = offers_by_model.get(mkey, [])
        models_data.append({
            "key": mkey,
            "name": cfg.get("name", mkey),
            "tier": cfg.get("tier", "watch"),
            "grade": row[7] or "-",
            "best_total": row[1],
            "best_price": row[2],
            "best_merchant": row[3],
            "best_stock": row[4],
            "best_in_stock_total": row[5],
            "best_in_stock_merchant": row[6],
            "ref_min": row[8],
            "ref_median": row[9],
            "n_offers": row[10],
            "target_price": cfg.get("target_price"),
            "excellent_price": cfg.get("excellent_price"),
            "burnin": cfg.get("burnin_warranty", ""),
            "review_summary": cfg.get("review_summary", ""),
            "pros": cfg.get("pros", ""),
            "cons": cfg.get("cons", ""),
            "reviews": cfg.get("reviews", []),
            "pricerunner": cfg.get("pricerunner", []),
            "offers": offers
        })
        
    models_data.sort(key=lambda x: (order_grade.get(x["grade"], 9), order_tier.get(x["tier"], 9), x["best_total"] or 999999))
    
    # Estadisticas generales
    total_models = len(models_data)
    count_deals = sum(1 for m in models_data if m["grade"] in ("A", "B"))
    count_c = sum(1 for m in models_data if m["grade"] == "C")
    count_d = sum(1 for m in models_data if m["grade"] == "D")
    total_active_offers = sum(len(m["offers"]) for m in models_data)
    best_deal = next((m for m in models_data if m["grade"] in ("A", "B")), None)
    
    # Formatear fecha
    try:
        dt_obj = datetime.fromisoformat(started_at)
        dt_str = dt_obj.strftime("%d de %B de %Y, %H:%M:%S")
    except Exception:
        dt_str = run_date
        
    # JSON seguro para incrustar en JavaScript para filtros
    js_models_json = json.dumps(models_data, ensure_ascii=False)
    
    # Generar HTML
    html_content = f"""<!DOCTYPE html>
<html lang="es" class="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Cuadro de Mando de Monitores 4K en Dinamarca</title>
  <script src="https://www.gstatic.com/antigravity/web/dev/tailwindcss.min.js"></script>
  <style>
    body {{
      background-color: #0f172a;
      color: #f1f5f9;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
    }}
    .custom-scrollbar::-webkit-scrollbar {{
      width: 6px;
      height: 6px;
    }}
    .custom-scrollbar::-webkit-scrollbar-track {{
      background: #1e293b;
    }}
    .custom-scrollbar::-webkit-scrollbar-thumb {{
      background: #475569;
      border-radius: 3px;
    }}
  </style>
</head>
<body class="p-4 sm:p-6 lg:p-8 min-h-screen">
  <div class="max-w-7xl mx-auto space-y-6">

    <!-- HEADER SUPERIOR -->
    <header class="bg-slate-800/90 border border-slate-700/80 rounded-2xl p-6 shadow-xl backdrop-blur-md flex flex-col md:flex-row md:items-center justify-between gap-4">
      <div>
        <div class="flex items-center gap-3">
          <span class="inline-flex p-2 rounded-xl bg-blue-500/10 text-blue-400 border border-blue-500/20 text-xl font-bold">🖥️</span>
          <div>
            <h1 class="text-2xl sm:text-3xl font-extrabold tracking-tight text-white flex items-center gap-2">
              Monitor Deals DK
              <span class="text-xs px-2.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 font-medium">Auto-Tracking Activo</span>
            </h1>
            <p class="text-slate-400 text-sm mt-0.5">Seguimiento automático diario en tiendas danesas (09:00 Copenhague) · RTX 2080 Super & Coding</p>
          </div>
        </div>
      </div>
      <div class="flex flex-wrap items-center gap-2 text-xs text-slate-300">
        <div class="bg-slate-900/80 px-3.5 py-2 rounded-xl border border-slate-700/60 flex items-center gap-2">
          <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
          <span>Actualizado: <strong>{html.escape(dt_str)}</strong></span>
        </div>
        <button onclick="location.reload()" class="bg-blue-600 hover:bg-blue-500 text-white font-medium px-3.5 py-2 rounded-xl transition shadow-sm flex items-center gap-1.5 cursor-pointer">
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
          Refrescar
        </button>
      </div>
    </header>

    <!-- METRICAS / KPIS PRINCIPALES -->
    <div class="grid grid-cols-2 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      <div class="bg-slate-800/70 border border-slate-700/70 rounded-2xl p-4 shadow-sm">
        <p class="text-xs font-medium text-slate-400 uppercase tracking-wider">Ofertas Recomendadas</p>
        <div class="mt-2 flex items-baseline gap-2">
          <span class="text-3xl font-black text-emerald-400">{count_deals}</span>
          <span class="text-xs text-emerald-400/80 bg-emerald-500/10 px-2 py-0.5 rounded-full border border-emerald-500/20 font-semibold">Clase A/B</span>
        </div>
        <p class="text-xs text-slate-400 mt-1">Precios en objetivo o mínimo ~3m</p>
      </div>

      <div class="bg-slate-800/70 border border-slate-700/70 rounded-2xl p-4 shadow-sm">
        <p class="text-xs font-medium text-slate-400 uppercase tracking-wider">Monitores Rastreados</p>
        <div class="mt-2 flex items-baseline gap-2">
          <span class="text-3xl font-black text-white">{total_models}</span>
          <span class="text-xs text-slate-400">candidatos 4K 32"</span>
        </div>
        <p class="text-xs text-slate-400 mt-1">{count_c} en precio normal · {count_d} no recomendados</p>
      </div>

      <div class="bg-slate-800/70 border border-slate-700/70 rounded-2xl p-4 shadow-sm">
        <p class="text-xs font-medium text-slate-400 uppercase tracking-wider">Ofertas Activas en DK</p>
        <div class="mt-2 flex items-baseline gap-2">
          <span class="text-3xl font-black text-blue-400">{total_active_offers}</span>
          <span class="text-xs text-slate-400">tiendas danesas</span>
        </div>
        <p class="text-xs text-slate-400 mt-1">Komplett, Proshop, CS, CompuMail...</p>
      </div>

      <div class="bg-slate-800/70 border border-slate-700/70 rounded-2xl p-4 shadow-sm">
        <p class="text-xs font-medium text-slate-400 uppercase tracking-wider">Mejor Oferta del Día</p>
        <div class="mt-2">
          <span class="text-lg font-bold text-emerald-300 truncate block">
            {html.escape(best_deal["name"].split("(")[0].strip()) if best_deal else "Sin ofertas B"}
          </span>
          <div class="flex items-center gap-2 mt-1">
            <span class="text-xl font-extrabold text-white">{fmt_dkk(best_deal["best_total"]) if best_deal else "n/d"}</span>
            <span class="text-xs px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 font-semibold">{html.escape(best_deal["best_merchant"]) if best_deal else ""}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- BARRA DE FILTROS Y BUSQUEDA -->
    <div class="bg-slate-800/60 border border-slate-700/60 rounded-2xl p-4 space-y-3">
      <div class="flex flex-col md:flex-row md:items-center justify-between gap-3">
        <!-- Filtro por Clase -->
        <div class="flex flex-wrap items-center gap-1.5" id="grade-filters">
          <span class="text-xs font-semibold text-slate-400 mr-1">Filtrar:</span>
          <button onclick="setFilter('all')" id="btn-all" class="filter-btn px-3 py-1.5 rounded-xl text-xs font-medium bg-blue-600 text-white shadow-sm transition">Todos ({total_models})</button>
          <button onclick="setFilter('deals')" id="btn-deals" class="filter-btn px-3 py-1.5 rounded-xl text-xs font-medium bg-slate-700/70 text-slate-300 hover:bg-slate-700 transition">Solo Ofertas A/B ({count_deals})</button>
          <button onclick="setFilter('normal')" id="btn-normal" class="filter-btn px-3 py-1.5 rounded-xl text-xs font-medium bg-slate-700/70 text-slate-300 hover:bg-slate-700 transition">Normales Clase C ({count_c})</button>
          <button onclick="setFilter('not_rec')" id="btn-not_rec" class="filter-btn px-3 py-1.5 rounded-xl text-xs font-medium bg-slate-700/70 text-slate-300 hover:bg-slate-700 transition">No recomendados ({count_d})</button>
        </div>

        <!-- Busqueda rapida -->
        <div class="relative w-full md:w-72">
          <input type="text" id="search-input" oninput="applyFilters()" placeholder="Buscar modelo o tienda..." class="w-full bg-slate-900/90 border border-slate-700 rounded-xl px-3.5 py-1.5 text-xs text-white placeholder-slate-400 focus:outline-none focus:border-blue-500 transition">
          <svg class="w-4 h-4 text-slate-400 absolute right-3 top-2 pointer-events-none" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
        </div>
      </div>

      <!-- Filtros secundarios -->
      <div class="flex flex-wrap items-center gap-2 pt-2 border-t border-slate-700/40 text-xs">
        <span class="text-slate-400">Tecnología:</span>
        <button onclick="setPanelFilter('all')" class="panel-btn px-2.5 py-1 rounded-lg bg-slate-700/50 text-slate-200 font-medium hover:bg-slate-700">Todos</button>
        <button onclick="setPanelFilter('qd-oled')" class="panel-btn px-2.5 py-1 rounded-lg bg-slate-900/50 text-slate-400 hover:text-white hover:bg-slate-700">QD-OLED</button>
        <button onclick="setPanelFilter('woled')" class="panel-btn px-2.5 py-1 rounded-lg bg-slate-900/50 text-slate-400 hover:text-white hover:bg-slate-700">WOLED (Mate)</button>
        <button onclick="setPanelFilter('ips')" class="panel-btn px-2.5 py-1 rounded-lg bg-slate-900/50 text-slate-400 hover:text-white hover:bg-slate-700">IPS / Mini-LED (Cero Burn-In)</button>

        <span class="text-slate-500 mx-1">|</span>
        <label class="inline-flex items-center gap-1.5 cursor-pointer text-slate-300">
          <input type="checkbox" id="stock-only" onchange="applyFilters()" class="rounded bg-slate-900 border-slate-700 text-blue-600 focus:ring-0">
          <span>Solo en stock inmediato (In Stock)</span>
        </label>
      </div>
    </div>

    <!-- LISTADO DE TARJETAS DE MONITORES -->
    <div id="monitors-container" class="space-y-4">
      <!-- Inyectado por JS o pre-renderizado -->
    </div>

    <!-- SECCION INFERIOR: DESCUBRIMIENTOS & REVIEWS -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 pt-4">
      <!-- Nuevos modelos detectados -->
      <div class="bg-slate-800/60 border border-slate-700/60 rounded-2xl p-5 shadow-sm">
        <div class="flex items-center justify-between mb-3">
          <h2 class="text-sm font-bold text-white flex items-center gap-2">
            <span class="text-amber-400">✨</span> Nuevos Monitores 32" OLED Detectados en DK
          </h2>
          <span class="text-xs text-slate-400">{len(discovered)} detectados</span>
        </div>
        <div class="space-y-2 max-h-56 overflow-y-auto custom-scrollbar pr-1">
          {"".join(f'''
          <div class="bg-slate-900/70 border border-slate-700/50 rounded-xl p-3 flex items-center justify-between text-xs">
            <div class="truncate mr-2">
              <p class="font-semibold text-slate-200 truncate">{html.escape(d[1])}</p>
              <p class="text-slate-400 text-[11px] mt-0.5">Detectado: {html.escape(d[3])}</p>
            </div>
            <a href="https://www.pricerunner.dk{html.escape(d[2])}" target="_blank" class="px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-blue-400 border border-slate-700 shrink-0 font-medium">Ver en PriceRunner</a>
          </div>
          ''' for d in discovered) if discovered else '<p class="text-xs text-slate-400 italic">No hay modelos nuevos sin clasificar.</p>'}
        </div>
      </div>

      <!-- Reviews recientes -->
      <div class="bg-slate-800/60 border border-slate-700/60 rounded-2xl p-5 shadow-sm">
        <div class="flex items-center justify-between mb-3">
          <h2 class="text-sm font-bold text-white flex items-center gap-2">
            <span class="text-blue-400">📝</span> Feeds de Reviews Relevantes (RTINGS, TFTCentral...)
          </h2>
          <span class="text-xs text-slate-400">{len(reviews)} relevantes</span>
        </div>
        <div class="space-y-2 max-h-56 overflow-y-auto custom-scrollbar pr-1">
          {"".join(f'''
          <div class="bg-slate-900/70 border border-slate-700/50 rounded-xl p-3 flex items-center justify-between text-xs">
            <div class="truncate mr-2">
              <span class="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-slate-800 text-blue-300 border border-slate-700 mr-1.5">{html.escape(r[1])}</span>
              <span class="font-medium text-slate-200">{html.escape(r[2])}</span>
              <p class="text-slate-400 text-[11px] mt-0.5">Fecha: {html.escape(r[3])}</p>
            </div>
            <a href="{html.escape(r[0])}" target="_blank" class="px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-blue-400 border border-slate-700 shrink-0 font-medium">Leer review</a>
          </div>
          ''' for r in reviews) if reviews else '<p class="text-xs text-slate-400 italic">No se han registrado reviews adicionales hoy.</p>'}
        </div>
      </div>
    </div>

    <!-- FOOTER INFORMATIVO -->
    <footer class="pt-6 border-t border-slate-800 text-xs text-slate-500 flex flex-col sm:flex-row items-center justify-between gap-3">
      <p>Base de datos persistente: <code class="text-slate-400">monitor-tracker/data/history.db</code> · Exportación CSV disponible en <code class="text-slate-400">data/offers_history.csv</code></p>
      <p>Programador de tareas: <strong class="text-slate-400">MonitorTrackerDK</strong> (09:00 Romance Standard Time hasta 01-12-2026)</p>
    </footer>

  </div>

  <script>
    const MONITORS = {js_models_json};

    let currentFilter = 'all';
    let currentPanel = 'all';

    function setFilter(f) {{
      currentFilter = f;
      document.querySelectorAll('.filter-btn').forEach(b => {{
        b.className = 'filter-btn px-3 py-1.5 rounded-xl text-xs font-medium bg-slate-700/70 text-slate-300 hover:bg-slate-700 transition';
      }});
      const active = document.getElementById('btn-' + f);
      if (active) active.className = 'filter-btn px-3 py-1.5 rounded-xl text-xs font-medium bg-blue-600 text-white shadow-sm transition';
      applyFilters();
    }}

    function setPanelFilter(p) {{
      currentPanel = p;
      document.querySelectorAll('.panel-btn').forEach(b => {{
        b.className = 'panel-btn px-2.5 py-1 rounded-lg bg-slate-900/50 text-slate-400 hover:text-white hover:bg-slate-700';
      }});
      event.target.className = 'panel-btn px-2.5 py-1 rounded-lg bg-slate-700/80 text-white font-medium';
      applyFilters();
    }}

    function fmtDkk(n) {{
      if (n === null || n === undefined) return 'n/d';
      return Math.round(n).toLocaleString('da-DK') + ' DKK';
    }}

    function getGradeBadge(g) {{
      switch(g) {{
        case 'A': return '<span class="px-2.5 py-1 rounded-xl text-xs font-black bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">Clase A · Excelente</span>';
        case 'B': return '<span class="px-2.5 py-1 rounded-xl text-xs font-black bg-blue-500/20 text-blue-300 border border-blue-500/30">Clase B · Compra Recomendada</span>';
        case 'C': return '<span class="px-2.5 py-1 rounded-xl text-xs font-bold bg-amber-500/15 text-amber-300 border border-amber-500/30">Clase C · Precio Normal</span>';
        case 'D': return '<span class="px-2.5 py-1 rounded-xl text-xs font-bold bg-rose-500/15 text-rose-300 border border-rose-500/30">Clase D · No Recomendado</span>';
        default:  return '<span class="px-2.5 py-1 rounded-xl text-xs font-bold bg-slate-700 text-slate-300">Clase ' + g + '</span>';
      }}
    }}

    function toggleOffers(key) {{
      const el = document.getElementById('offers-' + key);
      const icon = document.getElementById('icon-' + key);
      if (el.classList.contains('hidden')) {{
        el.classList.remove('hidden');
        if (icon) icon.style.transform = 'rotate(180deg)';
      }} else {{
        el.classList.add('hidden');
        if (icon) icon.style.transform = 'rotate(0deg)';
      }}
    }}

    function applyFilters() {{
      const q = (document.getElementById('search-input').value || '').toLowerCase();
      const inStockOnly = document.getElementById('stock-only').checked;

      const container = document.getElementById('monitors-container');
      container.innerHTML = '';

      const filtered = MONITORS.filter(m => {{
        // Filtro clase
        if (currentFilter === 'deals' && !(m.grade === 'A' || m.grade === 'B')) return false;
        if (currentFilter === 'normal' && m.grade !== 'C') return false;
        if (currentFilter === 'not_rec' && m.grade !== 'D') return false;

        // Filtro panel
        const nameLow = (m.name + ' ' + m.pros).toLowerCase();
        if (currentPanel === 'qd-oled' && !nameLow.includes('qd-oled')) return false;
        if (currentPanel === 'woled' && !nameLow.includes('woled')) return false;
        if (currentPanel === 'ips' && !(nameLow.includes('ips') || nameLow.includes('mini-led') || nameLow.includes('lcd'))) return false;

        // Filtro stock
        if (inStockOnly && m.best_stock !== 'IN_STOCK') return false;

        // Busqueda texto
        if (q) {{
          const txt = (m.name + ' ' + (m.best_merchant || '') + ' ' + m.pros + ' ' + m.cons).toLowerCase();
          if (!txt.includes(q)) return false;
        }}

        return true;
      }});

      if (filtered.length === 0) {{
        container.innerHTML = '<div class="bg-slate-800/40 border border-slate-700/60 rounded-2xl p-8 text-center text-slate-400 text-sm">No se encontraron monitores con los filtros seleccionados.</div>';
        return;
      }}

      filtered.forEach(m => {{
        const isDeal = m.grade === 'A' || m.grade === 'B';
        const cardBorder = isDeal ? 'border-blue-500/40 shadow-lg shadow-blue-500/5 bg-slate-800/90' : 'border-slate-700/70 bg-slate-800/60';
        
        // Mejor enlace de tienda o comparador
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
            else if (r.includes('techspot.com')) site = 'Monitors Unboxed';
            else if (r.includes('techpowerup.com')) site = 'TechPowerUp';
            return `<a href="${{r}}" target="_blank" class="px-2.5 py-1 rounded-lg bg-slate-900/80 hover:bg-blue-600/20 text-blue-400 hover:text-blue-300 border border-slate-700 text-xs font-semibold inline-flex items-center gap-1 transition">
              <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/></svg>
              ${{site}}
            </a>`;
          }}).join(' ');
        }}

        // Tabla de tiendas
        let offersTable = '<div class="text-xs text-slate-400 p-3 italic">No hay ofertas individuales indexadas hoy.</div>';
        if (m.offers && m.offers.length > 0) {{
          offersTable = `
            <div class="overflow-x-auto">
              <table class="w-full text-left text-xs">
                <thead class="bg-slate-900/80 text-slate-400 border-b border-slate-700/60 uppercase text-[10px] tracking-wider">
                  <tr>
                    <th class="py-2 px-3">Tienda</th>
                    <th class="py-2 px-3">Precio</th>
                    <th class="py-2 px-3">Envío</th>
                    <th class="py-2 px-3">Total</th>
                    <th class="py-2 px-3">Stock / Disponibilidad</th>
                    <th class="py-2 px-3 text-right">Acción</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-slate-800">
                  ${{m.offers.map(o => {{
                    const isBest = o === bestOffer;
                    const stockColor = o.stock === 'IN_STOCK' ? 'text-emerald-400' : 'text-amber-400';
                    const trustBadge = !o.trusted ? '<span class="text-[10px] px-1.5 py-0.5 rounded bg-rose-500/10 text-rose-400 border border-rose-500/20 ml-1">Marketplace / Poco fiable</span>' : '';
                    return `
                      <tr class="${{isBest ? 'bg-blue-500/5 font-semibold text-white' : 'hover:bg-slate-700/20 text-slate-300'}}">
                        <td class="py-2 px-3">${{o.merchant}} ${{trustBadge}}</td>
                        <td class="py-2 px-3">${{fmtDkk(o.price)}}</td>
                        <td class="py-2 px-3 text-slate-400">${{o.shipping !== null ? fmtDkk(o.shipping) : 'n/d'}}</td>
                        <td class="py-2 px-3 font-bold text-white">${{fmtDkk(o.total || o.price)}}</td>
                        <td class="py-2 px-3 ${{stockColor}}">${{o.stock}}</td>
                        <td class="py-2 px-3 text-right">
                          <a href="${{o.offer_url || o.product_url}}" target="_blank" class="px-2 py-0.5 rounded bg-slate-700 hover:bg-blue-600 text-white font-medium inline-block text-[11px] transition">Ir a tienda</a>
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
            
            <!-- Columna Izquierda: Datos del monitor -->
            <div class="space-y-3 flex-1">
              <div class="flex flex-wrap items-center gap-2">
                ${{getGradeBadge(m.grade)}}
                <span class="text-xs px-2.5 py-0.5 rounded-xl bg-slate-700/60 text-slate-300 font-medium">Tier: ${{m.tier.toUpperCase()}}</span>
                ${{m.best_stock === 'IN_STOCK' ? '<span class="text-xs px-2.5 py-0.5 rounded-xl bg-emerald-500/10 text-emerald-300 border border-emerald-500/20 font-medium">En stock</span>' : '<span class="text-xs px-2.5 py-0.5 rounded-xl bg-amber-500/10 text-amber-300 border border-amber-500/20 font-medium">Bajo pedido</span>'}}
              </div>

              <div>
                <h3 class="text-lg sm:text-xl font-bold text-white tracking-tight">${{m.name}}</h3>
                <p class="text-xs text-slate-400 mt-1">${{m.review_summary}}</p>
              </div>

              <!-- Bloque Pros y Cons -->
              <div class="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs pt-1">
                <div class="bg-slate-900/70 border border-slate-700/40 rounded-xl p-3">
                  <span class="text-emerald-400 font-bold block mb-1">✓ Ventajas clave:</span>
                  <p class="text-slate-300 leading-relaxed">${{m.pros}}</p>
                </div>
                <div class="bg-slate-900/70 border border-slate-700/40 rounded-xl p-3">
                  <span class="text-amber-400 font-bold block mb-1">⚠ Limitaciones a considerar:</span>
                  <p class="text-slate-300 leading-relaxed">${{m.cons}}</p>
                </div>
              </div>

              <!-- Garantia Burn-In & Reviews Links -->
              <div class="flex flex-wrap items-center justify-between gap-2 text-xs pt-1 text-slate-400">
                <div class="flex items-center gap-1.5">
                  <span class="text-blue-400">🛡️</span>
                  <span><strong>Garantía Burn-In:</strong> ${{m.burnin}}</span>
                </div>
                <div class="flex items-center gap-1.5">
                  ${{reviewsHtml}}
                </div>
              </div>
            </div>

            <!-- Columna Derecha: Precios y Acciones -->
            <div class="bg-slate-900/90 border border-slate-700/70 rounded-2xl p-4 sm:p-5 lg:w-80 shrink-0 flex flex-col justify-between space-y-4">
              <div>
                <div class="flex items-center justify-between">
                  <span class="text-xs font-semibold text-slate-400 uppercase tracking-wider">Mejor Precio Actual</span>
                  <span class="text-xs px-2 py-0.5 rounded font-semibold bg-blue-500/10 text-blue-300 border border-blue-500/20">${{m.best_merchant || 'n/d'}}</span>
                </div>
                
                <div class="mt-2 flex items-baseline gap-2">
                  <span class="text-3xl font-black text-white tracking-tight">${{fmtDkk(m.best_total)}}</span>
                </div>
                <p class="text-xs text-slate-400 mt-0.5">Incluye gastos de envío e IVA danés</p>

                <!-- Comparativa Precios de Referencia -->
                <div class="mt-4 space-y-1.5 text-xs border-t border-slate-800 pt-3">
                  <div class="flex justify-between">
                    <span class="text-slate-400">Precio Objetivo:</span>
                    <strong class="text-slate-200 font-semibold">${{fmtDkk(m.target_price)}}</strong>
                  </div>
                  <div class="flex justify-between">
                    <span class="text-slate-400">Mínimo observado ~3m:</span>
                    <strong class="text-slate-200 font-semibold">${{fmtDkk(m.ref_min)}}</strong>
                  </div>
                  <div class="flex justify-between">
                    <span class="text-slate-400">Mediana de mercado ~3m:</span>
                    <strong class="text-slate-200 font-semibold">${{fmtDkk(m.ref_median)}}</strong>
                  </div>
                </div>
              </div>

              <!-- Botones de compra y despliegue -->
              <div class="space-y-2 pt-2">
                <a href="${{buyUrl}}" target="_blank" class="w-full py-2.5 px-4 rounded-xl font-bold text-xs text-center bg-blue-600 hover:bg-blue-500 text-white transition shadow-sm flex items-center justify-center gap-2 cursor-pointer">
                  <span>Ir a la tienda (${{m.best_merchant || 'Comprar'}})</span>
                  <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"/></svg>
                </a>
                
                <div class="flex items-center gap-2">
                  <a href="${{prUrl}}" target="_blank" class="flex-1 py-1.5 px-3 rounded-lg text-center bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-medium border border-slate-700 transition">PriceRunner</a>
                  <button onclick="toggleOffers('${{m.key}}')" class="flex-1 py-1.5 px-3 rounded-lg text-center bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-medium border border-slate-700 transition flex items-center justify-center gap-1 cursor-pointer">
                    <span>${{m.offers ? m.offers.length : 0}} ofertas</span>
                    <svg id="icon-${{m.key}}" class="w-3 h-3 transition-transform" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
                  </button>
                </div>
              </div>

            </div>

          </div>

          <!-- Desplegable con todas las tiendas danesas -->
          <div id="offers-${{m.key}}" class="hidden mt-4 pt-4 border-t border-slate-700/60 bg-slate-900/50 rounded-xl overflow-hidden">
            <div class="px-4 py-2 bg-slate-900/80 border-b border-slate-800 flex items-center justify-between text-xs">
              <span class="font-bold text-slate-300">Desglose de ofertas en tiendas danesas</span>
              <span class="text-slate-500">${{m.offers ? m.offers.length : 0}} registradas</span>
            </div>
            ${{offersTable}}
          </div>
        `;

        container.appendChild(card);
      }});
    }}

    // Render inicial
    applyFilters();
  </script>
</body>
</html>
"""
    
    # Rutas por defecto si no se especifican (incluyendo public/ e index.html para Vercel)
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
    print("Dashboard generado con exito en:", paths)
