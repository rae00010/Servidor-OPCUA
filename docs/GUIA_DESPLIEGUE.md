# Guía de despliegue del servidor OPC UA externo (V5)

Esta guía explica cómo dejar en marcha el servidor OPC UA externo desacoplado
sobre la célula con el PLC Siemens S7-1200, cómo organizar los ficheros en
carpetas y cómo probarlo, con o sin hardware. Está pensada para reproducir el
despliegue en una máquina nueva.

## 1. Qué es cada fichero

El servidor se reparte en varios ficheros de código que deben estar siempre
juntos en `servidor/`, más el modelo de información en `modelos/`. El resto son
herramientas, pruebas y documentación. Las rutas se dan desde la raíz del proyecto.

| Fichero | Función | ¿Imprescindible? |
|---|---|---|
| `servidor/servidor_opcua.py` | Servidor (nivel upstream) | Sí |
| `servidor/driver_base.py` | Interfaz del nivel downstream | Sí |
| `servidor/driver_snap7.py` | Driver S7 sobre Snap7 (nivel downstream) | Sí |
| `servidor/drivers.py` | Registro de drivers por nombre de protocolo (`--driver`) | Sí |
| `servidor/seguridad.py` | Certificado, políticas y autenticación | Sí |
| `servidor/modelo.py` | Motor del modelo: descripción de planta, generador e importación de NodeSet2 | Sí |
| `servidor/configuracion.py` | Configuración única en YAML (`--config`), común al servidor y a la interfaz | Sí |
| `config/config_celula.yaml` | Configuración de la célula (snap7, sin seguridad, `modelo_celula.xml`) | Recomendado |
| `config/config_ejemplo_estricto.yaml` | Ejemplo: seguridad estricta y modelo generado desde una planta | Opcional |
| `servidor/interfaz.py`, `servidor/proceso.py`, `servidor/interfaz/` | Interfaz web local y gestor del proceso del servidor | Para la interfaz |
| `modelos/modelo_celula.xml` | Modelo de información de la célula (NodeSet2), generado desde `planta_celula.yaml` | Sí |
| `modelos/planta_celula.yaml` | Descripción de planta de la célula (origen de `modelo_celula.xml`) | Recomendado |
| `modelos/planta_ejemplo_variador.yaml` | Ejemplo de dispositivo propio con variables INT y REAL | Opcional |
| `modelos/ledmodel_v5.xml` | Modelo V5 hecho a mano; solo para pruebas de regresión | Solo pruebas |
| `requirements.txt` | Dependencias de Python | Recomendado |
| `herramientas/plc_emulado.py` | PLC S7 emulado para probar sin hardware | Solo pruebas |
| `pruebas/test_v5_plc_emulado.py` | Prueba funcional automática | Solo pruebas |
| `pruebas/test_paso7_fallos.py` | Prueba de caídas y reconexión | Solo pruebas |
| `pruebas/test_seguridad.py` | Prueba de cifrado y autenticación | Solo pruebas |
| `pruebas/test_faseA_contrato.py` | Prueba del contrato de driver y de la fábrica (sin PLC) | Solo pruebas |
| `pruebas/test_faseA_servidor.py` | Prueba de `--driver` y de modelos con otra URI y otros NodeIds | Solo pruebas |
| `pruebas/test_faseA_tipos.py` | Prueba de los tipos BOOL, BYTE, WORD, INT, DINT y REAL | Solo pruebas |
| `pruebas/test_faseB_planta.py` | Prueba de la descripción de planta en YAML (sin PLC) | Solo pruebas |
| `pruebas/test_faseB_generador.py` | Prueba del generador de NodeSet2 | Solo pruebas |
| `pruebas/test_faseB_metodos.py` | Prueba de los métodos declarados en el modelo | Solo pruebas |
| `pruebas/test_faseB_importacion.py` | Prueba de la importación con validación | Solo pruebas |
| `pruebas/test_faseB_coherencia.py` | Prueba de la coherencia de la planta | Solo pruebas |
| `pruebas/test_faseB_cierre.py` | Misma tabla, métodos y NodeIds con el modelo generado y con el V5 | Solo pruebas |
| `pruebas/test_faseC_contrato.py` | Contrato del driver v1.2 (parámetros declarados) | Solo pruebas |
| `pruebas/test_faseC_config.py` | Configuración YAML y arranque con `--config` | Solo pruebas |
| `pruebas/test_faseC_parada.py` | Parada ordenada (stdin, fichero de PID y fichero de control) | Solo pruebas |
| `pruebas/test_faseC_api.py` | API de la interfaz, rutas seguras, sesión y gestor del proceso | Solo pruebas |
| `pruebas/test_faseC_pagina.py` | Página web sin recursos externos | Solo pruebas |
| `pruebas/test_faseC_cierre.py` | Flujo completo desde la interfaz y arranque solo con `--config` | Solo pruebas |
| `herramientas/cliente_medida.py` | Cliente de medida para la campaña de laboratorio | Para medir |
| `herramientas/paso1_nodeset.py` | Utilidad para regenerar el modelo | Opcional |
| `pruebas/_rutas.py`, `pruebas/util_interop.py` | Rutas comunes de las pruebas y auxiliar de las de interoperabilidad | Solo pruebas |
| `pruebas/test_interop_*.py` | Pruebas de interoperabilidad (perfil, confianza, políticas, documentación, NodeId inexistente, navegación) | Solo pruebas |
| `docs/` | Guía de despliegue, interoperabilidad, plan y bitácora | — |
| `evidencias/` | Evidencias de las pruebas manuales con clientes | — |

