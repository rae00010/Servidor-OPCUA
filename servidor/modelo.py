"""
Motor del modelo de informacion (Fase B).

Descripcion de planta en YAML, independiente de OPC UA:

    modelo:      uri (obligatoria), version, driver (obligatorio, de drivers.DRIVERS)
    tipos:       <TipoDispositivo>: descripcion?, variables?, grupos?, metodos?
                   variables: <Nombre>: {tipo, acceso, descripcion?}
                   grupos:    <Grupo>: {<Nombre>: {tipo, acceso, descripcion?}}
                   metodos:   <Metodo>: {descripcion?, entradas: [...], salidas: [...]}
                              cada argumento: {nombre, tipo, descripcion?}
    instancias:  <Instancia>: {tipo, descripcion?, direcciones: {<ruta>: <texto>}}
                   ruta: 'Grupo/Variable' o 'Variable'. El texto lo interpreta
                   el driver del modelo (Driver.interpretar_direccion).

Tipos de dato: BOOL, BYTE, WORD, INT, DINT, REAL; en argumentos de metodo,
ademas STRING. Acceso: lectura (PLC -> OPC UA) o escritura (OPC UA -> PLC).
"""

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone

import yaml
from asyncua import ua

import drivers

# Tipo de dato de la planta -> VariantType OPC UA (tabla de la Fase A)
TIPOS_DATO = {
    "BOOL": ua.VariantType.Boolean,
    "BYTE": ua.VariantType.Byte,
    "WORD": ua.VariantType.UInt16,
    "INT": ua.VariantType.Int16,
    "DINT": ua.VariantType.Int32,
    "REAL": ua.VariantType.Float,
}
TIPOS_ARGUMENTO = {**TIPOS_DATO, "STRING": ua.VariantType.String}
ACCESOS = ("lectura", "escritura")
_RE_NOMBRE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ErrorPlanta(ValueError):
    """Descripcion de planta invalida. 'errores' lista todos los problemas."""

    def __init__(self, errores):
        self.errores = list(errores)
        super().__init__("Descripcion de planta invalida:\n  " + "\n  ".join(self.errores))


@dataclass
class VariableTipo:
    nombre: str
    grupo: str | None          # None si cuelga directamente del dispositivo
    tipo: str                  # BOOL, INT...
    acceso: str                # lectura | escritura
    descripcion: str = ""

    @property
    def ruta(self):
        return f"{self.grupo}/{self.nombre}" if self.grupo else self.nombre

    @property
    def escritura(self):
        return self.acceso == "escritura"


@dataclass
class Argumento:
    nombre: str
    tipo: str
    descripcion: str = ""


@dataclass
class Metodo:
    nombre: str
    descripcion: str = ""
    entradas: list = field(default_factory=list)
    salidas: list = field(default_factory=list)


@dataclass
class TipoDispositivo:
    nombre: str
    descripcion: str = ""
    variables: list = field(default_factory=list)     # [VariableTipo], en orden del YAML
    metodos: list = field(default_factory=list)       # [Metodo]

    @property
    def grupos(self):
        return list(dict.fromkeys(v.grupo for v in self.variables if v.grupo))


@dataclass
class Instancia:
    nombre: str
    tipo: TipoDispositivo
    descripcion: str = ""
    textos: dict = field(default_factory=dict)        # ruta -> texto de direccion
    direcciones: dict = field(default_factory=dict)   # ruta -> propiedades de Address


@dataclass
class Planta:
    uri: str
    version: str
    driver: str
    tipos: dict                                       # nombre -> TipoDispositivo
    instancias: list                                  # [Instancia]
    avisos: list = field(default_factory=list)        # de comprobar_coherencia


# ---------------------------------------------------------------------------
# Lectura del YAML (rechaza claves repetidas, que PyYAML aceptaria en silencio)
# ---------------------------------------------------------------------------
class _CargadorEstricto(yaml.SafeLoader):
    pass


def _mapa_sin_repetidos(cargador, nodo):
    vistos = set()
    for k, _ in nodo.value:
        clave = cargador.construct_object(k)
        if clave in vistos:
            raise yaml.constructor.ConstructorError(
                None, None, f"clave repetida '{clave}'", k.start_mark)
        vistos.add(clave)
    return cargador.construct_mapping(nodo)


_CargadorEstricto.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapa_sin_repetidos)


def cargar_planta(fuente):
    """
    fuente: ruta de un fichero .yaml/.yml o texto YAML. Devuelve una Planta.
    Lanza ErrorPlanta con la lista completa de problemas encontrados.
    """
    texto = fuente
    if isinstance(fuente, str) and "\n" not in fuente and fuente.lower().endswith((".yaml", ".yml")):
        with open(fuente, encoding="utf-8") as f:
            texto = f.read()
    try:
        datos = yaml.load(texto, Loader=_CargadorEstricto)
    except yaml.YAMLError as e:
        raise ErrorPlanta([f"YAML mal formado: {e}"]) from None
    return interpretar_planta(datos)


