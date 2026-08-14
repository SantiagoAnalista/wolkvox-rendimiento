import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services import horarios_excel as hx

CANDIDATOS_2026 = [(2026, m) for m in range(5, 10)]


def test_parsea_hora_de_salida_como_tarde():
    """'8 A 5' es 8am a 5pm, no a las 5am."""
    assert hx._jornada("8 A 5") == ("08:00", "17:00")
    assert hx._jornada("8 A 5:30") == ("08:00", "17:30")
    assert hx._jornada("8:30 A 5:30") == ("08:30", "17:30")
    assert hx._jornada("9 A 6") == ("09:00", "18:00")
    assert hx._jornada("9 A 1") == ("09:00", "13:00")


def test_horas_altas_de_salida_se_toman_literales():
    """En jornada de sábado '8 A 12' termina a mediodía, no a medianoche."""
    assert hx._jornada("8 A 12") == ("08:00", "12:00")
    assert hx._jornada("9 A 11") == ("09:00", "11:00")
    assert hx._jornada("10 a 12") == ("10:00", "12:00")


def test_cero_o_vacio_significa_que_no_labora():
    assert hx._jornada("0") is None
    assert hx._jornada("") is None
    assert hx._jornada(None) is None
    assert hx._jornada(float("nan")) is None


def test_texto_no_reconocido_no_revienta():
    assert hx._jornada("DESCANSO") is None
    assert hx._jornada("En casa") is None


def test_normalizar_iguala_nombres_de_excel_y_api():
    """El Excel trae 'NOREÑA' y espacios dobles; el YAML no siempre."""
    assert hx.normalizar("VILLA NOREÑA LAURA") == hx.normalizar("villa norena  laura")
    assert hx.normalizar("CARDONA GARCIA  MARIA") == "CARDONA GARCIA MARIA"


def test_fechas_del_bloque_elige_el_mes_que_cuadra():
    """Días 1-6 con lunes a sábado solo cuadra con junio de 2026."""
    dias = [(1, "lunes"), (2, "martes"), (3, "miercoles"),
            (4, "jueves"), (5, "viernes"), (6, "sabado")]
    fechas = hx._fechas_del_bloque(dias, CANDIDATOS_2026)
    assert fechas[0] == date(2026, 6, 1)
    assert fechas[-1] == date(2026, 6, 6)


def test_fechas_del_bloque_cruza_de_mes():
    """El bloque de cierre va 29, 30 de junio y sigue en julio."""
    dias = [(29, "lunes"), (30, "martes"), (1, "miercoles"),
            (2, "jueves"), (3, "viernes"), (4, "sabado")]
    fechas = hx._fechas_del_bloque(dias, CANDIDATOS_2026)
    assert fechas[0] == date(2026, 6, 29)
    assert fechas[2] == date(2026, 7, 1)
    assert fechas[-1] == date(2026, 7, 4)


def test_bloque_desalineado_no_encuentra_mes():
    """Si los nombres de día no cuadran con ningún mes, debe fallar y no
    inventar fechas: calcular tardanzas contra un horario corrido sería peor
    que no calcularlas."""
    dias = [(1, "viernes"), (2, "viernes"), (3, "viernes")]
    assert hx._fechas_del_bloque(dias, CANDIDATOS_2026) is None


def test_lee_los_excel_reales_de_la_operacion():
    raiz = Path(__file__).resolve().parent.parent
    archivos = [
        {"ruta": "src/data/Horarios JULIO - Auto - Wolkvox.xlsx",
         "hojas": ["JULIO", "Ajustados final 1 vuelta "]},
        {"ruta": "src/data/Horarios AGOSTO -AUT Wolkvox.xlsx", "hojas": ["AGOSTO"]},
    ]
    grupos = {"SERGIO-YEISON-LAURA": ["FLOREZ ESPINAL SERGIO ANTONIO"],
              "CAROLINA -ALEJANDRA": ["ALZATE VALENCIA CAROLINA"]}
    if not (raiz / archivos[0]["ruta"]).exists():
        pytest.skip("Los Excel de horarios no están disponibles")

    agenda = hx.cargar(archivos, grupos, CANDIDATOS_2026, raiz)
    sergio = hx.normalizar("FLOREZ ESPINAL SERGIO ANTONIO")

    # Lunes 6 de julio: jornada completa según la hoja JULIO
    assert agenda[(sergio, date(2026, 7, 6))] == ("08:00", "17:00")
    # Sábado 11 de julio: media jornada
    assert agenda[(sergio, date(2026, 7, 11))] == ("08:00", "12:00")
    # 1 de julio sale del cronograma de junio, no de la hoja JULIO
    assert agenda[(sergio, date(2026, 7, 1))] == ("09:00", "17:00")
    # Agosto arranca el lunes 3
    assert agenda[(sergio, date(2026, 8, 3))] == ("08:00", "17:00")


def test_los_festivos_del_cronograma_no_son_dias_laborales():
    """El archivo marca 'Festivo' arriba, pero deja el '8 a 5' de la
    plantilla en la fila de horario. Manda la anotación: si no, el asesor
    aparecería ausente en un día que nadie trabajó."""
    raiz = Path(__file__).resolve().parent.parent
    archivos = [{"ruta": "src/data/Horarios JULIO - Auto - Wolkvox.xlsx", "hojas": ["JULIO"]},
                {"ruta": "src/data/Horarios AGOSTO -AUT Wolkvox.xlsx", "hojas": ["AGOSTO"]}]
    if not (raiz / archivos[0]["ruta"]).exists():
        pytest.skip("Los Excel de horarios no están disponibles")

    agenda = hx.cargar(archivos, {"SERGIO-YEISON-LAURA": ["SERGIO"]}, CANDIDATOS_2026, raiz)
    sergio = hx.normalizar("SERGIO")

    assert agenda[(sergio, date(2026, 7, 20))] is None   # Independencia
    assert agenda[(sergio, date(2026, 8, 7))] is None    # Batalla de Boyacá
    assert agenda[(sergio, date(2026, 8, 17))] is None   # Asunción
    assert agenda[(sergio, date(2026, 7, 18))] is None   # DESCANSO


def test_trabajar_en_casa_sigue_siendo_dia_laboral():
    raiz = Path(__file__).resolve().parent.parent
    archivos = [{"ruta": "src/data/Horarios AGOSTO -AUT Wolkvox.xlsx", "hojas": ["AGOSTO"]}]
    if not (raiz / archivos[0]["ruta"]).exists():
        pytest.skip("Los Excel de horarios no están disponibles")

    agenda = hx.cargar(archivos, {"SERGIO-YEISON-LAURA": ["SERGIO"]}, CANDIDATOS_2026, raiz)
    # 5 de agosto está marcado 'En casa': es teletrabajo, no ausencia
    assert agenda[(hx.normalizar("SERGIO"), date(2026, 8, 5))] == ("08:00", "17:00")


def test_grupo_no_configurado_se_ignora_sin_romper():
    raiz = Path(__file__).resolve().parent.parent
    archivos = [{"ruta": "src/data/Horarios AGOSTO -AUT Wolkvox.xlsx", "hojas": ["AGOSTO"]}]
    if not (raiz / archivos[0]["ruta"]).exists():
        pytest.skip("Los Excel de horarios no están disponibles")
    assert hx.cargar(archivos, {"GRUPO-INEXISTENTE": ["Alguien"]}, CANDIDATOS_2026, raiz) == {}
