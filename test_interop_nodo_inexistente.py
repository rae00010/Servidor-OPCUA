"""
Interoperabilidad 5: limitacion conocida de asyncua con NodeId inexistentes
(detectada con ConsoleReferenceClient), en modo firmar con None / None. Documenta el comportamiento; no lo corrige.
  a  Leer un NodeId inexistente devuelve BadNodeIdUnknown.
  b  Escribir en un NodeId inexistente devuelve BadUserAccessDenied (no
     BadNodeIdUnknown): asyncua comprueba los permisos antes que la existencia.
     - Anonimo (sin permiso de escritura): se rechaza toda la operacion Write
       (resultado del servicio).
     - Usuario autenticado: el servicio responde Good y el rechazo llega como
       resultado por nodo.
  d  Usuario autenticado, una sola peticion Write con LedRojo.Value (True) y el
     NodeId inexistente: resultados por nodo Good y BadUserAccessDenied, y
     LedRojo llega al PLC (Q0.3 = 1).
  c  El servidor sigue funcionando: LedRojo.Value se escribe (False), se lee y
     llega al PLC (Q0.3 = 0).

Ejecutar en la carpeta del servidor:  py test_interop_nodo_inexistente.py
"""
import asyncio
import sys

from asyncua import Client, ua
from snap7.type import SrvArea

from util_interop import CARPETA, EP, PKI, PLCEmulado, check, esperar_servidor, lanzar, nodo, parar, resumen

USUARIO, CLAVE = "rafa", "clave123"


async def inexistente(c):
    ns = await c.get_namespace_index("urn:miservidor:ledRojo")
    return ua.NodeId("NoExiste.Variable", ns)


async def escribir(c, nodos):
    """Una peticion Write con True en cada nodo. Devuelve ('servicio', codigo) si se
    rechaza la operacion entera, o ('nodo', [codigos]) con el resultado de cada nodo."""
    esc = ua.WriteParameters()
    esc.NodesToWrite = [ua.WriteValue(NodeId=n, AttributeId=ua.AttributeIds.Value,
                                      Value=ua.DataValue(ua.Variant(True, ua.VariantType.Boolean)))
                        for n in nodos]
    try:
        return "nodo", [s.value for s in await c.uaclient.write(esc)]
    except ua.UaStatusCodeError as e:
        return "servicio", e.code


async def estados(c):
    """(estado de lectura, resultado de escritura) sobre un NodeId inexistente."""
    nid = await inexistente(c)
    lect = ua.ReadParameters()
    lect.NodesToRead = [ua.ReadValueId(NodeId=nid, AttributeId=ua.AttributeIds.Value)]
    r = await c.uaclient.read(lect)
    return r[0].StatusCode.value, await escribir(c, [nid])


async def main():
    CARPETA.mkdir(exist_ok=True)
    p = lanzar(["--seguridad", "firmar", "--cert-dir", str(PKI), "--usuario", f"{USUARIO}:{CLAVE}"], "logs/interop_nodo_inexistente.log")
    try:
        check("servidor arrancado", await esperar_servidor() is not None)
        for nombre, usuario in (("anonimo", None), ("usuario", USUARIO)):
            c = Client(EP)
            if usuario:
                c.set_user(usuario); c.set_password(CLAVE)
            await c.connect()
            try:
                lect, esc = await estados(c)
                check(f"a {nombre}: leer NodeId inexistente -> BadNodeIdUnknown",
                      lect == ua.StatusCodes.BadNodeIdUnknown)
                denegado = ua.StatusCodes.BadUserAccessDenied
                if usuario:
                    check("b usuario: escribir NodeId inexistente -> BadUserAccessDenied por nodo",
                          esc == ("nodo", [denegado]))
                    led = await nodo(c, "LedRojo.Value")
                    q03_antes = plc.bit(SrvArea.PA, 0, 3)
                    res = await escribir(c, [led.nodeid, await inexistente(c)])
                    await asyncio.sleep(0.5)
                    check(f"d usuario, una peticion: LedRojo Good, inexistente BadUserAccessDenied ({res})",
                          res == ("nodo", [ua.StatusCodes.Good, denegado]))
                    check("d LedRojo llega al PLC: Q0.3 pasa de 0 a 1",
                          not q03_antes and plc.bit(SrvArea.PA, 0, 3))
                    await led.write_value(ua.DataValue(ua.Variant(False, ua.VariantType.Boolean)))
                    await asyncio.sleep(0.5)
                    check("c tras ello, LedRojo.Value se escribe y se lee (False) y Q0.3 = 0",
                          await led.read_value() is False and not plc.bit(SrvArea.PA, 0, 3))
                else:
                    check("b anonimo: escribir NodeId inexistente -> BadUserAccessDenied en todo el Write",
                          esc == ("servicio", denegado))
            finally:
                await c.disconnect()
    finally:
        parar(p)
    return resumen()


plc = PLCEmulado()
try:
    exito = asyncio.run(main())
finally:
    plc.parar()
sys.exit(0 if exito else 1)
