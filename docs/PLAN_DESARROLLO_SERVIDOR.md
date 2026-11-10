# Plan de desarrollo del servidor OPC UA como estándar desacoplado (v4)

Instrucciones para programar lo planteado: consolidar el estándar del driver
aguas abajo, la interfaz para seleccionar el protocolo y la interfaz de gestión
del modelo de información. Por ahora el único protocolo es Snap7. Beckhoff queda
fuera de alcance; el diseño se prepara para que añadirlo después requiera el
menor trabajo posible, con las salvedades que se indican en el apartado 9.

Este documento es autocontenido: todas las decisiones de diseño están recogidas en el apartado 10. Se trabaja una fase cada vez y no se empieza la siguiente sin la validación del responsable del proyecto.

## 1. Objetivo y alcance

Convertir el servidor actual (V5) en un estándar desacoplado con una interfaz de
usuario desde la que el ingeniero pueda, sin editar ficheros a mano: seleccionar
el protocolo aguas abajo (hoy solo Snap7), configurar la conexión con el PLC y la
seguridad, importar o crear el modelo de información, y arrancar el servidor.
Fuera de alcance por ahora: el driver de Beckhoff (ADS) y la agregación de varios
autómatas.

## 2. Estado de partida

Ya existen la interfaz abstracta de driver en `driver_base.py`, la implementación
Snap7 en `driver_snap7.py`, el servidor en `serverOPCUA_MetodoTFGV5_asyncua.py`,
el modelo `ledmodel_v5.xml` con el tipo Address ampliado con el área de memoria,
la seguridad, la reconexión, la lectura agrupada y el periodo fijo.

Corrección respecto a la versión anterior: `paso1_nodeset.py` no genera un
NodeSet desde cero, sino que transforma uno existente (lee `ledmodel.xml` y le
añade propiedades). Sus funciones auxiliares de creación de nodos sí se pueden
reutilizar, pero el generador de la Fase B es trabajo nuevo, no una
generalización directa.

## 3. Arquitectura objetivo

Tres piezas nuevas sobre lo existente. Un registro de drivers que asocia cada
nombre de protocolo con su implementación. Un motor de modelo que importa un
NodeSet existente o lo genera a partir de una descripción de la planta. Y una
interfaz de usuario que reúne selección de protocolo, configuración y gestión del
modelo.

La interfaz se ejecuta como un proceso separado del servidor OPC UA y lo arranca y
detiene como subproceso. Así, un fallo de la interfaz no tumba el servidor, y la
carga de la interfaz no perturba el ciclo de adquisición. Esto es importante para
el paper: durante la campaña de medidas el servidor se lanza sin interfaz, desde
el fichero de configuración, para que las medidas no incluyan su efecto.

## 4. Fase A. Estándar del driver y servidor independiente del modelo

Pasos a programar:

1. Fijar `driver_base.py` como contrato público del estándar, con una cabecera que
   documente los métodos que todo driver implementa (conectar, desconectar,
   conectado, validar_direccion, texto_direccion, planificar_lectura, leer,
   escribir) y los tipos que maneja.
2. Crear `drivers.py` con el registro `DRIVERS = {"snap7": DriverSnap7}` y una
   función fábrica. La fábrica recibe el nombre del protocolo y un diccionario de
   parámetros específicos de ese driver, porque el constructor actual
   (ip, rack, slot) es propio de S7 y otro protocolo tendrá parámetros distintos.
   Con un nombre desconocido debe dar un error claro que liste los disponibles.
3. Sustituir en el servidor la instanciación fija de `DriverSnap7` por la fábrica,
   con snap7 por defecto para no cambiar el comportamiento actual.
4. Eliminar los identificadores fijados en el código. Hoy el servidor tiene
   escritos `NAMESPACE_URI = "urn:miservidor:ledRojo"`, `ADDRESS_TYPE_ID = 2003` y
   `TPULSADOR_TYPE_ID = 1014`. Si no se cambia esto, cualquier modelo generado o
   importado en la Fase B que no use exactamente esos valores romperá el
   servidor. El servidor debe tomar el espacio de nombres del propio NodeSet
   cargado y localizar el tipo Address y los tipos con métodos por su BrowseName
   dentro de ese espacio de nombres. Este paso es imprescindible para la Fase B.

5. Ampliar el driver Snap7 con tipos de dato más allá del booleano, porque el
   generador será genérico (decisión 2). Como mínimo BOOL, BYTE, WORD, INT, DINT
   y REAL, con su conversión de orden de bytes de S7. `validar_direccion` debe
   aceptarlos, la lectura agrupada debe extraer cada tipo del buffer con su
   tamaño, y la escritura debe codificarlos. En los tipos de varios bytes no se
   usa el Read-Modify-Write de bit: se escribe el dato completo.

Criterio de aceptación: el servidor arranca seleccionando el driver por nombre,
funciona con un NodeSet que use otra URI y otros NodeIds numéricos, lee y
escribe correctamente variables de cada tipo soportado contra el PLC emulado, y
las pruebas existentes siguen pasando.

## 5. Fase B. Motor del modelo de información

1. Definir una descripción de planta independiente de OPC UA, en YAML (decisión
   3). Debe ser genérica (decisión 2): además de los tipos de dispositivo de la
   célula (pulsador, led, cilindros), permitir declarar tipos de dispositivo
   nuevos y variables de cualquier tipo de dato soportado, con su área, byte,
   bit cuando proceda y sentido de acceso.
2. Programar en `modelo.py` el generador que produce desde cero un NodeSet2 bien
   formado a partir de esa descripción: tipo Address con área, ObjectTypes
   (también los definidos por el usuario), instancias con sus propiedades,
   DataType y AccessLevel correctos. Es el estándar de creación del NodeSet.
