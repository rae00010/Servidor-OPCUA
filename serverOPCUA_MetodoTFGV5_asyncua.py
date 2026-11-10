"""
Servidor OPC UA (asyncua) con carga de NodeSet XML y puente hacia un
controlador a traves de un driver downstream intercambiable.

V5. Pasos aplicados respecto a V4:
  Paso 2 - Tabla de mapeo generica construida al arrancar a partir de las
           variables cuyo TypeDefinition es el VariableType Address. El
           sentido sale del atributo AccessLevel (solo lectura: PLC -> OPC UA;
           escribible: OPC UA -> PLC, por eventos). Los metodos los declara
           el NodeSet; el servidor enlaza su implementacion por BrowseName
           (Fase B, IMPLEMENTACIONES).
  Paso 3 - Capa de driver (driver_base.py). El servidor no conoce el
           protocolo. Driver Snap7 con lectura agrupada, timeouts y PDU.
  Paso 7 - Robustez ante fallos del enlace:
           * Supervisor que reconecta con espera creciente (1, 2, 4... s) sin
             detener el servidor. Si el PLC no esta al arrancar, el servidor
             arranca igualmente y espera.
           * Calidad de los datos (StatusCode):
               BadWaitingForInitialData  hasta la primera lectura
               UncertainLastUsableValue  enlace caido, se conserva el ultimo valor
               BadNoCommunication        enlace caido y sin valor previo
               Good                      dato leido del PLC
             Una variable cuyo rango falla FALLOS_MAX ciclos seguidos se
             degrada aunque el resto del enlace funcione.
           * Escrituras de clientes: si el enlace esta caido o la escritura en
             el PLC falla, el cliente recibe BadNoCommunication o
             BadCommunicationError y la variable recupera su valor anterior.
           * El metodo devuelve StatusCode de error en lugar de un texto.
           * Al reconectar, las variables escribibles toman el valor del PLC
             (el servidor nunca escribe en el PLC sin orden de un cliente).
           * Log limitado: los errores repetidos se resumen periodicamente.
           * Sin simulacion interna (Fase C): sin PLC se usa un PLC emulado.

Pendiente: periodo fijo (4), registro crudo (5), escritura sin
Read-Modify-Write (6), simulacion fuera del servidor (8), endpoint en red (9),
seguridad (10).

Modelo de referencia de la celula: modelo_celula.xml, generado con
    py modelo.py planta_celula.yaml -o modelo_celula.xml
(ledmodel_v5.xml se conserva solo para pruebas de regresion).

Uso:
    python serverOPCUA_MetodoTFGV5_asyncua.py -m modelo_celula.xml
           [--plc-ip 192.168.0.1] [--rack 0] [--slot 1] [--timeout-ms 1000]
           [--fallos-max 3] [--reintento-max-s 10]
"""

import argparse
import asyncio
import csv
import json
import logging
import os
import sys
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone

from asyncua import Server, ua
from asyncua.common.callback import CallbackType
from asyncua.ua import NodeClass

from driver_base import DriverError, PuntoDriver
from drivers import DRIVERS, crear_driver
import modelo as modelo_planta
from configuracion import ErrorConfig, como_args, configuracion_efectiva, usuarios_de
from modelo import ErrorPlanta, comprobar_variable, mensaje_modelo_incompleto
from seguridad import aplicar_seguridad, crear_gestor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("asyncua").setLevel(logging.WARNING)
logging.getLogger("snap7").setLevel(logging.CRITICAL)   # los errores los gestiona el driver
logger = logging.getLogger("OPCUA-Server")

# ---------------------------------------------------------------------------
# CONFIGURACION
# ---------------------------------------------------------------------------
PLC_IP = "192.168.0.1"
PLC_RACK = 0
PLC_SLOT = 1
FALLOS_MAX = 3                     # ciclos fallidos seguidos para degradar una variable
REINTENTO_MAX_S = 10               # espera maxima entre intentos de reconexion
INTERVALO_RESUMEN_S = 30           # resumen de errores repetidos en el log
PERIODO_S = 0.1                    # espera tras cada ciclo (se sustituye en el paso 4)
APP_URI = "urn:uja:opcua:servidor-desacoplado"
VERSION = "5.0"
# Perfil de servidor que se declara (aspirado). La conformidad real se verifica
# con la Compliance Test Tool de la OPC Foundation.
PERFIL_STANDARD = "http://opcfoundation.org/UA-Profile/Server/StandardUA"

# Tipos del modelo que el servidor necesita. Se localizan por BrowseName dentro
# del espacio de nombres del NodeSet cargado, no por NodeId (paso A4).
NOMBRE_TIPO_ADDRESS = "Address"     # VariableType con la direccion fisica

SC = ua.StatusCodes
GOOD = ua.StatusCode(SC.Good)

# ---------------------------------------------------------------------------
# ESTADO
# ---------------------------------------------------------------------------
server_global = None
NS_IDX = None
NS_URI = None
ADDRESS_ID = None
DRIVER = None
PLAN_LECTURA = None
ENLACE = {"conectado": False, "caido_desde": None, "reconexiones": 0, "intentos": 0}


