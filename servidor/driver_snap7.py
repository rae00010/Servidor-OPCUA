"""
Driver downstream para controladores Siemens S7 mediante Snap7 (protocolo S7
sobre ISO-on-TCP). Probado con python-snap7 2.0.2.

Interpretacion del VariableType Address:
    area : enumerado AreaPLC del modelo (0=I, 1=Q, 2=M, 3=DB)
    byte : desplazamiento en bytes dentro del area
    bit  : 0..7
    db   : numero de bloque de datos (obligatorio si area == DB; el DB debe
           tener acceso estandar, no optimizado, para ser accesible por S7)

Lectura agrupada: al arrancar se agrupan los puntos por area (y DB) y se
fusionan en rangos de bytes contiguos. Dos direcciones se unen en el mismo
rango si el hueco entre ellas es como mucho MAX_HUECO bytes y el rango cabe en
una sola respuesta S7 (tamano de PDU negociado menos la cabecera). Cada ciclo
hace una peticion read_area por rango y extrae cada dato del buffer en memoria
(cada punto ocupa los bytes de su tipo).

Tipos (paso A5): el DataType de la variable fija el tipo S7. Boolean=BOOL,
Byte=BYTE, UInt16=WORD, Int16=INT, Int32=DINT, Float=REAL, en orden de bytes
big-endian de S7. bit solo se usa en BOOL; en el resto debe faltar o valer 0.

Escritura: BOOL con Read-Modify-Write del byte dentro del mismo acceso
exclusivo (PLC_LOCK). Los tipos de varios bytes escriben el dato completo.

Deteccion de caida (paso 7): por comportamiento, no por el texto del error,
que cambia entre sistemas operativos. El enlace se da por caido tras
FALLOS_MAX peticiones fallidas seguidas, o inmediatamente si una peticion
no responde dentro del tiempo limite. Cada conexion usa un cliente snap7
nuevo, para no reutilizar uno que haya quedado en un estado inconsistente.
"""

import asyncio
import re
import struct
import time
from dataclasses import dataclass, field

import snap7
from snap7.type import Area, Parameter
from snap7.util import get_bool, set_bool

from asyncua import ua

from driver_base import Driver, DriverError, ParametroDriver, PuntoDriver

AREA_SNAP7 = {0: Area.PE, 1: Area.PA, 2: Area.MK, 3: Area.DB}
AREA_NOMBRE = {0: "I", 1: "Q", 2: "M", 3: "DB"}

PDU_POR_DEFECTO = 240          # S7-1200; se sustituye por el valor negociado
CABECERA_RESPUESTA = 18        # bytes de cabecera en una respuesta de lectura S7
MAX_HUECO = 8                  # bytes sin usar que se aceptan dentro de un rango
FALLOS_MAX = 3                 # peticiones fallidas seguidas para dar el enlace por caido


# Tipos S7 soportados: VariantType OPC UA -> (nombre S7, formato struct big-endian).
# BOOL se trata aparte (bit dentro de un byte); el resto ocupa calcsize(formato) bytes.
TIPOS_S7 = {
    ua.VariantType.Boolean: ("BOOL", None),
    ua.VariantType.Byte: ("BYTE", ">B"),
    ua.VariantType.UInt16: ("WORD", ">H"),
    ua.VariantType.Int16: ("INT", ">h"),
    ua.VariantType.Int32: ("DINT", ">i"),
    ua.VariantType.Float: ("REAL", ">f"),
}
LETRA_TAMANO = {"BYTE": "B", "WORD": "W", "INT": "W", "DINT": "D", "REAL": "D"}   # IB/IW/ID, DBB/DBW/DBD

# Sintaxis de direccion en texto: I/Q/M<byte>[.<bit>] o DB<n>.<byte>[.<bit>]
_RE_IQM = re.compile(r"^(I|Q|M)(\d+)(?:\.(\d+))?$")
_RE_DB = re.compile(r"^DB(\d+)\.(\d+)(?:\.(\d+))?$")
AREA_CODIGO = {v: k for k, v in AREA_NOMBRE.items()}


