"""La extracción diaria de auxiliares no puede caerse por un día sin datos:
agent_3 responde 404 (no lista vacía) en domingos, festivos y descansos."""
import sys
from datetime import datetime
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.adaptadores.wolkvox import extraccion as extract


class ClienteFalso:
    """Devuelve datos para unas fechas y 404 para otras, como la API real."""

    def __init__(self, dias_con_datos, dias_error_500=()):
        self.dias_con_datos = set(dias_con_datos)
        self.dias_error_500 = set(dias_error_500)
        self.consultas = []

    def consultar(self, recurso, params):
        dia = params["date_ini"][:8]
        self.consultas.append(dia)
        if dia in self.dias_error_500:
            raise httpx.HTTPStatusError(
                "500", request=httpx.Request("GET", "http://x"),
                response=httpx.Response(500))
        if dia not in self.dias_con_datos:
            raise httpx.HTTPStatusError(
                "404", request=httpx.Request("GET", "http://x"),
                response=httpx.Response(404))
        return [{"agent_name": "Ana", "aux_state": "Almuerzo", "time": "01:00:00"}]


RANGO = (datetime(2026, 7, 1), datetime(2026, 7, 3))


def test_un_dia_sin_datos_no_corta_la_extraccion():
    """El 2 de julio responde 404; los otros dos días deben llegar igual."""
    cliente = ClienteFalso(dias_con_datos={"20260701", "20260703"})
    registros = extract.tiempo_auxiliar_por_dia(*(cliente, *RANGO), pausa_seg=0)

    assert len(registros) == 2
    assert {r["fecha"] for r in registros} == {"2026-07-01", "2026-07-03"}
    assert len(cliente.consultas) == 3   # consultó los tres días


def test_cada_registro_queda_marcado_con_su_fecha():
    cliente = ClienteFalso(dias_con_datos={"20260701"})
    registros = extract.tiempo_auxiliar_por_dia(*(cliente, *RANGO), pausa_seg=0)
    assert registros[0]["fecha"] == "2026-07-01"
    assert registros[0]["aux_state"] == "Almuerzo"


def test_un_error_distinto_de_404_tampoco_pierde_el_resto():
    cliente = ClienteFalso(dias_con_datos={"20260701", "20260703"},
                           dias_error_500={"20260703"})
    registros = extract.tiempo_auxiliar_por_dia(*(cliente, *RANGO), pausa_seg=0)
    assert {r["fecha"] for r in registros} == {"2026-07-01"}


def test_ningun_dia_con_datos_devuelve_lista_vacia():
    cliente = ClienteFalso(dias_con_datos=set())
    assert extract.tiempo_auxiliar_por_dia(*(cliente, *RANGO), pausa_seg=0) == []


# --- 404 = "sin datos", no un fallo --------------------------------------

def _http_error(codigo: int) -> httpx.HTTPStatusError:
    peticion = httpx.Request("GET", "https://wv0010.wolkvox.com/api/v2/x")
    return httpx.HTTPStatusError("x", request=peticion,
                                 response=httpx.Response(codigo, request=peticion))


def test_404_se_reconoce_como_ventana_sin_datos():
    """Wolkvox contesta 404 en vez de una lista vacia: un domingo, un festivo
    o las 08:10 antes de que nadie haya tomado una pausa."""
    assert extract.es_sin_datos(_http_error(404))


@pytest.mark.parametrize("codigo", [400, 401, 403, 429, 500, 503])
def test_los_demas_codigos_si_son_fallos(codigo):
    assert not extract.es_sin_datos(_http_error(codigo))


def test_un_error_que_no_es_http_no_se_confunde_con_falta_de_datos():
    """Un timeout o un fallo de red tienen que seguir saliendo como error."""
    assert not extract.es_sin_datos(httpx.ConnectTimeout("se agoto el tiempo"))
    assert not extract.es_sin_datos(ValueError("otra cosa"))