@dataclass
class Punto:
    """Variable del modelo enlazada a una direccion del controlador."""
    nodo: object                   # asyncua Node
    pd: PuntoDriver                # vista del punto para el driver
    escritura: bool                # True: OPC UA -> controlador
    tipo: object = ua.VariantType.Boolean   # VariantType del DataType de la variable
    valor: object = None           # ultimo valor conocido
    estado: object = None          # StatusCode publicado
    ts: object = None              # SourceTimestamp del ultimo valor bueno
    fallos: int = 0                # lecturas fallidas seguidas

    @property
    def ruta(self):
        return self.pd.ruta

    @property
    def direccion(self):
        return DRIVER.texto_direccion(self.pd.direccion)


def convertir(tipo, valor):
    """Valor Python del VariantType de la variable (bool, int o float)."""
    if tipo == ua.VariantType.Boolean:
        return bool(valor)
    if tipo in (ua.VariantType.Float, ua.VariantType.Double):
        return float(valor)
    return int(valor)


PUNTOS: list[Punto] = []
PUNTOS_POR_NODEID: dict = {}


# ---------------------------------------------------------------------------
# LOG LIMITADO
# ---------------------------------------------------------------------------
class AvisosLimitados:
    """Registra la primera aparicion de un aviso y resume las repeticiones."""

    def __init__(self, intervalo_s):
        self.intervalo = intervalo_s
        self._estado = {}            # clave -> [instante del ultimo log, suprimidos]

    def aviso(self, clave, mensaje, nivel=logging.WARNING):
        ahora = time.monotonic()
        e = self._estado.get(clave)
        if e is None or ahora - e[0] >= self.intervalo:
            extra = f" ({e[1]} repeticiones en los ultimos {self.intervalo} s)" if e and e[1] else ""
            logger.log(nivel, mensaje + extra)
            self._estado[clave] = [ahora, 0]
        else:
            e[1] += 1

    def olvidar(self, clave):
        self._estado.pop(clave, None)


AVISOS = AvisosLimitados(INTERVALO_RESUMEN_S)

# ---------------------------------------------------------------------------
# REGISTRO CRUDO A CSV (paso 9)
# ---------------------------------------------------------------------------
class RegistroCSV:
    """Escribe filas en un CSV con volcado periodico al disco."""

    def __init__(self, ruta, cabecera):
        if os.path.dirname(ruta):
            os.makedirs(os.path.dirname(ruta), exist_ok=True)
        self._f = open(ruta, "w", newline="", encoding="utf-8")
        self._w = csv.writer(self._f)
        self._w.writerow(cabecera)
        self._n = 0

    def fila(self, valores):
        self._w.writerow(valores)
        self._n += 1
        if self._n % 50 == 0:
            self._f.flush()

    def cerrar(self):
        try:
            self._f.flush(); self._f.close()
        except Exception:
            pass



# ---------------------------------------------------------------------------
# INSTRUMENTACION DEL CICLO (se sustituye por registro crudo en el paso 5)
# ---------------------------------------------------------------------------
RESUMEN_CADA = 100
_hist_ciclos = []
_metricas = None


def _init_metricas():
    token = os.getenv("INFLUX_TOKEN")
    if not token:
        logger.info("Metricas del ciclo: solo resumen por consola (sin INFLUX_TOKEN)")
        return None
    try:
        from influxdb_client import InfluxDBClient
        from influxdb_client.client.write_api import WriteOptions
        client = InfluxDBClient(
            url=os.getenv("INFLUX_URL", "http://localhost:8086"),
            token=token,
            org=os.getenv("INFLUX_ORG", "org_tfg"),
            timeout=2000,
        )
        wa = client.write_api(write_options=WriteOptions(batch_size=50, flush_interval=1000))
        logger.info("Metricas del ciclo activadas (InfluxDB)")
        return wa, os.getenv("INFLUX_BUCKET", "plc_data")
    except Exception as e:
        logger.warning(f"No se pudieron activar las metricas en InfluxDB: {e}")
        return None


def _registrar_ciclo(trabajo_ms, periodo_ms, acceso_ms, peticiones):
    if _metricas:
        try:
            from influxdb_client import Point as InfluxPoint
            p = (InfluxPoint("ciclo_servidor")
                 .field("trabajo_ms", trabajo_ms)
                 .field("snap7_ms", acceso_ms)
                 .field("snap7_n", peticiones))
            if periodo_ms is not None:
                p = p.field("periodo_ms", periodo_ms)
            _metricas[0].write(bucket=_metricas[1], record=p)
        except Exception as e:
            logger.debug(f"Error escribiendo metrica de ciclo: {e}")

    if periodo_ms is not None:
        _hist_ciclos.append((trabajo_ms, periodo_ms, acceso_ms, peticiones))
    if len(_hist_ciclos) >= RESUMEN_CADA:
        t = [c[0] for c in _hist_ciclos]
        per = sorted(c[1] for c in _hist_ciclos)
        s7 = [c[2] for c in _hist_ciclos]
        logger.info(
            f"[CICLO] n={len(per)} | periodo medio={sum(per)/len(per):.1f} ms "
            f"p99={per[int(0.99 * (len(per) - 1))]:.1f} max={per[-1]:.1f} | "
            f"trabajo medio={sum(t)/len(t):.1f} ms | acceso PLC medio={sum(s7)/len(s7):.1f} ms "
            f"en {_hist_ciclos[-1][3]} peticiones"
        )
        _hist_ciclos.clear()


