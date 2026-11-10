"""Prueba de la V5 sin hardware: PLC S7 emulado con snap7.server + cliente asyncua.
Ejecutar en la carpeta del servidor, los drivers y el modelo. Necesita poder abrir el puerto 102."""
import os; os.makedirs("logs", exist_ok=True)   # logs del servidor
import asyncio, subprocess, time, sys, ctypes, logging, importlib.metadata as md
import snap7
from snap7.type import SrvArea
from asyncua import Client, ua
logging.disable(logging.WARNING)
V = md.version("python-snap7"); print("python-snap7", V)

srv = snap7.server.Server(); areas = {}
for a in (SrvArea.PE, SrvArea.PA, SrvArea.MK):
    b = bytearray(4) if V.startswith("3") else (ctypes.c_ubyte * 4)()   # 3.x copia los ctypes
    srv.register_area(a, 0, b); areas[a] = b
if V.startswith("3"): srv.port = 102
srv.start_to("127.0.0.1", 102)
def setbit(a, by, bi, v):
    b = areas[a]; b[by] = (b[by] | (1 << bi)) if v else (b[by] & ~(1 << bi))
def getbit(a, by, bi): return bool(areas[a][by] >> bi & 1)

setbit(SrvArea.PA, 0, 3, 1)                      # LedRojo encendido en el PLC al arrancar
p = subprocess.Popen([sys.executable, "serverOPCUA_MetodoTFGV5_asyncua.py", "-m", "ledmodel_v5.xml",
                      "--plc-ip", "127.0.0.1", "--timeout-ms", "500"],
                     stdout=open("logs/srv.log", "w"), stderr=subprocess.STDOUT)
class H:
    def __init__(s): s.ev = []
    def datachange_notification(s, node, val, data): s.ev.append((str(node.nodeid.Identifier), val))

async def main():
    await asyncio.sleep(5)
    c = Client("opc.tcp://127.0.0.1:4840"); await c.connect()
    ns = await c.get_namespace_index("urn:miservidor:ledRojo")
    n = lambda s: c.get_node(f"ns={ns};s={s}")
    h = H(); sub = await c.create_subscription(20, h)
    await sub.subscribe_data_change([n(v) for v in ["CilindroA.Estado.Expandido", "CilindroB.Control.ExpandirSalida",
        "LedRojo.Value", "PulsadorVerde.Estado.Pulsado", "PulsadorVerde.Control.PulsadoRemoto"]])
    await asyncio.sleep(1); print("[0] Valores iniciales:", dict(h.ev))

    h.ev.clear(); setbit(SrvArea.PE, 0, 5, 1); setbit(SrvArea.PA, 0, 2, 1); await asyncio.sleep(1)
    print("[1] I0.5:=1, Q0.2:=1 ->", h.ev)

    await n("LedRojo.Value").write_value(ua.DataValue(ua.Variant(False, ua.VariantType.Boolean))); await asyncio.sleep(0.3)
    print("[2] LedRojo=False -> Q0.3:", getbit(SrvArea.PA, 0, 3), "| Q0.2 intacto:", getbit(SrvArea.PA, 0, 2))

    try:
        await n("CilindroA.Estado.Expandido").write_value(ua.DataValue(ua.Variant(False, ua.VariantType.Boolean))); print("[3] sensor escrito (MAL)")
    except ua.UaStatusCodeError as e: print("[3] escritura en sensor rechazada:", type(e).__name__)

    pv = n("PulsadorVerde"); m = await pv.get_child([f"{ns}:ControlarPulsador"])
    r = await pv.call_method(m, "pulsar"); await asyncio.sleep(0.3); m1 = getbit(SrvArea.MK, 0, 0)
    await pv.call_method(m, "soltar"); await asyncio.sleep(0.3)
    print("[4] metodo:", r, "| M0.0 tras pulsar:", m1, "tras soltar:", getbit(SrvArea.MK, 0, 0))

    print("[5] PLC caido: se detiene el emulador")
    srv.stop(); await asyncio.sleep(3)
    t = time.perf_counter()
    try:
        await n("LedRojo.Value").write_value(ua.DataValue(ua.Variant(True, ua.VariantType.Boolean))); r = "Good"
    except ua.UaStatusCodeError as e: r = type(e).__name__
    print(f"    escritura durante la caida -> {r} en {time.perf_counter()-t:.2f} s (el servidor sigue respondiendo)")
    await c.disconnect()

try: asyncio.run(main())
finally:
    p.terminate(); p.wait()
    print("\n--- log del servidor ---")
    print("".join(l for l in open("logs/srv.log") if " WARNING " in l or " ERROR " in l or "PLC" in l)[-3000:])
