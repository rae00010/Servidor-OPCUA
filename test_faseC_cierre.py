"""Fase C, criterio de cierre.
1) Desde la interfaz (solo por su API, como la pagina), en una carpeta de trabajo vacia
   y sin editar ficheros a mano: se configura la conexion a partir de los parametros
   que declara el driver, se crea el modelo desde una plantilla (y se importa), se
   guarda la configuracion, se arranca el servidor contra el PLC emulado, un cliente
   OPC UA lee, escribe y llama al metodo, y se detiene de forma ordenada.
2) El servidor arranca solo con --config, sin la interfaz (uso en la campana)."""
import os; os.makedirs("logs", exist_ok=True)   # logs del servidor
import asyncio, ctypes, http.cookiejar, json, logging, shutil, subprocess, sys, time, urllib.error, urllib.request
from pathlib import Path
import snap7
from snap7.type import SrvArea
from asyncua import Client, ua
logging.disable(logging.WARNING)

res = []
def check(nombre, ok): res.append((nombre, bool(ok)))
PUERTO = 8093
BASE = f"http://127.0.0.1:{PUERTO}"
CARPETA = Path("prueba_faseC") / "cierre"
shutil.rmtree(CARPETA, ignore_errors=True); CARPETA.mkdir(parents=True)
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def api(metodo, ruta, cuerpo=None):
    r = urllib.request.Request(BASE + ruta, method=metodo, headers={"X-Interfaz": "1"},
                               data=json.dumps(cuerpo).encode() if cuerpo is not None else None)
    if cuerpo is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with op.open(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


srv = snap7.server.Server(); areas = {}
for a in (SrvArea.PE, SrvArea.PA, SrvArea.MK):
    b = (ctypes.c_ubyte * 4)(); srv.register_area(a, 0, b); areas[a] = b
srv.start_to("127.0.0.1", 102)
bit = lambda area, byte, b: bool(areas[area][byte] >> b & 1)


async def cliente_opcua(etiqueta):
    c = Client("opc.tcp://127.0.0.1:4840")
    for _ in range(80):
        try:
            await c.connect(); break
        except Exception:
            await asyncio.sleep(0.25)
    else:
        return False
    try:
        ns = await c.get_namespace_index("urn:miservidor:ledRojo")
        n = lambda s: c.get_node(f"ns={ns};s={s}")
        areas[SrvArea.PE][0] = 0x20; await asyncio.sleep(1.2)
        lee = await n("CilindroA.Estado.Expandido").read_value() is True
        await n("LedRojo.Value").write_value(ua.DataValue(ua.Variant(True, ua.VariantType.Boolean)))
        await asyncio.sleep(0.3)
        escribe = bit(SrvArea.PA, 0, 3)
        pv = n("PulsadorVerde"); m = await pv.get_child([f"{ns}:ControlarPulsador"])
        await pv.call_method(m, "pulsar"); await asyncio.sleep(0.3)
        metodo = bit(SrvArea.MK, 0, 0)
        await pv.call_method(m, "soltar"); await asyncio.sleep(0.3)
        metodo = metodo and not bit(SrvArea.MK, 0, 0)
        await n("LedRojo.Value").write_value(ua.DataValue(ua.Variant(False, ua.VariantType.Boolean)))
        check(f"{etiqueta}: cliente OPC UA lee I0.5, escribe Q0.3 y ControlarPulsador -> M0.0",
              lee and escribe and metodo)
        return True
    finally:
        await c.disconnect()


ui = subprocess.Popen([sys.executable, "interfaz.py", "--carpeta", str(CARPETA), "--puerto", str(PUERTO)],
                      stdout=open("logs/interfaz_faseC_cierre.log", "w"), stderr=subprocess.STDOUT)
try:
    for _ in range(80):
        try:
            urllib.request.urlopen(BASE + "/", timeout=1); break
        except Exception:
            time.sleep(0.25)
    check("C0 carpeta de trabajo vacia: ningun fichero preparado a mano", not any(CARPETA.glob("*.*")))

    # 1. Conexion: formulario a partir de los parametros declarados
    _, drivers = api("GET", "/api/drivers")
    formulario = {p["nombre"]: p["defecto"] for p in drivers[0]["parametros"]}
    formulario["ip"] = "127.0.0.1"; formulario["timeout_ms"] = 500
    _, actual = api("GET", "/api/config")
    cfg = actual["config"]
    cfg["driver"] = {"nombre": drivers[0]["nombre"], "parametros": formulario}

    # 2. Modelo: plantilla -> validacion inmediata -> generar -> importar
    _, pl = api("GET", "/api/plantillas/celula")
    _, v = api("POST", "/api/modelo/validar", {"yaml": pl["yaml"]})
    check("C1 planta validada: tabla de 10 variables y aviso de Q0.3", v["valida"] and len(v["tabla"]) == 10
          and len(v["avisos"]) == 1)
    st, g = api("POST", "/api/modelo/generar", {"yaml": pl["yaml"], "planta": "planta_celula.yaml",
                                                 "nodeset": "modelo_celula.xml"})
    st2, imp = api("POST", "/api/modelo/importar", {"ruta": "modelo_celula.xml"})
    check("C1 NodeSet generado e importado sin errores", st == 200 and st2 == 200 and imp["valida"]
          and len(imp["tabla"]) == 10)
    cfg["modelo"] = {"origen": "nodeset", "nodeset": imp["nodeset"], "planta": None}

    # 3. Guardar configuracion
    st, val = api("POST", "/api/config/validar", {"config": cfg})
    st2, gu = api("PUT", "/api/config", {"config": cfg})
    check("C2 configuracion valida y guardada en config.yaml", val["valida"] and st2 == 200
          and (CARPETA / "config.yaml").exists())

    # 4. Arrancar, cliente, detener
    st, r = api("POST", "/api/servidor/arrancar")
    check("C3 servidor arrancado desde la interfaz", st == 200)
    asyncio.run(cliente_opcua("C3 interfaz"))
    _, log = api("GET", "/api/servidor/log")
    st, par = api("POST", "/api/servidor/detener")
    time.sleep(0.5)
    texto = (CARPETA / "logs" / "servidor.log").read_text(encoding="utf-8", errors="replace")
    check("C4 detenido de forma ordenada: codigo 0, conexion S7 cerrada y 0 sesiones en el PLC",
          st == 200 and par["codigo"] == 0 and not par["forzada"] and "Conexion con el PLC cerrada" in texto
          and srv.get_status()[2] == 0)
    check("C4 el log se vio desde la interfaz", any("Tabla de mapeo: 10 variables" in l for l in log["lineas"]))
finally:
    ui.terminate(); ui.wait()

# 5. Solo --config, sin la interfaz (campana de medidas)
p = subprocess.Popen([sys.executable, "serverOPCUA_MetodoTFGV5_asyncua.py", "--config",
                      str(CARPETA / "config.yaml")], stdout=open("logs/srv_faseC_cierre.log", "w"),
                     stderr=subprocess.STDOUT)
try:
    asyncio.run(cliente_opcua("C5 solo --config"))
finally:
    p.terminate(); p.wait()
    srv.stop()

print("=== RESUMEN ===")
for nombre, ok in res:
    print(f"  [{'OK   ' if ok else 'FALLO'}] {nombre}")
sys.exit(0 if all(ok for _, ok in res) else 1)
