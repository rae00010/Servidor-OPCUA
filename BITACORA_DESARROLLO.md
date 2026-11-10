# Bitácora de desarrollo del servidor OPC UA

Registro de cada paso realizado sobre el plan `PLAN_DESARROLLO_SERVIDOR.md`, con
el motivo de cada cambio. Se añade una entrada al cerrar cada paso; las entradas
anteriores no se modifican.

## Formato de cada entrada

```
### AAAA-MM-DD · Fase X, paso N · Título breve
- Qué: cambio realizado, en una o dos frases.
- Por qué: motivo del cambio y a qué criterio del plan responde.
- Ficheros: lista de ficheros creados o modificados.
- Pruebas: qué se ejecutó y resultado (por ejemplo, 14/14 OK).
- Decisiones: decisiones tomadas en este paso, si las hubo.
- Pendiente: lo que queda abierto, si algo.
```

## Entradas

### Punto de partida · V5
- Qué: versión de partida del servidor, etiquetada en git como respaldo.
- Por qué: base estable antes de aplicar el plan.
- Ficheros: servidor V5, drivers, seguridad, modelo v5, pruebas y emulador.
- Pruebas: funcional, fallos (14/14) y seguridad (8/8) en verde.
- Decisiones: ver apartado 10 del plan.
- Pendiente: fases A, B y C.

### Fase A · Paso 1 · Contrato del driver
- Qué: cabecera de `driver_base.py` como contrato público v1 (constructor por palabras clave, 8 métodos, tipos de dato, errores, estadísticas).
- Por qué: fijar el estándar que implementará cualquier driver.
- Ficheros: `driver_base.py`, `test_faseA_contrato.py` (nuevo).
- Pruebas: test_faseA_contrato 4/4 OK (paso 1).
- Decisiones: el DataType OPC UA de la variable fija el tipo del dato (Boolean, Byte, UInt16, Int16, Int32, Float).

### Fase A · Paso 2 · Registro y fábrica de drivers
- Qué: `drivers.py` con `DRIVERS = {"snap7": DriverSnap7}`, `disponibles()` y `crear_driver(nombre, parametros)`.
- Por qué: elegir el protocolo por nombre, con parámetros propios de cada driver.
- Ficheros: `drivers.py` (nuevo), `test_faseA_contrato.py`.
- Pruebas: test_faseA_contrato 7/7 OK.
- Decisiones: nombre desconocido o parámetros inválidos dan ValueError con el nombre del driver y la lista de disponibles.

### Fase A · Paso 3 · El servidor usa la fábrica
- Qué: argumento `--driver` (por defecto `snap7`, opciones tomadas de DRIVERS) y `crear_driver` con `ip, rack, slot, timeout_ms, fallos_max`. Los accesos a `DRIVER.pdu` pasan a `getattr`.
- Por qué: elegir el protocolo por nombre sin cambiar el comportamiento actual.
- Ficheros: servidor, `test_faseA_servidor.py` (nuevo).
- Pruebas: test_faseA_servidor 6/6 OK; test_v5_plc_emulado sin cambios en la salida.
- Decisiones: el nombre inválido lo rechaza argparse, listando los drivers disponibles.

### Fase A · Paso 4 · Sin identificadores fijos del modelo
- Qué: se eliminan NAMESPACE_URI, ADDRESS_TYPE_ID y TPULSADOR_TYPE_ID. La URI se lee de `<NamespaceUris>` del NodeSet, y Address/TPulsador se buscan por BrowseName entre los subtipos de BaseVariableType/BaseObjectType.
- Por qué: que los modelos de la Fase B funcionen con cualquier URI y cualquier NodeId.
- Ficheros: servidor, `test_faseA_servidor.py`.
- Pruebas: test_faseA_servidor 13/13 OK (modelo con otra URI y NodeIds +10000: lectura, escritura y método).
- Decisiones: se usa la primera URI del NodeSet. Sin Address, el servidor no arranca. Sin TPulsador, solo avisa.

### Fase A · Paso 5 · Tipos de dato en Snap7
- Qué: BOOL, BYTE, WORD, INT, DINT y REAL (big-endian, `struct`). DireccionS7 lleva tipo y tamaño. La lectura agrupada cubre `[byte, byte+tamaño)`. La escritura de tipos multibyte es completa, sin RMW. `Punto` guarda su VariantType y el servidor publica y escribe según él.
- Por qué: el generador de la Fase B será genérico (decisión 2).
- Ficheros: `driver_snap7.py`, servidor, `test_faseA_tipos.py` (nuevo).
- Pruebas: test_faseA_tipos 30/30 OK (límites y negativos; lectura en I/M/DB, escritura en Q/M/DB; plan de 4 peticiones).
- Decisiones: DataType→S7 Boolean=BOOL, Byte=BYTE, UInt16=WORD, Int16=INT, Int32=DINT, Float=REAL. `bit` solo en BOOL; en el resto debe faltar o valer 0.
- Pendiente: `simular_planta` sigue siendo propia del modelo LED/cilindros (booleana).

