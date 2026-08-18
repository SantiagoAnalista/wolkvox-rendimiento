import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dominio import gestion
from src.adaptadores.publicacion import tablero_datos, tablero_html

UMBRALES = {"auxiliar_alto": 30, "sin_tipificar_alto": 30, "efectividad_baja": 10,
            "ready_alto": 50, "entradas_tarde_alto": 10, "tarde_grave_min": 15}

METADATOS = {"Periodo": "Semana 32 de 2026", "Generado": "2026-08-13 11:35:57"}


def cuadros_minimos(**extra):
    base = {
        "general_puntualidad": pd.DataFrame(
            [("Total entradas tarde", 9), ("% Entradas tarde (operación)", "40.9 %")],
            columns=["Indicador", "Valor"]),
        "general_gestion": pd.DataFrame(
            [("Ocupación promedio", "71.4 %"), ("% Sin tipificar", "48.1 %")],
            columns=["Indicador", "Valor"]),
        "puntualidad_agente": pd.DataFrame([{"Agente": "Ana", "Entradas tarde": 2,
                                             "Días trabajados": 5, "% Entradas tarde": 40.0}]),
        "puntualidad_detalle": pd.DataFrame([{"Agente": "Ana", "Fecha": "2026-08-03",
                                              "Estado": "Tarde", "Min tarde": 3.4}]),
        "tiempos": pd.DataFrame([{"Agente": "Ana", "% Auxiliar": 29.2}]),
        "efectividad": pd.DataFrame([{"Agente": "Ana", "% Efectividad total": 2.2}]),
        "cruce": pd.DataFrame([{"Agente": "Ana", "Alerta": "No tipifica (55%)"}]),
        "auxiliares": pd.DataFrame([{"Agente": "Ana", "Estado auxiliar": "Almuerzo", "Horas": 1.02}]),
        "curva_horaria": pd.DataFrame([{"Hora": 8, "Agente": "Ana", "Llamadas": 12, "Digitales": 3}]),
    }
    base.update(extra)
    return base


def construir(**extra):
    return tablero_datos.construir(
        cuadros_minimos(**extra), METADATOS, "2026-S32", "semana",
        date(2026, 8, 3), date(2026, 8, 9), UMBRALES, tolerancia_min=3)


# ── gestion.curva_horaria ────────────────────────────────────────────────

LLAMADAS = pd.DataFrame([
    {"hora": 8, "agent_name": "Ana"}, {"hora": 8, "agent_name": "Ana"},
    {"hora": 8, "agent_name": "Beto"}, {"hora": 9, "agent_name": "Ana"},
])
CHATS = pd.DataFrame([
    {"hora": 8, "agent_name": "Ana"}, {"hora": 10, "agent_name": "Beto"},
])


def test_curva_horaria_agrupa_por_hora_y_agente():
    curva = gestion.curva_horaria(LLAMADAS).set_index(["Hora", "Agente"])
    assert curva.loc[(8, "Ana"), "Llamadas"] == 2
    assert curva.loc[(8, "Beto"), "Llamadas"] == 1
    assert curva.loc[(9, "Ana"), "Llamadas"] == 1


def test_curva_horaria_separa_voz_de_digital():
    """Dos columnas y no una suma: la llamada se fecha cuando ocurre, la
    conversación digital cuando se abre. Mezclarlas diría algo falso."""
    curva = gestion.curva_horaria(LLAMADAS, CHATS).set_index(["Hora", "Agente"])
    assert curva.loc[(8, "Ana"), "Llamadas"] == 2
    assert curva.loc[(8, "Ana"), "Digitales"] == 1
    assert curva.loc[(9, "Ana"), "Digitales"] == 0     # hora con voz pero sin digital


def test_una_hora_solo_digital_igual_aparece():
    """Beto abrió un chat a las 10 sin llamar: esa franja no puede perderse."""
    curva = gestion.curva_horaria(LLAMADAS, CHATS).set_index(["Hora", "Agente"])
    assert curva.loc[(10, "Beto"), "Digitales"] == 1
    assert curva.loc[(10, "Beto"), "Llamadas"] == 0


