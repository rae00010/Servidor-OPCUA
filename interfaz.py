"""
Interfaz web local del servidor OPC UA (Fase C).

    py interfaz.py [--carpeta .] [--host 127.0.0.1] [--puerto 8080]
                   [--usuario nombre:clave] [--tls-cert C --tls-key K]

No implementa logica de servidor ni de modelo: llama a drivers (Fase A),
modelo (Fase B), configuracion y proceso (Fase C).

Seguridad:
  - Por defecto escucha solo en 127.0.0.1. Fuera de la maquina local exige
    usuario (--usuario o INTERFAZ_USUARIO / INTERFAZ_CLAVE): la contrasena se
    comprueba una vez (PBKDF2) y se mantiene una sesion por cookie HttpOnly.
  - Las peticiones que modifican exigen la cabecera X-Interfaz (una web ajena
    no puede ponerla sin permiso CORS) y, en local, se comprueba Host
    (proteccion frente a DNS rebinding).
  - Todas las rutas de ficheros son relativas a la carpeta de trabajo: se
    rechazan las absolutas y las que contienen '..'; solo .xml y .yaml.
"""

import argparse
import asyncio
import copy
import hmac
import ipaddress
import os
import re
import secrets
import sys
import time
from pathlib import Path

import uvicorn
import yaml
from fastapi import Body, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import configuracion
import drivers
import modelo
from proceso import GestorServidor
from seguridad import DIR_CONFIANZA, DIR_RECHAZADOS, hash_clave, listar_rechazados, verificar_clave

PROYECTO = Path(__file__).resolve().parent
ESTATICOS = PROYECTO / "interfaz"
PLANTILLAS = {"celula": PROYECTO / "planta_celula.yaml",
              "variador": PROYECTO / "planta_ejemplo_variador.yaml"}
EXTENSIONES_MODELO = (".xml", ".yaml", ".yml")
SESION_S = 8 * 3600
COOKIE = "sesion_interfaz"
CABECERA = "x-interfaz"


class RutaNoValida(ValueError):
    pass


# ---------------------------------------------------------------------------
# Rutas dentro de la carpeta de trabajo
# ---------------------------------------------------------------------------
def ruta_segura(carpeta, nombre, extensiones=EXTENSIONES_MODELO):
    """Ruta relativa dentro de 'carpeta'. RutaNoValida si sale de ella o no es .xml/.yaml."""
    if not isinstance(nombre, str) or not nombre.strip():
        raise RutaNoValida("falta el nombre del fichero")
    n = nombre.strip().replace("\\", "/")
    if os.path.isabs(n) or n.startswith("/") or re.match(r"^[A-Za-z]:", n):
        raise RutaNoValida(f"'{nombre}': no se admiten rutas absolutas")
    if ".." in n.split("/"):
        raise RutaNoValida(f"'{nombre}': no se admite '..'")
    if extensiones and not n.lower().endswith(tuple(extensiones)):
        raise RutaNoValida(f"'{nombre}': solo se admiten ficheros {', '.join(extensiones)}")
    base = Path(carpeta).resolve()
    destino = (base / n).resolve()
    if os.path.commonpath([base, destino]) != str(base):
        raise RutaNoValida(f"'{nombre}': fuera de la carpeta de trabajo")
    return destino


def sanear_nombre(nombre):
    """Nombre de un fichero subido: sin carpetas, solo [A-Za-z0-9._-], sin punto inicial."""
    base = re.split(r"[\\/]", nombre or "")[-1]
    limpio = re.sub(r"[^A-Za-z0-9._-]", "_", base).lstrip(".")[:100]
    if not limpio.lower().endswith(EXTENSIONES_MODELO):
        raise RutaNoValida(f"'{nombre}': solo se admiten ficheros {', '.join(EXTENSIONES_MODELO)}")
    return limpio


