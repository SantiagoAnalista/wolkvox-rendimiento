import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dominio import gestion

# Inventario tal como lo devuelve information.php?api=activity_codes
CODIGOS = pd.DataFrame([
    {"cod_act": "Acuerdo_de_Pago", "hit": "no", "rpc": "yes"},
    {"cod_act": "Acuerdo_de_pago_whatsapp", "hit": "yes", "rpc": "yes"},
    {"cod_act": "No_contesta", "hit": "no", "rpc": "no"},
    {"cod_act": "Ya_pago_Whatasapp", "hit": "yes", "rpc": "yes"},
    {"cod_act": "01_acuerdo_de_pago_whatsapp", "hit": "", "rpc": ""},  # cod_act2, sin banderas
])


def test_mapa_codigos_usa_las_banderas_de_wolkvox():
    mapa = gestion.mapa_codigos(CODIGOS)
    assert mapa["ACUERDO_DE_PAGO_WHATSAPP"] == {"hit": True, "rpc": True}
    assert mapa["ACUERDO_DE_PAGO"] == {"hit": False, "rpc": True}   # contacto sin logro
    assert mapa["NO_CONTESTA"] == {"hit": False, "rpc": False}      # ni contacto hubo


def test_mapa_codigos_ignora_codigos_secundarios_sin_banderas():
    """Los cod_act2 vienen con hit vacío: no deben contarse como efectivos."""
    assert "01_ACUERDO_DE_PAGO_WHATSAPP" not in gestion.mapa_codigos(CODIGOS)


def test_sin_tipificar_detecta_timeout_y_vacios():
    serie = pd.Series(["TIMEOUTCHAT", "TIMEOUTACW", "", "Acuerdo_de_Pago"])
    assert list(gestion._sin_tipificar(serie)) == [True, True, True, False]


def test_efectividad_voz_se_mide_sobre_las_que_lograron_contacto():
    """Marcar 100 números y que 98 no contesten no es 2 % de efectividad:
    el denominador honesto es la gestión que sí logró contacto."""
    mapa = gestion.mapa_codigos(CODIGOS)
    llamadas = pd.DataFrame([
        {"agent_name": "Ana", "cod_act": "Acuerdo_de_pago_whatsapp"},  # contacto + efectiva
        {"agent_name": "Ana", "cod_act": "Acuerdo_de_Pago"},            # contacto, no efectiva
        {"agent_name": "Ana", "cod_act": "No_contesta"},                # sin contacto
        {"agent_name": "Ana", "cod_act": "No_contesta"},                # sin contacto
    ])
    df = gestion.efectividad(llamadas, pd.DataFrame(), pd.DataFrame(),
                             pd.DataFrame(), mapa).set_index("Agente")
    fila = df.loc["Ana"]
    assert fila["Llamadas"] == 4
    assert fila["Llamadas con contacto"] == 2
    assert fila["Llamadas efectivas"] == 1
    assert fila["% Efectividad voz"] == 50.0   # 1 de 2 contactadas, no 1 de 4


def test_efectividad_digital_excluye_las_no_tipificadas():
    """El 91 % de chats cierra por TIMEOUTCHAT: contarlos como 'no efectivos'
    escondería que el problema real es de tipificación, no de resultado."""
    mapa = gestion.mapa_codigos(CODIGOS)
    chats = pd.DataFrame([
        {"agent_name": "Ana", "cod_act": "TIMEOUTCHAT"},
        {"agent_name": "Ana", "cod_act": "TIMEOUTCHAT"},
        {"agent_name": "Ana", "cod_act": "TIMEOUTCHAT"},
        {"agent_name": "Ana", "cod_act": "Ya_pago_Whatasapp"},   # efectiva
        {"agent_name": "Ana", "cod_act": "No_contesta"},          # tipificada, no efectiva
    ])
    df = gestion.efectividad(pd.DataFrame(), chats, pd.DataFrame(),
                             pd.DataFrame(), mapa).set_index("Agente")
    fila = df.loc["Ana"]
    assert fila["Interacciones"] == 5
    assert fila["Interacciones sin tipificar"] == 3
    assert fila["% Digital sin tipificar"] == 60.0
    assert fila["% Efectividad digital"] == 50.0   # 1 de las 2 tipificadas


def test_efectividad_sin_gestiones_no_divide_por_cero():
    df = gestion.efectividad(pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
                             pd.DataFrame([{"agent_name": "Ana", "hits": 0, "rpc": 0}]),
                             gestion.mapa_codigos(CODIGOS))
    assert df.loc[0, "% Efectividad total"] == 0.0
    assert df.loc[0, "% Efectividad voz"] == 0.0