## 2. Estructura de carpetas

Todos los comandos se lanzan desde la raíz del proyecto, que es también la
carpeta de trabajo del servidor y de la interfaz: allí se crean `pki/` (la
primera vez que se arranca con seguridad), `logs/`, `config.yaml` (lo guarda la
interfaz), `servidor.pid` y `servidor.parar`. Los módulos de `servidor/` se
importan entre sí sin ruta, así que deben compartir carpeta. Las rutas relativas
de un fichero de configuración se toman respecto a la carpeta de ese fichero
(por eso `config/config_celula.yaml` usa `../modelos/modelo_celula.xml`).

```
servidor-opcua/
├── servidor/
│   ├── servidor_opcua.py         (servidor, nivel upstream)
│   ├── driver_base.py, driver_snap7.py, drivers.py
│   ├── seguridad.py, modelo.py, configuracion.py
│   ├── interfaz.py, proceso.py   (interfaz web local)
│   └── interfaz/                 (index.html, app.js, estilo.css)
├── modelos/
│   ├── modelo_celula.xml         (generado desde planta_celula.yaml; se versiona)
│   ├── planta_celula.yaml
│   ├── planta_ejemplo_variador.yaml  (ejemplo)
│   └── ledmodel_v5.xml           (solo para pruebas de regresión)
├── config/
│   ├── config_celula.yaml        (configuración de la célula)
│   └── config_ejemplo_estricto.yaml  (ejemplo)
├── herramientas/
│   ├── plc_emulado.py            (solo para pruebas sin PLC)
│   ├── cliente_medida.py         (campaña de medidas)
│   └── paso1_nodeset.py          (utilidad de referencia)
├── pruebas/                      (test_*.py, util_interop.py, _rutas.py)
├── docs/                         (guías, interoperabilidad, plan y bitácora)
├── evidencias/                   (evidencias de las pruebas manuales)
├── requirements.txt
├── config.yaml                   (configuración de trabajo de la interfaz)
├── pki/                          (se crea sola: certificado, clave, confianza/ y rechazados/)
├── logs/                         (logs del servidor arrancado desde la interfaz)
└── tmp/                          (temporales de las pruebas; se puede borrar)
```

En un despliegue de producción bastan `servidor/` (sin la interfaz, si no se
usa), la configuración y el NodeSet. La interfaz (`servidor/interfaz.py`,
`servidor/proceso.py` y `servidor/interfaz/`) es opcional.

## 3. Requisitos previos

En el ordenador que ejecuta el servidor (el nodo perimetral):

- Python 3.12 o superior. Probado con 3.12 y 3.14.
- Las dependencias de `requirements.txt`: `asyncua`, `python-snap7` 2.0.2 y
  `PyYAML` 6.0.3 (descripción de planta y configuración). La interfaz web añade
  FastAPI, Starlette, Pydantic, Uvicorn y python-multipart, todas con versión
  fijada y con ruedas binarias para Python 3.14 (no se compila nada ni se carga
  nada de internet al usarla). La
  generación de certificados usa la biblioteca `cryptography`, que viene con
  `asyncua`.
- La biblioteca nativa Snap7 (`snap7.dll` en Windows). Si en esta máquina ya
  funcionaba el servidor anterior (V4), la DLL ya está disponible.

En el lado del PLC, para que el servidor externo pueda leer y escribir por el
protocolo S7, en TIA Portal, dentro de las propiedades de la CPU, apartado de
protección y seguridad, debe estar marcada la opción de permitir el acceso
mediante comunicación PUT/GET. Sin eso, Snap7 no puede acceder a la memoria.

## 4. Instalación de las dependencias

Desde una consola situada en la raíz del proyecto:

```
cd C:\ruta\a\servidor-opcua
py -m pip install -r requirements.txt
```

Para comprobar que quedó todo instalado:

```
py -c "import asyncua, snap7, yaml; print('asyncua, snap7 y PyYAML OK')"
```

## 5. Puesta en marcha con el PLC

El comando mínimo, con la IP, el rack y el slot por defecto de la célula
(192.168.0.1, rack 0, slot 1):

```
py servidor/servidor_opcua.py -m modelos/modelo_celula.xml
```

Con esta orden el servidor escucha solo en la propia máquina
(`opc.tcp://127.0.0.1:4840`) y sin cifrado, de modo que el cliente OPC UA debe
ejecutarse en ese mismo ordenador. Para que un cliente de otra máquina pueda
conectarse, hay que indicar un endpoint accesible desde la red:

```
py servidor/servidor_opcua.py -m modelos/modelo_celula.xml --endpoint opc.tcp://0.0.0.0:4840
```

Si el PLC está en otra dirección:

```
py servidor/servidor_opcua.py -m modelos/modelo_celula.xml --plc-ip 192.168.0.10 --rack 0 --slot 1
```

Con un fichero de configuración (apartado 14), todo lo anterior cabe en un solo
parámetro:

