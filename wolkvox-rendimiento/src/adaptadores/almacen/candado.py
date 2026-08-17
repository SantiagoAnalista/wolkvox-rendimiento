"""Exclusión mutua entre corridas, sin depender del orquestador.

El token de Wolkvox no admite consumos en paralelo. Con ocho corridas
intradía más la diaria y la semanal, que dos se pisen deja de ser
hipotético — y puede pasar por Jenkins, por el Programador de tareas o
porque alguien lanzó una a mano.

Por eso el candado vive aquí y no en el Jenkinsfile: `main.py` se protege
solo, lo invoque quien lo invoque. Jenkins queda reducido a traer el
último cambio del repo y ejecutar el comando.

El archivo va en la carpeta compartida (la misma del tablero), que es el
único punto por el que pasan todas las corridas: un candado en el
workspace no serviría, porque cada job de Jenkins tiene el suyo.

Un candado huérfano —una corrida que murió sin soltarlo— caduca solo:
pasados `minutos_vigencia` la siguiente corrida lo toma. Sin eso, un
proceso muerto a las 3am dejaría la operación sin tablero hasta que
alguien lo notara.
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

NOMBRE = ".corrida.lock"
MINUTOS_VIGENCIA = 45          # el mismo tope que se le da a una corrida


class EnCurso(Exception):
    """Otra corrida tiene el candado y todavía está vigente."""


def _describir(archivo: Path) -> str:
    try:
        # lstrip del BOM: el candado lo escribe Python sin él, pero si alguien
        # lo crea a mano desde PowerShell aparece y ensucia el log.
        return archivo.read_text(encoding="utf-8").lstrip("﻿").strip() or "sin datos"
    except OSError:
        return "sin datos"


def _caduco(archivo: Path, minutos: int) -> bool:
    try:
        edad = (datetime.now().timestamp() - archivo.stat().st_mtime) / 60
    except OSError:
        return True            # desapareció entre medias: se puede tomar
    return edad > minutos


@contextmanager
def tomar(carpeta: Path, minutos_vigencia: int = MINUTOS_VIGENCIA):
    """Toma el candado mientras dure el bloque. Lanza EnCurso si otra
    corrida lo tiene y sigue vigente."""
    carpeta.mkdir(parents=True, exist_ok=True)
    archivo = carpeta / NOMBRE

    try:
        descriptor = os.open(archivo, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if not _caduco(archivo, minutos_vigencia):
            raise EnCurso(f"otra corrida en curso desde {_describir(archivo)}")
        log.warning("Candado vencido (%s). Se toma: la corrida anterior no lo soltó.",
                    _describir(archivo))
        try:
            archivo.unlink()
        except OSError:
            pass
        descriptor = os.open(archivo, os.O_CREAT | os.O_WRONLY)

    try:
        os.write(descriptor, f"pid {os.getpid()} · {datetime.now():%Y-%m-%d %H:%M:%S}"
                             .encode("utf-8"))
        os.close(descriptor)
        yield archivo
    finally:
        try:
            archivo.unlink()
        except OSError as e:
            log.warning("No se pudo soltar el candado %s: %s", archivo, e)
