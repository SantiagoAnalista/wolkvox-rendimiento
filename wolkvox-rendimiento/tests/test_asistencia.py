import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dominio import asistencia

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


def test_la_nomina_manda_el_ausente_total_no_desaparece():
    """Antes, quien no se conectaba en todo el periodo no salía en el informe.
    Un asesor invisible hace que el coordinador vea 'todo bien' justo cuando
    hay algo que atender."""
    horarios = {**HORARIOS, "agentes": ["ANA", "BETO"]}
    df = _logueo([{"agent_id": "1", "agent_name": "Ana", "fecha": "2026-08-10",
                   "login": "2026-08-10 08:00:00", "logout": "2026-08-10 18:00:00"}])
    det = asistencia.detalle(df, horarios, date(2026, 8, 10), date(2026, 8, 10), hoy=date(2026, 8, 12))
    assert sorted(det["Agente"]) == ["Ana", "BETO"]
    beto = det[det["Agente"] == "BETO"].iloc[0]
    assert beto["Sin conexión"]
    assert beto["Estado"] == "Sin conexión"


def test_sin_ningun_login_igual_sale_la_nomina_completa():
    """El corte de las 08:10: puede que todavía no se haya conectado nadie, y
    eso es exactamente lo que el coordinador necesita ver."""
    horarios = {**HORARIOS, "agentes": ["ANA", "BETO"]}
    det = asistencia.detalle(pd.DataFrame(), horarios,
                             date(2026, 8, 10), date(2026, 8, 10), hoy=date(2026, 8, 10))
    assert len(det) == 2
    assert det["Sin conexión"].all()
    assert list(det["Horario"]) == ["08:00-18:00", "08:00-18:00"]


def test_un_dia_sin_jornada_para_nadie_devuelve_vacio_sin_reventar():
    """Festivo, domingo o descanso general. Con la nómina como origen se llega
    al bucle con agentes pero sin filas; hay que devolver un DataFrame vacío,
    no un sort_values sobre un frame sin columnas."""
    horarios = {**HORARIOS, "agentes": ["ANA", "BETO"], "festivos": {"2026-08-10"}}
    det = asistencia.detalle(pd.DataFrame(), horarios,
                             date(2026, 8, 10), date(2026, 8, 10), hoy=date(2026, 8, 10))
    assert det.empty


def test_el_nombre_de_display_de_la_api_gana_sobre_el_de_la_nomina():
    """La nómina está normalizada (mayúsculas, sin tildes); la API trae el
    nombre real. Si se mezclaran, el asesor saldría dos veces."""
    horarios = {**HORARIOS, "agentes": ["VILLA NORENA LAURA"]}
    df = _logueo([{"agent_id": "1", "agent_name": "VILLA NOREÑA  LAURA", "fecha": "2026-08-10",
                   "login": "2026-08-10 08:00:00", "logout": "2026-08-10 18:00:00"}])
    det = asistencia.detalle(df, horarios, date(2026, 8, 10), date(2026, 8, 10), hoy=date(2026, 8, 12))
    assert list(det["Agente"]) == ["VILLA NOREÑA  LAURA"]
    assert not det.loc[0, "Sin conexión"]


def test_sin_nomina_configurada_se_enumeran_los_agentes_de_los_datos():
    """Comportamiento anterior: sin nómina no hay contra qué contrastar."""
    df = _logueo([{"agent_id": "1", "agent_name": "Ana", "fecha": "2026-08-10",
                   "login": "2026-08-10 08:00:00", "logout": "2026-08-10 18:00:00"}])
    det = asistencia.detalle(df, HORARIOS, date(2026, 8, 10), date(2026, 8, 10), hoy=date(2026, 8, 12))
    assert list(det["Agente"]) == ["Ana"]


