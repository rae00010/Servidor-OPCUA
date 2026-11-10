"""Fase C, paso 5: API de la interfaz (FastAPI) y gestor del proceso del servidor.
La interfaz se lanza como subproceso (Uvicorn) y se usa por HTTP con urllib; el
servidor arranca contra el PLC emulado en el puerto 102."""
import _rutas  # noqa: F401  (servidor/ en la ruta; carpeta de trabajo tmp/)
import os; os.makedirs("logs", exist_ok=True)   # logs del servidor
import asyncio, ctypes, http.cookiejar, json, logging, shutil, subprocess, sys, time, urllib.error, urllib.request
import uuid
from pathlib import Path
import snap7
from snap7.type import SrvArea
from asyncua import Client, ua
logging.disable(logging.WARNING)

res = []
def check(nombre, ok): res.append((nombre, bool(ok)))
PUERTO = 8091
BASE = f"http://127.0.0.1:{PUERTO}"
CARPETA = Path("prueba_faseC") / "api"
shutil.rmtree(CARPETA, ignore_errors=True); CARPETA.mkdir(parents=True)
shutil.copy("../modelos/modelo_celula.xml", CARPETA / "modelo_celula.xml")


class Cliente:
    def __init__(self, cabecera=True, host=None):
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.cabecera, self.host = cabecera, host

    def pedir(self, metodo, ruta, cuerpo=None, datos=None, tipo="application/json"):
        if cuerpo is not None:
            datos = json.dumps(cuerpo).encode()
        r = urllib.request.Request(BASE + ruta, data=datos, method=metodo)
        if datos is not None:
            r.add_header("Content-Type", tipo)
        if self.cabecera:
            r.add_header("X-Interfaz", "1")
        if self.host:
            r.add_header("Host", self.host)
        try:
            with self.op.open(r, timeout=30) as resp:
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    get = lambda s, ruta: s.pedir("GET", ruta)
    post = lambda s, ruta, cuerpo=None: s.pedir("POST", ruta, cuerpo if cuerpo is not None else {})
    put = lambda s, ruta, cuerpo: s.pedir("PUT", ruta, cuerpo)

    def subir(self, nombre, contenido):
        lim = uuid.uuid4().hex
        datos = (f"--{lim}\r\nContent-Disposition: form-data; name=\"fichero\"; filename=\"{nombre}\"\r\n"
                 f"Content-Type: application/xml\r\n\r\n").encode() + contenido + f"\r\n--{lim}--\r\n".encode()
        return self.pedir("POST", "/api/modelo/subir", datos=datos, tipo=f"multipart/form-data; boundary={lim}")


def lanzar_interfaz(*extra, log="logs/interfaz_faseC_5.log"):
    p = subprocess.Popen([sys.executable, "../servidor/interfaz.py", "--carpeta", str(CARPETA), "--puerto", str(PUERTO),
                          *extra], stdout=open(log, "w"), stderr=subprocess.STDOUT)
    for _ in range(80):
        try:
            urllib.request.urlopen(BASE + "/api/sesion", timeout=1); return p
        except Exception:
            if p.poll() is not None:
                return p
            time.sleep(0.25)
    raise RuntimeError("la interfaz no responde")


def esperar(cond, segundos=20):
    fin = time.time() + segundos
    while time.time() < fin:
        if cond():
            return True
        time.sleep(0.25)
    return False


srv = snap7.server.Server(); areas = {}
for a in (SrvArea.PE, SrvArea.PA, SrvArea.MK):
    b = (ctypes.c_ubyte * 4)(); srv.register_area(a, 0, b); areas[a] = b
srv.start_to("127.0.0.1", 102)

CONFIG = {"driver": {"nombre": "snap7", "parametros": {"ip": "127.0.0.1", "timeout_ms": 500}},
          "modelo": {"origen": "nodeset", "nodeset": "modelo_celula.xml"},
          "seguridad": {"modo": "none", "cert_dir": "pki", "usuarios": []},
          "medida": {"periodo_ms": 100}}


async def leer_escribir():
    c = Client("opc.tcp://127.0.0.1:4840")
    for _ in range(60):
        try:
            await c.connect(); break
        except Exception:
            await asyncio.sleep(0.25)
    try:
        ns = await c.get_namespace_index("urn:miservidor:ledRojo")
        areas[SrvArea.PE][0] |= 0x20; await asyncio.sleep(1.0)
        v = await c.get_node(f"ns={ns};s=CilindroA.Estado.Expandido").read_value()
        await c.get_node(f"ns={ns};s=LedRojo.Value").write_value(ua.DataValue(ua.Variant(True, ua.VariantType.Boolean)))
        await asyncio.sleep(0.3)
        return v is True and bool(areas[SrvArea.PA][0] & 0x08)
    finally:
        await c.disconnect()


