"""Fase A, pasos 1 y 2: contrato del driver y fabrica de drivers (sin PLC)."""
import _rutas  # noqa: F401  (servidor/ en la ruta; carpeta de trabajo tmp/)
import inspect, sys

from driver_base import Driver
from driver_snap7 import DriverSnap7
import drivers

res = []
def check(nombre, ok): res.append((nombre, bool(ok)))

# --- Paso 1: contrato ---
METODOS = ["conectar", "desconectar", "conectado", "validar_direccion", "texto_direccion",
           "planificar_lectura", "leer", "escribir"]
check("1a el contrato declara los 8 metodos abstractos",
      set(Driver.__abstractmethods__) == set(METODOS))
check("1b DriverSnap7 implementa todos (instanciable)",
      not getattr(DriverSnap7, "__abstractmethods__", set()) and DriverSnap7(ip="127.0.0.1"))
check("1c la cabecera documenta cada metodo",
      all(m in (inspect.getmodule(Driver).__doc__ or "") for m in METODOS))

class Incompleto(Driver):
    nombre = "incompleto"
    async def conectar(self): pass
try:
    Incompleto(); check("1d driver incompleto no instanciable", False)
except TypeError:
    check("1d driver incompleto no instanciable", True)

# --- Paso 2: fabrica ---
d = drivers.crear_driver("snap7", {"ip": "10.0.0.1", "rack": 0, "slot": 1, "timeout_ms": 500})
check("2a crear_driver('snap7') con parametros", isinstance(d, DriverSnap7)
      and d.ip == "10.0.0.1" and d.timeout_ms == 500 and d.nombre == "snap7")
try:
    drivers.crear_driver("beckhoff", {}); check("2b nombre desconocido -> error con la lista", False)
except ValueError as e:
    check("2b nombre desconocido -> error con la lista", "beckhoff" in str(e) and "snap7" in str(e))
try:
    drivers.crear_driver("snap7", {"ip": "x", "puerto_ads": 851}); check("2c parametro invalido", False)
except ValueError as e:
    check("2c parametro invalido -> ValueError que nombra el driver", "snap7" in str(e))

print("=== RESUMEN ===")
for n, ok in res:
    print(f"  [{'OK   ' if ok else 'FALLO'}] {n}")
sys.exit(0 if all(ok for _, ok in res) else 1)
