"""Un método por endpoint. Solo arma parámetros y parte la ventana cuando
hace falta; no conoce reglas de negocio."""
from __future__ import annotations

import calendar
import logging
from datetime import date, datetime, time, timedelta
from time import sleep

import httpx

from .cliente import WolkvoxClient

log = logging.getLogger(__name__)

FMT = "%Y%m%d%H%M%S"

# Guard deliberado: falla ruidosamente antes de truncar datos en silencio.
# Un reporte incompleto que parece completo es peor que una corrida fallida.
LIMITE_REGISTROS_API = 60_000
MARGEN_SEGURIDAD = 59_000


def es_sin_datos(error: Exception) -> bool:
    """¿Este fallo es en realidad "no hay nada en esa ventana"?

    Wolkvox responde 404 en vez de una lista vacía cuando no hay registros:
    un domingo, un festivo, o las 08:10 de un martes antes de que nadie haya
    tomado una pausa. Sin esta distinción, cada corrida temprana llena el log
    de ERROR por algo que no pasó, y un log que grita cuando no hay problema
    enseña a ignorarlo.

    **El mensaje del 404 es engañoso: dice "Validate dates entered".** Parece
    un problema con el rango y no lo es. Comprobado contra la API real:

        agent_3  12/08 00:00-12:00  (parcial, día con datos)  -> 200, 6 registros
        agent_3  12/08 completo                               -> 200, 15 registros
        agent_3  16/08 completo     (domingo, sin actividad)  -> 404
        chat_1   12/08 00:00-12:00                            -> 200, 519 registros

    O sea: las ventanas parciales se aceptan sin problema, y lo que dispara
    el 404 es que no haya nada que devolver. No hay que "arreglar" las fechas.

    Ojo: el 404 está sobrecargado. También lo devuelve un endpoint que no
    existe en este servidor (le pasa a chat_7). Por eso una fuente que nunca
    trae datos merece una mirada, aunque cada corrida se vea limpia.
    """
    return isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 404


def _rango(dt_ini: datetime, dt_fin: datetime) -> dict:
    return {"date_ini": dt_ini.strftime(FMT), "date_end": dt_fin.strftime(FMT)}


def _extraer_particionado(client: WolkvoxClient, recurso: str, api: str,
                           dt_ini: datetime, dt_fin: datetime, bloque_horas: int) -> list[dict]:
    registros: list[dict] = []
    cursor = dt_ini
    while cursor < dt_fin:
        corte = min(cursor + timedelta(hours=bloque_horas), dt_fin)
        params = {"api": api, **_rango(cursor, corte)}
        lote = client.consultar(recurso, params)

        if len(lote) >= MARGEN_SEGURIDAD:
            raise RuntimeError(
                f"{api} devolvió {len(lote)} registros entre {cursor} y {corte} "
                f"(límite de la API: {LIMITE_REGISTROS_API}). Reducir BLOQUE_HORAS_ANALISIS "
                f"en src/aplicacion/analisis.py."
            )

        registros.extend(lote)
        cursor = corte
    return registros


def agente_dia(client: WolkvoxClient, dt_ini: datetime, dt_fin: datetime) -> list[dict]:
    """reports_manager.php?api=agent_1 — resumen de tiempos por agente en la ventana."""
    return client.consultar("reports_manager.php", {"api": "agent_1", **_rango(dt_ini, dt_fin)})


def agente_hora(client: WolkvoxClient, dt_ini: datetime, dt_fin: datetime) -> list[dict]:
    """reports_manager.php?api=agent_8 — tiempos por agente, hora a hora.

    Es lo que permite saber a qué hora dejó de estar conectado alguien, cosa
    que agent_7 no distingue. Devuelve las 24 horas del día, con ceros en las
    que no hubo actividad.
    """
    return client.consultar("reports_manager.php", {"api": "agent_8", **_rango(dt_ini, dt_fin)})


def llamadas_detalle(client: WolkvoxClient, dt_ini: datetime, dt_fin: datetime,
                      bloque_horas: int = 6) -> list[dict]:
    """reports_manager.php?api=cdr_1 — detalle de llamadas conectadas/tipificadas."""
    return _extraer_particionado(client, "reports_manager.php", "cdr_1", dt_ini, dt_fin, bloque_horas)


# ---------------------------------------------------------------------------
# Modo análisis: el rango se parte en periodos, y cada tramo genera un informe.
#
# El partidor gobierna la EXTRACCIÓN, no solo el reporte: agent_1 y agent_3
# agregan todo el rango que se les consulte y no traen desglose interno por
# fecha, así que las cifras semanales solo salen consultando semana a semana.
# Los tres periodos caben de sobra en el límite de 31 días por consulta.
# ---------------------------------------------------------------------------

PERIODOS = ("mes", "semana", "dia")


def _tramo(etiqueta: str, ini: date, fin: date) -> tuple[str, datetime, datetime]:
    return (etiqueta,
            datetime.combine(ini, time.min),
            datetime.combine(fin, time.max).replace(microsecond=0))