def interpretar_planta(datos):
    errores = []
    if not isinstance(datos, dict):
        raise ErrorPlanta(["la descripcion debe ser un mapa con 'modelo', 'tipos' e 'instancias'"])
    _claves(datos, {"modelo", "tipos", "instancias"}, "raiz", errores)

    # --- modelo ---
    m = datos.get("modelo")
    uri = version = nombre_driver = None
    clase_driver = None
    if not isinstance(m, dict):
        errores.append("falta el bloque 'modelo'")
    else:
        _claves(m, {"uri", "version", "driver"}, "modelo", errores)
        uri, version, nombre_driver = m.get("uri"), str(m.get("version", "1.0.0")), m.get("driver")
        if not isinstance(uri, str) or not uri.strip():
            errores.append("modelo: falta 'uri'")
        if nombre_driver is None:
            errores.append(f"modelo: falta 'driver' (disponibles: {', '.join(drivers.disponibles())})")
        elif nombre_driver not in drivers.DRIVERS:
            errores.append(f"modelo: driver '{nombre_driver}' desconocido "
                           f"(disponibles: {', '.join(drivers.disponibles())})")
        else:
            clase_driver = drivers.DRIVERS[nombre_driver]

    # --- tipos ---
    tipos = {}
    t = datos.get("tipos")
    if not isinstance(t, dict) or not t:
        errores.append("falta el bloque 'tipos' o esta vacio")
        t = {}
    for nombre, spec in t.items():
        td = _tipo(nombre, spec, errores)
        if td:
            tipos[nombre] = td

    # --- instancias ---
    instancias = []
    inst = datos.get("instancias")
    if not isinstance(inst, dict) or not inst:
        errores.append("falta el bloque 'instancias' o esta vacio")
        inst = {}
    for nombre, spec in inst.items():
        donde = f"instancia {nombre}"
        if not _nombre_valido(nombre, donde, errores):
            continue
        if not isinstance(spec, dict):
            errores.append(f"{donde}: debe ser un mapa con 'tipo' y 'direcciones'")
            continue
        _claves(spec, {"tipo", "descripcion", "direcciones"}, donde, errores)
        nombre_tipo = spec.get("tipo")
        if nombre_tipo not in tipos:
            if nombre_tipo in t:
                continue          # el tipo existe pero es invalido: ya se ha informado
            errores.append(f"{donde}: tipo '{nombre_tipo}' no declarado en 'tipos'")
            continue
        td = tipos[nombre_tipo]
        i = Instancia(nombre, td, str(spec.get("descripcion", "")))
        dirs = spec.get("direcciones") or {}
        if not isinstance(dirs, dict):
            errores.append(f"{donde}: 'direcciones' debe ser un mapa ruta: direccion")
            dirs = {}
        rutas_tipo = {v.ruta: v for v in td.variables}
        for ruta, texto in dirs.items():
            v = rutas_tipo.get(ruta)
            if v is None:
                errores.append(f"{donde}: '{ruta}' no es una variable del tipo {td.nombre} "
                               f"(variables: {', '.join(rutas_tipo)})")
                continue
            if not isinstance(texto, str):
                errores.append(f"{donde}/{ruta}: la direccion debe ser texto (p.ej. \"Q0.0\"), "
                               f"no {texto!r}")
                continue
            i.textos[ruta] = texto
            if clase_driver is None:
                continue
            try:
                i.direcciones[ruta] = clase_driver.interpretar_direccion(texto, TIPOS_DATO[v.tipo])
            except ValueError as e:
                errores.append(f"{donde}/{ruta} ({v.tipo}): {e}")
        faltan = [r for r in rutas_tipo if r not in dirs]
        if faltan:
            errores.append(f"{donde}: sin direccion para {', '.join(faltan)}")
        instancias.append(i)

    if errores:
        raise ErrorPlanta(errores)
    planta = Planta(uri.strip(), version, nombre_driver, tipos, instancias)
    errores, planta.avisos = comprobar_coherencia(planta)
    if errores:
        raise ErrorPlanta(errores)
    return planta


# ---------------------------------------------------------------------------
# Coherencia de la planta
# ---------------------------------------------------------------------------
TAMANO_BYTES = {"BYTE": 1, "WORD": 2, "INT": 2, "DINT": 4, "REAL": 4}
AREA_Q = 1          # valor de AreaPLC para las salidas


def _bits(v, props):
    """Rango [inicio, fin) en bits que ocupa la variable dentro de su area."""
    if v.tipo == "BOOL":
        inicio = props["byte"] * 8 + props["bit"]
        return inicio, inicio + 1
    return props["byte"] * 8, (props["byte"] + TAMANO_BYTES[v.tipo]) * 8


