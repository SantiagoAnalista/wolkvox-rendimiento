"""Tiempos por estado, efectividad de la gestión y el cruce entre ambos.

La efectividad no se inventa: Wolkvox ya marca cada código de tipificación
con las banderas hit/rpc en `information.php?api=activity_codes`, que es lo
que la propia operación configuró. Una gestión es efectiva si su código está
marcado como `hit`.
"""
from __future__ import annotations

import pandas as pd

from .horarios_excel import normalizar


def filtrar_agentes(df: pd.DataFrame, incluidos: list[str], columna: str = "agent_name") -> pd.DataFrame:
    """Deja solo a los asesores del informe. Se compara por nombre
    normalizado: la API trae tildes y espacios dobles que el YAML no."""
    if df.empty or not incluidos or columna not in df.columns:
        return df
    return df[df[columna].map(normalizar).isin(incluidos)]

ESTADOS = {
    "En llamada": "llamada_seg",
    "ACW": "acw_time_seg",
    "Ready": "ready_time_seg",
    "Auxiliar": "aux_time_seg",
    "Ring": "ring_time_seg",
}


def _hhmmss(segundos) -> str:
    s = int(segundos or 0)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _horas(segundos) -> float:
    """Horas decimales. A diferencia del texto, esto queda como número en el
    Excel: se puede sumar, ordenar y graficar."""
    return round((segundos or 0) / 3600, 2)


def _horas_min(segundos) -> str:
    """'41h 07m'. Es el formato que se lee sin traducir mentalmente.

    Se redondea al minuto más cercano, no se trunca: en la vista día a día
    truncar perdía ~30 s por fila y, sumando el mes, el detalle quedaba diez
    minutos por debajo de su propio total.
    """
    minutos = round((segundos or 0) / 60)
    return f"{minutos // 60}h {minutos % 60:02d}m"


def _pct(parte, total) -> float:
    return round(parte / total * 100, 1) if total else 0.0


def mapa_codigos(df_codigos: pd.DataFrame) -> dict[str, dict[str, bool]]:
    """cod_act (normalizado) -> {'hit': bool, 'rpc': bool}, según lo que la
    operación ya tiene configurado en Wolkvox.

    - hit: la gestión cumplió su objetivo (acuerdo de pago, venta...).
    - rpc: se habló con la persona correcta (hubo contacto real).
    """
    if df_codigos.empty:
        return {}
    df = df_codigos[df_codigos["hit"].astype(str).str.strip() != ""]
    return {
        str(c).strip().upper(): {"hit": str(h).strip().lower() == "yes",
                                 "rpc": str(r).strip().lower() == "yes"}
        for c, h, r in zip(df["cod_act"], df["hit"], df["rpc"])
    }


def _normalizar(serie: pd.Series) -> pd.Series:
    return serie.astype(str).str.strip().str.upper()


def _sin_tipificar(serie: pd.Series) -> pd.Series:
    """Gestión que el agente nunca codificó: vacía o cerrada por timeout
    (TIMEOUTACW en voz, TIMEOUTCHAT en digital)."""
    cod = _normalizar(serie)
    return (cod == "") | (cod == "NAN") | cod.str.startswith("TIMEOUT")


def _bandera(serie: pd.Series, codigos: dict, cual: str) -> pd.Series:
    return _normalizar(serie).map(lambda c: codigos.get(c, {}).get(cual, False))


def tiempos_por_agente(df_agente: pd.DataFrame,
                       dias_trabajados: dict[tuple[str, str], int] | None = None) -> pd.DataFrame:
    """Informe 2: totales por estado y periodo, con porcentajes y promedios.

    `dias_trabajados` se indexa por (periodo, agente) para que el promedio
    diario se calcule contra los días de ESE periodo.
    """
    if df_agente.empty:
        return pd.DataFrame()

    df = df_agente.copy()
    df["llamada_seg"] = df["inbound_time_seg"] + df["outbound_time_seg"]

    filas = []
    for (periodo, agente), grupo in df.groupby(["Periodo", "agent_name"], sort=True):
        logueado = grupo["login_time_seg"].sum()
        dias = (dias_trabajados or {}).get((periodo, agente), 0)
        fila = {
            "Periodo": periodo,
            "Agente": agente,
            "Días trabajados": dias,
            "Logueado": _hhmmss(logueado),
            "Llamadas": int(grupo["calls"].sum()),
        }
        for etiqueta, col in ESTADOS.items():
            total = grupo[col].sum()
            fila[etiqueta] = _hhmmss(total)
            fila[f"% {etiqueta}"] = _pct(total, logueado)
            fila[f"Prom. día {etiqueta}"] = _hhmmss(total / dias) if dias else ""
        fila["Ocupación %"] = round(grupo["ocupacion"].mean(), 1)
        fila["Prom. día logueado"] = _hhmmss(logueado / dias) if dias else ""
        filas.append(fila)

    return pd.DataFrame(filas).sort_values(["Periodo", "Agente"]).reset_index(drop=True)