ui = lanzar_interfaz()
try:
    c = Cliente()
    # --- 5a: drivers ---
    st, d = c.get("/api/drivers")
    check("5a /api/drivers: snap7 con sus parametros declarados", st == 200 and d[0]["nombre"] == "snap7"
          and [p["nombre"] for p in d[0]["parametros"]][:2] == ["ip", "rack"])
    # --- 5b: protecciones ---
    st, _ = Cliente(cabecera=False).post("/api/modelo/validar", {"yaml": "x"})
    check("5b POST sin cabecera X-Interfaz: 403", st == 403)
    st, _ = Cliente(host="atacante.example:8091").get("/api/drivers")
    check("5b Host ajeno en modo local (DNS rebinding): 403", st == 403)
    # --- 5c: validacion inmediata de la planta ---
    st, pl = c.get("/api/plantillas/celula")
    st, v = c.post("/api/modelo/validar", {"yaml": pl["yaml"]})
    check("5c validar plantilla celula: valida, 1 aviso (Q0.3) y tabla de 10 filas",
          v["valida"] and len(v["avisos"]) == 1 and len(v["tabla"]) == 10
          and {"ruta": "LedRojo/Value", "direccion": "Q0.3", "tipo": "BOOL", "sentido": "OPC UA -> PLC"} in v["tabla"])
    st, v = c.post("/api/modelo/validar", {"yaml": pl["yaml"].replace("Value: Q0.3", "Value: Q0")})
    check("5c YAML con error: lista de errores con la ruta", not v["valida"] and any("LedRojo/Value" in e for e in v["errores"]))
    # --- 5d: generar: rutas fuera de la carpeta rechazadas ---
    malas = [("../fuera.yaml", "m.xml", "'..'"), ("p.yaml", "C:/Windows/m.xml", "absolutas"),
             ("/tmp/p.yaml", "m.xml", "absolutas"), ("p.txt", "m.xml", "solo se admiten"),
             ("p.yaml", "sub/../../m.xml", "'..'"), ("p.yaml", "m.yaml", "solo se admiten")]
    ok = True
    for pl_n, ns_n, frag in malas:
        st, r = c.post("/api/modelo/generar", {"yaml": pl["yaml"], "planta": pl_n, "nodeset": ns_n})
        ok &= st == 400 and frag in r["errores"][0]
    check("5d generar: rutas absolutas, con '..' o de otra extension rechazadas", ok and not Path("prueba_faseC/fuera.yaml").exists())
    st, r = c.post("/api/modelo/generar", {"yaml": pl["yaml"], "planta": "planta_ui.yaml", "nodeset": "modelos/modelo_ui.xml"})
    check("5d generar dentro de la carpeta: escribe planta y NodeSet", st == 200 and (CARPETA / "planta_ui.yaml").exists()
          and (CARPETA / "modelos" / "modelo_ui.xml").exists() and len(r["tabla"]) == 10)
    # --- 5e: importar ---
    ok = True
    for ruta in ("../modelo_celula.xml", str(Path("../modelos/modelo_celula.xml").resolve()), "..\\modelo_celula.xml", "config.yaml"):
        st, r = c.post("/api/modelo/importar", {"ruta": ruta})
        ok &= st == 400
    check("5e importar: ruta fuera de la carpeta, absoluta o no .xml rechazada", ok)
    st, r = c.post("/api/modelo/importar", {"ruta": "modelo_celula.xml"})
    check("5e importar NodeSet: valido, 10 variables y ControlarPulsador",
          st == 200 and r["valida"] and len(r["tabla"]) == 10 and r["metodos"][0]["nombre"] == "ControlarPulsador")
    # --- 5f: subida con nombre saneado ---
    st, r = c.subir("..\\..\\mi modelo$.xml", Path("../modelos/modelo_celula.xml").read_bytes())
    check("5f subir: nombre saneado y guardado dentro de la carpeta",
          st == 200 and r["nodeset"] == "mi_modelo_.xml" and (CARPETA / "mi_modelo_.xml").exists())
    st, r = c.subir("x.txt", b"hola")
    check("5f subir .txt rechazado", st == 400)
    st, r = c.subir("roto.xml", b"<no-es-xml")
    check("5f subir XML mal formado: error y no queda el fichero", st == 400 and not (CARPETA / "roto.xml").exists())
    st, r = c.subir("mi modelo$.xml", b"<x/>")
    check("5f subir sobre un fichero existente sin sobrescribir: 409", st == 409)
    # --- 5g: configuracion ---
    st, r = c.post("/api/config/validar", {"config": {**CONFIG, "modelo": {"nodeset": "../modelo_celula.xml"}}})
    check("5g validar configuracion con ruta fuera de la carpeta: error", not r["valida"] and "'..'" in r["errores"][0])
    cfg = json.loads(json.dumps(CONFIG)); cfg["seguridad"]["usuarios"] = [{"nombre": "rafa", "clave": "clave123"}]
    st, r = c.put("/api/config", {"config": cfg})
    texto = (CARPETA / "config.yaml").read_text(encoding="utf-8")
    check("5g guardar: la contrasena se guarda como clave_hash, nunca en claro",
          st == 200 and "clave_hash: pbkdf2_sha256$" in texto and "clave123" not in texto and not r["avisos"])
    st, r = c.get("/api/config")
    check("5g leer configuracion: no devuelve el hash", r["config"]["seguridad"]["usuarios"] == [{"nombre": "rafa", "tiene_clave": True}]
          and "pbkdf2" not in json.dumps(r))
    cfg["seguridad"]["usuarios"] = [{"nombre": "rafa"}]           # sin clave: se conserva el hash
    st, r = c.put("/api/config", {"config": cfg})
    check("5g guardar sin tocar la contrasena: se conserva el hash",
          st == 200 and "clave_hash: pbkdf2_sha256$" in (CARPETA / "config.yaml").read_text(encoding="utf-8"))
    st, r = c.put("/api/config", {"config": CONFIG})
    # --- 5h: arrancar, cliente OPC UA, log y parada ordenada ---
    st, r = c.post("/api/servidor/arrancar")
    check("5h arrancar: subproceso en marcha", st == 200 and c.get("/api/servidor/estado")[1]["en_marcha"])
    check("5h cliente OPC UA lee y escribe", asyncio.run(leer_escribir()))
    st, r = c.get("/api/servidor/log?desde=0")
    check("5h log del servidor visible", any("Tabla de mapeo: 10 variables" in l for l in r["lineas"]))
    st, r = c.post("/api/servidor/arrancar")
    check("5h arrancar dos veces: 409", st == 409)
    st, r = c.post("/api/servidor/detener")
    check("5h detener: ordenado (codigo 0, no forzado)", st == 200 and r["codigo"] == 0 and not r["forzada"])
    check("5h tras detener: 0 sesiones S7", esperar(lambda: srv.get_status()[2] == 0, 3))
    # --- 5i: una interfaz reiniciada detecta y detiene el servidor ---
    c.post("/api/servidor/arrancar")
    esperar(lambda: any("Conectado al PLC" in l for l in c.get("/api/servidor/log")[1]["lineas"]))
    ui.terminate(); ui.wait()                                    # cae la interfaz
    time.sleep(2.0)
    pid = json.loads((CARPETA / "servidor.pid").read_text())["pid"]
    ui = lanzar_interfaz(log="logs/interfaz_faseC_5i.log")
    c = Cliente()
    e = c.get("/api/servidor/estado")[1]
    check("5i el servidor sobrevive a la caida de la interfaz y la nueva lo detecta",
          e["en_marcha"] and not e["propio"] and e["pid"] == pid)
    st, r = c.post("/api/servidor/detener")
    log = (CARPETA / "logs" / "servidor.log").read_text(encoding="utf-8", errors="replace")
    check("5i la nueva interfaz lo detiene de forma ordenada (fichero de control)",
          st == 200 and not r["forzada"] and "fichero de control" in log and "Conexion con el PLC cerrada" in log
          and not c.get("/api/servidor/estado")[1]["en_marcha"])
