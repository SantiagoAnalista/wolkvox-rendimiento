import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.adaptadores.publicacion import carpeta_red

PLANTILLA = r"{base}\{anio}\{mes}\Infomes de Gestion"


def plantilla(tmp_path):
    return str(tmp_path) + r"\{anio}\{mes}\Infomes de Gestion"


def crear(carpeta, *nombres):
    carpeta.mkdir(parents=True, exist_ok=True)
    for n in nombres:
        (carpeta / n).write_text("x", encoding="utf-8")


def nombres(carpeta):
    return sorted(a.name for a in carpeta.glob("*.xlsx"))


# ── Resolución de la ruta ────────────────────────────────────────────────

@pytest.mark.parametrize("fecha, esperado", [
    (date(2026, 8, 18), r"2026\08. Agosto"),
    (date(2026, 1, 3), r"2026\01. Enero"),
    (date(2026, 12, 31), r"2026\12. Diciembre"),
    (date(2027, 9, 1), r"2027\09. Septiembre"),
])
def test_la_ruta_sigue_la_convencion_de_la_carpeta(fecha, esperado):
    """La operación ya tiene los doce meses como 'MM. Mes'."""
    ruta = carpeta_red.ruta_destino(r"C:\base\{anio}\{mes}\Infomes de Gestion", fecha)
    assert str(ruta) == rf"C:\base\{esperado}\Infomes de Gestion"


def test_el_mes_cambia_solo_al_cambiar_de_mes():
    """Con la ruta escrita a mano, el 1 de septiembre seguiría publicando en
    agosto y nadie lo notaría en días."""
    p = r"C:\base\{anio}\{mes}\x"
    assert carpeta_red.ruta_destino(p, date(2026, 8, 31)) != \
        carpeta_red.ruta_destino(p, date(2026, 9, 1))


def test_una_ruta_sin_marcadores_se_usa_tal_cual():
    ruta = carpeta_red.ruta_destino(r"C:\destino\fijo", date(2026, 8, 18))
    assert str(ruta) == r"C:\destino\fijo"


# ── Publicación del Excel ────────────────────────────────────────────────

def test_publicar_crea_la_carpeta_del_mes_si_no_existe(tmp_path):
    """Septiembre todavía no tiene su carpeta: la primera corrida la crea."""
    excel = tmp_path / "gestion_wolkvox_2026-09-01_0810.xlsx"
    excel.write_text("datos", encoding="utf-8")
    destino = carpeta_red.publicar_excel(excel, "2026-09-01", plantilla(tmp_path),
                                         date(2026, 9, 1))
    assert destino.exists()
    assert destino.parent.name == "Infomes de Gestion"
    assert destino.parent.parent.name == "09. Septiembre"


def test_un_corte_nuevo_reemplaza_al_anterior_del_mismo_dia(tmp_path):
    """Ocho corridas al día dejan un solo archivo, no ocho."""
    carpeta = tmp_path / "2026" / "08. Agosto" / "Infomes de Gestion"
    crear(carpeta, "gestion_wolkvox_2026-08-18_0810.xlsx",
          "gestion_wolkvox_2026-08-18_1210.xlsx")
    excel = tmp_path / "gestion_wolkvox_2026-08-18_1610.xlsx"
    excel.write_text("nuevo", encoding="utf-8")

    carpeta_red.publicar_excel(excel, "2026-08-18", plantilla(tmp_path), date(2026, 8, 18))
    assert nombres(carpeta) == ["gestion_wolkvox_2026-08-18_1610.xlsx"]


def test_el_informe_del_dia_no_se_lleva_por_delante_el_de_la_semana(tmp_path):
    """El comodín 'gestion_wolkvox_2026-08*' casaría con los diarios: por eso
    la comparación es por expresión regular sobre la etiqueta completa."""
    carpeta = tmp_path / "2026" / "08. Agosto" / "Infomes de Gestion"
    crear(carpeta, "gestion_wolkvox_2026-S33.xlsx", "gestion_wolkvox_2026-08.xlsx")
    excel = tmp_path / "gestion_wolkvox_2026-08-18_1610.xlsx"
    excel.write_text("nuevo", encoding="utf-8")

    carpeta_red.publicar_excel(excel, "2026-08-18", plantilla(tmp_path), date(2026, 8, 18))
    assert nombres(carpeta) == ["gestion_wolkvox_2026-08-18_1610.xlsx",
                                "gestion_wolkvox_2026-08.xlsx",
                                "gestion_wolkvox_2026-S33.xlsx"]


