"""Lee los Excel de horarios de la operación y los convierte en un horario
por agente y fecha.

Los archivos vienen como calendario semanal: bloques de columnas con el
número de día, el nombre del día y la fila 'Horario Laboral' con textos tipo
'8 A 5' o '8:30 A 5:30'. Cada bloque cubre a un grupo de asesores.

El mes de cada hoja NO se asume por su nombre: se deduce probando meses
candidatos hasta que los nombres de día del archivo coincidan con el
calendario real. Si ninguno coincide, se lanza un error en vez de calcular
tardanzas contra un horario desalineado (que fue justo lo que delató que la
hoja 'Ajustados final 1 vuelta' era de junio, no de julio).
"""
from __future__ import annotations

import calendar
import re
import unicodedata
from datetime import date
from pathlib import Path

import pandas as pd

DIAS_SEMANA = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
FILA_HORARIO = "horario laboral"
FILA_ALMUERZO = "hora almuerzo"

# Anotaciones que anulan el día aunque la fila de horario traiga una jornada:
# en los archivos reales los festivos conservan el "8 a 5" de la plantilla.
# "En casa" NO entra aquí: es teletrabajo, o sea día laboral normal.
ANOTACIONES_NO_LABORAL = ("festivo", "descanso", "vacaciones", "incapacidad")


def normalizar(texto) -> str:
    """Sin tildes, sin espacios repetidos, en mayúsculas. Los nombres de
    agente del Excel y de la API difieren en espacios dobles y acentos."""
    if texto is None or (isinstance(texto, float) and pd.isna(texto)):
        return ""
    limpio = unicodedata.normalize("NFKD", str(texto))
    limpio = "".join(c for c in limpio if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", limpio).strip().upper()


def _hora(texto: str, es_fin: bool) -> str | None:
    """'8' -> 08:00. '5:30' -> 17:30 si es hora de salida.

    Las horas de salida de 1 a 6 se entienden como tarde (13:00-18:00);
    de 7 en adelante se toman literales, así '8 A 12' termina a mediodía.
    """
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?$", texto.strip())
    if not m:
        return None
    h, minutos = int(m.group(1)), int(m.group(2) or 0)
    if es_fin and 1 <= h <= 6:
        h += 12
    return f"{h:02d}:{minutos:02d}" if 0 <= h <= 23 else None


def _jornada(celda) -> tuple[str, str] | None:
    """'8 A 5:30' -> ('08:00', '17:30'). '0' o vacío -> None (no labora)."""
    if celda is None or (isinstance(celda, float) and pd.isna(celda)):
        return None
    texto = str(celda).strip()
    if texto in ("0", "0.0", "", "nan"):
        return None
    partes = re.split(r"\s+[aA]\s+", texto)
    if len(partes) != 2:
        return None
    ini, fin = _hora(partes[0], False), _hora(partes[1], True)
    return (ini, fin) if ini and fin else None


def _fechas_del_bloque(dias: list[tuple[int, str]], candidatos: list[tuple[int, int]]):
    """Asigna una fecha real a cada (día, nombre_de_día) del bloque.

    Prueba cada mes candidato y devuelve el primero cuyos nombres de día
    cuadren con el calendario. Un bloque puede cruzar de mes (…29, 30, 1, 2).
    """
    for anio, mes in candidatos:
        fechas, y, m, anterior = [], anio, mes, 0
        for numero, nombre in dias:
            if numero < anterior:                      # el bloque cruzó de mes
                m += 1
                if m > 12:
                    y, m = y + 1, 1
            anterior = numero
            if numero > calendar.monthrange(y, m)[1]:
                break
            fecha = date(y, m, numero)
            if nombre and DIAS_SEMANA[fecha.weekday()] != nombre:
                break
            fechas.append(fecha)
        else:
            return fechas
    return None


def _bloques(hoja: pd.DataFrame):
    """Ubica cada fila 'Horario Laboral' y el grupo al que pertenece."""
    grupo = None
    for i in range(len(hoja)):
        etiqueta = str(hoja.iloc[i, 0]).strip() if pd.notna(hoja.iloc[i, 0]) else ""
        clave = etiqueta.lower()
        if etiqueta and clave not in (FILA_HORARIO, FILA_ALMUERZO) and not clave.startswith(
                ("horas", "tiempo", "productividad")):
            grupo = normalizar(etiqueta)
        if clave == FILA_HORARIO and grupo:
            yield grupo, i


def leer_hoja(ruta: Path, hoja: str, candidatos: list[tuple[int, int]]) -> dict:
    """{grupo: {fecha: (inicio, fin) | None}} para una hoja."""
    datos = pd.read_excel(ruta, sheet_name=hoja, header=None)
    resultado: dict[str, dict[date, tuple[str, str] | None]] = {}

    for grupo, fila in _bloques(datos):
        numeros, nombres = datos.iloc[fila - 2], datos.iloc[fila - 1]
        # Fila de anotaciones (Festivo / DESCANSO / En casa), justo encima.
        anotaciones = datos.iloc[fila - 3] if fila >= 3 else None
        columnas, dias = [], []
        for col in range(datos.shape[1]):
            valor = numeros.iloc[col]
            if isinstance(valor, (int, float)) and pd.notna(valor) and 1 <= valor <= 31:
                columnas.append(col)
                dias.append((int(valor), normalizar(nombres.iloc[col]).lower()))
        if not dias:
            continue

        fechas = _fechas_del_bloque(dias, candidatos)
        if fechas is None or len(fechas) != len(dias):
            raise ValueError(
                f"'{ruta.name}' hoja '{hoja}', grupo {grupo}: los días del archivo no "
                f"coinciden con ningún mes candidato ({candidatos}). Revisar el archivo "
                f"o el periodo consultado antes de calcular tardanzas."
            )
        for col, fecha in zip(columnas, fechas):
            nota = normalizar(anotaciones.iloc[col]).lower() if anotaciones is not None else ""
            no_laboral = any(marca in nota for marca in ANOTACIONES_NO_LABORAL)
            resultado.setdefault(grupo, {})[fecha] = (
                None if no_laboral else _jornada(datos.iloc[fila, col]))

    return resultado


def cargar(archivos: list[dict], grupos: dict[str, list[str]],
           candidatos: list[tuple[int, int]], raiz: Path) -> dict:
    """Horario por agente y fecha: {(AGENTE, fecha): (inicio, fin) | None}.

    Un valor None significa día no laboral (descanso o festivo). Una fecha
    ausente significa que el archivo no cubre ese día.
    """
    por_grupo: dict[str, dict[date, tuple[str, str] | None]] = {}
    for archivo in archivos:
        ruta = raiz / archivo["ruta"]
        for hoja in archivo["hojas"]:
            for grupo, dias in leer_hoja(ruta, hoja, candidatos).items():
                por_grupo.setdefault(grupo, {}).update(dias)

    agenda: dict[tuple[str, date], tuple[str, str] | None] = {}
    for grupo, agentes in grupos.items():
        dias = por_grupo.get(normalizar(grupo))
        if dias is None:
            continue
        for agente in agentes:
            for fecha, jornada in dias.items():
                agenda[(normalizar(agente), fecha)] = jornada
    return agenda
