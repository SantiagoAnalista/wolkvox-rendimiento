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
    bloque_horas: int
    zona_horaria: str
    ruta_backup: Path
    dias_backup: int
    ruta_salida: Path
    ruta_compartida: str
    jornada_inicio: int
    jornada_fin: int


def cargar_config() -> Config:
    load_dotenv(ROOT_DIR / ".env")

    servidor = os.getenv("WOLKVOX_SERVER", "")
    token = os.getenv("WOLKVOX_TOKEN", "")

    ruta_backup = ROOT_DIR / os.getenv("RUTA_BACKUP", "src/data")
    ruta_salida = ROOT_DIR / os.getenv("RUTA_SALIDA", "src/output")
    ruta_backup.mkdir(parents=True, exist_ok=True)
    ruta_salida.mkdir(parents=True, exist_ok=True)

    return Config(
        servidor=servidor,
        token=token,
        timeout_seg=int(os.getenv("WOLKVOX_TIMEOUT_SEG", "90")),
        reintentos=int(os.getenv("WOLKVOX_REINTENTOS", "3")),
        bloque_horas=int(os.getenv("BLOQUE_HORAS", "6")),
        zona_horaria=os.getenv("ZONA_HORARIA", "America/Bogota"),
        ruta_backup=ruta_backup,
        dias_backup=int(os.getenv("DIAS_BACKUP", "3")),
        ruta_salida=ruta_salida,
        ruta_compartida=os.getenv("RUTA_COMPARTIDA", ""),
        jornada_inicio=int(os.getenv("JORNADA_INICIO", "6")),
        jornada_fin=int(os.getenv("JORNADA_FIN", "21")),
    )
