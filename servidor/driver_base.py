"""
Interfaz del nivel downstream.

El servidor OPC UA (nivel upstream) no conoce el protocolo del controlador.
Solo maneja "puntos": variables del modelo con una direccion fisica opaca.
Cada driver sabe interpretar las propiedades del VariableType Address,
agrupar los accesos de la forma mas eficiente para su protocolo y leer o
escribir valores. Cambiar de fabricante consiste en anadir un driver nuevo
que implemente esta interfaz, sin tocar el resto del servidor.

CONTRATO PUBLICO DEL ESTANDAR (version 1.2)
===========================================
Todo driver hereda de Driver, se registra con un nombre en drivers.py y
cumple lo siguiente.

Constructor
    Recibe solo argumentos por palabra clave propios de su protocolo
    (p.ej. snap7: ip, rack, slot, timeout_ms, fallos_max). La fabrica
    drivers.crear_driver(nombre, parametros) lo instancia con **parametros.
    Debe llamar a Driver.__init__() y fijar el atributo de clase 'nombre'.

Parametros declarados (v1.2, opcional)
    PARAMETROS: tupla de ParametroDriver (nombre, tipo, defecto, obligatorio,
        descripcion, minimo, maximo, opciones, avanzado). Con ellos la interfaz
        construye el formulario de conexion sin conocer el protocolo, y
        validar_parametros(valores) -> dict (metodo de clase) convierte tipos,
        aplica defectos y lanza ValueError con todos los problemas.
        PARAMETROS_COMUNES (p.ej. fallos_max) los inyecta el servidor y no se
        declaran. Un driver sin PARAMETROS se crea como en v1.0/v1.1.

Ciclo de vida (llamado por el supervisor de enlace del servidor)
    async conectar()     Abre la conexion. DriverError si no es posible.
    async desconectar()  Cierre ordenado. No debe lanzar excepciones.
    conectado            Propiedad bool. El driver la pone a False cuando
                         considera el enlace caido (p.ej. tras N fallos).

Modelo (llamado una sola vez, al cargar el NodeSet)
    validar_direccion(propiedades, tipo_dato) -> direccion
        propiedades: {BrowseName: valor} de las Property del VariableType
        Address de la variable. tipo_dato: ua.VariantType del DataType de la
        variable. Devuelve una direccion opaca (hashable y comparable, se usa
        para ordenar y detectar escrituras solapadas). ValueError con un
        mensaje claro si no es valida o el tipo no esta soportado.
    texto_direccion(direccion) -> str   Forma legible para el log.
    interpretar_direccion(texto, tipo_dato) -> dict   (v1.1, opcional)
        Metodo de clase: no necesita instancia ni conexion. Traduce la
        direccion escrita en la descripcion de planta (sintaxis propia del
        driver, p.ej. 'Q0.3' en snap7) a las propiedades de Address
        {area, byte, bit?, db?}. ValueError con un mensaje claro si no es
        valida. Por defecto, el driver no admite direcciones en texto.

Acceso a datos (llamado en cada ciclo o por evento)
    planificar_lectura(puntos) -> plan  Una vez al arrancar y tras cada
        reconexion. El plan es opaco para el servidor.
    async leer(plan) -> ({clave: valor}, {claves fallidas})
        Valores ya convertidos al tipo Python del DataType. Un fallo parcial
        no aborta el resto del plan; solo un fallo de enlace lanza DriverError.
    async escribir(punto, valor)  Escribe un valor del tipo del DataType.
        DriverError si falla.
    describir_plan(plan) -> list[str]   Opcional, para el log.

Tipos de dato
    El DataType OPC UA de la variable determina el tipo del dato. Tipos del
    estandar y su valor Python:
        Boolean -> bool    Byte  -> int (0..255)       UInt16 -> int (0..65535)
        Int16   -> int     Int32 -> int                 Float  -> float
    Cada driver declara cuales soporta en validar_direccion.

Errores
    ValueError   configuracion o modelo invalido (el servidor no arranca).
    DriverError  fallo de comunicacion en tiempo de ejecucion.

Estadisticas
    self.estadisticas (EstadisticasAcceso) acumula peticiones, tiempo y bytes;
    el servidor la lee y la reinicia en cada ciclo.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class DriverError(Exception):
    """Error de comunicacion o de configuracion del driver."""


@dataclass
class EstadisticasAcceso:
    """Contadores que el servidor lee y pone a cero en cada ciclo."""
    peticiones: int = 0            # peticiones de protocolo enviadas
    ms: float = 0.0                # tiempo total en ellas (incluida la espera del lock)
    bytes_leidos: int = 0
    detalle: list = field(default_factory=list)   # [(etiqueta, ms)] por acceso, si se activa

    def reiniciar(self):
        self.peticiones, self.ms, self.bytes_leidos = 0, 0.0, 0
        self.detalle.clear()


TIPOS_PARAMETRO = ("texto", "entero", "real", "bool", "opcion")
PARAMETROS_COMUNES = ("fallos_max",)      # los inyecta el servidor (bloque 'enlace')


@dataclass(frozen=True)
class ParametroDriver:
    """Declaracion de un parametro del constructor de un driver (contrato v1.2)."""
    nombre: str
    tipo: str = "texto"                    # uno de TIPOS_PARAMETRO
    defecto: object = None
    obligatorio: bool = False
    descripcion: str = ""
    minimo: float | None = None
    maximo: float | None = None
    opciones: tuple = ()                   # solo para tipo 'opcion'
    avanzado: bool = False                 # la interfaz lo muestra plegado

    def a_dict(self):
        return {"nombre": self.nombre, "tipo": self.tipo, "defecto": self.defecto,
                "obligatorio": self.obligatorio, "descripcion": self.descripcion,
                "minimo": self.minimo, "maximo": self.maximo, "opciones": list(self.opciones),
                "avanzado": self.avanzado}

    def convertir(self, valor):
        """Valor de formulario o YAML -> valor Python. ValueError si no vale."""
        t = self.tipo
        if t == "bool":
            if isinstance(valor, bool):
                return valor
            if str(valor).strip().lower() in ("true", "1", "si", "sí"):
                return True
            if str(valor).strip().lower() in ("false", "0", "no"):
                return False
            raise ValueError("debe ser verdadero o falso")
        if t == "entero":
            if isinstance(valor, bool) or (isinstance(valor, float) and not valor.is_integer()):
                raise ValueError("debe ser un numero entero")
            try:
                v = int(str(valor).strip()) if not isinstance(valor, (int, float)) else int(valor)
            except ValueError:
                raise ValueError("debe ser un numero entero") from None
        elif t == "real":
            if isinstance(valor, bool):
                raise ValueError("debe ser un numero")
            try:
                v = float(valor)
            except (TypeError, ValueError):
                raise ValueError("debe ser un numero") from None
        elif t == "opcion":
            if valor not in self.opciones:
                raise ValueError(f"debe ser una de: {', '.join(map(str, self.opciones))}")
            return valor
        else:
            v = str(valor).strip()
            if self.obligatorio and not v:
                raise ValueError("no puede estar vacio")
            return v
        if self.minimo is not None and v < self.minimo:
            raise ValueError(f"debe ser >= {self.minimo:g}")
        if self.maximo is not None and v > self.maximo:
            raise ValueError(f"debe ser <= {self.maximo:g}")
        return v


@dataclass
class PuntoDriver:
    """Lo que el driver necesita de cada variable del modelo."""
    clave: object                  # identificador opaco para el servidor (p.ej. NodeId)
    ruta: str                      # solo para mensajes
    direccion: object              # resultado de Driver.validar_direccion()
    valor_escrito: object = field(default=None, repr=False)


class Driver(ABC):
    """Contrato que cumple cualquier driver downstream."""

    nombre: str = "abstracto"
    descripcion: str = ""
    PARAMETROS: tuple = ()                 # tupla de ParametroDriver (v1.2)

    @classmethod
    def validar_parametros(cls, valores: dict | None) -> dict:
        """
        Valida 'valores' contra PARAMETROS: convierte tipos, aplica defectos y
        conserva los PARAMETROS_COMUNES. Sin PARAMETROS, devuelve una copia.
        Lanza ValueError con todos los problemas a la vez.
        """
        valores = dict(valores or {})
        if not cls.PARAMETROS:
            return valores
        declarados = {p.nombre: p for p in cls.PARAMETROS}
        errores, salida = [], {}
        for k in valores:
            if k not in declarados and k not in PARAMETROS_COMUNES:
                errores.append(f"parametro '{k}' no existe (validos: {', '.join(declarados)})")
        for p in cls.PARAMETROS:
            v = valores.get(p.nombre)
            if v is None or (isinstance(v, str) and not v.strip() and p.tipo != "texto"):
                if p.defecto is None:
                    if p.obligatorio:
                        errores.append(f"falta el parametro obligatorio '{p.nombre}'")
                    continue
                v = p.defecto
            try:
                salida[p.nombre] = p.convertir(v)
            except ValueError as e:
                errores.append(f"{p.nombre}={v!r}: {e}")
        if errores:
            raise ValueError(f"Parametros no validos para el driver '{cls.nombre}': " + "; ".join(errores))
        salida.update({k: valores[k] for k in PARAMETROS_COMUNES if k in valores})
        return salida

    def __init__(self):
        self.estadisticas = EstadisticasAcceso()

    # --- Ciclo de vida -------------------------------------------------------
    @abstractmethod
    async def conectar(self) -> None:
        """Abre la conexion. Lanza DriverError si no es posible."""

    @abstractmethod
    async def desconectar(self) -> None:
        """Cierra la conexion de forma ordenada."""

    @property
    @abstractmethod
    def conectado(self) -> bool:
        ...

    # --- Modelo --------------------------------------------------------------
    @abstractmethod
    def validar_direccion(self, propiedades: dict, tipo_dato) -> object:
        """
        Convierte las propiedades del VariableType Address (byte, bit, area,
        db...) y el DataType de la variable en una direccion del protocolo.
        Lanza ValueError con un mensaje claro si no son validas.
        """

    @abstractmethod
    def texto_direccion(self, direccion) -> str:
        """Representacion legible, p.ej. 'I0.5' o 'DB10.DBX2.1'."""

    @classmethod
    def interpretar_direccion(cls, texto: str, tipo_dato) -> dict:
        """
        Texto de direccion de la descripcion de planta -> propiedades de
        Address. No abstracto: un driver sin sintaxis de texto lo rechaza.
        """
        raise ValueError(f"el driver {cls.nombre} no admite direcciones en texto")

    # --- Acceso a datos ------------------------------------------------------
    @abstractmethod
    def planificar_lectura(self, puntos: list[PuntoDriver]) -> object:
        """
        Calcula una vez, al arrancar, como leer un conjunto de puntos con el
        menor numero de peticiones. El plan se reutiliza en cada ciclo.
        """

    @abstractmethod
    async def leer(self, plan) -> tuple[dict, set]:
        """
        Ejecuta un plan de lectura. Devuelve ({clave: valor}, {claves fallidas}).
        Un fallo parcial no debe impedir leer el resto del plan. El driver
        decide cuando el enlace esta caido y lo refleja en 'conectado'.
        """

    @abstractmethod
    async def escribir(self, punto: PuntoDriver, valor) -> None:
        """Escribe un valor en el controlador."""

    def describir_plan(self, plan) -> list[str]:
        """Lineas legibles del plan, para el log de arranque."""
        return []
