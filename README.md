# Agente personal de investigación de monitores y seguimiento de ofertas en Dinamarca

Sistema de investigación técnica, recopilación diaria de precios en tiendas danesas y alerta de ofertas para monitores 4K de ~32 pulgadas (OLED, QD-OLED, WOLED y alternativas IPS/Mini-LED).

---

## 1. Arquitectura y funcionamiento

* **Ubicación del proyecto:** `C:\Users\hugod\.gemini\antigravity\scratch\monitor-tracker\`
* **Ejecución automática:**
  * **Programador de tareas de Windows (`MonitorTrackerDK`):** Se ejecuta todos los días a las **09:00 (Europe/Copenhagen / Romance Standard Time)** sin necesidad de abrir Antigravity ni iniciar una conversación. Si el ordenador está apagado a las 09:00, la tarea se ejecuta automáticamente en cuanto se enciende gracias a la opción `StartWhenAvailable`. Finaliza el **1 de diciembre de 2026**.
  * **Sidecar de Antigravity (`sidecar.json`):** También configurado en `~/.gemini/config/sidecars/dk-monitor-tracker/sidecar.json` con `CRON_TZ=Europe/Copenhagen 0 9 * * *` para quienes deseen que Antigravity inicie además una conversación de agente al mismo horario.
* **Fuentes consultadas automáticamente:**
  * **PriceRunner.dk:** Precios actuales, gastos de envío, disponibilidad de stock (`IN_STOCK`, `BACKORDER`), reputación y valoraciones de comercios de toda Dinamarca (Komplett.dk, Proshop.dk, ComputerSalg / CS Megastore, CompuMail, Elgiganten, Geekd, Merlin, Happii, avXperten, CDON, etc.).
  * **Prisjagt.dk:** Historial diario de precios de los últimos ~3 meses por tienda, precios medianos y mínimos del período.
  * **Feeds de reviews independientes:** RSS y Atom de TFTCentral, TechPowerUp, YouTube (Monitors Unboxed, Hardware Unboxed) y catálogo de RTINGS.
  * **Descubrimiento de nuevos modelos:** Búsquedas sistemáticas de nuevos paneles 32" OLED listados en Dinamarca.

---

## 2. Configuración de notificaciones en el móvil (ntfy.sh)

Para recibir las alertas en tu teléfono móvil (iOS o Android) o en el navegador:

1. Instala la aplicación gratuita **ntfy** (disponible en Google Play Store y Apple App Store, o ingresa en [ntfy.sh](https://ntfy.sh)).
2. Pulsa en **«Subscribe to topic»** (o «Añadir tema»).
3. Ingresa el nombre de tema privado generado:
   ```text
   mt-dk-xpfrsrpcjylx2prx
   ```
4. ¡Listo! No requiere crear cuenta, no pide contraseñas y las notificaciones llegarán de forma instantánea cuando se detecte una oferta **A (Excelente)** o **B (Buena / precio objetivo)**, un nuevo mínimo verificable o un nuevo modelo relevante.

---

## 3. Comandos útiles

Abre PowerShell en la carpeta del proyecto:

```powershell
cd C:\Users\hugod\.gemini\antigravity\scratch\monitor-tracker
```

* **Abrir el cuadro de mando visual interactivo:**
  Doble clic en el acceso directo del Escritorio: **`Monitor Deals Dashboard`**, o ejecuta:
  ```powershell
  .\open_dashboard.bat
  ```
  O ábrelo directamente en tu navegador web:
  `file:///C:/Users/hugod/.gemini/antigravity/scratch/monitor-tracker/reports/dashboard.html`
* **Ver el estado de la base de datos y últimas ejecuciones:**
  ```powershell
  python tracker.py --status
  ```
* **Enviar una notificación de prueba (Windows Toast + móvil ntfy):**
  ```powershell
  python tracker.py --test-notify
  ```
* **Ejecutar una pasada manual de comprobación (sin esperar a las 09:00):**
  ```powershell
  python tracker.py
  ```
* **Ejecutar en modo simulación (sin enviar alertas):**
  ```powershell
  python tracker.py --dry-run
  ```
* **Consultar el último informe generado:**
  Abrir el archivo `reports\latest.md`.

---

## 4. Estructura de archivos

```text
monitor-tracker/
├── candidates.json       # Lista de modelos monitorizados, filtros, precios objetivo y reviews
├── config.json           # Configuración de ntfy, tolerancias, límites y umbrales de anomalías
├── tracker.py            # Motor principal de scraping, deduplicación, clasificación y alertas
├── dashboard.py          # Generador del Cuadro de Mando Visual interactivo (HTML)
├── open_dashboard.bat    # Acceso rápido para abrir el dashboard en el navegador
├── run_tracker.bat       # Wrapper batch para ejecución desatendida
├── run_silent.vbs        # Wrapper VBScript para ejecución silenciosa sin ventana de consola
├── register_task.ps1     # Script de registro en el Programador de tareas de Windows
├── data/
│   ├── history.db        # Base de datos SQLite persistente (append-only)
│   └── offers_history.csv # Historial completo exportado a CSV para análisis en Excel
├── reports/
│   ├── dashboard.html    # Cuadro de mando visual interactivo (HTML) actualizado a diario
│   ├── latest.md         # Último informe en formato Markdown
│   └── YYYY-MM-DD.html   # Historial visual de cada día
└── logs/
    ├── run_YYYY-MM-DD.log # Registro detallado de ejecución de cada día
    └── scheduler.log     # Salida capturada por el Programador de tareas
```

---

## 5. Criterios de clasificación de ofertas

* **A. EXCELENTE:** Precio total (incl. envío e impuestos) inferior o igual al precio excelente fijado con base en la evidencia técnica, o por debajo del precio objetivo y marcando un nuevo mínimo del período observado en una tienda de alta reputación (`rating >= 4.0`, stock disponible).
* **B. BUENA:** Precio total inferior o igual al precio objetivo calculado, o situado en el mínimo observado del mercado danés para un modelo recomendable.
* **C. NORMAL:** Precio dentro de la mediana habitual del mercado (~3 meses) o rebaja publicitaria que no justifica la compra inmediata.
* **D. NO RECOMENDADA:** Precio inflado, marketplace dudoso (vendedores no verificados de 1 estrella en CDON/eBay), modelos desaconsejados por problemas técnicos probados o variantes con sobreprecio injustificado.

### Manejo de anomalías y errores
* Si una tienda falla o responde con código HTTP 403/429/Challenge, el tracker registra el incidente en la tabla `errors` y **nunca** marca el producto como agotado ni interpreta la falta de respuesta como una caída de precio a 0 DKK.
* Si una tienda publica un precio un 45% inferior a la mediana (posible error tipográfico o accesorio mal etiquetado), se etiqueta como `ANOMALY_SUSPECTED` y se alerta de forma informativa sin clasificarlo erróneamente como oferta confirmada.
