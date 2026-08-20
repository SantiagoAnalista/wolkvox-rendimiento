"""Copia el Excel y el tablero a la carpeta de la operación.

La ruta admite `{anio}` y `{mes}`, que se resuelven contra el periodo del
informe:

    \\\\nassoporte\\credintegral\\CARTERA\\{anio}\\{mes}\\Infomes de Gestion
    ->  ...\\CARTERA\\2026\\08. Agosto\\Infomes de Gestion

Se hace así y no con la ruta escrita a mano porque el 1 de septiembre esa
ruta fija seguiría apuntando a agosto, y nadie lo notaría en días. El formato
`MM. Mes` está tomado de la propia carpeta, donde los doce meses ya siguen
esa convención.

Una ruta sin marcadores se usa tal cual, para el caso en que la operación
prefiera un destino fijo.

En el destino se conserva UNA sola versión de cada periodo: antes de copiar
se borran los archivos del mismo periodo que ya estuvieran ahí. Los de otros
periodos no se tocan — el informe del día no puede llevarse por delante el de
la semana.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import time
from datetime import date
from pathlib import Path

from . import retencion
from .excel_analisis import PREFIJO

log = logging.getLogger(__name__)

MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
         "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]

INTENTOS = 3
ESPERA_SEG = 3


def ruta_destino(plantilla: str, fecha: date) -> Path:
    """Resuelve {anio} y {mes} contra la fecha del periodo."""
    return Path(plantilla.format(anio=f"{fecha:%Y}",
                                 mes=f"{fecha:%m}. {MESES[fecha.month - 1]}"))


def _copiar(origen: Path, destino: Path) -> None:
    """Copia con reintentos: un recurso de red puede estar ocupado un instante.

    Se escribe a un temporal y se reemplaza, para que nadie abra el archivo a
    medio copiar desde la carpeta compartida.
    """
    temporal = destino.with_suffix(destino.suffix + ".tmp")
    for intento in range(1, INTENTOS + 1):
        try:
            shutil.copyfile(origen, temporal)
            os.replace(temporal, destino)
            return
        except OSError as e:
            if intento == INTENTOS:
                raise
            log.warning("Publicación: %s ocupado (%s). Reintento %d de %d en %ds",
                        destino.name, e, intento + 1, INTENTOS, ESPERA_SEG)
            time.sleep(ESPERA_SEG)


def _del_mismo_periodo(carpeta: Path, etiqueta: str) -> list[Path]:
    """Archivos ya publicados de ese periodo, incluidos los cortes intradía.

    Se compara con expresión regular y no con comodín a propósito: el patrón
    `gestion_wolkvox_2026-08*` casaría también con `2026-08-18_1431`, y el
    informe mensual borraría los diarios.
    """
    patron = re.compile(rf"^{PREFIJO}_{re.escape(etiqueta)}(_\d{{4}})?\.xlsx$")
    return [a for a in carpeta.glob(f"{PREFIJO}_*.xlsx")
            if patron.match(a.name) and not a.name.startswith("~$")]


def publicar_excel(excel: Path, etiqueta: str, plantilla: str, fecha: date,
                   dias_diarios: int = 0) -> Path:
    """Deja en la carpeta la única versión vigente de ese periodo.

    `dias_diarios` limita cuántos informes DIARIOS se conservan ahí. Los
    semanales y los mensuales no caducan: la carpeta está organizada por mes y
    ese es justamente su archivo.
    """
    carpeta = ruta_destino(plantilla, fecha)
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / excel.name

    # Aquí también: con el nombre viejo, ni el reemplazo por periodo ni la
    # purga de diarios los ven, y se acumularían sin caducar nunca.
    retencion.migrar_prefijo(carpeta)
    _copiar(excel, destino)
    for viejo in _del_mismo_periodo(carpeta, etiqueta):
        if viejo == destino:
            continue
        try:
            viejo.unlink()
            log.info("Publicación: reemplazado %s", viejo.name)
        except OSError as e:
            # Lo más probable: alguien lo tiene abierto en Excel. El archivo
            # sobrante es inofensivo y la siguiente corrida lo reintenta.
            log.warning("Publicación: no se pudo borrar %s (%s)", viejo.name, e)
    log.info("Publicación: %s -> %s", excel.name, carpeta)

    if dias_diarios:
        _purgar_diarios(carpeta, dias_diarios, conservar=destino)
    return destino


DIARIO = re.compile(rf"^{PREFIJO}_(\d{{4}}-\d{{2}}-\d{{2}})(_\d{{4}})?\.xlsx$")


def _purgar_diarios(carpeta: Path, maximo: int, conservar: Path) -> list[Path]:
    """Conserva los `maximo` informes diarios más recientes de la carpeta.

    Se cuentan FECHAS, no archivos, y se ordenan por la fecha del nombre y no
    por la de modificación: reprocesar un día viejo no debe colarlo como si
    fuera el más nuevo. Los semanales y mensuales ni se miran.
    """
    por_fecha: dict[str, list[Path]] = {}
    for archivo in carpeta.glob(f"{PREFIJO}_*.xlsx"):
        m = DIARIO.match(archivo.name)
        if m and not archivo.name.startswith("~$"):
            por_fecha.setdefault(m.group(1), []).append(archivo)

    sobran = sorted(por_fecha, reverse=True)[maximo:]
    borrados = []
    for fecha in sobran:
        for archivo in por_fecha[fecha]:
            if archivo == conservar:
                continue
            try:
                archivo.unlink()
                borrados.append(archivo)
            except OSError as e:
                log.warning("Publicación: no se pudo borrar %s (%s)", archivo.name, e)
    if borrados:
        log.info("Publicación: %d diario(s) fuera de los últimos %d días",
                 len(borrados), maximo)
    return borrados


def publicar_tablero(tablero: Path, plantilla: str, fecha: date) -> Path:
    """El tablero siempre se reemplaza: es un archivo único con su histórico."""
    carpeta = ruta_destino(plantilla, fecha)
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / tablero.name
    _copiar(tablero, destino)
    log.info("Publicación: %s -> %s", tablero.name, carpeta)
    return destino
