"""
Configuracion unica del servidor en YAML (Fase C). La usan el servidor
(--config) y la interfaz.

    servidor:  endpoint, app_uri, host, perfiles
    driver:    nombre, parametros (los que declara el driver, contrato v1.2)
    enlace:    fallos_max, reintento_max_s
    modelo:    origen (nodeset | planta), nodeset, planta
    seguridad: modo (none | firmar | estricto), cert_dir, lista_confianza,
               usuarios: [{nombre, clave_hash}]  (o 'clave' en claro, con aviso)
    medida:    periodo_ms, log_csv, log_accesos

Las rutas relativas del fichero se interpretan respecto a la carpeta del
propio fichero. Los parametros sueltos de la linea de ordenes que se den
explicitamente prevalecen sobre el fichero.
"""

import argparse
import copy
import getpass
import os
import sys
from types import SimpleNamespace

import yaml

import drivers
from seguridad import ClaveHash, es_hash_valido, hash_clave

DEFECTOS = {
    "servidor": {"endpoint": "opc.tcp://127.0.0.1:4840", "app_uri": "urn:uja:opcua:servidor-desacoplado",
                 "host": None, "perfiles": []},
    "driver": {"nombre": "snap7", "parametros": {}},
    "enlace": {"fallos_max": 3, "reintento_max_s": 10.0},
    "modelo": {"origen": "nodeset", "nodeset": None, "planta": None},
    "seguridad": {"modo": "none", "cert_dir": "pki", "lista_confianza": True, "usuarios": []},
    "medida": {"periodo_ms": 100.0, "log_csv": None, "log_accesos": None},
}
MODOS_SEGURIDAD = ("none", "firmar", "estricto")
ORIGENES = ("nodeset", "planta")
RUTAS = [("modelo", "nodeset"), ("modelo", "planta"), ("seguridad", "cert_dir"),
         ("medida", "log_csv"), ("medida", "log_accesos")]

# Opcion de la linea de ordenes (dest de argparse) -> clave del YAML
OPCIONES_CLI = {
    "model": ("modelo", "nodeset"),
    "driver": ("driver", "nombre"),
    "driver_param": ("driver", "parametros"),
    "plc_ip": ("driver", "parametros", "ip"),
    "rack": ("driver", "parametros", "rack"),
    "slot": ("driver", "parametros", "slot"),
    "timeout_ms": ("driver", "parametros", "timeout_ms"),
    "fallos_max": ("enlace", "fallos_max"),
    "reintento_max_s": ("enlace", "reintento_max_s"),
    "periodo_ms": ("medida", "periodo_ms"),
    "log_csv": ("medida", "log_csv"),
    "log_accesos": ("medida", "log_accesos"),
    "endpoint": ("servidor", "endpoint"),
    "app_uri": ("servidor", "app_uri"),
    "host": ("servidor", "host"),
    "perfil": ("servidor", "perfiles"),
    "seguridad": ("seguridad", "modo"),
    "cert_dir": ("seguridad", "cert_dir"),
    "lista_confianza": ("seguridad", "lista_confianza"),
    "usuario": ("seguridad", "usuarios"),
}
# Opciones de control del proceso, sin clave en el YAML
OPCIONES_PROCESO = {"help", "config", "parada_stdin", "fichero_pid", "fichero_control"}
# Opciones antiguas del driver snap7 equivalentes a --driver-param
ALIAS_PARAMETROS = {"plc_ip": "ip", "rack": "rack", "slot": "slot", "timeout_ms": "timeout_ms"}


class ErrorConfig(ValueError):
    def __init__(self, errores):
        self.errores = list(errores)
        super().__init__("Configuracion invalida:\n  " + "\n  ".join(self.errores))


# ---------------------------------------------------------------------------
# Lectura, fusion con la linea de ordenes y validacion
# ---------------------------------------------------------------------------
def leer_yaml(ruta):
    try:
        with open(ruta, encoding="utf-8") as f:
            datos = yaml.safe_load(f) or {}
    except OSError as e:
        raise ErrorConfig([f"no se puede leer {ruta}: {e}"]) from None
    except yaml.YAMLError as e:
        raise ErrorConfig([f"{ruta}: YAML mal formado: {e}"]) from None
    if not isinstance(datos, dict):
        raise ErrorConfig([f"{ruta}: debe ser un mapa con los bloques {', '.join(DEFECTOS)}"])
    return datos


def _rellenar(datos, errores):
    """Defectos + datos del fichero, comprobando bloques y claves conocidos."""
    cfg = copy.deepcopy(DEFECTOS)
    for bloque, contenido in (datos or {}).items():
        if bloque not in DEFECTOS:
            errores.append(f"bloque '{bloque}' desconocido (validos: {', '.join(DEFECTOS)})")
            continue
        if contenido is None:
            continue
        if not isinstance(contenido, dict):
            errores.append(f"{bloque}: debe ser un mapa")
            continue
        for clave, valor in contenido.items():
            if clave not in DEFECTOS[bloque]:
                errores.append(f"{bloque}.{clave}: clave desconocida "
                               f"(validas: {', '.join(DEFECTOS[bloque])})")
                continue
            cfg[bloque][clave] = valor
    return cfg


