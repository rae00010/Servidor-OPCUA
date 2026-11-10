"""Fase A, pasos 3 y 4: seleccion del driver por nombre y servidor independiente
de la URI y de los NodeIds numericos del modelo. PLC emulado en el puerto 102."""
import _rutas  # noqa: F401  (servidor/ en la ruta; carpeta de trabajo tmp/)
import os; os.makedirs("logs", exist_ok=True)   # logs del servidor
import asyncio, ctypes, logging, re, subprocess, sys, time
import snap7
from snap7.type import SrvArea
from asyncua import Client, ua
logging.disable(logging.WARNING)

SERVIDOR = "../servidor/servidor_opcua.py"
URI_ALT = "urn:otra:planta:prueba"
DESPLAZAMIENTO = 10000
res = []
def check(nombre, ok): res.append((nombre, bool(ok)))


def generar_modelo_alterado(origen="../modelos/ledmodel_v5.xml", destino="modelo_alterado_faseA.xml"):
    """Copia del modelo con otra URI y todos los NodeIds numericos ns=1;i=N desplazados."""
    t = open(origen, encoding="utf-8").read()
    t = t.replace("urn:miservidor:ledRojo", URI_ALT)
    t = re.sub(r"ns=1;i=(\d+)", lambda m: f"ns=1;i={int(m.group(1)) + DESPLAZAMIENTO}", t)
    open(destino, "w", encoding="utf-8").write(t)
    return destino


URI_SENUELO = "urn:otro:vendedor:senuelo"
SENUELO = '''    <UAVariableType BrowseName="1:Address" ValueRank="-1" NodeId="ns=1;i=2003">
        <DisplayName>Address</DisplayName>
        <References>
            <Reference ReferenceType="HasSubtype" IsForward="false">i=63</Reference>
        </References>
    </UAVariableType>
    <UAObjectType BrowseName="1:TPulsador" NodeId="ns=1;i=1014">
        <DisplayName>TPulsador</DisplayName>
        <References>
            <Reference ReferenceType="HasSubtype" IsForward="false">i=58</Reference>
        </References>
    </UAObjectType>
'''


def generar_modelo_trampa(origen, destino):
    """
    El modelo (ModelUri = URI_ALT) pasa a ser la 2a URI de <NamespaceUris>. La 1a es
    de otro vendedor y define Address y TPulsador senuelo con los NodeIds de la V5.
    """
    t = open(origen, encoding="utf-8").read()
    t = t.replace("ns=1;", "ns=2;").replace('BrowseName="1:', 'BrowseName="2:')
    t = t.replace(f"<Uri>{URI_ALT}</Uri>", f"<Uri>{URI_SENUELO}</Uri>\n        <Uri>{URI_ALT}</Uri>")
    t = t.replace("</UANodeSet>", SENUELO + "</UANodeSet>")
    open(destino, "w", encoding="utf-8").write(t)
    return destino


srv = snap7.server.Server(); areas = {}
for a in (SrvArea.PE, SrvArea.PA, SrvArea.MK):
    b = (ctypes.c_ubyte * 4)(); srv.register_area(a, 0, b); areas[a] = b
srv.start_to("127.0.0.1", 102)
def setbit(a, by, bi, v):
    b = areas[a]; b[by] = (b[by] | (1 << bi)) if v else (b[by] & ~(1 << bi))
def getbit(a, by, bi): return bool(areas[a][by] >> bi & 1)


def lanzar(modelo, log, *extra):
    return subprocess.Popen([sys.executable, SERVIDOR, "-m", modelo, "--plc-ip", "127.0.0.1",
                             "--timeout-ms", "500", *extra],
                            stdout=open(log, "w"), stderr=subprocess.STDOUT)


async def conectar_cliente(intentos=40):
    for _ in range(intentos):
        c = Client("opc.tcp://127.0.0.1:4840")
        try:
            await c.connect(); return c
        except Exception:
            await asyncio.sleep(0.25)
    raise RuntimeError("el servidor no responde")


async def recorrido(c, uri, etiqueta):
    """Lectura de una entrada, escritura del LED y metodo ControlarPulsador."""
    ns = await c.get_namespace_index(uri)
    n = lambda s: c.get_node(f"ns={ns};s={s}")
    await asyncio.sleep(1.0)
    setbit(SrvArea.PE, 0, 5, 1); await asyncio.sleep(0.6)
    check(f"{etiqueta}: lee I0.5 = True", await n("CilindroA.Estado.Expandido").read_value() is True)
    setbit(SrvArea.PE, 0, 5, 0); await asyncio.sleep(0.6)
    check(f"{etiqueta}: lee I0.5 = False", await n("CilindroA.Estado.Expandido").read_value() is False)
    await n("LedRojo.Value").write_value(ua.DataValue(ua.Variant(True, ua.VariantType.Boolean)))
    await asyncio.sleep(0.3)
    check(f"{etiqueta}: escribe LedRojo -> Q0.3", getbit(SrvArea.PA, 0, 3))
    pv = n("PulsadorVerde"); m = await pv.get_child([f"{ns}:ControlarPulsador"])
    await pv.call_method(m, "pulsar"); await asyncio.sleep(0.3)
    m1 = getbit(SrvArea.MK, 0, 0)
    await pv.call_method(m, "soltar"); await asyncio.sleep(0.3)
    check(f"{etiqueta}: metodo ControlarPulsador -> M0.0", m1 and not getbit(SrvArea.MK, 0, 0))


