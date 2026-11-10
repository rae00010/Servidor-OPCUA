# Pruebas de interoperabilidad

Procedimiento para probar el servidor OPC UA desacoplado con clientes de
implementaciones distintas, sin la Compliance Test Tool (CTT) de la OPC
Foundation, a la que no se tiene acceso. Estas pruebas no sustituyen a la
certificación: por eso el servidor no declara ningún perfil
(`ServerProfileArray` vacío). Use `--perfil` solo cuando un perfil se haya
verificado con la CTT.

## 1. Clientes

| Cliente | Implementación | Papel |
|---|---|---|
| UaExpert (Unified Automation) | SDK C++ de Unified Automation | Cliente gráfico de uso habitual en la industria |
| ConsoleReferenceClient (OPC Foundation) | UA-.NETStandard (.NET) | Cliente de consola de referencia de la OPC Foundation |
| opcua-commander (node-opcua) | node-opcua (JavaScript, Node.js) | Tercer cliente, de otra implementación; de consola |

El tercer cliente previsto era Prosys OPC UA Browser (SDK Java de Prosys), pero
no está disponible. Se sustituye por opcua-commander, construido sobre
node-opcua: mantiene el criterio de usar una pila distinta de las otras dos
(C++, .NET) y de la del servidor.

Ninguno usa asyncua, la pila del servidor; así se evita que un mismo error en la
pila quede oculto en ambos extremos. Anote la versión exacta de cada cliente en
la tabla de resultados.

Independencia de los clientes: los tres clientes usan implementaciones
distintas entre sí y distintas de asyncua. UaExpert está basado en el SDK
cliente en C++ de Unified Automation sobre la pila ANSI C de la OPC Foundation,
según su ventana "About"; ConsoleReferenceClient, sobre la pila .NET Standard
de la OPC Foundation (UA-.NETStandard); y opcua-commander, sobre node-opcua
(JavaScript).

## 2. Preparación

1. PLC emulado, en una consola aparte (puerto 102; si no abre, puede estar
   ocupado por TIA Portal o PLCSIM):

   ```
   py plc_emulado.py
   ```

   Órdenes: `a+`/`a-` mueven el CilindroA (I0.5/I0.4), `p` pulsa el
   PulsadorVerde (I0.0), `s` muestra I, Q y M, `q` sale.

2. Servidor en modo `firmar` (None y las tres políticas seguras), con un
   usuario y la lista de confianza activa (por defecto):

   ```
   py serverOPCUA_MetodoTFGV5_asyncua.py -m modelo_celula.xml --plc-ip 127.0.0.1 --endpoint opc.tcp://0.0.0.0:4840 --seguridad firmar --usuario rafa:clave123
   ```

   Para las pruebas sin None, repita con `--seguridad estricto`. Si el cliente
   está en otra máquina, use el nombre de host del servidor en la URL del
   endpoint (el certificado se genera con ese nombre; `--host` lo cambia).

3. Intercambio de certificados (una vez por cliente):
   - Conecte el cliente con una política segura. El servidor rechaza su
     certificado (BadCertificateUntrusted) y lo guarda en
     `pki/rechazados/<CN> [<huella>].der`; la interfaz lo muestra en la lista
     de rechazados.
   - Compruebe el nombre y la huella, y mueva el fichero a `pki/confianza/`. No
     hace falta reiniciar.
   - Limitación conocida: no se comprueba la revocación (no se cargan listas
     de revocación). Para retirar un certificado basta con sacarlo de
     `pki/confianza/`.
   - En el cliente, acepte el certificado del servidor
     (`pki/servidor_cert.der`) cuando lo pregunte o en su almacén de confianza.

## 3. Procedimiento por cliente

En cada cliente, recorra estos pasos y anote el resultado en la sección 4.

| Función | Cómo se comprueba |
|---|---|
| Navegar | Desde Objects, abrir CilindroA, CilindroB, PulsadorVerde y LedRojo; ver variables, tipos y el método ControlarPulsador |
| Leer | Leer CilindroA.Estado.Expandido; con `a+` en el PLC emulado debe pasar a True (calidad Good) |
| Escribir | Escribir True en LedRojo.Value (requiere usuario); `s` en el PLC emulado muestra Q0.3 = 1 |
| Suscribirse | Suscribirse a CilindroA.Estado.Expandido y alternar `a+`/`a-`: llegan notificaciones con cada cambio |
| Llamar al método | Llamar a PulsadorVerde.ControlarPulsador con "pulsar" y "soltar": M0.0 pasa a 1 y a 0 |
| Modo y política | Conectar con cada endpoint de la tabla (política y modo de mensaje) y repetir leer y escribir |
| Autenticación de usuario | Anónimo: lee pero no escribe (BadUserAccessDenied); `rafa`/`clave123`: lee y escribe; contraseña errónea: rechazada; en `estricto`, anónimo rechazado |
| Lista de confianza | Primer intento rechazado y certificado en `pki/rechazados`; tras moverlo a `pki/confianza`, conecta sin reiniciar el servidor |
| Recuperación tras caída del PLC | Con la suscripción activa, salir del PLC emulado (`q`): las variables pasan a calidad Bad sin que se cierre la sesión; al volver a arrancarlo, recuperan Good y valores actualizados sin reconectar el cliente |

ConsoleReferenceClient: sus opciones de línea de órdenes cambian entre
versiones; consúltelas con `--help`. Si la versión usada no permite alguna
función (por ejemplo, escribir o llamar a un método), anote N/A, no OK.

opcua-commander (requiere Node.js con npm):

1. Instalación: `npm install -g opcua-commander`. Anote la versión
   (`npm ls -g opcua-commander`).
