"""Fase B, criterio de cierre: con el NodeSet generado desde planta_celula.yaml el
servidor construye la misma tabla de mapeo que con ledmodel_v5.xml (direcciones,
sentidos y metodos) y los NodeIds coinciden. PLC emulado en el puerto 102."""
import os; os.makedirs("logs", exist_ok=True)   # logs del servidor
import asyncio, ctypes, logging, re, subprocess, sys
import snap7
from snap7.type import SrvArea
from asyncua import Client
from asyncua.ua import NodeClass
import modelo
logging.disable(logging.WARNING)

res = []
def check(nombre, ok): res.append((nombre, bool(ok)))
URI = "urn:miservidor:ledRojo"
GENERADO = "modelo_faseB_celula.xml"
modelo.generar_fichero("planta_celula.yaml", GENERADO)

srv = snap7.server.Server()
bufs = [(ctypes.c_ubyte * 4)() for _ in range(3)]
for a, b in zip((SrvArea.PE, SrvArea.PA, SrvArea.MK), bufs):
    srv.register_area(a, 0, b)
srv.start_to("127.0.0.1", 102)


async def recorrer(c):
    """(ruta, NodeId, clase) de las variables Address y los metodos bajo Objects."""
    ns = await c.get_namespace_index(URI)
    address = None
    for t in await c.get_node("i=63").get_children():
        if (await t.read_browse_name()).Name == "Address" and t.nodeid.NamespaceIndex == ns:
            address = t.nodeid
    vistos = set()

    async def visitar(nodo, ruta):
        for h in await nodo.get_children():
            if h.nodeid.NamespaceIndex != ns:
                continue
            nombre = (await h.read_browse_name()).Name
            r = f"{ruta}/{nombre}" if ruta else nombre
            clase = await h.read_node_class()
            if clase == NodeClass.Variable:
                if await h.read_type_definition() == address:
                    vistos.add((r, h.nodeid.Identifier, "variable"))
            elif clase == NodeClass.Method:
                vistos.add((r, h.nodeid.Identifier, "metodo"))
            elif clase == NodeClass.Object:
                await visitar(h, r)

    await visitar(c.nodes.objects, "")
    return vistos


async def ejecutar(xml, log):
    p = subprocess.Popen([sys.executable, "serverOPCUA_MetodoTFGV5_asyncua.py", "-m", xml,
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
            nodos = await recorrer(c)
        finally:
            await c.disconnect()
    finally:
        p.terminate(); p.wait()
    texto = open(log, encoding="utf-8", errors="replace").read()
    tabla = set()
    for l in texto.splitlines():
        m = re.search(r"\s{2}(\S+(?: \(\w+\))?)\s+(OPC UA -> PLC|PLC -> OPC UA)\s+(\S+)$", l)
        if m:
            tabla.add((m[3], m[1], m[2]))
    metodos = sorted(re.findall(r"Metodo (\w+) enlazado a (\d+) instancias", texto))
    return tabla, metodos, nodos


async def main():
    t5, m5, n5 = await ejecutar("ledmodel_v5.xml", "logs/srv_faseB_cierre_v5.log")
    tg, mg, ng = await ejecutar(GENERADO, "logs/srv_faseB_cierre_gen.log")
    print(f"  ledmodel_v5.xml: {len(t5)} variables, metodos {m5}")
    print(f"  generado:        {len(tg)} variables, metodos {mg}")
    check("C1 tabla de mapeo con 10 variables en ambos", len(t5) == len(tg) == 10)
    check("C1 mismas direcciones y sentidos por variable", tg == t5)
    if tg != t5:
        print("   solo V5:", t5 - tg, "\n   solo generado:", tg - t5)
    check("C2 mismos metodos enlazados", mg == m5 == [("ControlarPulsador", "1")])
    check("C3 mismos NodeIds (variables Address y metodos)", ng == n5 and len(ng) == 11)
    if ng != n5:
        print("   solo V5:", n5 - ng, "\n   solo generado:", ng - n5)


try:
    asyncio.run(main())
finally:
    srv.stop()

print("=== RESUMEN ===")
for nombre, ok in res:
    print(f"  [{'OK   ' if ok else 'FALLO'}] {nombre}")
sys.exit(0 if all(ok for _, ok in res) else 1)
