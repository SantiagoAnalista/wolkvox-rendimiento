"""Limpieza de las maestras diarias.

Cada corrida intradía escribe un Excel NUEVO con la hora del corte
(`gestion_wolkbox_2026-08-17_1210.xlsx`) en vez de sobrescribir el anterior.
Es deliberado: en Windows, un `.xlsx` abierto por alguien queda tomado y
`wb.save()` falla con PermissionError. Escribiendo un archivo que hace un
segundo no existía, la colisión no puede ocurrir — no se maneja el error, se
elimina la posibilidad.

El precio es acumular archivos, y de eso se encarga este módulo, que corre en
cada ejecución:

  1. Un día ya cerrado se consolida: se conserva un solo archivo y se borran
     sus demás cortes. Gana el consolidado sin hora (el que escribe el job
     diario a la mañana siguiente); si no existe, el corte más tardío.
  2. Lo que quede fuera de la ventana de retención se borra.

Un día se da por cerrado si es anterior a hoy, si ya tiene su consolidado, o
si alguno de sus cortes es de `hora_cierre` en adelante. Así la última corrida
de la jornada limpia las anteriores sin necesidad de una bandera especial, y
si esa corrida falla, cualquier ejecución posterior termina el trabajo.

Los borrados que fallan (alguien tiene el archivo abierto) se registran y se
saltan: el archivo sobrante es inofensivo y la siguiente corrida lo reintenta.
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from pathlib import Path

from .excel_analisis import PREFIJO

log = logging.getLogger(__name__)

# gestion_wolkbox_2026-08-17.xlsx        -> consolidado del día
# gestion_wolkbox_2026-08-17_1210.xlsx   -> corte de las 12:10
# No casa los informes de semana (2026-S33) ni de mes (2026-08): esos no
# se generan varias veces al día y no entran en esta limpieza.
DIARIO = re.compile(rf"^{PREFIJO}_(\d{{4}}-\d{{2}}-\d{{2}})(?:_(\d{{2}})(\d{{2}}))?\.xlsx$")


def nombre_corte(fecha: date, hora: str) -> str:
    """'2026-08-17' + '12:10' -> 'gestion_wolkbox_2026-08-17_1210'."""
    return f"{PREFIJO}_{fecha.isoformat()}_{hora.replace(':', '')}"


def _diarios(ruta: Path) -> dict[str, list[tuple[int | None, Path]]]:
    """fecha -> [(minuto del día o None si es el consolidado, ruta)]."""
    encontrados: dict[str, list[tuple[int | None, Path]]] = {}
    for archivo in ruta.glob(f"{PREFIJO}_*.xlsx"):
        if archivo.name.startswith("~$"):
            continue                      # archivo de bloqueo de Excel
        m = DIARIO.match(archivo.name)
        if not m:
            continue
        fecha, hh, mm = m.groups()
        minuto = None if hh is None else int(hh) * 60 + int(mm)
        encontrados.setdefault(fecha, []).append((minuto, archivo))
    return encontrados


def _superviviente(cortes: list[tuple[int | None, Path]]) -> Path:
    """El consolidado sin hora manda; si no existe, el corte más tardío."""
    sin_hora = [p for minuto, p in cortes if minuto is None]
    if sin_hora:
        return sin_hora[0]
    return max(cortes, key=lambda c: c[0])[1]


def _borrar(archivo: Path) -> bool:
    try:
        archivo.unlink()
        return True
    except OSError as e:
        # Lo más probable: alguien lo tiene abierto en Excel. No es un fallo
        # de la corrida; la siguiente lo reintenta.
        log.warning("Maestra: no se pudo borrar %s (%s)", archivo.name, e)
        return False


def limpiar(ruta: Path, dias: int = 3, hora_cierre: int = 18,
            hoy: date | None = None, recien_escritos: list[Path] | None = None) -> list[Path]:
    """Consolida los días cerrados y aplica la retención. Devuelve lo borrado.

    `recien_escritos` son las maestras que produjo ESTA corrida, y quedan
    intocables. Sin eso, reprocesar un día viejo lo generaba y lo borraba
    acto seguido: la ventana de retención se mide contra hoy, así que un
    backfill del 12 de agosto cae fuera de ella el mismo día que se pide.
    Cada regla por separado es correcta; lo que no puede ser es que una
    corrida destruya el archivo que le acaban de encargar.

    Ojo: los protege durante esta corrida, no para siempre. La siguiente
    aplicará la retención normal sobre ellos, que es lo que se quiere para
    las corridas programadas. Si hace falta conservar backfills antiguos,
    la palanca es DIAS_EXCEL.
    """
    if not ruta.exists():
        return []

    hoy = hoy or date.today()
    vigentes = {(hoy - timedelta(days=n)).isoformat() for n in range(dias)}
    corte_min = hora_cierre * 60
    intocables = {p.resolve() for p in (recien_escritos or [])}
    borrable = lambda archivo: archivo.resolve() not in intocables

    borrados = []
    for fecha, cortes in _diarios(ruta).items():
        if fecha not in vigentes:
            for _, archivo in cortes:
                if borrable(archivo) and _borrar(archivo):
                    borrados.append(archivo)
            continue

        if len(cortes) < 2:
            continue

        cerrado = (fecha < hoy.isoformat()
                   or any(minuto is None for minuto, _ in cortes)
                   or any(minuto >= corte_min for minuto, _ in cortes if minuto is not None))
        if not cerrado:
            continue                       # jornada en curso: se conservan todos

        conservado = _superviviente(cortes)
        for _, archivo in cortes:
            if archivo != conservado and borrable(archivo) and _borrar(archivo):
                borrados.append(archivo)

    if borrados:
        log.info("Maestra: %d archivo(s) diarios eliminados (retención %d días)",
                 len(borrados), dias)
    return borrados


def vigentes(ruta: Path) -> set[str]:
    """Nombres de los Excel que existen ahora mismo en la carpeta.

    El tablero enlaza a la maestra por nombre; con esto se evita publicar un
    enlace a un archivo que la retención ya borró.
    """
    if not ruta.exists():
        return set()
    return {a.name for a in ruta.glob(f"{PREFIJO}_*.xlsx")
            if not a.name.startswith("~$")}
