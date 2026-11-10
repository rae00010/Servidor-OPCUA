"""
Interoperabilidad 1: declaracion de perfil.
  a  Sin --perfil, ServerProfileArray queda vacio y el log lo indica.
  b  Con --perfil URI, ServerProfileArray contiene exactamente [URI].
  c  servidor.perfiles en config.yaml llega igual que --perfil.
  d  La ayuda de --perfil advierte que solo se declare un perfil verificado.

Ejecutar desde la raiz del proyecto:  py pruebas/test_interop_perfil.py
"""
import _rutas  # noqa: F401  (servidor/ en la ruta; carpeta de trabajo tmp/)
import asyncio
import subprocess
import sys

import yaml
from asyncua import Client, ua

from util_interop import CARPETA, EP, PLCEmulado, check, esperar_servidor, lanzar, parar, resumen

PERFIL = "http://opcfoundation.org/UA-Profile/Server/EmbeddedUA2017"


async def leer_perfiles():
    if await esperar_servidor() is None:
        return None
    c = Client(EP); await c.connect()
    try:
        return await c.get_node(ua.NodeId(ua.ObjectIds.Server_ServerCapabilities_ServerProfileArray)).read_value()
    finally:
        await c.disconnect()


async def main():
    CARPETA.mkdir(exist_ok=True)
    p = lanzar([], "logs/interop_perfil_a.log")
    try:
        v = await leer_perfiles()
    finally:
        parar(p)
    check("a sin --perfil: ServerProfileArray vacio", v == [])
    log = open("logs/interop_perfil_a.log", encoding="utf-8", errors="replace").read()
    check("a log: 'ningun perfil declarado'", "Perfil declarado: ninguno" in log)

    p = lanzar(["--perfil", PERFIL], "logs/interop_perfil_b.log")
    try:
        v = await leer_perfiles()
    finally:
        parar(p)
    check("b --perfil URI: ServerProfileArray == [URI]", v == [PERFIL])

    cfg = CARPETA / "config_perfil.yaml"
    cfg.write_text(yaml.safe_dump({
        "driver": {"nombre": "snap7", "parametros": {"ip": "127.0.0.1", "timeout_ms": 500}},
        "modelo": {"origen": "nodeset", "nodeset": "../../modelos/modelo_celula.xml"},
        "servidor": {"endpoint": EP, "perfiles": [PERFIL]}}), encoding="utf-8")
    p = subprocess.Popen([sys.executable, "../servidor/servidor_opcua.py", "--config", str(cfg)],
                         stdout=open("logs/interop_perfil_c.log", "w"), stderr=subprocess.STDOUT)
    try:
        v = await leer_perfiles()
    finally:
        parar(p)
    check("c servidor.perfiles del YAML: ServerProfileArray == [URI]", v == [PERFIL])

    ayuda = subprocess.run([sys.executable, "../servidor/servidor_opcua.py", "--help"],
                           capture_output=True, text=True, timeout=60).stdout
    tramo = " ".join(ayuda.split("--perfil URI ")[-1].split()[:40])
    check("d ayuda de --perfil: por defecto ninguno, solo perfil verificado",
          "ninguno" in tramo and "verificado" in tramo)
    return resumen()


plc = PLCEmulado()
try:
    exito = asyncio.run(main())
finally:
    plc.parar()
sys.exit(0 if exito else 1)