### Fase A · Cierre
- Qué: batería completa tras los pasos 1 a 5.
- Pruebas: test_v5_plc_emulado igual que la V5; paso7 14/14; seguridad 8/8; contrato 7/7; servidor 13/13; tipos 30/30.
- Pendiente: validación del responsable antes de la Fase B.

### 2026-11-08 · Fase A · Revisión antes del cierre
- Qué: (1) la URI del modelo sale de `<Models><Model ModelUri>`, ya no de la 1ª `<NamespaceUris>`. (2) Address/TPulsador se aceptan solo si su NodeId y su BrowseName están en el espacio de nombres del modelo. (3) Comprobado: no queda `bool()` en el callback de escritura ni en publicar/restaurar/degradar (solo en `convertir()` para Boolean).
- Por qué: cumplir el estándar NodeSet y no confundir tipos de otro vendedor.
- Ficheros: servidor, `test_faseA_servidor.py`, `test_faseA_tipos.py`.
- Pruebas: servidor 21/21 (modelo trampa con URI ajena delante y Address/TPulsador señuelo con los NodeIds de la V5; sin `<Models>`); tipos 36/36 (INT y REAL escritos desde cliente asyncua, releídos, degradados y restaurados con su tipo). Batería completa en verde: V5 igual, paso7 14/14, seguridad 8/8, contrato 7/7.
- Decisiones: sin `<Models>` el servidor no arranca; con varios `<Model>` usa el primero y avisa. Desde esta entrada, la bitácora lleva fecha.
- Pendiente: validación del cierre de la Fase A.

### 2026-11-08 · Fase A · Documentación
- Qué: GUIA_DESPLIEGUE.md incluye `drivers.py` y las tres pruebas `test_faseA_*` (tabla de ficheros, árbol de carpetas y apartado de pruebas), el parámetro `--driver` y, en el apartado 12, los requisitos del NodeSet: ModelUri, Address/TPulsador por BrowseName y tabla DataType→S7. CLAUDE.md añade `drivers.py` y las tres pruebas a la lista obligatoria.
- Por qué: dejar la documentación al día antes de la Fase B.
- Ficheros: `GUIA_DESPLIEGUE.md`, `CLAUDE.md`.
- Pruebas: no aplica (sin cambios de código). La última batería, 6/6 en verde.
- Pendiente: validación para empezar la Fase B.

### 2026-11-08 · Fase B · Paso 1 · Descripción de planta en YAML
- Qué: `modelo.py` lee y valida la descripción de planta (bloques modelo/tipos/instancias, tipos BOOL…REAL, acceso lectura/escritura, grupos, métodos con argumentos) y lista todos los errores a la vez. Contrato del driver v1.1: método de clase no abstracto `interpretar_direccion(texto, tipo_dato)`; DriverSnap7 lo implementa (I/Q/M<byte>[.<bit>], DB<n>.<byte>[.<bit>]) con la validación común `comprobar_direccion`.
- Por qué: punto 1 del apartado 5 (decisiones 2 y 3); la sintaxis de dirección es del driver, no del generador.
- Ficheros: `modelo.py`, `planta_celula.yaml`, `planta_ejemplo_variador.yaml` (nuevos), `driver_base.py`, `driver_snap7.py`, `requirements.txt` (PyYAML==6.0.3, rueda binaria), `test_faseB_planta.py` (nuevo).
- Pruebas: test_faseB_planta 22/22 (sin PLC: solo lectura del YAML). Batería en verde: V5 igual, paso7 14/14, seguridad 8/8, contrato 7/7, servidor 21/21, tipos 36/36.
- Decisiones: `modelo.driver` es obligatorio; son error una variable sin dirección en la instancia y las claves repetidas en el YAML. Simplificación respecto al modelo publicado: `TCilindroEstado` y `TPulsadorEstado` se dejan fuera, porque ninguna instancia los usa.

