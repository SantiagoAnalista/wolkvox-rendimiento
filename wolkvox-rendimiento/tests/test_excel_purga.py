import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.adaptadores.publicacion import retencion as excel_purga

HOY = date(2026, 8, 17)


def crear(tmp_path, *nombres):
    for n in nombres:
        (tmp_path / n).write_text("x", encoding="utf-8")
    return tmp_path


def nombres(tmp_path):
    return sorted(a.name for a in tmp_path.glob("*.xlsx"))


def test_nombre_corte_arma_el_patron_esperado():
    assert excel_purga.nombre_corte(date(2026, 8, 17), "12:10") == "gestion_wolkvox_2026-08-17_1210"


def test_del_dia_en_curso_solo_queda_el_corte_mas_reciente(tmp_path):
    """La jornada no tiene que cerrar para consolidar. Escribir un archivo
    nuevo es lo que evita el bloqueo de Windows, pero conservar el anterior
    no aporta nada: obliga a mirar la hora del nombre para saber cuál abrir."""
    crear(tmp_path,
          "gestion_wolkvox_2026-08-17_0810.xlsx",
          "gestion_wolkvox_2026-08-17_1210.xlsx",
          "gestion_wolkvox_2026-08-17_1610.xlsx")
    excel_purga.limpiar(tmp_path, dias=3, hoy=HOY)
    assert nombres(tmp_path) == ["gestion_wolkvox_2026-08-17_1610.xlsx"]


def test_el_corte_de_cierre_consolida_la_jornada(tmp_path):
    """Cualquier corrida deja un solo archivo del día: el suyo."""
    crear(tmp_path,
          "gestion_wolkvox_2026-08-17_0810.xlsx",
          "gestion_wolkvox_2026-08-17_1210.xlsx",
          "gestion_wolkvox_2026-08-17_1810.xlsx")
    excel_purga.limpiar(tmp_path, dias=3, hoy=HOY)
    assert nombres(tmp_path) == ["gestion_wolkvox_2026-08-17_1810.xlsx"]


def test_un_dia_anterior_se_consolida_aunque_no_haya_corte_de_cierre(tmp_path):
    """Red de seguridad: si la corrida de las 18:10 falló, cualquier ejecución
    posterior termina el trabajo."""
    crear(tmp_path,
          "gestion_wolkvox_2026-08-16_0810.xlsx",
          "gestion_wolkvox_2026-08-16_1210.xlsx")
    excel_purga.limpiar(tmp_path, dias=3, hoy=HOY)
    assert nombres(tmp_path) == ["gestion_wolkvox_2026-08-16_1210.xlsx"]


def test_el_consolidado_sin_hora_gana_sobre_los_cortes(tmp_path):
    """El job diario de la mañana siguiente escribe la versión definitiva."""
    crear(tmp_path,
          "gestion_wolkvox_2026-08-16.xlsx",
          "gestion_wolkvox_2026-08-16_1810.xlsx",
          "gestion_wolkvox_2026-08-16_1210.xlsx")
    excel_purga.limpiar(tmp_path, dias=3, hoy=HOY)
    assert nombres(tmp_path) == ["gestion_wolkvox_2026-08-16.xlsx"]


def test_retencion_borra_lo_anterior_a_los_ultimos_tres_dias(tmp_path):
    crear(tmp_path,
          "gestion_wolkvox_2026-08-17_1210.xlsx",   # hoy
          "gestion_wolkvox_2026-08-16.xlsx",        # ayer
          "gestion_wolkvox_2026-08-15.xlsx",        # anteayer
          "gestion_wolkvox_2026-08-14.xlsx",        # fuera de retención
          "gestion_wolkvox_2026-08-10_0810.xlsx")   # fuera de retención
    excel_purga.limpiar(tmp_path, dias=3, hoy=HOY)
    assert nombres(tmp_path) == [
        "gestion_wolkvox_2026-08-15.xlsx",
        "gestion_wolkvox_2026-08-16.xlsx",
        "gestion_wolkvox_2026-08-17_1210.xlsx",
    ]


