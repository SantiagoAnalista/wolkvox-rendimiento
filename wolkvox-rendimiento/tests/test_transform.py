import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.adaptadores.wolkvox import traduccion as transform

CATEGORIAS = {
    "conectadas": {"VENTA": "efectiva"},
    "no_conectadas": {"busy": "no_contactada"},
}


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


def test_llamadas_categoriza_por_cod_act_y_parte_la_fecha():
    registros = [{"conn_id": "abc", "cod_act": "venta", "date": "2026-08-11 08:15:00"}]
    df = transform.llamadas(registros, CATEGORIAS)
    assert df.loc[0, "categoria_negocio"] == "efectiva"
    assert df.loc[0, "fecha"] == "2026-08-11"
    assert df.loc[0, "hora"] == 8


def test_llamadas_sin_mapeo_cae_en_sin_clasificar():
    df = transform.llamadas([{"conn_id": "abc", "cod_act": "DESCONOCIDO"}], CATEGORIAS)
    assert df.loc[0, "categoria_negocio"] == "sin_clasificar"


def test_no_conectadas_categoriza_por_result():
    registros = [{"conn_id": "xyz", "result": "Busy", "date": "2026-08-11 09:00:00",
                  "ring_time": "00:00:20"}]
    df = transform.llamadas_no_conectadas(registros, CATEGORIAS)
    assert df.loc[0, "categoria_negocio"] == "no_contactada"
    assert df.loc[0, "ring_time_seg"] == 20


def test_cargar_categorias_normaliza_mayusculas():
    cats = transform.cargar_categorias()
    assert all(k == k.upper() for k in cats["conectadas"])
    assert all(k == k.lower() for k in cats["no_conectadas"])