def _resolver_rutas(cfg, base):
    for bloque, clave in RUTAS:
        v = cfg[bloque][clave]
        if isinstance(v, str) and v and not os.path.isabs(v):
            cfg[bloque][clave] = os.path.normpath(os.path.join(base, v))


def _aplicar_cli(cfg, args, errores):
    """Opciones dadas explicitamente (distintas de None) sobre la configuracion."""
    params = cfg["driver"]["parametros"] = dict(cfg["driver"]["parametros"] or {})
    sueltos = {}
    for item in getattr(args, "driver_param", None) or []:
        if "=" not in item:
            errores.append(f"--driver-param '{item}': use clave=valor")
            continue
        k, v = item.split("=", 1)
        sueltos[k.strip()] = v.strip()
    params.update(sueltos)
    for dest, param in ALIAS_PARAMETROS.items():
        v = getattr(args, dest, None)
        if v is None:
            continue
        if param in sueltos and str(sueltos[param]) != str(v):
            opcion = "--" + dest.replace("_", "-")
            errores.append(f"{opcion} {v} y --driver-param {param}={sueltos[param]} se contradicen")
        params[param] = v
    for dest, ruta in OPCIONES_CLI.items():
        if dest in ALIAS_PARAMETROS or dest == "driver_param":
            continue
        v = getattr(args, dest, None)
        if v is None or v == []:
            continue
        if dest == "usuario":
            usuarios = []
            for item in v:
                if ":" not in item:
                    errores.append(f"--usuario '{item}': use nombre:clave")
                    continue
                n, c = item.split(":", 1)
                usuarios.append({"nombre": n, "clave": c, "_cli": True})
            v = usuarios
        cfg[ruta[0]][ruta[1]] = v
        if dest == "model":
            cfg["modelo"]["origen"] = "nodeset"


def validar(cfg, comprobar_ficheros=True):
    """Devuelve (errores, avisos) de una configuracion ya rellenada."""
    errores, avisos = [], []
    s, d, e, m, seg, med = (cfg[k] for k in ("servidor", "driver", "enlace", "modelo", "seguridad", "medida"))

    if not isinstance(s["endpoint"], str) or not s["endpoint"].startswith("opc.tcp://"):
        errores.append(f"servidor.endpoint: '{s['endpoint']}' debe empezar por opc.tcp://")
    if not isinstance(s["app_uri"], str) or not s["app_uri"]:
        errores.append("servidor.app_uri: debe ser un texto no vacio")
    if s["host"] is not None and not isinstance(s["host"], str):
        errores.append("servidor.host: debe ser un texto o null")
    if not isinstance(s["perfiles"], list) or not all(isinstance(x, str) for x in s["perfiles"]):
        errores.append("servidor.perfiles: debe ser una lista de URIs")

    if d["nombre"] not in drivers.DRIVERS:
        errores.append(f"driver.nombre: '{d['nombre']}' desconocido "
                       f"(disponibles: {', '.join(drivers.disponibles())})")
    elif not isinstance(d["parametros"], dict):
        errores.append("driver.parametros: debe ser un mapa")
    else:
        try:
            d["parametros"] = drivers.clase_driver(d["nombre"]).validar_parametros(d["parametros"])
        except ValueError as ex:
            errores.append(f"driver.parametros: {ex}")

    if isinstance(e["fallos_max"], bool) or not isinstance(e["fallos_max"], int) or e["fallos_max"] < 1:
        errores.append("enlace.fallos_max: debe ser un entero >= 1")
    if isinstance(e["reintento_max_s"], bool) or not isinstance(e["reintento_max_s"], (int, float)) \
            or e["reintento_max_s"] <= 0:
        errores.append("enlace.reintento_max_s: debe ser un numero > 0")

    if m["origen"] not in ORIGENES:
        errores.append(f"modelo.origen: '{m['origen']}' no valido (nodeset o planta)")
    if not isinstance(m["nodeset"], str) or not m["nodeset"]:
        errores.append("modelo.nodeset: falta la ruta del NodeSet" +
                       (" (destino del generado)" if m["origen"] == "planta" else "") + " o -m")
    elif comprobar_ficheros and m["origen"] == "nodeset" and not os.path.isfile(m["nodeset"]):
        errores.append(f"modelo.nodeset: no existe {m['nodeset']}")
    if m["origen"] == "planta":
        if not isinstance(m["planta"], str) or not m["planta"]:
            errores.append("modelo.planta: falta la descripcion de planta (origen: planta)")
        elif comprobar_ficheros and not os.path.isfile(m["planta"]):
            errores.append(f"modelo.planta: no existe {m['planta']}")

    if seg["modo"] not in MODOS_SEGURIDAD:
        errores.append(f"seguridad.modo: '{seg['modo']}' no valido ({', '.join(MODOS_SEGURIDAD)})")
    if not isinstance(seg["cert_dir"], str) or not seg["cert_dir"]:
        errores.append("seguridad.cert_dir: debe ser una ruta")
    if not isinstance(seg["lista_confianza"], bool):
        errores.append("seguridad.lista_confianza: debe ser true o false")
    if not isinstance(seg["usuarios"], list):
        errores.append("seguridad.usuarios: debe ser una lista")
    else:
        for k, u in enumerate(seg["usuarios"]):
            donde = f"seguridad.usuarios[{k}]"
            if not isinstance(u, dict) or not isinstance(u.get("nombre"), str) or not u.get("nombre"):
                errores.append(f"{donde}: debe tener 'nombre'")
                continue
            extra = set(u) - {"nombre", "clave", "clave_hash", "_cli"}
            if extra:
                errores.append(f"{donde}: claves desconocidas {sorted(extra)} (validas: nombre, clave_hash, clave)")
            if ("clave" in u) == ("clave_hash" in u):
                errores.append(f"{donde} ({u['nombre']}): indique 'clave_hash' (recomendado) o 'clave', una sola")
            elif "clave_hash" in u and not es_hash_valido(u["clave_hash"]):
                errores.append(f"{donde} ({u['nombre']}): clave_hash no tiene el formato pbkdf2_sha256$...")
            elif "clave" in u and not u.get("_cli"):
                avisos.append(f"{donde} ({u['nombre']}): contrasena guardada en claro en la configuracion; "
                              f"use clave_hash (py servidor/configuracion.py --hash-clave)")

    for clave in ("periodo_ms",):
        v = med[clave]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
            errores.append(f"medida.{clave}: debe ser un numero > 0")
    for clave in ("log_csv", "log_accesos"):
        if med[clave] is not None and not isinstance(med[clave], str):
            errores.append(f"medida.{clave}: debe ser una ruta o null")
    return errores, avisos