# ---------------------------------------------------------------------------
# PUBLICACION CON CALIDAD
# ---------------------------------------------------------------------------
async def publicar(p: Punto, valor, estado=GOOD, ts=None):
    """
    Publica valor y StatusCode en el espacio de direcciones.
      Good      -> valor nuevo con SourceTimestamp actual
      Uncertain -> se conserva el valor y el SourceTimestamp del ultimo dato bueno
      Bad       -> sin valor (la especificacion exige valor nulo)
    """
    ahora = datetime.now(timezone.utc)
    if estado.is_good():
        p.valor, p.ts = convertir(p.tipo, valor), ts or ahora
        variant = ua.Variant(p.valor, p.tipo)
        source_ts = p.ts
    elif estado.is_bad():
        variant, source_ts = ua.Variant(None, ua.VariantType.Null), ahora
    else:
        variant = ua.Variant(p.valor, p.tipo)
        source_ts = p.ts or ahora
    p.estado = estado
    await p.nodo.write_value(ua.DataValue(Value=variant, StatusCode=estado,
                                          SourceTimestamp=source_ts, ServerTimestamp=ahora))


async def degradar(p: Punto):
    """Enlace o lectura perdidos: Uncertain si hay valor previo, Bad si no."""
    if p.valor is None:
        destino = SC.BadNoCommunication
    else:
        destino = SC.UncertainLastUsableValue
    if p.estado is None or p.estado.value != destino:
        await publicar(p, p.valor, ua.StatusCode(destino))


async def restaurar(p: Punto):
    """Devuelve una variable a su ultimo valor y calidad publicados."""
    await publicar(p, p.valor, p.estado or ua.StatusCode(SC.BadWaitingForInitialData), ts=p.ts)


# ---------------------------------------------------------------------------
# TABLA DE MAPEO
# ---------------------------------------------------------------------------
async def _browse_name(nodo):
    try:
        return (await nodo.read_browse_name()).Name
    except Exception:
        return None


async def construir_tabla_mapeo(server):
    """
    Recorre una sola vez el arbol bajo Objects y registra cada variable cuyo
    TypeDefinition es Address. modelo.comprobar_variable (el mismo codigo que
    usa la importacion de la interfaz) y el driver validan su direccion.
    Si alguna variable no es valida, el servidor no arranca y las lista todas.
    """
    address_id = ADDRESS_ID
    errores = []

    async def visitar(nodo, ruta):
        for hijo in await nodo.get_children():
            if hijo.nodeid.NamespaceIndex == 0:
                continue
            nombre = await _browse_name(hijo)
            ruta_hijo = f"{ruta}/{nombre}" if ruta else nombre
            if await hijo.read_node_class() == NodeClass.Variable:
                if await hijo.read_type_definition() == address_id:
                    await registrar(hijo, ruta_hijo)
                continue
            await visitar(hijo, ruta_hijo)

    async def registrar(var, ruta):
        props = {}
        for prop in await var.get_properties():
            props[await _browse_name(prop)] = await prop.read_value()
        try:
            tipo = await var.read_data_type_as_variant_type()
        except Exception:
            tipo = None                       # DataType que no es un tipo basico
        try:
            direccion = comprobar_variable(ruta, props, tipo, DRIVER.validar_direccion)
        except ValueError as e:
            errores.append(str(e))
            return
        acceso = (await var.read_attribute(ua.AttributeIds.AccessLevel)).Value.Value
        p = Punto(nodo=var, pd=PuntoDriver(clave=var.nodeid, ruta=ruta, direccion=direccion),
                  escritura=bool(int(acceso) & ua.AccessLevel.CurrentWrite.mask), tipo=tipo)
        PUNTOS.append(p)
        PUNTOS_POR_NODEID[var.nodeid] = p

    await visitar(server.get_objects_node(), "")
    if errores:
        raise RuntimeError(mensaje_modelo_incompleto(errores))

    vistas = {}
    for p in PUNTOS:
        if p.escritura:
            if p.direccion in vistas:
                logger.warning(f"{p.direccion} escrita por {vistas[p.direccion]} y {p.ruta}")
            vistas[p.direccion] = p.ruta

    logger.info(f"Tabla de mapeo: {len(PUNTOS)} variables (driver {DRIVER.nombre})")
    for p in sorted(PUNTOS, key=lambda p: (p.escritura, p.direccion)):
        sentido = "OPC UA -> PLC" if p.escritura else "PLC -> OPC UA"
        logger.info(f"  {p.direccion:<14} {sentido:<14} {p.ruta}")


def planificar():
    """Calcula el plan de lectura con la PDU de la conexion actual."""
    global PLAN_LECTURA
    PLAN_LECTURA = DRIVER.planificar_lectura([p.pd for p in PUNTOS if not p.escritura])
    logger.info(f"Plan de lectura: {len(PLAN_LECTURA)} peticiones por ciclo "
                f"(PDU {getattr(DRIVER, 'pdu', None) or 'por defecto'})")
    for linea in DRIVER.describir_plan(PLAN_LECTURA):
        logger.info(f"  {linea}")


