import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services import asistencia

JORNADA = {"lunes": ["08:00", "18:00"], "martes": ["08:00", "18:00"],
           "miercoles": ["08:00", "18:00"], "jueves": ["08:00", "18:00"],
           "viernes": ["08:00", "18:00"], "sabado": ["08:00", "18:00"]}
HORARIOS = {"tolerancia_min": 3, "por_defecto": JORNADA, "agentes": [],
            "agenda": {}, "festivos": set()}

# 2026-08-10 lunes, 11 martes, 12 miércoles


def _logueo(filas):
    df = pd.DataFrame(filas)
    df["login_time_seg"] = 0
    return df


def test_entrada_dentro_de_tolerancia_no_es_tarde():
    df = _logueo([{"agent_id": "1", "agent_name": "Ana", "fecha": "2026-08-10",
                   "login": "2026-08-10 08:03:00", "logout": "2026-08-10 18:00:00"}])
    det = asistencia.detalle(df, HORARIOS, date(2026, 8, 10), date(2026, 8, 10), hoy=date(2026, 8, 12))
    assert not det.loc[0, "Entró tarde"]
    assert det.loc[0, "Estado"] == "OK"


def test_entrada_pasada_la_tolerancia_es_tarde():
    df = _logueo([{"agent_id": "1", "agent_name": "Ana", "fecha": "2026-08-10",
                   "login": "2026-08-10 08:21:00", "logout": "2026-08-10 18:00:00"}])
    det = asistencia.detalle(df, HORARIOS, date(2026, 8, 10), date(2026, 8, 10), hoy=date(2026, 8, 12))
    assert det.loc[0, "Entró tarde"]
    assert det.loc[0, "Min tarde"] == 21.0


def test_salida_temprano_se_detecta():
    df = _logueo([{"agent_id": "1", "agent_name": "Ana", "fecha": "2026-08-10",
                   "login": "2026-08-10 08:00:00", "logout": "2026-08-10 17:00:00"}])
    det = asistencia.detalle(df, HORARIOS, date(2026, 8, 10), date(2026, 8, 10), hoy=date(2026, 8, 12))
    assert det.loc[0, "Salió temprano"]
    assert det.loc[0, "Min antes"] == 60.0


def test_dia_en_curso_no_cuenta_como_salida_temprano():
    """A media jornada todos parecerían haber salido antes de tiempo."""
    df = _logueo([{"agent_id": "1", "agent_name": "Ana", "fecha": "2026-08-12",
                   "login": "2026-08-12 08:00:00", "logout": "2026-08-12 11:30:00"}])
    det = asistencia.detalle(df, HORARIOS, date(2026, 8, 12), date(2026, 8, 12), hoy=date(2026, 8, 12))
    assert not det.loc[0, "Salió temprano"]
    assert det.loc[0, "Estado"] == "OK"


def test_dia_laboral_sin_login_es_sin_conexion():
    df = _logueo([{"agent_id": "1", "agent_name": "Ana", "fecha": "2026-08-10",
                   "login": "2026-08-10 08:00:00", "logout": "2026-08-10 18:00:00"}])
    det = asistencia.detalle(df, HORARIOS, date(2026, 8, 10), date(2026, 8, 11), hoy=date(2026, 8, 12))
    sin_conexion = det[det["Fecha"] == "2026-08-11"].iloc[0]
    assert sin_conexion["Sin conexión"]
    assert sin_conexion["Estado"] == "Sin conexión"
    assert not sin_conexion["Entró tarde"]


def test_domingo_no_se_evalua():
    df = _logueo([{"agent_id": "1", "agent_name": "Ana", "fecha": "2026-08-10",
                   "login": "2026-08-10 08:00:00", "logout": "2026-08-10 18:00:00"}])
    # 2026-08-09 es domingo
    det = asistencia.detalle(df, HORARIOS, date(2026, 8, 9), date(2026, 8, 10), hoy=date(2026, 8, 12))
    assert list(det["Fecha"]) == ["2026-08-10"]


def test_festivo_no_se_evalua():
    horarios = {**HORARIOS, "festivos": {"2026-08-11"}}
    df = _logueo([{"agent_id": "1", "agent_name": "Ana", "fecha": "2026-08-10",
                   "login": "2026-08-10 08:00:00", "logout": "2026-08-10 18:00:00"}])
    det = asistencia.detalle(df, horarios, date(2026, 8, 10), date(2026, 8, 11), hoy=date(2026, 8, 12))
    assert list(det["Fecha"]) == ["2026-08-10"]


def test_varias_sesiones_toman_primer_login_y_ultimo_logout():
    df = _logueo([
        {"agent_id": "1", "agent_name": "Ana", "fecha": "2026-08-10",
         "login": "2026-08-10 08:00:00", "logout": "2026-08-10 12:00:00"},
        {"agent_id": "1", "agent_name": "Ana", "fecha": "2026-08-10",
         "login": "2026-08-10 13:00:00", "logout": "2026-08-10 18:00:00"},
    ])
    det = asistencia.detalle(df, HORARIOS, date(2026, 8, 10), date(2026, 8, 10), hoy=date(2026, 8, 12))
    assert len(det) == 1
    assert det.loc[0, "Entrada"] == "08:00:00"
    assert det.loc[0, "Salida"] == "18:00:00"
    assert det.loc[0, "Estado"] == "OK"