def comprobar_coherencia(planta):
    """
    Devuelve (errores, avisos).
    Error: dos variables de escritura sobre la misma direccion o con rangos
    solapados (p.ej. un BOOL dentro de un INT).
    Aviso: variable de escritura directamente en las salidas Q. La escritura
    de un bit es lectura-modificacion-escritura del byte, y si el programa del
    PLC escribe otros bits de ese byte el servidor puede devolverles un valor
    obsoleto. Lo correcto es una zona de marcas reservada que el PLC copie a Q.
    """
    errores, avisos = [], []
    filas = []          # (instancia/ruta, texto, VariableTipo, props)
    for i in planta.instancias:
        for v in i.tipo.variables:
            props = i.direcciones[v.ruta]
            filas.append((f"{i.nombre}/{v.ruta}", i.textos[v.ruta], v, props))

    zona = lambda props: (props["area"], props.get("db", 0))
    escrituras = [f for f in filas if f[2].escritura]
    for k, (ra, ta, va, pa) in enumerate(escrituras):
        a0, a1 = _bits(va, pa)
        for rb, tb, vb, pb in escrituras[k + 1:]:
            if zona(pa) != zona(pb):
                continue
            b0, b1 = _bits(vb, pb)
            if a0 == b0 and a1 == b1:
                errores.append(f"{ra} ({ta}) y {rb} ({tb}): dos variables de escritura sobre la misma direccion")
            elif a0 < b1 and b0 < a1:
                errores.append(f"{ra} ({ta}, {va.tipo}) y {rb} ({tb}, {vb.tipo}): "
                               f"variables de escritura con rangos solapados")

    for ruta, texto, v, props in escrituras:
        if props["area"] != AREA_Q:
            continue
        b0, b1 = _bits(v, props)
        vecinas = [f"{r} ({t})" for r, t, w, p in filas
                   if r != ruta and p["area"] == AREA_Q and _bytes_comunes((b0, b1), _bits(w, p))]
        comparte = f"; comparte byte con {', '.join(vecinas)}" if vecinas else ""
        avisos.append(f"{ruta} ({texto}): variable de escritura en las salidas Q{comparte}. "
                      f"El servidor escribe leyendo y reescribiendo el byte, y puede devolver un valor "
                      f"obsoleto a los bits que escriba el programa del PLC. Recomendado: una marca M "
                      f"reservada que el programa del PLC copie a la salida.")
    return errores, avisos


def _bytes_comunes(a, b):
    return a[0] // 8 < -(-b[1] // 8) and b[0] // 8 < -(-a[1] // 8)


# ---------------------------------------------------------------------------
# Auxiliares de validacion
# ---------------------------------------------------------------------------
def _claves(mapa, permitidas, donde, errores):
    for k in mapa:
        if k not in permitidas:
            errores.append(f"{donde}: clave '{k}' desconocida (validas: {', '.join(sorted(permitidas))})")


def _nombre_valido(nombre, donde, errores):
    if isinstance(nombre, str) and _RE_NOMBRE.match(nombre):
        return True
    errores.append(f"{donde}: nombre no valido (letras, cifras y _, sin empezar por cifra)")
    return False


def _variable(nombre, spec, grupo, donde, errores):
    d = f"{donde}/{grupo}/{nombre}" if grupo else f"{donde}/{nombre}"
    if not _nombre_valido(nombre, d, errores):
        return None
    if not isinstance(spec, dict):
        errores.append(f"{d}: debe ser un mapa {{tipo, acceso}}")
        return None
    _claves(spec, {"tipo", "acceso", "descripcion"}, d, errores)
    ok = True
    if spec.get("tipo") not in TIPOS_DATO:
        errores.append(f"{d}: tipo de dato '{spec.get('tipo')}' no soportado "
                       f"(soportados: {', '.join(TIPOS_DATO)})")
        ok = False
    if spec.get("acceso") not in ACCESOS:
        errores.append(f"{d}: acceso '{spec.get('acceso')}' no valido (lectura o escritura)")
        ok = False
    return VariableTipo(nombre, grupo, spec["tipo"], spec["acceso"],
                        str(spec.get("descripcion", ""))) if ok else None