def configuracion_efectiva(ruta_config=None, args=None, comprobar_ficheros=True):
    """
    Defectos <- fichero (si se da) <- linea de ordenes. Devuelve (cfg, avisos).
    Lanza ErrorConfig con todos los errores.
    """
    errores = []
    datos = leer_yaml(ruta_config) if ruta_config else {}
    cfg = _rellenar(datos, errores)
    if ruta_config:
        _resolver_rutas(cfg, os.path.dirname(os.path.abspath(ruta_config)))
    if args is not None:
        _aplicar_cli(cfg, args, errores)
    if not errores:
        e2, avisos = validar(cfg, comprobar_ficheros)
        errores += e2
    if errores:
        raise ErrorConfig(errores)
    return cfg, avisos


def usuarios_de(cfg):
    """{nombre: ClaveHash | clave en claro} para seguridad.crear_gestor."""
    return {u["nombre"]: ClaveHash(u["clave_hash"]) if "clave_hash" in u else u["clave"]
            for u in cfg["seguridad"]["usuarios"]}


def como_args(cfg):
    """Vista con los nombres de las opciones del servidor."""
    s, m, seg, med, e = cfg["servidor"], cfg["modelo"], cfg["seguridad"], cfg["medida"], cfg["enlace"]
    return SimpleNamespace(model=m["nodeset"], driver=cfg["driver"]["nombre"],
                           parametros=dict(cfg["driver"]["parametros"]), endpoint=s["endpoint"],
                           app_uri=s["app_uri"], host=s["host"], perfil=list(s["perfiles"]) or None,
                           seguridad=seg["modo"], cert_dir=seg["cert_dir"], lista_confianza=seg["lista_confianza"], periodo_ms=float(med["periodo_ms"]),
                           log_csv=med["log_csv"], log_accesos=med["log_accesos"],
                           fallos_max=e["fallos_max"], reintento_max_s=float(e["reintento_max_s"]))


def guardar(datos, ruta):
    """Valida (sin la linea de ordenes) y escribe el YAML. Devuelve los avisos."""
    errores = []
    cfg = _rellenar(datos, errores)
    if not errores:
        base = os.path.dirname(os.path.abspath(ruta))
        prueba = copy.deepcopy(cfg)
        _resolver_rutas(prueba, base)
        e2, avisos = validar(prueba)
        errores += e2
    if errores:
        raise ErrorConfig(errores)
    with open(ruta, "w", encoding="utf-8") as f:
        f.write("# Configuracion del servidor OPC UA (generada; ver GUIA_DESPLIEGUE.md)\n")
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return avisos


def main(argv=None):
    ap = argparse.ArgumentParser(description="Utilidades de la configuracion del servidor")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--hash-clave", action="store_true", help="pide una contrasena y muestra su clave_hash")
    g.add_argument("--validar", metavar="CONFIG", help="valida un fichero de configuracion")
    a = ap.parse_args(argv)
    if a.hash_clave:
        clave = getpass.getpass("Contrasena: ")
        if clave != getpass.getpass("Repita la contrasena: "):
            print("Las contrasenas no coinciden", file=sys.stderr)
            return 1
        print(hash_clave(clave))
        return 0
    try:
        _, avisos = configuracion_efectiva(a.validar)
    except ErrorConfig as e:
        print(e, file=sys.stderr)
        return 1
    for x in avisos:
        print(f"AVISO: {x}", file=sys.stderr)
    print(f"{a.validar}: configuracion valida")
    return 0


if __name__ == "__main__":
    sys.exit(main())