# ---------------------------------------------------------------------------
# ESCRITURAS DE CLIENTES (por eventos)
# ---------------------------------------------------------------------------
async def al_escribir_cliente(evento, _dispatcher):
    """
    Callback PostWrite. La lista evento.response_params es la misma que se
    devuelve al cliente en la respuesta Write: si la escritura en el PLC no
    es posible, se sustituye su Good por el StatusCode del error y la
    variable recupera su valor anterior.
    """
    if not evento.is_external:
        return
    resultados = evento.response_params
    for i, wv in enumerate(evento.request_params.NodesToWrite):
        if wv.AttributeId != ua.AttributeIds.Value or not resultados[i].is_good():
            continue
        p = PUNTOS_POR_NODEID.get(wv.NodeId)
        if p is None or not p.escritura:
            continue
        valor = convertir(p.tipo, wv.Value.Value.Value)
        if not DRIVER.conectado:
            error = SC.BadNoCommunication
        else:
            try:
                await DRIVER.escribir(p.pd, valor)
                await publicar(p, valor)
                continue
            except DriverError as e:
                AVISOS.aviso(("escritura", p.direccion), f"Error escribiendo {p.direccion} ({p.ruta}): {e}",
                             logging.ERROR)
                error = SC.BadCommunicationError
        resultados[i] = ua.StatusCode(error)
        await restaurar(p)


# ---------------------------------------------------------------------------
# METODO OPC UA: ControlarPulsador
# ---------------------------------------------------------------------------
async def controlar_pulsador(parent_nodeid, accion):
    """
    Entrada: String "pulsar" o "soltar". Salida: String con el resultado.
    Los errores se devuelven como StatusCode del metodo, no como texto.
    """
    accion = str(getattr(accion, "Value", accion)).lower()
    if accion not in ("pulsar", "soltar"):
        return ua.StatusCode(SC.BadInvalidArgument)
    parent = server_global.get_node(parent_nodeid)
    try:
        var = await parent.get_child([f"{NS_IDX}:Control", f"{NS_IDX}:PulsadoRemoto"])
    except ua.UaError:
        return ua.StatusCode(SC.BadNodeIdUnknown)
    p = PUNTOS_POR_NODEID.get(var.nodeid)
    if p is None:
        return ua.StatusCode(SC.BadConfigurationError)

    valor = accion == "pulsar"
    if not DRIVER.conectado:
        return ua.StatusCode(SC.BadNoCommunication)
    try:
        await DRIVER.escribir(p.pd, valor)
    except DriverError as e:
        AVISOS.aviso(("metodo", p.direccion), f"Error en ControlarPulsador ({p.ruta}): {e}",
                     logging.ERROR)
        return ua.StatusCode(SC.BadCommunicationError)
    await publicar(p, valor)
    return [ua.Variant("Pulsador pulsado" if valor else "Pulsador soltado", ua.VariantType.String)]


# Implementaciones de los metodos que puede declarar el modelo, por BrowseName
IMPLEMENTACIONES = {"ControlarPulsador": controlar_pulsador}


async def enlazar_metodos(server):
    """
    Enlaza cada metodo declarado en el NodeSet (bajo Objects, NodeId y
    BrowseName en el espacio del modelo) con su implementacion por BrowseName.
    Un metodo sin implementacion solo se avisa.
    """
    enlazados = {k: 0 for k in IMPLEMENTACIONES}
    sin_implementacion = {}

    async def visitar(nodo, ruta):
        for hijo in await nodo.get_children():
            if hijo.nodeid.NamespaceIndex != NS_IDX:
                continue
            clase = await hijo.read_node_class()
            if clase == NodeClass.Variable:
                continue
            qn = await hijo.read_browse_name()
            ruta_hijo = f"{ruta}/{qn.Name}" if ruta else qn.Name
            if clase == NodeClass.Method:
                if qn.NamespaceIndex != NS_IDX:
                    continue
                if qn.Name in IMPLEMENTACIONES:
                    server.link_method(hijo, IMPLEMENTACIONES[qn.Name])
                    enlazados[qn.Name] += 1
                else:
                    sin_implementacion.setdefault(qn.Name, []).append(ruta_hijo)
            elif clase == NodeClass.Object:
                await visitar(hijo, ruta_hijo)

    await visitar(server.get_objects_node(), "")
    for nombre, n in enlazados.items():
        if n:
            logger.info(f"Metodo {nombre} enlazado a {n} instancias")
    for nombre, rutas in sin_implementacion.items():
        logger.warning(f"El modelo declara el metodo {nombre} ({', '.join(rutas)}) "
                       f"sin implementacion en el servidor: no se enlaza")


