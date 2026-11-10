"""
Interoperabilidad 2: lista de confianza de certificados de cliente.
  a  Nombres de fichero de rechazados: CN saneado (valido en Windows) + huella.
  b  firmar (lista activa por defecto): un cliente no confiable es rechazado
     (BadCertificateUntrusted) y su certificado se guarda en pki/rechazados
     con el nombre '<CN saneado> [<huella>].der'.
  c  Dos certificados con el mismo CN dan dos ficheros distintos (huella).
  d  Moviendo el fichero a pki/confianza, el cliente conecta sin reiniciar.
  e  Un cliente sin cifrado (None, sin certificado) sigue conectando en firmar.
  f  --no-lista-confianza: el cliente no confiable conecta.
  g  config.yaml: lista_confianza false desactiva la lista; un valor no
     booleano impide arrancar y lo explica.
  h  Interfaz: casilla en la pagina y lista de solo lectura de rechazados
     (nombre, huella y fecha) en /api/certificados/rechazados.
  i  Motivo de cada rechazo (no confiable, URI no valido, caducado, revocado)
     en el log y en la API; solo el no confiable figura como aceptable
     moviendolo a confianza/. URI no valido, de extremo a extremo; caducado
     y revocado, contra el validador.
  j  La tabla de rechazados sale de los .der presentes (entradas de
     motivos.json sin fichero se ignoran); la guia e INTEROPERABILIDAD.md
     no afirman comprobar la revocacion y la documentan como limitacion.

Ejecutar desde la raiz del proyecto:  py pruebas/test_interop_confianza.py
"""
import _rutas  # noqa: F401  (servidor/ en la ruta; carpeta de trabajo tmp/)
import asyncio
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from asyncua import Client, ua
from asyncua.common.utils import ServiceError
from asyncua.crypto.security_policies import SecurityPolicyBasic256Sha256
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

import interfaz
import seguridad
from util_interop import (CARPETA, EP, PKI, PLCEmulado, check, cert_cliente, cliente_seguro,
                          esperar_servidor, lanzar, parar, resumen)

CN_RARO = 'Cliente: "interop" <a/b>|?*'
APP_URI = "urn:uja:test:interop-confianza"
NO_VALIDOS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVADOS = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
SAE = ua.MessageSecurityMode.SignAndEncrypt


def valido_windows(nombre):
    base = nombre.split(".")[0].upper()
    return (not NO_VALIDOS.search(nombre) and not nombre.endswith((".", " ")) and base not in RESERVADOS
            and len(nombre) < 200)


async def conectar(cert, app_uri=APP_URI):
    """Devuelve 'ok' o el nombre del error de la conexion cifrada."""
    c = await cliente_seguro(SecurityPolicyBasic256Sha256, SAE, cert, app_uri)
    try:
        await c.connect()
    except Exception as e:
        try:
            await c.disconnect()
        except Exception:
            pass
        return type(e).__name__
    v = await c.nodes.server_state.read_value()
    await c.disconnect()
    return "ok" if v == ua.ServerState.Running else "estado"


def cargar(ruta):
    return x509.load_der_x509_certificate(Path(ruta).read_bytes())


def cert_caducado():
    """Certificado autofirmado que caduco ayer."""
    clave = ec.generate_private_key(ec.SECP256R1())
    nombre = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "Cliente caducado")])
    ahora = datetime.now(timezone.utc)
    return (x509.CertificateBuilder().subject_name(nombre).issuer_name(nombre).public_key(clave.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(ahora - timedelta(days=30))
            .not_valid_after(ahora - timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.UniformResourceIdentifier(APP_URI)]), False)
            .sign(clave, hashes.SHA256()))


async def motivos_validador():
    """i: caducado (validador real) y revocado (validador simulado), en una carpeta aparte."""
    d = CARPETA / "pki_motivos"
    shutil.rmtree(d, ignore_errors=True)
    v = seguridad.ValidadorConfianza(d)
    desc = ua.ApplicationDescription(ApplicationUri=APP_URI)
    codigos = []
    for cert, simular in ((cert_caducado(), None), (cargar(cert_cliente("revocado", "Cliente revocado", APP_URI)[0]),
                                                    ua.StatusCodes.BadCertificateRevoked)):
        original = seguridad.CertificateValidator
        if simular:
            class Falso:
                def __init__(self, *a): pass
                async def validate(self, *a): raise ServiceError(simular)
            seguridad.CertificateValidator = Falso
        try:
            await v(cert, desc)
            codigos.append("ok")
        except ServiceError as e:
            codigos.append(ua.StatusCode(e.code).name)
        finally:
            seguridad.CertificateValidator = original
    lista = {c["nombre"]: c for c in seguridad.listar_rechazados(d)}
    return codigos, lista


