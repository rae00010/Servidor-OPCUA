# Guía de la prueba de aceptación en el laboratorio

Objetivo: comprobar, con el PLC S7-1200 real de la célula, que la versión del
servidor que se va a congelar como `v-paper` funciona igual que con el PLC
emulado. Si todo sale bien, al terminar se etiqueta esa versión. Si algo falla,
no se etiqueta: se anota y se corrige primero.

Todos los comandos se lanzan desde la raíz del proyecto, que es también la
carpeta de trabajo de la interfaz y del servidor (`config.yaml`, `pki/`,
`logs/`). La estructura de carpetas está en el apartado 2 de
`GUIA_DESPLIEGUE.md`.

## 0. Antes de ir

- La reorganización de carpetas está terminada y la batería completa pasa.
- Todo está confirmado en git (sin cambios pendientes) y sabes qué commit vas
  a probar: apúntalo con `git log -1 --format="%h"`.
- El portátil tiene instalado todo lo necesario: Python con las dependencias,
  UaExpert y, si vas a medir algo, el cliente de medida.
- Llevas un cable de red y, si el PLC está en un switch, acceso a él.
- Llevas esta guía y la tabla de resultados del apartado 10.

## 1. Seguridad física (leer antes de empezar)

El servidor escribe el LED rojo en Q0.3 mediante lectura, modificación y
escritura del byte Q0. Ese byte contiene también las electroválvulas de los
cilindros (Q0.0 a Q0.2). Existe un riesgo conocido: si el programa del PLC
cambia una de esas salidas justo durante la escritura del LED, el servidor
podría devolverle por un instante un valor anterior, y un cilindro podría
moverse de forma inesperada.

Por eso, durante las pruebas de escritura del LED:

- Los cilindros deben estar en reposo y nadie debe tener las manos en la
  zona de los cilindros.
- Si es posible, cierra el suministro de aire durante esas pruebas.

## 2. Preparación en el laboratorio

1. En TIA Portal, apunta para el paper el modelo de CPU y la versión de
   firmware (diagnóstico online de la CPU).
2. Comprueba en las propiedades de la CPU, en protección y seguridad, que el
   acceso por comunicación PUT/GET está permitido. Sin eso, Snap7 no puede
   acceder a la memoria.
3. Aprovecha para apuntar qué políticas de seguridad ofrece el servidor OPC UA
   embebido de esa CPU con ese firmware. Hace falta para decidir con qué
   política se medirá el coste de la seguridad en la campaña (la guía de
   despliegue tiene prevista Basic256Sha256, pero está sin confirmar).
4. La CPU debe estar en RUN. No cambies el programa del PLC.
5. Configura el portátil con una IP fija en la misma red que el PLC, por
   ejemplo 192.168.0.10 con máscara 255.255.255.0.
6. Comprueba la comunicación: `ping 192.168.0.1` debe responder.

## 3. Arranque desde la interfaz

1. Ejecuta `py servidor/interfaz.py` y abre `http://127.0.0.1:8080/`.
2. En la pestaña Conexión: snap7, IP 192.168.0.1, rack 0, slot 1. Seguridad en
   `none` para la primera prueba.
3. En la pestaña Modelo: importa `modelos/modelo_celula.xml` y comprueba que muestra
   las 10 variables y el método.
4. En la pestaña Servidor: guarda la configuración y arranca.
5. En el log en vivo, comprueba y apunta:
   - La PDU negociada. En un S7-1200 debería ser 240 bytes. Es un dato real
     que el emulador no da (allí era 480).
   - La tabla de mapeo con 10 variables.
   - El plan de lectura con 2 peticiones por ciclo.
   - Que no hay avisos de error.

## 4. Verificación funcional con UaExpert

Conecta UaExpert a `opc.tcp://localhost:4840` sin seguridad y arrastra las
variables a la vista de acceso a datos.

1. Sensores: cuando los cilindros se muevan, por el programa del PLC o por el
   cuadro de mando, Expandido y Retraido de cada cilindro deben seguirlos con
   calidad Good.