3. Declarar los métodos en el modelo. Hoy `ControlarPulsador` se añade por
   código al arrancar, fuera del NodeSet, y se enlaza a las instancias del tipo
   TPulsador. Lo coherente con un estándar es que el generador declare el método
   (con sus argumentos) en el NodeSet y que el servidor solo enlace su
   implementación a los métodos que encuentre en el modelo.
4. Programar la importación con validación: comprobar que las variables Address
   declaran área, byte y bit, y avisar con claridad de lo que falte. Extraer para
   ello la validación que el servidor ya hace al arrancar, de modo que servidor e
   interfaz usen el mismo código.
5. Comprobación de coherencia: dos variables de escritura sobre la misma
   dirección, áreas válidas, tipos soportados. Además, el generador debe avisar
   cuando una variable escribible apunte directamente a las salidas Q. Motivo:
   la escritura de bits sueltos se hace leyendo el byte, modificando el bit y
   reescribiendo el byte, y si el programa del PLC escribe otros bits de ese
   mismo byte (por ejemplo, el LED en Q0.3 comparte byte con las electroválvulas
   Q0.0 a Q0.2) el servidor puede devolverles un valor obsoleto. Lo correcto es
   que las órdenes externas vayan a un área de marcas reservada para la
   comunicación, que el programa del PLC copia a las salidas. Cambiar el
   programa del PLC queda fuera de este plan; el generador solo avisa.

Criterio de aceptación: a partir de la descripción de la célula actual, el
generador produce un NodeSet con el que el servidor construye la misma tabla de
mapeo que con `ledmodel_v5.xml` (mismas direcciones, mismos sentidos y mismos
métodos). No se exige que el XML sea idéntico byte a byte.

## 6. Fase C. Interfaz de usuario

1. Fichero de configuración único en YAML con protocolo y sus parámetros,
   seguridad, endpoint y origen del modelo. El servidor debe arrancar de él con un solo
   parámetro, manteniendo los parámetros sueltos actuales.
2. Interfaz web local (decisión 1), servida por un proceso propio, por ejemplo
   con FastAPI, y accesible desde el navegador. Debe permitir elegir el
   protocolo (hoy solo Snap7), rellenar conexión y
   seguridad, importar o crear el modelo, validar y arrancar o detener el
   servidor viendo su registro. La interfaz no implementa lógica de servidor ni de
   modelo: llama a la fábrica de la Fase A y al motor de la Fase B.
3. Cambiar el modelo implica reiniciar el servidor. La interfaz lo hace parando y
   relanzando el subproceso; la recarga en caliente queda fuera de alcance.
4. Seguridad de la propia interfaz: como permite reconfigurar y arrancar el
   servidor, por defecto debe escuchar solo en la máquina local; si se expone en
   red, con autenticación. Las contraseñas de usuarios OPC UA no deben guardarse
   en claro en el fichero de configuración sin advertirlo.

Criterio de aceptación: desde la interfaz, sin editar ficheros a mano, el
ingeniero configura la conexión, importa o crea el modelo y arranca el servidor
contra la célula.

## 7. Pruebas

Cada fase se prueba primero contra el PLC emulado y después con una prueba de
aceptación corta en la célula. La V5 actual se conserva como respaldo hasta que
cada fase supere sus pruebas.

## 8. Relación con la campaña de medidas del paper

La versión que se medirá en el paper es el servidor estándar final, una vez
completadas las fases A, B y C (decisión 4). Esto tiene tres consecuencias. La
primera es de calendario: la campaña de laboratorio no empieza hasta que las tres
fases hayan superado sus pruebas, incluida la aceptación en la célula. La segunda
es que esa versión final se congela y se etiqueta antes de medir, y es la que
describe la Sección 4 del paper; ningún cambio posterior entra en la campaña. La
tercera es que el servidor se mide sin la interfaz web en marcha, arrancado desde
el fichero de configuración, para que la interfaz no perturbe las medidas; la
interfaz se describe en el paper como herramienta de despliegue, no como parte
del sistema medido.

Mientras se desarrollan las fases, sí pueden prepararse en paralelo las partes de
la campaña que no dependen del servidor: la configuración del servidor embebido
en TIA Portal, el cableado del bucle físico y el contador de eventos en el PLC.

## 9. Preparación para Beckhoff (sin programarlo)

La interfaz de driver ya permite añadir otro protocolo sin tocar el servidor. Sin
embargo, el direccionamiento del modelo (área I/Q/M/DB, byte y bit) sigue el
esquema de S7 e IEC 61131-3, mientras que Beckhoff ADS se direcciona normalmente
por nombre simbólico o por grupo e índice. Cuando se aborde Beckhoff habrá que
ampliar el tipo Address, por ejemplo con una propiedad opcional de símbolo que
interprete su driver. Es una ampliación del modelo, no una reescritura del
servidor, pero no es correcto decir que no habrá que tocar nada.

## 10. Decisiones tomadas

1. Interfaz: web local, porque funciona en cualquier sistema.
2. Generador de modelo: genérico, preparado para tipos de dispositivo definidos
   por el usuario y para variables de cualquier tipo soportado, no solo los de la
   célula actual. Implica ampliar el driver con más tipos de dato (Fase A, paso 5).
3. Formato de la descripción de planta y de la configuración: YAML, por
   legibilidad. Añade la dependencia PyYAML a `requirements.txt`.
4. Versión medida en el paper: el servidor estándar final, tras las fases A, B y
   C, congelado antes de la campaña (ver apartado 8).

## 11. Dependencias nuevas

PyYAML para la configuración y la descripción de planta, y el marco web de la
interfaz (por ejemplo FastAPI con su servidor Uvicorn). Ambas se añadirán a
`requirements.txt` con versión fijada al empezar cada fase.