def test_no_toca_los_informes_de_semana_ni_de_mes(tmp_path):
    """Esos no se generan varias veces al día y tienen su propio ciclo."""
    crear(tmp_path,
          "gestion_wolkvox_2026-S32.xlsx",
          "gestion_wolkvox_2026-S33.xlsx",
          "gestion_wolkvox_2026-07.xlsx",
          "gestion_wolkvox_2026-08.xlsx",
          "gestion_wolkvox_2026-08-01.xlsx")   # diario fuera de retención
    excel_purga.limpiar(tmp_path, dias=3, hoy=HOY)
    assert nombres(tmp_path) == [
        "gestion_wolkvox_2026-07.xlsx", "gestion_wolkvox_2026-08.xlsx",
        "gestion_wolkvox_2026-S32.xlsx", "gestion_wolkvox_2026-S33.xlsx",
    ]


def test_ignora_los_archivos_de_bloqueo_de_excel(tmp_path):
    """Excel deja ~$archivo.xlsx cuando alguien lo tiene abierto."""
    crear(tmp_path,
          "gestion_wolkvox_2026-08-17_1210.xlsx",
          "~$gestion_wolkvox_2026-08-17_1210.xlsx")
    excel_purga.limpiar(tmp_path, dias=3, hoy=HOY)
    assert (tmp_path / "~$gestion_wolkvox_2026-08-17_1210.xlsx").exists()