# ---------------------------------------------------------------------------
# CARGA DEL MODELO
# ---------------------------------------------------------------------------
def uri_del_nodeset(xml_file):
    """
    URI del modelo: atributo ModelUri de <Models><Model> del NodeSet. Sin el,
    el servidor no arranca. Con varios <Model>, se usa el primero y se avisa.
    """
    uris = []
    for _, el in ET.iterparse(xml_file, events=("end",)):
        etiqueta = el.tag.rsplit("}", 1)[-1]
        if etiqueta == "Model" and el.get("ModelUri"):
            uris.append(el.get("ModelUri").strip())
        elif etiqueta == "Models":
            break
    if not uris:
        raise RuntimeError(f"{xml_file}: el NodeSet no declara <Models><Model ModelUri=...>")
    if len(uris) > 1:
        logger.warning(f"El NodeSet declara {len(uris)} modelos {uris}: se usa el primero")
    return uris[0]


async def buscar_tipo(server, raiz_id, nombre):
    """
    NodeId del subtipo de raiz_id que pertenece al espacio de nombres del
    modelo (NodeId y BrowseName en NS_IDX) y se llama 'nombre', o None.
    """
    pendientes = [server.get_node(raiz_id)]
    while pendientes:
        nodo = pendientes.pop()
        for hijo in await nodo.get_children(refs=ua.ObjectIds.HasSubtype):
            qn = await hijo.read_browse_name()
            if (hijo.nodeid.NamespaceIndex == NS_IDX and qn.NamespaceIndex == NS_IDX
                    and qn.Name == nombre):
                return hijo.nodeid
            pendientes.append(hijo)
    return None


async def cargar_modelo(server, xml_file):
    global NS_IDX, NS_URI, ADDRESS_ID
    NS_URI = uri_del_nodeset(xml_file)
    await server.register_namespace(NS_URI)
    await server.import_xml(xml_file)
    NS_IDX = await server.get_namespace_index(NS_URI)
    ADDRESS_ID = await buscar_tipo(server, ua.ObjectIds.BaseVariableType, NOMBRE_TIPO_ADDRESS)
    if ADDRESS_ID is None:
        raise RuntimeError(f"El modelo ({NS_URI}) no define el VariableType {NOMBRE_TIPO_ADDRESS}")
    logger.info(f"Modelo {NS_URI} (ns={NS_IDX}): {NOMBRE_TIPO_ADDRESS}={ADDRESS_ID.to_string()}")
    await construir_tabla_mapeo(server)
    await enlazar_metodos(server)
    for p in PUNTOS:
        await publicar(p, None, ua.StatusCode(SC.BadWaitingForInitialData))
    logger.info("Modelo cargado desde NodeSet2 con import_xml (tipos e instancias).")


async def sincronizar_escrituras():
    """Las variables escribibles toman el valor que tiene el PLC."""
    escribibles = [p for p in PUNTOS if p.escritura]
    valores, fallidas = await DRIVER.leer(DRIVER.planificar_lectura([p.pd for p in escribibles]))
    for p in escribibles:
        if p.pd.clave in valores:
            await publicar(p, valores[p.pd.clave])
        else:
            await degradar(p)


# ---------------------------------------------------------------------------
# METADATOS DEL SERVIDOR (paso 7)
# ---------------------------------------------------------------------------
async def aplicar_metadatos(server, perfiles):
    """Rellena BuildInfo y ServerProfileArray. Sin perfiles, el array queda
    vacio: solo debe declararse un perfil verificado (Compliance Test Tool)."""
    await server.set_build_info(
        product_uri=APP_URI + ":producto",
        manufacturer_name="Universidad de Jaen (GRAV)",
        product_name="Servidor OPC UA desacoplado",
        software_version=VERSION,
        build_number="1",
        build_date=datetime.now(timezone.utc),
    )
    perfil_node = server.get_node(ua.NodeId(ua.ObjectIds.Server_ServerCapabilities_ServerProfileArray))
    await perfil_node.write_value(ua.Variant(list(perfiles), ua.VariantType.String))
    logger.info(f"Metadatos: fabricante 'Universidad de Jaen (GRAV)', version {VERSION}")
    if perfiles:
        logger.info(f"Perfil declarado: {', '.join(perfiles)}")
    else:
        logger.info("Perfil declarado: ninguno (ServerProfileArray vacio)")


# ---------------------------------------------------------------------------
# SUPERVISOR DEL ENLACE
# ---------------------------------------------------------------------------
async def al_caer_enlace():
    ENLACE["conectado"] = False
    ENLACE["caido_desde"] = time.monotonic()
    logger.warning("Enlace con el PLC perdido: variables en UncertainLastUsableValue / BadNoCommunication")
    for p in PUNTOS:
        await degradar(p)