2. Pulsador: pulsa el pulsador verde físico. PulsadorVerde, Estado, Pulsado
   debe pasar a True mientras lo mantienes.
3. LED (con las precauciones del apartado 1): escribe True en LedRojo, Value.
   El piloto rojo debe encenderse. Escribe False y debe apagarse. Comprueba
   que ningún cilindro se ha movido.
4. Método: llama a ControlarPulsador con `pulsar` y después con `soltar`.
   Comprueba en una tabla de observación de TIA Portal que M0.0 pasa a 1 y a 0.
   Lo que ocurra físicamente depende de si el programa del PLC usa M0.0; lo
   que se comprueba aquí es que la orden llega a la marca.

## 5. Parada ordenada

En la pestaña Servidor, pulsa Detener. En el log debe aparecer el cierre de la
conexión S7 y del servidor OPC UA, y el estado debe indicar una parada normal,
no forzada.

## 6. Robustez de la interfaz

1. Arranca el servidor desde la interfaz y conecta UaExpert.
2. Cierra la consola de la interfaz. UaExpert debe seguir recibiendo datos.
3. Vuelve a ejecutar `py servidor/interfaz.py`, abre la página y comprueba que detecta
   el servidor en marcha y permite detenerlo de forma ordenada.

## 7. Arranque solo con la configuración

Con la interfaz cerrada:

```
py servidor/servidor_opcua.py --config config.yaml
```

Debe arrancar igual que desde la interfaz. Es como se usará en la campaña de
medidas. Mantenlo en marcha para el apartado siguiente.

## 8. Desconexión del cable

Es la prueba más importante de esta visita, porque el emulador no puede
reproducirla. Con UaExpert suscrito a CilindroA, Estado, Expandido:

1. Desconecta el cable de red del PLC y pon en marcha un cronómetro.
2. Apunta cuántos segundos tardan las variables en pasar a
   UncertainLastUsableValue. Con la configuración por defecto se espera en
   torno a 3 segundos (3 fallos seguidos con un tiempo límite de 1 segundo).
   La sesión de UaExpert no debe cerrarse.
3. Tras unos 10 segundos, vuelve a conectar el cable y apunta cuánto tarda en
   recuperarse la calidad Good, sin reconectar UaExpert.
4. Repite la prueba dos o tres veces y apunta todos los tiempos. No son
   todavía las medidas del paper, pero dan una primera idea para la campaña.

## 9. Seguridad (comprobación rápida)

Ya está probada a fondo con el emulador, así que basta una comprobación:
arranca el servidor con `--seguridad estricto --usuario rafa:clave123`
(`py servidor/servidor_opcua.py --config config.yaml --seguridad estricto --usuario rafa:clave123`),
conecta UaExpert con Basic256Sha256 y SignAndEncrypt (moviendo su certificado
a `pki/confianza/` si se rechaza) y comprueba que lee y escribe.

## 10. Registro de resultados

| Prueba | Resultado | Observaciones |
|---|---|---|
| Commit probado | | |
| CPU y firmware | | |
| Políticas del servidor embebido | | |
| PDU negociada | | |
| Arranque desde la interfaz | | |
| Sensores de los cilindros | | |
| Pulsador físico | | |
| Escritura del LED | | |
| Método ControlarPulsador (M0.0) | | |
| Parada ordenada | | |
| Robustez de la interfaz | | |
| Arranque con --config | | |
| Desconexión: tiempo hasta Uncertain | | |
| Desconexión: tiempo de recuperación | | |
| Seguridad estricta | | |

Guarda también el log de la pestaña Servidor (`logs/servidor.log`), el `config.yaml` usado y una
captura de UaExpert con las variables en Good.

## 11. Al terminar

- Si todo es correcto: de vuelta en el proyecto, añade los resultados a la
  bitácora y etiqueta la versión con `git tag v-paper` sobre el commit
  probado. A partir de ahí, el servidor no cambia hasta terminar la campaña.
- Si algo falla: no etiquetes. Apunta qué prueba falló, el mensaje del log y
  el código de estado si lo hay, y se corrige antes de congelar.