def _comprobar_rutas_config(carpeta, datos):
    """Rutas de la configuracion recibida por la API: relativas y dentro de la carpeta."""
    errores = []
    m = (datos or {}).get("modelo") or {}
    for clave in ("nodeset", "planta"):
        if m.get(clave):
            try:
                ruta_segura(carpeta, m[clave], (".xml",) if clave == "nodeset" else (".yaml", ".yml"))
            except RutaNoValida as e:
                errores.append(f"modelo.{clave}: {e}")
    for bloque, clave, ext in (("seguridad", "cert_dir", None), ("medida", "log_csv", (".csv",)),
                               ("medida", "log_accesos", (".csv",))):
        v = ((datos or {}).get(bloque) or {}).get(clave)
        if v:
            try:
                ruta_segura(carpeta, v, ext)
            except RutaNoValida as e:
                errores.append(f"{bloque}.{clave}: {e}")
    return errores


# ---------------------------------------------------------------------------
# Aplicacion
# ---------------------------------------------------------------------------
def crear_app(carpeta, usuario=None, clave_hash=None, hosts_locales=None):
    carpeta = Path(carpeta).resolve()
    gestor = GestorServidor(carpeta)
    sesiones = {}                                   # token -> caducidad
    app = FastAPI(title="Interfaz del servidor OPC UA", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.gestor = gestor
    ruta_config = carpeta / "config.yaml"

    def error(errores, codigo=400):
        return JSONResponse({"errores": errores if isinstance(errores, list) else [str(errores)]},
                            status_code=codigo)

    @app.middleware("http")
    async def proteger(request: Request, siguiente):
        if hosts_locales is not None:
            host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
            if host not in hosts_locales:
                return JSONResponse({"errores": ["Host no permitido"]}, status_code=403)
        ruta = request.url.path
        if ruta.startswith("/api/"):
            if request.method not in ("GET", "HEAD") and request.headers.get(CABECERA) != "1":
                return JSONResponse({"errores": ["falta la cabecera X-Interfaz"]}, status_code=403)
            if usuario and ruta not in ("/api/login", "/api/sesion"):
                token = request.cookies.get(COOKIE)
                if not token or sesiones.get(token, 0) < time.time():
                    sesiones.pop(token, None)
                    return JSONResponse({"errores": ["sesion no iniciada"]}, status_code=401)
        return await siguiente(request)

    # --- sesion ---
    @app.get("/api/sesion")
    async def sesion(request: Request):
        token = request.cookies.get(COOKIE)
        return {"requiere_login": bool(usuario),
                "iniciada": not usuario or sesiones.get(token, 0) > time.time()}

    @app.post("/api/login")
    async def login(cuerpo: dict = Body(...)):
        if not usuario:
            return {"iniciada": True}
        ok_usuario = hmac.compare_digest(str(cuerpo.get("usuario", "")).encode(), usuario.encode())
        clave_ok = await asyncio.to_thread(verificar_clave, cuerpo.get("clave", ""), clave_hash)
        if not (clave_ok and ok_usuario):
            await asyncio.sleep(0.5)
            return error("usuario o contrasena incorrectos", 401)
        token = secrets.token_urlsafe(32)
        sesiones[token] = time.time() + SESION_S
        r = JSONResponse({"iniciada": True})
        r.set_cookie(COOKIE, token, max_age=SESION_S, httponly=True, samesite="strict")
        return r

    @app.post("/api/logout")
    async def logout(request: Request):
        sesiones.pop(request.cookies.get(COOKIE), None)
        r = JSONResponse({"iniciada": False})
        r.delete_cookie(COOKIE)
        return r

    # --- drivers y ficheros ---
    @app.get("/api/drivers")
    async def lista_drivers():
        return drivers.describir()

    @app.get("/api/ficheros")
    async def ficheros():
        return sorted(str(p.relative_to(carpeta)).replace("\\", "/") for p in carpeta.rglob("*")
                      if p.suffix.lower() in EXTENSIONES_MODELO and p.is_file()
                      and "logs" not in p.relative_to(carpeta).parts)

    @app.get("/api/plantillas")
    async def plantillas():
        return sorted(PLANTILLAS)

    @app.get("/api/plantillas/{nombre}")
    async def plantilla(nombre: str):
        if nombre not in PLANTILLAS:
            return error(f"plantilla '{nombre}' desconocida (disponibles: {', '.join(sorted(PLANTILLAS))})", 404)
        return {"nombre": nombre, "yaml": PLANTILLAS[nombre].read_text(encoding="utf-8")}

    # --- configuracion ---
    def _config_actual():
        datos = configuracion.leer_yaml(ruta_config) if ruta_config.exists() else {}
        errores = []
        cfg = configuracion._rellenar(datos, errores)
        return cfg

    def _publica(cfg):
        cfg = copy.deepcopy(cfg)
        cfg["seguridad"]["usuarios"] = [{"nombre": u.get("nombre"), "tiene_clave": True}
                                        for u in cfg["seguridad"]["usuarios"]]
        return cfg

    def _preparar(datos):
        """Configuracion recibida: rutas seguras y contrasenas nuevas -> clave_hash."""
        errores = _comprobar_rutas_config(carpeta, datos)
        datos = copy.deepcopy(datos or {})
        actuales = {u.get("nombre"): u for u in _config_actual()["seguridad"]["usuarios"]}
        usuarios = []
        for u in ((datos.get("seguridad") or {}).get("usuarios") or []):
            nombre = u.get("nombre")
            if u.get("clave"):
                usuarios.append({"nombre": nombre, "clave_hash": hash_clave(u["clave"])})
            elif nombre in actuales:
                usuarios.append({k: v for k, v in actuales[nombre].items() if k != "_cli"})
            else:
                errores.append(f"seguridad.usuarios: falta la contrasena de '{nombre}'")
        if "seguridad" in datos:
            datos["seguridad"]["usuarios"] = usuarios
        return datos, errores

    @app.get("/api/config")
    async def leer_config():
        try:
            return {"fichero": "config.yaml", "existe": ruta_config.exists(), "config": _publica(_config_actual())}
        except configuracion.ErrorConfig as e:
            return error(e.errores)

    @app.get("/api/certificados/rechazados")
    async def certificados_rechazados():
        """Solo lectura, con el motivo de cada rechazo: solo los no confiables
        (aceptable=True) se aceptan moviendo el fichero a <cert_dir>/confianza."""
        try:
            seg = _config_actual()["seguridad"]
        except configuracion.ErrorConfig as e:
            return error(e.errores)
        cert_dir = Path(seg["cert_dir"] or "pki")
        if not cert_dir.is_absolute():
            cert_dir = Path(carpeta) / cert_dir
        return {"carpeta": str(cert_dir / DIR_RECHAZADOS), "confianza": str(cert_dir / DIR_CONFIANZA),
                "certificados": listar_rechazados(cert_dir)}

    @app.post("/api/config/validar")
    async def validar_config(cuerpo: dict = Body(...)):
        datos, errores = _preparar(cuerpo.get("config"))
        if errores:
            return {"valida": False, "errores": errores, "avisos": []}
        errores = []
        cfg = configuracion._rellenar(datos, errores)
        configuracion._resolver_rutas(cfg, str(carpeta))
        if not errores:
            e2, avisos = configuracion.validar(cfg, comprobar_ficheros=True)
            errores += e2
        return {"valida": not errores, "errores": errores, "avisos": [] if errores else avisos}

    @app.put("/api/config")
    async def guardar_config(cuerpo: dict = Body(...)):
        datos, errores = _preparar(cuerpo.get("config"))
        if errores:
            return error(errores)
        try:
            avisos = configuracion.guardar(datos, ruta_config)
        except configuracion.ErrorConfig as e:
            return error(e.errores)
        return {"guardada": "config.yaml", "avisos": avisos}

    # --- modelo ---
    def _driver(nombre=None):
        cfg = _config_actual()
        nombre = nombre or cfg["driver"]["nombre"]
        params = cfg["driver"]["parametros"] if nombre == cfg["driver"]["nombre"] else {}
        return drivers.crear_driver(nombre, params)

    def _resultado_planta(texto):
        try:
            planta = modelo.cargar_planta(texto)
        except modelo.ErrorPlanta as e:
            return None, {"valida": False, "errores": e.errores, "avisos": [], "tabla": []}
        tabla = modelo.tabla_planta(planta, _driver(planta.driver))
        return planta, {"valida": True, "errores": [], "avisos": planta.avisos, "tabla": tabla,
                        "uri": planta.uri, "driver": planta.driver}

    @app.post("/api/modelo/validar")
    async def validar_planta(cuerpo: dict = Body(...)):
        _, r = _resultado_planta(str(cuerpo.get("yaml", "")))
        return r

    @app.post("/api/modelo/generar")
    async def generar(cuerpo: dict = Body(...)):
        try:
            ruta_planta = ruta_segura(carpeta, cuerpo.get("planta"), (".yaml", ".yml"))
            ruta_nodeset = ruta_segura(carpeta, cuerpo.get("nodeset"), (".xml",))
        except RutaNoValida as e:
            return error(str(e))
        texto = str(cuerpo.get("yaml", ""))
        planta, r = _resultado_planta(texto)
        if planta is None:
            return JSONResponse(r, status_code=400)
        ruta_planta.parent.mkdir(parents=True, exist_ok=True)
        ruta_planta.write_text(texto, encoding="utf-8")
        ruta_nodeset.parent.mkdir(parents=True, exist_ok=True)
        modelo.generar_fichero(planta, ruta_nodeset)
        r.update(planta=cuerpo["planta"], nodeset=cuerpo["nodeset"])
        return r

    def _importar(ruta):
        drv = _driver()
        imp = modelo.importar_nodeset(str(ruta), drv.validar_direccion)
        return {"valida": not imp.errores, "errores": imp.errores, "uri": imp.uri,
                "metodos": [{"ruta": r, "nombre": n} for r, n in imp.metodos],
                "tabla": modelo.tabla_importacion(imp, drv)}

    @app.post("/api/modelo/importar")
    async def importar(cuerpo: dict = Body(...)):
        try:
            ruta = ruta_segura(carpeta, cuerpo.get("ruta"), (".xml",))
        except RutaNoValida as e:
            return error(str(e))
        if not ruta.is_file():
            return error(f"no existe {cuerpo.get('ruta')}", 404)
        r = _importar(ruta)
        r["nodeset"] = cuerpo["ruta"]
        return r

    @app.post("/api/modelo/subir")
    async def subir(fichero: UploadFile = File(...), sobrescribir: bool = Form(False)):
        try:
            nombre = sanear_nombre(fichero.filename)
            if not nombre.lower().endswith(".xml"):
                raise RutaNoValida(f"'{fichero.filename}': el NodeSet debe ser .xml")
            ruta = ruta_segura(carpeta, nombre, (".xml",))
        except RutaNoValida as e:
            return error(str(e))
        if ruta.exists() and not sobrescribir:
            return error(f"ya existe {nombre} (marque sobrescribir)", 409)
        contenido = await fichero.read()
        if len(contenido) > 20 * 1024 * 1024:
            return error("fichero demasiado grande (maximo 20 MB)", 413)
        ruta.write_bytes(contenido)
        try:
            r = _importar(ruta)
        except Exception as e:                     # XML mal formado, etc.
            ruta.unlink(missing_ok=True)
            return error(f"{nombre}: no es un NodeSet2 valido ({e})")
        r["nodeset"] = nombre
        return r

    # --- servidor ---
    @app.get("/api/servidor/estado")
    async def estado():
        return gestor.estado()

    def _arrancar():
        if not ruta_config.exists():
            return error("no hay config.yaml: guarde antes la configuracion")
        try:
            configuracion.configuracion_efectiva(str(ruta_config))
        except configuracion.ErrorConfig as e:
            return error(e.errores)
        try:
            pid = gestor.arrancar("config.yaml")
        except RuntimeError as e:
            return error(str(e), 409)
        return {"arrancado": True, "pid": pid}

    @app.post("/api/servidor/arrancar")
    async def arrancar():
        return _arrancar()

    @app.post("/api/servidor/detener")
    async def detener():
        return await asyncio.to_thread(gestor.detener)

    @app.post("/api/servidor/reiniciar")
    async def reiniciar():
        parada = await asyncio.to_thread(gestor.detener)
        r = _arrancar()
        if isinstance(r, dict):
            r["parada"] = parada
        return r

    @app.get("/api/servidor/log")
    async def log(desde: int = 0):
        lineas, siguiente = gestor.log(desde)
        return {"lineas": lineas, "siguiente": siguiente}

    # --- pagina ---
    if ESTATICOS.is_dir():
        app.mount("/estaticos", StaticFiles(directory=ESTATICOS), name="estaticos")

        @app.get("/")
        async def inicio():
            return FileResponse(ESTATICOS / "index.html")

    return app


# ---------------------------------------------------------------------------
# Arranque
# ---------------------------------------------------------------------------
def es_local(host):
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def main(argv=None):
    ap = argparse.ArgumentParser(description="Interfaz web local del servidor OPC UA")
    ap.add_argument("--carpeta", default=".", help="Carpeta de trabajo (config.yaml, modelos, logs)")
    ap.add_argument("--host", default="127.0.0.1", help="Direccion de escucha (por defecto, solo local)")
    ap.add_argument("--puerto", type=int, default=8080)
    ap.add_argument("--usuario", metavar="NOMBRE:CLAVE",
                    help="Obligatorio si --host no es local (o INTERFAZ_USUARIO e INTERFAZ_CLAVE)")
    ap.add_argument("--tls-cert", help="Certificado TLS (PEM) para servir por HTTPS")
    ap.add_argument("--tls-key", help="Clave privada TLS (PEM)")
    a = ap.parse_args(argv)

    usuario = clave = None
    if a.usuario:
        if ":" not in a.usuario:
            ap.error("--usuario: use nombre:clave")
        usuario, clave = a.usuario.split(":", 1)
    elif os.getenv("INTERFAZ_USUARIO") and os.getenv("INTERFAZ_CLAVE"):
        usuario, clave = os.getenv("INTERFAZ_USUARIO"), os.getenv("INTERFAZ_CLAVE")
    local = es_local(a.host)
    if not local and not usuario:
        print(f"La interfaz no se expone en {a.host} sin autenticacion: indique --usuario nombre:clave "
              f"(o INTERFAZ_USUARIO e INTERFAZ_CLAVE).", file=sys.stderr)
        return 2
    if bool(a.tls_cert) != bool(a.tls_key):
        ap.error("--tls-cert y --tls-key van juntos")
    if not local and not a.tls_cert:
        print("AVISO: interfaz en red sin TLS: la contrasena y la sesion viajan sin cifrar. "
              "Use --tls-cert/--tls-key o un tunel SSH.", file=sys.stderr)
    carpeta = Path(a.carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    hosts = {"127.0.0.1", "localhost", "::1"} if local else None
    app = crear_app(carpeta, usuario, hash_clave(clave) if usuario else None, hosts)
    esquema = "https" if a.tls_cert else "http"
    print(f"Interfaz en {esquema}://{a.host}:{a.puerto}/ (carpeta de trabajo {carpeta.resolve()})")
    uvicorn.run(app, host=a.host, port=a.puerto, log_level="warning",
                ssl_certfile=a.tls_cert, ssl_keyfile=a.tls_key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