async def supervisor_enlace():
    """
    Mantiene la conexion con el PLC. Si cae, reintenta con espera creciente
    (1, 2, 4... hasta REINTENTO_MAX_S) sin detener el servidor OPC UA.
    """
    espera = 1
    while True:
        if DRIVER.conectado:
            espera = 1
            await asyncio.sleep(0.2)
            continue
        ENLACE["intentos"] += 1
        try:
            await DRIVER.desconectar()
            await DRIVER.conectar()
        except DriverError as e:
            AVISOS.aviso("conexion", f"Sin conexion con el PLC {PLC_IP}: {e}. Reintentando con espera creciente (max. {REINTENTO_MAX_S:g} s)")
            if ENLACE["caido_desde"] is None and ENLACE["reconexiones"] == 0:
                # Primer arranque sin PLC: las variables dejan de "esperar datos".
                for p in PUNTOS:
                    if p.estado is not None and p.estado.value == SC.BadWaitingForInitialData:
                        await publicar(p, None, ua.StatusCode(SC.BadNoCommunication))
            await asyncio.sleep(espera)
            espera = min(espera * 2, REINTENTO_MAX_S)
            continue

        AVISOS.olvidar("conexion")
        planificar()
        try:
            await sincronizar_escrituras()
        except DriverError:
            pass
        if ENLACE["caido_desde"] is not None:
            caida = time.monotonic() - ENLACE["caido_desde"]
            logger.info(f"Enlace con el PLC restablecido tras {caida:.1f} s "
                        f"({ENLACE['intentos']} intentos)")
        else:
            logger.info(f"Conectado al PLC en {PLC_IP} ({DRIVER.nombre}: rack {PLC_RACK}, slot {PLC_SLOT}), "
                        f"PDU negociada {getattr(DRIVER, 'pdu', None)} bytes")
        ENLACE.update(conectado=True, caido_desde=None, intentos=0)
        ENLACE["reconexiones"] += 1
        espera = 1


# ---------------------------------------------------------------------------
# CICLO DE SINCRONIZACION
# ---------------------------------------------------------------------------
async def ciclo_lectura():
    """
    Controlador -> OPC UA: ejecuta el plan y publica cambios de valor o calidad.
    Devuelve el numero de variables publicadas en este ciclo.
    """
    valores, fallidas = await DRIVER.leer(PLAN_LECTURA)
    con_error = []
    publicados = 0
    for p in PUNTOS:
        if p.escritura:
            continue
        clave = p.pd.clave
        if clave in valores:
            p.fallos = 0
            if valores[clave] != p.valor or not (p.estado and p.estado.is_good()):
                await publicar(p, valores[clave]); publicados += 1
        elif clave in fallidas:
            p.fallos += 1
            con_error.append(p.direccion)
            if p.fallos >= FALLOS_MAX:
                await degradar(p)
    # Un solo aviso por conjunto de variables afectadas. Si falla todo el plan
    # es un problema de enlace, que ya notifica el supervisor.
    if con_error and valores:
        AVISOS.aviso(("lectura", tuple(con_error)),
                     f"Lectura parcial fallida: {len(con_error)} variables sin datos ({', '.join(con_error)})")
    return publicados


# ---------------------------------------------------------------------------
# ARRANQUE
# ---------------------------------------------------------------------------
def crear_parser():
    """
    Opciones de la linea de ordenes. Todas tienen su clave en config.yaml
    (configuracion.OPCIONES_CLI) salvo las de control del proceso. Por defecto
    valen None: solo las dadas explicitamente prevalecen sobre --config.
    """
    p = argparse.ArgumentParser(description="Servidor OPC UA (asyncua) desacoplado")
    p.add_argument("--config", metavar="RUTA", help="Fichero de configuracion YAML (Fase C)")
    p.add_argument("-m", "--model", help="Ruta al NodeSet XML (modelo.nodeset)")
    p.add_argument("--driver", choices=sorted(DRIVERS),
                   help="Protocolo aguas abajo (driver registrado en drivers.py); por defecto snap7")
    p.add_argument("--driver-param", action="append", metavar="CLAVE=VALOR",
                   help="Parametro del driver (se puede repetir), p.ej. --driver-param ip=10.0.0.5")
    p.add_argument("--plc-ip", help=f"Equivale a --driver-param ip= (por defecto {PLC_IP})")
    p.add_argument("--rack", type=int, help="Equivale a --driver-param rack=")
    p.add_argument("--slot", type=int, help="Equivale a --driver-param slot=")
    p.add_argument("--timeout-ms", type=int, help="Equivale a --driver-param timeout_ms=")
    p.add_argument("--fallos-max", type=int,
                   help=f"Peticiones o ciclos fallidos seguidos para dar por perdido el dato o el enlace "
                        f"(por defecto {FALLOS_MAX})")
    p.add_argument("--reintento-max-s", type=float,
                   help=f"Espera maxima entre intentos de reconexion (por defecto {REINTENTO_MAX_S})")
    p.add_argument("--periodo-ms", type=float, help="Periodo objetivo del ciclo, en ms (por defecto 100)")
    p.add_argument("--log-csv", metavar="RUTA", help="Registra en CSV una fila por ciclo (crudo, para estadistica)")
    p.add_argument("--log-accesos", metavar="RUTA", help="Registra en CSV una fila por cada acceso al PLC")
    p.add_argument("--endpoint", help="Endpoint del servidor OPC UA (por defecto opc.tcp://127.0.0.1:4840; "
                                      "0.0.0.0 para aceptar clientes de otras maquinas)")
    p.add_argument("--seguridad", choices=["none", "firmar", "estricto"],
                   help="none: sin cifrado; firmar: None + Basic256Sha256, Aes128-Sha256-RsaOaep y "
                        "Aes256-Sha256-RsaPss en Sign/SignAndEncrypt; estricto: solo esas politicas seguras")
    p.add_argument("--cert-dir", help="Carpeta del certificado y la clave del servidor (por defecto pki)")
    p.add_argument("--lista-confianza", action=argparse.BooleanOptionalAction, default=None,
                   help="Valida los certificados de cliente contra <cert-dir>/confianza y guarda los "
                        "rechazados en <cert-dir>/rechazados (firmar y estricto; activada por defecto)")
    p.add_argument("--usuario", action="append", metavar="NOMBRE:CLAVE",
                   help="Habilita autenticacion usuario-contrasena (se puede repetir)")
    p.add_argument("--app-uri", help=f"ApplicationUri del servidor (por defecto {APP_URI})")
    p.add_argument("--host", help="Nombre de host para el certificado (por defecto, el del sistema)")
    p.add_argument("--perfil", action="append", metavar="URI",
                   help="Perfil de servidor declarado en ServerProfileArray (se puede repetir). Por defecto, "
                        "ninguno. Declare solo un perfil verificado con la Compliance Test Tool")
    # Control del proceso (sin clave en el YAML): parada ordenada sin senales
    p.add_argument("--parada-stdin", action="store_true",
                   help="La linea 'parar' en la entrada estandar detiene el servidor de forma ordenada; "
                        "cerrar la entrada solo se avisa")
    p.add_argument("--fichero-pid", metavar="RUTA",
                   help="Escribe el PID (JSON, con latido periodico) y lo borra al terminar")
    p.add_argument("--fichero-control", metavar="RUTA",
                   help="Si aparece este fichero, el servidor lo borra y se detiene de forma ordenada")
    return p