```
py servidor/servidor_opcua.py --config config/config_celula.yaml
```

Al arrancar correctamente, el registro muestra la PDU negociada (en un
S7-1200 suele ser 240 bytes), la tabla de mapeo con las variables del modelo y
el plan de lectura. Conviene comprobar esas tres cosas en el log.

## 6. Seguridad

El servidor ofrece tres modos de seguridad, elegidos con `--seguridad`.

| Modo | Qué ofrece | Uso |
|---|---|---|
| `none` (por defecto) | Solo SecurityPolicy None, sin cifrado | Pruebas en red aislada |
| `firmar` | None y Basic256Sha256, Aes128-Sha256-RsaOaep y Aes256-Sha256-RsaPss en Sign y SignAndEncrypt | Admite clientes con y sin cifrado; útil para medir el coste de la seguridad |
| `estricto` | Solo Basic256Sha256, Aes128-Sha256-RsaOaep y Aes256-Sha256-RsaPss en Sign y SignAndEncrypt | Despliegue seguro, sin opción sin cifrar |

Medida del coste de la seguridad: en la campaña de medidas se usará una
política que también ofrezca el servidor OPC UA embebido del S7-1200, para que
la comparación entre ambos servidores sea con la misma criptografía. La política
común prevista es Basic256Sha256; antes de la campaña, confirme en TIA Portal
qué políticas tiene habilitadas el servidor embebido con el firmware de la CPU
usada.

En los modos `firmar` y `estricto`, la primera vez que se arranca se genera un
certificado de aplicación autofirmado y su clave en la carpeta `pki`, y se
reutilizan después. La clave privada (`pki/servidor_key.pem`) no debe
compartirse.

La autenticación de usuario se activa pasando credenciales con `--usuario`, que
puede repetirse. Cuando hay credenciales, el usuario anónimo puede leer y
suscribirse, pero solo un usuario autenticado puede escribir o invocar métodos.
En modo `estricto` con credenciales, el acceso anónimo queda deshabilitado por
completo.

Ejemplo de arranque seguro, en red, con cifrado obligatorio y un usuario:

```
py servidor/servidor_opcua.py -m modelos/modelo_celula.xml --endpoint opc.tcp://0.0.0.0:4840 --seguridad estricto --usuario rafa:clave123
```

Lista de confianza de certificados de cliente. En los modos `firmar` y
`estricto` está activada por defecto: el certificado que presenta cada cliente
se valida (vigencia, ApplicationUri y confianza) contra la carpeta
`pki/confianza`. Un certificado no confiable se rechaza
(BadCertificateUntrusted) y se guarda en `pki/rechazados` con el nombre
`<CN saneado> [<huella SHA-1>].der`. Para aceptarlo, el administrador mueve ese
fichero a `pki/confianza`; se tiene en cuenta en la conexión siguiente, sin
reiniciar. Cada rechazo se registra con su motivo (en el log y en
`pki/rechazados/motivos.json`): no confiable, URI no válido (el ApplicationUri
del cliente no coincide con el del certificado) o caducado (fuera de vigencia).
Solo los «no confiables» se aceptan moviéndolos a `pki/confianza`; los demás
seguirían rechazados y el cliente necesita otro certificado. La interfaz
muestra la lista de rechazados (nombre, huella, fecha, motivo y si se acepta
moviéndolo) en solo lectura; se construye a partir de los ficheros `.der`
presentes en `pki/rechazados` (las entradas de `motivos.json` sin fichero se
ignoran).

Limitación conocida: no se comprueba la revocación. El servidor no carga
listas de revocación (CRL), así que un certificado revocado por su CA no se
detecta como tal. Para retirar la confianza a un cliente basta con sacar su
certificado de `pki/confianza`: desde la conexión siguiente se rechaza como no
confiable, sin reiniciar.

Decisión: no se exige `nonRepudiation` en el KeyUsage del certificado de
cliente. La validación completa de asyncua comprueba el KeyUsage entero,
incluido `nonRepudiation`, y rechazaría (BadCertificateUseNotAllowed)
certificados válidos que generan algunos clientes, que solo declaran
`digitalSignature`, `keyEncipherment` y `dataEncipherment`. La especificación
no lo hace imprescindible para firmar y cifrar el canal, así que se comprueban
vigencia, ApplicationUri y confianza, y no ese bit.

La lista de confianza se desactiva con `--no-lista-confianza` o
`seguridad.lista_confianza: false`, y entonces se acepta cualquier certificado
de cliente. Nota: un cliente que se conecta con la política None pero envía su
certificado también se valida contra la lista.

Para conectar desde UaExpert con seguridad, elija una de las políticas
seguras y el modo Sign and Encrypt, introduzca el usuario y la contraseña, y
acepte confiar en el certificado del servidor cuando UaExpert lo pregunte. La
primera conexión será rechazada; mueva el certificado de UaExpert de
`pki/rechazados` a `pki/confianza` y vuelva a conectar. El procedimiento de
pruebas con varios clientes está en `INTEROPERABILIDAD.md`.

Advertencia: el modo por defecto sigue siendo `none`. Mientras arranque sin
`--seguridad`, el servidor no cifra ni autentica, así que en ese caso
despliéguelo únicamente en una red de laboratorio aislada o de confianza.