def _tipo(nombre, spec, errores):
    donde = f"tipo {nombre}"
    if not _nombre_valido(nombre, donde, errores):
        return None
    if not isinstance(spec, dict):
        errores.append(f"{donde}: debe ser un mapa con 'variables' y/o 'grupos'")
        return None
    _claves(spec, {"descripcion", "variables", "grupos", "metodos"}, donde, errores)
    n_err = len(errores)
    td = TipoDispositivo(nombre, str(spec.get("descripcion", "")))
    for vn, vs in (spec.get("variables") or {}).items():
        v = _variable(vn, vs, None, donde, errores)
        if v:
            td.variables.append(v)
    for gn, gs in (spec.get("grupos") or {}).items():
        if not _nombre_valido(gn, f"{donde}/{gn}", errores):
            continue
        if not isinstance(gs, dict) or not gs:
            errores.append(f"{donde}/{gn}: un grupo debe contener al menos una variable")
            continue
        if gn in (spec.get("variables") or {}):
            errores.append(f"{donde}: '{gn}' es a la vez grupo y variable")
        for vn, vs in gs.items():
            v = _variable(vn, vs, gn, donde, errores)
            if v:
                td.variables.append(v)
    if not td.variables and len(errores) == n_err:
        errores.append(f"{donde}: no declara ninguna variable")
    for mn, ms in (spec.get("metodos") or {}).items():
        d = f"{donde}/metodo {mn}"
        if not _nombre_valido(mn, d, errores):
            continue
        ms = ms or {}
        if not isinstance(ms, dict):
            errores.append(f"{d}: debe ser un mapa con 'entradas' y 'salidas'")
            continue
        _claves(ms, {"descripcion", "entradas", "salidas"}, d, errores)
        if mn in {v.nombre for v in td.variables if v.grupo is None} or mn in td.grupos:
            errores.append(f"{d}: el nombre coincide con una variable o grupo del tipo")
        met = Metodo(mn, str(ms.get("descripcion", "")))
        for clave, destino in (("entradas", met.entradas), ("salidas", met.salidas)):
            for k, a in enumerate(ms.get(clave) or []):
                da = f"{d}/{clave}[{k}]"
                if not isinstance(a, dict) or not isinstance(a.get("nombre"), str):
                    errores.append(f"{da}: debe ser {{nombre, tipo}}")
                    continue
                _claves(a, {"nombre", "tipo", "descripcion"}, da, errores)
                if a.get("tipo") not in TIPOS_ARGUMENTO:
                    errores.append(f"{da}: tipo '{a.get('tipo')}' no soportado "
                                   f"(soportados: {', '.join(TIPOS_ARGUMENTO)})")
                    continue
                destino.append(Argumento(a["nombre"], a["tipo"], str(a.get("descripcion", ""))))
        td.metodos.append(met)
    return td if len(errores) == n_err else None


# ---------------------------------------------------------------------------
# Generador del NodeSet2
# ---------------------------------------------------------------------------
NS_UANODESET = "http://opcfoundation.org/UA/2011/03/UANodeSet.xsd"
NS_UATYPES = "http://opcfoundation.org/UA/2008/02/Types.xsd"
ET.register_namespace("", NS_UANODESET)
ET.register_namespace("uax", NS_UATYPES)
_N = lambda etiqueta: f"{{{NS_UANODESET}}}{etiqueta}"
_X = lambda etiqueta: f"{{{NS_UATYPES}}}{etiqueta}"

# Alias -> NodeId del espacio 0
ALIAS = {
    "Boolean": "i=1", "Byte": "i=3", "Int16": "i=4", "UInt16": "i=5", "Int32": "i=6",
    "Float": "i=10", "String": "i=12", "LocalizedText": "i=21", "Argument": "i=296",
    "Organizes": "i=35", "HasModellingRule": "i=37", "HasTypeDefinition": "i=40",
    "HasSubtype": "i=45", "HasProperty": "i=46", "HasComponent": "i=47",
}
DATATYPE_UA = {"BOOL": "Boolean", "BYTE": "Byte", "WORD": "UInt16", "INT": "Int16",
               "DINT": "Int32", "REAL": "Float", "STRING": "String"}
VALOR_INICIAL = {"BOOL": "false", "REAL": "0"}            # resto: "0"
AREAS = [("I", "Entradas (%I)"), ("Q", "Salidas (%Q)"), ("M", "Marcas (%M)"), ("DB", "Bloque de datos")]
OBLIGATORIO, OPCIONAL = "i=78", "i=80"                     # ModellingRule Mandatory / Optional
BASE_OBJECT_TYPE, BASE_DATA_VARIABLE_TYPE, PROPERTY_TYPE, OBJECTS = "i=58", "i=63", "i=68", "i=85"
NOMBRE_ADDRESS = "Address"
# Propiedades del tipo Address: nombre, DataType, ModellingRule
PROPIEDADES_ADDRESS = [("area", "AreaPLC", OBLIGATORIO), ("byte", "Int32", OBLIGATORIO),
                       ("bit", "Int32", OPCIONAL), ("db", "Int32", OPCIONAL)]