finally:
    ui.terminate(); ui.wait()

# --- 5j: autenticacion ---
r = subprocess.run([sys.executable, "../servidor/interfaz.py", "--carpeta", str(CARPETA), "--host", "0.0.0.0",
                    "--puerto", str(PUERTO)], capture_output=True, text=True, timeout=30)
check("5j en red sin usuario: no arranca y lo explica", r.returncode == 2 and "sin autenticacion" in r.stderr)
ui = lanzar_interfaz("--usuario", "admin:secreta", log="logs/interfaz_faseC_5j.log")
try:
    c = Cliente()
    st1, _ = c.get("/api/drivers")
    st2, _ = c.post("/api/login", {"usuario": "admin", "clave": "mala"})
    t0 = time.time(); st3, _ = c.post("/api/login", {"usuario": "admin", "clave": "secreta"}); t_login = time.time() - t0
    t0 = time.time()
    st4 = [c.get("/api/drivers")[0] for _ in range(10)]
    t10 = time.time() - t0
    c.post("/api/logout"); st5, _ = c.get("/api/drivers")
    check("5j con usuario: 401 sin sesion, 401 con clave mala, 200 tras login, 401 tras logout",
          st1 == 401 and st2 == 401 and st3 == 200 and set(st4) == {200} and st5 == 401)
    check(f"5j la sesion evita recalcular PBKDF2 (10 peticiones {t10:.2f} s, login {t_login:.2f} s)",
          t10 < t_login * 3)
finally:
    ui.terminate(); ui.wait()
    srv.stop()

print("=== RESUMEN ===")
for nombre, ok in res:
    print(f"  [{'OK   ' if ok else 'FALLO'}] {nombre}")
sys.exit(0 if all(ok for _, ok in res) else 1)
