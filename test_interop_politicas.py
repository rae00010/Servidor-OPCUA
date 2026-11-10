"""
Interoperabilidad 3: politicas de seguridad.
  a  firmar anuncia None y Basic256Sha256, Aes128-Sha256-RsaOaep y
     Aes256-Sha256-RsaPss en Sign y SignAndEncrypt (7 endpoints).
  b  estricto anuncia las mismas politicas seguras, sin None (6 endpoints).
  c  Con cada politica y modo de mensaje (estricto, usuario): navega, lee,
     escribe (Q0.3) y llama al metodo ControlarPulsador (M0.0).

Ejecutar en la carpeta del servidor:  py test_interop_politicas.py
"""
import asyncio
import sys

from asyncua import ua
from asyncua.crypto.security_policies import (SecurityPolicyAes128Sha256RsaOaep,
                                              SecurityPolicyAes256Sha256RsaPss,
                                              SecurityPolicyBasic256Sha256)
from snap7.type import SrvArea

from util_interop import (PKI, PLCEmulado, check, cert_cliente, cliente_seguro, esperar_servidor,
                          lanzar, nodo, parar, resumen)

BASE = "http://opcfoundation.org/UA/SecurityPolicy#"
SEGURAS = {"Basic256Sha256": SecurityPolicyBasic256Sha256,
           "Aes128_Sha256_RsaOaep": SecurityPolicyAes128Sha256RsaOaep,
           "Aes256_Sha256_RsaPss": SecurityPolicyAes256Sha256RsaPss}
MODOS = {"Sign": ua.MessageSecurityMode.Sign, "SignAndEncrypt": ua.MessageSecurityMode.SignAndEncrypt}
APP_URI = "urn:uja:test:interop-politicas"


def anunciados(eps):
    return {(e.SecurityPolicyUri.replace(BASE, ""), e.SecurityMode.name) for e in eps or []}


def esperados(con_none):
    s = {(p, m) for p in SEGURAS for m in MODOS}
    return s | {("None", "None_")} if con_none else s


async def probar(politica, modo, cert, plc):
    c = await cliente_seguro(SEGURAS[politica], MODOS[modo], cert, APP_URI, "rafa", "clave123")
    await c.connect()
    try:
        hijos = await c.nodes.objects.get_children()
        navega = len(hijos) > 1
        plc.areas[SrvArea.PE][0] = 0x20; await asyncio.sleep(1.2)
        lee = await (await nodo(c, "CilindroA.Estado.Expandido")).read_value() is True
        led = await nodo(c, "LedRojo.Value")
        await led.write_value(ua.DataValue(ua.Variant(True, ua.VariantType.Boolean))); await asyncio.sleep(0.3)
        escribe = plc.bit(SrvArea.PA, 0, 3)
        await led.write_value(ua.DataValue(ua.Variant(False, ua.VariantType.Boolean))); await asyncio.sleep(0.3)
        escribe = escribe and not plc.bit(SrvArea.PA, 0, 3)
        pv = await nodo(c, "PulsadorVerde")
        ns = await c.get_namespace_index("urn:miservidor:ledRojo")
        m = await pv.get_child([f"{ns}:ControlarPulsador"])
        await pv.call_method(m, "pulsar"); await asyncio.sleep(0.3)
        metodo = plc.bit(SrvArea.MK, 0, 0)
        await pv.call_method(m, "soltar"); await asyncio.sleep(0.3)
        metodo = metodo and not plc.bit(SrvArea.MK, 0, 0)
        plc.areas[SrvArea.PE][0] = 0
        return navega, lee, escribe, metodo
    finally:
        await c.disconnect()


async def main(plc):
    cert = cert_cliente("politicas", "Cliente interop politicas", APP_URI)
    (PKI / "confianza").mkdir(parents=True, exist_ok=True)
    (PKI / "confianza" / "politicas.der").write_bytes(open(cert[0], "rb").read())
    usuario = ["--usuario", "rafa:clave123", "--cert-dir", str(PKI)]

    p = lanzar(["--seguridad", "firmar"] + usuario, "logs/interop_politicas_firmar.log")
    try:
        eps = await esperar_servidor()
    finally:
        parar(p)
    check("a firmar: None + 3 politicas seguras x Sign/SignAndEncrypt", anunciados(eps) == esperados(True))

    p = lanzar(["--seguridad", "estricto"] + usuario, "logs/interop_politicas_estricto.log")
    try:
        eps = await esperar_servidor()
        check("b estricto: 3 politicas seguras x Sign/SignAndEncrypt, sin None",
              anunciados(eps) == esperados(False))
        for politica in SEGURAS:
            for modo in MODOS:
                try:
                    r = await probar(politica, modo, cert, plc)
                except Exception as e:
                    print(f"     {politica} {modo}: {type(e).__name__}: {e}")
                    r = (False,) * 4
                check(f"c {politica} {modo}: navega={r[0]} lee={r[1]} escribe={r[2]} metodo={r[3]}", all(r))
    finally:
        parar(p)
    return resumen()


plc = PLCEmulado()
try:
    exito = asyncio.run(main(plc))
finally:
    plc.parar()
sys.exit(0 if exito else 1)
