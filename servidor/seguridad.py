"""
Nivel de seguridad del servidor OPC UA (paso 6).

Configura, segun el modo elegido:
  - El certificado de aplicacion X.509 autofirmado (se genera la primera vez
    y se reutiliza despues).
  - Las politicas de seguridad y modos de mensaje.
  - La autenticacion de usuario (anonima y/o usuario-contrasena).

Modos de seguridad:
  none      solo SecurityPolicy None, sin cifrado. Para pruebas en red aislada.
  firmar    ofrece None y las politicas seguras (Basic256Sha256,
            Aes128-Sha256-RsaOaep y Aes256-Sha256-RsaPss) en modos Sign y
            SignAndEncrypt, de modo que el mismo servidor admite clientes con y
            sin cifrado. Util para medir el coste de la seguridad.
  estricto  ofrece solo las politicas seguras, sin None.

Lista de confianza (modos firmar y estricto, activada por defecto): el
certificado de cada cliente se valida contra <cert_dir>/confianza. Uno no
confiable se rechaza y se guarda en <cert_dir>/rechazados; el administrador lo
acepta moviendolo a confianza (se relee en cada conexion, sin reiniciar).

La autenticacion de usuario se activa pasando credenciales. En modo estricto
con credenciales, el acceso anonimo queda deshabilitado.
"""

import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from asyncua import ua
from asyncua.common.utils import ServiceError
from asyncua.crypto.cert_gen import setup_self_signed_certificate
from asyncua.crypto.truststore import TrustStore
from asyncua.crypto.validator import CertificateValidator, CertificateValidatorOptions
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from asyncua.crypto.permission_rules import (
    User, UserRole, PermissionRuleset, USER_TYPES, ADMIN_TYPES)
from asyncua.server.user_managers import UserManager
from cryptography.x509.oid import ExtendedKeyUsageOID

logger = logging.getLogger("OPCUA-Server")

SEGURAS = [
    ua.SecurityPolicyType.Basic256Sha256_Sign,
    ua.SecurityPolicyType.Basic256Sha256_SignAndEncrypt,
    ua.SecurityPolicyType.Aes128Sha256RsaOaep_Sign,
    ua.SecurityPolicyType.Aes128Sha256RsaOaep_SignAndEncrypt,
    ua.SecurityPolicyType.Aes256Sha256RsaPss_Sign,
    ua.SecurityPolicyType.Aes256Sha256RsaPss_SignAndEncrypt,
]
POLITICAS = {
    "none": [ua.SecurityPolicyType.NoSecurity],
    "firmar": [ua.SecurityPolicyType.NoSecurity] + SEGURAS,
    "estricto": list(SEGURAS),
}

# ---------------------------------------------------------------------------
# Lista de confianza de certificados de cliente
# ---------------------------------------------------------------------------
DIR_CONFIANZA, DIR_RECHAZADOS = "confianza", "rechazados"
# Se comprueban vigencia, ApplicationUri y confianza. REVOKED queda activo, pero
# el TrustStore no carga CRL, asi que la revocacion no se detecta (limitacion
# conocida: para retirar un certificado se saca de confianza/). No se exige
# el KeyUsage completo de asyncua (incluye nonRepudiation), que rechazaria
# certificados validos de algunos clientes.
OPCIONES_VALIDACION = (CertificateValidatorOptions.TIME_RANGE | CertificateValidatorOptions.URI
                       | CertificateValidatorOptions.TRUSTED | CertificateValidatorOptions.REVOKED
                       | CertificateValidatorOptions.PEER_CLIENT)
_NO_VALIDOS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVADOS = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
               *(f"LPT{i}" for i in range(1, 10))}


def huella(cert) -> str:
    """Huella SHA-1 del certificado (thumbprint OPC UA), en hexadecimal."""
    return cert.fingerprint(hashes.SHA1()).hex().upper()