## 7. Parámetros del servidor

Desde la Fase C, todas las opciones tienen su clave en el fichero de
configuración (apartado 14), y el servidor puede arrancar solo con
`--config config.yaml`. Si se da a la vez una opción suelta, prevalece la opción.
Entre paréntesis, la clave equivalente del YAML.

| Parámetro | Valor por defecto | Para qué sirve |
|---|---|---|
| `--config` | (ninguno) | Fichero de configuración YAML |
| `-m`, `--model` | (obligatorio si no hay `--config`) | Ruta al NodeSet2 (`modelo.nodeset`; fuerza `origen: nodeset`) |
| `--driver` | `snap7` | Protocolo aguas abajo, por nombre del registro de `drivers.py` (`driver.nombre`) |
| `--driver-param` | (ninguno) | `clave=valor` de un parámetro del driver; se puede repetir (`driver.parametros`) |
| `--plc-ip` | `192.168.0.1` | Dirección IP del PLC |
| `--rack` | `0` | Rack del PLC |
| `--slot` | `1` | Slot del PLC |
| `--endpoint` | `opc.tcp://127.0.0.1:4840` | Dirección donde escucha el servidor |
| `--seguridad` | `none` | Modo de seguridad: `none`, `firmar` o `estricto` |
| `--cert-dir` | `pki` | Carpeta del certificado y la clave del servidor, y de `confianza/` y `rechazados/` |
| `--lista-confianza` / `--no-lista-confianza` | activada | Lista de confianza de certificados de cliente (firmar y estricto) |
| `--usuario` | (ninguno) | Credencial `nombre:clave`; se puede repetir |
| `--app-uri` | `urn:uja:opcua:servidor-desacoplado` | ApplicationUri del servidor |
| `--host` | nombre del sistema | Nombre de host para el certificado |
| `--perfil` | (ninguno) | Perfil de servidor declarado en ServerProfileArray; se puede repetir. Declare solo un perfil verificado |
| `--timeout-ms` | `1000` | Tiempo límite de cada petición S7 |
| `--fallos-max` | `3` | Peticiones o ciclos fallidos seguidos para dar por perdido el enlace |
| `--reintento-max-s` | `10` | Espera máxima entre intentos de reconexión |
| `--periodo-ms` | `100` | Periodo objetivo del ciclo de adquisición, en ms |
| `--log-csv` | (ninguno) | Registra una fila por ciclo en ese CSV (para la estadística) |
| `--log-accesos` | (ninguno) | Registra una fila por cada acceso al PLC en ese CSV |
| `--parada-stdin` | (desactivado) | La línea `parar` en la entrada estándar detiene el servidor de forma ordenada |
| `--fichero-pid` | (ninguno) | Escribe el PID (JSON con latido cada segundo) y lo borra al terminar |
| `--fichero-control` | (ninguno) | Si aparece ese fichero, el servidor lo borra y se detiene de forma ordenada |

`--plc-ip`, `--rack`, `--slot` y `--timeout-ms` se mantienen por compatibilidad y
equivalen a `--driver-param ip=`, `rack=`, `slot=` y `timeout_ms=`. Si se dan
`--plc-ip` y `--driver-param ip=` con valores distintos, es un error. Las tres
últimas opciones son de control del proceso, no de configuración: las usa la
interfaz. La opción `--simulacion` se retiró en la Fase C: sin PLC se usa el PLC
emulado.

## 8. Verificación con un cliente

Con el servidor en marcha, abra UaExpert y conecte al endpoint
(`opc.tcp://127.0.0.1:4840` si es local, o la IP de la máquina si es en red).
Si arrancó sin seguridad, conecte sin cifrado; si arrancó con seguridad, siga
el apartado 6. Arrastre las variables del modelo a la vista de datos y
compruebe que los sensores de los cilindros siguen a la planta, que al escribir
en `LedRojo/Value` cambia la salida, y que el método `ControlarPulsador`
responde. Si las escrituras en los sensores se rechazan con
`BadUserAccessDenied`, es lo correcto: son de solo lectura.

## 9. Pruebas sin hardware

Para validar el despliegue sin conectar el PLC, hay un PLC S7 emulado y
veinticuatro pruebas automáticas, seis de ellas de interoperabilidad. Todo se
ejecuta desde la raíz del proyecto con `py pruebas/<prueba>.py`. Todas salvo
`test_faseA_contrato.py`, `test_faseB_planta.py`, `test_faseC_pagina.py`,
`test_interop_doc.py` y `test_interop_navegacion.py` abren el puerto 102, así
que deben ejecutarse de una en una y con TIA Portal o PLCSIM cerrados.

