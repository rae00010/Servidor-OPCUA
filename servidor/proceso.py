"""
Gestor del proceso del servidor OPC UA para la interfaz (Fase C).

Lanza el servidor como subproceso con --config, --parada-stdin, --fichero-pid
y --fichero-control, y lo detiene de forma ordenada sin senales (valido en
Windows): primero la orden 'parar' por stdin y el fichero de control, y solo
si no termina a tiempo, se mata y se informa como parada forzada.

Una interfaz reiniciada detecta un servidor en marcha por el fichero de PID
(proceso vivo y latido reciente) y lo detiene con el fichero de control.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

SERVIDOR = Path(__file__).with_name("servidor_opcua.py")
LATIDO_MAX_S = 5.0          # PID con latido mas antiguo: se considera obsoleto
PARADA_S = 10.0             # espera de la parada ordenada antes de forzar


def proceso_vivo(pid):
    """True si existe el proceso 'pid'. En Windows no usa os.kill (lo terminaria)."""
    if not pid:
        return False
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, int(pid))         # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            codigo = ctypes.c_ulong()
            return bool(k32.GetExitCodeProcess(h, ctypes.byref(codigo))) and codigo.value == 259
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


class GestorServidor:
    def __init__(self, carpeta):
        self.carpeta = Path(carpeta).resolve()
        self.fichero_pid = self.carpeta / "servidor.pid"
        self.fichero_control = self.carpeta / "servidor.parar"
        self.fichero_log = self.carpeta / "logs" / "servidor.log"
        self._p = None              # subproceso lanzado por esta interfaz
        self.ultima_salida = None   # {"codigo", "forzada"} de la ultima parada

    # --- estado ---
    def _pid_externo(self):
        """Info del fichero de PID si corresponde a un servidor vivo y con latido."""
        try:
            info = json.loads(self.fichero_pid.read_text(encoding="utf-8"))
            edad = time.time() - self.fichero_pid.stat().st_mtime
        except (OSError, ValueError):
            return None
        if edad > LATIDO_MAX_S or not proceso_vivo(info.get("pid")):
            return None
        return info

    def estado(self):
        if self._p is not None and self._p.poll() is None:
            info = self._pid_externo() or {}
            return {"en_marcha": True, "pid": self._p.pid, "propio": True,
                    "inicio": info.get("inicio"), "config": info.get("config")}
        if self._p is not None:
            self.ultima_salida = self.ultima_salida or {"codigo": self._p.returncode, "forzada": False}
            self._p = None
        info = self._pid_externo()
        if info:
            return {"en_marcha": True, "pid": info["pid"], "propio": False,
                    "inicio": info.get("inicio"), "config": info.get("config")}
        return {"en_marcha": False, "ultima_salida": self.ultima_salida}

    # --- control ---
    def arrancar(self, config):
        """config: ruta del YAML (ya validada por quien llama)."""
        if self.estado()["en_marcha"]:
            raise RuntimeError("el servidor ya esta en marcha")
        self.fichero_log.parent.mkdir(parents=True, exist_ok=True)
        self.fichero_control.unlink(missing_ok=True)
        log = open(self.fichero_log, "w", encoding="utf-8")
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        self._p = subprocess.Popen(
            [sys.executable, str(SERVIDOR), "--config", str(config), "--parada-stdin",
             "--fichero-pid", str(self.fichero_pid), "--fichero-control", str(self.fichero_control)],
            cwd=self.carpeta, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
            text=True, creationflags=flags)
        log.close()                 # el hijo conserva su copia del descriptor
        self.ultima_salida = None
        return self._p.pid

    def detener(self, espera=PARADA_S):
        """Parada ordenada. Devuelve {"codigo", "forzada", "segundos"}."""
        t0 = time.time()
        e = self.estado()
        if not e["en_marcha"]:
            return {"codigo": None, "forzada": False, "segundos": 0.0, "ya_parado": True}
        self.fichero_control.write_text("parar", encoding="utf-8")
        if e["propio"]:
            try:
                self._p.stdin.write("parar\n"); self._p.stdin.flush()
            except (OSError, ValueError):
                pass                # el fichero de control basta
            try:
                self._p.wait(timeout=espera)
                r = {"codigo": self._p.returncode, "forzada": False}
            except subprocess.TimeoutExpired:
                self._p.kill(); self._p.wait()
                r = {"codigo": self._p.returncode, "forzada": True}
            self._p = None
        else:
            pid = e["pid"]
            while proceso_vivo(pid) and time.time() - t0 < espera:
                time.sleep(0.2)
            forzada = proceso_vivo(pid)
            if forzada:
                subprocess.run(["taskkill", "/PID", str(pid), "/F"] if os.name == "nt"
                               else ["kill", "-9", str(pid)], capture_output=True)
            r = {"codigo": None, "forzada": forzada}
        self.fichero_control.unlink(missing_ok=True)
        r["segundos"] = round(time.time() - t0, 2)
        self.ultima_salida = r
        return r

    def log(self, desde=0):
        """Lineas del log a partir de la linea 'desde'. Devuelve (lineas, siguiente)."""
        try:
            lineas = self.fichero_log.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return [], 0
        desde = max(0, min(int(desde), len(lineas)))
        return lineas[desde:], len(lineas)