def test_el_cronograma_manda_sobre_el_horario_por_defecto():
    """Entrar 07:50 sería puntual con el horario por defecto (08:00), pero es
    tardanza con el turno del cronograma (07:00). Si sale 'Tarde', el
    cronograma del Excel tuvo prioridad."""
    horarios = {**HORARIOS, "agenda": {("ANA", date(2026, 8, 10)): ("07:00", "17:00")}}
    df = _logueo([{"agent_id": "1", "agent_name": "Ana", "fecha": "2026-08-10",
                   "login": "2026-08-10 07:50:00", "logout": "2026-08-10 17:00:00"}])
    det = asistencia.detalle(df, horarios, date(2026, 8, 10), date(2026, 8, 10), hoy=date(2026, 8, 12))
    assert det.loc[0, "Horario"] == "07:00-17:00"
    assert det.loc[0, "Entró tarde"]
    assert det.loc[0, "Min tarde"] == 50.0


def test_dia_marcado_sin_jornada_en_el_cronograma_no_se_evalua():
    """Un descanso o festivo del Excel llega como None: ese día no existe
    para el informe, no cuenta como ausencia."""
    horarios = {**HORARIOS, "agenda": {("ANA", date(2026, 8, 11)): None}}
    df = _logueo([{"agent_id": "1", "agent_name": "Ana", "fecha": "2026-08-10",
                   "login": "2026-08-10 08:00:00", "logout": "2026-08-10 18:00:00"}])
    det = asistencia.detalle(df, horarios, date(2026, 8, 10), date(2026, 8, 11), hoy=date(2026, 8, 12))
    assert list(det["Fecha"]) == ["2026-08-10"]


def test_solo_se_evaluan_los_agentes_del_informe():
    horarios = {**HORARIOS, "agentes": ["ANA"]}
    df = _logueo([
        {"agent_id": "1", "agent_name": "Ana", "fecha": "2026-08-10",
         "login": "2026-08-10 08:00:00", "logout": "2026-08-10 18:00:00"},
        {"agent_id": "9", "agent_name": "Routing_point_61182", "fecha": "2026-08-10",
         "login": "2026-08-10 10:00:00", "logout": "2026-08-10 11:00:00"},
    ])
    det = asistencia.detalle(df, horarios, date(2026, 8, 10), date(2026, 8, 10), hoy=date(2026, 8, 12))
    assert list(det["Agente"]) == ["Ana"]


def test_porcentaje_excluye_dias_sin_conexion():
    """1 de 2 días trabajados con tardanza = 50 %, no 33 % sobre 3 laborales."""
    df = _logueo([
        {"agent_id": "1", "agent_name": "Ana", "fecha": "2026-08-10",
         "login": "2026-08-10 08:30:00", "logout": "2026-08-10 18:00:00"},
        {"agent_id": "1", "agent_name": "Ana", "fecha": "2026-08-11",
         "login": "2026-08-11 08:00:00", "logout": "2026-08-11 18:00:00"},
    ])
    det = asistencia.detalle(df, HORARIOS, date(2026, 8, 10), date(2026, 8, 12), hoy=date(2026, 8, 12))
    res = asistencia.por_agente(det)
    assert res.loc[0, "Días laborales"] == 3
    assert res.loc[0, "Días trabajados"] == 2
    assert res.loc[0, "Sin conexión"] == 1
    assert res.loc[0, "% Entradas tarde"] == 50.0


def test_reporta_el_total_de_minutos_tarde_no_solo_el_promedio():
    """Tres tardanzas de 10, 20 y 30 min: 60 en total y 20 de promedio."""
    df = _logueo([
        {"agent_id": "1", "agent_name": "Ana", "fecha": f"2026-08-{d:02d}",
         "login": f"2026-08-{d:02d} 08:{m:02d}:00", "logout": f"2026-08-{d:02d} 18:00:00"}
        for d, m in ((10, 10), (11, 20), (12, 30))
    ])
    det = asistencia.detalle(df, HORARIOS, date(2026, 8, 10), date(2026, 8, 12),
                             hoy=date(2026, 8, 13))
    res = asistencia.por_agente(det)
    assert res.loc[0, "Entradas tarde"] == 3
    assert res.loc[0, "Total min tarde"] == 60.0
    assert res.loc[0, "Prom. min tarde"] == 20.0
    assert res.loc[0, "Máx. min tarde"] == 30.0


def test_reporta_el_total_de_minutos_de_salida_anticipada():
    df = _logueo([
        {"agent_id": "1", "agent_name": "Ana", "fecha": "2026-08-10",
         "login": "2026-08-10 08:00:00", "logout": "2026-08-10 17:00:00"},
        {"agent_id": "1", "agent_name": "Ana", "fecha": "2026-08-11",
         "login": "2026-08-11 08:00:00", "logout": "2026-08-11 17:30:00"},
    ])
    det = asistencia.detalle(df, HORARIOS, date(2026, 8, 10), date(2026, 8, 11),
                             hoy=date(2026, 8, 13))
    res = asistencia.por_agente(det)
    assert res.loc[0, "Total min antes"] == 90.0   # 60 + 30
    assert res.loc[0, "Prom. min antes"] == 45.0