def test_el_ausente_total_no_mueve_los_promedios_de_la_operacion():
    """Queda marcado 'Activo: No' y solo suma al contador de excluidos."""
    horarios = {**HORARIOS, "agentes": ["ANA", "BETO"]}
    df = _logueo([{"agent_id": "1", "agent_name": "Ana", "fecha": "2026-08-10",
                   "login": "2026-08-10 08:30:00", "logout": "2026-08-10 18:00:00"}])
    det = asistencia.detalle(df, horarios, date(2026, 8, 10), date(2026, 8, 10), hoy=date(2026, 8, 12))
    res = asistencia.por_agente(det, minimo_dias=1).set_index("Agente")
    assert res.loc["BETO", "Activo"] == "No"
    assert res.loc["Ana", "Activo"] == "Sí"

    general = asistencia.general(res.reset_index())
    gen = dict(zip(general["Indicador"], general["Valor"]))
    assert gen["Asesores activos evaluados"] == 1
    assert gen["Asesores excluidos (baja actividad)"] == 1
    assert gen["% Entradas tarde (operación)"] == "100.0 %"   # 1 de 1 día trabajado


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


# --- "Salida" en una jornada en curso -------------------------------------

def test_en_jornada_en_curso_no_se_publica_hora_de_salida():
    """El logout de agent_7 no es una desconexion. Medido contra la operacion
    real: con todos activos venia agrupado al segundo (marca del informe,
    avanzando sola) y con tres en almuerzo venia disperso (se congela al
    entrar en auxiliar). No se puede distinguir "se fue" de "esta almorzando",
    asi que no se publica hora."""
    horarios = {**HORARIOS, "agentes": ["ANA", "BETO"]}
    df = _logueo([
        {"agent_id": "1", "agent_name": "Ana", "fecha": "2026-08-10",
         "login": "2026-08-10 08:00:00", "logout": "2026-08-10 10:20:33"},
        {"agent_id": "2", "agent_name": "Beto", "fecha": "2026-08-10",
         "login": "2026-08-10 08:05:00", "logout": "2026-08-10 10:20:27"},
    ])
    det = asistencia.detalle(df, horarios, date(2026, 8, 10), date(2026, 8, 10),
                             hoy=date(2026, 8, 10)).set_index("Agente")
    for quien in ("Ana", "Beto"):
        assert det.loc[quien, "En jornada"]
        assert det.loc[quien, "Salida"] == ""


def test_un_logout_viejo_en_jornada_tampoco_se_publica():
    """Beto lleva desde las 09:15 sin señal: puede haberse ido o llevar hora y
    media en auxiliar. Desde un solo informe no se distingue, y adivinar es
    peor que callar."""
    horarios = {**HORARIOS, "agentes": ["ANA", "BETO"]}
    df = _logueo([
        {"agent_id": "1", "agent_name": "Ana", "fecha": "2026-08-10",
         "login": "2026-08-10 08:00:00", "logout": "2026-08-10 10:20:33"},
        {"agent_id": "2", "agent_name": "Beto", "fecha": "2026-08-10",
         "login": "2026-08-10 08:05:00", "logout": "2026-08-10 09:15:00"},
    ])
    det = asistencia.detalle(df, horarios, date(2026, 8, 10), date(2026, 8, 10),
                             hoy=date(2026, 8, 10)).set_index("Agente")
    assert det.loc["Beto", "En jornada"]
    assert det.loc["Beto", "Salida"] == ""


def test_en_un_dia_cerrado_la_salida_es_la_salida():
    """Ahi el campo ya dejo de moverse: esa hora si es la salida real."""
    df = _logueo([{"agent_id": "1", "agent_name": "Ana", "fecha": "2026-08-10",
                   "login": "2026-08-10 08:00:00", "logout": "2026-08-10 18:00:00"}])
    det = asistencia.detalle(df, HORARIOS, date(2026, 8, 10), date(2026, 8, 10),
                             hoy=date(2026, 8, 12))
    assert not det.loc[0, "En jornada"]
    assert det.loc[0, "Salida"] == "18:00:00"


# --- agent_8: quien sigue conectado y desde cuando no ----------------------

def _hora(filas):
    """agent_8 ya traducido: agent_name, hora, login_time_seg."""
    return pd.DataFrame(filas)