def nombre_comun(cert) -> str:
    cn = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
    return str(cn[0].value) if cn else ""


def sanear_nombre(texto: str, maximo: int = 64) -> str:
    """Texto valido como nombre de fichero en Windows."""
    t = _NO_VALIDOS.sub("_", texto or "").strip()[:maximo].rstrip(". ")
    if not t:
        t = "sin_CN"
    if t.split(".")[0].upper() in _RESERVADOS:
        t = "_" + t
    return t


def nombre_rechazado(cert) -> str:
    """'<CN saneado> [<huella>].der': valido en Windows y unico por certificado."""
    return f"{sanear_nombre(nombre_comun(cert))} [{huella(cert)}].der"


def _nombre_estado(codigo) -> str:
    try:
        return ua.StatusCode(codigo).name
    except Exception:
        return hex(codigo)


# Motivo legible de cada rechazo. Solo el no confiable se acepta moviendo el
# fichero a confianza/; los demas exigen otro certificado en el cliente.
MOTIVOS = {
    "BadCertificateUntrusted": "no confiable",
    "BadCertificateUriInvalid": "URI no válido",
    "BadCertificateTimeInvalid": "caducado",
    "BadCertificateRevoked": "revocado",
}
ACEPTABLE = "BadCertificateUntrusted"
FICHERO_MOTIVOS = "motivos.json"     # en rechazados/: {huella: codigo de estado}


def motivo(codigo: str) -> str:
    return MOTIVOS.get(codigo, f"no válido ({codigo})")


def _leer_motivos(carpeta: Path) -> dict:
    try:
        d = json.loads((carpeta / FICHERO_MOTIVOS).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


class ValidadorConfianza:
    """
    Valida el certificado del cliente al crear la sesion. El almacen se relee
    en cada validacion: mover un certificado de rechazados a confianza lo
    acepta sin reiniciar el servidor.
    """

    def __init__(self, cert_dir):
        self.confianza = Path(cert_dir) / DIR_CONFIANZA
        self.rechazados = Path(cert_dir) / DIR_RECHAZADOS
        self.confianza.mkdir(parents=True, exist_ok=True)
        self.rechazados.mkdir(parents=True, exist_ok=True)

    async def __call__(self, cert, app_description):
        almacen = TrustStore([self.confianza], [])
        await almacen.load()
        try:
            await CertificateValidator(OPCIONES_VALIDACION, almacen).validate(cert, app_description)
        except ServiceError as e:
            destino = self.rechazados / nombre_rechazado(cert)
            if not destino.exists():
                destino.write_bytes(cert.public_bytes(serialization.Encoding.DER))
            codigo = _nombre_estado(e.code)
            motivos = _leer_motivos(self.rechazados)
            motivos[huella(cert)] = codigo
            (self.rechazados / FICHERO_MOTIVOS).write_text(json.dumps(motivos, indent=1), encoding="utf-8")
            consejo = (f"para aceptarlo, muévalo a {self.confianza}" if codigo == ACEPTABLE
                       else "moverlo a confianza no basta: el cliente necesita otro certificado")
            logger.warning(f"Certificado de cliente rechazado, motivo: {motivo(codigo)} ({codigo}): "
                           f"'{nombre_comun(cert)}' [{huella(cert)}], guardado en {destino}; {consejo}")
            raise


def listar_rechazados(cert_dir) -> list:
    """
    [{nombre, huella, fecha, fichero, codigo, motivo, aceptable}] de los
    certificados en <cert_dir>/rechazados. aceptable: se acepta moviendolo a
    confianza/ (solo los no confiables).
    """
    lista = []
    carpeta = Path(cert_dir) / DIR_RECHAZADOS
    motivos = _leer_motivos(carpeta)
    for f in sorted(carpeta.glob("*.der")):
        try:
            cert = x509.load_der_x509_certificate(f.read_bytes())
            nombre, h = nombre_comun(cert), huella(cert)
        except (OSError, ValueError):
            nombre, h = f.stem, ""
        fecha = datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).astimezone()
        codigo = motivos.get(h, "")
        lista.append({"nombre": nombre, "huella": h, "fichero": f.name,
                      "fecha": fecha.strftime("%Y-%m-%d %H:%M:%S"),
                      "codigo": codigo, "motivo": motivo(codigo) if codigo else "desconocido",
                      "aceptable": codigo == ACEPTABLE})
    return lista


