"""
Cliente OPC UA de medida para la comparativa embebido vs externo.

Es el mismo instrumento para ambas soluciones: localiza los nodos por su ruta
de navegacion (BrowseName), no por NodeId, de modo que funciona igual contra el
servidor externo y contra el servidor embebido del PLC, cuyos identificadores
difieren. Todas las medidas se guardan crudas en CSV, con reloj monotonico, y
al final se muestra un resumen (mediana, rango intercuartilico, p95, p99, max).

Modos (subcomandos):
  latencia       Latencia extremo a extremo. modo loopback: escribe una salida
                 cableada a una entrada y mide el tiempo hasta la notificacion
                 de la entrada (incluye PLC y ambos tramos). modo eco: escribe
                 una variable y espera su propia notificacion (sin cableado).
  rendimiento    Suscribe el contador del PLC (y N items extra) y mide el caudal
                 de notificaciones y las perdidas (huecos en el contador).
  fallos         Registra la linea temporal de valor y StatusCode de un nodo
                 (y los huecos del contador) mientras se provoca un fallo
                 externo (desconexion de red, parada del PLC...).
  disponibilidad Sondea un nodo a intervalo fijo y mide el tiempo de
                 indisponibilidad del servicio (para los cambios de modelo).

Conexion comun a todos los modos:
  --endpoint, --ns / --ns-uri, --seguridad {none,cifrado}, --cert-dir,
  --server-cert, --usuario NOMBRE:CLAVE

Ejemplos:
  py herramientas/cliente_medida.py latencia --modo loopback --salida Loopback/Salida \\
     --entrada Loopback/Entrada --n 2000 --csv lat_B.csv
  py herramientas/cliente_medida.py rendimiento --contador Diagnostico/Contador \\
     --duracion-s 60 --csv rend_B.csv
"""

import argparse
import asyncio
import math
import socket
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from asyncua import Client, ua