def test_un_archivo_bloqueado_no_tumba_la_limpieza(tmp_path, monkeypatch):
    """Si alguien tiene abierto un corte viejo, se salta y se sigue."""
    crear(tmp_path,
          "gestion_wolkvox_2026-08-16_0810.xlsx",
          "gestion_wolkvox_2026-08-16_1210.xlsx",
          "gestion_wolkvox_2026-08-10_0810.xlsx")

    original = Path.unlink

    def unlink_falla(self, *a, **kw):
        if self.name == "gestion_wolkvox_2026-08-16_0810.xlsx":
            raise PermissionError("lo tiene abierto Excel")
        return original(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", unlink_falla)
    borrados = excel_purga.limpiar(tmp_path, dias=3, hoy=HOY)

    assert [b.name for b in borrados] == ["gestion_wolkvox_2026-08-10_0810.xlsx"]
    assert (tmp_path / "gestion_wolkvox_2026-08-16_0810.xlsx").exists()   # se saltó
    assert (tmp_path / "gestion_wolkvox_2026-08-16_1210.xlsx").exists()   # el superviviente


def test_una_corrida_no_borra_la_maestra_que_acaba_de_escribir(tmp_path):
    """Reprocesar un día viejo lo generaba y lo borraba acto seguido: la
    ventana de retención se mide contra hoy. Que una corrida destruya el
    archivo que le acaban de encargar no es política, es un defecto."""
    crear(tmp_path, "gestion_wolkvox_2026-08-12.xlsx")
    recien = tmp_path / "gestion_wolkvox_2026-08-12.xlsx"

    excel_purga.limpiar(tmp_path, dias=3, hoy=HOY,
                        recien_escritos=[recien])
    assert recien.exists()


def test_lo_recien_escrito_se_protege_solo_en_esa_corrida(tmp_path):
    """La siguiente corrida sí aplica la retención normal: la protección es
    para no autodestruirse, no una excepción permanente."""
    crear(tmp_path, "gestion_wolkvox_2026-08-12.xlsx")
    excel_purga.limpiar(tmp_path, dias=3, hoy=HOY,
                        recien_escritos=[tmp_path / "gestion_wolkvox_2026-08-12.xlsx"])
    excel_purga.limpiar(tmp_path, dias=3, hoy=HOY)
    assert nombres(tmp_path) == []


def test_proteger_lo_recien_escrito_no_frena_la_consolidacion(tmp_path):
    """Los cortes viejos del mismo día se siguen borrando."""
    crear(tmp_path,
          "gestion_wolkvox_2026-08-16_0810.xlsx",
          "gestion_wolkvox_2026-08-16_1210.xlsx",
          "gestion_wolkvox_2026-08-16_1810.xlsx")
    excel_purga.limpiar(tmp_path, dias=3, hoy=HOY,
                        recien_escritos=[tmp_path / "gestion_wolkvox_2026-08-16_1810.xlsx"])
    assert nombres(tmp_path) == ["gestion_wolkvox_2026-08-16_1810.xlsx"]


def test_carpeta_inexistente_no_falla(tmp_path):
    assert excel_purga.limpiar(tmp_path / "no-existe") == []


def test_vigentes_lista_las_maestras_sin_los_bloqueos(tmp_path):
    crear(tmp_path,
          "gestion_wolkvox_2026-08-17_1210.xlsx",
          "gestion_wolkvox_2026-S33.xlsx",
          "~$gestion_wolkvox_2026-S33.xlsx")
    assert excel_purga.vigentes(tmp_path) == {
        "gestion_wolkvox_2026-08-17_1210.xlsx", "gestion_wolkvox_2026-S33.xlsx"}


# ── Migración del prefijo mal escrito ────────────────────────────────────

def test_migrar_renombra_las_maestras_del_prefijo_viejo(tmp_path):
    """Con el nombre viejo son invisibles para el glob: ni se consolidan ni
    caducan. Se renombran en vez de borrarse porque entre ellas hay semanas
    y meses que no tienen equivalente nuevo."""
    crear(tmp_path,
          "gestion_wolkbox_2026-S31.xlsx",
          "gestion_wolkbox_2026-08-19_0816.xlsx")
    excel_purga.migrar_prefijo(tmp_path)
    assert nombres(tmp_path) == [
        "gestion_wolkvox_2026-08-19_0816.xlsx", "gestion_wolkvox_2026-S31.xlsx"]


def test_migrar_borra_el_viejo_si_el_nuevo_ya_existe(tmp_path):
    """Lo generó una corrida posterior: es al menos tan completo."""
    crear(tmp_path,
          "gestion_wolkbox_2026-S33.xlsx",
          "gestion_wolkvox_2026-S33.xlsx")
    excel_purga.migrar_prefijo(tmp_path)
    assert nombres(tmp_path) == ["gestion_wolkvox_2026-S33.xlsx"]


def test_migrar_alcanza_tambien_al_nombre_original_del_proyecto(tmp_path):
    crear(tmp_path, "analisis_gestion_2026-08-15.xlsx")
    excel_purga.migrar_prefijo(tmp_path)
    assert nombres(tmp_path) == ["gestion_wolkvox_2026-08-15.xlsx"]


def test_migrar_y_limpiar_dejan_un_solo_archivo_del_dia(tmp_path):
    """El caso real: seis cortes con el nombre viejo sobrevivieron al cambio
    de prefijo porque la limpieza ya no los veía."""
    crear(tmp_path,
          "gestion_wolkbox_2026-08-16_0816.xlsx",
          "gestion_wolkbox_2026-08-16_1201.xlsx",
          "gestion_wolkvox_2026-08-16.xlsx")
    excel_purga.migrar_prefijo(tmp_path)
    excel_purga.limpiar(tmp_path, dias=3, hoy=HOY)
    assert nombres(tmp_path) == ["gestion_wolkvox_2026-08-16.xlsx"]


def test_migrar_ignora_los_archivos_de_bloqueo(tmp_path):
    crear(tmp_path, "~$gestion_wolkbox_2026-S31.xlsx")
    excel_purga.migrar_prefijo(tmp_path)
    assert (tmp_path / "~$gestion_wolkbox_2026-S31.xlsx").exists()


def test_migrar_en_carpeta_inexistente_no_falla(tmp_path):
    assert excel_purga.migrar_prefijo(tmp_path / "no-existe") == []