# ---------------------------------------------------------------------------
# PARADA ORDENADA (Fase C)
# ---------------------------------------------------------------------------
LATIDO_S = 1.0       # cada cuanto se vigila el fichero de control y se renueva el PID


def _escribir_pid(ruta, info):
    tmp = f"{ruta}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(info, f)
    os.replace(tmp, ruta)


def _hilo_stdin(loop, parada):
    """Lee la entrada estandar. 'parar' detiene; el cierre de la tuberia solo se avisa."""
    for linea in sys.stdin:
        orden = linea.strip().lower()
        if orden == "parar":
            loop.call_soon_threadsafe(logger.info, "Orden de parada recibida (entrada estandar)")
            loop.call_soon_threadsafe(parada.set)
            return
        if orden:
            loop.call_soon_threadsafe(logger.warning, f"Orden desconocida en la entrada estandar: {orden!r}")
    loop.call_soon_threadsafe(logger.warning, "Entrada estandar cerrada: el servidor sigue en marcha "
                                              "(se detiene con 'parar' o con el fichero de control)")


async def preparar_parada(parada, opciones, args):
    """Activa los mecanismos de parada pedidos. Devuelve la tarea vigilante o None."""
    if opciones.parada_stdin:
        threading.Thread(target=_hilo_stdin, args=(asyncio.get_running_loop(), parada),
                         daemon=True, name="parada-stdin").start()
    if not (opciones.fichero_pid or opciones.fichero_control):
        return None
    info = {"pid": os.getpid(), "inicio": datetime.now(timezone.utc).isoformat(),
            "endpoint": args.endpoint, "config": opciones.config,
            "fichero_control": opciones.fichero_control}
    if opciones.fichero_control and os.path.exists(opciones.fichero_control):
        os.remove(opciones.fichero_control)          # orden antigua, de otra ejecucion
    if opciones.fichero_pid:
        _escribir_pid(opciones.fichero_pid, info)
        logger.info(f"PID {info['pid']} en {opciones.fichero_pid}")

    async def vigilar():
        while not parada.is_set():
            await asyncio.sleep(LATIDO_S)
            if opciones.fichero_control and os.path.exists(opciones.fichero_control):
                try:
                    os.remove(opciones.fichero_control)
                except OSError:
                    pass
                logger.info(f"Orden de parada recibida (fichero de control {opciones.fichero_control})")
                parada.set()
            elif opciones.fichero_pid:
                try:
                    os.utime(opciones.fichero_pid)            # latido
                except OSError:
                    _escribir_pid(opciones.fichero_pid, info)
    return asyncio.create_task(vigilar())


def preparar_modelo(cfg):
    """Ruta del NodeSet a cargar. Con origen 'planta', lo genera antes (Fase B)."""
    m = cfg["modelo"]
    if m["origen"] != "planta":
        return m["nodeset"]
    planta = modelo_planta.cargar_planta(m["planta"])
    if planta.driver != cfg["driver"]["nombre"]:
        raise ErrorPlanta([f"la planta usa el driver '{planta.driver}' y la configuracion "
                           f"'{cfg['driver']['nombre']}'"])
    for aviso in planta.avisos:
        logger.warning(f"Planta: {aviso}")
    modelo_planta.generar_fichero(planta, m["nodeset"])
    logger.info(f"NodeSet generado desde {m['planta']} en {m['nodeset']}")
    return m["nodeset"]


