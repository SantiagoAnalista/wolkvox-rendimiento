import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services import backup

DF = pd.DataFrame([{"conn_id": "1", "agent_name": "Ana"}])


def _ruta_temporal() -> Path:
    return Path(tempfile.mkdtemp())


def test_guardar_escribe_un_csv_por_dataset():
    ruta = _ruta_temporal()
    escritos = backup.guardar({"llamadas": DF, "agentes": DF}, "2026-08-11", ruta)
    assert len(escritos) == 2
    assert (ruta / "llamadas_2026-08-11.csv").exists()


def test_guardar_omite_dataframes_vacios():
    ruta = _ruta_temporal()
    escritos = backup.guardar({"llamadas": DF, "vacio": pd.DataFrame()}, "2026-08-11", ruta)
    assert len(escritos) == 1


def test_reprocesar_el_mismo_dia_sobrescribe_sin_duplicar():
    ruta = _ruta_temporal()
    backup.guardar({"llamadas": DF}, "2026-08-11", ruta)
    backup.guardar({"llamadas": DF}, "2026-08-11", ruta)
    assert len(list(ruta.glob("llamadas_*.csv"))) == 1
    assert len(backup.cargar("llamadas", "2026-08-11", ruta)) == 1


def test_cargar_inexistente_devuelve_vacio():
    assert backup.cargar("llamadas", "1999-01-01", _ruta_temporal()).empty


def test_purgar_borra_lo_viejo_y_conserva_lo_vigente():
    ruta = _ruta_temporal()
    hoy = datetime.today().strftime("%Y-%m-%d")
    viejo = (datetime.today() - timedelta(days=10)).strftime("%Y-%m-%d")
    backup.guardar({"llamadas": DF}, hoy, ruta)
    backup.guardar({"llamadas": DF}, viejo, ruta)

    borrados = backup.purgar(ruta, dias=3)

    assert len(borrados) == 1
    assert (ruta / f"llamadas_{hoy}.csv").exists()
    assert not (ruta / f"llamadas_{viejo}.csv").exists()


def test_purgar_ignora_archivos_ajenos():
    ruta = _ruta_temporal()
    (ruta / "notas.csv").write_text("no me borres", encoding="utf-8")
    backup.purgar(ruta, dias=3)
    assert (ruta / "notas.csv").exists()