def test_curva_horaria_sin_chats_deja_la_columna_en_cero():
    """El modo semanal y el mensual la llaman igual; no puede faltar la columna."""
    curva = gestion.curva_horaria(LLAMADAS)
    assert list(curva.columns) == gestion.COLUMNAS_CURVA
    assert curva["Digitales"].sum() == 0


def test_curva_horaria_sin_datos_devuelve_columnas_esperadas():
    """El tablero itera las columnas: un DataFrame vacío sin ellas rompería."""
    vacia = gestion.curva_horaria(pd.DataFrame(), pd.DataFrame())
    assert list(vacia.columns) == gestion.COLUMNAS_CURVA
    assert vacia.empty


# ── tablero_datos.construir ──────────────────────────────────────────────

def test_construir_aplana_los_indicadores_de_los_dos_cuadros_generales():
    p = construir()
    assert p["kpis"]["% Entradas tarde (operación)"] == "40.9 %"
    assert p["kpis"]["Ocupación promedio"] == "71.4 %"


def test_construir_conserva_los_umbrales_para_que_el_html_no_los_cablee():
    p = construir()["umbrales"]
    assert p["tolerancia_min"] == 3
    assert p["tarde_grave_min"] == 15
    assert p["efectividad_baja"] == 10


def test_construir_solo_incluye_los_cuadros_que_el_tablero_dibuja():
    p = construir(auxiliares_hm=pd.DataFrame([{"Agente": "Ana"}]))
    assert set(p["cuadros"]) == set(tablero_datos.CUADROS_TABLERO)
    assert "auxiliares_hm" not in p["cuadros"]


def test_construir_tolera_cuadros_ausentes_o_vacios():
    p = tablero_datos.construir({}, METADATOS, "2026-S32", "semana",
                                date(2026, 8, 3), date(2026, 8, 9), UMBRALES, 3)
    assert p["kpis"] == {}
    assert all(v == [] for v in p["cuadros"].values())


def test_construir_convierte_nan_a_null_serializable():
    """json.dumps escribiría NaN, que rompe el JSON.parse del navegador."""
    cuadros = cuadros_minimos(puntualidad_detalle=pd.DataFrame(
        [{"Agente": "Ana", "Fecha": "2026-08-03", "Min tarde": None, "Estado": "OK"}]))
    p = tablero_datos.construir(cuadros, METADATOS, "2026-S32", "semana",
                                date(2026, 8, 3), date(2026, 8, 9), UMBRALES, 3)
    crudo = json.dumps(p)
    assert "NaN" not in crudo
    assert p["cuadros"]["puntualidad_detalle"][0]["Min tarde"] is None


# ── Almacén ──────────────────────────────────────────────────────────────

def test_guardar_y_cargar_ida_y_vuelta(tmp_path):
    tablero_datos.guardar(construir(), tmp_path)
    cargados = tablero_datos.cargar_todos(tmp_path)
    assert [p["etiqueta"] for p in cargados] == ["2026-S32"]
    assert cargados[0]["kpis"]["Ocupación promedio"] == "71.4 %"


def test_guardar_no_deja_archivos_temporales(tmp_path):
    tablero_datos.guardar(construir(), tmp_path)
    assert list(tablero_datos.ruta_store(tmp_path).glob("*.tmp")) == []


def test_reprocesar_un_periodo_lo_reescribe_sin_duplicar(tmp_path):
    tablero_datos.guardar(construir(), tmp_path)
    p = construir()
    p["kpis"]["Ocupación promedio"] = "80.0 %"
    tablero_datos.guardar(p, tmp_path)
    cargados = tablero_datos.cargar_todos(tmp_path)
    assert len(cargados) == 1
    assert cargados[0]["kpis"]["Ocupación promedio"] == "80.0 %"


