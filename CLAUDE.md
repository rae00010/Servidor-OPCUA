# Servidor OPC UA externo desacoplado

Servidor OPC UA (asyncua) que expone un PLC Siemens S7-1200 accediendo a su
memoria con Snap7. Proyecto de investigación de la Universidad de Jaén.

## Entorno
- Windows. Se ejecuta con `py`. Python 3.14, python-snap7 2.0.2 (no usar la API
  de la 3.x, es distinta), asyncua.
- Código, comentarios, mensajes de commit y documentación en castellano.

## Trabajo
- La tarea está en `PLAN_DESARROLLO_SERVIDOR.md`. Se trabaja una fase cada vez.
  Al terminar una fase, parar y esperar validación antes de empezar la siguiente.
- La V5 etiquetada en git es la versión de respaldo: no reescribir historia.
- Ficheros principales: `serverOPCUA_MetodoTFGV5_asyncua.py` (nivel upstream),
  `driver_base.py` (contrato de driver), `driver_snap7.py`, `drivers.py`
  (registro y fábrica de drivers), `seguridad.py`, `modelo.py` (descripción de
  planta YAML, generador e importación de NodeSet2), `planta_celula.yaml` y
  `modelo_celula.xml` (modelo de referencia, generado con
  `py modelo.py planta_celula.yaml -o modelo_celula.xml`). `ledmodel_v5.xml`
  queda solo para regresión. Fase C: `configuracion.py` (config YAML,
  `--config`), `config_celula.yaml`, `interfaz.py`, `proceso.py` e `interfaz/`
  (interfaz web local).
- `paso1_nodeset.py` es solo de referencia (sus rutas son de otro entorno): no
  ejecutarlo; reutilizar sus funciones auxiliares si sirven.

## Pruebas
- Tras cada cambio deben seguir pasando: `py test_v5_plc_emulado.py`,
  `py test_paso7_fallos.py` (tarda ~1 min), `py test_seguridad.py`,
  `py test_faseA_contrato.py`, `py test_faseA_servidor.py`, `py test_faseA_tipos.py`,
  `py test_faseB_planta.py`, `py test_faseB_generador.py`, `py test_faseB_metodos.py`,
  `py test_faseB_importacion.py`, `py test_faseB_coherencia.py`, `py test_faseB_cierre.py`,
  `py test_faseC_contrato.py`, `py test_faseC_config.py`, `py test_faseC_parada.py`,
  `py test_faseC_api.py`, `py test_faseC_pagina.py`, `py test_faseC_cierre.py`,
  `py test_interop_perfil.py`, `py test_interop_confianza.py`, `py test_interop_politicas.py`,
  `py test_interop_doc.py`, `py test_interop_nodo_inexistente.py`,
  `py test_interop_navegacion.py` (interoperabilidad; auxiliar común `util_interop.py`).
- Los logs del servidor que generan las pruebas van a `logs/`.
- Usan un PLC emulado en el puerto 102; si falla al abrirlo, puede estar
  ocupado por TIA Portal o PLCSIM: avisar, no intentar cambiar el puerto.
- Para cada cambio nuevo, añadir su prueba contra el PLC emulado.

## Eficiencia (obligatorio)
- No leer `ledmodel_v5.xml` entero (75 KB): localizar con búsqueda lo necesario
  y leer solo ese tramo. Igual con ficheros largos: leer funciones concretas.
- No releer ficheros ya leídos salvo que hayan cambiado.
- Editar con cambios puntuales, no reescribir ficheros completos.
- Durante el desarrollo, ejecutar solo la prueba afectada; la batería completa,
  al cerrar cada paso. De la salida de las pruebas, quedarse con el resumen.
- Respuestas breves: qué se hizo, resultado de las pruebas y siguiente paso.
- Ante una decisión de diseño no prevista en el plan, preguntar antes de seguir.

## Bitácora
Al cerrar cada paso, añadir al final de `BITACORA_DESARROLLO.md` una entrada con
el formato de ese fichero (máximo 10 líneas). Solo añadir: no releer ni reescribir
entradas anteriores.

# Compact instructions
Al compactar, conservar: fase y paso actuales, cambios de código hechos,
resultado de las últimas pruebas, decisiones tomadas y pendientes. Descartar la
exploración de ficheros y las salidas largas.
