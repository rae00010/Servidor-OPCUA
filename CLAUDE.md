# Servidor OPC UA externo desacoplado

Servidor OPC UA (asyncua) que expone un PLC Siemens S7-1200 accediendo a su
memoria con Snap7. Proyecto de investigación de la Universidad de Jaén.

## Entorno
- Windows. Se ejecuta con `py`. Python 3.14, python-snap7 2.0.2 (no usar la API
  de la 3.x, es distinta), asyncua.
- Código, comentarios, mensajes de commit y documentación en castellano.

## Trabajo
- La tarea está en `docs/PLAN_DESARROLLO_SERVIDOR.md`. Se trabaja una fase cada vez.
  Al terminar una fase, parar y esperar validación antes de empezar la siguiente.
- Repositorio git desde el 10/11/2026; la etiqueta `pre-reorganizacion` es el
  primer punto de restauración: no reescribir historia. Mover ficheros con `git mv`.
- Todos los comandos se lanzan desde la raíz del proyecto.
- Estructura:
  - `servidor/`: `servidor_opcua.py` (nivel upstream), `driver_base.py` (contrato
    de driver), `driver_snap7.py`, `drivers.py` (registro y fábrica de drivers),
    `seguridad.py`, `modelo.py` (descripción de planta YAML, generador e
    importación de NodeSet2), `configuracion.py` (config YAML, `--config`),
    `interfaz.py`, `proceso.py` e `interfaz/` (interfaz web local). Se importan
    entre sí sin ruta.
  - `modelos/`: `planta_celula.yaml` y `modelo_celula.xml` (modelo de referencia,
    generado con `py servidor/modelo.py modelos/planta_celula.yaml -o
    modelos/modelo_celula.xml`), `planta_ejemplo_variador.yaml` y
    `ledmodel_v5.xml` (solo para regresión).
  - `config/`: `config_celula.yaml`, `config_ejemplo_estricto.yaml` (rutas
    relativas a `config/`).
  - `herramientas/`: `plc_emulado.py`, `cliente_medida.py`, `paso1_nodeset.py`.
  - `pruebas/`: `test_*.py`, `util_interop.py` y `_rutas.py`.
  - `docs/`: guías, `INTEROPERABILIDAD.md`, plan y bitácora. `evidencias/`:
    evidencias de las pruebas manuales.
  - Raíz: carpeta de trabajo del servidor y de la interfaz (`config.yaml`, `pki/`,
    `logs/`). `tmp/`: temporales de las pruebas (ignorada por git).
- Arranque: `py servidor/servidor_opcua.py --config config/config_celula.yaml`
  (o `-m modelos/modelo_celula.xml`), `py servidor/interfaz.py`,
  `py herramientas/plc_emulado.py`.
- `herramientas/paso1_nodeset.py` es solo de referencia (sus rutas son de otro
  entorno): no ejecutarlo; reutilizar sus funciones auxiliares si sirven.

## Pruebas
- Se lanzan desde la raíz con `py pruebas/<prueba>.py`. Cada prueba importa
  primero `_rutas`, que añade `servidor/` y `herramientas/` a la ruta de
  importación y fija `tmp/` como carpeta de trabajo: desde allí las fuentes se
  referencian como `../servidor`, `../modelos`, `../config`, `../docs`.
- Tras cada cambio deben seguir pasando: `test_v5_plc_emulado`,
  `test_paso7_fallos` (tarda ~1 min), `test_seguridad`,
  `test_faseA_contrato`, `test_faseA_servidor`, `test_faseA_tipos`,
  `test_faseB_planta`, `test_faseB_generador`, `test_faseB_metodos`,
  `test_faseB_importacion`, `test_faseB_coherencia`, `test_faseB_cierre`,
  `test_faseC_contrato`, `test_faseC_config`, `test_faseC_parada`,
  `test_faseC_api`, `test_faseC_pagina`, `test_faseC_cierre`,
  `test_interop_perfil`, `test_interop_confianza`, `test_interop_politicas`,
  `test_interop_doc`, `test_interop_nodo_inexistente`,
  `test_interop_navegacion` (interoperabilidad; auxiliar común `util_interop.py`).
- Los logs del servidor que generan las pruebas van a `tmp/logs/`.
- Usan un PLC emulado en el puerto 102; si falla al abrirlo, puede estar
  ocupado por TIA Portal o PLCSIM: avisar, no intentar cambiar el puerto.
- Para cada cambio nuevo, añadir su prueba contra el PLC emulado.

## Eficiencia (obligatorio)
- No leer `modelos/ledmodel_v5.xml` entero (75 KB): localizar con búsqueda lo necesario
  y leer solo ese tramo. Igual con ficheros largos: leer funciones concretas.
- No releer ficheros ya leídos salvo que hayan cambiado.
- Editar con cambios puntuales, no reescribir ficheros completos.
- Durante el desarrollo, ejecutar solo la prueba afectada; la batería completa,
  al cerrar cada paso. De la salida de las pruebas, quedarse con el resumen.
- Respuestas breves: qué se hizo, resultado de las pruebas y siguiente paso.
- Ante una decisión de diseño no prevista en el plan, preguntar antes de seguir.

## Bitácora
Al cerrar cada paso, añadir al final de `docs/BITACORA_DESARROLLO.md` una entrada con
el formato de ese fichero (máximo 10 líneas). Solo añadir: no releer ni reescribir
entradas anteriores.

# Compact instructions
Al compactar, conservar: fase y paso actuales, cambios de código hechos,
resultado de las últimas pruebas, decisiones tomadas y pendientes. Descartar la
exploración de ficheros y las salidas largas.