async def caso(modelo, uri, etiqueta, log, *extra):
    p = lanzar(modelo, log, *extra)
    try:
        c = await conectar_cliente()
        try:
            await recorrido(c, uri, etiqueta)
        finally:
            await c.disconnect()
    finally:
        p.terminate(); p.wait()
    return open(log).read()


async def main():
    # Paso 3: seleccion por nombre
    log = await caso("../modelos/ledmodel_v5.xml", "urn:miservidor:ledRojo", "3a --driver snap7",
                     "logs/srv_faseA_3.log", "--driver", "snap7")
    check("3b log: tabla de mapeo con driver snap7", "Tabla de mapeo: 10 variables (driver snap7)" in log)
    r = subprocess.run([sys.executable, SERVIDOR, "-m", "../modelos/ledmodel_v5.xml", "--driver", "inexistente"],
                       capture_output=True, text=True, timeout=30)
    check("3c --driver inexistente: termina con error que lista los disponibles",
          r.returncode != 0 and "inexistente" in r.stderr and "snap7" in r.stderr)

    # Paso 4: modelo con otra URI y otros NodeIds numericos
    alt = generar_modelo_alterado()
    log = await caso(alt, URI_ALT, "4a modelo alterado", "logs/srv_faseA_4.log")
    check("4b log: tabla de mapeo completa con el modelo alterado",
          "Tabla de mapeo: 10 variables" in log)
    # Fase B, paso 3: el metodo lo declara el modelo y se enlaza por BrowseName
    check("4c log: metodo del modelo enlazado a su instancia",
          "Metodo ControlarPulsador enlazado a 1 instancias" in log)
    sin_address = "modelo_sin_address_faseA.xml"
    open(sin_address, "w", encoding="utf-8").write(
        open(alt, encoding="utf-8").read().replace('BrowseName="1:Address"', 'BrowseName="1:Otra"'))
    try:
        r = subprocess.run([sys.executable, SERVIDOR, "-m", sin_address, "--plc-ip", "127.0.0.1"],
                           capture_output=True, text=True, timeout=30)
        salida = r.stdout + r.stderr
        check("4d modelo sin Address: no arranca y lo explica",
              r.returncode != 0 and "no define el VariableType Address" in salida)
    except subprocess.TimeoutExpired:
        check("4d modelo sin Address: no arranca y lo explica", False)

    # Revision: URI desde ModelUri y tipos solo en el espacio de nombres del modelo
    trampa = generar_modelo_trampa(alt, "modelo_trampa_faseA.xml")
    log = await caso(trampa, URI_ALT, "4e modelo con URI ajena delante", "logs/srv_faseA_4e.log")
    linea = next((l for l in log.splitlines() if "Modelo urn:" in l), "")
    m = re.search(rf"Modelo {re.escape(URI_ALT)} \(ns=(\d+)\)", linea)
    check("4f URI tomada de ModelUri, no de la 1a NamespaceUri", m)
    ns = m.group(1) if m else "?"
    check("4f Address y metodo del modelo, no los senuelos",
          f"Address=ns={ns};i=12003" in linea and "Metodo ControlarPulsador enlazado a 1 instancias" in log)

    def no_arranca(modelo, texto):
        try:
            r = subprocess.run([sys.executable, SERVIDOR, "-m", modelo, "--plc-ip", "127.0.0.1"],
                               capture_output=True, text=True, timeout=30)
            return r.returncode != 0 and texto in r.stdout + r.stderr
        except subprocess.TimeoutExpired:
            return False

    sin_addr_trampa = "modelo_trampa_sin_address_faseA.xml"
    open(sin_addr_trampa, "w", encoding="utf-8").write(
        open(trampa, encoding="utf-8").read().replace('BrowseName="2:Address"', 'BrowseName="2:Otra"'))
    check("4g Address solo en otro espacio de nombres: no arranca",
          no_arranca(sin_addr_trampa, "no define el VariableType Address"))
    sin_models = "modelo_sin_models_faseA.xml"
    open(sin_models, "w", encoding="utf-8").write(
        re.sub(r"<Models>.*?</Models>", "", open(alt, encoding="utf-8").read(), flags=re.S))
    check("4h NodeSet sin <Models>: no arranca y lo explica", no_arranca(sin_models, "no declara <Models>"))


try:
    asyncio.run(main())
finally:
    srv.stop()
print("=== RESUMEN ===")
for n_, ok in res:
    print(f"  [{'OK   ' if ok else 'FALLO'}] {n_}")
sys.exit(0 if res and all(ok for _, ok in res) else 1)