def comprobar_direccion(propiedades, tipo_dato):
    """Propiedades de Address + VariantType -> DireccionS7. ValueError si no valen."""
    if tipo_dato not in TIPOS_S7:
        soportados = ", ".join(v.name for v in TIPOS_S7)
        raise ValueError(f"tipo {tipo_dato.name} no soportado (soportados: {soportados})")
    tipo = TIPOS_S7[tipo_dato][0]
    obligatorias = ("area", "byte", "bit") if tipo == "BOOL" else ("area", "byte")
    faltan = [k for k in obligatorias if propiedades.get(k) is None]
    if faltan:
        raise ValueError(f"faltan propiedades {faltan}")
    area, byte = int(propiedades["area"]), int(propiedades["byte"])
    bit = int(propiedades.get("bit") or 0)
    if area not in AREA_SNAP7:
        raise ValueError(f"area {area} no valida")
    if byte < 0 or not 0 <= bit <= 7:
        raise ValueError(f"direccion fuera de rango (byte={byte}, bit={bit})")
    if tipo != "BOOL" and bit != 0:
        raise ValueError(f"bit={bit} no aplicable al tipo {tipo} (debe faltar o valer 0)")
    db = propiedades.get("db")
    if area == 3 and db is None:
        raise ValueError("area DB sin propiedad db")
    return DireccionS7(area, int(db or 0) if area == 3 else 0, byte, bit, tipo)


@dataclass(frozen=True)
class DireccionS7:
    area: int
    db: int
    byte: int
    bit: int
    tipo: str = "BOOL"            # BOOL, BYTE, WORD, INT, DINT o REAL

    @property
    def formato(self):
        return next(f for n, f in TIPOS_S7.values() if n == self.tipo)

    @property
    def tamano(self):
        return 1 if self.tipo == "BOOL" else struct.calcsize(self.formato)

    def decodificar(self, data, desplazamiento):
        if self.tipo == "BOOL":
            return get_bool(data, desplazamiento, self.bit)
        return struct.unpack_from(self.formato, data, desplazamiento)[0]

    def codificar(self, valor):
        if self.tipo == "REAL":
            return bytearray(struct.pack(self.formato, float(valor)))
        try:
            return bytearray(struct.pack(self.formato, int(valor)))
        except struct.error as e:
            raise DriverError(f"valor {valor!r} fuera de rango para {self.tipo}") from e


@dataclass
class RangoLectura:
    area: int
    db: int
    inicio: int
    longitud: int
    puntos: list = field(default_factory=list)     # (clave, desplazamiento, DireccionS7)


