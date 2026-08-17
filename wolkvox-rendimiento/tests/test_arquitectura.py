"""La arquitectura como prueba, no como acuerdo verbal.

Un layout hexagonal se degrada callado: alguien necesita leer un YAML desde
el dominio, lo importa, y seis meses después las reglas de negocio no se
pueden probar sin tocar disco. Estas pruebas fallan en el momento en que eso
pasa, que es cuando cuesta barato arreglarlo.

La regla es una sola: **las dependencias apuntan hacia adentro**.

    main.py  ->  aplicacion  ->  dominio
                     |
                     v
                adaptadores  ->  dominio
"""
import ast
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

DOMINIO = RAIZ / "src" / "dominio"
APLICACION = RAIZ / "src" / "aplicacion"
ADAPTADORES = RAIZ / "src" / "adaptadores"

# Todo lo que implica salir del proceso: red, disco, formatos de archivo.
LIBRERIAS_DE_IO = {"httpx", "yaml", "openpyxl", "requests", "sqlite3", "dotenv", "tenacity"}


def modulos(carpeta: Path) -> list[Path]:
    return sorted(p for p in carpeta.rglob("*.py") if p.name != "__init__.py")


def importados(archivo: Path) -> set[str]:
    """Nombres de módulo importados, ya sean absolutos o relativos."""
    arbol = ast.parse(archivo.read_text(encoding="utf-8"), filename=str(archivo))
    nombres = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            nombres.update(a.name for a in nodo.names)
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            nombres.add(("." * nodo.level) + nodo.module)
    return nombres


@pytest.mark.parametrize("archivo", modulos(DOMINIO), ids=lambda p: p.name)
def test_el_dominio_no_conoce_adaptadores_ni_configuracion(archivo):
    """El núcleo calcula; no sabe de dónde vienen los datos ni a dónde van."""
    prohibidos = [m for m in importados(archivo)
                  if m.startswith(("src.adaptadores", "src.aplicacion", "config"))]
    assert not prohibidos, f"{archivo.name} importa {prohibidos}"


@pytest.mark.parametrize("archivo", modulos(DOMINIO), ids=lambda p: p.name)
def test_el_dominio_no_hace_entrada_salida(archivo):
    """Sin red, sin disco, sin formatos de archivo: así las reglas de negocio
    se prueban con DataFrames en memoria y sin montar nada."""
    sucios = {m.split(".")[0] for m in importados(archivo)} & (LIBRERIAS_DE_IO | {"os"})
    assert not sucios, f"{archivo.name} importa {sorted(sucios)}"


@pytest.mark.parametrize("archivo", modulos(ADAPTADORES), ids=lambda p: p.name)
def test_los_adaptadores_no_dependen_de_los_casos_de_uso(archivo):
    """Un adaptador es intercambiable: si conociera el caso de uso, cambiar de
    Excel a otra salida obligaría a tocar la aplicación."""
    prohibidos = [m for m in importados(archivo) if m.startswith("src.aplicacion")]
    assert not prohibidos, f"{archivo.name} importa {prohibidos}"


def test_el_punto_de_entrada_no_calcula_nada():
    """main.py traduce la línea de comandos y nada más: sin pandas, sin
    reglas. Si necesita pandas es que se le metió lógica."""
    assert "pandas" not in importados(RAIZ / "main.py")


def test_la_plantilla_del_tablero_viaja_con_su_adaptador():
    """Si se mueve el módulo sin la plantilla, el fallo aparece en producción
    y no en las pruebas."""
    assert (ADAPTADORES / "publicacion" / "plantillas" / "tablero.html").exists()


def test_no_quedan_carpetas_de_la_estructura_anterior():
    assert not (RAIZ / "src" / "services").exists()
    assert not (RAIZ / "src" / "api").exists()
