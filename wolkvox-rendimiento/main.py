"""Orquestador. El flujo completo es: extraer -> transformar -> exportar.

Reporte diario de operación:
    python main.py                  # procesa el día de hoy hasta la hora actual
    python main.py --dias-atras 1   # reprocesa el día de ayer completo
    python main.py --sin-excel      # solo extrae y respalda, no genera Excel

Informe de análisis de gestión (puntualidad, tiempos, efectividad):
    python main.py --analisis --desde 2026-07-01 --hasta 2026-08-12
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from config.logger_config import setup_logger
from config.settings import Config, cargar_config
from src.api import extract
from src.api.client import WolkvoxClient
from src.services import (asistencia, backup, bloqueo, excel_purga, gestion, report,
                          reporte_analisis, tablero_datos, tablero_html, transform)

log = logging.getLogger("main")


def extraer(cfg: Config, dt_ini: datetime, dt_fin: datetime) -> dict[str, list[dict]]:
    """Consulta los 5 endpoints en serie: el token no admite paralelismo.
    Un endpoint que falla no tumba la corrida, devuelve vacío y queda en el log.
    """
    crudos: dict[str, list[dict]] = {}
    with WolkvoxClient(cfg.servidor, cfg.token, cfg.timeout_seg, cfg.reintentos) as api:
        tareas = {
            "agentes": lambda: extract.agentes_catalogo(api),
            "agente_dia": lambda: extract.agente_dia(api, dt_ini, dt_fin),
            "agente_hora": lambda: extract.agente_hora(api, dt_ini, dt_fin),
            "llamadas": lambda: extract.llamadas_detalle(api, dt_ini, dt_fin, cfg.bloque_horas),
            "no_conectadas": lambda: extract.llamadas_no_conectadas(api, dt_ini, dt_fin, cfg.bloque_horas),
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
        "agentes": transform.agentes(crudos["agentes"]),
        "agente_dia": transform.agente_dia(crudos["agente_dia"], fecha),
        "agente_hora": transform.agente_hora(crudos["agente_hora"], fecha),
        "llamadas": transform.llamadas(crudos["llamadas"], categorias),
        "no_conectadas": transform.llamadas_no_conectadas(crudos["no_conectadas"], categorias),
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

    dfs = transformar(crudos, fecha, transform.cargar_categorias())

    backup.guardar(dfs, fecha, cfg.ruta_backup)
    backup.purgar(cfg.ruta_backup, cfg.dias_backup)

    if sin_excel:
        log.info("--sin-excel: backup guardado, no se genera reporte")
        return 0

    metadatos = {
        "Fecha procesada": fecha,
        "Ventana extraída": f"{dt_ini:%Y-%m-%d %H:%M} -> {dt_fin:%Y-%m-%d %H:%M}",
        "Generado en": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        **{f"Registros {nombre}": len(df) for nombre, df in dfs.items()},
    }
    destino = report.generar(dfs, metadatos, cfg.ruta_salida)
    log.info("Reporte generado: %s", destino)
    return 0


# ---------------------------------------------------------------------------
# Modo análisis: puntualidad, uso del tiempo y efectividad de la gestión.
#
# Se ejecuta por mes, por semana o por día. El periodo elegido gobierna la
# extracción y no solo el reporte: agent_1 y agent_3 agregan todo el rango
# consultado sin desglose interno, así que las cifras de una semana solo
# salen consultando esa semana.
# ---------------------------------------------------------------------------

def extraer_analisis(cfg: Config, desde: date, hasta: date, periodo: str = "mes",
                     corte: datetime | None = None) -> dict[str, pd.DataFrame]:
    tramos = extract.partir_periodo(desde, hasta, periodo)
    if corte:
        # Jornada en curso: no tiene sentido pedirle a la API hasta las 23:59
        # de un día que no ha terminado.
        tramos = [(e, ini, min(fin, corte)) for e, ini, fin in tramos]
    log.info("Rango partido en %d %s(s): %s", len(tramos), periodo, [t[0] for t in tramos])

    acumulado: dict[str, list[pd.DataFrame]] = {
        "logueo": [], "agente": [], "auxiliar": [], "auxiliar_dia": [],
        "llamadas": [], "chats_prod": [], "chats": []}

    with WolkvoxClient(cfg.servidor, cfg.token, cfg.timeout_seg, cfg.reintentos) as api:
        codigos = transform.codigos_tipificacion(extract.codigos_tipificacion(api))
        log.info("Códigos de tipificación: %d", len(codigos))
        time.sleep(2)

        for etiqueta, ini, fin in tramos:
            fuentes = {
                "logueo": (lambda: extract.logueo_por_dia(api, ini, fin), transform.logueo_por_dia),
                "agente": (lambda: extract.agente_dia(api, ini, fin),
                           lambda r: transform.agente_dia(r, etiqueta)),
                "auxiliar": (lambda: extract.tiempo_auxiliar(api, ini, fin), transform.tiempo_auxiliar),
                "auxiliar_dia": (lambda: extract.tiempo_auxiliar_por_dia(api, ini, fin),
                                 transform.tiempo_auxiliar),
                "llamadas": (lambda: extract.llamadas_detalle(api, ini, fin, 24 * 31),
                             lambda r: transform.llamadas(r, {"conectadas": {}, "no_conectadas": {}})),
                "chats_prod": (lambda: extract.chats_productividad(api, ini, fin),
                               transform.chats_productividad),
                "chats": (lambda: extract.chats_detalle(api, ini, fin), transform.chats_detalle),
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
    nombre = (excel_purga.nombre_corte(desde, hora_corte) if hora_corte
              else f"analisis_gestion_{etiqueta}")
    destino = reporte_analisis.generar(cuadros, metadatos, cfg.ruta_salida, nombre=nombre)

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
    dfs = {n: backup.cargar(f"analisis_{n}", marca, cfg.ruta_backup) for n in FUENTES_ANALISIS}
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
    horarios = asistencia.cargar_horarios(desde, hasta, periodo)
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
        backup.guardar({f"analisis_{k}": v for k, v in dfs.items()},
                       f"{desde:%Y%m%d}_{hasta:%Y%m%d}", cfg.ruta_backup)

    generados = []
    for etiqueta, ini, fin in extract.partir_periodo(desde, hasta, periodo):
        destino = _informe_del_periodo(dfs, horarios, periodo, etiqueta,
                                       ini.date(), fin.date(), cfg, corte)
        if destino:
            generados.append(destino)
            log.info("Informe generado: %s", destino)

    if not generados:
        log.error("No se generó ningún informe.")
        return 1
    log.info("%d informe(s) en %s", len(generados), cfg.ruta_salida)

    # Consolida los cortes de las jornadas ya cerradas y aplica la retención.
    excel_purga.limpiar(cfg.ruta_salida, cfg.dias_excel, cfg.hora_cierre_jornada)

    # Un solo HTML con todos los periodos del almacén, no solo los de esta
    # corrida: el coordinador conserva un único enlace y ve el histórico.
    tablero_datos.purgar(cfg.ruta_tablero)
    tablero = tablero_html.generar(tablero_datos.cargar_todos(cfg.ruta_tablero),
                                   cfg.ruta_tablero,
                                   excel_vigentes=excel_purga.vigentes(cfg.ruta_salida))
    log.info("Tablero generado: %s", tablero)
    return 0


def _construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Automatización de reportes de Wolkvox.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Ejemplos:
  main.py                                        reporte operativo de hoy
  main.py --analisis --periodo mes               el mes pasado completo
  main.py --analisis --periodo semana            la semana pasada (lun-dom)
  main.py --analisis --periodo dia               ayer
  main.py --analisis --periodo dia --hoy         la jornada en curso (corridas intradía)
  main.py --analisis --periodo mes --desde 2026-07-01 --hasta 2026-08-31
""")
    parser.add_argument("--analisis", action="store_true",
                        help="genera el informe de gestión (puntualidad, tiempos, efectividad)")
    parser.add_argument("--periodo", choices=extract.PERIODOS, default="mes",
                        help="agrupación del informe de gestión: un Excel por periodo (por defecto: mes)")
    parser.add_argument("--desde",
                        help="fecha inicial (YYYY-MM-DD). Si se omite, se toma el último "
                             "periodo cerrado según --periodo")
    parser.add_argument("--hasta", help="fecha final (YYYY-MM-DD)")
    parser.add_argument("--hoy", action="store_true",
                        help="jornada en curso: procesa el día de hoy hasta la hora actual. "
                             "Es el modo de las corridas intradía")
    parser.add_argument("--desde-backup", action="store_true",
                        help="regenera el Excel desde el backup CSV, sin consumir API")
    parser.add_argument("--dias-atras", type=int, default=0,
                        help="reporte operativo: 0 = hoy, 1 = ayer, etc.")
    parser.add_argument("--sin-excel", action="store_true",
                        help="reporte operativo: extrae y respalda, sin generar el Excel")
    return parser