class _Generador:
    def __init__(self, planta, fecha):
        self.p = planta
        self.fecha = fecha
        self.raiz = ET.Element(_N("UANodeSet"))
        self._siguiente = {"tipo_objeto": 1001, "nodo_tipo": 5001}
        self.ids = {}

    def nuevo_id(self, clase):
        n = self._siguiente[clase]
        self._siguiente[clase] += 1
        return f"ns=1;i={n}"

    # --- elementos basicos ---
    def nodo(self, clase, nodeid, nombre, padre=None, descripcion="", **atributos):
        el = ET.SubElement(self.raiz, _N(clase))
        el.set("NodeId", nodeid)
        el.set("BrowseName", f"1:{nombre}")
        if padre:
            el.set("ParentNodeId", padre)
        for k, v in atributos.items():
            el.set(k, str(v))
        ET.SubElement(el, _N("DisplayName")).text = nombre
        if descripcion:
            ET.SubElement(el, _N("Description")).text = descripcion
        el.append(ET.Element(_N("References")))
        return el

    @staticmethod
    def ref(el, tipo, destino, directa=True):
        r = ET.SubElement(el.find(_N("References")), _N("Reference"))
        r.set("ReferenceType", tipo)
        if not directa:
            r.set("IsForward", "false")
        r.text = destino

    def hijo(self, padre_el, padre_id, el, tipo_ref):
        """Referencia padre -> hijo y la inversa en el hijo."""
        self.ref(padre_el, tipo_ref, el.get("NodeId"))
        self.ref(el, tipo_ref, padre_id, directa=False)

    @staticmethod
    def valor(el, tipo_xml, texto):
        v = ET.SubElement(el, _N("Value"))
        ET.SubElement(v, _X(tipo_xml)).text = texto

    # --- cabecera ---
    def cabecera(self):
        uris = ET.SubElement(self.raiz, _N("NamespaceUris"))
        ET.SubElement(uris, _N("Uri")).text = self.p.uri
        modelos = ET.SubElement(self.raiz, _N("Models"))
        m = ET.SubElement(modelos, _N("Model"), ModelUri=self.p.uri, Version=self.p.version,
                          PublicationDate=self.fecha)
        ET.SubElement(m, _N("RequiredModel"), ModelUri="http://opcfoundation.org/UA/",
                      Version="1.05.05", PublicationDate="2025-06-30T00:00:00Z")
        alias = ET.SubElement(self.raiz, _N("Aliases"))
        for k, v in ALIAS.items():
            ET.SubElement(alias, _N("Alias"), Alias=k).text = v

    # --- tipo Address y enumerado AreaPLC ---
    def tipo_address(self):
        area = "ns=1;i=3001"
        el = self.nodo("UADataType", area, "AreaPLC", descripcion="Area de memoria del controlador")
        self.ref(el, "HasSubtype", "i=29", directa=False)
        definicion = ET.SubElement(el, _N("Definition"), Name="1:AreaPLC")
        for i, (nombre, desc) in enumerate(AREAS):
            f = ET.SubElement(definicion, _N("Field"), Name=nombre, Value=str(i))
            ET.SubElement(f, _N("Description")).text = desc
        enum = self.nodo("UAVariable", "ns=1;i=6100", "EnumStrings", area, DataType="LocalizedText",
                         ValueRank="1", ArrayDimensions=str(len(AREAS)))
        enum.set("BrowseName", "EnumStrings")                 # espacio 0
        self.ref(enum, "HasTypeDefinition", PROPERTY_TYPE)
        self.hijo(el, area, enum, "HasProperty")
        v = ET.SubElement(ET.SubElement(enum, _N("Value")), _X("ListOfLocalizedText"))
        for nombre, _ in AREAS:
            lt = ET.SubElement(v, _X("LocalizedText"))
            ET.SubElement(lt, _X("Locale"))
            ET.SubElement(lt, _X("Text")).text = nombre
        self.ids["AreaPLC"] = area

        address = "ns=1;i=2003"
        el = self.nodo("UAVariableType", address, NOMBRE_ADDRESS, ValueRank="-1",
                       descripcion="Direccion fisica en el controlador")
        self.ref(el, "HasSubtype", BASE_DATA_VARIABLE_TYPE, directa=False)
        for k, (nombre, tipo, regla) in enumerate(PROPIEDADES_ADDRESS):
            pid = f"ns=1;i={6101 + k}"
            pr = self.nodo("UAVariable", pid, nombre, address, DataType=self.ids.get(tipo, tipo),
                           AccessLevel="1")
            self.ref(pr, "HasTypeDefinition", PROPERTY_TYPE)
            self.ref(pr, "HasModellingRule", regla)
            self.hijo(el, address, pr, "HasProperty")
        self.ids[NOMBRE_ADDRESS] = address

    # --- variables con direccion (en tipos e instancias) ---
    def variable(self, padre_el, padre_id, nodeid, v, propiedades, en_tipo):
        acceso = "3" if v.escritura else "1"
        el = self.nodo("UAVariable", nodeid, v.nombre, padre_id, v.descripcion,
                       DataType=DATATYPE_UA[v.tipo], AccessLevel=acceso, UserAccessLevel=acceso)
        self.ref(el, "HasTypeDefinition", self.ids[NOMBRE_ADDRESS])
        if en_tipo:
            self.ref(el, "HasModellingRule", OBLIGATORIO)
        self.hijo(padre_el, padre_id, el, "HasComponent")
        self.valor(el, DATATYPE_UA[v.tipo], VALOR_INICIAL.get(v.tipo, "0"))
        for nombre, valor in propiedades.items():
            pid = self.nuevo_id("nodo_tipo") if en_tipo else f"{nodeid}.{nombre}"
            tipo = self.ids["AreaPLC"] if nombre == "area" else "Int32"
            pr = self.nodo("UAVariable", pid, nombre, nodeid, DataType=tipo, AccessLevel="1")
            self.ref(pr, "HasTypeDefinition", PROPERTY_TYPE)
            if en_tipo:
                self.ref(pr, "HasModellingRule", OBLIGATORIO)
            self.hijo(el, nodeid, pr, "HasProperty")
            if valor is not None:
                self.valor(pr, "Int32", str(valor))
        return el

    def estructura(self, raiz_el, raiz_id, td, nodeid_de, propiedades_de, en_tipo):
        """Grupos y variables de un tipo de dispositivo bajo raiz (tipo o instancia)."""
        grupos = {}
        for g in td.grupos:
            gid = nodeid_de(g)
            gel = self.nodo("UAObject", gid, g, raiz_id)
            self.ref(gel, "HasTypeDefinition", BASE_OBJECT_TYPE)
            if en_tipo:
                self.ref(gel, "HasModellingRule", OBLIGATORIO)
            self.hijo(raiz_el, raiz_id, gel, "HasComponent")
            grupos[g] = (gel, gid)
        for v in td.variables:
            pel, pid = grupos[v.grupo] if v.grupo else (raiz_el, raiz_id)
            self.variable(pel, pid, nodeid_de(v.ruta), v, propiedades_de(v), en_tipo)

    def argumentos(self, metodo_el, metodo_id, nombre, args, nodeid):
        """Propiedad InputArguments/OutputArguments con la lista de Argument."""
        pr = self.nodo("UAVariable", nodeid, nombre, metodo_id, DataType="Argument",
                       ValueRank="1", ArrayDimensions=str(len(args)))
        pr.set("BrowseName", nombre)                           # espacio 0
        self.ref(pr, "HasTypeDefinition", PROPERTY_TYPE)
        self.hijo(metodo_el, metodo_id, pr, "HasProperty")
        lista = ET.SubElement(ET.SubElement(pr, _N("Value")), _X("ListOfExtensionObject"))
        for a in args:
            eo = ET.SubElement(lista, _X("ExtensionObject"))
            ET.SubElement(ET.SubElement(eo, _X("TypeId")), _X("Identifier")).text = "i=297"
            arg = ET.SubElement(ET.SubElement(eo, _X("Body")), _X("Argument"))
            ET.SubElement(arg, _X("Name")).text = a.nombre
            ET.SubElement(ET.SubElement(arg, _X("DataType")), _X("Identifier")).text = \
                ALIAS[DATATYPE_UA[a.tipo]]
            ET.SubElement(arg, _X("ValueRank")).text = "-1"
            ET.SubElement(arg, _X("ArrayDimensions"))
            d = ET.SubElement(arg, _X("Description"))
            ET.SubElement(d, _X("Locale"))
            ET.SubElement(d, _X("Text")).text = a.descripcion
        return pr

    def metodos(self, padre_el, padre_id, td, nodeid_de, en_tipo):
        """UAMethod con sus argumentos. En la instancia, MethodDeclarationId apunta al del tipo."""
        for m in td.metodos:
            mid = nodeid_de(m.nombre)
            extra = {} if en_tipo else {"MethodDeclarationId": self.ids[(td.nombre, m.nombre)]}
            el = self.nodo("UAMethod", mid, m.nombre, padre_id, m.descripcion, **extra)
            if en_tipo:
                self.ref(el, "HasModellingRule", OBLIGATORIO)
                self.ids[(td.nombre, m.nombre)] = mid
            self.hijo(padre_el, padre_id, el, "HasComponent")
            for nombre, args in (("InputArguments", m.entradas), ("OutputArguments", m.salidas)):
                if args:
                    pid = self.nuevo_id("nodo_tipo") if en_tipo else f"{mid}.{nombre}"
                    pr = self.argumentos(el, mid, nombre, args, pid)
                    if en_tipo:
                        self.ref(pr, "HasModellingRule", OBLIGATORIO)

    def tipos(self):
        for td in self.p.tipos.values():
            tid = self.nuevo_id("tipo_objeto")
            el = self.nodo("UAObjectType", tid, td.nombre, descripcion=td.descripcion)
            self.ref(el, "HasSubtype", BASE_OBJECT_TYPE, directa=False)
            self.ids[td.nombre] = tid

            def props_tipo(v):
                return {k: None for k in ("area", "byte", "bit")
                        if k != "bit" or v.tipo == "BOOL"}

            self.estructura(el, tid, td, lambda _r: self.nuevo_id("nodo_tipo"), props_tipo, True)
            self.metodos(el, tid, td, lambda _r: self.nuevo_id("nodo_tipo"), True)

    def instancias(self):
        for i in self.p.instancias:
            iid = f"ns=1;s={i.nombre}"
            el = self.nodo("UAObject", iid, i.nombre, descripcion=i.descripcion)
            self.ref(el, "HasTypeDefinition", self.ids[i.tipo.nombre])
            self.ref(el, "Organizes", OBJECTS, directa=False)
            # NodeId de texto Instancia.Grupo.Variable, como en ledmodel_v5.xml
            self.estructura(el, iid, i.tipo, lambda r, n=i.nombre: f"ns=1;s={n}.{r.replace('/', '.')}",
                            lambda v, d=i.direcciones: d[v.ruta], False)
            self.metodos(el, iid, i.tipo, lambda r, n=i.nombre: f"ns=1;s={n}.{r}", False)

    def generar(self):
        self.cabecera()
        self.tipo_address()
        self.tipos()
        self.instancias()
        ET.indent(self.raiz, space="    ")
        return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(self.raiz, encoding="unicode") + "\n"