2. Conexión: `opcua-commander -e opc.tcp://<host>:4840`. Sin más opciones usa
   None / None y usuario anónimo.
3. Seguridad: consulte `opcua-commander --help` y anote aquí las opciones que
   muestre la versión usada. Según su documentación son `-s`/`--securityMode`
   (None, Sign, SignAndEncrypt), `-P`/`--securityPolicy` (nombre de la
   política, p. ej. Basic256Sha256) y `-u`/`--userName` con
   `-p`/`--password`; por ejemplo:

   ```
   opcua-commander -e opc.tcp://<host>:4840 -s SignAndEncrypt -P Basic256Sha256 -u rafa -p clave123
   ```

   Opciones mostradas por `--help` en la versión probada: pendiente.
4. Certificado: node-opcua crea su propio certificado de cliente en el primer
   arranque; tras el primer rechazo, muévalo de `pki/rechazados/` a
   `pki/confianza/` como en la sección 2.
5. Si la versión no permite una función (escribir, llamar a un método…) o no
   ofrece una política (no aparece entre los valores de `--securityPolicy`),
   anote N/A en esa casilla, no OK ni FALLO.

## 4. Resultados

Valores: `OK`, `FALLO` (con nota), `N/A` (el cliente no lo permite) o
`pendiente` (sin probar). Sin probar no se marca nada como OK.

Versiones: UaExpert 2.0.2 (compilación 665) · ConsoleReferenceClient
2.0.3-preview.9+9ce0c9abf0, compilado desde UA-.NETStandard con .NET 10 ·
opcua-commander (node-opcua) 0.40.0 · servidor 5.0 · fecha de las pruebas:
10/11/2026.

Una política o modo que el cliente no ofrezca se marca N/A.

| Función | UaExpert | ConsoleReferenceClient | opcua-commander (node-opcua) |
|---|---|---|---|
| Navegar | OK | OK | OK |
| Leer | OK | OK | OK |
| Escribir | OK | N/A | OK |
| Suscribirse | OK | N/A | OK |
| Llamar al método | OK | N/A | OK |
| None / None | OK | OK | OK |
| Basic256Sha256 / Sign | OK | N/A | OK |
| Basic256Sha256 / SignAndEncrypt | OK | N/A | OK |
| Aes128_Sha256_RsaOaep / Sign | OK | N/A | OK |
| Aes128_Sha256_RsaOaep / SignAndEncrypt | OK | N/A | OK |
| Aes256_Sha256_RsaPss / Sign | OK | N/A | OK |
| Aes256_Sha256_RsaPss / SignAndEncrypt | OK | N/A | OK |
| Usuario: anónimo (lee, no escribe) | OK | N/A | OK |
| Usuario: usuario y contraseña | OK | OK | OK |
| Usuario: contraseña errónea rechazada | OK | OK | OK |
| Usuario: anónimo rechazado en estricto | OK | OK | OK |
| Lista de confianza: rechazo y aceptación | OK | OK | OK |
| Recuperación tras caída del PLC | OK | N/A | OK |

Resumen: ninguna prueba dio FALLO con ningún cliente y no fue necesario
modificar el servidor. Las funciones que el cliente de referencia no permite
probar (N/A) están verificadas con los otros dos clientes.

Notas de los fallos:

- Ninguna.

Notas de la columna ConsoleReferenceClient:

- Es un cliente de ejemplo que ejecuta operaciones predefinidas sobre nodos de
  su propio servidor de ejemplo: por eso no permite escribir en variables ni
  llamar a métodos concretos, y su suscripción no muestra variables de este
  servidor.
- Elige automáticamente el endpoint más seguro y no permite seleccionar una
  política concreta; su opción `--testall` no produjo resultados en esta
  versión. La conexión cifrada se estableció correctamente.
- Leer: verificado sobre nodos del servidor (ServerStatus, BuildInfo,
  NamespaceArray), todos con calidad Good.
- Navegar: verificado comparando el NodeSet exportado por el cliente
  (`evidencias/miservidor_ledRojo_ns2.xml`) con `modelo_celula.xml`: 53 de 53
  nodos de instancia idénticos en clase, tipo de dato y nivel de acceso. La
  comparación es reproducible con `py test_interop_navegacion.py`.
- Contraseña errónea: en modo `firmar` el cliente llegó a conectar; en modo
  `estricto`, sin acceso anónimo, se rechaza con BadUserAccessDenied.
- Anónimo en estricto: el servidor no ofrece token anónimo y el cliente no
  puede establecer la sesión.
- Anónimo (lee, no escribe): la conexión y la lectura anónimas constan en
  None / None y Leer; la denegación de escritura no pudo observarse.
- Se usó la opción `-a`, que solo afecta a la confianza del cliente en el
  servidor; la lista de confianza del servidor se aplicó igualmente.

## 5. Comportamientos conocidos

- La lista de confianza se aplica al crear la sesión: el cliente recibe
  BadCertificateUntrusted en CreateSession, no al abrir el canal seguro.
- Un cliente que se conecta con la política None pero envía su certificado
  también se valida contra la lista.
- Se exige que el ApplicationUri del cliente coincida con el URI de su
  certificado (BadCertificateUriInvalid en caso contrario).
- El servidor admite usuario y contraseña, no certificados X.509 de usuario.
- Limitación conocida de asyncua, detectada con ConsoleReferenceClient: al
  escribir en un NodeId inexistente, el servidor devuelve BadUserAccessDenied
  en lugar de BadNodeIdUnknown (la lectura sí devuelve BadNodeIdUnknown),
  porque asyncua comprueba los permisos antes que la existencia del nodo. No
  afecta al funcionamiento. Lo documenta `py test_interop_nodo_inexistente.py`.
