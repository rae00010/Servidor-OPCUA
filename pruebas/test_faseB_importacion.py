"""Fase B, paso 4: importacion de NodeSet con validacion comun al servidor y a la interfaz.
PLC emulado en el puerto 102."""
import _rutas  # noqa: F401  (servidor/ en la ruta; carpeta de trabajo tmp/)
import os; os.makedirs("logs", exist_ok=True)   # logs del servidor
import ctypes, re, subprocess, sys, time
import xml.etree.ElementTree as ET
import snap7
from snap7.type import SrvArea
import modelo
from driver_snap7 import DriverSnap7

res = []
def check(nombre, ok): res.append((nombre, bool(ok)))
N = "{http://opcfoundation.org/UA/2011/03/UANodeSet.xsd}"
SERVIDOR = "../servidor/servidor_opcua.py"
D = DriverSnap7(ip="127.0.0.1")

CELULA, FALLOS = "modelo_faseB_celula.xml", "modelo_faseB_fallos.xml"
modelo.generar_fichero("../modelos/planta_celula.yaml", CELULA)


def tabla(imp):
    return {(v.ruta, D.texto_direccion(v.direccion), v.escritura) for v in imp.variables}


# --- 4a: importacion de modelos validos ---
gen = modelo.importar_nodeset(CELULA, D.validar_direccion)
v5 = modelo.importar_nodeset("../modelos/ledmodel_v5.xml", D.validar_direccion)
check("4a NodeSet generado: 10 variables sin errores", len(gen.variables) == 10 and not gen.errores)
check("4a ledmodel_v5.xml: 10 variables sin errores", len(v5.variables) == 10 and not v5.errores)
check("4a misma tabla (ruta, direccion, sentido) en ambos", tabla(gen) == tabla(v5))
check("4a metodos encontrados", gen.metodos == v5.metodos == [("PulsadorVerde/ControlarPulsador", "ControlarPulsador")])

# --- 4b: modelo con propiedades que faltan ---
QUITAR = ["LedRojo.Value.bit", "CilindroA.Estado.Expandido.byte", "PulsadorVerde.Estado.Pulsado.area",
          "CilindroB.Control.ExpandirSalida.byte", "CilindroB.Control.ExpandirSalida.bit"]
arbol = ET.parse(CELULA); raiz = arbol.getroot()
for el in list(raiz):
    if el.get("NodeId") in {f"ns=1;s={q}" for q in QUITAR}:
        raiz.remove(el)
    for refs in el.iter(f"{N}References"):
        for r in list(refs):
            if r.text in {f"ns=1;s={q}" for q in QUITAR}:
                refs.remove(r)
arbol.write(FALLOS, encoding="utf-8", xml_declaration=True)
ESPERADOS = {
    "LedRojo/Value: falta bit (obligatorio en variables Boolean)",
    "CilindroA/Estado/Expandido: falta byte",
    "PulsadorVerde/Estado/Pulsado: falta area",
    "CilindroB/Control/ExpandirSalida: falta byte, bit (obligatorio en variables Boolean)",
}
imp = modelo.importar_nodeset(FALLOS, D.validar_direccion)
check("4b importacion: lista todas las carencias, con ruta y propiedad", set(imp.errores) == ESPERADOS)
if set(imp.errores) != ESPERADOS:
    print("  ", imp.errores)
check("4b importacion: las variables correctas siguen ahi", len(imp.variables) == 10
      and sum(v.direccion is not None for v in imp.variables) == 6)

r = subprocess.run([sys.executable, SERVIDOR, "-m", FALLOS, "--plc-ip", "127.0.0.1"],
                   capture_output=True, text=True, timeout=60)
salida = r.stdout + r.stderr
check("4c servidor: no arranca y da los mismos errores que la importacion",
      r.returncode != 0 and "Modelo incompleto" in salida and all(e in salida for e in ESPERADOS))

# --- 4d: el servidor construye la misma tabla que la importacion (PLC emulado) ---
srv = snap7.server.Server()
bufs = [(ctypes.c_ubyte * 4)() for _ in range(3)]
for a, b in zip((SrvArea.PE, SrvArea.PA, SrvArea.MK), bufs):
    srv.register_area(a, 0, b)
srv.start_to("127.0.0.1", 102)
try:
    log = "logs/srv_faseB_4.log"
    p = subprocess.Popen([sys.executable, SERVIDOR, "-m", CELULA, "--plc-ip", "127.0.0.1",
                          "--timeout-ms", "500"], stdout=open(log, "w"), stderr=subprocess.STDOUT)
    for _ in range(60):
        time.sleep(0.25)
        texto = open(log, encoding="utf-8", errors="replace").read()
        if "Plan de lectura" in texto:
            break
    p.terminate(); p.wait()
finally:
    srv.stop()
filas = set()
for l in texto.splitlines():
    m = re.search(r"\s{2}(\S+(?: \(\w+\))?)\s+(OPC UA -> PLC|PLC -> OPC UA)\s+(\S+)$", l)
    if m:
        filas.add((m[3], m[1], m[2] == "OPC UA -> PLC"))
check("4d servidor y importacion: misma tabla de mapeo", filas == tabla(gen))

print("=== RESUMEN ===")
for nombre, ok in res:
    print(f"  [{'OK   ' if ok else 'FALLO'}] {nombre}")
sys.exit(0 if all(ok for _, ok in res) else 1)