### 2026-11-08 · Fase B · Paso 2 · Generador de NodeSet2
- Qué: `modelo.py` genera desde cero el NodeSet2 con ElementTree: `<Models>` con ModelUri, el enumerado AreaPLC, el tipo Address (area y byte obligatorias; bit y db opcionales, i=80), los ObjectTypes con sus grupos, y las instancias con propiedades, DataType, AccessLevel y valor inicial tipado. También se puede usar desde la línea de órdenes: `py modelo.py planta.yaml -o modelo.xml`.
- Por qué: punto 2 del apartado 5, el estándar de creación del NodeSet.
- Ficheros: `modelo.py`, `test_faseB_generador.py` (nuevo; genera `modelo_faseB_celula.xml` y `modelo_faseB_variador.xml`).
- Pruebas: test_faseB_generador 14/14 (XML; el servidor arranca con el variador contra el PLC emulado; lee REAL, INT y BOOL; escribe INT en DB y BOOL en M). Batería en verde: planta 22/22, V5 igual, paso7 14/14, seguridad 8/8, contrato 7/7, servidor 21/21, tipos 36/36.
- Decisiones: NodeIds de texto Instancia.Grupo.Variable en las instancias. Los tipos usan NodeIds numéricos: Address i=2003, AreaPLC i=3001, ObjectTypes desde i=1001 y sus nodos desde i=5001. El bit solo se declara en las variables BOOL. Las propiedades de Address son de solo lectura (AccessLevel 1).

### 2026-11-08 · Fase B · Paso 3 · Métodos declarados en el modelo
- Qué: el generador declara cada método como UAMethod, en el tipo (Mandatory) y en cada instancia (con MethodDeclarationId), con sus InputArguments y OutputArguments (Argument). El servidor ya no usa `add_method`: tiene el registro `IMPLEMENTACIONES` y enlaza con `link_method`, por BrowseName, los métodos del espacio del modelo que haya bajo Objects. Un método sin implementación solo produce un aviso con su ruta. Se eliminan NOMBRE_TIPO_PULSADOR y TPULSADOR_ID.
- Por qué: punto 3 del apartado 5.
- Ficheros: `modelo.py`, servidor, `ledmodel_v5.xml` (ControlarPulsador declarado en TPulsador y en PulsadorVerde), `test_faseA_servidor.py` (4c y 4f comprueban ahora el aviso del enlace nuevo), `test_faseB_metodos.py` (nuevo).
- Pruebas: test_faseB_metodos 7/7 (argumentos visibles desde el cliente; pulsar y soltar llegan a M0.0; con "saltar" devuelve BadInvalidArgument; aviso por el método sin implementación). Batería en verde: planta 22/22, generador 14/14, V5 igual, paso7 14/14, seguridad 8/8, contrato 7/7, servidor 21/21, tipos 36/36.
- Decisiones: opción (a) aprobada, es decir, el método se declara en `ledmodel_v5.xml`. Los NodeIds del método en la instancia son de texto: Instancia.Metodo e Instancia.Metodo.InputArguments.

### 2026-11-08 · Fase B · Paso 4 · Importación con validación
- Qué: la validación del arranque pasa a `modelo.comprobar_variable`, que nombra la ruta y todo lo que falta (area y byte siempre; bit en Boolean) y luego delega en `Driver.validar_direccion`. `modelo.importar_nodeset(fichero, validar)` lee el NodeSet sin servidor (ModelUri, Address del espacio del modelo, recorrido desde Objects) y devuelve variables, métodos y todos los errores. El servidor usa `comprobar_variable` y `mensaje_modelo_incompleto`.
- Por qué: punto 4 del apartado 5. El servidor y la interfaz de la Fase C usan el mismo código.
- Ficheros: `modelo.py`, servidor, `test_faseB_importacion.py` (nuevo).
- Pruebas: test_faseB_importacion 8/8: el NodeSet generado y ledmodel_v5.xml dan la misma tabla; con 5 propiedades quitadas, salen los 4 errores esperados, tanto en la importación como en el arranque fallido del servidor; con el PLC emulado, la tabla del servidor coincide con la de la importación. Batería en verde: planta 22, generador 14, métodos 7, V5 igual, paso7 14, seguridad 8, contrato 7, servidor 21, tipos 36.
- Decisiones: un DataType que no es tipo básico llega como None y se rechaza con la lista de tipos soportados. El servidor importa `modelo.py`, así que ahora depende de PyYAML.

### 2026-11-08 · Fase B · Paso 5 · Coherencia de la planta
- Qué: `modelo.comprobar_coherencia` se ejecuta al cargar la planta. Es error que dos escrituras tengan la misma dirección o rangos solapados (por bits, por área y DB). Es aviso una escritura en Q, nombrando las salidas que comparten byte y recomendando una marca M. La línea de órdenes muestra los avisos, y con errores no genera el NodeSet.
- Por qué: punto 5 del apartado 5 (riesgo de RMW sobre Q0.x).
- Ficheros: `modelo.py`, `test_faseB_coherencia.py` (nuevo).
- Pruebas: test_faseB_coherencia 15/15. La célula da un único aviso, el de LedRojo/Value en Q0.3. Se detectan como error la escritura duplicada y los solapes BOOL/INT y BOOL/REAL; no lo son lectura+escritura sobre la misma dirección ni el mismo byte en otro DB. Con un NodeSet externo duplicado, el servidor arranca y avisa.
- Decisiones: aprobado que sea error en el generador y aviso en el servidor (el aviso del servidor sigue limitado a la misma dirección exacta). El área y el tipo ya los validan el paso 1 y el driver.

