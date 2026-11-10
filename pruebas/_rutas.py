"""
Rutas comunes de las pruebas. Cada prueba lo importa antes que nada:
  - Añade servidor/ y herramientas/ a la ruta de importación.
  - Fija tmp/ (en la raíz, ignorada por git) como carpeta de trabajo: los ficheros
    temporales de las pruebas (modelos generados, carpetas de prueba, pki y
    certificados de cliente de prueba, logs) quedan allí. Las fuentes se
    referencian desde tmp/ como ../servidor, ../modelos, ../docs, ../evidencias.
Así las pruebas se lanzan desde la raíz con  py pruebas/test_x.py  (o desde
cualquier otra carpeta).
"""
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
TMP = RAIZ / "tmp"
for _d in (RAIZ / "servidor", RAIZ / "herramientas"):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))
TMP.mkdir(exist_ok=True)
os.chdir(TMP)
