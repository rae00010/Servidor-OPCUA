"""
Prueba del paso 7 sin hardware: robustez ante fallos del enlace con el PLC.
PLC S7 emulado en este proceso (snap7.server), servidor V5 como subproceso y
cliente asyncua suscrito que registra valor y StatusCode de cada notificacion.

Escenarios:
  A. El servidor arranca sin PLC y se conecta solo cuando el PLC aparece.
  B. Caida del PLC: las variables pasan a UncertainLastUsableValue.
  C. Durante la caida: escritura y metodo devuelven BadNoCommunication.
  D. El PLC vuelve tras un reinicio (memoria a cero): reconexion automatica.
  E. Fallo parcial: el area Q deja de responder pero I sigue; solo se degradan
     las variables de Q y una escritura en Q devuelve BadCommunicationError.
  F. El log no se inunda durante la caida.

Ejecutar desde la raiz del proyecto:
    py pruebas/test_paso7_fallos.py
"""
import _rutas  # noqa: F401  (servidor/ en la ruta; carpeta de trabajo tmp/)
import os; os.makedirs("logs", exist_ok=True)   # logs del servidor
import asyncio
import ctypes
import importlib.metadata as md
import logging
import subprocess
import sys
import time

import snap7
from snap7.type import SrvArea
from asyncua import Client, ua

logging.disable(logging.WARNING)
V3 = md.version("python-snap7").startswith("3")
SC = ua.StatusCodes
NOMBRES = {v: k for k, v in vars(SC).items() if isinstance(v, int)}


def nombre(sc):
    return NOMBRES.get(sc.value, hex(sc.value)) if sc else "Good"


# ---------------- PLC emulado ----------------
areas = {a: (bytearray(4) if V3 else (ctypes.c_ubyte * 4)())
         for a in (SrvArea.PE, SrvArea.PA, SrvArea.MK)}
srv = None


def plc_arrancar(registrar_q=True):
    global srv
    srv = snap7.server.Server()
    for a, b in areas.items():
        if a != SrvArea.PA or registrar_q:
            srv.register_area(a, 0, b)
    if V3:
        srv.port = 102
    srv.start_to("127.0.0.1", 102)


def plc_parar():
    srv.stop()


def setbit(a, by, bi, v):
    b = areas[a]
    b[by] = (b[by] | (1 << bi)) if v else (b[by] & ~(1 << bi) & 0xFF)


class Registro:
    """Guarda (instante, variable, valor, StatusCode) de cada notificacion."""

    def __init__(self):
        self.ev = []

    def datachange_notification(self, node, val, data):
        var = str(node.nodeid.Identifier).split(".")[-1]
        self.ev.append((time.perf_counter(), var, val, data.monitored_item.Value.StatusCode))

    def estados(self, desde=0):
        out = {}
        for t, n, v, sc in self.ev:
            if t >= desde:
                out[n] = (v, nombre(sc))
        return out

    async def esperar(self, cond, timeout):
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < timeout:
            if cond():
                return time.perf_counter() - t0
            await asyncio.sleep(0.05)
        return None


async def intentar(corrutina):
    try:
        await corrutina
        return "Good"
    except ua.UaStatusCodeError as e:
        return type(e).__name__


