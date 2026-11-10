"""
Utilidades comunes de las pruebas de interoperabilidad (test_interop_*.py):
PLC emulado, arranque del servidor, certificados de cliente y resumen.
"""
import os; os.makedirs("logs", exist_ok=True)   # logs del servidor
import asyncio
import logging; logging.disable(logging.WARNING)   # avisos del cliente asyncua
import ctypes
import socket
import subprocess
import sys
from pathlib import Path

import snap7
from snap7.type import SrvArea
from asyncua import Client, ua
from asyncua.crypto.cert_gen import (dump_private_key_as_pem, generate_private_key,
                                     generate_self_signed_app_certificate)
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import ExtendedKeyUsageOID

EP = "opc.tcp://127.0.0.1:4840"
URI_MODELO = "urn:miservidor:ledRojo"
CARPETA = Path("prueba_interop")
PKI = CARPETA / "pki"
SRV_CERT = str(PKI / "servidor_cert.der")
_resultados = []


def check(nombre, cond):
    _resultados.append((nombre, bool(cond)))
    print(f"  [{'OK   ' if cond else 'FALLO'}] {nombre}")
    return cond


def resumen():
    fallos = [n for n, c in _resultados if not c]
    print(f"\n=== RESUMEN: {len(_resultados) - len(fallos)}/{len(_resultados)} OK ===")
    for n in fallos:
        print(f"  FALLO: {n}")
    return not fallos


class PLCEmulado:
    """Areas I, Q y M de 4 bytes en 127.0.0.1:102."""

    def __init__(self):
        self.srv = snap7.server.Server()
        self.areas = {a: (ctypes.c_ubyte * 4)() for a in (SrvArea.PE, SrvArea.PA, SrvArea.MK)}
        for a, b in self.areas.items():
            self.srv.register_area(a, 0, b)
        self.srv.start_to("127.0.0.1", 102)

    def bit(self, area, byte, b):
        return bool(self.areas[area][byte] >> b & 1)

    def parar(self):
        self.srv.stop()


def lanzar(extra, log):
    return subprocess.Popen(
        [sys.executable, "serverOPCUA_MetodoTFGV5_asyncua.py", "-m", "modelo_celula.xml",
         "--plc-ip", "127.0.0.1", "--timeout-ms", "500", "--endpoint", EP] + list(extra),
        stdout=open(log, "w"), stderr=subprocess.STDOUT)


def parar(p):
    p.terminate()
    try:
        p.wait(10)
    except subprocess.TimeoutExpired:
        p.kill()


async def esperar_servidor(intentos=80):
    """Espera a que el servidor publique sus endpoints."""
    for _ in range(intentos):
        try:
            c = Client(EP)
            return await c.connect_and_get_server_endpoints()
        except Exception:
            await asyncio.sleep(0.25)
    return None


def cert_cliente(nombre, cn, app_uri):
    """Certificado de cliente autofirmado con CN y ApplicationUri dados. Devuelve (cert, clave)."""
    d = CARPETA / "cli"; d.mkdir(parents=True, exist_ok=True)
    c, k = d / f"{nombre}.der", d / f"{nombre}.pem"
    if not c.exists():
        clave = generate_private_key()
        cert = generate_self_signed_app_certificate(
            clave, cn, {}, [x509.UniformResourceIdentifier(app_uri), x509.DNSName(socket.gethostname())],
            extended=[ExtendedKeyUsageOID.CLIENT_AUTH], days=365)
        c.write_bytes(cert.public_bytes(Encoding.DER))
        k.write_bytes(dump_private_key_as_pem(clave))
    return str(c), str(k)


async def cliente_seguro(politica, modo, cert, app_uri, usuario=None, clave=None):
    c = Client(EP)
    c.application_uri = app_uri
    await c.set_security(politica, cert[0], cert[1], server_certificate=SRV_CERT, mode=modo)
    if usuario:
        c.set_user(usuario); c.set_password(clave)
    return c


async def nodo(c, s):
    ns = await c.get_namespace_index(URI_MODELO)
    return c.get_node(f"ns={ns};s={s}")