class DriverSnap7(Driver):
    nombre = "snap7"
    descripcion = "Siemens S7 (S7-1200/1500) por Snap7, acceso PUT/GET"
    PARAMETROS = (
        ParametroDriver("ip", "texto", "192.168.0.1", True, "Direccion IP del PLC"),
        ParametroDriver("rack", "entero", 0, False, "Rack de la CPU", 0, 7),
        ParametroDriver("slot", "entero", 1, False, "Slot de la CPU", 0, 31),
        ParametroDriver("timeout_ms", "entero", 1000, False, "Tiempo limite de cada peticion S7 (ms)",
                        50, 60000),
        ParametroDriver("max_hueco", "entero", MAX_HUECO, False,
                        "Bytes sin usar que se aceptan dentro de una lectura agrupada", 0, 64,
                        avanzado=True),
    )

    def __init__(self, ip, rack=0, slot=1, timeout_ms=1000, max_hueco=MAX_HUECO,
                 fallos_max=FALLOS_MAX):
        super().__init__()
        self.ip, self.rack, self.slot = ip, rack, slot
        self.timeout_ms = timeout_ms
        self.max_hueco = max_hueco
        self.fallos_max = fallos_max
        self.pdu = None
        self.registrar_detalle = False      # si True, guarda (etiqueta, ms) por acceso
        self._cliente = None
        self._lock = asyncio.Lock()        # PLC_LOCK: un unico acceso al socket
        self._conectado = False
        self._fallos = 0

    # --- Ciclo de vida -------------------------------------------------------
    @property
    def conectado(self):
        return self._conectado

    async def conectar(self):
        def _conectar():
            c = self._cliente = snap7.client.Client()
            for p in (Parameter.PingTimeout, Parameter.SendTimeout, Parameter.RecvTimeout):
                c.set_param(p, self.timeout_ms)
            c.connect(self.ip, self.rack, self.slot)
            return c.get_connected(), c.get_pdu_length()

        try:
            async with self._lock:
                ok, pdu = await asyncio.to_thread(_conectar)
        except Exception as e:
            raise DriverError(f"No se pudo conectar a {self.ip}: {e}") from e
        if not ok:
            raise DriverError(f"El PLC {self.ip} no acepta la conexion")
        self.pdu = pdu
        self._fallos = 0
        self._conectado = True

    async def desconectar(self):
        self._conectado = False
        if self._cliente is None:
            return
        try:
            async with self._lock:
                await asyncio.wait_for(asyncio.to_thread(self._cliente.disconnect),
                                       timeout=3 * self.timeout_ms / 1000)
        except Exception:
            pass

    # --- Modelo --------------------------------------------------------------
    def validar_direccion(self, propiedades, tipo_dato):
        return comprobar_direccion(propiedades, tipo_dato)

    @classmethod
    def interpretar_direccion(cls, texto, tipo_dato):
        """'Q0.3', 'M20', 'DB10.4', 'DB10.0.1' -> {area, byte, bit?, db?}."""
        t = str(texto).strip().upper()
        m, props = _RE_IQM.match(t), {}
        if m:
            props = {"area": AREA_CODIGO[m[1]], "byte": int(m[2])}
            bit = m[3]
        elif (m := _RE_DB.match(t)):
            props = {"area": 3, "db": int(m[1]), "byte": int(m[2])}
            bit = m[3]
        else:
            raise ValueError(f"direccion '{texto}' no reconocida (formatos: I0.0, Q0.1, M10, "
                             f"DB10.4, DB10.0.1)")
        nombre_tipo = TIPOS_S7.get(tipo_dato, (None,))[0]
        if nombre_tipo == "BOOL" and bit is None:
            raise ValueError(f"direccion '{texto}': una variable BOOL necesita bit (p.ej. {t}.0)")
        if nombre_tipo not in (None, "BOOL") and bit is not None:
            raise ValueError(f"direccion '{texto}': una variable {nombre_tipo} no lleva bit")
        if bit is not None:
            props["bit"] = int(bit)
        try:
            comprobar_direccion(props, tipo_dato)
        except ValueError as e:
            raise ValueError(f"direccion '{texto}': {e}") from None
        return props

    def texto_direccion(self, d):
        if d.tipo == "BOOL":
            if d.area == 3:
                return f"DB{d.db}.DBX{d.byte}.{d.bit}"
            return f"{AREA_NOMBRE[d.area]}{d.byte}.{d.bit}"
        sufijo = "" if d.tipo in ("BYTE", "WORD", "DINT") else f" ({d.tipo})"
        if d.area == 3:
            return f"DB{d.db}.DB{LETRA_TAMANO[d.tipo]}{d.byte}{sufijo}"
        return f"{AREA_NOMBRE[d.area]}{LETRA_TAMANO[d.tipo]}{d.byte}{sufijo}"

    # --- Lectura agrupada ----------------------------------------------------
    def planificar_lectura(self, puntos):
        max_carga = (self.pdu or PDU_POR_DEFECTO) - CABECERA_RESPUESTA
        por_area = {}
        for p in puntos:
            d = p.direccion
            por_area.setdefault((d.area, d.db), []).append(p)

        plan = []
        for (area, db), grupo in sorted(por_area.items()):
            grupo.sort(key=lambda p: (p.direccion.byte, p.direccion.bit))
            rango = None
            for p in grupo:
                b, tam = p.direccion.byte, p.direccion.tamano    # ocupa [b, b + tam)
                if rango is not None:
                    fin = rango.inicio + rango.longitud          # primer byte fuera
                    nueva_long = max(rango.longitud, b + tam - rango.inicio)
                    if b - fin <= self.max_hueco and nueva_long <= max_carga:
                        rango.longitud = nueva_long
                    else:
                        rango = None
                if rango is None:
                    rango = RangoLectura(area, db, b, tam)
                    plan.append(rango)
                rango.puntos.append((p.clave, b - rango.inicio, p.direccion))
        return plan

    def describir_plan(self, plan):
        lineas = []
        for r in plan:
            area = f"DB{r.db}" if r.area == 3 else AREA_NOMBRE[r.area]
            lineas.append(f"read_area {area} bytes {r.inicio}..{r.inicio + r.longitud - 1} "
                          f"({r.longitud} B, {len(r.puntos)} variables)")
        return lineas

    async def leer(self, plan):
        """
        Lee todos los rangos del plan. Un rango que falla no impide leer los
        demas: sus claves se devuelven en 'fallidas'. Si el enlace cae a
        mitad, el resto del plan se da por fallido sin enviar mas peticiones.
        """
        valores, fallidas = {}, set()
        for r in plan:
            try:
                data = await self._peticion(
                    lambda r=r: self._cliente.read_area(AREA_SNAP7[r.area], r.db, r.inicio, r.longitud),
                    etiqueta=f"R {AREA_NOMBRE[r.area]}{'' if r.area != 3 else r.db}:{r.inicio}+{r.longitud}")
            except DriverError:
                fallidas.update(clave for clave, _, _ in r.puntos)
                continue
            self.estadisticas.bytes_leidos += len(data)
            for clave, desplazamiento, d in r.puntos:
                valores[clave] = d.decodificar(data, desplazamiento)
        return valores, fallidas

    # --- Escritura -----------------------------------------------------------
    async def escribir(self, punto: PuntoDriver, valor):
        d = punto.direccion
        area = AREA_SNAP7[d.area]

        if d.tipo != "BOOL":
            # Tipos de varios bytes: se escribe el dato completo, sin RMW.
            data = d.codificar(valor)
            await self._peticion(lambda: self._cliente.write_area(area, d.db, d.byte, data),
                                 etiqueta=f"W {self.texto_direccion(d)}")
            return

        def _rmw():
            c = self._cliente
            data = c.read_area(area, d.db, d.byte, 1)
            set_bool(data, 0, d.bit, bool(valor))
            c.write_area(area, d.db, d.byte, data)

        await self._peticion(_rmw, peticiones=2, etiqueta=f"W {self.texto_direccion(d)}")

    # --- Acceso exclusivo con tiempo limite ----------------------------------
    async def _peticion(self, fn, *args, peticiones=1, etiqueta=None):
        if not self._conectado:
            raise DriverError("sin conexion con el PLC")
        t = time.perf_counter()
        enviada = False
        try:
            async with self._lock:
                if not self._conectado:      # pudo caer mientras se esperaba el lock
                    raise DriverError("sin conexion con el PLC")
                enviada = True
                # Los timeouts de socket de snap7 cortan la peticion; wait_for es
                # una salvaguarda por si la libreria no respondiera. En ese caso
                # el hilo puede seguir usando el cliente, asi que se marca la
                # conexion como no valida para no reutilizarlo.
                resultado = await asyncio.wait_for(asyncio.to_thread(fn, *args),
                                                   timeout=3 * self.timeout_ms / 1000)
            self._fallos = 0
            return resultado
        except asyncio.TimeoutError as e:
            self._conectado = False
            raise DriverError("la peticion S7 no respondio a tiempo") from e
        except DriverError:
            raise
        except Exception as e:
            self._fallos += 1
            if self._fallos >= self.fallos_max:
                self._conectado = False
            raise DriverError(str(e)) from e
        finally:
            if enviada:
                dur = (time.perf_counter() - t) * 1000
                self.estadisticas.peticiones += peticiones
                self.estadisticas.ms += dur
                if self.registrar_detalle and etiqueta is not None:
                    self.estadisticas.detalle.append((etiqueta, dur))