def _fila_mes(mes, segundos_logueado=36000):
    return {"Periodo": mes, "agent_name": "Ana", "calls": 10,
            "login_time_seg": segundos_logueado,
            "inbound_time_seg": 7200, "outbound_time_seg": 1800,
            "acw_time_seg": 1800, "ready_time_seg": 18000, "aux_time_seg": 7200,
            "ring_time_seg": 0, "ocupacion": 55.0}


def test_tiempos_calcula_porcentajes_sobre_lo_logueado():
    agente = pd.DataFrame([_fila_mes("2026-07")])
    df = gestion.tiempos_por_agente(agente, {("2026-07", "Ana"): 20}).iloc[0]

    assert df["Logueado"] == "10:00:00"
    assert df["En llamada"] == "02:30:00"
    assert df["% En llamada"] == 25.0
    assert df["% Auxiliar"] == 20.0
    assert df["Prom. día logueado"] == "00:30:00"   # 10h / 20 días


def test_promedio_diario_usa_los_dias_de_cada_mes():
    """Cada mes debe dividirse por SUS días trabajados. Si se usara el total
    del periodo, el promedio diario de cada mes saldría a la mitad."""
    agente = pd.DataFrame([_fila_mes("2026-07"), _fila_mes("2026-08")])
    dias = {("2026-07", "Ana"): 20, ("2026-08", "Ana"): 10}
    df = gestion.tiempos_por_agente(agente, dias).set_index("Periodo")

    assert df.loc["2026-07", "Días trabajados"] == 20
    assert df.loc["2026-07", "Prom. día logueado"] == "00:30:00"   # 10h / 20
    assert df.loc["2026-08", "Días trabajados"] == 10
    assert df.loc["2026-08", "Prom. día logueado"] == "01:00:00"   # 10h / 10


def test_cruce_suma_los_dias_de_los_dos_meses_sin_duplicar():
    agente = pd.DataFrame([_fila_mes("2026-07"), _fila_mes("2026-08")])
    tiempos = gestion.tiempos_por_agente(agente, {("2026-07", "Ana"): 20, ("2026-08", "Ana"): 10})
    efect = pd.DataFrame([{"Agente": "Ana", "Gestiones totales": 300, "Efectivas totales": 30,
                           "% Efectividad total": 10.0, "% Sin tipificar": 0.0}])
    df = gestion.cruce(tiempos, efect).set_index("Agente")
    assert df.loc["Ana", "Días trabajados"] == 30   # 20 + 10, no 40 ni 60


def test_auxiliares_reporta_horas_por_estado():
    aux = pd.DataFrame([
        {"Periodo": "2026-07", "agent_name": "Ana", "aux_state": "Almuerzo", "time_seg": 3600},
        {"Periodo": "2026-07", "agent_name": "Ana", "aux_state": "Baño", "time_seg": 1200},
    ])
    df = gestion.auxiliares_por_tipo(aux)
    assert list(df["Horas"]) == [1.0, 0.33]
    assert df.loc[0, "Estado auxiliar"] == "Almuerzo"   # ordenado por mayor tiempo


def _cruce_de(tiempos_extra, efect_extra, **kwargs):
    tiempos = pd.DataFrame([{"Agente": "Ana", "Días trabajados": 20, "% Auxiliar": 10.0,
                             "% En llamada": 40.0, "% Ready": 20.0, "Ocupación %": 70.0,
                             **tiempos_extra}])
    efect = pd.DataFrame([{"Agente": "Ana", "Gestiones totales": 400, "Efectivas totales": 200,
                           "% Efectividad total": 50.0, "% Sin tipificar": 5.0, **efect_extra}])
    return gestion.cruce(tiempos, efect, **kwargs).set_index("Agente")


def test_asesor_sano_no_genera_alerta():
    assert _cruce_de({}, {}).loc["Ana", "Alerta"] == ""


def test_alerta_de_disponible_sin_gestionar():
    """El caso QUIÑONES: 86 % del tiempo en Ready."""
    alerta = _cruce_de({"% Ready": 86.0}, {}).loc["Ana", "Alerta"]
    assert "Disponible sin gestionar (86%)" == alerta


def test_alerta_de_exceso_de_auxiliares():
    alerta = _cruce_de({"% Auxiliar": 41.0}, {}).loc["Ana", "Alerta"]
    assert "Exceso de auxiliares (41%)" == alerta