```
py pruebas/test_v5_plc_emulado.py      (prueba funcional)
py pruebas/test_paso7_fallos.py        (caídas y reconexión, tarda alrededor de un minuto)
py pruebas/test_seguridad.py           (cifrado y autenticación)
py pruebas/test_faseA_contrato.py      (contrato de driver y fábrica de drivers, sin PLC)
py pruebas/test_faseA_servidor.py      (--driver y modelos con otra URI y otros NodeIds)
py pruebas/test_faseA_tipos.py         (tipos BOOL, BYTE, WORD, INT, DINT y REAL)
py pruebas/test_faseB_planta.py        (descripción de planta en YAML, sin PLC)
py pruebas/test_faseB_generador.py     (generador de NodeSet2; variador con INT y REAL)
py pruebas/test_faseB_metodos.py       (métodos declarados en el modelo)
py pruebas/test_faseB_importacion.py   (importación con validación)
py pruebas/test_faseB_coherencia.py    (escrituras duplicadas o solapadas y aviso de Q)
py pruebas/test_faseB_cierre.py        (misma tabla, métodos y NodeIds que con ledmodel_v5.xml)
py pruebas/test_faseC_contrato.py      (contrato del driver v1.2)
py pruebas/test_faseC_config.py        (configuración YAML y arranque con --config)
py pruebas/test_faseC_parada.py        (parada ordenada sin señales)
py pruebas/test_faseC_api.py           (API de la interfaz, rutas seguras y sesión)
py pruebas/test_faseC_pagina.py        (página web sin recursos externos, sin PLC)
py pruebas/test_faseC_cierre.py        (flujo completo desde la interfaz)
py pruebas/test_interop_perfil.py      (ServerProfileArray vacío salvo --perfil)
py pruebas/test_interop_confianza.py   (lista de confianza: rechazo, aceptación y motivos)
py pruebas/test_interop_politicas.py   (políticas y modos de firmar y estricto)
py pruebas/test_interop_doc.py         (coherencia de INTEROPERABILIDAD.md, sin PLC)
py pruebas/test_interop_nodo_inexistente.py  (escritura en un NodeId inexistente)
py pruebas/test_interop_navegacion.py  (evidencia de navegación frente al modelo, sin PLC)
```

Las pruebas trabajan en la carpeta `tmp/` de la raíz (la fija `pruebas/_rutas.py`):
allí dejan la carpeta `prueba_faseC/`, la `pki/` y los certificados de cliente
de prueba, que se pueden borrar.

Las pruebas `test_faseA_*` y `test_faseB_*` terminan con un resumen
`[OK]`/`[FALLO]` y salen con código 0 si todo pasa. Generan en `tmp/` modelos
de prueba (`modelo_*_faseA.xml`, `modelo_faseB_*.xml`, `planta_faseB_*.yaml`), que
se pueden borrar. Los logs del servidor de todas las pruebas se guardan en
`tmp/logs/` (se crea sola).

Para una sesión interactiva con UaExpert sobre el emulador, en dos consolas. En
la primera se arranca el PLC emulado:

```
py herramientas/plc_emulado.py
```

Acepta órdenes por teclado: `a+` y `a-` mueven el cilindro A, `b+` y `b-` el
cilindro B, `p` pulsa el pulsador, `s` muestra el estado y `q` sale. En la
segunda consola se arranca el servidor contra el emulador:

```
py servidor/servidor_opcua.py -m modelos/modelo_celula.xml --plc-ip 127.0.0.1
```

Después se conecta UaExpert igual que en el apartado 8.

Para la campaña de medidas, el ciclo se ejecuta a periodo fijo con
`--periodo-ms`, y con `--log-csv` y `--log-accesos` se obtiene el registro
crudo (una fila por ciclo y una por acceso, con reloj monotónico) sobre el que
después se calcula la estadística. Una fila de ciclo con la columna `desborde`
a 1 indica que ese ciclo no cupo en el periodo.

## 10. Resolución de problemas

El emulador no arranca porque el puerto 102 está ocupado. En Windows suele
deberse a que hay software de Siemens usándolo, como TIA Portal o PLCSIM.
Compruébelo con `netstat -ano | findstr :102` y cierre ese programa mientras
prueba. El PLC emulado solo hace falta para las pruebas sin hardware.

El servidor no conecta con el PLC real. Verifique que está habilitado el
acceso PUT/GET en la CPU (apartado 3), que la IP, el rack y el slot son
correctos, y que no hay un cortafuegos bloqueando la comunicación.

El cliente de otra máquina no ve el servidor. Revise que arrancó con
`--endpoint opc.tcp://0.0.0.0:4840` y que el cortafuegos de Windows permite el
puerto 4840 en la red correspondiente.

El cliente seguro no confía en el servidor. Hay que aceptar el certificado del
servidor en el cliente (en UaExpert, moverlo a los certificados de confianza).
Si se regenera el certificado, por ejemplo al borrar la carpeta `pki`, habrá
que volver a confiar en el nuevo.

El log se llena de errores de lectura. Indica que el enlace con el PLC se ha
perdido; el servidor reintenta solo y marca las variables con calidad
Uncertain o Bad. Si ocurre sin motivo aparente, suba `--timeout-ms`.

## 11. Cliente de medida (campaña de laboratorio)

El fichero `cliente_medida.py` es un cliente OPC UA scriptado que automatiza las
medidas del estudio. Es el mismo instrumento para la solución externa y para la
embebida: localiza los nodos por su ruta de navegación, no por su identificador,
de modo que funciona contra ambos servidores. Para apuntarlo al servidor
embebido basta con indicar su endpoint y su espacio de nombres con `--ns-uri` o
`--ns`. Cada modo guarda las muestras crudas en un CSV y muestra al final un
resumen con mediana, rango intercuartílico, p95, p99 y máximo.

