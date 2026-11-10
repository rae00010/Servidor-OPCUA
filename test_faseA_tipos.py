"""Fase A, paso 5: tipos BOOL, BYTE, WORD, INT, DINT y REAL en el driver Snap7.
Genera un NodeSet de prueba (ledmodel_v5.xml + objeto Tipos) y lo prueba contra el
PLC emulado en el puerto 102: lectura (I, M, DB), escritura (Q, M, DB) y plan de lectura."""
import os; os.makedirs("logs", exist_ok=True)   # logs del servidor
import asyncio, ctypes, logging, struct, subprocess, sys
import snap7
from snap7.type import SrvArea
from asyncua import Client, ua
from driver_snap7 import DriverSnap7
logging.disable(logging.WARNING)

res = []
def check(nombre, ok): res.append((nombre, bool(ok)))
VT = ua.VariantType
I, Q, M, DB = 0, 1, 2, 3

# nombre, DataType, escritura, area, byte, bit, db
VARIABLES = [
    ("L_BYTE", "Byte", False, I, 4, None, None),
    ("L_WORD", "UInt16", False, I, 5, None, None),        # direccion impar: valida en S7
    ("L_INT", "Int16", False, I, 8, None, None),
    ("L_DINT", "Int32", False, I, 10, None, None),
    ("L_INT_M", "Int16", False, M, 4, None, None),
    ("L_REAL", "Float", False, DB, 0, None, 10),
    ("L_BOOL", "Boolean", False, DB, 4, 1, 10),
    ("W_BYTE", "Byte", True, Q, 4, None, None),
    ("W_WORD", "UInt16", True, Q, 6, None, None),
    ("W_INT", "Int16", True, M, 8, None, None),
    ("W_DINT", "Int32", True, DB, 8, None, 10),
    ("W_REAL", "Float", True, M, 12, None, None),
    ("W_BOOL", "Boolean", True, DB, 6, 5, 10),
]
FMT = {"Byte": ">B", "UInt16": ">H", "Int16": ">h", "Int32": ">i", "Float": ">f"}
VTIPO = {"Byte": VT.Byte, "UInt16": VT.UInt16, "Int16": VT.Int16, "Int32": VT.Int32,
         "Float": VT.Float, "Boolean": VT.Boolean}


def propiedad(padre, nombre, valor, tipo="Int32"):
    return f'''    <UAVariable DataType="{tipo}" AccessLevel="1" BrowseName="1:{nombre}" ParentNodeId="ns=1;s={padre}" NodeId="ns=1;s={padre}.{nombre}">
        <DisplayName>{nombre}</DisplayName>
        <References>
            <Reference ReferenceType="HasTypeDefinition">i=68</Reference>
            <Reference ReferenceType="HasProperty" IsForward="false">ns=1;s={padre}</Reference>
        </References>
        <Value><uax:Int32>{valor}</uax:Int32></Value>
    </UAVariable>
'''


def generar_modelo(destino="modelo_tipos_faseA.xml"):
    xml = ['''    <UAObject BrowseName="1:Tipos" NodeId="ns=1;s=Tipos">
        <DisplayName>Tipos</DisplayName>
        <References>
            <Reference ReferenceType="HasTypeDefinition">i=58</Reference>
            <Reference IsForward="false" ReferenceType="Organizes">i=85</Reference>
        </References>
    </UAObject>
''']
    for nombre, tipo, esc, area, byte, bit, db in VARIABLES:
        nid = f"Tipos.{nombre}"
        props = {"area": area, "byte": byte}
        if bit is not None: props["bit"] = bit
        if db is not None: props["db"] = db
        refs = "".join(f'            <Reference ReferenceType="HasProperty">ns=1;s={nid}.{k}</Reference>\n'
                       for k in props)
        acceso = 3 if esc else 1
        xml.append(f'''    <UAVariable DataType="{tipo}" AccessLevel="{acceso}" UserAccessLevel="{acceso}" BrowseName="1:{nombre}" ParentNodeId="ns=1;s=Tipos" NodeId="ns=1;s={nid}">
        <DisplayName>{nombre}</DisplayName>
        <References>
            <Reference ReferenceType="HasTypeDefinition">ns=1;i=2003</Reference>
            <Reference IsForward="false" ReferenceType="HasComponent">ns=1;s=Tipos</Reference>
{refs}        </References>
    </UAVariable>
''')
        for k, v in props.items():
            xml.append(propiedad(nid, k, v, "ns=1;i=3001" if k == "area" else "Int32"))
    t = open("ledmodel_v5.xml", encoding="utf-8").read()
    t = t.replace("</UANodeSet>", "".join(xml) + "</UANodeSet>")
    open(destino, "w", encoding="utf-8").write(t)
    return destino