# ---------------------------------------------------------------------------
# ESTADISTICA Y CSV
# ---------------------------------------------------------------------------
def percentil(datos, p):
    """Percentil p (0..100) por interpolacion lineal, sobre datos ya ordenados."""
    if not datos:
        return float("nan")
    orden = sorted(datos)
    if len(orden) == 1:
        return orden[0]
    k = (len(orden) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return orden[int(k)]
    return orden[f] * (c - k) + orden[c] * (k - f)


def resumen(nombre, datos, unidad="ms"):
    if not datos:
        print(f"  {nombre}: sin muestras")
        return
    orden = sorted(datos)
    q1, q3 = percentil(orden, 25), percentil(orden, 75)
    print(f"  {nombre} (n={len(datos)}): "
          f"mediana={statistics.median(orden):.3f} {unidad}, "
          f"RIC=[{q1:.3f}, {q3:.3f}], "
          f"p95={percentil(orden, 95):.3f}, p99={percentil(orden, 99):.3f}, "
          f"max={orden[-1]:.3f}, media={statistics.fmean(orden):.3f}")


class CSV:
    def __init__(self, ruta, cabecera):
        import csv as _csv
        self._f = open(ruta, "w", newline="", encoding="utf-8")
        self._w = _csv.writer(self._f)
        self._w.writerow(cabecera)
        self._n = 0

    def fila(self, valores):
        self._w.writerow(valores)
        self._n += 1
        if self._n % 100 == 0:
            self._f.flush()

    def cerrar(self):
        self._f.flush()
        self._f.close()


def ahora_iso():
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# CONEXION
# ---------------------------------------------------------------------------
async def cert_cliente(cert_dir):
    from asyncua.crypto.cert_gen import setup_self_signed_certificate
    from cryptography.x509.oid import ExtendedKeyUsageOID
    d = Path(cert_dir)
    d.mkdir(parents=True, exist_ok=True)
    cert, key = d / "cliente_cert.der", d / "cliente_key.pem"
    await setup_self_signed_certificate(
        key, cert, "urn:uja:opcua:cliente-medida", socket.gethostname(),
        [ExtendedKeyUsageOID.CLIENT_AUTH],
        {"countryName": "ES", "organizationName": "Universidad de Jaen",
         "commonName": "Cliente de medida"})
    return str(cert), str(key)


async def conectar(args):
    cli = Client(args.endpoint)
    if args.seguridad == "cifrado":
        from asyncua.crypto.security_policies import SecurityPolicyBasic256Sha256
        cc, ck = await cert_cliente(args.cert_dir)
        if not args.server_cert:
            raise SystemExit("Con --seguridad cifrado hace falta --server-cert (certificado del servidor)")
        await cli.set_security(
            SecurityPolicyBasic256Sha256, cc, ck, server_certificate=args.server_cert,
            mode=ua.MessageSecurityMode.SignAndEncrypt)
    if args.usuario:
        nombre, clave = args.usuario.split(":", 1)
        cli.set_user(nombre)
        cli.set_password(clave)
    await cli.connect()
    return cli


async def indice_ns(cli, args):
    if args.ns is not None:
        return args.ns
    arr = await cli.get_namespace_array()
    if args.ns_uri and args.ns_uri in arr:
        return arr.index(args.ns_uri)
    # Heuristica: el ultimo espacio de nombres personalizado (indice >= 2).
    return len(arr) - 1 if len(arr) > 2 else 1


async def nodo(cli, ns, ruta):
    partes = [f"{ns}:{p}" for p in ruta.strip("/").split("/")]
    return await cli.nodes.objects.get_child(partes)


# ---------------------------------------------------------------------------
# SUSCRIPCION
# ---------------------------------------------------------------------------
class Manejador:
    """Guarda cada notificacion y, para la latencia, despierta al que espera."""

    def __init__(self):
        self.esperado = None
        self.t_recibido = None
        self.evento = asyncio.Event()
        self.registro = []          # (t_perf, nombre_nodo, valor, statuscode)
        self._loop = asyncio.get_event_loop()

    def datachange_notification(self, node, val, data):
        t = time.perf_counter()
        sc = data.monitored_item.Value.StatusCode
        self.registro.append((t, node.nodeid, val, sc))
        if self.esperado is not None and val == self.esperado:
            self.t_recibido = t
            self._loop.call_soon_threadsafe(self.evento.set)


# ---------------------------------------------------------------------------
# MODO LATENCIA
# ---------------------------------------------------------------------------
async def medir_latencia(cli, args):
    ns = await indice_ns(cli, args)
    n_salida = await nodo(cli, ns, args.salida)
    n_entrada = await nodo(cli, ns, args.entrada)

    h = Manejador()
    sub = await cli.create_subscription(args.pub_ms, h)
    await sub.subscribe_data_change(n_entrada)
    await asyncio.sleep(0.3)

    # Estado de partida: el primer objetivo es el contrario al valor actual.
    actual = bool(await n_entrada.read_value())
    objetivo = not actual

    csv = CSV(args.csv, ["iso_time", "iteracion", "objetivo", "rtt_ms", "timeout"])
    rtts, timeouts = [], 0
    calent = max(0, args.calentamiento)

    print(f"Latencia modo {args.modo}: {args.iteraciones} iteraciones "
          f"(descartando {calent} de calentamiento)")
    for i in range(args.iteraciones + calent):
        h.esperado = objetivo
        h.evento.clear()
        t0 = time.perf_counter()
        await n_salida.write_value(ua.DataValue(ua.Variant(objetivo, ua.VariantType.Boolean)))
        try:
            await asyncio.wait_for(h.evento.wait(), timeout=args.timeout_s)
            rtt = (h.t_recibido - t0) * 1000.0
            to = 0
        except asyncio.TimeoutError:
            rtt, to, timeouts = float("nan"), 1, timeouts + 1
        if i >= calent:
            csv.fila([ahora_iso(), i - calent, int(objetivo),
                      f"{rtt:.4f}" if not math.isnan(rtt) else "", to])
            if not math.isnan(rtt):
                rtts.append(rtt)
        objetivo = not objetivo
        await asyncio.sleep(args.intervalo_ms / 1000.0)

    await sub.delete()
    csv.cerrar()
    print(f"\nResultados (timeouts: {timeouts})")
    resumen("RTT extremo a extremo", rtts)


# ---------------------------------------------------------------------------
# MODO RENDIMIENTO (caudal y perdidas)
# ---------------------------------------------------------------------------
async def medir_rendimiento(cli, args):
    ns = await indice_ns(cli, args)
    items = [await nodo(cli, ns, r) for r in args.items] if args.items else []
    n_contador = await nodo(cli, ns, args.contador) if args.contador else None

    h = Manejador()
    sub = await cli.create_subscription(args.pub_ms, h)
    if n_contador is not None:
        await sub.subscribe_data_change(n_contador)
    for it in items:
        await sub.subscribe_data_change(it)

    print(f"Rendimiento: {len(items)} items extra"
          + (" + contador" if n_contador is not None else "")
          + f", durante {args.duracion_s} s")
    h.registro.clear()
    t_ini = time.perf_counter()
    await asyncio.sleep(args.duracion_s)
    await sub.delete()

    # Caudal total de notificaciones
    n_notif = len(h.registro)
    caudal = n_notif / args.duracion_s
    print(f"\nNotificaciones: {n_notif} en {args.duracion_s} s -> {caudal:.1f}/s")

    # Perdidas a partir del contador (saltos mayores que 1)
    csv = CSV(args.csv, ["t_monotonic_s", "contador", "salto"])
    if n_contador is not None:
        valores = [(t, int(v)) for (t, nid, v, sc) in h.registro if nid == n_contador.nodeid]
        huecos = 0
        prev = None
        for t, v in valores:
            salto = None if prev is None else v - prev
            if salto is not None and salto > 1:
                huecos += salto - 1
            csv.fila([f"{t - t_ini:.6f}", v, salto if salto is not None else ""])
            prev = v
        recibidos = len(valores)
        esperados = (valores[-1][1] - valores[0][1] + 1) if len(valores) >= 2 else recibidos
        print(f"Contador: {recibidos} valores recibidos, {huecos} perdidos "
              f"({100 * huecos / max(esperados, 1):.2f}% de {esperados} esperados)")
    csv.cerrar()


# ---------------------------------------------------------------------------
# MODO FALLOS (linea temporal de StatusCode)
# ---------------------------------------------------------------------------
async def medir_fallos(cli, args):
    ns = await indice_ns(cli, args)
    n_nodo = await nodo(cli, ns, args.nodo)
    n_contador = await nodo(cli, ns, args.contador) if args.contador else None

    h = Manejador()
    sub = await cli.create_subscription(args.pub_ms, h)
    await sub.subscribe_data_change(n_nodo)
    if n_contador is not None:
        await sub.subscribe_data_change(n_contador)

    print(f"Fallos: registrando {args.duracion_s} s. Provoque el fallo externamente "
          f"(desconecte la red o pare el PLC) y luego restablezcalo.")
    h.registro.clear()
    t_ini = time.perf_counter()
    await asyncio.sleep(args.duracion_s)
    await sub.delete()

    csv = CSV(args.csv, ["t_monotonic_s", "nodo", "valor", "statuscode", "good"])
    cambios_estado = []
    estado_prev = {}
    for t, nid, val, sc in h.registro:
        nombre = "contador" if (n_contador is not None and nid == n_contador.nodeid) else "nodo"
        buena = sc.is_good()
        csv.fila([f"{t - t_ini:.6f}", nombre, val, sc.name if hasattr(sc, "name") else str(sc),
                  int(buena)])
        if estado_prev.get(nombre) != buena:
            cambios_estado.append((t - t_ini, nombre, "Good" if buena else sc.name))
            estado_prev[nombre] = buena
    csv.cerrar()
    print("\nCambios de calidad (instante s, nodo, estado):")
    for ts, nombre, est in cambios_estado:
        print(f"  {ts:7.2f}  {nombre:8}  {est}")


# ---------------------------------------------------------------------------
# MODO DISPONIBILIDAD (indisponibilidad del servicio)
# ---------------------------------------------------------------------------
async def medir_disponibilidad(cli, args):
    ns = await indice_ns(cli, args)
    n_nodo = await nodo(cli, ns, args.nodo)

    print(f"Disponibilidad: sondeando cada {args.intervalo_ms:g} ms durante {args.duracion_s} s. "
          f"Haga el cambio de modelo cuando quiera; se medira la indisponibilidad.")
    csv = CSV(args.csv, ["t_monotonic_s", "ok", "detalle"])
    t_ini = time.perf_counter()
    cortes = []
    en_corte = None
    while time.perf_counter() - t_ini < args.duracion_s:
        t = time.perf_counter()
        ok, detalle = True, ""
        try:
            dv = await asyncio.wait_for(n_nodo.read_data_value(raise_on_bad_status=False),
                                        timeout=args.timeout_s)
            if not dv.StatusCode.is_good():
                ok, detalle = False, dv.StatusCode.name if hasattr(dv.StatusCode, "name") else "Bad"
        except Exception as e:
            ok, detalle = False, type(e).__name__
        csv.fila([f"{t - t_ini:.6f}", int(ok), detalle])
        if not ok and en_corte is None:
            en_corte = t
        elif ok and en_corte is not None:
            cortes.append((en_corte - t_ini, t - en_corte))
            en_corte = None
        await asyncio.sleep(args.intervalo_ms / 1000.0)
    if en_corte is not None:
        cortes.append((en_corte - t_ini, (time.perf_counter()) - en_corte))
    csv.cerrar()

    total = sum(d for _, d in cortes)
    print(f"\nCortes detectados: {len(cortes)}, indisponibilidad total {total * 1000:.0f} ms")
    for ini, dur in cortes:
        print(f"  inicio t={ini:.2f} s, duracion {dur * 1000:.0f} ms")


# ---------------------------------------------------------------------------
# ARGUMENTOS
# ---------------------------------------------------------------------------
def construir_parser():
    p = argparse.ArgumentParser(description="Cliente OPC UA de medida (comparativa embebido vs externo)",
                                allow_abbrev=False)
    p.add_argument("--endpoint", default="opc.tcp://127.0.0.1:4840")
    p.add_argument("--ns", type=int, default=None, help="Indice de espacio de nombres del modelo")
    p.add_argument("--ns-uri", default="urn:miservidor:ledRojo",
                   help="URI del espacio de nombres del modelo (para resolver --ns)")
    p.add_argument("--seguridad", choices=["none", "cifrado"], default="none")
    p.add_argument("--cert-dir", default="pki_cliente")
    p.add_argument("--server-cert", default=None, help="Certificado del servidor (modo cifrado)")
    p.add_argument("--usuario", default=None, metavar="NOMBRE:CLAVE")

    sub = p.add_subparsers(dest="modo_cmd", required=True)

    a = sub.add_parser("latencia", help="Latencia extremo a extremo")
    a.add_argument("--modo", choices=["loopback", "eco"], default="loopback")
    a.add_argument("--salida", required=True, help="Ruta de la variable de salida a escribir")
    a.add_argument("--entrada", default=None, help="Ruta de la variable de entrada a observar "
                                                  "(en modo eco se toma igual que la salida)")
    a.add_argument("--iteraciones", type=int, default=2000, help="Numero de muestras de RTT")
    a.add_argument("--calentamiento", type=int, default=100, help="Iteraciones iniciales descartadas")
    a.add_argument("--intervalo-ms", type=float, default=50.0, help="Espera entre iteraciones")
    a.add_argument("--pub-ms", type=float, default=10.0, help="Intervalo de publicacion de la suscripcion")
    a.add_argument("--timeout-s", type=float, default=2.0)
    a.add_argument("--csv", default="latencia.csv")

    r = sub.add_parser("rendimiento", help="Caudal de notificaciones y perdidas")
    r.add_argument("--contador", default=None, help="Ruta del contador del PLC (para medir perdidas)")
    r.add_argument("--items", default=None, help="Rutas extra separadas por comas")
    r.add_argument("--duracion-s", type=float, default=60.0)
    r.add_argument("--pub-ms", type=float, default=10.0)
    r.add_argument("--csv", default="rendimiento.csv")

    f = sub.add_parser("fallos", help="Linea temporal de valor y StatusCode ante un fallo")
    f.add_argument("--nodo", required=True, help="Ruta del nodo a observar")
    f.add_argument("--contador", default=None, help="Ruta del contador (para contar notificaciones perdidas)")
    f.add_argument("--duracion-s", type=float, default=60.0)
    f.add_argument("--pub-ms", type=float, default=20.0)
    f.add_argument("--csv", default="fallos.csv")

    d = sub.add_parser("disponibilidad", help="Indisponibilidad del servicio (cambios de modelo)")
    d.add_argument("--nodo", required=True, help="Ruta del nodo a sondear")
    d.add_argument("--intervalo-ms", type=float, default=50.0)
    d.add_argument("--duracion-s", type=float, default=120.0)
    d.add_argument("--timeout-s", type=float, default=2.0)
    d.add_argument("--csv", default="disponibilidad.csv")
    return p


async def main():
    args = construir_parser().parse_args()
    if args.modo_cmd == "latencia":
        if args.modo == "eco":
            args.entrada = args.salida
        elif not args.entrada:
            raise SystemExit("El modo loopback requiere --entrada")
    if getattr(args, "items", None):
        args.items = [x for x in args.items.split(",") if x]

    cli = await conectar(args)
    try:
        if args.modo_cmd == "latencia":
            await medir_latencia(cli, args)
        elif args.modo_cmd == "rendimiento":
            await medir_rendimiento(cli, args)
        elif args.modo_cmd == "fallos":
            await medir_fallos(cli, args)
        elif args.modo_cmd == "disponibilidad":
            await medir_disponibilidad(cli, args)
    finally:
        await cli.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
