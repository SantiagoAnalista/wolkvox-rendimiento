"""Puntualidad: cruza el login/logout real contra el horario pactado para
detectar entradas tarde y salidas temprano.

Núcleo de dominio: recibe el horario ya resuelto como un dict y no sabe de
dónde salió. Quien lo lee del YAML y de los Excel es el adaptador
`adaptadores.almacen.horarios`.

Dos decisiones que cambian los números y conviene tener presentes:

1. El día en curso se excluye de "salió temprano". Si son las 11am, todo el
   mundo parecería haber salido 7 horas antes de tiempo.
2. Un día laboral sin ningún login se reporta como "sin conexión" en columna
   propia y NO entra en el % de entradas tarde, para no mezclar vacaciones o
   incapacidades con impuntualidad.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from .nombres import DIAS_SEMANA as DIAS
from .nombres import normalizar


def _jornada(horarios: dict, agente: str, dia: date) -> tuple[str, str] | None:
    """Horario pactado para ese agente ese día, o None si no es laboral.

    Manda el cronograma real de la operación; el horario por defecto solo se
    usa para fechas que los Excel no cubran.
    """
    if dia.isoformat() in horarios.get("festivos", ()):
        return None

    agenda = horarios.get("agenda") or {}
    clave = (normalizar(agente), dia)
    if clave in agenda:
        return agenda[clave]

    tramo = (horarios.get("por_defecto") or {}).get(DIAS[dia.weekday()])
    return (tramo[0], tramo[1]) if tramo else None


def _a_minutos(hhmm: str) -> int:
    h, m = str(hhmm).split(":")[:2]
    return int(h) * 60 + int(m)


def _minutos_del_dia(momento) -> float | None:
    if pd.isna(momento):
        return None
    return momento.hour * 60 + momento.minute + momento.second / 60


def _mmss(minutos) -> str:
    """Minutos (float) a texto 'Xh Ym', para que se lea sin calcular."""
    if minutos is None or pd.isna(minutos):
        return ""
    total = int(round(abs(minutos)))
    signo = "-" if minutos < 0 else ""
    return f"{signo}{total // 60}h {total % 60:02d}m" if total >= 60 else f"{signo}{total}m"


COLUMNAS_SESION = ["agente", "fecha", "entrada", "salida", "logueado_seg"]


def _sesiones(df_logueo: pd.DataFrame, incluidos: list[str]) -> pd.DataFrame:
    """Primer login y último logout por agente y día.

    agent_7 suele traer una fila por día, pero si un agente entra y sale
    varias veces hay que quedarse con los extremos. Devuelve siempre las
    mismas columnas, incluso sin datos: a las 08:10 puede no haberse
    conectado nadie todavía y el informe igual tiene que salir.
    """
    if df_logueo.empty:
        return pd.DataFrame(columns=COLUMNAS_SESION)

    df = df_logueo.copy()
    df["login_dt"] = pd.to_datetime(df["login"], errors="coerce")
    df["logout_dt"] = pd.to_datetime(df["logout"], errors="coerce")
    sesiones = (df.groupby(["agent_name", "fecha"], as_index=False)
                  .agg(entrada=("login_dt", "min"),
                       salida=("logout_dt", "max"),
                       logueado_seg=("login_time_seg", "sum"))
                  .rename(columns={"agent_name": "agente"}))

    # Solo los asesores del informe. Se busca por nombre normalizado porque
    # el Excel y la API difieren en tildes y espacios dobles.
    if incluidos:
        sesiones = sesiones[sesiones["agente"].map(normalizar).isin(incluidos)]
    return sesiones


def detalle(df_logueo: pd.DataFrame, horarios: dict, desde: date, hasta: date,
            hoy: date | None = None, df_hora: pd.DataFrame | None = None) -> pd.DataFrame:
    """Una fila por agente y día laboral del periodo, con su evaluación.

    `df_hora` es agent_8 (tiempos hora a hora) y solo se usa en una jornada en
    curso, para saber quién sigue conectado. Sin él, todos los que tienen
    login quedan "en jornada" y sin hora de salida.
    """
    hoy = hoy or date.today()

    incluidos = horarios.get("agentes") or []
    sesiones = _sesiones(df_logueo, incluidos)

    # La lista de asesores sale de la NÓMINA, no de los datos de login. Quien
    # no se ha conectado tiene que aparecer con su fila marcada, no
    # desaparecer del informe: si el ausente es invisible, el coordinador ve
    # "todo bien" justo cuando hay algo que atender. Sin nómina configurada se
    # mantiene el comportamiento anterior (enumerar lo que traigan los datos).
    presentes = {normalizar(a): a for a in sesiones["agente"].unique()}
    agentes = sorted(presentes.get(n, n) for n in incluidos) if incluidos \
        else sorted(presentes.values())
    if not agentes:
        return pd.DataFrame()

    tolerancia = horarios["tolerancia_min"]

    filas = []
    for agente in agentes:
        propias = sesiones[sesiones["agente"] == agente].set_index("fecha")

        dia = desde
        while dia <= hasta:
            jornada = _jornada(horarios, agente, dia)
            if jornada is None:
                dia += timedelta(days=1)
                continue

            clave = dia.isoformat()
            reg = propias.loc[clave] if clave in propias.index else None
            ini_pactado, fin_pactado = jornada
            base = {
                "Agente": agente,
                "Fecha": clave,
                "Día": DIAS[dia.weekday()].capitalize(),
                "Horario": f"{ini_pactado}-{fin_pactado}",
            }

            if reg is None:
                filas.append({**base, "Entrada": "", "Salida": "", "Logueado": "",
                              "Min tarde": None, "Min antes": None,
                              "Entró tarde": False, "Salió temprano": False,
                              "Sin conexión": True, "Estado conexión": "",
                              "Sin conexión desde": "", "Estado": "Sin conexión"})
                dia += timedelta(days=1)
                continue

            entrada_min = _minutos_del_dia(reg["entrada"])
            salida_min = _minutos_del_dia(reg["salida"])
            min_tarde = None if entrada_min is None else entrada_min - _a_minutos(ini_pactado)
            min_antes = None if salida_min is None else _a_minutos(fin_pactado) - salida_min

            tarde = min_tarde is not None and min_tarde > tolerancia
            # El día en curso aún no termina: no se puede juzgar la salida.
            dia_cerrado = dia < hoy
            temprano = dia_cerrado and min_antes is not None and min_antes > tolerancia

            etiquetas = [t for t, activo in (("Tarde", tarde), ("Temprano", temprano)) if activo]
            filas.append({
                **base,
                "Entrada": "" if pd.isna(reg["entrada"]) else reg["entrada"].strftime("%H:%M:%S"),
                "Salida": "" if pd.isna(reg["salida"]) else reg["salida"].strftime("%H:%M:%S"),
                "Logueado": _mmss(reg["logueado_seg"] / 60),
                "Min tarde": round(min_tarde, 1) if tarde else None,
                "Min antes": round(min_antes, 1) if temprano else None,
                "Entró tarde": tarde,
                "Salió temprano": temprano,
                "Sin conexión": False,
                "Estado conexión": "",      # se resuelven abajo, con el día a la vista
                "Sin conexión desde": "",
                "Estado": " y ".join(etiquetas) if etiquetas else "OK",
            })
            dia += timedelta(days=1)

    if not filas:
        # Ningún asesor tiene jornada en el rango: festivo, domingo o descanso
        # de todo el equipo. Con la nómina como origen se llega hasta aquí con
        # agentes pero sin filas, y sort_values sobre un DataFrame sin columnas
        # lanzaría KeyError.
        return pd.DataFrame()

    return _marcar_conexion(pd.DataFrame(filas), hoy, df_hora, tolerancia) \
        .sort_values(["Agente", "Fecha"]).reset_index(drop=True)


# Cuánto puede quedarse atrás un asesor respecto del avance del equipo antes
# de darlo por desconectado. El informe se reconstruye cada ~10 min, así que
# por debajo de eso la diferencia es ruido del propio informe, no una ausencia.
MARGEN_FRONTERA_MIN = 12


def _minuto_final(df_hora: pd.DataFrame) -> pd.Series:
    """Agente -> último minuto del día con conexión registrada.

    De agent_8: la última hora con tiempo logueado, más los minutos que
    acumuló dentro de ella. Con 29 minutos en la franja de las 13:00, el
    asesor estuvo conectado hasta las 13:29.
    """
    activas = df_hora[df_hora["login_time_seg"] > 0]
    if activas.empty:
        return pd.Series(dtype=float)
    ultima = activas.loc[activas.groupby("agent_name")["hora"].idxmax()]
    return (ultima["hora"] * 60 + ultima["login_time_seg"] / 60) \
        .set_axis(ultima["agent_name"].map(normalizar))


EN_JORNADA, TERMINADA, DESCONECTADO = "En jornada", "Jornada terminada", "Desconectado"


def _marcar_conexion(det: pd.DataFrame, hoy: date, df_hora: pd.DataFrame | None,
                     tolerancia: int = 0) -> pd.DataFrame:
    """Resuelve, en una jornada en curso, quién sigue conectado y desde cuándo no.

    El `logout` de agent_7 no sirve para esto: unas veces es la marca del
    informe avanzando sola (09:33, todos activos, logouts agrupados en
    09:20:2x) y otras una desconexión real (13:54, logouts dispersos entre
    13:01 y 13:29). Desde un solo informe no se distinguen, y publicarlo como
    salida dice que alguien se marchó estando en su puesto.

    agent_8 sí lo resuelve, comparando contra el equipo. En la FRONTERA del
    informe "sigue conectado" y "acaba de irse" son idénticos —los dos son
    actividad hasta T y nada después—, así que la referencia es hasta dónde
    llegó el que más avanzó: quien se queda `MARGEN_FRONTERA_MIN` por detrás
    se desconectó, y se sabe a qué minuto.

    Quien se desconecta a su hora de salida no "se fue": TERMINÓ. Se separan
    los dos casos contra el horario pactado, porque marcar como incidencia a
    quien cumplió su turno llena el corte de las 18:10 de alarmas falsas y
    entierra la única que importa.

    Su límite, a propósito: si TODO el equipo sale a la vez, el último en irse
    marca la frontera y aparece "en jornada" hasta el corte siguiente. En el
    caso que importa —uno se va y el resto sigue— se detecta enseguida, porque
    la frontera sigue avanzando sin él.
    """
    en_curso = (det["Fecha"] == hoy.isoformat()) & (~det["Sin conexión"])
    det["Estado conexión"] = ""
    det["Sin conexión desde"] = ""
    det.loc[en_curso, "Estado conexión"] = EN_JORNADA
    if not en_curso.any():
        return det

    # Sin agent_8 no se adivina: todos quedan "en jornada" y sin hora.
    det.loc[en_curso, "Salida"] = ""
    if df_hora is None or df_hora.empty:
        return det

    finales = _minuto_final(df_hora)
    if finales.empty:
        return det
    frontera = finales.max()

    for i in det.index[en_curso]:
        suyo = finales.get(normalizar(det.at[i, "Agente"]))
        if suyo is None or frontera - suyo <= MARGEN_FRONTERA_MIN:
            continue
        fin_pactado = _a_minutos(str(det.at[i, "Horario"]).split("-")[-1])
        det.at[i, "Estado conexión"] = TERMINADA if suyo >= fin_pactado - tolerancia else DESCONECTADO
        det.at[i, "Sin conexión desde"] = f"{int(suyo) // 60:02d}:{int(suyo) % 60:02d}"
    return det


def por_agente(det: pd.DataFrame, minimo_dias: int = 0) -> pd.DataFrame:
    """Informe 1: resumen por asesor con los porcentajes de alerta.

    Marca como no activo a quien trabajó menos de `minimo_dias` en el periodo
    (cuentas de prueba, ingresos o retiros recientes). Siguen apareciendo en
    el informe, pero no entran en los promedios generales.
    """
    if det.empty:
        return pd.DataFrame()

    trabajados = det[~det["Sin conexión"]]
    resumen = []
    for agente, grupo in det.groupby("Agente"):
        trab = trabajados[trabajados["Agente"] == agente]
        n_lab, n_trab = len(grupo), len(trab)
        # La salida solo se juzga en días ya cerrados.
        juzgables = trab[trab["Fecha"] < det["Fecha"].max()] if n_trab else trab
        tarde, temprano = int(trab["Entró tarde"].sum()), int(trab["Salió temprano"].sum())
        resumen.append({
            "Agente": agente,
            "Activo": "Sí" if n_trab >= minimo_dias else "No",
            "Días laborales": n_lab,
            "Días trabajados": n_trab,
            "Sin conexión": n_lab - n_trab,
            "% Sin conexión": round((n_lab - n_trab) / n_lab * 100, 1) if n_lab else 0.0,
            "Entradas tarde": tarde,
            "% Entradas tarde": round(tarde / n_trab * 100, 1) if n_trab else 0.0,
            "Total min tarde": round(trab["Min tarde"].sum(), 1) if tarde else 0.0,
            "Prom. min tarde": round(trab["Min tarde"].mean(), 1) if tarde else 0.0,
            "Máx. min tarde": round(trab["Min tarde"].max(), 1) if tarde else 0.0,
            "Salidas temprano": temprano,
            "% Salidas temprano": round(temprano / len(juzgables) * 100, 1) if len(juzgables) else 0.0,
            "Total min antes": round(trab["Min antes"].sum(), 1) if temprano else 0.0,
            "Prom. min antes": round(trab["Min antes"].mean(), 1) if temprano else 0.0,
            "Días con alerta": int((trab["Entró tarde"] | trab["Salió temprano"]).sum()),
        })

    df = pd.DataFrame(resumen)
    return df.sort_values(["Activo", "% Entradas tarde"], ascending=[True, False]).reset_index(drop=True)


def dias_trabajados_por_periodo(det: pd.DataFrame, periodo: str) -> dict[tuple[str, str], int]:
    """(periodo, agente) -> días efectivamente trabajados.

    Se usa para promediar los tiempos contra los días de ESE periodo. Cada
    informe cubre un solo periodo, así que la etiqueta llega desde afuera en
    vez de deducirse de la fecha: así funciona igual para mes, semana o día.
    """
    if det.empty:
        return {}
    trabajados = det[~det["Sin conexión"]]
    return {(periodo, agente): int(n)
            for agente, n in trabajados.groupby("Agente").size().items()}


def general(res_agente: pd.DataFrame) -> pd.DataFrame:
    """Informe 3 (parte puntualidad): promedios de la operación, calculados
    solo sobre los asesores activos."""
    if res_agente.empty:
        return pd.DataFrame()

    excluidos = int((res_agente["Activo"] == "No").sum()) if "Activo" in res_agente else 0
    if "Activo" in res_agente:
        res_agente = res_agente[res_agente["Activo"] == "Sí"]
    if res_agente.empty:
        return pd.DataFrame([("Sin asesores activos en el periodo", "")],
                            columns=["Indicador", "Valor"])

    total_lab = res_agente["Días laborales"].sum()
    total_trab = res_agente["Días trabajados"].sum()
    filas = [
        ("Asesores activos evaluados", len(res_agente)),
        ("Asesores excluidos (baja actividad)", excluidos),
        ("Días laborales evaluados (suma)", int(total_lab)),
        ("Días efectivamente trabajados", int(total_trab)),
        ("Días sin conexión", int(res_agente["Sin conexión"].sum())),
        ("% Días sin conexión", f"{round((total_lab - total_trab) / total_lab * 100, 1)} %" if total_lab else "0 %"),
        ("Total entradas tarde", int(res_agente["Entradas tarde"].sum())),
        ("% Entradas tarde (operación)",
         f"{round(res_agente['Entradas tarde'].sum() / total_trab * 100, 1)} %" if total_trab else "0 %"),
        ("Promedio min de tardanza", round(res_agente.loc[res_agente["Entradas tarde"] > 0, "Prom. min tarde"].mean(), 1)
         if (res_agente["Entradas tarde"] > 0).any() else 0.0),
        ("Total salidas temprano", int(res_agente["Salidas temprano"].sum())),
        ("% Salidas temprano (operación)",
         f"{round(res_agente['Salidas temprano'].sum() / total_trab * 100, 1)} %" if total_trab else "0 %"),
        ("Promedio min de salida anticipada",
         round(res_agente.loc[res_agente["Salidas temprano"] > 0, "Prom. min antes"].mean(), 1)
         if (res_agente["Salidas temprano"] > 0).any() else 0.0),
    ]
    return pd.DataFrame(filas, columns=["Indicador", "Valor"])
