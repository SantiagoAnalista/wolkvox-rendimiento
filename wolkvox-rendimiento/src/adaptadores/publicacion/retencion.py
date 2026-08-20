"""Limpieza de las maestras diarias.

Cada corrida intradía escribe un Excel NUEVO con la hora del corte
(`gestion_wolkvox_2026-08-17_1210.xlsx`) en vez de sobrescribir el anterior.
Es deliberado: en Windows, un `.xlsx` abierto por alguien queda tomado y
`wb.save()` falla con PermissionError. Escribiendo un archivo que hace un
segundo no existía, la colisión no puede ocurrir — no se maneja el error, se
elimina la posibilidad.

El precio es acumular archivos, y de eso se encarga este módulo, que corre en
cada ejecución:

  1. **De cada día queda un solo archivo.** Gana el consolidado sin hora (el
     que escribe el job de la mañana siguiente, y que cubre la jornada
     entera); si no existe todavía, el corte más tardío.
  2. Lo que quede fuera de la ventana de retención se borra.

La consolidación no espera al cierre de la jornada: se hace en cada corrida.
Escribir un archivo nuevo es lo que evita el bloqueo de Windows, pero no hay
razón para conservar el anterior una vez existe uno más completo — a media
mañana, tres cortes del mismo día solo obligan a coordinación a mirar la hora
del nombre para saber cuál abrir.

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


def limpiar(ruta: Path, dias: int = 3,
            hoy: date | None = None, recien_escritos: list[Path] | None = None) -> list[Path]:
    """Deja un solo archivo por día y aplica la retención. Devuelve lo borrado.

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

        conservado = _superviviente(cortes)
        for _, archivo in cortes:
            if archivo != conservado and borrable(archivo) and _borrar(archivo):
                borrados.append(archivo)

    if borrados:
        log.info("Maestra: %d archivo(s) diarios eliminados (retención %d días)",
                 len(borrados), dias)
    return borrados


# El prefijo estuvo mal escrito hasta agosto de 2026. Los archivos que
# quedaron con el nombre viejo son invisibles para todo lo de arriba —el glob
# y la expresión regular usan PREFIJO—, así que ni se consolidan ni caducan:
# se quedarían para siempre. Se renombran en vez de borrarse porque entre
# ellos hay semanas y meses que no tienen equivalente nuevo.
PREFIJOS_HISTORICOS = ("gestion_wolkbox", "analisis_gestion")


def migrar_prefijo(ruta: Path) -> list[Path]:
    """Renombra las maestras del prefijo viejo. Devuelve las migradas.

    Si el destino ya existe, el archivo viejo se borra: el nuevo lo generó
    una corrida posterior y es al menos tan completo.

    Se puede quitar cuando ninguna carpeta —local, de red o del servidor de
    Jenkins— tenga ya archivos con los nombres antiguos.
    """
    if not ruta.exists():
        return []

    migrados = []
    for viejo in PREFIJOS_HISTORICOS:
        for archivo in ruta.glob(f"{viejo}_*.xlsx"):
            if archivo.name.startswith("~$"):
                continue
            destino = archivo.with_name(archivo.name.replace(viejo, PREFIJO, 1))
            try:
                if destino.exists():
                    archivo.unlink()
                    log.info("Maestra: %s ya existe con el nombre nuevo; se borra el viejo",
                             destino.name)
                else:
                    archivo.rename(destino)
                    log.info("Maestra: %s -> %s", archivo.name, destino.name)
                migrados.append(destino)
            except OSError as e:
                log.warning("Maestra: no se pudo migrar %s (%s)", archivo.name, e)
    return migrados


def vigentes(ruta: Path) -> set[str]:
    """Nombres de los Excel que existen ahora mismo en la carpeta.

    El tablero enlaza a la maestra por nombre; con esto se evita publicar un
    enlace a un archivo que la retención ya borró.
    """
    if not ruta.exists():
        return set()
    return {a.name for a in ruta.glob(f"{PREFIJO}_*.xlsx")
            if not a.name.startswith("~$")}