async def main():
    resultados, tiempos = [], {}
    srv_p = subprocess.Popen(
        [sys.executable, "../servidor/servidor_opcua.py", "-m", "../modelos/ledmodel_v5.xml",
         "--plc-ip", "127.0.0.1", "--timeout-ms", "500", "--reintento-max-s", "4"],
        stdout=open("logs/srv_paso7.log", "w"), stderr=subprocess.STDOUT)
    try:
        await asyncio.sleep(5)
        c = Client("opc.tcp://127.0.0.1:4840")
        await c.connect()
        ns = await c.get_namespace_index("urn:miservidor:ledRojo")
        n = lambda s: c.get_node(f"ns={ns};s={s}")
        reg = Registro()
        sub = await c.create_subscription(20, reg)
        await sub.subscribe_data_change([n(x) for x in (
            "CilindroA.Estado.Expandido", "CilindroA.Control.ExpandirSalida",
            "LedRojo.Value", "PulsadorVerde.Control.PulsadoRemoto")])
        await asyncio.sleep(0.5)
        bool_dv = lambda v: ua.DataValue(ua.Variant(v, ua.VariantType.Boolean))

        # ---- A ----
        st = (await n("CilindroA.Estado.Expandido").read_data_value(raise_on_bad_status=False)).StatusCode
        print("A. Servidor arrancado sin PLC. Expandido:", nombre(st))
        resultados.append(("A1 sin PLC: variables en BadNoCommunication", st.value == SC.BadNoCommunication))
        setbit(SrvArea.PE, 0, 5, 1)
        setbit(SrvArea.PA, 0, 3, 1)
        t0 = time.perf_counter()
        plc_arrancar()
        dt = await reg.esperar(lambda: reg.estados(t0).get("Expandido", (0, ""))[1] == "Good", 15)
        tiempos["conexion inicial"] = dt
        print("   PLC arrancado:", reg.estados(t0))
        resultados.append(("A2 conexion automatica cuando aparece el PLC", dt is not None))

        # ---- B ----
        print("\nB. Caida del PLC")
        t0 = time.perf_counter()
        plc_parar()
        dt = await reg.esperar(
            lambda: reg.estados(t0).get("Expandido", (0, ""))[1] == "UncertainLastUsableValue", 10)
        tiempos["deteccion de la caida"] = dt
        e = reg.estados(t0)
        print("  ", e)
        resultados.append(("B  degradacion a UncertainLastUsableValue conservando el valor",
                           dt is not None and e["Expandido"][0] is True))

        # ---- C ----
        print("\nC. Escritura y metodo durante la caida")
        leer = lambda s: n(s).read_data_value(raise_on_bad_status=False)
        led_antes = (await leer("LedRojo.Value")).Value.Value
        res_w = await intentar(n("LedRojo.Value").write_value(bool_dv(not led_antes)))
        led_despues = (await leer("LedRojo.Value")).Value.Value
        print(f"   escritura LedRojo -> {res_w}; valor antes {led_antes}, despues {led_despues}")
        resultados.append(("C1 escritura rechazada y valor restaurado",
                           res_w == "BadNoCommunication" and led_antes == led_despues))
        pv = n("PulsadorVerde")
        m = await pv.get_child([f"{ns}:ControlarPulsador"])
        res_m = await intentar(pv.call_method(m, "pulsar"))
        res_a = await intentar(pv.call_method(m, "saltar"))
        print(f"   metodo 'pulsar' -> {res_m}; metodo 'saltar' -> {res_a}")
        resultados.append(("C2 metodo durante la caida -> BadNoCommunication", res_m == "BadNoCommunication"))
        resultados.append(("C3 argumento invalido -> BadInvalidArgument", res_a == "BadInvalidArgument"))

        # ---- D ----
        print("\nD. El PLC vuelve tras un reinicio (memoria a cero)")
        await asyncio.sleep(6)
        for b in areas.values():
            for i in range(4):
                b[i] = 0
        t0 = time.perf_counter()
        plc_arrancar()
        dt = await reg.esperar(lambda: reg.estados(t0).get("Expandido", (0, ""))[1] == "Good", 15)
        tiempos["reconexion"] = dt
        e = reg.estados(t0)
        print("  ", e)
        resultados.append(("D1 reconexion automatica", dt is not None))
        resultados.append(("D2 valores frescos tras el reinicio (LED toma el valor del PLC)",
                           e.get("Expandido", (None,))[0] is False and e.get("Value", (None,))[0] is False))
        setbit(SrvArea.PE, 0, 5, 1)
        await asyncio.sleep(0.6)
        resultados.append(("D3 lecturas normales tras reconectar",
                           reg.estados(t0)["Expandido"] == (True, "Good")))
        res_m = await intentar(pv.call_method(m, "pulsar"))
        await asyncio.sleep(0.2)
        resultados.append(("D4 metodo vuelve a funcionar", res_m == "Good" and bool(areas[SrvArea.MK][0] & 1)))

        # ---- E ----
        print("\nE. Fallo parcial: el area Q deja de responder")
        plc_parar()
        plc_arrancar(registrar_q=False)
        await reg.esperar(lambda: False, 6)
        setbit(SrvArea.PE, 0, 5, 0)
        await asyncio.sleep(1.5)
        e = reg.estados(0)
        print("  ", e)
        resultados.append(("E1 variables de I siguen Good y actualizadas", e["Expandido"] == (False, "Good")))
        resultados.append(("E2 variables de Q degradadas", e["ExpandirSalida"][1] != "Good"))
        res_w = await intentar(n("LedRojo.Value").write_value(bool_dv(True)))
        print("   escritura LedRojo con Q sin respuesta ->", res_w)
        resultados.append(("E3 escritura fallida -> BadCommunicationError", res_w == "BadCommunicationError"))
        await c.disconnect()
    finally:
        srv_p.terminate()
        srv_p.wait()
        try:
            plc_parar()
        except Exception:
            pass

    log = open("logs/srv_paso7.log").read().splitlines()
    avisos = [l for l in log if " WARNING " in l or " ERROR " in l]
    print(f"\nF. Log del servidor: {len(log)} lineas, {len(avisos)} avisos o errores")
    resultados.append(("F  log sin inundar (menos de 40 avisos en toda la prueba)", len(avisos) < 40))

    print("\n=== TIEMPOS (orientativos: PLC emulado) ===")
    for k, v in tiempos.items():
        print(f"  {k}: {v:.2f} s" if v is not None else f"  {k}: no ocurrio")
    print("\n=== RESUMEN ===")
    for r, c_ in resultados:
        print(f"  [{'OK   ' if c_ else 'FALLO'}] {r}")
    print("\n--- avisos y cambios de enlace en el log ---")
    for l in log:
        if " WARNING " in l or " ERROR " in l or "restablecido" in l or "Conectado al PLC" in l:
            print("  " + l.split(" ", 2)[-1])


asyncio.run(main())