def test_cargar_ordena_del_mas_reciente_al_mas_antiguo(tmp_path):
    for etiqueta, ini in (("2026-S31", date(2026, 7, 27)), ("2026-S32", date(2026, 8, 3))):
        p = construir()
        p["etiqueta"], p["desde"] = etiqueta, ini.isoformat()
        tablero_datos.guardar(p, tmp_path)
    assert [p["etiqueta"] for p in tablero_datos.cargar_todos(tmp_path)] == ["2026-S32", "2026-S31"]


def test_cargar_ignora_un_json_corrupto_sin_tumbar_la_corrida(tmp_path):
    tablero_datos.guardar(construir(), tmp_path)
    (tablero_datos.ruta_store(tmp_path) / "2026-S30.json").write_text("{roto", encoding="utf-8")
    assert [p["etiqueta"] for p in tablero_datos.cargar_todos(tmp_path)] == ["2026-S32"]


def test_retencion_recorta_por_tipo_y_purgar_borra_lo_que_sobra(tmp_path):
    for n in range(1, 6):
        p = construir()
        p["etiqueta"], p["desde"] = f"2026-S{n:02d}", date(2026, 1, n).isoformat()
        tablero_datos.guardar(p, tmp_path)

    retencion = {"semana": 3}
    assert len(tablero_datos.cargar_todos(tmp_path, retencion)) == 3
    tablero_datos.purgar(tmp_path, retencion)
    assert len(list(tablero_datos.ruta_store(tmp_path).glob("*.json"))) == 3
    # Se conservan los más recientes, no los primeros que se escribieron.
    assert [p["etiqueta"] for p in tablero_datos.cargar_todos(tmp_path, retencion)] == \
        ["2026-S05", "2026-S04", "2026-S03"]


@pytest.mark.parametrize("etiqueta, tipo", [
    ("2026-08-12", "dia"), ("2026-S32", "semana"), ("2026-08", "mes"),
])
def test_tipo_se_deduce_de_la_etiqueta(etiqueta, tipo):
    assert tablero_datos._tipo(etiqueta) == tipo


# ── Render ───────────────────────────────────────────────────────────────

def test_generar_escribe_html_con_el_payload_embebido(tmp_path):
    destino = tablero_html.generar([construir()], tmp_path)
    html = destino.read_text(encoding="utf-8")
    assert destino.name == "tablero.html"
    assert tablero_html.MARCADOR not in html
    assert '"etiqueta":"2026-S32"' in html.replace(" ", "")


def test_generar_neutraliza_el_cierre_de_etiqueta_en_los_datos(tmp_path):
    """Un '</script>' dentro de un nombre cerraría la etiqueta antes de tiempo."""
    p = construir()
    p["titulo"] = "Semana </script><b>rota</b>"
    html = tablero_html.generar([p], tmp_path).read_text(encoding="utf-8")
    assert "</script><b>rota" not in html
    assert "<\\/script>" in html


def test_generar_no_deja_temporales_y_es_reejecutable(tmp_path):
    tablero_html.generar([construir()], tmp_path)
    destino = tablero_html.generar([construir()], tmp_path)
    assert list(tmp_path.glob("*.tmp")) == []
    assert destino.exists()


def test_generar_sin_periodos_produce_un_html_valido(tmp_path):
    """La primera corrida de un entorno nuevo no debe reventar."""
    html = tablero_html.generar([], tmp_path).read_text(encoding="utf-8")
    assert '"periodos":[]' in html.replace(" ", "")


def test_generar_falla_si_la_plantilla_no_tiene_marcador(tmp_path):
    plantilla = tmp_path / "sin_marcador.html"
    plantilla.write_text("<html></html>", encoding="utf-8")
    with pytest.raises(ValueError, match=tablero_html.MARCADOR):
        tablero_html.generar([construir()], tmp_path, plantilla=plantilla)


def test_la_plantilla_del_repo_existe_y_es_autocontenida():
    """Sin CDN: el tablero se abre desde una carpeta compartida, sin red."""
    html = tablero_html.PLANTILLA.read_text(encoding="utf-8")
    assert tablero_html.MARCADOR in html
    assert "src=\"http" not in html and "href=\"http" not in html


