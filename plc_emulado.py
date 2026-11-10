"""
PLC S7 emulado con la celula de validacion, para probar el servidor sin hardware.

Expone las areas I, Q y M por el protocolo S7 en 127.0.0.1:102 (snap7.server)
y simula la planta:
    CilindroA (doble efecto): Q0.0 expandir, Q0.1 contraer
                              -> sensores I0.5 expandido, I0.4 retraido
    CilindroB (simple efecto): Q0.2 expandir, retorno por muelle
                              -> sensores I0.7 expandido, I0.6 retraido
    PulsadorVerde: I0.0 (se pulsa desde la consola)
    LedRojo: Q0.3 (lo escribe el servidor OPC UA)
Los cilindros tardan TIEMPO_CARRERA segundos en llegar al final de carrera.

Como no se conoce aun el programa real del PLC, las salidas de los cilindros
se activan desde la consola (en la celula las activa el programa del PLC).

Ordenes de consola:
    a+  a-      CilindroA expandir / contraer
    b+  b-      CilindroB expandir / soltar
    p           pulsar y soltar PulsadorVerde (I0.0 durante 1 s)
    s           mostrar el estado de I, Q y M
    q           salir

Uso:  py plc_emulado.py
"""

import ctypes
import importlib.metadata as md
import threading
import time

import snap7
from snap7.type import SrvArea

TIEMPO_CARRERA = 0.5
VERSION = md.version("python-snap7")

srv = snap7.server.Server()
areas = {}
for a in (SrvArea.PE, SrvArea.PA, SrvArea.MK):
    # python-snap7 2.x comparte los buffers ctypes; 3.x necesita bytearray
    buf = bytearray(8) if VERSION.startswith("3") else (ctypes.c_ubyte * 8)()
    srv.register_area(a, 0, buf)
    areas[a] = buf
I, Q, M = areas[SrvArea.PE], areas[SrvArea.PA], areas[SrvArea.MK]


def get(buf, byte, bit):
    return bool(buf[byte] >> bit & 1)


def put(buf, byte, bit, valor):
    buf[byte] = (buf[byte] | (1 << bit)) if valor else (buf[byte] & ~(1 << bit) & 0xFF)


def planta():
    """Cinematica de los cilindros, evaluada cada 10 ms."""
    pos_a, pos_b = 0.0, 0.0              # 0 = retraido, 1 = expandido
    paso = 0.01 / TIEMPO_CARRERA
    while True:
        if get(Q, 0, 0) and not get(Q, 0, 1):
            pos_a = min(1.0, pos_a + paso)
        elif get(Q, 0, 1) and not get(Q, 0, 0):
            pos_a = max(0.0, pos_a - paso)
        pos_b = min(1.0, pos_b + paso) if get(Q, 0, 2) else max(0.0, pos_b - paso)
        put(I, 0, 5, pos_a >= 1.0); put(I, 0, 4, pos_a <= 0.0)
        put(I, 0, 7, pos_b >= 1.0); put(I, 0, 6, pos_b <= 0.0)
        time.sleep(0.01)


def estado():
    f = lambda b: " ".join(f"{b[i]:08b}" for i in range(2))
    print(f"  I0..1: {f(I)}   Q0..1: {f(Q)}   M0..1: {f(M)}")
    print(f"  CilindroA exp={get(I,0,5)} ret={get(I,0,4)} | CilindroB exp={get(I,0,7)} "
          f"ret={get(I,0,6)} | LedRojo(Q0.3)={get(Q,0,3)} | PulsadoRemoto(M0.0)={get(M,0,0)}")


if VERSION.startswith("3"):
    srv.port = 102
srv.start_to("127.0.0.1", 102)
threading.Thread(target=planta, daemon=True).start()
print(f"PLC emulado escuchando en 127.0.0.1:102 (python-snap7 {VERSION})")
print("Ordenes: a+ a- b+ b- p s q")
estado()

ordenes = {"a+": lambda: (put(Q, 0, 1, 0), put(Q, 0, 0, 1)),
           "a-": lambda: (put(Q, 0, 0, 0), put(Q, 0, 1, 1)),
           "b+": lambda: put(Q, 0, 2, 1),
           "b-": lambda: put(Q, 0, 2, 0)}
try:
    while True:
        o = input("> ").strip().lower()
        if o == "q":
            break
        if o in ordenes:
            ordenes[o]()
        elif o == "p":
            put(I, 0, 0, 1)
            threading.Timer(1.0, put, (I, 0, 0, 0)).start()
        elif o != "s":
            print("Ordenes: a+ a- b+ b- p s q")
            continue
        time.sleep(TIEMPO_CARRERA + 0.05) if o in ordenes else None
        estado()
finally:
    srv.stop()
