import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.adaptadores.wolkvox import traduccion as transform


def test_hhmmss_a_segundos_formatos_validos():
    assert transform.hhmmss_a_segundos("01:02:03") == 3723
    assert transform.hhmmss_a_segundos("02:03") == 123
    assert transform.hhmmss_a_segundos("125") == 125
    assert transform.hhmmss_a_segundos(125) == 125


def test_hhmmss_a_segundos_vacio_nulo_o_basura():
    assert transform.hhmmss_a_segundos("") == 0
    assert transform.hhmmss_a_segundos(None) == 0
    assert transform.hhmmss_a_segundos("no-es-un-tiempo") == 0


def test_ocupacion_parsea_el_formato_de_wolkvox():
    """Wolkvox entrega la ocupación ya calculada, como '34.64 %'."""
    df = pd.DataFrame({"occupancy": ["34.64 %", "0.00 %", "100 %"]})
    assert list(transform._ocupacion(df)) == [34.64, 0.0, 100.0]


def test_ocupacion_vacia_o_invalida_cae_en_cero():
    df = pd.DataFrame({"occupancy": ["", None, "n/d"]})
    assert list(transform._ocupacion(df)) == [0.0, 0.0, 0.0]


def test_agente_dia_con_registro_real_de_la_api():
    """Registro tal como lo devuelve agent_1 en producción."""
    registros = [{
        "agent_id": "12523", "agent_dni": "98570460", "agent_name": "SERGIO FLOREZ",
        "calls": "2", "inbound": "2", "outbound": "0", "internal": "0",
        "ready_time": "00:55:47", "inbound_time": "00:25:53", "outbound_time": "00:00:00",
        "acw_time": "00:03:41", "ring_time": "00:00:00", "login_time": "02:02:47",
        "aht": "00:14:47", "occupancy": "34.64 %", "aux_time": "00:37:26",
        "hits": "1", "rpc": "1", "aht_outbund": "00:00:00", "aht_inbound": "00:14:47",
        "login": "2026-08-12 08:02:08", "logout": "2026-08-12 10:02:42",
    }]
    df = transform.agente_dia(registros, "2026-08-12")
    assert df.loc[0, "login_time_seg"] == 7367
    assert df.loc[0, "aht_seg"] == 887
    assert df.loc[0, "calls"] == 2
    assert df.loc[0, "ocupacion"] == 34.64
    assert df.loc[0, "fecha"] == "2026-08-12"


def test_agente_dia_sin_registros_devuelve_vacio():
    assert transform.agente_dia([], "2026-08-11").empty


def test_columna_ausente_no_rompe():
    """Si la API deja de enviar un campo, se rellena en vez de fallar."""
    df = transform.agente_dia([{"agent_id": "1"}], "2026-08-11")
    assert df.loc[0, "aht_seg"] == 0
    assert df.loc[0, "hits"] == 0




def test_llamadas_parte_la_fecha_en_fecha_y_hora():
    """La curva horaria del tablero se construye sobre estas dos columnas."""
    registros = [{"conn_id": "1", "date": "2026-08-12 15:04:22", "agent_name": "Ana",
                  "cod_act": "Acuerdo_de_Pago", "time_seg": "201"}]
    df = transform.llamadas(registros)
    assert df.loc[0, "fecha"] == "2026-08-12"
    assert df.loc[0, "hora"] == 15
    assert df.loc[0, "time_seg"] == 201
