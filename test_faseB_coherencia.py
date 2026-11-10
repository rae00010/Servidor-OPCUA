"""Fase B, paso 5: coherencia de la descripcion de planta.
Errores del generador, aviso de escritura en Q y aviso del servidor con un NodeSet
externo con escrituras duplicadas (PLC emulado en el puerto 102)."""
import os; os.makedirs("logs", exist_ok=True)   # logs del servidor
import ctypes, subprocess, sys, time
import snap7
from snap7.type import SrvArea
import modelo
from modelo import ErrorPlanta, cargar_planta

res = []
def check(nombre, ok): res.append((nombre, bool(ok)))
CELULA = open("planta_celula.yaml", encoding="utf-8").read()
VARIADOR = open("planta_ejemplo_variador.yaml", encoding="utf-8").read()
SEGUNDO_PULSADOR = """  PulsadorRojo:
    tipo: TPulsador
    direcciones:
      Control/PulsadoRemoto: {remoto}
      Estado/Pulsado: I0.1
"""


def errores_de(texto):
    try:
        cargar_planta(texto); return []
    except ErrorPlanta as e:
        return e.errores


# --- 5a: la celula da exactamente un aviso (LED en Q0.3) ---
p = cargar_planta("planta_celula.yaml")
check("5a celula: un unico aviso, LedRojo/Value en Q0.3", len(p.avisos) == 1
      and p.avisos[0].startswith("LedRojo/Value (Q0.3): variable de escritura en las salidas Q"))
check("5a el aviso nombra las salidas que comparten byte y recomienda marcas M",
      all(s in p.avisos[0] for s in ("CilindroA/Control/ExpandirSalida (Q0.0)",
                                     "CilindroA/Control/ContraerSalida (Q0.1)",
                                     "CilindroB/Control/ExpandirSalida (Q0.2)", "marca M")))
check("5a variador: sin errores ni avisos (escrituras en M y DB)", not cargar_planta(VARIADOR).avisos)

# --- 5b: errores de coherencia ---
e = errores_de(CELULA + SEGUNDO_PULSADOR.format(remoto="M0.0"))
check("5b dos escrituras en la misma direccion -> error",
      e == ["PulsadorVerde/Control/PulsadoRemoto (M0.0) y PulsadorRojo/Control/PulsadoRemoto (M0.0): "
            "dos variables de escritura sobre la misma direccion"])
check("5b misma direccion de lectura en dos variables: permitido",
      not errores_de(CELULA.replace("Estado/Pulsado: I0.0", "Estado/Pulsado: I0.4")))
check("5b escritura y lectura sobre la misma direccion: permitido",
      not errores_de(VARIADOR.replace("Estado/Velocidad:   DB10.2", "Estado/Velocidad:   DB10.0")))
e = errores_de(VARIADOR.replace("Control/Marcha:     M10.0", "Control/Marcha:     DB10.1.3"))
check("5b BOOL dentro de un INT de escritura -> error de solape",
      len(e) == 1 and "Transportador1/Control/Marcha (DB10.1.3, BOOL)" in e[0]
      and "Transportador1/Control/Consigna (DB10.0, INT)" in e[0] and "solapados" in e[0])
e = errores_de(VARIADOR.replace("Consigna: {tipo: INT,", "Consigna: {tipo: REAL,")
               .replace("Control/Marcha:     M10.0", "Control/Marcha:     DB10.3.7"))
check("5b REAL de 4 bytes solapa con BOOL en su ultimo byte", len(e) == 1 and "solapados" in e[0])
check("5b INT en DB10.0 y escritura en DB10.2: sin solape",
      not errores_de(VARIADOR.replace("Control/Marcha:     M10.0", "Control/Marcha:     DB10.2.0")))
check("5b mismo byte en otro DB: sin solape",
      not errores_de(VARIADOR.replace("Control/Marcha:     M10.0", "Control/Marcha:     DB11.0.0")))
check("5b area invalida -> error",
      any("no reconocida" in x for x in errores_de(CELULA.replace("Value: Q0.3", "Value: T0.3"))))
check("5b tipo no soportado -> error",
      any("no soportado" in x for x in errores_de(CELULA.replace("Value: {tipo: BOOL", "Value: {tipo: LINT"))))

# --- 5c: el CLI muestra el aviso; el generador rechaza las escrituras duplicadas ---
r = subprocess.run([sys.executable, "modelo.py", "planta_celula.yaml", "-o", "modelo_faseB_celula.xml"],
                   capture_output=True, text=True)
check("5c CLI: genera y muestra el aviso", r.returncode == 0 and "AVISO: LedRojo/Value (Q0.3)" in r.stderr)
open("planta_faseB_duplicada.yaml", "w", encoding="utf-8").write(CELULA + SEGUNDO_PULSADOR.format(remoto="M0.0"))
r = subprocess.run([sys.executable, "modelo.py", "planta_faseB_duplicada.yaml", "-o", "modelo_faseB_dup.xml"],
                   capture_output=True, text=True)
check("5c CLI: escritura duplicada -> no genera y explica", r.returncode != 0 and "misma direccion" in r.stderr)

# --- 5d: NodeSet externo con escrituras duplicadas: el servidor solo avisa ---
planta = cargar_planta(CELULA + SEGUNDO_PULSADOR.format(remoto="M0.1"))
planta.instancias[-1].direcciones["Control/PulsadoRemoto"] = {"area": 2, "byte": 0, "bit": 0}
open("modelo_faseB_dup.xml", "w", encoding="utf-8").write(modelo.generar_nodeset(planta))
srv = snap7.server.Server()
bufs = [(ctypes.c_ubyte * 4)() for _ in range(3)]
for a, b in zip((SrvArea.PE, SrvArea.PA, SrvArea.MK), bufs):
    srv.register_area(a, 0, b)
srv.start_to("127.0.0.1", 102)
try:
    log = "logs/srv_faseB_5.log"
    proc = subprocess.Popen([sys.executable, "serverOPCUA_MetodoTFGV5_asyncua.py", "-m", "modelo_faseB_dup.xml",
                             "--plc-ip", "127.0.0.1", "--timeout-ms", "500"],
                            stdout=open(log, "w"), stderr=subprocess.STDOUT)
    for _ in range(60):
        time.sleep(0.25)
        texto = open(log, encoding="utf-8", errors="replace").read()
        if "Plan de lectura" in texto:
            break
    proc.terminate(); proc.wait()
finally:
    srv.stop()
check("5d servidor: arranca con 12 variables y avisa de la escritura duplicada",
      "Tabla de mapeo: 12 variables" in texto and "M0.0 escrita por" in texto
      and "PulsadorRojo/Control/PulsadoRemoto" in texto)

print("=== RESUMEN ===")
for nombre, ok in res:
    print(f"  [{'OK   ' if ok else 'FALLO'}] {nombre}")
sys.exit(0 if all(ok for _, ok in res) else 1)