def test_alerta_de_no_tipificar():
    """El caso de los chats: 90 % cerrados por TIMEOUTCHAT."""
    alerta = _cruce_de({}, {"% Sin tipificar": 90.0}).loc["Ana", "Alerta"]
    assert "No tipifica (90%)" == alerta


def test_varias_alertas_se_acumulan():
    alerta = _cruce_de({"% Auxiliar": 41.0}, {"% Sin tipificar": 60.0,
                                              "% Efectividad total": 2.0}).loc["Ana", "Alerta"]
    assert "Exceso de auxiliares" in alerta
    assert "No tipifica" in alerta
    assert "Baja efectividad" in alerta


def test_umbrales_configurables():
    sin_alerta = _cruce_de({"% Auxiliar": 41.0}, {}, umbrales={"auxiliar_alto": 50})
    assert sin_alerta.loc["Ana", "Alerta"] == ""


def test_no_se_alerta_a_quien_no_es_asesor_activo():
    """Un punto de enrutamiento no es un asesor con bajo desempeño."""
    df = _cruce_de({"% Ready": 99.0}, {}, activos={"Otro"})
    assert df.loc["Ana", "Alerta"] == ""


def test_cruce_calcula_gestiones_por_dia():
    assert _cruce_de({}, {}).loc["Ana", "Gestiones por día"] == 20.0   # 400 / 20


# ---------------------------------------------------------------------------
# Ocupación: el número que más veces se ha desviado entre fuentes.
# ---------------------------------------------------------------------------

def _fila_agente(nombre, periodo, login_seg, ocupacion):
    return {"agent_name": nombre, "Periodo": periodo, "login_time_seg": login_seg,
            "ready_time_seg": 0, "inbound_time_seg": 0, "outbound_time_seg": 0,
            "acw_time_seg": 0, "aux_time_seg": 0, "ring_time_seg": 0,
            "calls": 0, "hits": 0, "rpc": 0,
            "ocupacion": ocupacion, "fecha": periodo}


def test_ocupacion_se_pondera_por_tiempo_logueado():
    """Con agent_8 son 24 filas por agente y las horas sin conexión traen
    ocupación 0. Promediarlas planas hundía la cifra (26 % donde el informe
    del día decía 67 %), así que la media va ponderada por login."""
    horas = pd.DataFrame([
        _fila_agente("ANA", "2026-08-12", 3600, 80.0),   # una hora al 80 %
        _fila_agente("ANA", "2026-08-12", 1800, 40.0),   # media hora al 40 %
        _fila_agente("ANA", "2026-08-12", 0, 0.0),       # hora sin conexión
    ])
    fila = gestion.tiempos_por_agente(horas, {("2026-08-12", "ANA"): 1})
    # (80*3600 + 40*1800) / 5400 = 66,7 — no el 40 % del promedio plano.
    assert fila.loc[0, "Ocupación %"] == 66.7


def test_ocupacion_de_una_sola_fila_es_la_de_wolkvox():
    """Con agent_1, que trae una fila por agente, ponderar no debe alterar
    el número que ya calculó el proveedor."""
    dia = pd.DataFrame([_fila_agente("ANA", "2026-08-12", 28800, 67.4)])
    fila = gestion.tiempos_por_agente(dia, {("2026-08-12", "ANA"): 1})
    assert fila.loc[0, "Ocupación %"] == 67.4


def test_por_dia_prefiere_agent_1_sobre_agent_8_para_los_tiempos():
    """Su ocupación no es reconstruible desde las horas: Wolkvox la calcula
    con otro denominador. Si llega agent_1 día a día, manda ese."""
    horas = pd.DataFrame([_fila_agente("ANA", "2026-08-12", 3600, 20.0)])
    dias = pd.DataFrame([_fila_agente("ANA", "2026-08-12", 3600, 67.4)])
    vacio = pd.DataFrame()

    cuadros = gestion.por_dia(horas, vacio, vacio, vacio, vacio, {}, {},
                              agente_dia=dias)
    assert cuadros["tiempos_dia"].loc[0, "Ocupación %"] == 67.4

    # Sin agent_1 sigue funcionando con agent_8, que es mejor que nada.
    solo_horas = gestion.por_dia(horas, vacio, vacio, vacio, vacio, {}, {})
    assert solo_horas["tiempos_dia"].loc[0, "Ocupación %"] == 20.0
