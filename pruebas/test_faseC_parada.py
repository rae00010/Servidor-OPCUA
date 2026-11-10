"""Fase C, paso 4: parada ordenada del servidor sin senales (valida en Windows).
'parar' por stdin; el cierre de la tuberia no detiene; fichero de PID con latido y
fichero de control. PLC emulado en el puerto 102: tras la parada no quedan sesiones S7."""
import _rutas  # noqa: F401  (servidor/ en la ruta; carpeta de trabajo tmp/)
import os; os.makedirs("logs", exist_ok=True)   # logs del servidor
import csv, ctypes, json, subprocess, sys, time
from pathlib import Path
import snap7
import yaml
from snap7.type import SrvArea

res = []
def check(nombre, ok): res.append((nombre, bool(ok)))
SERVIDOR = "../servidor/servidor_opcua.py"
DIR = Path("prueba_faseC"); DIR.mkdir(exist_ok=True)
CFG = DIR / "config_parada.yaml"
CFG.write_text(yaml.safe_dump({
    "driver": {"nombre": "snap7", "parametros": {"ip": "127.0.0.1", "timeout_ms": 500}},
    "modelo": {"origen": "nodeset", "nodeset": "../../modelos/modelo_celula.xml"},
    "medida": {"periodo_ms": 50, "log_csv": "medidas/parada.csv"}}), encoding="utf-8")
PID, CONTROL = DIR / "servidor.pid", DIR / "servidor.parar"

srv = snap7.server.Server()
bufs = [(ctypes.c_ubyte * 4)() for _ in range(3)]
for a, b in zip((SrvArea.PE, SrvArea.PA, SrvArea.MK), bufs):
    srv.register_area(a, 0, b)
srv.start_to("127.0.0.1", 102)
clientes_s7 = lambda: srv.get_status()[2]


def lanzar(log, *extra, stdin=subprocess.DEVNULL):
    return subprocess.Popen([sys.executable, SERVIDOR, "--config", str(CFG), *extra], stdin=stdin,
                            stdout=open(log, "w"), stderr=subprocess.STDOUT, text=True)


def esperar_log(log, texto, segundos=20):
    fin = time.time() + segundos
    while time.time() < fin:
        if texto in open(log, encoding="utf-8", errors="replace").read():
            return True
        time.sleep(0.2)
    return False


def esperar_fin(p, segundos=10):
    t0 = time.time()
    try:
        p.wait(timeout=segundos)
        return p.returncode, time.time() - t0
    except subprocess.TimeoutExpired:
        p.kill(); p.wait()
        return None, segundos


def leer(log):
    return open(log, encoding="utf-8", errors="replace").read()


try:
    # --- 4a: 'parar' por stdin ---
    log = "logs/srv_faseC_4a.log"
    p = lanzar(log, "--parada-stdin", stdin=subprocess.PIPE)
    conectado = esperar_log(log, "Conectado al PLC")
    time.sleep(3.0)                                   # unas 60 filas de CSV
    s7_en_marcha = clientes_s7()
    p.stdin.write("parar\n"); p.stdin.flush()
    codigo, t = esperar_fin(p)
    texto = leer(log)
    check("4a 'parar' por stdin: termina con codigo 0 en menos de 10 s",
          conectado and codigo == 0 and t < 10)
    check("4a log: orden recibida, conexion S7 cerrada y servidor detenido",
          all(s in texto for s in ("Orden de parada recibida (entrada estandar)", "Parada ordenada",
                                   "Conexion con el PLC cerrada", "Servidor OPC UA detenido")))
    time.sleep(0.5)
    check("4a PLC emulado: 1 sesion S7 en marcha y 0 tras la parada", s7_en_marcha == 1 and clientes_s7() == 0)
    filas = list(csv.reader(open(DIR / "medidas" / "parada.csv", encoding="utf-8")))
    check("4a CSV de ciclos volcado al cerrar (cabecera y filas completas)",
          filas[0][0] == "iso_time" and len(filas) > 20 and all(len(f) == len(filas[0]) for f in filas))

    # --- 4b: cerrar la tuberia no detiene; el fichero de control si ---
    log = "logs/srv_faseC_4b.log"
    p = lanzar(log, "--parada-stdin", "--fichero-pid", str(PID), "--fichero-control", str(CONTROL),
               stdin=subprocess.PIPE)
    esperar_log(log, "Conectado al PLC")
    p.stdin.close()                                   # como si la interfaz se hubiera caido
    aviso = esperar_log(log, "Entrada estandar cerrada: el servidor sigue en marcha", 5)
    time.sleep(2.0)
    check("4b cierre de stdin: solo aviso, el servidor sigue en marcha", aviso and p.poll() is None)

    # --- 4c: PID con latido y parada por fichero de control (interfaz reiniciada) ---
    info = json.loads(PID.read_text(encoding="utf-8"))
    m1 = PID.stat().st_mtime; time.sleep(2.5); m2 = PID.stat().st_mtime
    check("4c fichero de PID: pid del proceso, endpoint y fichero de control",
          info["pid"] == p.pid and info["endpoint"].startswith("opc.tcp://")
          and Path(info["fichero_control"]) == CONTROL)
    check("4c latido: el fichero de PID se renueva", m2 > m1)
    CONTROL.write_text("parar", encoding="utf-8")     # lo que haria una interfaz nueva
    codigo, t = esperar_fin(p)
    texto = leer(log)
    check("4c fichero de control: parada ordenada con codigo 0",
          codigo == 0 and "Orden de parada recibida (fichero de control" in texto
          and "Conexion con el PLC cerrada" in texto)
    check("4c al terminar se borran el PID y la orden", not PID.exists() and not CONTROL.exists())
    time.sleep(0.5)
    check("4c PLC emulado: 0 sesiones S7 tras la parada", clientes_s7() == 0)

    # --- 4d: una orden antigua no detiene un arranque nuevo ---
    CONTROL.write_text("parar", encoding="utf-8")
    log = "logs/srv_faseC_4d.log"
    p = lanzar(log, "--fichero-pid", str(PID), "--fichero-control", str(CONTROL))
    esperar_log(log, "Conectado al PLC"); time.sleep(2.0)
    vivo = p.poll() is None
    CONTROL.write_text("parar", encoding="utf-8")
    codigo, _ = esperar_fin(p)
    check("4d orden de una ejecucion anterior: se descarta al arrancar", vivo and codigo == 0)
finally:
    srv.stop()

print("=== RESUMEN ===")
for nombre, ok in res:
    print(f"  [{'OK   ' if ok else 'FALLO'}] {nombre}")
sys.exit(0 if all(ok for _, ok in res) else 1)