# --- Validacion y texto de direcciones (sin PLC) ---
d = DriverSnap7(ip="127.0.0.1")
check("5a validar: REAL en DB -> DB10.DBD0 (REAL)",
      d.texto_direccion(d.validar_direccion({"area": 3, "byte": 0, "db": 10}, VT.Float)) == "DB10.DBD0 (REAL)")
check("5a validar: WORD en I sin bit -> IW5",
      d.texto_direccion(d.validar_direccion({"area": 0, "byte": 5}, VT.UInt16)) == "IW5")
check("5a validar: BOOL sigue con formato I0.5",
      d.texto_direccion(d.validar_direccion({"area": 0, "byte": 0, "bit": 5}, VT.Boolean)) == "I0.5")
for props, tipo, etiqueta in [({"area": 0, "byte": 0, "bit": 3}, VT.Int16, "bit<>0 en INT"),
                              ({"area": 0, "byte": 0}, VT.Double, "tipo Double no soportado"),
                              ({"area": 0, "byte": 0}, VT.Boolean, "BOOL sin bit")]:
    try:
        d.validar_direccion(props, tipo); check(f"5b rechaza {etiqueta}", False)
    except ValueError:
        check(f"5b rechaza {etiqueta}", True)

# --- PLC emulado ---
srv = snap7.server.Server(); areas = {}
for a in (SrvArea.PE, SrvArea.PA, SrvArea.MK):
    b = (ctypes.c_ubyte * 16)(); srv.register_area(a, 0, b); areas[a] = b
bdb = (ctypes.c_ubyte * 16)(); srv.register_area(SrvArea.DB, 10, bdb)
AREA = {I: areas[SrvArea.PE], Q: areas[SrvArea.PA], M: areas[SrvArea.MK], DB: bdb}
srv.start_to("127.0.0.1", 102)

def poner(nombre, valor):
    _, tipo, _, area, byte, bit, _ = next(v for v in VARIABLES if v[0] == nombre)
    buf = AREA[area]
    if tipo == "Boolean":
        buf[byte] = (buf[byte] | (1 << bit)) if valor else (buf[byte] & ~(1 << bit))
    else:
        for i, x in enumerate(struct.pack(FMT[tipo], valor)): buf[byte + i] = x

def leer_plc(nombre):
    _, tipo, _, area, byte, bit, _ = next(v for v in VARIABLES if v[0] == nombre)
    buf = AREA[area]
    if tipo == "Boolean":
        return bool(buf[byte] >> bit & 1)
    return struct.unpack(FMT[tipo], bytes(buf[byte:byte + struct.calcsize(FMT[tipo])]))[0]

f32 = lambda x: struct.unpack(">f", struct.pack(">f", x))[0]