### 2026-11-08 · Fase B · Cierre
- Qué: `test_faseB_cierre.py` arranca el servidor con ledmodel_v5.xml y con el NodeSet generado desde planta_celula.yaml, y compara las tablas de mapeo (ruta, dirección, sentido), los métodos enlazados y los NodeIds de las variables Address y los métodos, leídos por el cliente. El modelo de referencia pasa a ser `modelo_celula.xml`, generado; ledmodel_v5.xml queda solo para regresión.
- Ficheros: `test_faseB_cierre.py` (nuevo), `modelo_celula.xml` (generado), cabecera del servidor.
- Pruebas: cierre 4/4 (10 variables iguales, ControlarPulsador ×1 en ambos, 11 NodeIds iguales). Batería completa en verde: planta 22, generador 14, métodos 7, importación 8, coherencia 15, cierre 4, V5 igual, paso7 14, seguridad 8, contrato 7, servidor 21, tipos 36.
- Pendiente: validación de la Fase B. Documentación: GUIA_DESPLIEGUE.md (modelo.py, YAML, `-m modelo_celula.xml`, contrato v1.1) y CLAUDE.md (las 6 pruebas test_faseB_* en la lista obligatoria).

### 2026-11-08 · Fase B · Validación y documentación
- Qué: GUIA_DESPLIEGUE.md cubre `modelo.py`, `modelo_celula.xml` y `planta_*.yaml` (tabla de ficheros y árbol de carpetas), PyYAML, `-m modelo_celula.xml` en todos los ejemplos, las 6 pruebas `test_faseB_*` y un apartado 13 nuevo (esquema del YAML, coherencia, contrato v1.1 y registro IMPLEMENTACIONES). CLAUDE.md añade las 6 pruebas a la lista obligatoria. Se crea `.gitignore`. Los logs de las pruebas se escriben ahora en `logs/` (cada prueba crea la carpeta) y se movieron allí los 15 existentes.
- Por qué: cierre de la Fase B, validada por el responsable; carpeta del servidor más ordenada.
- Ficheros: `GUIA_DESPLIEGUE.md`, `CLAUDE.md`, `.gitignore` (nuevo), 10 pruebas (ruta de los logs), `logs/`.
- Pruebas: batería completa en verde: planta 22, generador 14, métodos 7, importación 8, coherencia 15, cierre 4, V5 igual, paso7 14, seguridad 8, contrato 7, servidor 21, tipos 36. No queda ningún .log en la raíz.
- Decisiones: el nombre de un método es la clave global de IMPLEMENTACIONES; el mismo BrowseName en cualquier tipo usa la misma implementación. `.gitignore` excluye `modelo_*_faseA.xml`, `modelo_faseB_*.xml`, `planta_faseB_*.yaml`, `*.log`, `logs/`, `pki/`, `cli/` y `__pycache__/`; `modelo_celula.xml` sí se versiona (reglas comprobadas en un repositorio de prueba).
- Pendiente: Fase C, a la espera de que se indique empezarla.

### 2026-11-08 · Fase C · Paso 1 · Se retira la simulación interna
- Qué: se eliminan `--simulacion`, `SIMULACION` y `simular_planta`, el último código propio de la célula. Las escrituras, ControlarPulsador y la carga del modelo pasan siempre por el driver. `test_seguridad.py` arranca un PLC emulado, usa `modelo_celula.xml`, sale con código distinto de 0 si algo falla y comprueba que `--simulacion` se rechaza.
- Por qué: requisito 5 de la Fase C; sin PLC se usa el PLC emulado.
- Ficheros: servidor, `test_seguridad.py`, `GUIA_DESPLIEGUE.md` (se quita la fila de `--simulacion`).
- Pruebas: seguridad 9/9. Batería en verde: planta 22, generador 14, métodos 7, importación 8, coherencia 15, cierre 4, V5 igual, paso7 14, contrato 7, servidor 21, tipos 36.
- Decisiones (aprobadas antes de empezar): esquema de config.yaml; contrato v1.2 con PARAMETROS; `clave_hash` PBKDF2; parada por la orden "parar" en stdin y por fichero de control (cerrar la tubería solo avisa) con fichero de PID; editor YAML con validación inmediata; `--driver-param`, que es error si contradice a `--plc-ip`; rutas de la API relativas a la carpeta de trabajo; sesión en la interfaz.

