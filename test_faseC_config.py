"""Fase C, paso 3: configuracion YAML del servidor (--config).
Validacion sin PLC y arranque del servidor solo con --config contra el PLC emulado
(puerto 102): celula sin seguridad y variador con seguridad estricta y planta."""
import os; os.makedirs("logs", exist_ok=True)   # logs del servidor
import asyncio, csv, ctypes, importlib, logging, socket, struct, subprocess, sys, time
from pathlib import Path
import snap7
import yaml
from snap7.type import SrvArea
from asyncua import Client, ua
from asyncua.crypto.security_policies import SecurityPolicyBasic256Sha256
from asyncua.crypto.cert_gen import setup_self_signed_certificate
from cryptography.x509.oid import ExtendedKeyUsageOID

import configuracion as C
from seguridad import hash_clave, verificar_clave
logging.disable(logging.WARNING)

res = []
def check(nombre, ok): res.append((nombre, bool(ok)))
SERVIDOR = "serverOPCUA_MetodoTFGV5_asyncua.py"
DIR = Path("prueba_faseC"); DIR.mkdir(exist_ok=True)
servidor = importlib.import_module(SERVIDOR[:-3])
parser = servidor.crear_parser()


def escribir(nombre, datos):
    ruta = DIR / nombre
    ruta.write_text(yaml.safe_dump(datos, sort_keys=False), encoding="utf-8")
    return str(ruta)


def errores(ruta, *cli):
    try:
        C.configuracion_efectiva(ruta, parser.parse_args(list(cli)) if cli else None); return []
    except C.ErrorConfig as e:
        return e.errores


# --- 3a: toda opcion de la linea de ordenes tiene su clave ---
dests = {a.dest for a in parser._actions}
check("3a cada opcion del servidor tiene clave en el YAML (salvo control del proceso)",
      dests - C.OPCIONES_PROCESO == set(C.OPCIONES_CLI) and "simulacion" not in dests)

# --- 3b: ejemplos del repositorio ---
cfg, av = C.configuracion_efectiva("config_celula.yaml")
check("3b config_celula.yaml valida, sin avisos, rutas resueltas desde su carpeta",
      not av and cfg["modelo"]["nodeset"] == os.path.abspath("modelo_celula.xml")
      and cfg["driver"]["parametros"]["max_hueco"] == 8)
cfg, av = C.configuracion_efectiva("config_ejemplo_estricto.yaml")
check("3b config_ejemplo_estricto.yaml valida (planta, estricto, clave_hash)",
      not av and cfg["modelo"]["origen"] == "planta" and cfg["seguridad"]["modo"] == "estricto")

# --- 3c: errores listados juntos ---
e = errores(escribir("mala.yaml", {"servidor": {"endpoint": "http://x", "puerto": 1}, "medidas": {},
                                   "driver": {"nombre": "snap7", "parametros": {"rack": 9}},
                                   "modelo": {"nodeset": "no_existe.xml"}, "seguridad": {"modo": "total"},
                                   "medida": {"periodo_ms": 0}}))
esperados = ["bloque 'medidas' desconocido", "servidor.puerto: clave desconocida"]
check("3c bloque y clave desconocidos", all(any(x in y for y in e) for x in esperados))
e = errores(escribir("mala2.yaml", {"servidor": {"endpoint": "http://x"},
                                    "driver": {"nombre": "snap7", "parametros": {"rack": 9}},
                                    "modelo": {"nodeset": "no_existe.xml"}, "seguridad": {"modo": "total"},
                                    "medida": {"periodo_ms": 0}}))
esperados = ["servidor.endpoint", "rack=9", "modelo.nodeset: no existe", "seguridad.modo: 'total'",
             "medida.periodo_ms"]
check("3c varios errores de valor a la vez", len(e) == 5 and all(any(x in y for y in e) for x in esperados))
check("3c sin modelo ni -m: error claro", any("falta la ruta del NodeSet" in x for x in errores(None, "--rack", "0")))
check("3c planta sin fichero de planta", any("modelo.planta: falta" in x for x in errores(
    escribir("p.yaml", {"modelo": {"origen": "planta", "nodeset": "x.xml"}}))))

# --- 3d: contrasenas ---
h = hash_clave("secreta", iteraciones=1000)
check("3d verificar_clave: correcta e incorrecta", verificar_clave("secreta", h) and not verificar_clave("otra", h))
base = {"modelo": {"nodeset": "../modelo_celula.xml"}}
_, av = C.configuracion_efectiva(escribir("claro.yaml", {**base, "seguridad": {
    "usuarios": [{"nombre": "ana", "clave": "abc"}]}}))
check("3d contrasena en claro en el fichero: aviso", len(av) == 1 and "en claro" in av[0] and "ana" in av[0])
_, av = C.configuracion_efectiva(escribir("hash.yaml", {**base, "seguridad": {
    "usuarios": [{"nombre": "ana", "clave_hash": h}]}}))
