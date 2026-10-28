# -*- coding: utf-8 -*-
"""
Listener en segundo plano para Hardware Deals DK.
Escucha eventos de actualizacion remota desde ntfy.sh (enviados desde el Dashboard de Vercel
o desde el movil). Al recibir una orden, ejecuta tracker.py y notifica el resultado.
"""
import os
import sys
import json
import time
import subprocess
import urllib.request
import traceback
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.dirname(os.path.abspath(__file__))
TRIGGER_TOPIC = "mt-dk-trigger-xpfrsrpcjylx2prx"
ALERT_TOPIC = "mt-dk-xpfrsrpcjylx2prx"
STREAM_URL = f"https://ntfy.sh/{TRIGGER_TOPIC}/json"

LOG_FILE = os.path.join(BASE, "logs", "listener.log")
os.makedirs(os.path.join(BASE, "logs"), exist_ok=True)

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def send_feedback(title, message, click_url=None):
    try:
        payload = {
            "topic": ALERT_TOPIC,
            "title": title,
            "message": message,
            "priority": 3,
            "tags": ["arrows_counterclockwise"]
        }
        if click_url:
            payload["click"] = click_url
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request("https://ntfy.sh", data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            pass
    except Exception as e:
        log(f"Error enviando feedback a ntfy: {e}")

def run_update():
    log("Iniciando actualizacion bajo demanda...")
    send_feedback("Hardware Deals DK: Actualización iniciada",
                  "Se ha recibido la orden de actualización. Escaneando tiendas en Dinamarca...",
                  "https://monitor-deals-dk.vercel.app/")
    t0 = time.time()
    try:
        # Ejecutar tracker.py
        py_exe = sys.executable
        tracker_script = os.path.join(BASE, "tracker.py")
        proc = subprocess.run([py_exe, tracker_script], cwd=BASE, capture_output=True, text=True, encoding="utf-8")
        dur = int(time.time() - t0)
        if proc.returncode == 0:
            log(f"Actualizacion completada con exito en {dur}s.")
            send_feedback("✅ Precios actualizados en Vercel",
                          f"Búsqueda finalizada con éxito ({dur}s). El dashboard ya tiene los datos frescos.",
                          "https://monitor-deals-dk.vercel.app/")
        else:
            log(f"Tracker finalizo con codigo {proc.returncode}. Salida:\n{proc.stderr[-500:]}")
            send_feedback("⚠ Actualización finalizada con advertencias",
                          f"Finalizó en {dur}s con código {proc.returncode}. Consulta logs locales.",
                          "https://monitor-deals-dk.vercel.app/")
    except Exception as e:
        log(f"Excepcion ejecutando tracker: {e}\n{traceback.format_exc()}")
        send_feedback("❌ Error en actualización remota", str(e))

def main():
    log(f"Listener iniciado. Escuchando topic: {TRIGGER_TOPIC}")
    backoff = 2
    is_running = False

    while True:
        try:
            req = urllib.request.Request(STREAM_URL, headers={"User-Agent": "HardwareDealsDK-Listener/1.0"})
            with urllib.request.urlopen(req, timeout=90) as resp:
                log("Conexion establecida con ntfy stream.")
                backoff = 2
                for line in resp:
                    text = line.decode("utf-8", errors="ignore").strip()
                    if not text:
                        continue
                    try:
                        event = json.loads(text)
                    except Exception:
                        continue

                    ev_type = event.get("event")
                    if ev_type == "message":
                        msg_body = event.get("message", "")
                        log(f"Mensaje recibido: {msg_body[:100]}")
                        if is_running:
                            log("Actualizacion ya en progreso, ignorando orden duplicada.")
                            continue
                        is_running = True
                        try:
                            run_update()
                        finally:
                            is_running = False
        except Exception as e:
            log(f"Conexion interrumpida ({e}). Reintentando en {backoff}s...")
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)

if __name__ == "__main__":
    main()
