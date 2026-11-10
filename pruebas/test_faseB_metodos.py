"""Fase B, paso 3: metodos declarados en el NodeSet generado y enlazados por el servidor.
PLC emulado en el puerto 102."""
import _rutas  # noqa: F401  (servidor/ en la ruta; carpeta de trabajo tmp/)
import os; os.makedirs("logs", exist_ok=True)   # logs del servidor
import asyncio, ctypes, logging, subprocess, sys
import xml.etree.ElementTree as ET
import snap7
from snap7.type import SrvArea
from asyncua import Client, ua
import modelo
logging.disable(logging.WARNING)

res = []
def check(nombre, ok): res.append((nombre, bool(ok)))
N = "{http://opcfoundation.org/UA/2011/03/UANodeSet.xsd}"
URI = "urn:miservidor:ledRojo"

CELULA = "modelo_faseB_celula.xml"
modelo.generar_fichero("../modelos/planta_celula.yaml", CELULA)
SIN_IMPL = "modelo_faseB_sin_impl.xml"
yaml_sin_impl = open("../modelos/planta_celula.yaml", encoding="utf-8").read().replace(
    "      Value: {tipo: BOOL, acceso: escritura}\n",
    "      Value: {tipo: BOOL, acceso: escritura}\n    metodos:\n      Parpadear:\n"
    "        entradas:\n          - {nombre: periodo_ms, tipo: DINT}\n")
modelo.generar_fichero(modelo.cargar_planta(yaml_sin_impl), SIN_IMPL)

# --- 3a: XML ---
raiz = ET.parse(CELULA).getroot()
metodos = {el.get("NodeId"): el for el in raiz if el.tag == f"{N}UAMethod"}
inst = metodos.get("ns=1;s=PulsadorVerde.ControlarPulsador")
decl = metodos.get(inst.get("MethodDeclarationId")) if inst is not None else None
check("3a UAMethod en TPulsador (obligatorio) y en la instancia con MethodDeclarationId",
      len(metodos) == 2 and decl is not None and decl.get("ParentNodeId") ==
      next(el.get("NodeId") for el in raiz if el.get("BrowseName") == "1:TPulsador")
      and any(r.text == "i=78" for r in decl.iter(f"{N}Reference")))

# --- PLC emulado ---
srv = snap7.server.Server(); areas = {}
for a in (SrvArea.PE, SrvArea.PA, SrvArea.MK):
    b = (ctypes.c_ubyte * 4)(); srv.register_area(a, 0, b); areas[a] = b
srv.start_to("127.0.0.1", 102)
m0 = lambda: bool(areas[SrvArea.MK][0] & 1)


def lanzar(xml, log):
    return subprocess.Popen([sys.executable, "../servidor/servidor_opcua.py", "-m", xml,
                             "--plc-ip", "127.0.0.1", "--timeout-ms", "500"],
                            stdout=open(log, "w"), stderr=subprocess.STDOUT)


async def conectar():
    for _ in range(40):
        c = Client("opc.tcp://127.0.0.1:4840")
        try: await c.connect(); return c
        except Exception: await asyncio.sleep(0.25)
    raise RuntimeError("el servidor no responde")


async def llamar(pv, m, accion):
    try:
        return await pv.call_method(m, accion)
    except ua.UaStatusCodeError as e:
        return e.code


async def main():
    p = lanzar(CELULA, "logs/srv_faseB_3.log")
    try:
        c = await conectar()
        try:
            ns = await c.get_namespace_index(URI)
            pv = c.get_node(f"ns={ns};s=PulsadorVerde")
            m = await pv.get_child([f"{ns}:ControlarPulsador"])
            ent = await (await m.get_child(["0:InputArguments"])).read_value()
            sal = await (await m.get_child(["0:OutputArguments"])).read_value()
            check("3b argumentos visibles: accion (String) -> resultado (String)",
                  [(a.Name, a.DataType) for a in ent] == [("accion", ua.NodeId(12))]
                  and [(a.Name, a.DataType) for a in sal] == [("resultado", ua.NodeId(12))])
            await asyncio.sleep(1.0)
            r = await llamar(pv, m, "pulsar"); await asyncio.sleep(0.3)
            check("3c pulsar -> M0.0 = True y 'Pulsador pulsado'", m0() and r == "Pulsador pulsado")
            r = await llamar(pv, m, "soltar"); await asyncio.sleep(0.3)
            check("3c soltar -> M0.0 = False", not m0() and r == "Pulsador soltado")
            r = await llamar(pv, m, "saltar")
            check("3c argumento invalido -> BadInvalidArgument", r == ua.StatusCodes.BadInvalidArgument)
        finally:
            await c.disconnect()
    finally:
        p.terminate(); p.wait()
    log = open("logs/srv_faseB_3.log", encoding="utf-8", errors="replace").read()
    check("3d log: metodo del modelo enlazado, sin add_method",
          "Metodo ControlarPulsador enlazado a 1 instancias" in log and "sin implementacion" not in log)

    # Metodo declarado sin implementacion: aviso y el servidor sigue
    p = lanzar(SIN_IMPL, "logs/srv_faseB_3e.log")
    try:
        c = await conectar()
        try:
            ns = await c.get_namespace_index(URI)
            pv = c.get_node(f"ns={ns};s=PulsadorVerde")
            m = await pv.get_child([f"{ns}:ControlarPulsador"])
            await asyncio.sleep(1.0)
            r = await llamar(pv, m, "pulsar"); await asyncio.sleep(0.3)
            ok_pulsador = m0() and r == "Pulsador pulsado"
            await llamar(pv, m, "soltar")
        finally:
            await c.disconnect()
    finally:
        p.terminate(); p.wait()
    log = open("logs/srv_faseB_3e.log", encoding="utf-8", errors="replace").read()
    check("3e metodo sin implementacion: aviso con su ruta y el servidor arranca",
          "declara el metodo Parpadear (LedRojo/Parpadear) sin implementacion" in log
          and "Tabla de mapeo: 10 variables" in log and ok_pulsador)


try:
    asyncio.run(main())
finally:
    srv.stop()

print("=== RESUMEN ===")
for nombre, ok in res:
    print(f"  [{'OK   ' if ok else 'FALLO'}] {nombre}")
sys.exit(0 if all(ok for _, ok in res) else 1)