check("3d clave_hash: sin aviso", not av)
check("3d clave y clave_hash a la vez: error", any("una sola" in x for x in errores(escribir("dos.yaml", {
    **base, "seguridad": {"usuarios": [{"nombre": "a", "clave": "x", "clave_hash": h}]}}))))
check("3d clave_hash mal formado: error", any("formato" in x for x in errores(escribir("malh.yaml", {
    **base, "seguridad": {"usuarios": [{"nombre": "a", "clave_hash": "md5$abc"}]}}))))
_, av = C.configuracion_efectiva(None, parser.parse_args(["-m", "modelo_celula.xml", "--usuario", "a:b"]))
check("3d --usuario en la linea de ordenes: sin aviso (no queda guardado)", not av)

# --- 3e/3f: prioridad de la linea de ordenes ---
cel = escribir("celula.yaml", {"driver": {"parametros": {"ip": "192.168.0.1", "timeout_ms": 1000}},
                               "modelo": {"nodeset": "../modelo_celula.xml"}, "medida": {"periodo_ms": 100}})
cfg, _ = C.configuracion_efectiva(cel, parser.parse_args(["--config", cel, "--plc-ip", "10.0.0.9",
                                                          "--periodo-ms", "20"]))
check("3e la linea de ordenes prevalece; lo demas sale del fichero",
      cfg["driver"]["parametros"]["ip"] == "10.0.0.9" and cfg["medida"]["periodo_ms"] == 20
      and cfg["driver"]["parametros"]["timeout_ms"] == 1000)
cfg, _ = C.configuracion_efectiva(cel, parser.parse_args(["--driver-param", "rack=2", "--driver-param",
                                                          "timeout_ms=300"]))
check("3e --driver-param convertido por el driver", cfg["driver"]["parametros"]["rack"] == 2
      and cfg["driver"]["parametros"]["timeout_ms"] == 300)
check("3f --plc-ip y --driver-param ip distintos: error",
      any("se contradicen" in x for x in errores(cel, "--plc-ip", "1.1.1.1", "--driver-param", "ip=2.2.2.2")))
check("3f --plc-ip y --driver-param ip iguales: valido",
      not errores(cel, "--plc-ip", "1.1.1.1", "--driver-param", "ip=1.1.1.1"))
check("3f --driver-param desconocido: error del driver",
      any("'puerto' no existe" in x for x in errores(cel, "--driver-param", "puerto=1")))
check("3f -m prevalece sobre origen planta",
      C.configuracion_efectiva("config_ejemplo_estricto.yaml", parser.parse_args(
          ["-m", "modelo_celula.xml"]))[0]["modelo"]["origen"] == "nodeset")

# --- PLC emulado ---
srv = snap7.server.Server(); areas = {}
for a in (SrvArea.PE, SrvArea.PA, SrvArea.MK):
    b = (ctypes.c_ubyte * 16)(); srv.register_area(a, 0, b); areas[a] = b
bdb = (ctypes.c_ubyte * 16)(); srv.register_area(SrvArea.DB, 10, bdb)
srv.start_to("127.0.0.1", 102)


def lanzar(argumentos, log):
    return subprocess.Popen([sys.executable, SERVIDOR, *argumentos], stdout=open(log, "w"),
                            stderr=subprocess.STDOUT)


async def conectar(cliente_fn):
    for _ in range(60):
        c = cliente_fn()
        try:
            await c.connect(); return c
        except Exception:
            await asyncio.sleep(0.25)
    raise RuntimeError("el servidor no responde")


async def cert_cliente():
    d = Path("cli"); d.mkdir(exist_ok=True)
    c, k = d / "c.der", d / "k.pem"
    await setup_self_signed_certificate(k, c, "urn:cliente:test", socket.gethostname(),
                                        [ExtendedKeyUsageOID.CLIENT_AUTH], {"commonName": "Cliente test"})
    # Lista de confianza (activa por defecto): el cliente de prueba es de confianza
    confianza = Path("pki") / "confianza"; confianza.mkdir(parents=True, exist_ok=True)
    (confianza / "cliente_test.der").write_bytes(c.read_bytes())
    return str(c), str(k)