def generar_nodeset(planta, fecha=None):
    """Planta -> texto XML del NodeSet2. fecha: PublicationDate (ISO 8601, UTC)."""
    fecha = fecha or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return _Generador(planta, fecha).generar()


def generar_fichero(fuente, destino, fecha=None):
    planta = fuente if isinstance(fuente, Planta) else cargar_planta(fuente)
    with open(destino, "w", encoding="utf-8") as f:
        f.write(generar_nodeset(planta, fecha))
    return planta


# ---------------------------------------------------------------------------
# Importacion con validacion (comun al servidor y a la interfaz)
# ---------------------------------------------------------------------------
def comprobar_variable(ruta, propiedades, tipo_dato, validar):
    """
    Valida una variable de tipo Address. propiedades: {BrowseName: valor};
    tipo_dato: ua.VariantType o None si el DataType no es un tipo basico;
    validar: Driver.validar_direccion. Devuelve la direccion del driver o
    lanza ValueError con la ruta y todo lo que falta.
    """
    faltan = [k for k in ("area", "byte") if propiedades.get(k) is None]
    if tipo_dato == ua.VariantType.Boolean and propiedades.get("bit") is None:
        faltan.append("bit")
    if faltan:
        detalle = " (obligatorio en variables Boolean)" if "bit" in faltan else ""
        raise ValueError(f"{ruta}: falta {', '.join(faltan)}{detalle}")
    if tipo_dato is None:
        raise ValueError(f"{ruta}: DataType no soportado (soportados: "
                         f"{', '.join(v.name for v in TIPOS_DATO.values())})")
    try:
        return validar(propiedades, tipo_dato)
    except ValueError as e:
        raise ValueError(f"{ruta}: {e}") from None


