"""El filtro de actividad: cuentas de prueba o retiros recientes no deben
mover los promedios de toda la operación."""
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dominio import asistencia

JORNADA = {d: ["08:00", "18:00"] for d in
           ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado"]}
HORARIOS = {"tolerancia_min": 3, "por_defecto": JORNADA, "agentes": [],
            "agenda": {}, "festivos": set()}


def _detalle_de(filas):
    df = pd.DataFrame(filas)
    df["login_time_seg"] = 0
    return asistencia.detalle(df, HORARIOS, date(2026, 8, 3), date(2026, 8, 11),
                              hoy=date(2026, 8, 12))


# 2026-08-03 a 08-11 = lunes a martes siguiente = 8 días laborales (sin domingo 09)
FIJO = [{"agent_id": "1", "agent_name": "Ana", "fecha": f"2026-08-{d:02d}",
         "login": f"2026-08-{d:02d} 08:00:00", "logout": f"2026-08-{d:02d} 18:00:00"}
        for d in (3, 4, 5, 6, 7, 8, 10, 11)]
ESPORADICO = [{"agent_id": "9", "agent_name": "Cuenta prueba", "fecha": "2026-08-03",
               "login": "2026-08-03 14:00:00", "logout": "2026-08-03 14:10:00"}]


def test_marca_como_no_activo_a_quien_trabajo_poco():
    res = asistencia.por_agente(_detalle_de(FIJO + ESPORADICO), minimo_dias=5).set_index("Agente")
    assert res.loc["Ana", "Activo"] == "Sí"
    assert res.loc["Cuenta prueba", "Activo"] == "No"


def test_el_no_activo_sigue_apareciendo_en_el_informe():
    """Marcarlo no es esconderlo: el detalle debe seguir estando."""
    res = asistencia.por_agente(_detalle_de(FIJO + ESPORADICO), minimo_dias=5)
    assert "Cuenta prueba" in set(res["Agente"])


def test_general_ignora_a_los_no_activos():
    res = asistencia.por_agente(_detalle_de(FIJO + ESPORADICO), minimo_dias=5)
    gen = asistencia.general(res).set_index("Indicador")["Valor"]

    assert gen["Asesores activos evaluados"] == 1
    assert gen["Asesores excluidos (baja actividad)"] == 1
    # Ana trabajó los 8 días laborales: sin la exclusión, la cuenta de prueba
    # habría metido 7 días "sin conexión" al indicador general.
    assert gen["Días sin conexión"] == 0
    assert gen["% Días sin conexión"] == "0.0 %"


def test_sin_minimo_todos_cuentan_como_activos():
    res = asistencia.por_agente(_detalle_de(FIJO + ESPORADICO), minimo_dias=0)
    assert set(res["Activo"]) == {"Sí"}