class RulesetControl(PermissionRuleset):
    """
    Permisos por rol:
      Anonimo  lectura, navegacion y suscripcion (monitorizacion). La escritura
               y la llamada a metodos se permiten solo si anonimo_escribe=True,
               que es el caso del modo 'none' (sin seguridad, comportamiento
               heredado); con cifrado, el anonimo es de solo lectura.
      Usuario  ademas, escritura y llamada a metodos (control).
      Admin    ademas, cambios en el espacio de direcciones.
    """

    def __init__(self, anonimo_escribe: bool):
        user_ids = set(map(ua.NodeId, USER_TYPES))
        admin_ids = set(map(ua.NodeId, ADMIN_TYPES))
        escritura = {
            ua.NodeId(ua.ObjectIds.WriteRequest_Encoding_DefaultBinary),
            ua.NodeId(ua.ObjectIds.CallRequest_Encoding_DefaultBinary),
        }
        anon = user_ids if anonimo_escribe else (user_ids - escritura)
        self._permiso = {
            UserRole.Admin: user_ids | admin_ids,
            UserRole.User: user_ids,
            UserRole.Anonymous: anon,
        }

    def check_validity(self, user, action_type_id, body):
        return action_type_id in self._permiso.get(user.role, set())


PREFIJO_HASH = "pbkdf2_sha256"
ITERACIONES_HASH = 600_000


class ClaveHash(str):
    """Clave guardada como hash PBKDF2 ('pbkdf2_sha256$iter$sal$hash', base64)."""


def hash_clave(clave: str, iteraciones: int = ITERACIONES_HASH) -> str:
    """Texto para clave_hash. Para el gestor de usuarios, envolver en ClaveHash."""
    sal = os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", clave.encode("utf-8"), sal, iteraciones)
    return (f"{PREFIJO_HASH}${iteraciones}${base64.b64encode(sal).decode()}"
            f"${base64.b64encode(h).decode()}")


def es_hash_valido(texto) -> bool:
    try:
        prefijo, it, sal, h = str(texto).split("$")
        return prefijo == PREFIJO_HASH and int(it) > 0 and base64.b64decode(sal) and base64.b64decode(h)
    except (ValueError, TypeError, binascii.Error):
        return False


def verificar_clave(clave, guardado) -> bool:
    """Compara una clave con su hash PBKDF2 en tiempo constante."""
    if clave is None or not es_hash_valido(guardado):
        return False
    _, it, sal, h = str(guardado).split("$")
    calc = hashlib.pbkdf2_hmac("sha256", str(clave).encode("utf-8"), base64.b64decode(sal), int(it))
    return hmac.compare_digest(calc, base64.b64decode(h))


def comprobar_clave(guardada, clave) -> bool:
    """guardada: ClaveHash o texto en claro (este ultimo, solo con aviso)."""
    if isinstance(guardada, ClaveHash):
        return verificar_clave(clave, guardada)
    return clave is not None and hmac.compare_digest(str(guardada).encode(), str(clave).encode())


class GestorUsuarios(UserManager):
    """Autenticacion de usuario: acceso anonimo y/o usuario-contrasena."""

    def __init__(self, usuarios: dict, permitir_anonimo: bool):
        self.usuarios = usuarios or {}
        self.permitir_anonimo = permitir_anonimo

    def get_user(self, iserver, username=None, password=None, certificate=None):
        # Sesion anonima: username y password vienen vacios.
        if not username and password is None:
            return User(role=UserRole.Anonymous) if self.permitir_anonimo else None
        # Usuario y contrasena.
        if username in self.usuarios and comprobar_clave(self.usuarios[username], password):
            return User(role=UserRole.User, name=username)
        logger.warning(f"Autenticacion rechazada para el usuario '{username}'")
        return None