def auxiliares_por_tipo(df_aux: pd.DataFrame) -> pd.DataFrame:
    """Desglose del tiempo auxiliar por tipo de pausa (agent_3), una fila por
    agente y estado. Responde 'mucho tiempo en auxiliares' diciendo en cuál.

    Se reporta en horas: 'Horas' es el número con el que se puede operar en
    Excel y 'Tiempo' el mismo dato en HH:MM:SS para leerlo exacto.
    """
    if df_aux.empty:
        return pd.DataFrame()

    tabla = (df_aux.groupby(["Periodo", "agent_name", "aux_state"], as_index=False)["time_seg"].sum()
                   .rename(columns={"agent_name": "Agente", "aux_state": "Estado auxiliar"}))
    tabla["Duración"] = tabla["time_seg"].map(_horas_min)
    tabla["Horas"] = tabla["time_seg"].map(_horas)
    return (tabla.sort_values(["Periodo", "Agente", "time_seg"], ascending=[True, True, False])
                 [["Periodo", "Agente", "Estado auxiliar", "Duración", "Horas"]]
                 .reset_index(drop=True))


def _pivote_auxiliares(df_aux: pd.DataFrame, formato: str,
                       indice: list[str] | None = None) -> pd.DataFrame:
    """Tabla cruzada: estados auxiliares en columnas, ordenados por peso
    total, así el que más tiempo consume queda en la primera columna.

    `formato` = 'hm' ('41h 07m', para leer) o 'decimal' (número, para operar).
    `indice` permite abrir por agente, o por agente y fecha.
    """
    if df_aux.empty:
        return pd.DataFrame()

    indice = indice or ["agent_name"]
    base = df_aux.groupby(indice + ["aux_state"], as_index=False)["time_seg"].sum()
    orden = (base.groupby("aux_state")["time_seg"].sum()
                 .sort_values(ascending=False).index.tolist())
    tabla = (base.pivot(index=indice, columns="aux_state", values="time_seg")
                 .reindex(columns=orden).fillna(0))

    if formato == "hm":
        salida = tabla.map(_horas_min)
        salida["TOTAL auxiliar"] = tabla.sum(axis=1).map(_horas_min)
    else:
        # El total se suma sobre las horas YA redondeadas: si se redondeara
        # aparte, las columnas de la fila no cuadrarían con su propio total.
        salida = tabla.map(_horas)
        salida["TOTAL auxiliar"] = salida.sum(axis=1).round(2)

    salida = salida.reset_index().rename(columns={"agent_name": "Agente", "fecha": "Fecha"})
    return salida.sort_values([c for c in ("Agente", "Fecha") if c in salida.columns]).reset_index(drop=True)


def auxiliares_hm(df_aux: pd.DataFrame) -> pd.DataFrame:
    """Vista cruzada en horas y minutos ('41h 07m')."""
    return _pivote_auxiliares(df_aux, "hm")


def auxiliares_horas(df_aux: pd.DataFrame) -> pd.DataFrame:
    """La misma vista en horas decimales, numérica y operable en Excel."""
    return _pivote_auxiliares(df_aux, "decimal")


def auxiliares_dia_a_dia(df_aux_dia: pd.DataFrame) -> pd.DataFrame:
    """Día a día: una fila por asesor y fecha, con sus estados auxiliares.

    Requiere el auxiliar extraído día por día (agent_3 no desglosa por fecha
    dentro del rango consultado, así que se consulta un día a la vez).
    """
    if df_aux_dia.empty or "fecha" not in df_aux_dia.columns:
        return pd.DataFrame()
    return _pivote_auxiliares(df_aux_dia, "hm", indice=["agent_name", "fecha"])