### 2026-11-08 · Fase C · Paso 2 · Contrato del driver v1.2
- Qué: `driver_base.py` añade `ParametroDriver` (nombre, tipo texto/entero/real/bool/opcion, defecto, obligatorio, descripción, mínimo, máximo, opciones, avanzado), el atributo de clase `PARAMETROS = ()` y el método de clase `validar_parametros`, que convierte, aplica defectos y lista todos los errores a la vez. `PARAMETROS_COMUNES = ("fallos_max",)` los inyecta el servidor. DriverSnap7 declara ip, rack, slot, timeout_ms y max_hueco (este último, avanzado). `drivers.py` añade `clase_driver()` y `describir()`; `crear_driver` valida antes de instanciar.
- Por qué: requisito 2 de la Fase C; el formulario de la interfaz sale de la declaración del driver, sin código de Snap7.
- Ficheros: `driver_base.py`, `driver_snap7.py`, `drivers.py`, `test_faseC_contrato.py` (nuevo).
- Pruebas: test_faseC_contrato 12/12 (declaración, defectos, conversión de texto de formulario, errores múltiples, compatibilidad sin PARAMETROS, conexión al PLC emulado y lectura de I0.5). Batería en verde, con contrato A 7/7 sin cambios.
- Decisiones: los métodos nuevos no son abstractos, así que un driver sin declaración se crea como en v1.1.

### 2026-11-08 · Fase C · Paso 3 · Configuración YAML (--config)
- Qué: `configuracion.py` (bloques servidor/driver/enlace/modelo/seguridad/medida; defectos ← fichero ← línea de órdenes; rutas relativas a la carpeta del fichero; todos los errores juntos; `guardar`; `py configuracion.py --hash-clave` y `--validar`). El servidor tiene `crear_parser()` con defectos None, `--config`, `--driver-param` y `-m` opcional, y con origen: planta genera el NodeSet al arrancar. `seguridad.py` añade `hash_clave`, `verificar_clave` y `ClaveHash` (PBKDF2-SHA256, 600 000 iteraciones, `hmac.compare_digest`). RegistroCSV crea la carpeta del fichero.
- Por qué: punto 1 del apartado 6 y requisito 1 de la Fase C.
- Ficheros: `configuracion.py`, `config_celula.yaml`, `config_ejemplo_estricto.yaml` (nuevos), servidor, `seguridad.py`, `test_faseC_config.py` (nuevo).
- Pruebas: test_faseC_config 26/26. Toda opción de argparse tiene su clave. El servidor arranca solo con --config, para la célula y para el variador en estricto con planta y clave_hash, contra el PLC emulado. La línea de órdenes prevalece; --plc-ip contra --driver-param ip es error. Hay aviso de contraseña en claro. Una configuración inválida devuelve código 2. Batería en verde.
- Decisiones: `--usuario` no avisa (no queda guardado). `-m` fuerza origen: nodeset. hash_clave devuelve texto; la marca ClaveHash la pone `usuarios_de`.
- Pendiente: el contenido del CSV se comprueba en el paso 4, porque terminate() no deja volcarlo.

### 2026-11-08 · Fase C · Paso 4 · Parada ordenada sin señales
- Qué: nuevas opciones de proceso. `--parada-stdin`: la línea "parar" detiene; si la tubería se cierra, solo avisa y el servidor sigue. `--fichero-pid`: JSON con pid, inicio, endpoint, config y fichero de control, con latido (mtime) cada 1 s; se borra al terminar. `--fichero-control`: si aparece, se borra y el servidor se detiene; una orden de una ejecución anterior se descarta al arrancar. El bucle termina con `while not parada`, y el `finally` cierra CSV, driver y PID.
- Por qué: requisito 4 de la Fase C y cambio 1 aprobado. terminate() en Windows mata sin limpieza, y una caída de la interfaz no debe tumbar el servidor.
- Ficheros: servidor, `test_faseC_parada.py` (nuevo).
- Pruebas: test_faseC_parada 11/11 (código 0 en menos de 10 s; 1 sesión S7 durante la marcha y 0 tras la parada; CSV volcado; cerrar stdin no detiene; PID con latido; parada por fichero de control; orden antigua descartada). Batería en verde.
- Decisiones: la latencia de la parada es de un ciclo o un latido (≤ 1 s). No se usa wait_for en el sueño del ciclo para no alterar el periodo que se mide.