Tiene cuatro modos. La latencia extremo a extremo se mide con un bucle físico:
se cablea una salida del PLC a una de sus entradas, se añaden ambas al modelo, y
el cliente escribe la salida y cronometra hasta recibir la notificación de la
entrada.

```
py herramientas/cliente_medida.py latencia --modo loopback --salida Loopback/Salida --entrada Loopback/Entrada --iteraciones 2000 --csv lat_B.csv
```

Si aún no hay cableado, el modo eco escribe una variable y espera su propia
notificación, útil para probar el cliente, aunque no incluye el lazo físico:

```
py herramientas/cliente_medida.py latencia --modo eco --salida LedRojo/Value --iteraciones 500 --csv lat_eco.csv
```

El rendimiento y las pérdidas se miden con el contador del PLC (un contador
incrementado a periodo conocido, expuesto en el modelo):

```
py herramientas/cliente_medida.py rendimiento --contador Diagnostico/Contador --duracion-s 60 --csv rend_B.csv
```

El comportamiento ante fallos registra la línea temporal de valor y calidad de
un nodo mientras se provoca el fallo externamente (desconectando la red o
parando el PLC):

```
py herramientas/cliente_medida.py fallos --nodo CilindroA/Estado/Expandido --contador Diagnostico/Contador --duracion-s 60 --csv fallos_B.csv
```

La indisponibilidad del servicio, para los cambios del modelo de información, se
mide sondeando un nodo a intervalo fijo mientras se realiza el cambio:

```
py herramientas/cliente_medida.py disponibilidad --nodo CilindroA/Estado/Expandido --duracion-s 120 --csv disp_B.csv
```

Para medir con cifrado, se añade a cualquier modo `--seguridad cifrado
--server-cert pki/servidor_cert.der` y, si el servidor lo exige, `--usuario
nombre:clave`. Para medir la solución embebida, se cambia `--endpoint` y
`--ns-uri` por los suyos; el resto de comandos es idéntico, lo que garantiza que
ambas soluciones se miden en condiciones iguales.

## 12. Estado actual y limitaciones

Esta versión incluye el tipo Address con el área de memoria, el mapeo estático
del modelo, la lectura agrupada por áreas, el ciclo de periodo fijo con registro
crudo, la reconexión automática con códigos de calidad del dato, la seguridad
con certificado, las políticas Basic256Sha256, Aes128-Sha256-RsaOaep y
Aes256-Sha256-RsaPss, la lista de confianza de certificados de cliente y la
autenticación de usuario, y los metadatos del servidor. Por defecto no se
declara ningún perfil (ServerProfileArray vacío): `--perfil` solo debe usarse
para declarar un perfil verificado con la Compliance Test Tool de la OPC
Foundation, a la que no se tiene acceso por ahora. Mientras tanto, la
interoperabilidad se comprueba con varios clientes según
`INTEROPERABILIDAD.md`. Si el servidor se arranca en
modo `none`, sigue sin cifrar, por lo que en ese caso solo debe desplegarse en
una red de confianza.

Desde la Fase A, el driver se elige por nombre (`--driver`, registro en
`drivers.py`), y el servidor no depende de la URI ni de los NodeIds del modelo.
Requisitos del NodeSet que se cargue:
- Debe declarar `<Models><Model ModelUri="...">`. Esa URI es el espacio de
  nombres del modelo. Si falta, el servidor no arranca; si hay varios `<Model>`,
  usa el primero y avisa.
- En ese espacio de nombres, debe definir un VariableType con BrowseName `Address`
  (si no, el servidor no arranca). Sus propiedades `area` y `byte` son
  obligatorias; `bit` es opcional y solo se exige en las variables Boolean; `db`
  es opcional y solo se exige en el área DB. Si falta alguna, el servidor no
  arranca y lista todas las variables afectadas.
- Los métodos los declara el NodeSet (UAMethod con InputArguments y
  OutputArguments). El servidor solo enlaza su implementación (ver apartado 13).
- El DataType de cada variable fija su tipo S7: Boolean=BOOL, Byte=BYTE,
  UInt16=WORD, Int16=INT, Int32=DINT, Float=REAL. La propiedad `bit` solo se usa
  en BOOL; en los demás tipos debe faltar o valer 0.

## 13. Descripción de planta y generación del modelo

Desde la Fase B, el modelo de información no se escribe a mano. Se describe la
planta en YAML, sin nada propio de OPC UA, y `modelo.py` genera el NodeSet2:

```
py servidor/modelo.py modelos/planta_celula.yaml -o modelos/modelo_celula.xml
```

`modelos/modelo_celula.xml` es el modelo de referencia de la célula. `ledmodel_v5.xml` se
conserva solo para las pruebas de regresión. El generador valida la descripción
completa y lista todos los errores a la vez; si hay errores, no escribe el
fichero.

Estructura del YAML (ejemplo completo en `planta_celula.yaml`; otro con INT y
REAL en `planta_ejemplo_variador.yaml`):

