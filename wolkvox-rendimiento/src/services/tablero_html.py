"""Render del tablero: plantilla + almacén de periodos -> un solo HTML.

El HTML es autocontenido (CSS y JS embebidos, sin CDN) para que funcione
abriéndolo desde una carpeta compartida, sin servidor ni permisos.

Este módulo no calcula nada: recibe los periodos ya serializados por
tablero_datos y los inyecta en la plantilla.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

PLANTILLA = Path(__file__).parent / "plantillas" / "tablero.html"
MARCADOR = "__PAYLOAD__"
NOMBRE = "tablero.html"


def _inyectable(datos: dict) -> str:
    """JSON listo para ir dentro de <script type="application/json">.

    Solo hay que neutralizar '</', que cerraría la etiqueta antes de tiempo.
    `ensure_ascii=False` mantiene las tildes legibles y pesa menos.
    """
    return json.dumps(datos, ensure_ascii=False).replace("</", "<\\/")


INTENTOS = 3
ESPERA_SEG = 2


def _reemplazar(temporal: Path, destino: Path, intentos: int = INTENTOS,
                espera: float = ESPERA_SEG) -> None:
    """os.replace con reintentos.

    Los navegadores leen el HTML y sueltan el archivo, así que un coordinador
    con la pestaña abierta no estorba. Lo que sí puede retener el archivo una
    fracción de segundo es el antivirus o el indexador de Windows, y eso se
    resuelve esperando, no fallando.
    """
    for intento in range(1, intentos + 1):
        try:
            os.replace(temporal, destino)
            return
        except OSError as e:
            if intento == intentos:
                raise
            log.warning("Tablero: %s ocupado (%s). Reintento %d de %d en %.0fs",
                        destino.name, e, intento + 1, intentos, espera)
            time.sleep(espera)


def generar(periodos: list[dict], ruta_salida: Path, nombre: str = NOMBRE,
            plantilla: Path | None = None,
            excel_vigentes: set[str] | None = None) -> Path:
    """Escribe el tablero con todos los periodos del almacén.

    La escritura es atómica: si la corrida muere a mitad, el coordinador
    sigue viendo el tablero anterior completo en vez de un archivo truncado.

    `excel_vigentes` son los nombres de maestra que existen ahora mismo. Los
    periodos cuyo Excel ya borró la retención pierden el enlace en vez de
    ofrecer uno roto.
    """
    origen = plantilla or PLANTILLA
    if not origen.exists():
        raise FileNotFoundError(f"Falta la plantilla del tablero: {origen}")

    html = origen.read_text(encoding="utf-8")
    if MARCADOR not in html:
        raise ValueError(f"La plantilla {origen.name} no contiene {MARCADOR}")

    if excel_vigentes is not None:
        periodos = [{**p, "archivo_excel": (p.get("archivo_excel")
                                            if p.get("archivo_excel") in excel_vigentes else None)}
                    for p in periodos]

    datos = {
        "generado": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "periodos": periodos,
    }
    html = html.replace(MARCADOR, _inyectable(datos))

    ruta_salida.mkdir(parents=True, exist_ok=True)
    destino = ruta_salida / nombre
    temporal = destino.with_suffix(".html.tmp")
    temporal.write_text(html, encoding="utf-8")
    _reemplazar(temporal, destino)

    log.info("Tablero: %s con %d periodo(s), %d KB",
             destino.name, len(periodos), destino.stat().st_size // 1024)
    return destino
