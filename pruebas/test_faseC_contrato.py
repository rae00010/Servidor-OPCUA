"""Fase C, paso 2: contrato del driver v1.2 (parametros declarados).
Validacion sin PLC y conexion al PLC emulado (puerto 102) con los parametros declarados."""
import _rutas  # noqa: F401  (servidor/ en la ruta; carpeta de trabajo tmp/)
import asyncio, ctypes, sys
import snap7
from snap7.type import SrvArea
from asyncua import ua

import drivers
from driver_base import Driver, ParametroDriver, PuntoDriver
from driver_snap7 import DriverSnap7

res = []
def check(nombre, ok): res.append((nombre, bool(ok)))


def error_de(clase, valores):
    try:
        clase.validar_parametros(valores); return ""
    except ValueError as e:
        return str(e)


# --- declaracion ---
check("2a contrato v1.2: PARAMETROS y validar_parametros no abstractos",
      Driver.PARAMETROS == () and "validar_parametros" not in Driver.__abstractmethods__
      and "PARAMETROS" in (sys.modules["driver_base"].__doc__ or ""))
d = {x["nombre"]: x for x in drivers.describir()}
nombres = [p["nombre"] for p in d["snap7"]["parametros"]]
check("2a describir(): lista de disponibles() con parametros de snap7",
      list(d) == drivers.disponibles() and nombres == ["ip", "rack", "slot", "timeout_ms", "max_hueco"])
rack = next(p for p in d["snap7"]["parametros"] if p["nombre"] == "rack")
check("2a parametro declarado con tipo, defecto, limites y descripcion",
      rack["tipo"] == "entero" and rack["defecto"] == 0 and rack["maximo"] == 7 and rack["descripcion"])

# --- validacion ---
v = DriverSnap7.validar_parametros({})
check("2b sin valores: se aplican los defectos",
      v == {"ip": "192.168.0.1", "rack": 0, "slot": 1, "timeout_ms": 1000, "max_hueco": 8})
v = DriverSnap7.validar_parametros({"ip": " 10.0.0.5 ", "rack": "0", "slot": "2", "timeout_ms": "500",
                                    "fallos_max": 5})
check("2b texto de formulario convertido y fallos_max (comun) conservado",
      v["ip"] == "10.0.0.5" and v["slot"] == 2 and v["timeout_ms"] == 500 and v["fallos_max"] == 5)
e = error_de(DriverSnap7, {"rack": 9, "timeout_ms": "abc", "puerto": 1, "ip": ""})
check("2c varios errores a la vez, con el nombre del driver",
      all(s in e for s in ("'snap7'", "rack=9", "<= 7", "timeout_ms='abc'", "entero",
                           "parametro 'puerto' no existe", "ip=''", "vacio")))
check("2c entero con decimales rechazado", "entero" in error_de(DriverSnap7, {"slot": 1.5}))
check("2c bool no es entero", "entero" in error_de(DriverSnap7, {"rack": True}))
try:
    drivers.crear_driver("snap7", {"ip": "x", "puerto_ads": 851}); check("2c crear_driver con parametro ajeno", False)
except ValueError as ex:
    check("2c crear_driver con parametro ajeno -> ValueError", "puerto_ads" in str(ex))


class SinDeclarar(DriverSnap7):
    nombre = "sin_declarar"
    PARAMETROS = ()


check("2d driver sin PARAMETROS: se pasa tal cual (compatibilidad v1.1)",
      SinDeclarar.validar_parametros({"ip": "1.2.3.4", "x": 1}) == {"ip": "1.2.3.4", "x": 1})
p = ParametroDriver("modo", "opcion", "a", opciones=("a", "b"))
check("2d tipo opcion", p.convertir("b") == "b" and "una de" in error_de(
    type("D", (DriverSnap7,), {"nombre": "d", "PARAMETROS": (p,)}), {"modo": "c"}))

# --- conexion con los parametros declarados (PLC emulado) ---
srv = snap7.server.Server()
bufs = [(ctypes.c_ubyte * 4)() for _ in range(3)]
for a, b in zip((SrvArea.PE, SrvArea.PA, SrvArea.MK), bufs):
    srv.register_area(a, 0, b)
srv.start_to("127.0.0.1", 102)
bufs[0][0] = 0x20                     # I0.5


async def conectar():
    drv = drivers.crear_driver("snap7", {"ip": "127.0.0.1", "timeout_ms": "500", "fallos_max": 3})
    await drv.conectar()
    try:
        pd = PuntoDriver(clave="x", ruta="x", direccion=drv.validar_direccion(
            {"area": 0, "byte": 0, "bit": 5}, ua.VariantType.Boolean))
        valores, _ = await drv.leer(drv.planificar_lectura([pd]))
        return drv.timeout_ms == 500 and drv.conectado and valores.get("x") is True
    finally:
        await drv.desconectar()


try:
    check("2e crear_driver con parametros de formulario: conecta y lee I0.5", asyncio.run(conectar()))
finally:
    srv.stop()

print("=== RESUMEN ===")
for n, ok in res:
    print(f"  [{'OK   ' if ok else 'FALLO'}] {n}")
sys.exit(0 if all(ok for _, ok in res) else 1)
