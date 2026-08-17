"""Identidad de un asesor y días de la semana.

Vive en el dominio porque son reglas del negocio, no detalles de ningún
archivo: el mismo asesor llega escrito distinto desde la API, desde el Excel
de horarios y desde el YAML de la nómina, y decidir que son la misma persona
es una regla, no una conversión de formato.
"""
from __future__ import annotations

import re
import unicodedata

import pandas as pd

DIAS_SEMANA = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]


def normalizar(texto) -> str:
    """Sin tildes, sin espacios repetidos, en mayúsculas. Los nombres de
    agente del Excel y de la API difieren en espacios dobles y acentos."""
    if texto is None or (isinstance(texto, float) and pd.isna(texto)):
        return ""
    limpio = unicodedata.normalize("NFKD", str(texto))
    limpio = "".join(c for c in limpio if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", limpio).strip().upper()
