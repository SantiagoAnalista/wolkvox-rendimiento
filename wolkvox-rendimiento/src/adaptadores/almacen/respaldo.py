"""Backup ligero de lo extraído, en CSV. Reemplaza a la base de datos:
conserva los últimos N días para poder auditar una cifra o reconstruir un
reporte sin volver a consultar la API.

Se guarda un CSV por dataset y día (`llamadas_2026-08-11.csv`), así que
reprocesar un día simplemente reescribe sus archivos: no hay duplicados que
deduplicar ni estado que corromper.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

PATRON = re.compile(r"^(?P<nombre>.+)_(?P<fecha>\d{4}-\d{2}-\d{2})\.csv$")


def guardar(dfs: dict[str, pd.DataFrame], fecha: str, ruta: Path) -> list[Path]:
    """Escribe un CSV por dataset. Sobrescribe el del mismo día."""
    ruta.mkdir(parents=True, exist_ok=True)
    escritos = []
    for nombre, df in dfs.items():
        if df.empty:
            continue
        destino = ruta / f"{nombre}_{fecha}.csv"
        df.to_csv(destino, index=False, encoding="utf-8-sig")
        escritos.append(destino)
    log.info("Backup: %d archivos en %s", len(escritos), ruta)
    return escritos


def cargar(nombre: str, fecha: str, ruta: Path) -> pd.DataFrame:
    """Relee un dataset ya respaldado. Devuelve vacío si no existe."""
    archivo = ruta / f"{nombre}_{fecha}.csv"
    if not archivo.exists():
        return pd.DataFrame()
    return pd.read_csv(archivo, dtype=str, keep_default_na=False)


def purgar(ruta: Path, dias: int = 3) -> list[Path]:
    """Borra los backups anteriores a los últimos `dias` días."""
    if not ruta.exists():
        return []
    vigentes = {
        (datetime.today() - timedelta(days=offset)).strftime("%Y-%m-%d")
        for offset in range(dias)
    }
    borrados = []
    for archivo in ruta.glob("*.csv"):
        coincidencia = PATRON.match(archivo.name)
        if coincidencia and coincidencia.group("fecha") not in vigentes:
            try:
                archivo.unlink()
                borrados.append(archivo)
            except OSError as e:
                log.warning("No se pudo borrar el backup %s: %s", archivo.name, e)
    if borrados:
        log.info("Backup: %d archivos purgados (>%d días)", len(borrados), dias)
    return borrados