def test_el_mensual_tampoco_borra_los_diarios(tmp_path):
    """El caso simétrico: la etiqueta '2026-08' no puede casar con '2026-08-18'."""
    carpeta = tmp_path / "2026" / "08. Agosto" / "Infomes de Gestion"
    crear(carpeta, "gestion_wolkvox_2026-08-18_1610.xlsx")
    excel = tmp_path / "gestion_wolkvox_2026-08.xlsx"
    excel.write_text("mensual", encoding="utf-8")

    carpeta_red.publicar_excel(excel, "2026-08", plantilla(tmp_path), date(2026, 8, 31))
    assert nombres(carpeta) == ["gestion_wolkvox_2026-08-18_1610.xlsx",
                                "gestion_wolkvox_2026-08.xlsx"]


def test_un_archivo_abierto_en_excel_no_tumba_la_publicacion(tmp_path, monkeypatch):
    carpeta = tmp_path / "2026" / "08. Agosto" / "Infomes de Gestion"
    crear(carpeta, "gestion_wolkvox_2026-08-18_0810.xlsx")
    excel = tmp_path / "gestion_wolkvox_2026-08-18_1610.xlsx"
    excel.write_text("nuevo", encoding="utf-8")

    original = Path.unlink

    def unlink_falla(self, *a, **kw):
        if self.name == "gestion_wolkvox_2026-08-18_0810.xlsx":
            raise PermissionError("lo tiene abierto Excel")
        return original(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", unlink_falla)
    destino = carpeta_red.publicar_excel(excel, "2026-08-18", plantilla(tmp_path),
                                         date(2026, 8, 18))
    assert destino.exists()                                   # lo nuevo sí quedó
    assert (carpeta / "gestion_wolkvox_2026-08-18_0810.xlsx").exists()


def test_no_deja_temporales(tmp_path):
    excel = tmp_path / "gestion_wolkvox_2026-08-18_1610.xlsx"
    excel.write_text("x", encoding="utf-8")
    destino = carpeta_red.publicar_excel(excel, "2026-08-18", plantilla(tmp_path),
                                         date(2026, 8, 18))
    assert list(destino.parent.glob("*.tmp")) == []


# ── Publicación del tablero ──────────────────────────────────────────────

def test_el_tablero_se_reemplaza(tmp_path):
    carpeta = tmp_path / "2026" / "08. Agosto" / "Infomes de Gestion"
    carpeta.mkdir(parents=True)
    (carpeta / "tablero.html").write_text("viejo", encoding="utf-8")
    nuevo = tmp_path / "tablero.html"
    nuevo.write_text("nuevo", encoding="utf-8")

    destino = carpeta_red.publicar_tablero(nuevo, plantilla(tmp_path), date(2026, 8, 18))
    assert destino.read_text(encoding="utf-8") == "nuevo"


def test_publicar_el_tablero_no_toca_los_excel(tmp_path):
    carpeta = tmp_path / "2026" / "08. Agosto" / "Infomes de Gestion"
    crear(carpeta, "gestion_wolkvox_2026-08-18_1610.xlsx")
    nuevo = tmp_path / "tablero.html"
    nuevo.write_text("nuevo", encoding="utf-8")

    carpeta_red.publicar_tablero(nuevo, plantilla(tmp_path), date(2026, 8, 18))
    assert nombres(carpeta) == ["gestion_wolkvox_2026-08-18_1610.xlsx"]


# ── Retención de los informes diarios ────────────────────────────────────

def test_solo_se_conservan_los_tres_diarios_mas_recientes(tmp_path):
    carpeta = tmp_path / "2026" / "08. Agosto" / "Infomes de Gestion"
    crear(carpeta,
          "gestion_wolkvox_2026-08-15.xlsx",
          "gestion_wolkvox_2026-08-16.xlsx",
          "gestion_wolkvox_2026-08-17.xlsx",
          "gestion_wolkvox_2026-08-18.xlsx")
    excel = tmp_path / "gestion_wolkvox_2026-08-19_0816.xlsx"
    excel.write_text("nuevo", encoding="utf-8")

    carpeta_red.publicar_excel(excel, "2026-08-19", plantilla(tmp_path),
                               date(2026, 8, 19), dias_diarios=3)
    assert nombres(carpeta) == ["gestion_wolkvox_2026-08-17.xlsx",
                                "gestion_wolkvox_2026-08-18.xlsx",
                                "gestion_wolkvox_2026-08-19_0816.xlsx"]


def test_la_retencion_diaria_no_toca_semanales_ni_mensuales(tmp_path):
    """La carpeta esta organizada por mes: ese es justamente su archivo."""
    carpeta = tmp_path / "2026" / "08. Agosto" / "Infomes de Gestion"
    crear(carpeta,
          "gestion_wolkvox_2026-08-10.xlsx", "gestion_wolkvox_2026-08-11.xlsx",
          "gestion_wolkvox_2026-08-12.xlsx", "gestion_wolkvox_2026-08-13.xlsx",
          "gestion_wolkvox_2026-S32.xlsx", "gestion_wolkvox_2026-S33.xlsx",
          "gestion_wolkvox_2026-07.xlsx")
    excel = tmp_path / "gestion_wolkvox_2026-08-19_0816.xlsx"
    excel.write_text("nuevo", encoding="utf-8")

    carpeta_red.publicar_excel(excel, "2026-08-19", plantilla(tmp_path),
                               date(2026, 8, 19), dias_diarios=3)
    quedan = nombres(carpeta)
    assert "gestion_wolkvox_2026-S32.xlsx" in quedan
    assert "gestion_wolkvox_2026-S33.xlsx" in quedan
    assert "gestion_wolkvox_2026-07.xlsx" in quedan
    assert len([n for n in quedan if DIARIOS_RE.match(n)]) == 3


DIARIOS_RE = carpeta_red.DIARIO


def test_reprocesar_un_dia_viejo_no_desplaza_a_los_recientes(tmp_path):
    """Se ordena por la fecha del NOMBRE, no por la de modificacion: un
    backfill del 1 de agosto no puede echar al informe del 19.

    Sobrevive esa corrida porque una ejecucion nunca borra lo que acaba de
    escribir —crear y destruir el propio entregable es un defecto, no una
    politica—, asi que la carpeta queda con 4 diarios hasta la siguiente
    corrida, que ya lo purga.
    """
    carpeta = tmp_path / "2026" / "08. Agosto" / "Infomes de Gestion"
    crear(carpeta, "gestion_wolkvox_2026-08-17.xlsx",
          "gestion_wolkvox_2026-08-18.xlsx", "gestion_wolkvox_2026-08-19.xlsx")
    excel = tmp_path / "gestion_wolkvox_2026-08-01.xlsx"
    excel.write_text("backfill", encoding="utf-8")

    carpeta_red.publicar_excel(excel, "2026-08-01", plantilla(tmp_path),
                               date(2026, 8, 1), dias_diarios=3)
    assert "gestion_wolkvox_2026-08-19.xlsx" in nombres(carpeta)

    # La corrida siguiente lo deja en tres.
    otro = tmp_path / "gestion_wolkvox_2026-08-20.xlsx"
    otro.write_text("x", encoding="utf-8")
    carpeta_red.publicar_excel(otro, "2026-08-20", plantilla(tmp_path),
                               date(2026, 8, 20), dias_diarios=3)
    assert nombres(carpeta) == ["gestion_wolkvox_2026-08-18.xlsx",
                                "gestion_wolkvox_2026-08-19.xlsx",
                                "gestion_wolkvox_2026-08-20.xlsx"]


def test_sin_limite_configurado_no_se_purga_nada(tmp_path):
    carpeta = tmp_path / "2026" / "08. Agosto" / "Infomes de Gestion"
    crear(carpeta, "gestion_wolkvox_2026-08-10.xlsx", "gestion_wolkvox_2026-08-11.xlsx",
          "gestion_wolkvox_2026-08-12.xlsx", "gestion_wolkvox_2026-08-13.xlsx")
    excel = tmp_path / "gestion_wolkvox_2026-08-19_0816.xlsx"
    excel.write_text("x", encoding="utf-8")
    carpeta_red.publicar_excel(excel, "2026-08-19", plantilla(tmp_path), date(2026, 8, 19))
    assert len(nombres(carpeta)) == 5