### 2026-11-08 · Fase C · Paso 5 · API de la interfaz y gestor del proceso
- Qué: `interfaz.py` (FastAPI y Uvicorn) con endpoints para drivers, ficheros, plantillas, configuración (leer, validar y guardar), modelo (validar, generar, importar y subir), servidor (estado, arrancar, detener, reiniciar) y log. `proceso.py` (GestorServidor) lanza el servidor con --config, --parada-stdin, --fichero-pid y --fichero-control y lo detiene de forma ordenada (fuerza la parada solo tras 10 s, y lo informa). Detecta un servidor de una interfaz anterior por PID vivo y latido < 5 s. `modelo.py` añade tabla_planta y tabla_importacion.
- Seguridad: escucha en 127.0.0.1 por defecto. En red exige --usuario (o variables de entorno) y avisa si no hay TLS. Sesión por cookie HttpOnly SameSite=Strict, con PBKDF2 solo en el login. Exige la cabecera X-Interfaz en las peticiones que modifican y comprueba Host en local. Rutas relativas a la carpeta de trabajo: sin absolutas ni "..", solo .xml/.yaml y nombres subidos saneados. Las contraseñas se guardan siempre como clave_hash, y GET /api/config no devuelve el hash.
- Ficheros: `interfaz.py`, `proceso.py`, `test_faseC_api.py` (nuevos), `modelo.py`, `requirements.txt` (fastapi 0.162.2, starlette 1.10.0, pydantic 2.14.3, pydantic-core 2.50.4, uvicorn 0.54.0, python-multipart 0.0.33; ruedas binarias).
- Pruebas: test_faseC_api 28/28 (rutas fuera de la carpeta rechazadas al importar, generar, subir y configurar; el servidor sobrevive a la caída de la interfaz y otra nueva lo detiene de forma ordenada; 0 sesiones S7; login y sesión). Batería en verde.

### 2026-11-08 · Fase C · Paso 6 · Página web
- Qué: `interfaz/index.html`, `app.js` y `estilo.css`, sin bibliotecas. Cuatro pestañas: Conexión (formulario generado desde PARAMETROS), Modelo (importar o subir un NodeSet, o editor YAML con validación a los 500 ms y tabla de mapeo, errores y avisos), Medida y Servidor (validar y guardar, arrancar, detener y reiniciar, estado y log cada segundo).
- Ficheros: `interfaz/` (nueva), `test_faseC_pagina.py` (nuevo).
- Pruebas: test_faseC_pagina 8/8 (sin URLs externas; ids y rutas de la API coherentes; sin nombres de snap7 en app.js; la página se sirve). Sin Node en la máquina, el JavaScript no se ejecuta en la prueba.
- Pendiente: comprobación manual en el navegador.

### 2026-11-08 · Fase C · Paso 7 · Cierre
- Qué: `test_faseC_cierre.py` parte de una carpeta de trabajo vacía y lo hace todo por la API de la interfaz, sin editar ficheros. Forma la conexión con los parámetros declarados por el driver, crea el modelo desde la plantilla de la célula (validar, generar, importar), guarda la configuración, arranca el servidor contra el PLC emulado y un cliente OPC UA lee I0.5, escribe Q0.3 y llama a ControlarPulsador. La parada es ordenada (código 0, 0 sesiones S7). Después, el servidor arranca solo con --config, sin la interfaz. Documentación: GUIA_DESPLIEGUE.md (ficheros, parámetros con su clave YAML, --config en la puesta en marcha, apartado 14 sobre configuración, campaña con origen: nodeset y NodeSet congelado, contrato v1.2, interfaz, parada y seguridad); CLAUDE.md (6 pruebas test_faseC_* en la lista obligatoria); `.gitignore`.
- Ficheros: `test_faseC_cierre.py` (nuevo), `GUIA_DESPLIEGUE.md`, `CLAUDE.md`, `.gitignore`.
- Pruebas: cierre 9/9. Batería completa en verde (18 pruebas): contrato C 12, config 26, parada 11, API 28, página 8, cierre C 9, planta 22, generador 14, métodos 7, importación 8, coherencia 15, cierre B 4, V5 igual, paso7 14, seguridad 9, contrato A 7, servidor 21, tipos 36.
- Pendiente: comprobación manual de la página en el navegador (el JavaScript no se ejecuta en las pruebas automáticas) y validación de la Fase C.

### 2026-11-08 · Fase C · Validación
- Qué: el responsable valida la Fase C. `.gitignore` excluye ya `prueba_faseC/` y ahora también `*.zip`. Comprobado en un repositorio de prueba: quedan fuera `prueba_faseC/`, los `.zip`, `logs/`, `servidor.pid`, `servidor.parar` y los NodeSet de las pruebas; se versionan `modelo_celula.xml`, `planta_celula.yaml`, `config_*.yaml` e `interfaz/`.
- Ficheros: `.gitignore`.
- Pruebas: no aplica (sin cambios de código). La última batería pasó entera (18 pruebas).

### 2026-11-08 · Cierre del plan de desarrollo
- Qué: terminadas las fases A (contrato de driver y servidor independiente del modelo), B (motor del modelo: descripción de planta, generador, métodos, importación y coherencia) y C (configuración YAML, parada ordenada e interfaz web local) de `PLAN_DESARROLLO_SERVIDOR.md`.
- Estado: el servidor arranca solo con `--config`; el modelo de referencia es `modelo_celula.xml`; contrato del driver v1.2; 18 pruebas contra el PLC emulado en la lista obligatoria de CLAUDE.md. La V5 etiquetada en git sigue como respaldo.
- Pendiente: congelar la versión que se medirá en el paper (decisión 4 y apartado 8 del plan), con `origen: nodeset`, y hacer la prueba de aceptación corta en la célula real (apartado 7).