# ── Cortes intradía ──────────────────────────────────────────────────────

def test_sin_corte_el_periodo_no_es_parcial():
    p = construir()
    assert p["corte"] is None
    assert p["parcial"] is False


def test_con_corte_el_periodo_queda_marcado_como_parcial():
    """El tablero rotula la hora para que nadie lea una foto de mediodía
    como dato en vivo."""
    p = tablero_datos.construir(cuadros_minimos(), METADATOS, "2026-08-17", "dia",
                                date(2026, 8, 17), date(2026, 8, 17), UMBRALES, 3,
                                corte="12:10",
                                archivo_excel="analisis_gestion_2026-08-17_1210.xlsx")
    assert p["corte"] == "12:10"
    assert p["parcial"] is True
    assert p["archivo_excel"] == "analisis_gestion_2026-08-17_1210.xlsx"


def test_los_cortes_del_dia_reescriben_el_mismo_periodo(tmp_path):
    """Ocho corridas al día dejan un solo JSON por fecha, no ocho."""
    for hora in ("08:10", "12:10", "18:10"):
        tablero_datos.guardar(
            tablero_datos.construir(cuadros_minimos(), METADATOS, "2026-08-17", "dia",
                                    date(2026, 8, 17), date(2026, 8, 17), UMBRALES, 3,
                                    corte=hora), tmp_path)
    cargados = tablero_datos.cargar_todos(tmp_path)
    assert len(cargados) == 1
    assert cargados[0]["corte"] == "18:10"


# ── Enlace a la maestra ──────────────────────────────────────────────────

def test_se_conserva_el_enlace_cuando_la_maestra_sigue_vigente(tmp_path):
    p = construir()
    p["archivo_excel"] = "analisis_gestion_2026-S32.xlsx"
    html = tablero_html.generar([p], tmp_path,
                                excel_vigentes={"analisis_gestion_2026-S32.xlsx"}
                                ).read_text(encoding="utf-8")
    assert "analisis_gestion_2026-S32.xlsx" in html


def test_se_quita_el_enlace_cuando_la_retencion_ya_borro_la_maestra(tmp_path):
    """Mejor sin enlace que con un enlace roto."""
    p = construir()
    p["archivo_excel"] = "analisis_gestion_2026-S32.xlsx"
    html = tablero_html.generar([p], tmp_path, excel_vigentes=set()).read_text(encoding="utf-8")
    assert "analisis_gestion_2026-S32.xlsx" not in html


def test_sin_lista_de_vigentes_no_se_toca_el_enlace(tmp_path):
    p = construir()
    p["archivo_excel"] = "analisis_gestion_2026-S32.xlsx"
    html = tablero_html.generar([p], tmp_path).read_text(encoding="utf-8")
    assert "analisis_gestion_2026-S32.xlsx" in html


# ── Reintento de escritura ───────────────────────────────────────────────

def test_reintenta_cuando_el_archivo_esta_momentaneamente_ocupado(tmp_path, monkeypatch):
    """El antivirus o el indexador pueden retener el archivo un instante."""
    intentos = {"n": 0}
    real = tablero_html.os.replace

    def replace_ocupado(origen, destino):
        intentos["n"] += 1
        if intentos["n"] < 3:
            raise PermissionError("el archivo está en uso")
        return real(origen, destino)

    monkeypatch.setattr(tablero_html.os, "replace", replace_ocupado)
    monkeypatch.setattr(tablero_html.time, "sleep", lambda _: None)

    destino = tablero_html.generar([construir()], tmp_path)
    assert intentos["n"] == 3
    assert destino.exists()


def test_si_sigue_ocupado_tras_los_reintentos_la_corrida_falla(tmp_path, monkeypatch):
    def siempre_ocupado(origen, destino):
        raise PermissionError("el archivo está en uso")

    monkeypatch.setattr(tablero_html.os, "replace", siempre_ocupado)
    monkeypatch.setattr(tablero_html.time, "sleep", lambda _: None)

    with pytest.raises(PermissionError):
        tablero_html.generar([construir()], tmp_path)
