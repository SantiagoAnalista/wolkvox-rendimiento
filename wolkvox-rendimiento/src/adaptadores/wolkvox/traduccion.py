"""Normaliza los registros crudos de la API a DataFrames y deriva la
categoría de negocio. No sabe nada de HTTP ni de Excel.

Los campos crudos de Wolkvox se conservan siempre; las columnas derivadas
(`*_seg`, `ocupacion`, `categoria_negocio`) se agregan al lado.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml

from config.paths import ROOT_DIR


# Campos que Wolkvox entrega como texto de tiempo y pasamos a segundos.
TIEMPOS = ["ready_time", "inbound_time", "outbound_time", "acw_time",
           "ring_time", "login_time", "aux_time", "aht", "aht_inbound", "aht_outbund"]
CONTEOS = ["calls", "inbound", "outbound", "internal", "hits", "rpc"]


def hhmmss_a_segundos(valor) -> int:
    """Wolkvox documenta los tiempos como String pero no confirma el formato
    exacto (los ejemplos de la colección de Postman vienen vacíos). Se admite
    'HH:MM:SS', 'MM:SS' o un número ya en segundos; cualquier otra cosa cae a
    0 sin romper la corrida.

    ⚠️ Confirmar el formato real contra la primera corrida con datos reales.
    """
    if valor is None or valor == "" or (isinstance(valor, float) and pd.isna(valor)):
        return 0
    if isinstance(valor, (int, float)):
        return int(valor)
    texto = str(valor).strip()
    if ":" not in texto:
        try:
            return int(float(texto))
        except ValueError:
            return 0
    try:
        numeros = [int(float(p)) for p in texto.split(":")]
    except ValueError:
        return 0
    if len(numeros) == 3:
        h, m, s = numeros
        return h * 3600 + m * 60 + s
    if len(numeros) == 2:
        m, s = numeros
        return m * 60 + s
    return numeros[0]


def _df(registros: list[dict], columnas: list[str]) -> pd.DataFrame:
    """DataFrame con las columnas garantizadas y en orden estable, aunque la
    API omita alguna o agregue una nueva (las nuevas se conservan al final)."""
    df = pd.DataFrame(registros)
    for col in columnas:
        if col not in df.columns:
            df[col] = ""
    extras = [c for c in df.columns if c not in columnas]
    return df[columnas + extras]


def _segundos(df: pd.DataFrame) -> pd.DataFrame:
    """Agrega una columna '<campo>_seg' por cada campo de tiempo presente."""
    for col in TIEMPOS:
        if col in df.columns:
            df[f"{col}_seg"] = df[col].map(hhmmss_a_segundos)
    return df


def _enteros(df: pd.DataFrame) -> pd.DataFrame:
    for col in CONTEOS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    return df


def _ocupacion(df: pd.DataFrame) -> pd.Series:
    """Ocupación en porcentaje (0-100), tomada del campo 'occupancy' que ya
    calcula Wolkvox y que llega como texto: '34.64 %'.

    Se usa el número del proveedor en vez de recalcularlo para que el reporte
    cuadre con el Manager por construcción. Su fórmula, verificada contra
    datos reales, es:
        (inbound_time + outbound_time + acw_time) / (login_time - aux_time)
    """
    limpio = df["occupancy"].astype(str).str.replace("%", "", regex=False).str.strip()
    return pd.to_numeric(limpio, errors="coerce").fillna(0.0).astype(float)


def _fecha_y_hora(df: pd.DataFrame) -> pd.DataFrame:
    """Parte el campo 'date' de la API en 'fecha' (YYYY-MM-DD) y 'hora' (0-23)."""
    momento = pd.to_datetime(df["date"], errors="coerce")
    df["fecha"] = momento.dt.strftime("%Y-%m-%d")
    df["hora"] = momento.dt.hour.fillna(0).astype(int)
    return df


def cargar_categorias(ruta: Path | None = None) -> dict:
    with open(ruta or (ROOT_DIR / "categorias.yaml"), "r", encoding="utf-8") as f:
        crudo = yaml.safe_load(f) or {}
    return {
        "conectadas": {str(k).strip().upper(): v for k, v in (crudo.get("conectadas") or {}).items()},
        "no_conectadas": {str(k).strip().lower(): v for k, v in (crudo.get("no_conectadas") or {}).items()},
    }


def agentes(registros: list[dict]) -> pd.DataFrame:
    """information.php?api=agents — catálogo de agentes."""
    cols = ["agent_id", "agent_name", "agent_dni", "agent_status", "last_use", "agent_sso"]
    return _df(registros, cols)[cols]


def agente_dia(registros: list[dict], fecha: str) -> pd.DataFrame:
    """agent_1 — resumen por agente. El endpoint no devuelve fecha: la
    asignamos según la ventana consultada."""
    cols = ["agent_id", "agent_name", "agent_dni", *CONTEOS, *TIEMPOS,
            "occupancy", "login", "logout"]
    df = _enteros(_segundos(_df(registros, cols)))
    if df.empty:
        return df
    df["fecha"] = fecha
    df["ocupacion"] = _ocupacion(df)
    return df


def agente_hora(registros: list[dict], fecha: str) -> pd.DataFrame:
    """agent_8 — mismos indicadores, desglosados hora a hora."""
    cols = ["agent_id", "agent_name", "agent_dni", "date", "hour", *CONTEOS, *TIEMPOS, "occupancy"]
    df = _enteros(_segundos(_df(registros, cols)))
    if df.empty:
        return df
    df["fecha"] = df["date"].replace("", pd.NA).fillna(fecha)
    df["hora"] = pd.to_numeric(df["hour"], errors="coerce").fillna(0).astype(int)
    df["ocupacion"] = _ocupacion(df)
    return df


def llamadas(registros: list[dict], categorias: dict) -> pd.DataFrame:
    """cdr_1 — llamadas conectadas. La categoría de negocio sale del código
    de tipificación que el agente le asigna a la llamada."""
    cols = ["conn_id", "date", "agent_id", "agent_name", "campaign_id", "skill_id", "skill_name",
            "type_interaction", "destiny", "telephone", "customer_id", "time_seg", "cost",
            "cod_act", "description_cod_act", "cod_act_2", "description_cod_act_2",
            "hang_up", "comment"]
    df = _df(registros, cols)
    if df.empty:
        return df
    df = _fecha_y_hora(df)
    df["time_seg"] = pd.to_numeric(df["time_seg"], errors="coerce").fillna(0).astype(int)
    df["categoria_negocio"] = (
        df["cod_act"].astype(str).str.strip().str.upper()
        .map(categorias.get("conectadas", {})).fillna("sin_clasificar")
    )
    return df


def codigos_tipificacion(registros: list[dict]) -> pd.DataFrame:
    """activity_codes — inventario con las banderas hit/rpc/voice/chat que la
    operación ya tiene configuradas en Wolkvox."""
    cols = ["cod_act", "description_cod_act", "hit", "rpc", "chat", "voice",
            "type_code", "interactions"]
    return _df(registros, cols)[cols]


def logueo_por_dia(registros: list[dict]) -> pd.DataFrame:
    """agent_7 — login/logout por agente y día."""
    cols = ["agent_id", "agent_name", "agent_dni", "date", "login", "logout", "login_time"]
    df = _segundos(_df(registros, cols))
    if df.empty:
        return df
    df["fecha"] = df["date"].astype(str).str.strip()
    return df


def tiempo_auxiliar(registros: list[dict]) -> pd.DataFrame:
    """agent_3 — tiempo por tipo de estado auxiliar.

    Conserva la columna 'fecha' si la extracción se hizo día por día.
    """
    cols = ["agent_id", "agent_name", "agent_dni", "aux_state", "time"]
    if registros and "fecha" in registros[0]:
        cols.append("fecha")
    df = _df(registros, cols)
    if df.empty:
        return df
    df["aux_state"] = df["aux_state"].astype(str).str.strip()
    df["time_seg"] = df["time"].map(hhmmss_a_segundos)
    return df


def chats_productividad(registros: list[dict]) -> pd.DataFrame:
    """chat_16 — productividad de canales digitales por agente.

    ⚠️ 'time' y 'tmo' miden la vida de la conversación (desde que se abre
    hasta que se cierra), no el tiempo que el agente estuvo gestionándola:
    por eso el TMO da valores de ~23 horas. No es comparable con el AHT de
    llamadas y por eso el reporte usa 'total_chats' como medida principal.
    """
    cols = ["agent_id", "agent_name", "total_chats", "time", "tmo", "percent_transfer"]
    df = _df(registros, cols)
    if df.empty:
        return df
    df["total_chats"] = pd.to_numeric(df["total_chats"], errors="coerce").fillna(0).astype(int)
    df["tmo_seg"] = df["tmo"].map(hhmmss_a_segundos)
    return df


def chats_detalle(registros: list[dict]) -> pd.DataFrame:
    """chat_1 — una fila por conversación digital (WhatsApp, web, etc.).

    ⚠️ 'chat_duration' mide desde que se abre la conversación hasta que se
    cierra, y las que nadie tipifica se cierran por timeout a las ~23 horas.
    No es tiempo de gestión del agente: para eso está 'time_on_agent' y
    'agent_average_response_time'.
    """
    cols = ["conn_id", "channel", "date", "date_close", "agent_id", "agent_name",
            "cod_act", "description_cod_act", "skill_id", "customer_id",
            "time_on_agent", "chat_duration", "agent_average_response_time", "transfer"]
    df = _df(registros, cols)
    if df.empty:
        return df
    df = _fecha_y_hora(df)
    df["time_on_agent_seg"] = df["time_on_agent"].map(hhmmss_a_segundos)
    df["chat_duration_seg"] = df["chat_duration"].map(hhmmss_a_segundos)
    return df


def llamadas_no_conectadas(registros: list[dict], categorias: dict) -> pd.DataFrame:
    """cdr_5 — intentos que no conectaron. La categoría sale del resultado
    técnico que reporta la red."""
    cols = ["conn_id", "date", "agent_id", "agent_name", "campaign_id", "type_interaction",
            "destiny", "telephone", "customer_id", "ring_time", "result"]
    df = _df(registros, cols)
    if df.empty:
        return df
    df = _segundos(_fecha_y_hora(df))
    df["categoria_negocio"] = (
        df["result"].astype(str).str.strip().str.lower()
        .map(categorias.get("no_conectadas", {})).fillna("sin_clasificar")
    )
    return df
