"""
Prueba del paso 6 (seguridad) sin hardware. Arranca el servidor contra un PLC
emulado en el puerto 102 (Fase C: ya no hay simulacion interna) y comprueba,
con clientes asyncua reales:

  Modo firmar (None + Basic256Sha256), con un usuario definido:
    - Conexion sin cifrado (anonima): lee, pero NO puede escribir (solo lectura).
    - Conexion Basic256Sha256 SignAndEncrypt con usuario y contrasena: lee y escribe.

  Modo estricto (solo Basic256Sha256), con un usuario definido:
    - Conexion sin cifrado: rechazada (no se ofrece None).
    - Cifrado + usuario correcto: lee.
    - Cifrado + contrasena incorrecta: rechazada.
    - Cifrado + anonimo: rechazado (anonima deshabilitada).

Ejecutar desde la raiz del proyecto:  py pruebas/test_seguridad.py
"""
import _rutas  # noqa: F401  (servidor/ en la ruta; carpeta de trabajo tmp/)
import os; os.makedirs("logs", exist_ok=True)   # logs del servidor
import asyncio
import ctypes
import logging
import socket
import subprocess
import sys
from pathlib import Path

import snap7
from snap7.type import SrvArea
import snap7
from snap7.type import SrvArea
from asyncua import Client, ua
from asyncua.crypto.security_policies import SecurityPolicyBasic256Sha256
from asyncua.crypto.cert_gen import setup_self_signed_certificate
from cryptography.x509.oid import ExtendedKeyUsageOID

logging.disable(logging.CRITICAL)
EP = "opc.tcp://127.0.0.1:4840"
URI = "urn:miservidor:ledRojo"
SRV_CERT = "pki/servidor_cert.der"


async def cert_cliente():
    d = Path("cli"); d.mkdir(exist_ok=True)
    c, k = d / "c.der", d / "k.pem"
    await setup_self_signed_certificate(
        k, c, "urn:cliente:test", socket.gethostname(),
        [ExtendedKeyUsageOID.CLIENT_AUTH], {"commonName": "Cliente test"})
    # Lista de confianza (activa por defecto): el cliente de prueba es de confianza
    confianza = Path("pki") / "confianza"; confianza.mkdir(parents=True, exist_ok=True)
    (confianza / "cliente_test.der").write_bytes(c.read_bytes())
    return str(c), str(k)


def arrancar(modo, extra):
    return subprocess.Popen(
        [sys.executable, "../servidor/servidor_opcua.py", "-m", "../modelos/modelo_celula.xml",
         "--plc-ip", "127.0.0.1", "--timeout-ms", "500", "--endpoint", EP, "--seguridad", modo, "--cert-dir", "pki"] + extra,
        stdout=open(f"logs/s_{modo}.log", "w"), stderr=subprocess.STDOUT)


async def nodo(cli, s):
    ns = await cli.get_namespace_index(URI)
    return cli.get_node(f"ns={ns};s={s}")


async def puede_leer(cli):
    v = await (await nodo(cli, "CilindroA.Estado.Expandido")).read_data_value(raise_on_bad_status=False)
    return v.StatusCode.is_good()


async def puede_escribir(cli):
    try:
        await (await nodo(cli, "LedRojo.Value")).write_value(
            ua.DataValue(ua.Variant(True, ua.VariantType.Boolean)))
        return True
    except ua.UaStatusCodeError:
        return False


def ok(c):
    return "OK   " if c else "FALLO"


async def main():
    cc, ck = await cert_cliente()
    resultados = []

    # ---------------- modo firmar ----------------
    print("== modo firmar (con usuario rafa) ==")
    p = arrancar("firmar", ["--usuario", "rafa:clave123"])
    await asyncio.sleep(5)
    try:
        c = Client(EP); await c.connect()
        leer = await puede_leer(c); escribir = await puede_escribir(c); await c.disconnect()
        resultados.append(("anonima sin cifrado: lee", leer))
        resultados.append(("anonima sin cifrado: NO escribe (solo lectura)", not escribir))

        c = Client(EP)
        c.application_uri = "urn:cliente:test"; await c.set_security(SecurityPolicyBasic256Sha256, cc, ck, server_certificate=SRV_CERT,
                             mode=ua.MessageSecurityMode.SignAndEncrypt)
        c.set_user("rafa"); c.set_password("clave123")
        await c.connect()
        leer = await puede_leer(c); escribir = await puede_escribir(c); await c.disconnect()
        resultados.append(("cifrada + usuario: lee", leer))
        resultados.append(("cifrada + usuario: escribe (control)", escribir))
    finally:
        p.terminate(); p.wait()

    # ---------------- modo estricto ----------------
    print("== modo estricto (con usuario rafa) ==")
    p = arrancar("estricto", ["--usuario", "rafa:clave123"])
    await asyncio.sleep(5)
    try:
        try:
            c = Client(EP); await c.connect(); await c.disconnect()
            resultados.append(("sin cifrado rechazada", False))
        except Exception:
            resultados.append(("sin cifrado rechazada", True))

        c = Client(EP)
        c.application_uri = "urn:cliente:test"; await c.set_security(SecurityPolicyBasic256Sha256, cc, ck, server_certificate=SRV_CERT,
                             mode=ua.MessageSecurityMode.SignAndEncrypt)
        c.set_user("rafa"); c.set_password("clave123")
        await c.connect(); leer = await puede_leer(c); await c.disconnect()
        resultados.append(("cifrada + usuario correcto: lee", leer))

        c = Client(EP)
        c.application_uri = "urn:cliente:test"; await c.set_security(SecurityPolicyBasic256Sha256, cc, ck, server_certificate=SRV_CERT,
                             mode=ua.MessageSecurityMode.SignAndEncrypt)
        c.set_user("rafa"); c.set_password("incorrecta")
        try:
            await c.connect(); await c.disconnect()
            resultados.append(("contrasena incorrecta rechazada", False))
        except Exception:
            resultados.append(("contrasena incorrecta rechazada", True))

        c = Client(EP)
        c.application_uri = "urn:cliente:test"; await c.set_security(SecurityPolicyBasic256Sha256, cc, ck, server_certificate=SRV_CERT,
                             mode=ua.MessageSecurityMode.SignAndEncrypt)
        try:
            await c.connect(); await c.disconnect()
            resultados.append(("anonimo rechazado en estricto", False))
        except Exception:
            resultados.append(("anonimo rechazado en estricto", True))
    finally:
        p.terminate(); p.wait()

    # Fase C, paso 1: la simulacion interna ya no existe
    r = subprocess.run([sys.executable, "../servidor/servidor_opcua.py", "-m", "../modelos/modelo_celula.xml",
                        "--simulacion"], capture_output=True, text=True, timeout=60)
    resultados.append(("--simulacion rechazada (opcion retirada)",
                       r.returncode != 0 and "unrecognized arguments: --simulacion" in r.stderr))

    print("\n=== RESUMEN ===")
    for r, c_ in resultados:
        print(f"  [{ok(c_)}] {r}")
    return all(c_ for _, c_ in resultados)


# PLC emulado: I, Q y M de 4 bytes
plc = snap7.server.Server()
_bufs = [(ctypes.c_ubyte * 4)() for _ in range(3)]
for _a, _b in zip((SrvArea.PE, SrvArea.PA, SrvArea.MK), _bufs):
    plc.register_area(_a, 0, _b)
plc.start_to("127.0.0.1", 102)
try:
    exito = asyncio.run(main())
finally:
    plc.stop()
sys.exit(0 if exito else 1)
