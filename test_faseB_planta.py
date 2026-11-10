"""Fase B, paso 1: descripcion de planta en YAML y direcciones en texto del driver (sin PLC)."""
import sys

from asyncua import ua

from driver_base import Driver
from driver_snap7 import DriverSnap7
from modelo import ErrorPlanta, cargar_planta

res = []
def check(nombre, ok): res.append((nombre, bool(ok)))

CELULA = open("planta_celula.yaml", encoding="utf-8").read()


def errores_de(texto):
    try:
        cargar_planta(texto)
        return []
    except ErrorPlanta as e:
        return e.errores


def rechaza(nombre, texto, *fragmentos):
    errs = errores_de(texto)
    ok = bool(errs) and all(any(f in e for e in errs) for f in fragmentos)
    if not ok:
        print(f"  {nombre}: {errs}")
    check(nombre, ok)


# --- 1a/1b: los dos ejemplos ---
p = cargar_planta("planta_celula.yaml")
rutas = {(i.nombre, r): d for i in p.instancias for r, d in i.direcciones.items()}
check("1a celula: uri, driver, 4 tipos, 4 instancias, 10 variables",
      p.uri == "urn:miservidor:ledRojo" and p.driver == "snap7" and len(p.tipos) == 4
      and len(p.instancias) == 4 and len(rutas) == 10)
check("1a celula: LedRojo/Value = Q0.3 y PulsadoRemoto = M0.0",
      rutas[("LedRojo", "Value")] == {"area": 1, "byte": 0, "bit": 3}
      and rutas[("PulsadorVerde", "Control/PulsadoRemoto")] == {"area": 2, "byte": 0, "bit": 0})
met = p.tipos["TPulsador"].metodos
check("1a celula: ControlarPulsador con entrada accion y salida resultado (STRING)",
      len(met) == 1 and met[0].nombre == "ControlarPulsador"
      and [(a.nombre, a.tipo) for a in met[0].entradas] == [("accion", "STRING")]
      and [(a.nombre, a.tipo) for a in met[0].salidas] == [("resultado", "STRING")])
check("1a celula: grupos y sentidos del tipo",
      p.tipos["TCilindroDobleEfecto"].grupos == ["Control", "Estado"]
      and [v.escritura for v in p.tipos["TLed"].variables] == [True])

v = cargar_planta("planta_ejemplo_variador.yaml")
d = v.instancias[0].direcciones
check("1b variador: INT y REAL en DB sin bit",
      d["Control/Consigna"] == {"area": 3, "db": 10, "byte": 0}
      and d["Estado/Frecuencia"] == {"area": 3, "db": 10, "byte": 4}
      and {x.ruta: x.tipo for x in v.tipos["TVariador"].variables}["Estado/Frecuencia"] == "REAL")

# --- 1c: interpretar_direccion en el contrato ---
I = DriverSnap7.interpretar_direccion
B, INT, REAL = ua.VariantType.Boolean, ua.VariantType.Int16, ua.VariantType.Float
check("1c snap7: I0.5, M20 (INT), DB10.4 (REAL), DB10.0.1 (BOOL)",
      I("I0.5", B) == {"area": 0, "byte": 0, "bit": 5} and I("M20", INT) == {"area": 2, "byte": 20}
      and I("DB10.4", REAL) == {"area": 3, "db": 10, "byte": 4}
      and I("DB10.0.1", B) == {"area": 3, "db": 10, "byte": 0, "bit": 1})
casos_malos = [("Q0", B, "necesita bit"), ("DB10.2", B, "necesita bit"), ("M20.0", INT, "no lleva bit"),
               ("X0.0", B, "no reconocida"), ("Q0.8", B, "fuera de rango"), ("", B, "no reconocida")]
malos = []
for texto, tipo, frag in casos_malos:
    try:
        I(texto, tipo); malos.append(texto)
    except ValueError as e:
        if frag not in str(e):
            malos.append(f"{texto}: {e}")
check("1c snap7: direcciones invalidas rechazadas con mensaje claro", not malos)
if malos:
    print("  ", malos)
check("1c contrato: no abstracto y la base lo rechaza",
      "interpretar_direccion" not in Driver.__abstractmethods__
      and "interpretar_direccion" in (sys.modules["driver_base"].__doc__ or ""))
try:
    Driver.interpretar_direccion("Q0.0", B); check("1c contrato: rechazo por defecto", False)
except ValueError as e:
    check("1c contrato: rechazo por defecto", "no admite direcciones en texto" in str(e))

# --- 1d: errores de la descripcion ---
rechaza("1d tipo de dato desconocido", CELULA.replace("Value: {tipo: BOOL", "Value: {tipo: LREAL"),
        "tipo de dato 'LREAL' no soportado")
rechaza("1d BOOL sin bit", CELULA.replace("Value: Q0.3", "Value: Q0"), "LedRojo/Value", "necesita bit")
rechaza("1d INT con bit", open("planta_ejemplo_variador.yaml", encoding="utf-8").read()
        .replace("Control/Consigna:   DB10.0", "Control/Consigna:   DB10.0.1"), "Consigna", "no lleva bit")
rechaza("1d ruta inexistente en el tipo", CELULA.replace("Estado/Pulsado: I0.0", "Estado/Pulsado: I0.0\n      Estado/Otro: I0.1"),
        "'Estado/Otro' no es una variable del tipo TPulsador")
rechaza("1d area invalida", CELULA.replace("Estado/Pulsado: I0.0", "Estado/Pulsado: X0.0"), "no reconocida")
rechaza("1d instancia de tipo no declarado", CELULA.replace("tipo: TLed\n", "tipo: TLuz\n"),
        "tipo 'TLuz' no declarado")
rechaza("1d instancia sin direccion para una variable del tipo",
        CELULA.replace("      Estado/Retraido:  I0.6\n", ""), "CilindroB", "sin direccion para Estado/Retraido")
rechaza("1d clave repetida", CELULA.replace("Estado/Pulsado: I0.0", "Estado/Pulsado: I0.0\n      Estado/Pulsado: I0.1"),
        "clave repetida")
rechaza("1d driver desconocido", CELULA.replace("driver: snap7", "driver: ads"), "driver 'ads' desconocido", "snap7")
rechaza("1d sin driver", CELULA.replace("  driver: snap7\n", ""), "falta 'driver'")
rechaza("1d acceso invalido", CELULA.replace("acceso: escritura}", "acceso: rw}", 1), "acceso 'rw' no valido")
rechaza("1d direccion no textual", CELULA.replace("Value: Q0.3", "Value: 3"), "debe ser texto")
rechaza("1d varios errores a la vez",
        CELULA.replace("Value: Q0.3", "Value: Q0").replace("tipo: TLed\n", "tipo: TLed\n    color: rojo\n"),
        "necesita bit", "clave 'color' desconocida")

print("=== RESUMEN ===")
for n, ok in res:
    print(f"  [{'OK   ' if ok else 'FALLO'}] {n}")
sys.exit(0 if all(ok for _, ok in res) else 1)
