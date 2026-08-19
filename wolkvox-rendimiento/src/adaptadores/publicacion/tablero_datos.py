"""Almacén del tablero: un JSON por periodo en `src/output/tablero/`.

El tablero es un archivo único con selector, así que necesita tener a mano
todos los periodos, no solo el que acaba de correr. Cada corrida deja (o
reescribe) el JSON de SU periodo y el HTML se reconstruye leyendo la carpeta
completa.

Ese mismo archivo resuelve los deltas: para mostrar "vs. semana anterior"
basta buscar en el almacén el periodo previo del mismo tipo. No hace falta
guardar un resumen aparte ni volver a consultar la API.

Un JSON por periodo, y no uno global, tiene dos consecuencias que importan:
una corrida fallida solo puede dañar su propio periodo, y reprocesar una
semana la reescribe sin tocar el resto del histórico.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

CARPETA = "tablero"

# Cuántos periodos de cada tipo conserva el tablero. Acota el tamaño del HTML
# (cada periodo pesa entre 10 y 40 KB de JSON) sin perder la comparación
# interanual del mes.
RETENCION = {"dia": 21, "semana": 16, "mes": 13}

# Los cuadros que el tablero realmente dibuja. Se listan de forma explícita
# para no arrastrar al HTML hojas que solo tienen sentido en el Excel.
CUADROS_TABLERO = (
    "general_puntualidad",
    "general_gestion",
    "puntualidad_agente",
    "puntualidad_detalle",
    "tiempos",
    "efectividad",
    "cruce",
    "auxiliares",
    "curva_horaria",
    "tipificaciones",
    # Versión por día de los mismos cuadros: alimenta el filtro de fecha.
    "tiempos_dia",
    "cruce_dia",
    "auxiliares_dia_detalle",
    "curva_dia",
    "general_dia",
)

ETIQUETA_VALIDA = re.compile(r"^\d{4}-(\d{2}|S\d{2}|\d{2}-\d{2})$")


def _registros(df: pd.DataFrame | None) -> list[dict]:
    """DataFrame -> lista de dicts serializable.

    NaN/NaT pasan a None: `json.dumps` escribiría `NaN`, que no es JSON válido
    y rompe el `JSON.parse` del navegador.
    """
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return []
    return json.loads(df.to_json(orient="records", date_format="iso"))


def _indicadores(df: pd.DataFrame | None) -> dict:
    """Los cuadros 'Indicador | Valor' se aplanan a un dict para que el HTML
    los lea por nombre en vez de por posición."""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return {}
    if not {"Indicador", "Valor"} <= set(df.columns):
        return {}
    return {str(k).strip(): v for k, v in zip(df["Indicador"], df["Valor"])}


def construir(cuadros: dict[str, pd.DataFrame], metadatos: dict, etiqueta: str,
              periodo: str, desde, hasta, umbrales: dict,
              tolerancia_min: int, corte: str | None = None,
              archivo_excel: str | None = None) -> dict:
    """Arma el payload de un periodo a partir de los MISMOS cuadros que
    alimentan el Excel. Esta función no calcula indicadores: solo selecciona,
    aplana y serializa. Cualquier cifra nueva se agrega en gestion.py o
    asistencia.py para que ambas salidas la compartan."""
    kpis = {**_indicadores(cuadros.get("general_puntualidad")),
            **_indicadores(cuadros.get("general_gestion"))}

    payload = {
        "etiqueta": etiqueta,
        "periodo": periodo,
        "desde": desde.isoformat(),
        "hasta": hasta.isoformat(),
        "titulo": metadatos.get("Periodo", etiqueta),
        "generado": metadatos.get("Generado", ""),
        # `corte` marca una jornada en curso: el tablero rotula la hora del
        # corte para que nadie lea una foto de mediodía como dato en vivo.
        "corte": corte,
        "parcial": bool(corte),
        "archivo_excel": archivo_excel,
        "umbrales": {
            "tolerancia_min": tolerancia_min,
            "auxiliar_alto": umbrales.get("auxiliar_alto", 30),
            "sin_tipificar_alto": umbrales.get("sin_tipificar_alto", 30),
            "efectividad_baja": umbrales.get("efectividad_baja", 10),
            "ready_alto": umbrales.get("ready_alto", 50),
            "entradas_tarde_alto": umbrales.get("entradas_tarde_alto", 10),
            "tarde_grave_min": umbrales.get("tarde_grave_min", 15),
        },
        "kpis": kpis,
        "cuadros": {n: _registros(cuadros.get(n)) for n in CUADROS_TABLERO},
    }
    return payload


def ruta_store(ruta_salida: Path) -> Path:
    return ruta_salida / CARPETA


def guardar(payload: dict, ruta_salida: Path) -> Path:
    """Escribe el JSON del periodo de forma atómica."""
    destino_dir = ruta_store(ruta_salida)
    destino_dir.mkdir(parents=True, exist_ok=True)
    destino = destino_dir / f"{payload['etiqueta']}.json"

    temporal = destino.with_suffix(".json.tmp")
    temporal.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temporal, destino)     # atómico: nunca deja un JSON a medias
    log.info("Tablero: periodo %s guardado (%d KB)", payload["etiqueta"],
             destino.stat().st_size // 1024)
    return destino


def _tipo(etiqueta: str) -> str:
    if "S" in etiqueta:
        return "semana"
    return "dia" if etiqueta.count("-") == 2 else "mes"


def cargar_todos(ruta_salida: Path, retencion: dict | None = None) -> list[dict]:
    """Lee el almacén completo, aplica retención por tipo y devuelve los
    periodos ordenados del más reciente al más antiguo.

    Un JSON corrupto se descarta con un aviso en el log en vez de tumbar la
    corrida: perder un periodo del histórico es mucho menos grave que no
    publicar el tablero.
    """
    carpeta = ruta_store(ruta_salida)
    if not carpeta.exists():
        return []

    retencion = retencion or RETENCION
    periodos = []
    for archivo in carpeta.glob("*.json"):
        if not ETIQUETA_VALIDA.match(archivo.stem):
            continue
        try:
            periodos.append(json.loads(archivo.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Tablero: se ignora %s (%s)", archivo.name, e)

    periodos.sort(key=lambda p: (p.get("desde", ""), p.get("etiqueta", "")), reverse=True)

    conservados, vistos = [], {}
    for p in periodos:
        tipo = p.get("periodo") or _tipo(p.get("etiqueta", ""))
        vistos[tipo] = vistos.get(tipo, 0) + 1
        if vistos[tipo] <= retencion.get(tipo, 12):
            conservados.append(p)
    return conservados


def purgar(ruta_salida: Path, retencion: dict | None = None) -> list[Path]:
    """Borra del almacén los periodos que ya no entran en la retención."""
    carpeta = ruta_store(ruta_salida)
    if not carpeta.exists():
        return []

    vigentes = {p["etiqueta"] for p in cargar_todos(ruta_salida, retencion)}
    borrados = []
    for archivo in carpeta.glob("*.json"):
        if ETIQUETA_VALIDA.match(archivo.stem) and archivo.stem not in vigentes:
            try:
                archivo.unlink()
                borrados.append(archivo)
            except OSError as e:
                log.warning("Tablero: no se pudo borrar %s: %s", archivo.name, e)
    if borrados:
        log.info("Tablero: %d periodo(s) fuera de retención", len(borrados))
    return borrados