async def main():
    global server_global, _metricas, DRIVER, PLC_IP, PLC_RACK, PLC_SLOT
    global FALLOS_MAX, REINTENTO_MAX_S

    parser = crear_parser()
    opciones = parser.parse_args()
    try:
        cfg, avisos = configuracion_efectiva(opciones.config, opciones)
        modelo_xml = preparar_modelo(cfg)
    except (ErrorConfig, ErrorPlanta) as e:
        parser.exit(2, f"{e}\n")
    for aviso in avisos:
        logger.warning(f"Configuracion: {aviso}")
    if opciones.config:
        logger.info(f"Configuracion cargada de {opciones.config}")
    args = como_args(cfg)
    PLC_IP = args.parametros.get("ip", "?")
    PLC_RACK, PLC_SLOT = args.parametros.get("rack"), args.parametros.get("slot")
    FALLOS_MAX, REINTENTO_MAX_S = args.fallos_max, args.reintento_max_s

    DRIVER = crear_driver(args.driver, {**args.parametros, "fallos_max": FALLOS_MAX})
    _metricas = _init_metricas()

    import socket
    usuarios = usuarios_de(cfg)
    host_name = args.host or socket.gethostname()

    server = Server(user_manager=crear_gestor(usuarios, args.seguridad))
    server_global = server
    await server.init()
    server.set_endpoint(args.endpoint)
    server.set_server_name("Servidor OPC UA desacoplado (UJA)")
    await aplicar_seguridad(server, args.seguridad, args.cert_dir, usuarios,
                            args.app_uri, host_name, lista_confianza=args.lista_confianza)

    await cargar_modelo(server, modelo_xml)
    await aplicar_metadatos(server, args.perfil or [])
    server.subscribe_server_callback(CallbackType.PostWrite, al_escribir_cliente)

    periodo = args.periodo_ms / 1000.0
    log_ciclos = RegistroCSV(args.log_csv,
        ["iso_time", "t_monotonic_s", "periodo_ms", "trabajo_ms", "acceso_plc_ms",
         "n_peticiones", "bytes_leidos", "n_publicados", "desborde"]) if args.log_csv else None
    log_accesos = RegistroCSV(args.log_accesos,
        ["iso_time", "t_monotonic_s", "etiqueta", "ms"]) if args.log_accesos else None
    if log_accesos:
        DRIVER.registrar_detalle = True

    desbordes = 0
    supervisor = vigilante = None
    parada = asyncio.Event()
    try:
        async with server:
            supervisor = asyncio.create_task(supervisor_enlace())
            vigilante = await preparar_parada(parada, opciones, args)
            logger.info(f"Servidor OPC UA iniciado. Periodo objetivo {args.periodo_ms:g} ms"
                        + (f", log de ciclos en {args.log_csv}" if log_ciclos else "")
                        + (f", log de accesos en {args.log_accesos}" if log_accesos else ""))
            t_prev = None
            siguiente = time.perf_counter() + periodo
            while not parada.is_set():
                t_ini = time.perf_counter()
                DRIVER.estadisticas.reiniciar()
                publicados = 0

                if ENLACE["conectado"]:
                    publicados = await ciclo_lectura()
                    if not DRIVER.conectado:
                        await al_caer_enlace()

                fin = time.perf_counter()
                trabajo_ms = (fin - t_ini) * 1000
                periodo_ms = None if t_prev is None else (t_ini - t_prev) * 1000
                t_prev = t_ini
                e = DRIVER.estadisticas

                # Planificacion de periodo fijo: dormir hasta el proximo instante
                # objetivo. Si el ciclo se paso de largo, es un desbordamiento:
                # se cuenta, se resincroniza el reloj y no se intenta recuperar.
                siguiente += periodo
                retraso = siguiente - time.perf_counter()
                desborde = retraso < 0
                if desborde:
                    desbordes += 1
                    AVISOS.aviso("desborde",
                                 f"Ciclo mas largo que el periodo ({trabajo_ms:.1f} ms > "
                                 f"{args.periodo_ms:g} ms). Desbordes acumulados: {desbordes}")
                    siguiente = time.perf_counter()

                if log_ciclos:
                    log_ciclos.fila([datetime.now(timezone.utc).isoformat(), f"{fin:.6f}",
                                     f"{periodo_ms:.3f}" if periodo_ms is not None else "",
                                     f"{trabajo_ms:.3f}", f"{e.ms:.3f}", e.peticiones,
                                     e.bytes_leidos, publicados, int(desborde)])
                if log_accesos and e.detalle:
                    iso = datetime.now(timezone.utc).isoformat()
                    for etiqueta, ms in e.detalle:
                        log_accesos.fila([iso, f"{fin:.6f}", etiqueta, f"{ms:.3f}"])

                if ENLACE["conectado"]:
                    _registrar_ciclo(trabajo_ms, periodo_ms, e.ms, e.peticiones)

                if not desborde:
                    await asyncio.sleep(retraso)
            logger.info("Parada ordenada: cerrando el servidor OPC UA")
    finally:
        for tarea in (supervisor, vigilante):
            if tarea:
                tarea.cancel()
        if log_ciclos:
            log_ciclos.cerrar()
        if log_accesos:
            log_accesos.cerrar()
        await DRIVER.desconectar()
        logger.info(f"Conexion con el PLC cerrada (driver {DRIVER.nombre})")
        if opciones.fichero_pid:
            try:
                os.remove(opciones.fichero_pid)
            except OSError:
                pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
        logger.info("Servidor OPC UA detenido.")
    except KeyboardInterrupt:
        logger.info("Servidor OPC UA detenido.")
