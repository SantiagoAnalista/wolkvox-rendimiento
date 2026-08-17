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
    assert excel_purga.nombre_corte(date(2026, 8, 17), "12:10") == "analisis_gestion_2026-08-17_1210"


def test_cortes_del_dia_en_curso_se_conservan(tmp_path):
    """Mientras la jornada no cierre, cada corte es un archivo que alguien
    puede tener abierto: borrarlos a mitad del día es lo que queremos evitar."""
    crear(tmp_path,
          "analisis_gestion_2026-08-17_0810.xlsx",
          "analisis_gestion_2026-08-17_1210.xlsx",
          "analisis_gestion_2026-08-17_1610.xlsx")
    excel_purga.limpiar(tmp_path, dias=3, hora_cierre=18, hoy=HOY)
    assert len(nombres(tmp_path)) == 3


def test_el_corte_de_cierre_consolida_la_jornada(tmp_path):
    """La última corrida del día deja un solo archivo: el suyo."""
    crear(tmp_path,
          "analisis_gestion_2026-08-17_0810.xlsx",
          "analisis_gestion_2026-08-17_1210.xlsx",
          "analisis_gestion_2026-08-17_1810.xlsx")
    excel_purga.limpiar(tmp_path, dias=3, hora_cierre=18, hoy=HOY)
    assert nombres(tmp_path) == ["analisis_gestion_2026-08-17_1810.xlsx"]


def test_un_dia_anterior_se_consolida_aunque_no_haya_corte_de_cierre(tmp_path):
    """Red de seguridad: si la corrida de las 18:10 falló, cualquier ejecución
    posterior termina el trabajo."""
    crear(tmp_path,
          "analisis_gestion_2026-08-16_0810.xlsx",
          "analisis_gestion_2026-08-16_1210.xlsx")
    excel_purga.limpiar(tmp_path, dias=3, hora_cierre=18, hoy=HOY)
    assert nombres(tmp_path) == ["analisis_gestion_2026-08-16_1210.xlsx"]


def test_el_consolidado_sin_hora_gana_sobre_los_cortes(tmp_path):
    """El job diario de la mañana siguiente escribe la versión definitiva."""
    crear(tmp_path,
          "analisis_gestion_2026-08-16.xlsx",
          "analisis_gestion_2026-08-16_1810.xlsx",
          "analisis_gestion_2026-08-16_1210.xlsx")
    excel_purga.limpiar(tmp_path, dias=3, hora_cierre=18, hoy=HOY)
    assert nombres(tmp_path) == ["analisis_gestion_2026-08-16.xlsx"]


def test_retencion_borra_lo_anterior_a_los_ultimos_tres_dias(tmp_path):
    crear(tmp_path,
          "analisis_gestion_2026-08-17_1210.xlsx",   # hoy
          "analisis_gestion_2026-08-16.xlsx",        # ayer
          "analisis_gestion_2026-08-15.xlsx",        # anteayer
          "analisis_gestion_2026-08-14.xlsx",        # fuera de retención
          "analisis_gestion_2026-08-10_0810.xlsx")   # fuera de retención
    excel_purga.limpiar(tmp_path, dias=3, hora_cierre=18, hoy=HOY)
    assert nombres(tmp_path) == [
        "analisis_gestion_2026-08-15.xlsx",
        "analisis_gestion_2026-08-16.xlsx",
        "analisis_gestion_2026-08-17_1210.xlsx",
    ]


def test_no_toca_los_informes_de_semana_ni_de_mes(tmp_path):
    """Esos no se generan varias veces al día y tienen su propio ciclo."""
    crear(tmp_path,
          "analisis_gestion_2026-S32.xlsx",
          "analisis_gestion_2026-S33.xlsx",
          "analisis_gestion_2026-07.xlsx",
          "analisis_gestion_2026-08.xlsx",
          "analisis_gestion_2026-08-01.xlsx")   # diario fuera de retención
    excel_purga.limpiar(tmp_path, dias=3, hora_cierre=18, hoy=HOY)
    assert nombres(tmp_path) == [
        "analisis_gestion_2026-07.xlsx", "analisis_gestion_2026-08.xlsx",
        "analisis_gestion_2026-S32.xlsx", "analisis_gestion_2026-S33.xlsx",
    ]


def test_ignora_los_archivos_de_bloqueo_de_excel(tmp_path):
    """Excel deja ~$archivo.xlsx cuando alguien lo tiene abierto."""
    crear(tmp_path,
          "analisis_gestion_2026-08-17_1210.xlsx",
          "~$analisis_gestion_2026-08-17_1210.xlsx")
    excel_purga.limpiar(tmp_path, dias=3, hora_cierre=18, hoy=HOY)
    assert (tmp_path / "~$analisis_gestion_2026-08-17_1210.xlsx").exists()


def test_un_archivo_bloqueado_no_tumba_la_limpieza(tmp_path, monkeypatch):
    """Si alguien tiene abierto un corte viejo, se salta y se sigue."""
    crear(tmp_path,
          "analisis_gestion_2026-08-16_0810.xlsx",
          "analisis_gestion_2026-08-16_1210.xlsx",
          "analisis_gestion_2026-08-10_0810.xlsx")

    original = Path.unlink

    def unlink_falla(self, *a, **kw):
        if self.name == "analisis_gestion_2026-08-16_0810.xlsx":
            raise PermissionError("lo tiene abierto Excel")
        return original(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", unlink_falla)
    borrados = excel_purga.limpiar(tmp_path, dias=3, hora_cierre=18, hoy=HOY)

    assert [b.name for b in borrados] == ["analisis_gestion_2026-08-10_0810.xlsx"]
    assert (tmp_path / "analisis_gestion_2026-08-16_0810.xlsx").exists()   # se saltó
    assert (tmp_path / "analisis_gestion_2026-08-16_1210.xlsx").exists()   # el superviviente


def test_carpeta_inexistente_no_falla(tmp_path):
    assert excel_purga.limpiar(tmp_path / "no-existe") == []


def test_vigentes_lista_las_maestras_sin_los_bloqueos(tmp_path):
    crear(tmp_path,
          "analisis_gestion_2026-08-17_1210.xlsx",
          "analisis_gestion_2026-S33.xlsx",
          "~$analisis_gestion_2026-S33.xlsx")
    assert excel_purga.vigentes(tmp_path) == {
        "analisis_gestion_2026-08-17_1210.xlsx", "analisis_gestion_2026-S33.xlsx"}