async def main():
    modelo = generar_modelo()
    p = subprocess.Popen([sys.executable, "serverOPCUA_MetodoTFGV5_asyncua.py", "-m", modelo,
                          "--plc-ip", "127.0.0.1", "--timeout-ms", "500"],
                         stdout=open("logs/srv_faseA_5.log", "w"), stderr=subprocess.STDOUT)
    try:
        for _ in range(40):
            c = Client("opc.tcp://127.0.0.1:4840")
            try: await c.connect(); break
            except Exception: await asyncio.sleep(0.25)
        else:
            raise RuntimeError("el servidor no responde")
        try:
            ns = await c.get_namespace_index("urn:miservidor:ledRojo")
            n = lambda s: c.get_node(f"ns={ns};s=Tipos.{s}")

            # Lectura: dos juegos de valores, incluidos limites y negativos
            juegos = [{"L_BYTE": 255, "L_WORD": 65535, "L_INT": -32768, "L_DINT": -2147483648,
                       "L_INT_M": -1, "L_REAL": -1234.5, "L_BOOL": True},
                      {"L_BYTE": 0, "L_WORD": 0x1234, "L_INT": 32767, "L_DINT": 2147483647,
                       "L_INT_M": 12345, "L_REAL": f32(3.14159), "L_BOOL": False}]
            for j, juego in enumerate(juegos, 1):
                for k, v in juego.items(): poner(k, v)
                await asyncio.sleep(0.6)
                for k, v in juego.items():
                    dv = await n(k).read_data_value()
                    tipo = next(x[1] for x in VARIABLES if x[0] == k)
                    check(f"5c lectura {j} {k} = {v}", dv.Value.Value == v and dv.Value.VariantType == VTIPO[tipo]
                          and dv.StatusCode.is_good())

            # Escritura: el cliente escribe y se comprueban los bytes del PLC
            AREA[Q][5] = 0xAA            # byte entre QB4 y QW6: no debe tocarse
            escrituras = {"W_BYTE": 200, "W_WORD": 54321, "W_INT": -20000, "W_DINT": -123456789,
                          "W_REAL": f32(-0.15625e2), "W_BOOL": True}
            for k, v in escrituras.items():
                tipo = next(x[1] for x in VARIABLES if x[0] == k)
                await n(k).write_value(ua.DataValue(ua.Variant(v, VTIPO[tipo])))
            await asyncio.sleep(0.3)
            for k, v in escrituras.items():
                check(f"5d escritura {k} = {v} llega al PLC", leer_plc(k) == v)
            check("5d escritura de varios bytes no toca los vecinos (QB5)", AREA[Q][5] == 0xAA)
            AREA[DB][6] |= 0x01                        # otro bit del mismo byte que W_BOOL
            await n("W_BOOL").write_value(ua.DataValue(ua.Variant(False, VT.Boolean)))
            await asyncio.sleep(0.3)
            check("5d BOOL mantiene RMW (DBX6.0 intacto)", AREA[DB][6] & 0x01 and not leer_plc("W_BOOL"))

            # Revision: INT y REAL escritos por el cliente conservan valor y tipo
            # en el espacio de direcciones (publicar), y tambien al degradar y restaurar
            for k, v, vt in (("W_INT", -20000, VT.Int16), ("W_REAL", f32(-15.625), VT.Float)):
                dv = await n(k).read_data_value()
                check(f"5f {k} escrito: el cliente relee {v} como {vt.name}",
                      dv.Value.Value == v and dv.Value.VariantType == vt and dv.StatusCode.is_good())
            srv.stop(); await asyncio.sleep(3)          # PLC caido
            for k, v, vt in (("L_INT", 32767, VT.Int16), ("L_REAL", f32(3.14159), VT.Float)):
                dv = await n(k).read_data_value(raise_on_bad_status=False)
                check(f"5g degradar {k}: Uncertain con {v} como {vt.name}",
                      dv.StatusCode.value == ua.StatusCodes.UncertainLastUsableValue
                      and dv.Value.Value == v and dv.Value.VariantType == vt)
            for k, nuevo, previo, vt in (("W_INT", 111, -20000, VT.Int16),
                                         ("W_REAL", 2.5, f32(-15.625), VT.Float)):
                try:
                    await n(k).write_value(ua.DataValue(ua.Variant(nuevo, vt))); rechazada = False
                except ua.UaStatusCodeError:
                    rechazada = True
                dv = await n(k).read_data_value(raise_on_bad_status=False)
                check(f"5h restaurar {k}: escritura rechazada y vuelve a {previo} como {vt.name}",
                      rechazada and dv.Value.Value == previo and dv.Value.VariantType == vt)
        finally:
            await c.disconnect()
    finally:
        p.terminate(); p.wait()
    log = open("logs/srv_faseA_5.log").read()
    check("5e tabla de mapeo: 10 + 13 variables", "Tabla de mapeo: 23 variables" in log)
    check("5e plan de lectura: 4 peticiones (I, Q, M, DB10)", "Plan de lectura: 4 peticiones por ciclo" in log)


try:
    asyncio.run(main())
finally:
    srv.stop()
print("=== RESUMEN ===")
for n_, ok in res:
    print(f"  [{'OK   ' if ok else 'FALLO'}] {n_}")
sys.exit(0 if res and all(ok for _, ok in res) else 1)