def _por_canal(df: pd.DataFrame, codigos: dict, etiqueta: str) -> pd.DataFrame:
    """Agrega un CDR (voz o digital) a métricas por agente.

    Se separan tres cosas que suelen confundirse en un solo '% efectividad':
    la gestión que nadie tipificó, la que no logró contacto, y la que sí
    contactó pero no cumplió el objetivo.
    """
    columnas = ["Agente", f"{etiqueta}", f"{etiqueta} sin tipificar",
                f"{etiqueta} tipificadas", f"{etiqueta} con contacto", f"{etiqueta} efectivas"]
    if df.empty:
        return pd.DataFrame(columns=columnas)

    d = df.copy()
    d["_sin_tip"] = _sin_tipificar(d["cod_act"])
    d["_tipificada"] = ~d["_sin_tip"]
    d["_contacto"] = _bandera(d["cod_act"], codigos, "rpc") & d["_tipificada"]
    d["_efectiva"] = _bandera(d["cod_act"], codigos, "hit") & d["_tipificada"]

    return (d.groupby("agent_name", as_index=False)
              .agg(**{etiqueta: ("cod_act", "size"),
                      f"{etiqueta} sin tipificar": ("_sin_tip", "sum"),
                      f"{etiqueta} tipificadas": ("_tipificada", "sum"),
                      f"{etiqueta} con contacto": ("_contacto", "sum"),
                      f"{etiqueta} efectivas": ("_efectiva", "sum")})
              .rename(columns={"agent_name": "Agente"}))[columnas]


def efectividad(df_llamadas: pd.DataFrame, df_chats: pd.DataFrame,
                df_chats_prod: pd.DataFrame, df_agente: pd.DataFrame,
                codigos: dict) -> pd.DataFrame:
    """Informe 4: efectividad por canal (voz y digital) para cada asesor."""
    voz = _por_canal(df_llamadas, codigos, "Llamadas")
    digital = _por_canal(df_chats, codigos, "Interacciones")

    wolkvox = pd.DataFrame(columns=["Agente", "Hits (Wolkvox)", "RPC (Wolkvox)"])
    if not df_agente.empty:
        wolkvox = (df_agente.groupby("agent_name", as_index=False)
                     .agg(**{"Hits (Wolkvox)": ("hits", "sum"), "RPC (Wolkvox)": ("rpc", "sum")})
                     .rename(columns={"agent_name": "Agente"}))

    df = voz
    for otro in (digital, wolkvox):
        df = df.merge(otro, on="Agente", how="outer")
    if df.empty:
        return df

    for col in df.columns[1:]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    # Voz: casi todo es marcación saliente, así que el denominador honesto de
    # la efectividad es la gestión que logró contacto, no el total marcado.
    df["% Efectividad voz"] = df.apply(
        lambda r: _pct(r["Llamadas efectivas"], r["Llamadas con contacto"]), axis=1)
    df["% Digital sin tipificar"] = df.apply(
        lambda r: _pct(r["Interacciones sin tipificar"], r["Interacciones"]), axis=1)
    df["% Efectividad digital"] = df.apply(
        lambda r: _pct(r["Interacciones efectivas"], r["Interacciones tipificadas"]), axis=1)

    df["Gestiones totales"] = df["Llamadas"] + df["Interacciones"]
    df["Sin tipificar"] = df["Llamadas sin tipificar"] + df["Interacciones sin tipificar"]
    df["% Sin tipificar"] = df.apply(lambda r: _pct(r["Sin tipificar"], r["Gestiones totales"]), axis=1)
    df["Efectivas totales"] = df["Llamadas efectivas"] + df["Interacciones efectivas"]
    df["% Efectividad total"] = df.apply(
        lambda r: _pct(r["Efectivas totales"], r["Gestiones totales"] - r["Sin tipificar"]), axis=1)

    columnas = ["Agente",
                "Llamadas", "Llamadas con contacto", "Llamadas efectivas", "% Efectividad voz",
                "Interacciones", "Interacciones sin tipificar", "% Digital sin tipificar",
                "Interacciones tipificadas", "Interacciones efectivas", "% Efectividad digital",
                "Gestiones totales", "Sin tipificar", "% Sin tipificar",
                "Efectivas totales", "% Efectividad total",
                "Hits (Wolkvox)", "RPC (Wolkvox)"]
    return df[columnas].sort_values("Gestiones totales", ascending=False).reset_index(drop=True)