def tabla_planta(planta, driver):
    """Tabla de mapeo de una planta: [{ruta, direccion, tipo, sentido}] (driver sin conectar)."""
    filas = []
    for i in planta.instancias:
        for v in i.tipo.variables:
            d = driver.validar_direccion(i.direcciones[v.ruta], TIPOS_DATO[v.tipo])
            filas.append({"ruta": f"{i.nombre}/{v.ruta}", "direccion": driver.texto_direccion(d),
                          "tipo": v.tipo, "sentido": "OPC UA -> PLC" if v.escritura else "PLC -> OPC UA"})
    return filas


def tabla_importacion(imp, driver):
    """Tabla de mapeo de una Importacion (solo variables validas)."""
    return [{"ruta": v.ruta, "direccion": driver.texto_direccion(v.direccion),
             "tipo": v.tipo.name if v.tipo else None,
             "sentido": "OPC UA -> PLC" if v.escritura else "PLC -> OPC UA"}
            for v in imp.variables if v.direccion is not None]


def mensaje_modelo_incompleto(errores):
    return "Modelo incompleto:\n  " + "\n  ".join(errores)


@dataclass
class VariableImportada:
    ruta: str                  # BrowseNames desde Objects, separados por '/'
    nodeid: str                # tal como aparece en el NodeSet
    tipo: object               # ua.VariantType o None
    escritura: bool
    propiedades: dict
    direccion: object = None   # resultado de validar; None si hay error


@dataclass
class Importacion:
    uri: str
    variables: list
    errores: list
    metodos: list = field(default_factory=list)    # [(ruta, nombre)]


_JERARQUICAS = {"Organizes", "HasComponent", "HasProperty", "HasOrderedComponent", "i=35", "i=47",
                "i=46", "i=49"}