def partir_periodo(desde: date, hasta: date,
                   periodo: str = "mes") -> list[tuple[str, datetime, datetime]]:
    """[(etiqueta, dt_ini, dt_fin), ...], un tramo por periodo.

    Las etiquetas ordenan alfabéticamente igual que cronológicamente, para
    que los archivos queden en orden en la carpeta de salida:
        mes    -> 2026-07
        semana -> 2026-S28   (semana ISO, lunes a domingo)
        dia    -> 2026-07-15
    Los tramos de los extremos se recortan al rango pedido.
    """
    if periodo not in PERIODOS:
        raise ValueError(f"Periodo '{periodo}' no válido. Opciones: {', '.join(PERIODOS)}")

    tramos, cursor = [], desde
    while cursor <= hasta:
        if periodo == "dia":
            etiqueta, ini_p, fin_p = f"{cursor:%Y-%m-%d}", cursor, cursor
        elif periodo == "semana":
            ini_p = cursor - timedelta(days=cursor.weekday())        # lunes
            fin_p = ini_p + timedelta(days=6)                        # domingo
            anio_iso, semana_iso, _ = cursor.isocalendar()
            etiqueta = f"{anio_iso}-S{semana_iso:02d}"
        else:
            ini_p = cursor.replace(day=1)
            fin_p = cursor.replace(day=calendar.monthrange(cursor.year, cursor.month)[1])
            etiqueta = f"{cursor:%Y-%m}"

        tramos.append(_tramo(etiqueta, max(ini_p, desde), min(fin_p, hasta)))
        cursor = fin_p + timedelta(days=1)
    return tramos


def ultimo_periodo_completo(periodo: str = "mes", hoy: date | None = None) -> tuple[date, date]:
    """Rango del último periodo YA CERRADO. Es lo que necesita un job
    programado: correr 'el mes pasado' sin fechas cableadas en el Jenkinsfile.

    Se excluye el periodo en curso a propósito: un mes a medias produciría
    cifras que luego cambian, y el día de hoy ni siquiera permite evaluar las
    salidas temprano.
    """
    hoy = hoy or date.today()
    if periodo == "dia":
        ayer = hoy - timedelta(days=1)
        return ayer, ayer
    if periodo == "semana":
        lunes_actual = hoy - timedelta(days=hoy.weekday())
        lunes_previo = lunes_actual - timedelta(days=7)
        return lunes_previo, lunes_previo + timedelta(days=6)
    if periodo == "mes":
        fin = hoy.replace(day=1) - timedelta(days=1)
        return fin.replace(day=1), fin
    raise ValueError(f"Periodo '{periodo}' no válido. Opciones: {', '.join(PERIODOS)}")


def codigos_tipificacion(client: WolkvoxClient) -> list[dict]:
    """information.php?api=activity_codes — inventario de códigos con las
    banderas hit/rpc/voice/chat que la propia operación ya configuró."""
    return client.consultar("information.php", {"api": "activity_codes"})


def logueo_por_dia(client: WolkvoxClient, dt_ini: datetime, dt_fin: datetime) -> list[dict]:
    """agent_7 — login/logout por agente y día. Base del informe de puntualidad."""
    return client.consultar("reports_manager.php", {"api": "agent_7", **_rango(dt_ini, dt_fin)})


def tiempo_auxiliar(client: WolkvoxClient, dt_ini: datetime, dt_fin: datetime) -> list[dict]:
    """agent_3 — tiempo auxiliar desglosado por tipo de estado."""
    return client.consultar("reports_manager.php", {"api": "agent_3", **_rango(dt_ini, dt_fin)})


def tiempo_auxiliar_por_dia(client: WolkvoxClient, dt_ini: datetime, dt_fin: datetime,
                             pausa_seg: float = 1.5) -> list[dict]:
    """agent_3 día por día, agregando la fecha a cada registro.

    El endpoint agrega todo el rango consultado sin desglose diario, así que
    la única forma de tener el día a día es consultar un día a la vez. Son
    ~25 consumos por mes: irrelevante frente a la cuota, y sobre un endpoint
    ya probado.

    Un día sin actividad (domingo, festivo, descanso) responde 404 en vez de
    lista vacía: se trata como "sin datos" y se sigue. Cualquier otro fallo
    se registra y tampoco corta la extracción, para no perder el mes entero
    por un día malo; el log dice cuántos días quedaron fuera.
    """
    registros, sin_datos, fallidos = [], 0, []
    dia = dt_ini.date()
    while dia <= dt_fin.date():
        ini = datetime.combine(dia, time.min)
        fin = datetime.combine(dia, time.max).replace(microsecond=0)
        try:
            lote = client.consultar("reports_manager.php", {"api": "agent_3", **_rango(ini, fin)})
            registros.extend({**fila, "fecha": dia.isoformat()} for fila in lote)
        except httpx.HTTPStatusError as e:
            if es_sin_datos(e):
                sin_datos += 1
            else:
                fallidos.append(dia.isoformat())
        except Exception:
            fallidos.append(dia.isoformat())
        dia += timedelta(days=1)
        sleep(pausa_seg)               # respiro entre consumos del mismo token

    log.info("agent_3 día a día: %d registros, %d días sin actividad", len(registros), sin_datos)
    if fallidos:
        log.warning("agent_3 día a día: %d días no se pudieron consultar: %s",
                    len(fallidos), ", ".join(fallidos))
    return registros


def chats_productividad(client: WolkvoxClient, dt_ini: datetime, dt_fin: datetime) -> list[dict]:
    """chat_16 — total de chats, tiempo y % de transferencia por agente."""
    return client.consultar("reports_manager.php", {"api": "chat_16", **_rango(dt_ini, dt_fin)})


def chats_detalle(client: WolkvoxClient, dt_ini: datetime, dt_fin: datetime) -> list[dict]:
    """chat_1 — una fila por conversación, con canal, agente y tipificación.
    Es el equivalente digital de cdr_1 y la base de la efectividad en
    WhatsApp/chat.

    Se usa chat_1 y no chat_7 (interacciones por agente y código) porque
    chat_7 devuelve 404 en este servidor; además chat_1 aporta el canal y el
    detalle por conversación, que chat_7 no tiene.
    """
    return client.consultar("reports_manager.php", {"api": "chat_1", **_rango(dt_ini, dt_fin)})
