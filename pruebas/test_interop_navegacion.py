"""
Interoperabilidad 6: evidencia de la navegacion con ConsoleReferenceClient.
Compara el NodeSet exportado por el cliente al navegar el servidor
(evidencias/miservidor_ledRojo_ns2.xml) con el modelo cargado (modelo_celula.xml).
Nodos de instancia: UAObject, UAVariable y UAMethod con NodeId de cadena (ns=1;s=...).
  a  Ambos ficheros declaran el mismo espacio de nombres del modelo.
  b  Los dos tienen los mismos nodos de instancia (53), con los mismos NodeId.
  c  Cada nodo coincide en clase, BrowseName, tipo de dato y nivel de acceso
     (alias resueltos; AccessLevel ausente = 1, valor por defecto del esquema).

No necesita servidor ni PLC.  Ejecutar:  py pruebas/test_interop_navegacion.py
"""
import _rutas  # noqa: F401  (servidor/ en la ruta; carpeta de trabajo tmp/)
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

NS = "{http://opcfoundation.org/UA/2011/03/UANodeSet.xsd}"
CLASES = ("UAObject", "UAVariable", "UAMethod")
EVIDENCIA = Path("../evidencias/miservidor_ledRojo_ns2.xml")
MODELO = Path("../modelos/modelo_celula.xml")
resultados = []


def check(nombre, cond):
    resultados.append(bool(cond))
    print(f"  [{'OK   ' if cond else 'FALLO'}] {nombre}")


def instancias(fichero):
    """({NodeId: (clase, BrowseName, DataType, AccessLevel)}, [URIs])."""
    raiz = ET.parse(fichero).getroot()
    alias = {a.get("Alias"): a.text.strip() for a in raiz.find(NS + "Aliases")}
    uris = [u.text for u in raiz.find(NS + "NamespaceUris")]
    nodos = {}
    for e in raiz:
        clase = e.tag[len(NS):]
        if clase in CLASES and ";s=" in e.get("NodeId", ""):
            dt = e.get("DataType")
            nodos[e.get("NodeId")] = (clase, e.get("BrowseName"), alias.get(dt, dt) if dt else None,
                                      e.get("AccessLevel", "1") if clase == "UAVariable" else None)
    return nodos, uris


ev, uris_ev = instancias(EVIDENCIA)
mod, uris_mod = instancias(MODELO)
check(f"a mismo espacio de nombres ({uris_mod})", uris_ev == uris_mod)
check(f"b mismos nodos de instancia: evidencia {len(ev)}, modelo {len(mod)}",
      set(ev) == set(mod) and len(mod) == 53)
for n in sorted(set(ev) ^ set(mod)):
    print("   solo en", "evidencia:" if n in ev else "modelo:", n)
distintos = [n for n in sorted(set(ev) & set(mod)) if ev[n] != mod[n]]
check(f"c clase, BrowseName, tipo de dato y acceso identicos ({len(set(ev) & set(mod)) - len(distintos)}"
      f" de {len(mod)})", not distintos)
for n in distintos:
    print(f"   {n}: evidencia {ev[n]} / modelo {mod[n]}")

print(f"\n=== RESUMEN: {sum(resultados)}/{len(resultados)} OK ===")
sys.exit(0 if all(resultados) else 1)
