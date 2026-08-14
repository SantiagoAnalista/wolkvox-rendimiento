"""Los tres modos de ejecución: mes, semana y día.

El partidor gobierna la extracción, no solo el reporte, así que un tramo mal
calculado no produce un Excel feo: produce cifras de otro periodo.
"""
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api import extract


def _etiquetas(desde, hasta, periodo):
    return [t[0] for t in extract.partir_periodo(date.fromisoformat(desde),
                                                 date.fromisoformat(hasta), periodo)]


def _rangos(desde, hasta, periodo):
    return [(t[1].date().isoformat(), t[2].date().isoformat())
            for t in extract.partir_periodo(date.fromisoformat(desde),
                                            date.fromisoformat(hasta), periodo)]


# --- Modo mes -------------------------------------------------------------

def test_mes_genera_un_tramo_por_mes_calendario():
    assert _etiquetas("2026-07-01", "2026-09-30", "mes") == ["2026-07", "2026-08", "2026-09"]


def test_mes_recorta_los_extremos_al_rango_pedido():
    assert _rangos("2026-07-15", "2026-08-10", "mes") == [
        ("2026-07-15", "2026-07-31"), ("2026-08-01", "2026-08-10")]


# --- Modo semana ----------------------------------------------------------

def test_semana_va_de_lunes_a_domingo():
    # 2026-07-06 es lunes
    assert _rangos("2026-07-06", "2026-07-19", "semana") == [
        ("2026-07-06", "2026-07-12"), ("2026-07-13", "2026-07-19")]


def test_semana_no_se_parte_aunque_cruce_de_mes():
    """El caso que fallaba antes: una semana entre julio y agosto salía en
    dos archivos porque el partidor era mensual."""
    tramos = extract.partir_periodo(date(2026, 7, 27), date(2026, 8, 2), "semana")
    assert len(tramos) == 1
    assert (tramos[0][1].date(), tramos[0][2].date()) == (date(2026, 7, 27), date(2026, 8, 2))


def test_semana_se_etiqueta_con_la_semana_iso():
    assert _etiquetas("2026-07-06", "2026-07-12", "semana") == ["2026-S28"]


def test_semana_empezando_a_mitad_arranca_en_la_fecha_pedida():
    # miércoles 8 de julio: el tramo no puede empezar el lunes 6
    assert _rangos("2026-07-08", "2026-07-12", "semana") == [("2026-07-08", "2026-07-12")]


# --- Modo día -------------------------------------------------------------

def test_dia_genera_un_tramo_por_dia():
    assert _etiquetas("2026-07-06", "2026-07-08", "dia") == [
        "2026-07-06", "2026-07-07", "2026-07-08"]


def test_un_solo_dia_genera_un_solo_tramo():
    assert _etiquetas("2026-08-11", "2026-08-11", "dia") == ["2026-08-11"]


# --- Comunes --------------------------------------------------------------

def test_ningun_tramo_supera_el_limite_de_31_dias_de_la_api():
    for periodo in extract.PERIODOS:
        for _, ini, fin in extract.partir_periodo(date(2026, 1, 1), date(2026, 12, 31), periodo):
            assert (fin.date() - ini.date()).days <= 30


def test_las_etiquetas_ordenan_igual_que_el_calendario():
    """Los archivos deben quedar en orden cronológico en la carpeta."""
    for periodo in extract.PERIODOS:
        etiquetas = [t[0] for t in extract.partir_periodo(
            date(2026, 6, 1), date(2026, 9, 30), periodo)]
        assert etiquetas == sorted(etiquetas)


def test_periodo_invalido_falla_de_inmediato():
    with pytest.raises(ValueError, match="no válido"):
        extract.partir_periodo(date(2026, 7, 1), date(2026, 7, 31), "trimestre")


# --- Último periodo cerrado (lo que usa el job programado) ----------------

def test_ultimo_mes_cerrado_es_el_mes_anterior_completo():
    assert extract.ultimo_periodo_completo("mes", hoy=date(2026, 8, 13)) == (
        date(2026, 7, 1), date(2026, 7, 31))


def test_ultimo_mes_cerrado_cruzando_el_ano():
    assert extract.ultimo_periodo_completo("mes", hoy=date(2026, 1, 5)) == (
        date(2025, 12, 1), date(2025, 12, 31))


def test_ultima_semana_cerrada_es_lunes_a_domingo_previos():
    # jueves 13 de agosto -> semana del lunes 3 al domingo 9
    assert extract.ultimo_periodo_completo("semana", hoy=date(2026, 8, 13)) == (
        date(2026, 8, 3), date(2026, 8, 9))


def test_ultima_semana_cerrada_estando_en_lunes():
    assert extract.ultimo_periodo_completo("semana", hoy=date(2026, 8, 10)) == (
        date(2026, 8, 3), date(2026, 8, 9))


def test_ultimo_dia_cerrado_es_ayer():
    assert extract.ultimo_periodo_completo("dia", hoy=date(2026, 8, 13)) == (
        date(2026, 8, 12), date(2026, 8, 12))


def test_el_periodo_en_curso_nunca_se_incluye():
    """Un mes a medias daría cifras que cambian al día siguiente."""
    hoy = date(2026, 8, 13)
    for periodo in extract.PERIODOS:
        _, fin = extract.ultimo_periodo_completo(periodo, hoy=hoy)
        assert fin < hoy


# --- Umbral de actividad por modo -----------------------------------------

from src.services import asistencia


def test_umbral_de_actividad_escala_con_el_modo():
    """Exigir 5 días trabajados en un informe diario dejaría a todos fuera
    de los promedios generales."""
    valor = {"mes": 5, "semana": 2, "dia": 1}
    assert asistencia._minimo_dias(valor, "mes") == 5
    assert asistencia._minimo_dias(valor, "semana") == 2
    assert asistencia._minimo_dias(valor, "dia") == 1


def test_umbral_admite_un_numero_suelto_para_todos_los_modos():
    assert asistencia._minimo_dias(5, "dia") == 5


def test_umbral_ausente_no_excluye_a_nadie():
    assert asistencia._minimo_dias(None, "mes") == 0
