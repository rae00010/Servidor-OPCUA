"""Fase C, paso 6: pagina web de la interfaz.
Sin recursos de internet; se sirve desde la propia interfaz; los ids y las rutas de
la API que usa app.js existen. (La interaccion en el navegador se prueba a mano.)"""
import _rutas  # noqa: F401  (servidor/ en la ruta; carpeta de trabajo tmp/)
import os; os.makedirs("logs", exist_ok=True)   # logs del servidor
import re, subprocess, sys, time, urllib.request
from pathlib import Path

import interfaz

res = []
def check(nombre, ok): res.append((nombre, bool(ok)))
DIR = Path("../servidor/interfaz")
FICHEROS = {p.name: p.read_text(encoding="utf-8") for p in DIR.iterdir() if p.is_file()}
html, js, css = FICHEROS["index.html"], FICHEROS["app.js"], FICHEROS["estilo.css"]

# --- 6a: nada cargado de internet ---
externos = {n: re.findall(r"(?:https?:)?//[\w.-]+\.[a-z]{2,}|@import|url\(\s*['\"]?http", t, re.I)
            for n, t in FICHEROS.items()}
check("6a ningun fichero estatico referencia recursos externos (http, https, //cdn, @import)",
      not any(externos.values()))
if any(externos.values()):
    print("  ", externos)
check("6a solo scripts y estilos propios", re.findall(r'<script[^>]*src="([^"]+)"', html) == ["/estaticos/app.js"]
      and re.findall(r'<link[^>]*href="([^"]+)"', html) == ["/estaticos/estilo.css"])

# --- 6b: coherencia de app.js con la pagina y con la API ---
ids_html = set(re.findall(r'id="([^"]+)"', html))
ids_js = set(re.findall(r'\$\("([^"]+)"\)', js)) | {f"{b}-{k}" for b, ks in {
    "enlace": ["fallos_max", "reintento_max_s"], "servidor": ["endpoint", "app_uri", "host"],
    "seguridad": ["modo", "cert_dir"], "medida": ["periodo_ms", "log_csv", "log_accesos"]}.items() for k in ks}
check("6b todos los ids que usa app.js existen en index.html", ids_js <= ids_html)
if not ids_js <= ids_html:
    print("  faltan:", ids_js - ids_html)
app = interfaz.crear_app(Path("prueba_faseC") / "pagina")
rutas_api = {r.path for r in app.routes if getattr(r, "path", "").startswith("/api/")}
usadas = {re.sub(r"\$\{[^}]+\}", "{nombre}", u).split("?")[0] for u in re.findall(r'[`"](/api/[^`"]*)[`"]', js)}
check("6b todas las rutas de la API que usa app.js existen", usadas and usadas <= rutas_api)
if not usadas <= rutas_api:
    print("  no existen:", usadas - rutas_api)
check("6b app.js envia la cabecera X-Interfaz", '"X-Interfaz": "1"' in js)
check("6b el formulario del driver sale de los parametros declarados (sin nombres de snap7)",
      "data-parametro" in js and not re.search(r"\b(rack|slot|snap7|S7)\b", js))
check("6b validacion inmediata del editor (500 ms)", "setTimeout(validarPlanta, 500)" in js)

# --- 6c: servida por la interfaz ---
PUERTO = 8092
p = subprocess.Popen([sys.executable, "../servidor/interfaz.py", "--carpeta", "prueba_faseC/pagina", "--puerto", str(PUERTO)],
                     stdout=open("logs/interfaz_faseC_6.log", "w"), stderr=subprocess.STDOUT)
try:
    respuestas = {}
    for _ in range(60):
        try:
            for ruta in ("/", "/estaticos/app.js", "/estaticos/estilo.css"):
                with urllib.request.urlopen(f"http://127.0.0.1:{PUERTO}{ruta}", timeout=2) as r:
                    respuestas[ruta] = (r.status, r.headers.get("content-type", ""), r.read().decode())
            break
        except Exception:
            time.sleep(0.25)
    check("6c la interfaz sirve la pagina, el script y la hoja de estilo",
          respuestas.get("/", (0,))[0] == 200 and "text/html" in respuestas["/"][1]
          and respuestas["/"][2] == html and respuestas["/estaticos/app.js"][2] == js
          and "css" in respuestas["/estaticos/estilo.css"][1])
finally:
    p.terminate(); p.wait()

print("=== RESUMEN ===")
for n, ok in res:
    print(f"  [{'OK   ' if ok else 'FALLO'}] {n}")
sys.exit(0 if all(ok for _, ok in res) else 1)
