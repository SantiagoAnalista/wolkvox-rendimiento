"""Carga la configuración desde variables de entorno (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .paths import ROOT_DIR


@dataclass(frozen=True)
class Config:
    servidor: str
    token: str
    timeout_seg: int
    reintentos: int
    ruta_backup: Path
    dias_backup: int
    ruta_salida: Path
    ruta_tablero: Path
    dias_excel: int
    ruta_publicacion: str


def cargar_config() -> Config:
    load_dotenv(ROOT_DIR / ".env")

    servidor = os.getenv("WOLKVOX_SERVER", "")
    token = os.getenv("WOLKVOX_TOKEN", "")

    ruta_backup = ROOT_DIR / os.getenv("RUTA_BACKUP", "src/data")
    ruta_salida = ROOT_DIR / os.getenv("RUTA_SALIDA", "src/output")
    ruta_backup.mkdir(parents=True, exist_ok=True)
    ruta_salida.mkdir(parents=True, exist_ok=True)

    # El tablero es un archivo único que acumula el histórico de TODOS los
    # periodos, así que su almacén no puede vivir en el workspace de Jenkins:
    # el job diario y el semanal tienen workspaces distintos y cada uno
    # publicaría un tablero con solo sus propios periodos. Apuntando
    # RUTA_TABLERO a la carpeta compartida, ambos jobs escriben en el mismo
    # histórico. No se hace mkdir aquí a propósito: si el recurso de red no
    # responde, debe fallar la publicación del tablero, no la carga de config.
    # `or` y no el default de getenv: en el .env la variable existe pero vacía,
    # y getenv devolvería "" (ROOT_DIR / "" es la raíz del repo, no src/output).
    ruta_tablero = ROOT_DIR / (os.getenv("RUTA_TABLERO") or os.getenv("RUTA_SALIDA") or "src/output")

    return Config(
        servidor=servidor,
        token=token,
        timeout_seg=int(os.getenv("WOLKVOX_TIMEOUT_SEG", "90")),
        reintentos=int(os.getenv("WOLKVOX_REINTENTOS", "3")),
        ruta_backup=ruta_backup,
        dias_backup=int(os.getenv("DIAS_BACKUP", "3")),
        ruta_salida=ruta_salida,
        ruta_tablero=ruta_tablero,
        # Días de maestras diarias que se conservan (hoy incluido), y hora a
        # partir de la cual una jornada se da por cerrada y sus cortes se
        # consolidan en un solo archivo.
        dias_excel=int(os.getenv("DIAS_EXCEL", "3")),
        # Carpeta de la operación donde se dejan el Excel y el tablero. Admite
        # {anio} y {mes}. Vacío = no se publica (útil en desarrollo).
        ruta_publicacion=os.getenv("RUTA_PUBLICACION", "").strip(),
    )