if __name__ == "__main__":
    setup_logger()
    args = _construir_parser().parse_args()

    if not args.analisis:
        sys.exit(ejecutar(args.dias_atras, args.sin_excel))

    # --hoy fija la ventana en la jornada en curso; sin fechas explícitas se
    # toma el último periodo YA CERRADO. Las dos formas dejan el cron de
    # Jenkins sin fechas cableadas.
    corte = None
    if args.hoy:
        if args.desde or args.hasta:
            _construir_parser().error("--hoy no se combina con --desde/--hasta")
        if args.periodo != "dia":
            _construir_parser().error("--hoy solo aplica con --periodo dia")
        corte = datetime.now()
        d = h = corte.date()
        log.info("Modo --hoy: jornada en curso hasta las %s", f"{corte:%H:%M}")
    elif args.desde:
        d = date.fromisoformat(args.desde)
        h = date.fromisoformat(args.hasta) if args.hasta else date.today()
    else:
        d, h = extract.ultimo_periodo_completo(args.periodo)
        log.info("Sin --desde: se toma el último %s cerrado (%s a %s)", args.periodo, d, h)

    if d > h:
        _construir_parser().error(f"--desde ({d}) es posterior a --hasta ({h})")

    try:
        # El candado es del programa, no del orquestador: protege igual si la
        # corrida la lanza Jenkins, el Programador de tareas o una persona.
        with bloqueo.tomar(cargar_config().ruta_tablero):
            codigo = analizar(d, h, args.periodo, args.desde_backup, corte)
        sys.exit(codigo)
    except bloqueo.EnCurso as e:
        # No es un fallo: la otra corrida va a dejar los datos igual. Salir en
        # verde evita marcar en rojo algo que se resolvió solo.
        log.warning("Se omite esta corrida (%s)", e)
        sys.exit(0)
    except Exception:
        # Traza completa al log y salida != 0 para que el orquestador marque
        # la corrida en rojo en vez de darla por buena.
        log.exception("La corrida terminó con un error no controlado")
        sys.exit(1)