UMBRALES = {"ready_alto": 50, "auxiliar_alto": 30, "sin_tipificar_alto": 30, "efectividad_baja": 10}


def _alertas(fila, umbrales: dict) -> str:
    """Señales concretas y accionables, no un veredicto genérico. Un asesor
    puede estar disponible sin gestionar, o gestionando sin tipificar, o
    tipificando sin lograr resultado: son problemas distintos."""
    u = {**UMBRALES, **(umbrales or {})}
    v = lambda c: fila.get(c, 0) or 0        # la hoja puede no traer la columna
    señales = []
    if v("% Ready") > u["ready_alto"]:
        señales.append(f"Disponible sin gestionar ({v('% Ready'):.0f}%)")
    if v("% Auxiliar") > u["auxiliar_alto"]:
        señales.append(f"Exceso de auxiliares ({v('% Auxiliar'):.0f}%)")
    if v("% Sin tipificar") > u["sin_tipificar_alto"]:
        señales.append(f"No tipifica ({v('% Sin tipificar'):.0f}%)")
    if v("Gestiones totales") > 0 and v("% Efectividad total") < u["efectividad_baja"]:
        señales.append(f"Baja efectividad ({v('% Efectividad total'):.0f}%)")
    return " | ".join(señales)


def cruce(df_tiempos: pd.DataFrame, df_efect: pd.DataFrame,
          activos: set[str] | None = None, umbrales: dict | None = None) -> pd.DataFrame:
    """Informe 5: contrasta cuánto tiempo estuvo conectado cada asesor contra
    cuánto produjo. Es el cuadro que responde 'conectado pero poco efectivo'."""
    if df_tiempos.empty or df_efect.empty:
        return pd.DataFrame()

    tiempos = (df_tiempos.groupby("Agente", as_index=False)
                 .agg(**{"Días trabajados": ("Días trabajados", "sum"),
                         "% Auxiliar": ("% Auxiliar", "mean"),
                         "% En llamada": ("% En llamada", "mean"),
                         "% Ready": ("% Ready", "mean"),
                         "Ocupación %": ("Ocupación %", "mean")}))

    df = tiempos.merge(df_efect, on="Agente", how="outer").fillna(0)
    for col in ("% Auxiliar", "% En llamada", "% Ready", "Ocupación %"):
        df[col] = df[col].round(1)

    df["Gestiones por día"] = df.apply(
        lambda r: round(r["Gestiones totales"] / r["Días trabajados"], 1) if r["Días trabajados"] else 0.0,
        axis=1)
    df["Efectivas por día"] = df.apply(
        lambda r: round(r["Efectivas totales"] / r["Días trabajados"], 1) if r["Días trabajados"] else 0.0,
        axis=1)

    # Solo los asesores activos pueden quedar señalados: un punto de
    # enrutamiento o una cuenta de prueba no es un asesor con bajo desempeño.
    df["Alerta"] = df.apply(
        lambda r: _alertas(r, umbrales) if (not activos or r["Agente"] in activos) else "",
        axis=1)

    columnas = ["Agente", "Días trabajados", "Ocupación %", "% En llamada", "% Ready", "% Auxiliar",
                "Gestiones totales", "Gestiones por día", "% Sin tipificar",
                "Efectivas totales", "Efectivas por día", "% Efectividad total", "Alerta"]
    return df[columnas].sort_values("% Efectividad total").reset_index(drop=True)