```yaml
modelo:
  uri: urn:miservidor:ledRojo     # ModelUri y espacio de nombres
  version: 1.1.0
  driver: snap7                   # obligatorio: interpreta las direcciones
tipos:
  TPulsador:                      # tipo de dispositivo (ObjectType)
    grupos:
      Control:
        PulsadoRemoto: {tipo: BOOL, acceso: escritura}
      Estado:
        Pulsado: {tipo: BOOL, acceso: lectura}
    metodos:
      ControlarPulsador:
        entradas: [{nombre: accion, tipo: STRING}]
        salidas:  [{nombre: resultado, tipo: STRING}]
instancias:
  PulsadorVerde:
    tipo: TPulsador
    direcciones:                  # todas las variables del tipo, sin excepción
      Control/PulsadoRemoto: M0.0
      Estado/Pulsado: I0.0
```

- Tipos de dato: BOOL, BYTE, WORD, INT, DINT y REAL. En los argumentos de método
  se admite además STRING.
- Acceso: `lectura` (PLC → OPC UA, AccessLevel 1) o `escritura` (OPC UA → PLC,
  AccessLevel 3).
- Las variables pueden ir agrupadas (`grupos`) o colgar directamente del
  dispositivo (`variables`). La ruta en `direcciones` es `Grupo/Variable` o
  `Variable`.
- NodeIds generados: de texto, `Instancia.Grupo.Variable` (o `Instancia.Variable`)
  e `Instancia.Metodo`, iguales a los de la V5 para que los clientes existentes
  sigan funcionando.
- Sintaxis de dirección con el driver `snap7`: `I0.5`, `Q0.3`, `M10.0`, `M20`,
  `DB10.4` y `DB10.0.1`. El bit es obligatorio en BOOL y no se admite en el resto.

Comprobaciones de coherencia del generador:
- Error: dos variables de escritura sobre la misma dirección o con rangos
  solapados (por ejemplo, un BOOL dentro de un INT). Si un NodeSet externo trae
  esa situación, el servidor arranca y solo avisa.
- Aviso: variable de escritura directamente en las salidas Q. El servidor escribe
  un bit leyendo, modificando y reescribiendo el byte entero, así que puede
  devolver un valor obsoleto a los bits del mismo byte que escriba el programa
  del PLC. Con la célula actual sale un aviso para `LedRojo/Value` (Q0.3, que
  comparte byte con las electroválvulas Q0.0 a Q0.2). Lo recomendable es una
  zona de marcas M reservada que el programa del PLC copie a la salida.

### Contrato del driver v1.1 (ampliado en v1.2, apartado 14)

La versión 1.1 añade a `driver_base.Driver` un método de clase, no abstracto:
`interpretar_direccion(texto, tipo_dato) -> dict`. Traduce la dirección escrita
en el YAML, con la sintaxis propia del driver, a las propiedades de Address
(`area`, `byte`, `bit`, `db`), o lanza `ValueError` con un mensaje claro. Al ser
de clase, el generador lo llama sin crear el driver ni conectarse. Por defecto
rechaza cualquier texto, así que un driver que no lo implemente sigue cumpliendo
el contrato v1 pero no puede usarse en una descripción de planta. La validación
de las variables importadas (`modelo.comprobar_variable`) es la misma en el
servidor y en `modelo.importar_nodeset`, que leerá la interfaz de la Fase C.

### Métodos: registro IMPLEMENTACIONES

El servidor tiene un registro `IMPLEMENTACIONES = {"ControlarPulsador":
controlar_pulsador}`. Al arrancar, recorre los objetos del modelo y enlaza
(`link_method`) cada UAMethod cuyo BrowseName esté en el registro.

El nombre del método es la clave global del registro: no depende del tipo de
dispositivo ni de la instancia. Todos los métodos con el mismo BrowseName, en
cualquier tipo, comparten la misma implementación. Por eso dos tipos de
dispositivo no pueden tener métodos distintos con el mismo nombre: si su
comportamiento difiere, hay que darles nombres distintos. La implementación
recibe el NodeId del objeto sobre el que se llama y localiza sus variables a
partir de él (`ControlarPulsador` escribe en `Control/PulsadoRemoto` de ese
objeto). Para añadir un método nuevo: se declara en el YAML, se implementa una
función en el servidor y se añade al registro con el mismo nombre. Un método del
modelo sin implementación no impide arrancar: el servidor avisa con su ruta y no
lo enlaza.

## 14. Configuración en YAML e interfaz web (Fase C)

### Fichero de configuración

El servidor arranca con `--config config.yaml`. Las claves que falten toman el
valor por defecto. Las rutas relativas se interpretan respecto a la carpeta del
propio fichero. Una clave desconocida o un valor no válido impiden arrancar, y
se listan todos los errores a la vez.

```yaml
servidor:   {endpoint: opc.tcp://127.0.0.1:4840, app_uri: urn:uja:opcua:servidor-desacoplado, host: null, perfiles: []}
driver:
  nombre: snap7
  parametros: {ip: 192.168.0.1, rack: 0, slot: 1, timeout_ms: 1000}   # los que declara el driver
enlace:     {fallos_max: 3, reintento_max_s: 10}
modelo:     {origen: nodeset, nodeset: modelo_celula.xml}             # o origen: planta + planta: x.yaml
seguridad:
  modo: none                       # none | firmar | estricto
  cert_dir: pki
  lista_confianza: true            # firmar/estricto: pki/confianza y pki/rechazados
  usuarios:
    - {nombre: rafa, clave_hash: "pbkdf2_sha256$600000$..."}          # 'clave' en claro: se admite, con aviso
medida:     {periodo_ms: 100, log_csv: null, log_accesos: null}
```

