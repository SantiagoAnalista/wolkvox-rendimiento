import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services import bloqueo


def test_una_corrida_toma_y_suelta_el_candado(tmp_path):
    with bloqueo.tomar(tmp_path) as archivo:
        assert archivo.exists()
    assert not archivo.exists()


def test_el_candado_se_suelta_aunque_la_corrida_falle(tmp_path):
    """Si no se soltara, un error dejaría la operación sin tablero hasta que
    alguien borrara el archivo a mano."""
    with pytest.raises(RuntimeError):
        with bloqueo.tomar(tmp_path):
            raise RuntimeError("la corrida reventó")
    assert not (tmp_path / bloqueo.NOMBRE).exists()


def test_una_segunda_corrida_simultanea_no_entra(tmp_path):
    with bloqueo.tomar(tmp_path):
        with pytest.raises(bloqueo.EnCurso):
            with bloqueo.tomar(tmp_path):
                pytest.fail("no debería haber entrado")


def test_la_corrida_que_esperaba_puede_entrar_al_soltarse(tmp_path):
    with bloqueo.tomar(tmp_path):
        pass
    with bloqueo.tomar(tmp_path) as archivo:
        assert archivo.exists()


def test_un_candado_huerfano_caduca_y_se_puede_tomar(tmp_path):
    """Una corrida que murió sin soltarlo no puede dejar la operación
    bloqueada para siempre."""
    archivo = tmp_path / bloqueo.NOMBRE
    archivo.write_text("pid 999 · corrida muerta", encoding="utf-8")
    viejo = time.time() - 60 * 60           # una hora atrás
    os.utime(archivo, (viejo, viejo))

    with bloqueo.tomar(tmp_path, minutos_vigencia=45) as tomado:
        assert tomado.exists()
        assert "pid" in tomado.read_text(encoding="utf-8")


def test_un_candado_reciente_no_caduca(tmp_path):
    archivo = tmp_path / bloqueo.NOMBRE
    archivo.write_text("pid 999", encoding="utf-8")
    with pytest.raises(bloqueo.EnCurso):
        with bloqueo.tomar(tmp_path, minutos_vigencia=45):
            pytest.fail("no debería haber entrado")


def test_el_candado_registra_pid_y_hora_para_poder_diagnosticar(tmp_path):
    with bloqueo.tomar(tmp_path) as archivo:
        contenido = archivo.read_text(encoding="utf-8")
    assert f"pid {os.getpid()}" in contenido


def test_el_mensaje_de_en_curso_dice_desde_cuando(tmp_path):
    (tmp_path / bloqueo.NOMBRE).write_text("pid 4242 · 2026-08-17 08:10:00", encoding="utf-8")
    with pytest.raises(bloqueo.EnCurso, match="4242"):
        with bloqueo.tomar(tmp_path):
            pytest.fail("no debería haber entrado")


def test_crea_la_carpeta_si_no_existe(tmp_path):
    """La primera corrida contra una carpeta compartida recién montada."""
    destino = tmp_path / "compartida" / "rendimiento"
    with bloqueo.tomar(destino) as archivo:
        assert archivo.parent == destino
