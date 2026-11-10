"""
Interoperabilidad 4: coherencia de INTEROPERABILIDAD.md con el servidor.
  a  La tabla de resultados tiene una columna por cliente (UaExpert,
     ConsoleReferenceClient, opcua-commander (node-opcua)).
  b  Tiene una fila por cada politica y modo que ofrece el modo firmar
     (seguridad.POLITICAS), y ninguna que el servidor no ofrezca.
  c  Tiene las filas de navegar, leer, escribir, suscribirse, metodo,
     autenticacion de usuario y recuperacion tras caida del PLC.
  d  Cada celda es OK, FALLO, N/A o pendiente.
  e  El procedimiento usa el PLC emulado y las carpetas de la lista de confianza.

Ejecutar en la carpeta del servidor:  py test_interop_doc.py
"""
import re
import sys
from pathlib import Path

import seguridad

resultados = []


def check(nombre, cond):
    resultados.append(cond)
    print(f"  [{'OK   ' if cond else 'FALLO'}] {nombre}")


texto = Path("INTEROPERABILIDAD.md").read_text(encoding="utf-8")
tramo = texto.split("## 4. Resultados", 1)[-1].split("\n## ", 1)[0]
filas = [[c.strip() for c in l.strip().strip("|").split("|")] for l in tramo.splitlines()
         if l.startswith("|") and not re.match(r"^\|[-| ]+\|$", l)]
cabecera, cuerpo = filas[0], filas[1:]
check("a columnas: Funcion + los tres clientes",
      cabecera == ["Función", "UaExpert", "ConsoleReferenceClient", "opcua-commander (node-opcua)"])

ofrecidas = set()
for p in seguridad.POLITICAS["firmar"]:
    if p.name == "NoSecurity":
        ofrecidas.add("None / None")
    else:
        pol, modo = p.name.rsplit("_", 1)
        nombre = {"Aes128Sha256RsaOaep": "Aes128_Sha256_RsaOaep",
                  "Aes256Sha256RsaPss": "Aes256_Sha256_RsaPss"}.get(pol, pol)
        ofrecidas.add(f"{nombre} / {modo}")
en_tabla = {f[0] for f in cuerpo if " / " in f[0]}
check(f"b una fila por politica y modo ofrecidos ({len(ofrecidas)})", en_tabla == ofrecidas)
if en_tabla != ofrecidas:
    print("   faltan:", ofrecidas - en_tabla, " sobran:", en_tabla - ofrecidas)

funciones = ["Navegar", "Leer", "Escribir", "Suscribirse", "Llamar al método", "Usuario:",
             "Lista de confianza", "Recuperación tras caída del PLC"]
check("c filas de todas las funciones pedidas",
      all(any(f[0].startswith(x) for f in cuerpo) for x in funciones))
check("d celdas: OK, FALLO, N/A o pendiente", cuerpo and all(
    len(f) == 4 and all(re.fullmatch(r"(OK|FALLO|N/A|pendiente)( .*)?", c) for c in f[1:]) for f in cuerpo))
check("e procedimiento: PLC emulado, pki/rechazados y pki/confianza",
      "py plc_emulado.py" in texto and "pki/rechazados" in texto and "pki/confianza" in texto)

print(f"\n=== RESUMEN: {sum(resultados)}/{len(resultados)} OK ===")
sys.exit(0 if all(resultados) else 1)