def resumen_por_agente(df_punt: pd.DataFrame, df_tiempos: pd.DataFrame,
                       df_efect: pd.DataFrame, umbrales: dict | None = None,
                       df_aux: pd.DataFrame | None = None) -> pd.DataFrame:
    """Una fila por asesor con lo esencial de los tres frentes: puntualidad,
    uso del tiempo (incluido el desglose de auxiliares) y resultado. Es la
    hoja para revisar en un minuto quién necesita seguimiento y por qué."""
    if df_punt.empty:
        return pd.DataFrame()

    base = df_punt[["Agente", "Días laborales", "Días trabajados", "Sin conexión",
                    "Entradas tarde", "% Entradas tarde", "Total min tarde",
                    "Salidas temprano", "% Salidas temprano", "Total min antes"]].copy()

    if not df_tiempos.empty:
        tiempos = (df_tiempos.groupby("Agente", as_index=False)
                     .agg(**{"Ocupación %": ("Ocupación %", "mean"),
                             "% En llamada": ("% En llamada", "mean"),
                             "% Ready": ("% Ready", "mean"),
                             "% Auxiliar": ("% Auxiliar", "mean")}))
        base = base.merge(tiempos, on="Agente", how="left")

    if not df_efect.empty:
        cols = ["Agente", "Gestiones totales", "Sin tipificar", "% Sin tipificar",
                "Efectivas totales", "% Efectividad total"]
        base = base.merge(df_efect[cols], on="Agente", how="left")

    base = base.fillna(0)
    for col in ("Ocupación %", "% En llamada", "% Ready", "% Auxiliar"):
        if col in base:
            base[col] = base[col].round(1)

    if "Gestiones totales" in base:
        base["Gestiones por día"] = base.apply(
            lambda r: round(r["Gestiones totales"] / r["Días trabajados"], 1)
            if r["Días trabajados"] else 0.0, axis=1)

    # Desglose de auxiliares en horas y minutos, para ver de un vistazo en
    # qué se va la pausa de cada uno.
    if df_aux is not None and not df_aux.empty:
        pivote = auxiliares_hm(df_aux)
        pivote.columns = (["Agente"] +
                          [f"Aux: {c}" if c != "TOTAL auxiliar" else "Auxiliar total"
                           for c in pivote.columns[1:]])
        columnas = ["Agente", "Auxiliar total"] + [c for c in pivote.columns
                                                   if c.startswith("Aux: ")]
        base = base.merge(pivote[columnas], on="Agente", how="left")
        base[columnas[1:]] = base[columnas[1:]].fillna("0h 00m")

    if "Gestiones totales" in base:
        base["Alerta"] = base.apply(lambda r: _alertas(r, umbrales), axis=1)

    return base.sort_values("% Entradas tarde", ascending=False).reset_index(drop=True)


def general(df_tiempos: pd.DataFrame, df_efect: pd.DataFrame,
            activos: set[str] | None = None) -> pd.DataFrame:
    """Informe 3 (parte tiempos y efectividad): promedios de la operación.

    Si se pasan los asesores activos, los promedios se calculan solo sobre
    ellos: una cuenta con 3 llamadas en el mes movería la media sin significar
    nada.
    """
    if activos:
        df_tiempos = df_tiempos[df_tiempos["Agente"].isin(activos)] if not df_tiempos.empty else df_tiempos
        df_efect = df_efect[df_efect["Agente"].isin(activos)] if not df_efect.empty else df_efect

    filas = []
    if not df_tiempos.empty:
        for etiqueta in ESTADOS:
            filas.append((f"Promedio % {etiqueta}", f"{round(df_tiempos[f'% {etiqueta}'].mean(), 1)} %"))
        filas.append(("Ocupación promedio", f"{round(df_tiempos['Ocupación %'].mean(), 1)} %"))
    if not df_efect.empty:
        gestiones = df_efect["Gestiones totales"].sum()
        sin_tip = df_efect["Sin tipificar"].sum()
        filas += [
            ("Gestiones totales (operación)", int(gestiones)),
            ("  Llamadas", int(df_efect["Llamadas"].sum())),
            ("  Interacciones digitales", int(df_efect["Interacciones"].sum())),
            ("Gestiones sin tipificar", int(sin_tip)),
            ("% Sin tipificar", f"{_pct(sin_tip, gestiones)} %"),
            ("Gestiones efectivas", int(df_efect["Efectivas totales"].sum())),
            ("% Efectividad global (sobre lo tipificado)",
             f"{_pct(df_efect['Efectivas totales'].sum(), gestiones - sin_tip)} %"),
            # Razón agregada, no promedio de porcentajes: un asesor que tipificó
            # 4 chats de 3.000 no puede aportar un "100 %" al promedio.
            ("% Efectividad voz (sobre llamadas con contacto)",
             f"{_pct(df_efect['Llamadas efectivas'].sum(), df_efect['Llamadas con contacto'].sum())} %"),
            ("% Efectividad digital (sobre lo tipificado)",
             f"{_pct(df_efect['Interacciones efectivas'].sum(), df_efect['Interacciones tipificadas'].sum())} %"),
            ("% Digital sin tipificar",
             f"{_pct(df_efect['Interacciones sin tipificar'].sum(), df_efect['Interacciones'].sum())} %"),
        ]
    return pd.DataFrame(filas, columns=["Indicador", "Valor"])
