"""
Registro de drivers downstream del estandar.

Cada protocolo se identifica por un nombre. Anadir un protocolo consiste en
implementar driver_base.Driver y registrarlo aqui; el servidor lo selecciona
por nombre con crear_driver().
"""

from driver_base import Driver
from driver_snap7 import DriverSnap7

DRIVERS: dict[str, type[Driver]] = {
    "snap7": DriverSnap7,
}


def disponibles() -> list[str]:
    return sorted(DRIVERS)


def clase_driver(nombre: str) -> type[Driver]:
    clase = DRIVERS.get(nombre)
    if clase is None:
        raise ValueError(f"Driver '{nombre}' desconocido. Disponibles: {', '.join(disponibles())}")
    return clase


def describir() -> list[dict]:
    """Drivers disponibles y sus parametros declarados (para la interfaz)."""
    return [{"nombre": n, "descripcion": DRIVERS[n].descripcion,
             "parametros": [p.a_dict() for p in DRIVERS[n].PARAMETROS]} for n in disponibles()]


def crear_driver(nombre: str, parametros: dict | None = None) -> Driver:
    """
    Instancia el driver 'nombre' con sus parametros especificos
    (diccionario de argumentos por palabra clave de su constructor).
    """
    clase = clase_driver(nombre)
    parametros = clase.validar_parametros(parametros)     # contrato v1.2
    try:
        return clase(**parametros)
    except TypeError as e:
        raise ValueError(f"Parametros no validos para el driver '{nombre}': {e}") from e
