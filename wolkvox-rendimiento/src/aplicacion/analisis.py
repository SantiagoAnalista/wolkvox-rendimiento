"""Caso de uso: informe de gestión (puntualidad, tiempos y efectividad).

Se ejecuta por mes, por semana, por día cerrado o sobre la jornada en curso.
El periodo elegido gobierna la extracción y no solo el reporte: agent_1 y
agent_3 agregan todo el rango consultado sin desglose interno, así que las
cifras de una semana solo salen consultando esa semana.

Orquesta dominio y adaptadores. Las cifras las calcula el dominio; aquí solo
se decide qué se pide, en qué orden y a dónde va.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from config.settings import Config, cargar_config
from src.adaptadores.almacen import horarios as adaptador_horarios
from src.adaptadores.almacen import respaldo
from src.adaptadores.publicacion import (excel_analisis, retencion, tablero_datos,
                                         tablero_html)
from src.adaptadores.wolkvox import extraccion, traduccion
from src.adaptadores.wolkvox.cliente import WolkvoxClient
from src.dominio import asistencia, gestion

log = logging.getLogger(__name__)

# cdr_1 se pide de una sola vez por periodo: mes, semana y día caben de sobra
# en el límite de 31 días por consulta. Si alguna vez el volumen supera el
# tope de registros de la API, este es el número que hay que bajar.
BLOQUE_HORAS_ANALISIS = 24 * 31


def extraer_analisis(cfg: Config, desde: date, hasta: date, periodo: str = "mes",
                     corte: datetime | None = None) -> dict[str, pd.DataFrame]:
    tramos = extraccion.partir_periodo(desde, hasta, periodo)
    if corte:
        # Jornada en curso: no tiene sentido pedirle a la API hasta las 23:59
        # de un día que no ha terminado.
        tramos = [(e, ini, min(fin, corte)) for e, ini, fin in tramos]
    log.info("Rango partido en %d %s(s): %s", len(tramos), periodo, [t[0] for t in tramos])

    acumulado: dict[str, list[pd.DataFrame]] = {
        "logueo": [], "agente": [], "auxiliar": [], "auxiliar_dia": [],
        "llamadas": [], "chats_prod": [], "chats": []}

    with WolkvoxClient(cfg.servidor, cfg.token, cfg.timeout_seg, cfg.reintentos) as api:
        codigos = traduccion.codigos_tipificacion(extraccion.codigos_tipificacion(api))
        log.info("Códigos de tipificación: %d", len(codigos))
        time.sleep(2)

        for etiqueta, ini, fin in tramos:
            fuentes = {
                "logueo": (lambda: extraccion.logueo_por_dia(api, ini, fin), traduccion.logueo_por_dia),
                "agente": (lambda: extraccion.agente_dia(api, ini, fin),
                           lambda r: traduccion.agente_dia(r, etiqueta)),
                "auxiliar": (lambda: extraccion.tiempo_auxiliar(api, ini, fin), traduccion.tiempo_auxiliar),
                "auxiliar_dia": (lambda: extraccion.tiempo_auxiliar_por_dia(api, ini, fin),
                                 traduccion.tiempo_auxiliar),
                "llamadas": (lambda: extraccion.llamadas_detalle(api, ini, fin, BLOQUE_HORAS_ANALISIS),
                             traduccion.llamadas),
                "chats_prod": (lambda: extraccion.chats_productividad(api, ini, fin),
                               traduccion.chats_productividad),
                "chats": (lambda: extraccion.chats_detalle(api, ini, fin), traduccion.chats_detalle),
            }
            for nombre, (consultar, normalizar) in fuentes.items():
                try:
                    df = normalizar(consultar())
                    if not df.empty:
                        df["Periodo"] = etiqueta
                    acumulado[nombre].append(df)
                    log.info("  %s %s: %d registros", etiqueta, nombre, len(df))
                except Exception as e:
                    log.error("  %s %s falló: %s", etiqueta, nombre, e)
                time.sleep(2)

    dfs = {k: (pd.concat(v, ignore_index=True) if any(not d.empty for d in v) else pd.DataFrame())
           for k, v in acumulado.items()}
    dfs["codigos"] = codigos
    return dfs


MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
         "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def _titulo_periodo(periodo: str, etiqueta: str, desde: date, hasta: date) -> str:
    """Cómo se anuncia el periodo en la portada del Excel."""
    fechas = f"{desde:%d/%m/%Y} al {hasta:%d/%m/%Y}"
    if periodo == "mes":
        anio, numero = etiqueta.split("-")
        return f"{MESES[int(numero) - 1].capitalize()} {anio}  ({fechas})"
    if periodo == "semana":
        return f"Semana {etiqueta.split('-S')[1]} de {etiqueta[:4]}  ({fechas})"
    return f"{desde:%d/%m/%Y}"


def _informe_del_periodo(dfs: dict, horarios: dict, periodo: str, etiqueta: str,
                         desde: date, hasta: date, cfg: Config,
                         corte: datetime | None = None) -> Path | None:
    """Arma y escribe el Excel de un solo periodo (mes, semana o día)."""
    incluidos = horarios["agentes"]
    del_periodo = {k: (v[v["Periodo"] == etiqueta] if not v.empty and "Periodo" in v.columns else v)
                   for k, v in dfs.items()}

    detalle = asistencia.detalle(del_periodo["logueo"], horarios, desde, hasta)
    if detalle.empty:
        log.warning("%s: sin días laborales para los asesores del informe "
                    "(festivo, domingo o descanso general). Se omite.", etiqueta)
        return None

    punt_agente = asistencia.por_agente(detalle, horarios["minimo_dias_actividad"])
    activos = set(punt_agente.loc[punt_agente["Activo"] == "Sí", "Agente"])
    log.info("%s: %d asesores (%d activos)", etiqueta, len(punt_agente), len(activos))

    filtrar = lambda clave: gestion.filtrar_agentes(del_periodo[clave], incluidos)
    agente_mes, auxiliar = filtrar("agente"), filtrar("auxiliar")
    auxiliar_dia = filtrar("auxiliar_dia")

    tiempos = gestion.tiempos_por_agente(
        agente_mes, asistencia.dias_trabajados_por_periodo(detalle, etiqueta))
    efect = gestion.efectividad(filtrar("llamadas"), filtrar("chats"), filtrar("chats_prod"),
                                agente_mes, gestion.mapa_codigos(dfs["codigos"]))

    cuadros = {
        "general_puntualidad": asistencia.general(punt_agente),
        "general_gestion": gestion.general(tiempos, efect, activos),
        "resumen_agente": gestion.resumen_por_agente(punt_agente, tiempos, efect,
                                                     horarios["umbrales"], auxiliar),
        "puntualidad_agente": punt_agente,
        "puntualidad_detalle": detalle,
        "tiempos": tiempos,
        "auxiliares_hm": gestion.auxiliares_hm(auxiliar),
        "auxiliares_horas": gestion.auxiliares_horas(auxiliar),
        "auxiliares_dia": gestion.auxiliares_dia_a_dia(auxiliar_dia),
        "auxiliares": gestion.auxiliares_por_tipo(auxiliar),
        "efectividad": efect,
        "cruce": gestion.cruce(tiempos, efect, activos, horarios["umbrales"]),
        "curva_horaria": gestion.curva_horaria(filtrar("llamadas")),
    }

    hora_corte = f"{corte:%H:%M}" if corte else None
    titulo = _titulo_periodo(periodo, etiqueta, desde, hasta)
    metadatos = {
        "Periodo": f"{titulo} · corte {hora_corte}" if hora_corte else titulo,
        "Agrupación": periodo,
        "Asesores": ", ".join(sorted(punt_agente["Agente"])),
        "Horario": "Cronograma real de la operación, leído de los Excel de horarios",
        "Tolerancia": f"{horarios['tolerancia_min']} minutos, tanto en entrada como en salida",
        "Días sin login": "Se reportan como 'sin conexión' y NO cuentan en el % de entradas tarde",
        "Día en curso": "Excluido del cálculo de salidas temprano (la jornada aún no termina)",
        "Efectividad": "Una gestión es efectiva si su código de tipificación está marcado como 'hit' en Wolkvox",
        "Efectividad voz": "Se mide sobre las llamadas que lograron contacto (RPC), no sobre todo lo marcado",
        "Sin tipificar": "TIMEOUTCHAT / TIMEOUTACW / vacío se cuentan aparte y salen del denominador de efectividad",
        "Generado": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    # Cada corte escribe un archivo NUEVO en vez de sobrescribir: un .xlsx que
    # alguien tiene abierto no se puede reemplazar en Windows, y así la
    # colisión no puede darse. excel_purga los consolida al cerrar la jornada.
    nombre = (retencion.nombre_corte(desde, hora_corte) if hora_corte
              else f"analisis_gestion_{etiqueta}")
    destino = excel_analisis.generar(cuadros, metadatos, cfg.ruta_salida, nombre=nombre)

    # El tablero se alimenta de los MISMOS cuadros, sin recalcular. Aquí solo
    # se persiste el periodo; el HTML se rearma al final con todo el histórico.
    tablero_datos.guardar(
        tablero_datos.construir(cuadros, metadatos, etiqueta, periodo, desde, hasta,
                                horarios["umbrales"], horarios["tolerancia_min"],
                                corte=hora_corte, archivo_excel=destino.name),
        cfg.ruta_tablero)
    return destino


FUENTES_ANALISIS = ["logueo", "agente", "auxiliar", "auxiliar_dia",
                    "llamadas", "chats_prod", "chats", "codigos"]


def _leer_backup(cfg: Config, desde: date, hasta: date) -> dict[str, pd.DataFrame]:
    """Rearma el análisis desde el backup CSV, sin tocar la API.

    Sirve para regenerar el Excel tras cambiar formatos o reglas, y para
    trabajar cuando la API no responde.
    """
    marca = f"{desde:%Y%m%d}_{hasta:%Y%m%d}"
    numericas = {"calls", "inbound", "outbound", "internal", "hits", "rpc",
                 "ocupacion", "total_chats", "hora", "time_seg"}
    dfs = {n: respaldo.cargar(f"analisis_{n}", marca, cfg.ruta_backup) for n in FUENTES_ANALISIS}
    for nombre, df in dfs.items():
        # El CSV se lee como texto: hay que devolver a número todo lo que se
        # sume o promedie después (los segundos y los conteos).
        for col in df.columns:
            if col.endswith("_seg") or col in numericas:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
        log.info("  backup %s: %d registros", nombre, len(df))
    return dfs


def analizar(desde: date, hasta: date, periodo: str = "mes",
             desde_backup: bool = False, corte: datetime | None = None) -> int:
    cfg = cargar_config()
    horarios = adaptador_horarios.cargar_horarios(desde, hasta, periodo)
    log.info("Análisis de gestión por %s: %s -> %s%s", periodo, desde, hasta,
             f" (corte {corte:%H:%M})" if corte else "")
    log.info("Asesores del informe: %d | días con horario cargado: %d | mínimo para promedios: %d día(s)",
             len(horarios["agentes"]), len(horarios["agenda"]), horarios["minimo_dias_actividad"])

    if desde_backup:
        log.info("Modo --desde-backup: no se consulta la API")
        dfs = _leer_backup(cfg, desde, hasta)
    else:
        dfs = extraer_analisis(cfg, desde, hasta, periodo, corte)

    if dfs["logueo"].empty and dfs["agente"].empty:
        # En una jornada en curso esto es un estado legítimo, no un fallo: en
        # el corte de las 08:10 puede que todavía no se haya conectado nadie,
        # y eso es justo lo que el coordinador necesita ver.
        if not corte:
            log.error("Sin datos de agentes en el periodo. Se aborta.")
            return 1
        log.warning("Corte %s: aún sin actividad. Se publica la nómina sin conexión.", f"{corte:%H:%M}")

    if not desde_backup:
        respaldo.guardar({f"analisis_{k}": v for k, v in dfs.items()},
                       f"{desde:%Y%m%d}_{hasta:%Y%m%d}", cfg.ruta_backup)

    generados = []
    for etiqueta, ini, fin in extraccion.partir_periodo(desde, hasta, periodo):
        destino = _informe_del_periodo(dfs, horarios, periodo, etiqueta,
                                       ini.date(), fin.date(), cfg, corte)
        if destino:
            generados.append(destino)
            log.info("Informe generado: %s", destino)

    if not generados:
        log.error("No se generó ningún informe.")
        return 1
    log.info("%d informe(s) en %s", len(generados), cfg.ruta_salida)

    # Consolida los cortes de las jornadas ya cerradas y aplica la retención,
    # sin tocar lo que esta corrida acaba de escribir.
    retencion.limpiar(cfg.ruta_salida, cfg.dias_excel, cfg.hora_cierre_jornada,
                      recien_escritos=generados)

    # Un solo HTML con todos los periodos del almacén, no solo los de esta
    # corrida: el coordinador conserva un único enlace y ve el histórico.
    tablero_datos.purgar(cfg.ruta_tablero)
    tablero = tablero_html.generar(tablero_datos.cargar_todos(cfg.ruta_tablero),
                                   cfg.ruta_tablero,
                                   excel_vigentes=retencion.vigentes(cfg.ruta_salida))
    log.info("Tablero generado: %s", tablero)
    return 0