def importar_nodeset(fichero, validar):
    """
    Lee un NodeSet2 sin servidor OPC UA y valida, con comprobar_variable, cada
    variable de tipo Address bajo Objects. No lanza por variables invalidas:
    las devuelve en Importacion.errores (todas a la vez).
    """
    raiz = ET.parse(fichero).getroot()
    q = lambda e: f"{{{NS_UANODESET}}}{e}"
    uris = [u.text.strip() for u in raiz.iterfind(f"{q('NamespaceUris')}/{q('Uri')}")]
    modelos = [m.get("ModelUri") for m in raiz.iter(q("Model")) if m.get("ModelUri")]
    if not modelos:
        return Importacion(None, [], [f"{fichero}: el NodeSet no declara <Models><Model ModelUri=...>"])
    uri = modelos[0].strip()
    ns = uris.index(uri) + 1 if uri in uris else None
    alias = {a.get("Alias"): a.text.strip() for a in raiz.iter(q("Alias"))}
    nodos = {el.get("NodeId"): el for el in raiz if el.get("NodeId")}
    en_ns = lambda nid: ns is not None and nid.startswith(f"ns={ns};")

    def nombre(el):
        bn = el.get("BrowseName", "")
        return bn.split(":", 1)[1] if ":" in bn else bn

    address = next((nid for nid, el in nodos.items() if el.tag == q("UAVariableType") and en_ns(nid)
                    and el.get("BrowseName") == f"{ns}:Address"), None)
    if address is None:
        return Importacion(uri, [], [f"el modelo ({uri}) no define el VariableType Address"])

    # Referencias jerarquicas en ambos sentidos y definiciones de tipo
    hijos, tipo_de = {}, {}
    for nid, el in nodos.items():
        for r in el.iter(q("Reference")):
            rt, destino, directa = alias.get(r.get("ReferenceType"), r.get("ReferenceType")), \
                r.text.strip(), r.get("IsForward") != "false"
            if r.get("ReferenceType") == "HasTypeDefinition" or rt == "i=40":
                tipo_de[nid] = destino
            elif r.get("ReferenceType") in _JERARQUICAS or rt in _JERARQUICAS:
                padre, hijo = (nid, destino) if directa else (destino, nid)
                hijos.setdefault(padre, [])
                if hijo not in hijos[padre]:
                    hijos[padre].append(hijo)

    def valor(el):
        v = el.find(q("Value"))
        if v is None or not len(v):
            return None
        t = (v[0].text or "").strip()
        try:
            return int(t)
        except ValueError:
            return t or None

    def tipo_dato(el):
        dt = alias.get(el.get("DataType", ""), el.get("DataType", ""))
        dt = dt[5:] if dt.startswith("ns=0;") else dt
        if dt.startswith("i="):
            try:
                return ua.VariantType(int(dt[2:]))     # el driver decide si lo soporta
            except ValueError:
                return None
        return None

    variables, errores, metodos = [], [], []

    def visitar(nid, ruta):
        for h in hijos.get(nid, []):
            el = nodos.get(h)
            if el is None or not en_ns(h):
                continue
            ruta_h = f"{ruta}/{nombre(el)}" if ruta else nombre(el)
            if el.tag == q("UAVariable"):
                if tipo_de.get(h) == address:
                    props = {nombre(nodos[p]): valor(nodos[p]) for p in hijos.get(h, [])
                             if p in nodos and nodos[p].tag == q("UAVariable")}
                    vt = tipo_dato(el)
                    acceso = int(el.get("AccessLevel", "1"))
                    v = VariableImportada(ruta_h, h, vt, bool(acceso & 2), props)
                    try:
                        v.direccion = comprobar_variable(ruta_h, props, vt, validar)
                    except ValueError as e:
                        errores.append(str(e))
                    variables.append(v)
            elif el.tag == q("UAMethod"):
                metodos.append((ruta_h, nombre(el)))
            elif el.tag == q("UAObject"):
                visitar(h, ruta_h)

    visitar("i=85", "")            # desde Objects, como el servidor
    return Importacion(uri, variables, errores, metodos)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Genera un NodeSet2 desde una descripcion de planta YAML")
    ap.add_argument("planta", help="fichero YAML de descripcion de planta")
    ap.add_argument("-o", "--salida", required=True, help="fichero NodeSet2 (XML) de salida")
    a = ap.parse_args(argv)
    try:
        planta = generar_fichero(a.planta, a.salida)
    except (ErrorPlanta, OSError) as e:
        print(e, file=sys.stderr)
        return 1
    n = sum(len(i.direcciones) for i in planta.instancias)
    for aviso in planta.avisos:
        print(f"AVISO: {aviso}", file=sys.stderr)
    print(f"{a.salida}: {len(planta.tipos)} tipos, {len(planta.instancias)} instancias, {n} variables")
    return 0


if __name__ == "__main__":
    sys.exit(main())