### 2026-11-09 · Interoperabilidad · Preparación sin CTT
- Qué: (1) `ServerProfileArray` vacío por defecto; `--perfil` solo para un perfil verificado. (2) Lista de confianza de certificados de cliente en `pki/confianza` y `pki/rechazados` (activada por defecto en firmar/estricto; `seguridad.lista_confianza`, `--[no-]lista-confianza`, casilla y lista de rechazados de solo lectura en la interfaz); los rechazados se guardan como `<CN saneado> [<huella SHA-1>].der` y se aceptan moviéndolos, sin reiniciar. (3) Políticas Aes128-Sha256-RsaOaep y Aes256-Sha256-RsaPss junto a Basic256Sha256. (4) `INTEROPERABILIDAD.md` con procedimiento y tabla de resultados (todo «pendiente»).
- Ficheros: `seguridad.py`, `serverOPCUA_MetodoTFGV5_asyncua.py`, `configuracion.py`, `interfaz.py`, `interfaz/index.html`, `interfaz/app.js`, `config_celula.yaml`, `config_ejemplo_estricto.yaml`, `requirements.txt` (asyncua==2.0.1), `GUIA_DESPLIEGUE.md`, `INTEROPERABILIDAD.md`, `CLAUDE.md`, `.gitignore`; pruebas nuevas `test_interop_{perfil,confianza,politicas,doc}.py` y `util_interop.py`.
- Pruebas: batería completa, 22/22 (perfil 5/5, confianza 15/15, políticas 8/8, documento 5/5). `test_seguridad` y `test_faseC_config` ahora fijan el ApplicationUri del cliente y confían en su certificado.
- Decisiones: validación de vigencia, ApplicationUri, confianza y revocación (sin KeyUsage completo, que rechazaría clientes válidos); la lista también valida a un cliente None que envíe certificado; guía: el coste de la seguridad se medirá con una política común con el servidor embebido del S7-1200 (prevista Basic256Sha256, a confirmar en TIA Portal).
- Pendiente: ejecutar las pruebas con UaExpert, ConsoleReferenceClient y Prosys OPC UA Browser y rellenar la tabla.

### 2026-11-10 · Interoperabilidad · Motivo de los certificados rechazados
- Qué: cada rechazo guarda su motivo (no confiable, URI no válido, caducado, revocado) en `pki/rechazados/motivos.json` (huella → código) y lo escribe en el log, indicando si basta moverlo a `confianza/`; la API y la tabla de la interfaz añaden «Motivo» y «¿Se acepta moviéndolo?» (solo los no confiables).
- Ficheros: `seguridad.py`, `interfaz.py`, `interfaz/index.html`, `interfaz/app.js`, `GUIA_DESPLIEGUE.md`, `test_interop_confianza.py`.
- Pruebas: `test_interop_confianza` 23/23 (URI no válido de extremo a extremo; caducado con el validador real; revocado con validador simulado). Batería completa en verde (22/22).
- Decisiones: documentado en la guía no exigir nonRepudiation (KeyUsage completo de asyncua rechazaría clientes válidos).
- Nota: sin carpeta de CRL en el TrustStore, el motivo «revocado» no puede darse aún en la práctica.

### 2026-11-10 · Interoperabilidad · Revocación como limitación conocida
- Qué: la guía e `INTEROPERABILIDAD.md` ya no afirman que se compruebe la revocación; la documentan como limitación conocida (no se cargan CRL; para retirar un certificado basta con sacarlo de `pki/confianza`). Retirado «revocados» de la nota de la interfaz y corregido el comentario de `seguridad.py`.
- Comprobado: la tabla de rechazados se construye a partir de los `.der` presentes en `rechazados/`; las entradas de `motivos.json` sin fichero se ignoran y un `motivos.json` ilegible da motivo «desconocido» (no aceptable).
- Ficheros: `GUIA_DESPLIEGUE.md`, `INTEROPERABILIDAD.md`, `interfaz/index.html`, `seguridad.py` (comentario), `test_interop_confianza.py`.
- Pruebas: `test_interop_confianza` 26/26 (nuevas: entradas huérfanas, JSON ilegible, documentación sin afirmar revocación). Batería completa en verde (22/22).