async def main():
    # 3g: celula solo con --config (rutas relativas a la carpeta del fichero)
    cfg_a = escribir("config_a.yaml", {
        "driver": {"nombre": "snap7", "parametros": {"ip": "127.0.0.1", "timeout_ms": 500}},
        "modelo": {"origen": "nodeset", "nodeset": "../modelo_celula.xml"},
        "seguridad": {"modo": "none", "cert_dir": "../pki"}})
    p = lanzar(["--config", cfg_a], "logs/srv_faseC_3a.log")
    try:
        c = await conectar(lambda: Client("opc.tcp://127.0.0.1:4840"))
        try:
            ns = await c.get_namespace_index("urn:miservidor:ledRojo")
            n = lambda s: c.get_node(f"ns={ns};s={s}")
            areas[SrvArea.PE][0] |= 0x20; await asyncio.sleep(1.2)
            leido = await n("CilindroA.Estado.Expandido").read_value()
            await n("LedRojo.Value").write_value(ua.DataValue(ua.Variant(True, ua.VariantType.Boolean)))
            await asyncio.sleep(0.3)
            check("3g solo --config (celula): lee I0.5 y escribe el LED en Q0.3",
                  leido is True and areas[SrvArea.PA][0] & 0x08)
        finally:
            await c.disconnect()
    finally:
        p.terminate(); p.wait()
    log = open("logs/srv_faseC_3a.log", encoding="utf-8", errors="replace").read()
    check("3g log: configuracion cargada y conexion al PLC de la configuracion",
          "Configuracion cargada de" in log and "Conectado al PLC en 127.0.0.1" in log)

    # 3h: estricto + modelo generado desde la planta + CSV de medida
    cfg_b = escribir("config_b.yaml", {
        "driver": {"nombre": "snap7", "parametros": {"ip": "127.0.0.1", "timeout_ms": 500}},
        "modelo": {"origen": "planta", "planta": "../planta_ejemplo_variador.yaml",
                   "nodeset": "modelo_variador_faseC.xml"},
        "seguridad": {"modo": "estricto", "cert_dir": "../pki",
                      "usuarios": [{"nombre": "rafa", "clave_hash": hash_clave("clave123")}]},
        "medida": {"periodo_ms": 50, "log_csv": "medidas/ciclos.csv"}})
    cc, ck = await cert_cliente()
    p = lanzar(["--config", cfg_b], "logs/srv_faseC_3b.log")
    try:
        async def cliente_seguro(usuario=None, clave=None):
            c = Client("opc.tcp://127.0.0.1:4840")
            c.application_uri = "urn:cliente:test"; await c.set_security(SecurityPolicyBasic256Sha256, cc, ck, server_certificate="pki/servidor_cert.der",
                                 mode=ua.MessageSecurityMode.SignAndEncrypt)
            if usuario:
                c.set_user(usuario); c.set_password(clave)
            return c
        for _ in range(60):
            try:
                c = await cliente_seguro("rafa", "clave123"); await c.connect(); break
            except Exception:
                await asyncio.sleep(0.25)
        try:
            ns = await c.get_namespace_index("urn:ujaen:planta:transporte")
            n = lambda s: c.get_node(f"ns={ns};s=Transportador1.{s}")
            for i, x in enumerate(struct.pack(">f", 12.5)): bdb[4 + i] = x
            await asyncio.sleep(1.2)
            frec = await n("Estado.Frecuencia").read_value()
            await n("Control.Consigna").write_value(ua.DataValue(ua.Variant(321, ua.VariantType.Int16)))
            await asyncio.sleep(0.3)
            check("3h estricto + planta: usuario con clave_hash lee REAL y escribe INT",
                  frec == 12.5 and struct.unpack(">h", bytes(bdb[0:2]))[0] == 321)
        finally:
            await c.disconnect()
        try:
            c = await cliente_seguro(); await c.connect(); await c.disconnect(); anon = True
        except Exception:
            anon = False
        try:
            c = await cliente_seguro("rafa", "mala"); await c.connect(); await c.disconnect(); mala = True
        except Exception:
            mala = False
        check("3h estricto: anonimo y contrasena incorrecta rechazados", not anon and not mala)
    finally:
        p.terminate(); p.wait()
    # El contenido del CSV se comprueba en test_faseC_parada: terminate() no deja volcarlo
    check("3h NodeSet generado al arrancar y CSV de ciclos creado en la ruta de la configuracion",
          (DIR / "modelo_variador_faseC.xml").exists() and (DIR / "medidas" / "ciclos.csv").exists())

    # 3i: la linea de ordenes prevalece en el servidor
    cfg_c = escribir("config_c.yaml", {"driver": {"parametros": {"ip": "192.168.250.250"}},
                                       "modelo": {"nodeset": "../modelo_celula.xml"}})
    p = lanzar(["--config", cfg_c, "--plc-ip", "127.0.0.1", "--timeout-ms", "500"], "logs/srv_faseC_3c.log")
    try:
        for _ in range(60):
            time.sleep(0.25)
            log = open("logs/srv_faseC_3c.log", encoding="utf-8", errors="replace").read()
            if "Conectado al PLC" in log:
                break
    finally:
        p.terminate(); p.wait()
    check("3i servidor: --plc-ip prevalece sobre el fichero", "Conectado al PLC en 127.0.0.1" in log)

    # 3j: configuracion invalida: el servidor no arranca y lista los errores
    r = subprocess.run([sys.executable, SERVIDOR, "--config", str(DIR / "mala2.yaml")],
                       capture_output=True, text=True, timeout=60)
    check("3j configuracion invalida: no arranca y lista los errores",
          r.returncode == 2 and "Configuracion invalida" in r.stderr and "seguridad.modo" in r.stderr)


try:
    asyncio.run(main())
finally:
    srv.stop()

print("=== RESUMEN ===")
for nombre, ok in res:
    print(f"  [{'OK   ' if ok else 'FALLO'}] {nombre}")
sys.exit(0 if all(ok for _, ok in res) else 1)
