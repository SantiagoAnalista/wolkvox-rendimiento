"""Punto de entrada: traduce la línea de comandos a un caso de uso.

Aquí no hay reglas de negocio ni orquestación. Las cifras las calcula
`src/dominio`, el flujo lo arma `src/aplicacion` y todo lo que toca red,
disco o Excel vive en `src/adaptadores`.

Reporte operativo del día:
    python main.py                  # procesa el día de hoy hasta la hora actual
    python main.py --dias-atras 1   # reprocesa el día de ayer completo
    python main.py --sin-excel      # solo extrae y respalda, no genera Excel

Informe de gestión (puntualidad, tiempos, efectividad):
    python main.py --analisis --periodo semana
    python main.py --analisis --periodo dia --hoy
    python main.py --analisis --periodo mes --desde 2026-07-01 --hasta 2026-08-12
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime

from config.logger_config import setup_logger
from config.settings import cargar_config
from src.adaptadores.almacen import candado
from src.adaptadores.wolkvox import extraccion
from src.aplicacion.analisis import analizar
from src.aplicacion.operativo import ejecutar

log = logging.getLogger("main")


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
    parser.add_argument("--periodo", choices=extraccion.PERIODOS, default="mes",
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


def _ventana(args, parser) -> tuple[date, date, datetime | None]:
    """Rango a procesar y, si la jornada está en curso, la hora de corte.

    --hoy fija la ventana en el día de hoy; sin fechas explícitas se toma el
    último periodo YA CERRADO. Las dos formas dejan el cron sin fechas
    cableadas.
    """
    if args.hoy:
        if args.desde or args.hasta:
            parser.error("--hoy no se combina con --desde/--hasta")
        if args.periodo != "dia":
            parser.error("--hoy solo aplica con --periodo dia")
        corte = datetime.now()
        log.info("Modo --hoy: jornada en curso hasta las %s", f"{corte:%H:%M}")
        return corte.date(), corte.date(), corte

    if args.desde:
        desde = date.fromisoformat(args.desde)
        hasta = date.fromisoformat(args.hasta) if args.hasta else date.today()
        return desde, hasta, None

    desde, hasta = extraccion.ultimo_periodo_completo(args.periodo)
    log.info("Sin --desde: se toma el último %s cerrado (%s a %s)", args.periodo, desde, hasta)
    return desde, hasta, None


def main() -> int:
    parser = _construir_parser()
    args = parser.parse_args()

    if not args.analisis:
        return ejecutar(args.dias_atras, args.sin_excel)

    desde, hasta, corte = _ventana(args, parser)
    if desde > hasta:
        parser.error(f"--desde ({desde}) es posterior a --hasta ({hasta})")

    # El candado es del programa, no del orquestador: protege igual si la
    # corrida la lanza Jenkins, el Programador de tareas o una persona.
    with candado.tomar(cargar_config().ruta_tablero):
        return analizar(desde, hasta, args.periodo, args.desde_backup, corte)


if __name__ == "__main__":
    setup_logger()
    try:
        sys.exit(main())
    except candado.EnCurso as e:
        # No es un fallo: la otra corrida va a dejar los datos igual. Salir en
        # verde evita marcar en rojo algo que se resolvió solo.
        log.warning("Se omite esta corrida (%s)", e)
        sys.exit(0)
    except Exception:
        # Traza completa al log y salida != 0 para que el orquestador marque
        # la corrida en rojo en vez de darla por buena.
        log.exception("La corrida terminó con un error no controlado")
        sys.exit(1)