async def main():
    # --- a: saneado de nombres ---
    casos = [CN_RARO, "CON", "nul.txt", "  ..  ", "", "a" * 300, "Lpt1", "Servidor. "]
    nombres = [seguridad.sanear_nombre(t) for t in casos]
    check("a CN saneados: validos en Windows", all(valido_windows(n) for n in nombres))
    check("a CN sin caracteres raros se conserva", seguridad.sanear_nombre("UaExpert@PC-1") == "UaExpert@PC-1")

    shutil.rmtree(PKI / "confianza", ignore_errors=True)
    shutil.rmtree(PKI / "rechazados", ignore_errors=True)
    raro = cert_cliente("raro", CN_RARO, APP_URI)
    otro = cert_cliente("raro2", CN_RARO, APP_URI)          # mismo CN, otra clave
    nombre_raro = seguridad.nombre_rechazado(cargar(raro[0]))
    check("a nombre del rechazado: '<CN saneado> [<huella>].der' valido en Windows",
          valido_windows(nombre_raro)
          and nombre_raro == f"{seguridad.sanear_nombre(CN_RARO)} [{seguridad.huella(cargar(raro[0]))}].der")

    seg = ["--seguridad", "firmar", "--cert-dir", str(PKI)]
    p = lanzar(seg, "logs/interop_confianza_b.log")
    try:
        await esperar_servidor()
        r = await conectar(raro)
        check("b cliente no confiable: rechazado (BadCertificateUntrusted)", r == "BadCertificateUntrusted")
        guardado = PKI / "rechazados" / nombre_raro
        check("b su certificado se guarda en pki/rechazados con CN saneado + huella", guardado.is_file()
              and guardado.read_bytes() == Path(raro[0]).read_bytes())

        await conectar(otro)
        ficheros = sorted(f.name for f in (PKI / "rechazados").glob("*.der"))
        check("c mismo CN, otro certificado: dos ficheros distintos", len(ficheros) == 2
              and seguridad.nombre_rechazado(cargar(otro[0])) in ficheros)

        shutil.move(str(guardado), str(PKI / "confianza" / nombre_raro))
        r = await conectar(raro)
        check("d movido a pki/confianza: conecta sin reiniciar", r == "ok")
        check("d el otro sigue rechazado", await conectar(otro) == "BadCertificateUntrusted")

        # --- i: URI no valido (ya confiable, pero el cliente declara otro ApplicationUri) ---
        check("i URI distinto del certificado: BadCertificateUriInvalid",
              await conectar(raro, "urn:uja:test:otro") == "BadCertificateUriInvalid")
        por_huella = {c["huella"]: c for c in seguridad.listar_rechazados(PKI)}
        h_raro, h_otro = seguridad.huella(cargar(raro[0])), seguridad.huella(cargar(otro[0]))
        check("i lista: no confiable aceptable; URI no valido no aceptable",
              por_huella.get(h_otro, {}).get("motivo") == "no confiable" and por_huella[h_otro]["aceptable"]
              and por_huella.get(h_raro, {}).get("motivo") == "URI no válido"
              and por_huella[h_raro]["aceptable"] is False)
        (PKI / "rechazados" / nombre_raro).unlink(missing_ok=True)      # para h: solo queda el otro

        c = Client(EP); await c.connect()
        estado = await c.nodes.server_state.read_value(); await c.disconnect()
        check("e cliente None sin certificado: conecta", estado == ua.ServerState.Running)

        # --- h: API de la interfaz (lista de solo lectura) ---
        ui = CARPETA / "ui"; ui.mkdir(parents=True, exist_ok=True)
        (ui / "config.yaml").write_text(yaml.safe_dump({"seguridad": {"modo": "firmar", "cert_dir": "../pki"}}),
                                        encoding="utf-8")
        app = interfaz.crear_app(ui)
        rutas = {r.path: r for r in app.routes if getattr(r, "path", "")}
        ruta = rutas.get("/api/certificados/rechazados")
        datos = await ruta.endpoint() if ruta else {}
        certs = datos.get("certificados", []) if isinstance(datos, dict) else []
        h = seguridad.huella(cargar(otro[0]))
        check("h API: lista los rechazados con nombre, huella y fecha", len(certs) == 1
              and certs[0]["nombre"] == CN_RARO and certs[0]["huella"] == h
              and re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", certs[0]["fecha"]))
        check("i API: motivo y si se acepta moviendolo", len(certs) == 1
              and certs[0]["motivo"] == "no confiable" and certs[0]["aceptable"] is True)
        check("h API de solo lectura (solo GET)", ruta is not None and ruta.methods == {"GET"}
              and not any(r.startswith("/api/certificados") and r != ruta.path for r in rutas))
    finally:
        parar(p)
    log = Path("logs/interop_confianza_b.log").read_text(encoding="utf-8", errors="replace")
    check("i log: motivo de cada rechazo y si basta moverlo a confianza",
          "motivo: no confiable (BadCertificateUntrusted)" in log and "(BadCertificateUriInvalid)" in log
          and "para aceptarlo, mu" in log and "moverlo a confianza no basta" in log)

    codigos, lista = await motivos_validador()
    check("i caducado y revocado: rechazados con su codigo",
          codigos == ["BadCertificateTimeInvalid", "BadCertificateRevoked"])
    check("i lista: caducado y revocado con su motivo, no aceptables",
          lista.get("Cliente caducado", {}).get("motivo") == "caducado"
          and lista.get("Cliente revocado", {}).get("motivo") == "revocado"
          and not any(c["aceptable"] for c in lista.values()))

    # --- j: la tabla sale de los .der presentes; entradas huerfanas de motivos.json se ignoran ---
    d = CARPETA / "pki_motivos" / "rechazados"
    motivos = json.loads((d / "motivos.json").read_text(encoding="utf-8"))
    motivos["0" * 40] = "BadCertificateUntrusted"                 # huerfana: sin fichero
    (d / "motivos.json").write_text(json.dumps(motivos), encoding="utf-8")
    next(d.glob("Cliente revocado*.der")).unlink()               # su entrada queda huerfana
    lista = seguridad.listar_rechazados(CARPETA / "pki_motivos")
    check("j tabla: solo los .der presentes; entradas de motivos.json sin fichero ignoradas",
          [c["nombre"] for c in lista] == ["Cliente caducado"] and lista[0]["motivo"] == "caducado")
    (d / "motivos.json").write_text("{no es json", encoding="utf-8")
    lista = seguridad.listar_rechazados(CARPETA / "pki_motivos")
    check("j motivos.json ilegible: la tabla sigue saliendo, motivo desconocido y no aceptable",
          len(lista) == 1 and lista[0]["motivo"] == "desconocido" and lista[0]["aceptable"] is False)

    p = lanzar(seg + ["--no-lista-confianza"], "logs/interop_confianza_f.log")
    try:
        await esperar_servidor()
        check("f --no-lista-confianza: el no confiable conecta", await conectar(otro) == "ok")
    finally:
        parar(p)

    base = {"driver": {"nombre": "snap7", "parametros": {"ip": "127.0.0.1", "timeout_ms": 500}},
            "modelo": {"origen": "nodeset", "nodeset": "../../modelos/modelo_celula.xml"},
            "servidor": {"endpoint": EP}}
    cfg = CARPETA / "config_confianza.yaml"
    cfg.write_text(yaml.safe_dump({**base, "seguridad": {"modo": "firmar", "cert_dir": "pki",
                                                          "lista_confianza": False}}), encoding="utf-8")
    p = subprocess.Popen([sys.executable, "../servidor/servidor_opcua.py", "--config", str(cfg)],
                         stdout=open("logs/interop_confianza_g.log", "w"), stderr=subprocess.STDOUT)
    try:
        await esperar_servidor()
        check("g config.yaml lista_confianza: false -> el no confiable conecta", await conectar(otro) == "ok")
    finally:
        parar(p)
    cfg.write_text(yaml.safe_dump({**base, "seguridad": {"modo": "firmar", "lista_confianza": "si"}}),
                   encoding="utf-8")
    r = subprocess.run([sys.executable, "../servidor/servidor_opcua.py", "--config", str(cfg)],
                       capture_output=True, text=True, timeout=60)
    check("g lista_confianza no booleano: no arranca y lo explica",
          r.returncode != 0 and "seguridad.lista_confianza" in (r.stdout + r.stderr))

    html = Path("../servidor/interfaz/index.html").read_text(encoding="utf-8")
    js = Path("../servidor/interfaz/app.js").read_text(encoding="utf-8")
    check("h pagina: casilla de lista de confianza y tabla de rechazados",
          'id="seguridad-lista_confianza"' in html and 'id="rechazados"' in html
          and "lista_confianza" in js and "/api/certificados/rechazados" in js)
    check("i pagina: columnas de motivo y aceptacion; solo los no confiables se aceptan moviendolos",
          "<th>Motivo</th>" in html and "no confiable" in html and "c.motivo" in js and "c.aceptable" in js)
    guia = Path("../docs/GUIA_DESPLIEGUE.md").read_text(encoding="utf-8")
    check("i guia: motivos y decision de no exigir nonRepudiation",
          "nonRepudiation" in guia and "motivos.json" in guia and "revocado" in guia)
    interop = Path("../docs/INTEROPERABILIDAD.md").read_text(encoding="utf-8")
    # Toda mencion de la revocacion debe ser la de la limitacion (no se comprueba).
    menciones = [" ".join(t[max(0, m.start() - 40):m.end()].split())
                 for t in (guia, interop) for m in re.finditer(r"revocaci", t)]
    check("j documentacion: la revocacion no se comprueba, como limitacion conocida",
          all("Limitación conocida: no se comprueba la revocación" in " ".join(t.split())
              and "pki/confianza" in t for t in (guia, interop))
          and all("no se comprueba la" in m or "listas de revocaci" in m for m in menciones))
    return resumen()


plc = PLCEmulado()
try:
    exito = asyncio.run(main())
finally:
    plc.parar()
sys.exit(0 if exito else 1)