### 2026-11-10 · Interoperabilidad · Cierre de la preparación sin CTT
- Qué: validada la preparación de interoperabilidad: perfil vacío por defecto, lista de confianza con motivo de cada rechazo (solo los no confiables se aceptan moviéndolos a `pki/confianza`), tres políticas seguras y `INTEROPERABILIDAD.md`.
- Decisiones: no se exige nonRepudiation; la revocación no se comprueba (limitación conocida, sin CRL: se retira un certificado sacándolo de `confianza/`).
- Pruebas: batería completa en verde (22/22); `test_interop_confianza` 26/26.
- Pendiente: pruebas con UaExpert, ConsoleReferenceClient y Prosys OPC UA Browser y rellenar la tabla de `INTEROPERABILIDAD.md`; congelar la versión del paper y prueba de aceptación en la célula real.

### 2026-11-10 · Interoperabilidad · Tercer cliente: opcua-commander
- Qué: Prosys OPC UA Browser no está disponible; se sustituye por opcua-commander (node-opcua, JavaScript), que mantiene una pila distinta de UaExpert (C++), ConsoleReferenceClient (.NET) y el servidor (asyncua).
- `INTEROPERABILIDAD.md`: columna renombrada «opcua-commander (node-opcua)», motivo del cambio, procedimiento (instalación con npm, conexión con `-e`, opciones de seguridad a confirmar con `--help`) y regla: política o función no ofrecida por el cliente = N/A. Todas sus casillas en «pendiente»; columna de UaExpert sin tocar.
- `test_interop_doc.py`: cabecera esperada actualizada al nuevo nombre de columna.
- Pruebas: no ejecutadas por indicación del usuario; las pruebas con opcua-commander las hace el usuario a mano (sin instalar Node.js aquí).

### 2026-11-10 · Interoperabilidad · Resultados de las pruebas manuales
- Qué: `INTEROPERABILIDAD.md` con los resultados de UaExpert, ConsoleReferenceClient y opcua-commander: tabla rellenada (UaExpert y opcua-commander OK en las 18 filas; ConsoleReferenceClient OK en 7 y N/A en 11), versiones y fecha, resumen (ningún FALLO, servidor sin modificar), notas de la columna ConsoleReferenceClient e independencia de las tres implementaciones.
- Limitación conocida de asyncua: escribir en un NodeId inexistente devuelve BadUserAccessDenied (leer, BadNodeIdUnknown); documentada en la sección 5.
- Pruebas nuevas: `test_interop_navegacion.py` (evidencia de ConsoleReferenceClient frente a `modelo_celula.xml`: 53/53 nodos de instancia; AccessLevel ausente = 1, valor por defecto del esquema) y `test_interop_nodo_inexistente.py`; añadidas a CLAUDE.md. Código del servidor sin cambios.
- Pruebas: `test_interop_navegacion` 3/3, `test_interop_doc` 5/5. `test_interop_nodo_inexistente` y la batería completa pendientes: puertos 4840 y 102 ocupados por la sesión de pruebas manuales.

### 2026-11-10 · Interoperabilidad · Cierre de las pruebas manuales
- Puertos 4840 y 102 libres: ejecutada `test_interop_nodo_inexistente` (6/6). asyncua devuelve BadUserAccessDenied como resultado de todo el servicio Write (no por nodo); la prueba lo captura así.
- Pruebas: batería completa en verde (24/24 ficheros). Código del servidor sin cambios.

### 2026-11-10 · Interoperabilidad · Ajuste de test_interop_nodo_inexistente
- Corregida la explicación: el rechazo de toda la operación Write solo ocurre con el usuario anónimo (sin permiso de escritura); con usuario autenticado el servicio responde Good y BadUserAccessDenied llega por nodo. La prueba distingue y comprueba ambos casos.
- Nuevo caso d: una sola petición Write (usuario autenticado) con LedRojo.Value y un NodeId inexistente: Good y BadUserAccessDenied por nodo; LedRojo llega al PLC (Q0.3 de 0 a 1). El caso c escribe False y comprueba Q0.3 = 0.
- Pruebas: `test_interop_nodo_inexistente` 8/8. Código del servidor sin cambios.

### 2026-11-10 · Verificación previa a la etiqueta v-paper
- Qué: batería completa de CLAUDE.md sobre el estado actual, antes de congelar la versión del paper. Puertos 4840 y 102 libres; código del servidor sin cambios desde la última validación.
- Resultado: 24/24 ficheros con código de salida 0 y ninguna comprobación en FALLO (306 comprobaciones con recuento, más `test_v5_plc_emulado`, pasos 0–5 correctos).
- Detalle: paso7_fallos 14, seguridad 9, faseA 7/21/36, faseB 22/14/7/8/15/4, faseC 12/26/11/28/8/9, interop perfil 5, confianza 26, políticas 8, doc 5, nodo_inexistente 8, navegación 3.
- Salidas en `logs/bateria_*.txt`. Pendiente: crear la etiqueta v-paper (a cargo del usuario).
