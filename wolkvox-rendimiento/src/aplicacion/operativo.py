"""Caso de uso: reporte operativo del día.

Extrae la ventana del día en curso, la transforma y publica el Excel
operativo. Orquesta dominio y adaptadores; no contiene reglas de negocio.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

import pandas as pd

from config.settings import Config, cargar_config
from src.adaptadores.almacen import respaldo
from src.adaptadores.publicacion import excel_operativo
from src.adaptadores.wolkvox import extraccion, traduccion
from src.adaptadores.wolkvox.cliente import WolkvoxClient

log = logging.getLogger(__name__)


def extraer(cfg: Config, dt_ini: datetime, dt_fin: datetime) -> dict[str, list[dict]]:
    """Consulta los 5 endpoints en serie: el token no admite paralelismo.
    Un endpoint que falla no tumba la corrida, devuelve vacío y queda en el log.
    """
    crudos: dict[str, list[dict]] = {}
    with WolkvoxClient(cfg.servidor, cfg.token, cfg.timeout_seg, cfg.reintentos) as api:
        tareas = {
            "agentes": lambda: extraccion.agentes_catalogo(api),
            "agente_dia": lambda: extraccion.agente_dia(api, dt_ini, dt_fin),
            "agente_hora": lambda: extraccion.agente_hora(api, dt_ini, dt_fin),
            "llamadas": lambda: extraccion.llamadas_detalle(api, dt_ini, dt_fin, cfg.bloque_horas),
            "no_conectadas": lambda: extraccion.llamadas_no_conectadas(api, dt_ini, dt_fin, cfg.bloque_horas),
        }
        for nombre, consultar in tareas.items():
            inicio = time.perf_counter()
            try:
                crudos[nombre] = consultar()
                log.info("%s: %d registros en %.1fs", nombre, len(crudos[nombre]),
                         time.perf_counter() - inicio)
            except Exception as e:
                crudos[nombre] = []
                log.error("%s falló: %s", nombre, e)
            time.sleep(2)  # respiro entre llamadas al mismo token
    return crudos


def transformar(crudos: dict[str, list[dict]], fecha: str, categorias: dict) -> dict[str, pd.DataFrame]:
    return {
        "agentes": traduccion.agentes(crudos["agentes"]),
        "agente_dia": traduccion.agente_dia(crudos["agente_dia"], fecha),
        "agente_hora": traduccion.agente_hora(crudos["agente_hora"], fecha),
        "llamadas": traduccion.llamadas(crudos["llamadas"], categorias),
        "no_conectadas": traduccion.llamadas_no_conectadas(crudos["no_conectadas"], categorias),
    }


def ejecutar(dias_atras: int = 0, sin_excel: bool = False) -> int:
    cfg = cargar_config()

    objetivo = datetime.now() - timedelta(days=dias_atras)
    dt_ini = objetivo.replace(hour=0, minute=0, second=0, microsecond=0)
    dt_fin = min(objetivo.replace(hour=23, minute=59, second=59, microsecond=0), datetime.now())
    fecha = dt_ini.strftime("%Y-%m-%d")
    log.info("Ventana: %s -> %s", dt_ini, dt_fin)

    crudos = extraer(cfg, dt_ini, dt_fin)

    if not crudos["agente_dia"] and not crudos["llamadas"]:
        # Preferible no actualizar el Excel (y que alguien lo note) a publicar
        # un reporte en ceros que parezca válido.
        log.error("Sin datos en las fuentes principales para %s. Se aborta.", fecha)
        return 1

    dfs = transformar(crudos, fecha, traduccion.cargar_categorias())

    respaldo.guardar(dfs, fecha, cfg.ruta_backup)
    respaldo.purgar(cfg.ruta_backup, cfg.dias_backup)

    if sin_excel:
        log.info("--sin-excel: backup guardado, no se genera reporte")
        return 0

    metadatos = {
        "Fecha procesada": fecha,
        "Ventana extraída": f"{dt_ini:%Y-%m-%d %H:%M} -> {dt_fin:%Y-%m-%d %H:%M}",
        "Generado en": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        **{f"Registros {nombre}": len(df) for nombre, df in dfs.items()},
    }
    destino = excel_operativo.generar(dfs, metadatos, cfg.ruta_salida)
    log.info("Reporte generado: %s", destino)
    return 0
