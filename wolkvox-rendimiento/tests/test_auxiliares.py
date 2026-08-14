"""Desglose de estados auxiliares: se reporta en horas, no en porcentaje."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services import gestion

AUX = pd.DataFrame([
    {"Periodo": "2026-07", "agent_name": "Ana", "aux_state": "Respuesta Whats", "time_seg": 7200},
    {"Periodo": "2026-07", "agent_name": "Ana", "aux_state": "Almuerzo", "time_seg": 3600},
    {"Periodo": "2026-07", "agent_name": "Ana", "aux_state": "Baño", "time_seg": 1800},
    {"Periodo": "2026-07", "agent_name": "Luis", "aux_state": "Almuerzo", "time_seg": 1800},
])


def test_vista_cruzada_en_horas_decimales():
    df = gestion.auxiliares_horas(AUX).set_index("Agente")
    assert df.loc["Ana", "Respuesta Whats"] == 2.0
    assert df.loc["Ana", "Almuerzo"] == 1.0
    assert df.loc["Ana", "Baño"] == 0.5
    assert df.loc["Ana", "TOTAL auxiliar"] == 3.5


def test_las_horas_son_numeros_no_texto():
    """En Excel deben poder sumarse y ordenarse, no quedar como '02:00:00'."""
    df = gestion.auxiliares_horas(AUX)
    columnas = [c for c in df.columns if c != "Agente"]
    assert all(pd.api.types.is_numeric_dtype(df[c]) for c in columnas)


def test_el_total_es_la_suma_de_los_estados():
    df = gestion.auxiliares_horas(AUX).set_index("Agente")
    estados = [c for c in df.columns if c != "TOTAL auxiliar"]
    assert round(df.loc["Ana", estados].sum(), 2) == df.loc["Ana", "TOTAL auxiliar"]


def test_estados_ordenados_por_horas_totales():
    """El estado que más horas consume queda en la primera columna."""
    assert list(gestion.auxiliares_horas(AUX).columns)[1] == "Respuesta Whats"


def test_agente_sin_ese_estado_queda_en_cero():
    df = gestion.auxiliares_horas(AUX).set_index("Agente")
    assert df.loc["Luis", "Respuesta Whats"] == 0.0


def test_vista_en_horas_y_minutos():
    df = gestion.auxiliares_hm(AUX).set_index("Agente")
    assert df.loc["Ana", "Respuesta Whats"] == "2h 00m"
    assert df.loc["Ana", "Baño"] == "0h 30m"
    assert df.loc["Ana", "TOTAL auxiliar"] == "3h 30m"


def test_lista_reporta_duracion_y_horas_sin_porcentajes():
    df = gestion.auxiliares_por_tipo(AUX)
    assert list(df.columns) == ["Periodo", "Agente", "Estado auxiliar", "Duración", "Horas"]
    ana = df[(df["Agente"] == "Ana") & (df["Estado auxiliar"] == "Respuesta Whats")].iloc[0]
    assert ana["Duración"] == "2h 00m"
    assert ana["Horas"] == 2.0


def test_lista_ordena_por_mayor_tiempo_dentro_del_agente():
    df = gestion.auxiliares_por_tipo(AUX)
    ana = df[df["Agente"] == "Ana"]
    assert list(ana["Estado auxiliar"]) == ["Respuesta Whats", "Almuerzo", "Baño"]


def _puntualidad():
    return pd.DataFrame([{
        "Agente": "Ana", "Días laborales": 20, "Días trabajados": 20, "Sin conexión": 0,
        "Entradas tarde": 0, "% Entradas tarde": 0.0, "Total min tarde": 0.0,
        "Salidas temprano": 0, "% Salidas temprano": 0.0, "Total min antes": 0.0,
    }])


def test_resumen_por_agente_trae_los_auxiliares_en_horas_y_minutos():
    res = gestion.resumen_por_agente(_puntualidad(), pd.DataFrame(), pd.DataFrame(),
                                     None, AUX).set_index("Agente")
    assert res.loc["Ana", "Auxiliar total"] == "3h 30m"
    assert res.loc["Ana", "Aux: Respuesta Whats"] == "2h 00m"
    assert res.loc["Ana", "Aux: Baño"] == "0h 30m"


def test_resumen_no_incluye_porcentajes_por_estado_auxiliar():
    res = gestion.resumen_por_agente(_puntualidad(), pd.DataFrame(), pd.DataFrame(), None, AUX)
    assert not any(c.startswith("Aux: ") and "%" in c for c in res.columns)


def test_resumen_sigue_funcionando_sin_datos_auxiliares():
    res = gestion.resumen_por_agente(_puntualidad(), pd.DataFrame(), pd.DataFrame())
    assert len(res) == 1
    assert not any(c.startswith("Aux: ") for c in res.columns)


AUX_DIA = pd.DataFrame([
    {"agent_name": "Ana", "fecha": "2026-07-06", "aux_state": "Almuerzo", "time_seg": 3600},
    {"agent_name": "Ana", "fecha": "2026-07-06", "aux_state": "Baño", "time_seg": 900},
    {"agent_name": "Ana", "fecha": "2026-07-07", "aux_state": "Almuerzo", "time_seg": 4500},
    {"agent_name": "Luis", "fecha": "2026-07-06", "aux_state": "Almuerzo", "time_seg": 1800},
])


def test_dia_a_dia_abre_una_fila_por_asesor_y_fecha():
    df = gestion.auxiliares_dia_a_dia(AUX_DIA)
    assert list(df["Agente"]) == ["Ana", "Ana", "Luis"]
    assert list(df["Fecha"]) == ["2026-07-06", "2026-07-07", "2026-07-06"]


def test_dia_a_dia_reporta_horas_y_minutos():
    df = gestion.auxiliares_dia_a_dia(AUX_DIA).set_index(["Agente", "Fecha"])
    assert df.loc[("Ana", "2026-07-06"), "Almuerzo"] == "1h 00m"
    assert df.loc[("Ana", "2026-07-06"), "Baño"] == "0h 15m"
    assert df.loc[("Ana", "2026-07-06"), "TOTAL auxiliar"] == "1h 15m"
    assert df.loc[("Ana", "2026-07-07"), "Almuerzo"] == "1h 15m"


def test_dia_a_dia_sin_columna_fecha_devuelve_vacio():
    """Si la extracción no se hizo día por día, no se puede abrir por fecha."""
    assert gestion.auxiliares_dia_a_dia(AUX).empty


def test_dia_a_dia_sin_datos_no_rompe():
    assert gestion.auxiliares_dia_a_dia(pd.DataFrame()).empty