def test_quien_se_queda_atras_del_equipo_se_marca_desconectado():
    """Beto dejo de aparecer a las 13:29 mientras Ana seguia hasta las 15:xx.
    Es el caso que importa: uno se va y el resto sigue."""
    horarios = {**HORARIOS, "agentes": ["ANA", "BETO"]}
    df = _logueo([
        {"agent_id": "1", "agent_name": "Ana", "fecha": "2026-08-10",
         "login": "2026-08-10 08:00:00", "logout": "2026-08-10 15:40:00"},
        {"agent_id": "2", "agent_name": "Beto", "fecha": "2026-08-10",
         "login": "2026-08-10 08:00:00", "logout": "2026-08-10 13:29:00"},
    ])
    hora = _hora([
        {"agent_name": "Ana", "hora": 14, "login_time_seg": 3600},
        {"agent_name": "Ana", "hora": 15, "login_time_seg": 40 * 60},
        {"agent_name": "Beto", "hora": 13, "login_time_seg": 29 * 60},
        {"agent_name": "Beto", "hora": 14, "login_time_seg": 0},
    ])
    det = asistencia.detalle(df, horarios, date(2026, 8, 10), date(2026, 8, 10),
                             hoy=date(2026, 8, 10), df_hora=hora).set_index("Agente")
    assert det.loc["Ana", "En jornada"]
    assert not det.loc["Beto", "En jornada"]
    assert det.loc["Beto", "Sin conexión desde"] == "13:29"


def test_en_la_frontera_del_informe_se_da_por_conectado():
    """"Sigue conectado" y "acaba de irse" son identicos ahi: los dos son
    actividad hasta T y nada despues. Se prefiere no acusar de irse a quien
    quiza solo esta al borde del informe; el corte siguiente lo resuelve."""
    horarios = {**HORARIOS, "agentes": ["ANA", "BETO"]}
    df = _logueo([
        {"agent_id": "1", "agent_name": "Ana", "fecha": "2026-08-10",
         "login": "2026-08-10 08:00:00", "logout": "2026-08-10 13:29:00"},
        {"agent_id": "2", "agent_name": "Beto", "fecha": "2026-08-10",
         "login": "2026-08-10 08:00:00", "logout": "2026-08-10 13:25:00"},
    ])
    hora = _hora([
        {"agent_name": "Ana", "hora": 13, "login_time_seg": 29 * 60},
        {"agent_name": "Beto", "hora": 13, "login_time_seg": 25 * 60},
    ])
    det = asistencia.detalle(df, horarios, date(2026, 8, 10), date(2026, 8, 10),
                             hoy=date(2026, 8, 10), df_hora=hora).set_index("Agente")
    assert det.loc["Ana", "En jornada"]
    assert det.loc["Beto", "En jornada"]     # 4 min de diferencia: es ruido del informe


def test_sin_agent_8_nadie_queda_marcado_como_desconectado():
    """Si esa fuente falla, se degrada a 'en jornada' sin inventar horas."""
    horarios = {**HORARIOS, "agentes": ["ANA"]}
    df = _logueo([{"agent_id": "1", "agent_name": "Ana", "fecha": "2026-08-10",
                   "login": "2026-08-10 08:00:00", "logout": "2026-08-10 13:29:00"}])
    det = asistencia.detalle(df, horarios, date(2026, 8, 10), date(2026, 8, 10),
                             hoy=date(2026, 8, 10), df_hora=pd.DataFrame())
    assert det.loc[0, "En jornada"]
    assert det.loc[0, "Sin conexión desde"] == ""
    assert det.loc[0, "Salida"] == ""


def test_en_un_dia_cerrado_agent_8_no_cambia_nada():
    """Ahi la salida real ya la da agent_7 y no hay nada que resolver."""
    horarios = {**HORARIOS, "agentes": ["ANA"]}
    df = _logueo([{"agent_id": "1", "agent_name": "Ana", "fecha": "2026-08-10",
                   "login": "2026-08-10 08:00:00", "logout": "2026-08-10 18:00:00"}])
    hora = _hora([{"agent_name": "Ana", "hora": 13, "login_time_seg": 29 * 60}])
    det = asistencia.detalle(df, horarios, date(2026, 8, 10), date(2026, 8, 10),
                             hoy=date(2026, 8, 12), df_hora=hora)
    assert not det.loc[0, "En jornada"]
    assert det.loc[0, "Sin conexión desde"] == ""
    assert det.loc[0, "Salida"] == "18:00:00"
