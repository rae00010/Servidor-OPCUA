"""Fase B, paso 2: generador de NodeSet2 desde la descripcion de planta.
Comprueba el XML generado y arranca el servidor con el ejemplo del variador
(INT y REAL sin bit) contra el PLC emulado en el puerto 102."""
import os; os.makedirs("logs", exist_ok=True)   # logs del servidor
import asyncio, ctypes, logging, struct, subprocess, sys
import xml.etree.ElementTree as ET
import snap7
from snap7.type import SrvArea
from asyncua import Client, ua
import modelo
logging.disable(logging.WARNING)

res = []
def check(nombre, ok): res.append((nombre, bool(ok)))
VT = ua.VariantType
N = "{http://opcfoundation.org/UA/2011/03/UANodeSet.xsd}"

CELULA, VARIADOR = "modelo_faseB_celula.xml", "modelo_faseB_variador.xml"
modelo.generar_fichero("planta_celula.yaml", CELULA)
modelo.generar_fichero("planta_ejemplo_variador.yaml", VARIADOR)


def nodos(fichero):
    raiz = ET.parse(fichero).getroot()
    return raiz, {el.get("NodeId"): el for el in raiz if el.get("NodeId")}


def regla(el):
    return next((r.text for r in el.iter(f"{N}Reference") if r.get("ReferenceType") == "HasModellingRule"), None)


def propiedades(nodos_, nodeid):
    return {nodos_[r.text].get("BrowseName").split(":")[-1] for r in nodos_[nodeid].iter(f"{N}Reference")
            if r.get("ReferenceType") == "HasProperty" and r.get("IsForward") != "false"}


# --- 2a/2b: XML generado ---
raiz, nc = nodos(CELULA)
modelos = [m.get("ModelUri") for m in raiz.iter(f"{N}Model")]
check("2a XML bien formado con <Models ModelUri> y <NamespaceUris>",
      modelos == ["urn:miservidor:ledRojo"] and raiz.find(f"{N}NamespaceUris/{N}Uri").text == modelos[0])
address = next(el for el in raiz if el.tag == f"{N}UAVariableType" and el.get("BrowseName") == "1:Address")
reglas = {nc[r.text].get("BrowseName"): regla(nc[r.text]) for r in address.iter(f"{N}Reference")
          if r.get("ReferenceType") == "HasProperty"}
check("2a Address: area y byte obligatorias, bit y db opcionales",
      reglas == {"1:area": "i=78", "1:byte": "i=78", "1:bit": "i=80", "1:db": "i=80"})
tipos_obj = {el.get("BrowseName") for el in raiz if el.tag == f"{N}UAObjectType"}
check("2a ObjectTypes de la celula",
      tipos_obj == {"1:TCilindroDobleEfecto", "1:TCilindroSimpleEfecto", "1:TLed", "1:TPulsador"})
led = nc["ns=1;s=LedRojo.Value"]
check("2a LedRojo.Value: Boolean, AccessLevel 3, Q0.3",
      led.get("DataType") == "Boolean" and led.get("AccessLevel") == "3"
      and nc["ns=1;s=LedRojo.Value.bit"].find(f"{N}Value")[0].text == "3")
check("2a ContraerSalida: solo lectura (AccessLevel 1)",
      nc["ns=1;s=CilindroA.Control.ContraerSalida"].get("AccessLevel") == "1")

_, nv = nodos(VARIADOR)
check("2b variador: INT y REAL sin propiedad bit, DataType Int16/Float",
      propiedades(nv, "ns=1;s=Transportador1.Control.Consigna") == {"area", "db", "byte"}
      and propiedades(nv, "ns=1;s=Transportador1.Estado.Frecuencia") == {"area", "db", "byte"}
      and nv["ns=1;s=Transportador1.Control.Consigna"].get("DataType") == "Int16"
      and nv["ns=1;s=Transportador1.Estado.Frecuencia"].get("DataType") == "Float")
check("2b variador: BOOL con bit",
      propiedades(nv, "ns=1;s=Transportador1.Control.Marcha") == {"area", "byte", "bit"})

# --- PLC emulado ---
srv = snap7.server.Server(); areas = {}
for a in (SrvArea.PE, SrvArea.PA, SrvArea.MK):
    b = (ctypes.c_ubyte * 16)(); srv.register_area(a, 0, b); areas[a] = b
bdb = (ctypes.c_ubyte * 16)(); srv.register_area(SrvArea.DB, 10, bdb)
srv.start_to("127.0.0.1", 102)
I, M = areas[SrvArea.PE], areas[SrvArea.MK]


async def main():
    log = "logs/srv_faseB_2.log"
    p = subprocess.Popen([sys.executable, "serverOPCUA_MetodoTFGV5_asyncua.py", "-m", VARIADOR,
                          "--plc-ip", "127.0.0.1", "--timeout-ms", "500"],
                         stdout=open(log, "w"), stderr=subprocess.STDOUT)
    try:
        for _ in range(40):
            c = Client("opc.tcp://127.0.0.1:4840")
            try: await c.connect(); break
            except Exception: await asyncio.sleep(0.25)
        else:
            raise RuntimeError("el servidor no responde")
        try:
            ns = await c.get_namespace_index("urn:ujaen:planta:transporte")
            n = lambda s: c.get_node(f"ns={ns};s=Transportador1.{s}")
            # Lectura: REAL en DB10.4, INT en DB10.2, BOOL en I1.0
            for i, x in enumerate(struct.pack(">f", 49.5)): bdb[4 + i] = x
            for i, x in enumerate(struct.pack(">h", -1200)): bdb[2 + i] = x
            I[1] |= 0x01
            await asyncio.sleep(0.6)
            dv = await n("Estado.Frecuencia").read_data_value()
            check("2c lee Frecuencia = 49.5 como Float", dv.Value.Value == 49.5 and dv.Value.VariantType == VT.Float)
            dv = await n("Estado.Velocidad").read_data_value()
            check("2c lee Velocidad = -1200 como Int16", dv.Value.Value == -1200 and dv.Value.VariantType == VT.Int16)
            check("2c lee EnMarcha = True", await n("Estado.EnMarcha").read_value() is True)
            # Escritura: INT en DB10.0, BOOL en M10.0
            await n("Control.Consigna").write_value(ua.DataValue(ua.Variant(1500, VT.Int16)))
            await n("Control.Marcha").write_value(ua.DataValue(ua.Variant(True, VT.Boolean)))
            await asyncio.sleep(0.3)
            check("2d escribe Consigna = 1500 en DB10.0",
                  struct.unpack(">h", bytes(bdb[0:2]))[0] == 1500)
            check("2d escribe Marcha en M10.0", M[10] & 0x01)
            dv = await n("Control.Consigna").read_data_value()
            check("2d el cliente relee Consigna = 1500 como Int16",
                  dv.Value.Value == 1500 and dv.Value.VariantType == VT.Int16)
        finally:
            await c.disconnect()
    finally:
        p.terminate(); p.wait()
    texto = open(log, encoding="utf-8", errors="replace").read()
    check("2c el servidor arranca con el NodeSet generado (5 variables)",
          "Tabla de mapeo: 5 variables" in texto)


try:
    asyncio.run(main())
finally:
    srv.stop()

print("=== RESUMEN ===")
for nombre, ok in res:
    print(f"  [{'OK   ' if ok else 'FALLO'}] {nombre}")
sys.exit(0 if all(ok for _, ok in res) else 1)