Hay dos ejemplos completos en `config/`: `config_celula.yaml` y `config_ejemplo_estricto.yaml`.

- Con `origen: planta`, el servidor valida la descripción de planta y genera
  `nodeset` al arrancar. Si la planta tiene errores, no arranca.
- Contraseñas: se guardan como `clave_hash` (PBKDF2-SHA256). Para obtener el hash
  sin la interfaz: `py servidor/configuracion.py --hash-clave`. Si el fichero contiene una
  `clave` en claro, el servidor y la interfaz lo avisan.
- Para comprobar un fichero sin arrancar el servidor:
  `py servidor/configuracion.py --validar config.yaml`.

### Configuración para la campaña de medidas

Para medir, la configuración usa `origen: nodeset` con un NodeSet congelado, por
ejemplo `modelo_celula.xml` generado una sola vez y versionado. No se usa
`origen: planta`, porque regeneraría el modelo en cada arranque. El servidor se
lanza sin la interfaz, solo con `--config`, y las rutas de los CSV se fijan en el
bloque `medida`:

```
py servidor/servidor_opcua.py --config config/config_campana.yaml
```

Para detenerlo, se pulsa Ctrl+C en su consola, que es una parada ordenada.

### Contrato del driver v1.2

`driver_base.Driver` añade `PARAMETROS`, una tupla de `ParametroDriver` con
nombre, tipo (texto, entero, real, bool u opcion), defecto, obligatorio,
descripción, límites, opciones y avanzado. Añade también `validar_parametros()`,
que convierte los tipos, aplica los defectos y lista todos los errores. Ninguno
de los dos es abstracto: un driver sin `PARAMETROS` se crea como antes.
`fallos_max` es un parámetro común: lo inyecta el servidor desde el bloque
`enlace`. La interfaz construye el formulario de conexión a partir de esta
declaración y de `drivers.disponibles()`, sin código propio de ningún protocolo.

### Interfaz web

```
py servidor/interfaz.py                                   # http://127.0.0.1:8080/
py servidor/interfaz.py --carpeta C:\ruta\trabajo        # otra carpeta de trabajo
```

La interfaz trabaja sobre una carpeta (por defecto, la actual: lanzada desde la
raíz, la propia raíz, y las plantillas de `modelos/` quedan dentro), que contiene
`config.yaml`, los modelos, `logs/servidor.log`, `servidor.pid` y
`servidor.parar`. Tiene cuatro pestañas:

1. **Conexión:** protocolo, parámetros del driver, enlace, endpoint, seguridad y
   usuarios OPC UA.
2. **Modelo:** importar o subir un NodeSet, o crear el modelo en el editor de
   YAML. El editor valida 500 ms después de dejar de teclear y muestra los
   errores, los avisos y la tabla de mapeo. "Generar NodeSet" no se activa
   mientras haya errores.
3. **Medida:** periodo y ficheros CSV.
4. **Servidor:** validar y guardar `config.yaml`, arrancar, detener y reiniciar,
   y ver el log en vivo.

Cambiar el modelo obliga a reiniciar el servidor: no hay recarga en caliente.

**Parada ordenada.** La interfaz lanza el servidor con `--parada-stdin`,
`--fichero-pid` y `--fichero-control`. Lo detiene con la orden `parar` y con el
fichero de control, de modo que el servidor cierra la conexión S7 y no deja
sesiones abiertas en el PLC. No usa `terminate()`, que en Windows mata el
proceso sin limpieza. Si el servidor no termina en 10 s, se fuerza la parada y
la interfaz lo indica. Si la interfaz se cae, el servidor sigue en marcha: el
cierre de la entrada estándar solo se avisa. Una interfaz nueva lo detecta por el
fichero de PID (proceso vivo y latido de menos de 5 s) y puede detenerlo de
forma ordenada.

**Seguridad de la interfaz:**
- Por defecto escucha solo en `127.0.0.1`. Para exponerla en red hay que dar
  `--host 0.0.0.0 --usuario nombre:clave` (o las variables de entorno
  `INTERFAZ_USUARIO` e `INTERFAZ_CLAVE`); sin usuario, no arranca.
- La contraseña se comprueba una sola vez y después se mantiene una sesión
  (cookie HttpOnly, SameSite=Strict, 8 h).
- Sin TLS, la contraseña viaja en claro, y la interfaz lo avisa. Se puede usar
  `--tls-cert cert.pem --tls-key clave.pem` o un túnel SSH.
- Las peticiones que modifican algo exigen la cabecera `X-Interfaz`. En modo
  local se rechazan las peticiones con un `Host` ajeno (protección frente a DNS
  rebinding).
- Todas las rutas de fichero de la API son relativas a la carpeta de trabajo. Se
  rechazan las absolutas y las que contienen `..`. Solo se admiten `.xml` y
  `.yaml`, y los nombres de los ficheros subidos se sanean.
- La página no carga nada de internet: HTML, CSS y JavaScript propios, sin CDN.

Pendiente para más adelante: un asistente con formulario para la descripción de
planta, el resaltado de sintaxis en el editor y la importación de la tabla de
símbolos de TIA Portal.