def crear_gestor(usuarios: dict, modo: str) -> GestorUsuarios:
    """El acceso anonimo se deshabilita solo en modo estricto con credenciales."""
    permitir_anonimo = not (modo == "estricto" and usuarios)
    return GestorUsuarios(usuarios, permitir_anonimo)


def _fijar_tokens(server, usuarios: dict, permitir_anonimo: bool):
    tokens = []
    if permitir_anonimo:
        tokens.append(ua.AnonymousIdentityToken)
    if usuarios:
        tokens.append(ua.UserNameIdentityToken)
    if not tokens:                       # salvaguarda: nunca dejar el servidor sin tokens
        tokens.append(ua.AnonymousIdentityToken)
    try:
        server.set_identity_tokens(tokens)
    except AttributeError:               # asyncua antiguo
        ids = (["Anonymous"] if permitir_anonimo else []) + (["Username"] if usuarios else [])
        server.set_security_IDs(ids or ["Anonymous"])


async def aplicar_seguridad(server, modo, cert_dir, usuarios, app_uri, host_name, lista_confianza=True):
    """
    Configura la seguridad del servidor ya inicializado. Devuelve la ruta del
    certificado generado, o None en modo 'none'. La lista de confianza solo se
    aplica en los modos firmar y estricto.
    """
    await server.set_application_uri(app_uri)
    server.set_security_policy(POLITICAS[modo], RulesetControl(anonimo_escribe=(modo == "none")))
    _fijar_tokens(server, usuarios, permitir_anonimo=not (modo == "estricto" and usuarios))

    if modo == "none":
        logger.warning("Seguridad: solo SecurityPolicy None (sin cifrado). "
                       "Despliegue unicamente en red aislada.")
        return None

    cert_dir = Path(cert_dir)
    cert_dir.mkdir(parents=True, exist_ok=True)
    cert_file = cert_dir / "servidor_cert.der"
    key_file = cert_dir / "servidor_key.pem"
    await setup_self_signed_certificate(
        key_file, cert_file, app_uri, host_name,
        [ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH],
        {
            "countryName": "ES",
            "stateOrProvinceName": "Jaen",
            "organizationName": "Universidad de Jaen",
            "commonName": "Servidor OPC UA desacoplado",
        },
    )
    await server.load_certificate(str(cert_file))
    await server.load_private_key(str(key_file))

    politicas = ", ".join(p.name for p in POLITICAS[modo])
    anonima_off = (modo == "estricto" and usuarios)
    auth = "usuario-contrasena" + (" (anonima deshabilitada)" if anonima_off else "") if usuarios else "anonima"
    logger.info(f"Seguridad: modo '{modo}'. Politicas: {politicas}")
    logger.info(f"Autenticacion: {auth}")
    logger.info(f"Certificado del servidor: {cert_file}")
    if lista_confianza:
        v = ValidadorConfianza(cert_dir)
        server.set_certificate_validator(v)
        logger.info(f"Lista de confianza activa: confianza en {v.confianza}, rechazados en {v.rechazados}")
    else:
        logger.warning("Lista de confianza desactivada: se acepta cualquier certificado de cliente")
    return cert_file


def parse_usuarios(lista) -> dict:
    """Convierte ['nombre:clave', ...] en un diccionario {nombre: clave}."""
    usuarios = {}
    for item in lista or []:
        if ":" not in item:
            raise ValueError(f"Credencial mal formada (use nombre:clave): {item}")
        nombre, clave = item.split(":", 1)
        usuarios[nombre] = clave
    return usuarios
