# Automatización de Reportes de Rendimiento — Wolkvox

**Proyecto:** Extracción, transformación y presentación de indicadores de operación de call center
**Fase:** 1 — MVP
**Estado del documento:** Borrador para validación técnica

---

## 1. Contexto

La operación de call center funciona sobre **Wolkvox**, plataforma de contact center en la nube (SaaS). Hoy la consulta de rendimiento se hace de forma manual desde el Manager, lo que implica:

- Dependencia de una persona para generar cada informe.
- Latencia entre "necesito el dato" y "tengo el dato".
- Riesgo de inconsistencia entre informes generados por distintas personas.
- Imposibilidad de construir series históricas propias para análisis de tendencia.

Wolkvox expone una **API v2 REST documentada**, con autenticación por token, respuestas en JSON y endpoints específicos para reportería de agentes, llamadas, skills y campañas. Esto habilita automatizar el ciclo completo sin scraping ni intervención manual.

### Alternativas evaluadas

| Opción | Veredicto |
|---|---|
| **API v2 oficial** | ✅ **Seleccionada.** Documentada, estable, soportada, sin costo adicional aparente sobre la licencia |
| Scraping del Manager web | ❌ Descartada. Frágil ante releases, posible violación de ToS, sin ventaja funcional |
| Acceso directo a base de datos | ❌ No aplica. SaaS multi-tenant, el proveedor no expone la BD |
| Exportación manual programada | ⚠️ Plan B. Requiere intervención humana, no versionable, no parametrizable |

### Supuesto pendiente de confirmar

El consumo de la API **no tiene un precio publicado por separado**. La cuota diaria está atada al número de licencias (licencias × 1000 tokens/día), lo que sugiere que está incluida en el contrato vigente.

> ⚠️ **Acción requerida antes de desarrollar:** confirmar con el ejecutivo de cuenta / partner de Wolkvox que el consumo de API está incluido en el contrato actual y cuál es la cuota diaria efectiva asignada.

---

## 2. Objetivo

Construir una automatización en Python que, **cada hora**, extraiga información de rendimiento desde la API de Wolkvox, la transforme en indicadores de negocio y entregue un **archivo Excel resumen con gráficos** para consumo de la jefatura de operación y supervisores.

---

## 3. Alcance

### 3.1 Dentro del alcance (Fase 1)

**Extracción**
- Estados y tiempos de los agentes (ready, ACW, ring, AHT, ocupación, auxiliares, login/logout).
- Detalle de llamadas con su resultado y tipificación.
- Llamadas no conectadas clasificadas por resultado.

**Transformación**
- Cálculo de indicadores por agente y consolidado general.
- Clasificación de llamadas en categorías de negocio (efectiva, colgada por cliente, colgada por agente, no acepta, no contactada).
- Agregación por hora del día en curso.

**Presentación**
- Un archivo `.xlsx` por corrida con hojas de resumen, desglose por agente, desglose horario y gráficos nativos de Excel.
- Ubicación en una carpeta compartida de la operación.

**Operación**
- Ejecución programada cada hora en jornada operativa.
- Idempotencia: reprocesar una ventana no duplica ni corrompe datos.
- Log de ejecuciones con trazabilidad de éxito/fallo por endpoint.

### 3.2 Fuera del alcance (Fase 1)

- Dashboard en tiempo real (latencia < 1 minuto).
- Integración con Power BI u otra herramienta de BI.
- Métricas de canales digitales (chat, WhatsApp, correo).
- Speech analytics, calidad (QA) y gamification.
- Alertas automáticas por umbral.
- Interfaz web de consulta.

Todo lo anterior queda contemplado en el roadmap de escalamiento (sección 11) y **la arquitectura se diseña para no bloquear ninguno de esos caminos**.

### 3.3 Criterios de aceptación

1. El Excel se genera automáticamente cada hora sin intervención manual.
2. Los totales de llamadas del reporte cuadran con los del Manager de Wolkvox para la misma ventana (tolerancia 0).
3. Una corrida fallida no deja el almacenamiento en estado inconsistente.
4. Reejecutar manualmente una hora ya procesada produce el mismo resultado.
5. El histórico queda persistido y consultable, no solo el último Excel.

---

## 4. Decisiones de arquitectura

### 4.1 Almacenamiento: SQLite

**Decisión:** SQLite como almacén persistente (un archivo `.db` en el servidor).

**Justificación:**

| Criterio | Por qué SQLite gana aquí |
|---|---|
| Idempotencia | `PRIMARY KEY` + `INSERT ... ON CONFLICT DO UPDATE` resuelve el reprocesamiento en una línea. Con archivos planos hay que reimplementarlo a mano |
| Cero instalación | Viene en la stdlib de Python. Sin servicio, sin puertos, sin credenciales adicionales en Windows |
| Consultas | SQL directo para las agregaciones, en vez de cargar todo a memoria en cada corrida |
| Concurrencia | Suficiente para un escritor y varios lectores, que es exactamente este caso |
| Portabilidad | Un solo archivo: se respalda copiándolo, se inspecciona con DB Browser for SQLite |

**Por qué no parquet en esta fase:** parquet es excelente para lectura analítica columnar de grandes volúmenes, pero es *inmutable*. Cada corrección obliga a reescribir la partición completa, y la deduplicación queda a cargo del código de aplicación. En un flujo horario con reprocesamiento, eso es fricción sin beneficio. Con los volúmenes de una operación típica, SQLite responde de sobra.

**Por qué no Excel como almacén:** el Excel es la *salida*, no la fuente de verdad. Si el histórico vive en Excel, cualquier persona puede romperlo con una edición manual.

**Ruta de migración:** cuando el volumen o la concurrencia lo exijan → PostgreSQL (mismo SQL, cambia el driver) o export a parquet para la capa analítica. La capa de acceso a datos se aísla desde el día uno para que ese cambio no toque la lógica de negocio.

### 4.2 Estrategia de extracción: ventana deslizante con reproceso

En lugar de un cursor incremental frágil, cada corrida **reextrae el día completo en curso y hace upsert**. Ventajas:

- Auto-corrige registros que Wolkvox ajusta retroactivamente (tipificaciones tardías, cierres de ACW).
- Si una corrida falla, la siguiente recupera lo perdido sin lógica de reintento especial.
- Elimina la clase entera de bugs de "se perdió una ventana".

**Restricción a vigilar:** el límite es de 60.000 registros por consumo. Si el CDR diario de la operación se acerca a ese número, la extracción se parte en bloques de 6 horas y se concatena. La sección 7.3 incluye la función que lo maneja de forma transparente.

Adicionalmente, una **corrida de reconciliación nocturna** reprocesa los últimos 3 días completos.

### 4.3 Presentación: Excel con gráficos nativos

Se usa `openpyxl` generando **gráficos nativos de Excel** (no imágenes incrustadas), de modo que sean interactivos y se puedan reutilizar. Los valores se escriben calculados desde Python, no como fórmulas: el archivo es un *reporte*, no un modelo que el usuario deba recalcular.

---

## 5. Fuentes de datos (endpoints)

**Base URL:** `https://wv{SERVIDOR}.wolkvox.com/api/v2/`
**Autenticación:** header `wolkvox-token: {TOKEN}`
**Método:** GET
**Formato de fecha:** `YYYYmmddHHiiss` (ej. `20260810080000`)

### 5.1 Endpoints de Fase 1

| # | Endpoint | Recurso | Uso en el reporte |
|---|---|---|---|
| 1 | `reports_manager.php?api=agent_1` | Tiempo por estado, por agente | Base de tiempos: llamadas inbound/outbound/internal, ring, ACW, AHT, login/logout, hits, RPC |
| 2 | `reports_manager.php?api=agent_8` | Estados de agente hora a hora | Curva horaria de ocupación y disponibilidad |
| 3 | `reports_manager.php?api=cdr_1` | Detalle de llamadas | Resultado por llamada, código de tipificación, quién termina la interacción, duración |
| 4 | `reports_manager.php?api=cdr_6` | No conectadas por resultado | Clasificación de intentos fallidos |
| 5 | `information.php?api=agents` | Catálogo de agentes | Nombres, skills, última conexión (dimensión de referencia) |

### 5.2 Endpoints de referencia para fases posteriores

| Endpoint | Aporta |
|---|---|
| `reports_manager.php?api=agent_3` | Desglose de tiempo auxiliar (pausas por tipo) |
| `reports_manager.php?api=agent_11` | Tiempos agrupados por skill |
| `reports_manager.php?api=act_1` / `act_2` | Códigos de tipificación por agente y por código |
| `reports_manager.php?api=act_4` | Llamadas **sin** tipificar por agente (disciplina del asesor) |
| `reports_manager.php?api=skill_1` / `skill_2` | Nivel de servicio y detalle de abandonadas |
| `real_time.php?api=calls_last_10` | Llamadas de los últimos 10 minutos (carril de tiempo real) |
| `real_time.php?api=campaigns` | Contactabilidad y penetración de campañas en vivo |

### 5.3 Límites de la API (condicionan el diseño)

| Límite | Valor |
|---|---|
| Registros por consumo | 60.000 |
| Peso del resultado | 256 MB |
| Rango de fechas por consulta | 31 días |
| Timeout | 60 segundos |
| Solicitudes simultáneas por token | 2 |
| Cuota diaria | Licencias × 1000 tokens |
| Frecuencia recomendada por el proveedor | 1 consumo cada 5 minutos |

**Implicación:** las llamadas se **serializan**, no se paralelizan sobre un mismo token. Con 5 endpoints × 12 corridas diarias = 60 llamadas/día, se está muy por debajo de cualquier cuota razonable.

---

## 6. Modelo de datos

### 6.1 Tablas

```sql
-- Dimensión: catálogo de agentes
CREATE TABLE IF NOT EXISTS dim_agente (
    agent_id        TEXT PRIMARY KEY,
    nombre          TEXT,
    skill           TEXT,
    ultima_conexion TEXT,
    actualizado_en  TEXT NOT NULL
);

-- Hecho: tiempos y estados por agente y día
CREATE TABLE IF NOT EXISTS fact_agente_dia (
    fecha           TEXT NOT NULL,
    agent_id        TEXT NOT NULL,
    llamadas_in     INTEGER,
    llamadas_out    INTEGER,
    llamadas_int    INTEGER,
    tiempo_login    INTEGER,   -- segundos
    tiempo_ready    INTEGER,
    tiempo_acw      INTEGER,
    tiempo_ring     INTEGER,
    tiempo_aux      INTEGER,
    aht             INTEGER,
    ocupacion       REAL,
    hits            INTEGER,
    rpc             INTEGER,
    extraido_en     TEXT NOT NULL,
    PRIMARY KEY (fecha, agent_id)
);

-- Hecho: tiempos por agente y hora
CREATE TABLE IF NOT EXISTS fact_agente_hora (
    fecha           TEXT NOT NULL,
    hora            INTEGER NOT NULL,
    agent_id        TEXT NOT NULL,
    llamadas        INTEGER,
    tiempo_login    INTEGER,
    tiempo_ready    INTEGER,
    tiempo_acw      INTEGER,
    tiempo_aux      INTEGER,
    aht             INTEGER,
    ocupacion       REAL,
    extraido_en     TEXT NOT NULL,
    PRIMARY KEY (fecha, hora, agent_id)
);

-- Hecho: detalle de llamadas
CREATE TABLE IF NOT EXISTS fact_llamada (
    call_id             TEXT PRIMARY KEY,
    fecha               TEXT NOT NULL,
    hora                INTEGER NOT NULL,
    timestamp_inicio    TEXT,
    agent_id            TEXT,
    campaign_id         TEXT,
    skill               TEXT,
    tipo_llamada        TEXT,      -- inbound / outbound / internal
    telefono            TEXT,
    duracion            INTEGER,   -- segundos
    cod_tipificacion    TEXT,
    desc_tipificacion   TEXT,
    quien_termina       TEXT,      -- agente / cliente / sistema
    resultado_tecnico   TEXT,      -- crudo, tal como lo entrega la API
    categoria_negocio   TEXT,      -- derivado (ver 6.2)
    extraido_en         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_llamada_fecha ON fact_llamada(fecha, hora);
CREATE INDEX IF NOT EXISTS ix_llamada_agente ON fact_llamada(agent_id, fecha);

-- Control: bitácora de ejecuciones
CREATE TABLE IF NOT EXISTS log_ejecucion (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint        TEXT NOT NULL,
    date_ini        TEXT NOT NULL,
    date_end        TEXT NOT NULL,
    estado          TEXT NOT NULL,   -- OK / ERROR
    registros       INTEGER,
    mensaje         TEXT,
    duracion_seg    REAL,
    ejecutado_en    TEXT NOT NULL
);
```

### 6.2 Regla de negocio: categorización de llamadas

Wolkvox **no entrega un campo único** con "efectiva / colgada / no acepta". La categoría de negocio se deriva cruzando dos dimensiones independientes:

- **Resultado técnico** (qué pasó en la red): lo reporta `cdr_1` / `cdr_6`.
- **Tipificación del agente** (qué dice el asesor que pasó): código de actividad.

> 🔑 **Decisión de diseño:** se conservan **ambos campos crudos** en la tabla, y la categoría de negocio se calcula como una columna derivada. No se colapsan en un solo campo "estado".
>
> **Razón:** cuando el resultado técnico y la tipificación no coinciden (llamada conectada de 4 segundos tipificada como "venta"), esa discrepancia es información valiosa para la operación. Si se colapsan los campos, esa señal se pierde para siempre y no se puede reconstruir.

La tabla de mapeo `resultado_técnico → categoría_negocio` se define en un archivo de configuración externo (`config/categorias.yaml`), **no hardcodeada en el código**, porque va a cambiar conforme la operación afine sus definiciones.

> ⚠️ **Tarea de Fase 0:** los valores reales que devuelve el campo de resultado deben inventariarse contra la respuesta real de la API antes de escribir el mapeo. No asumirlos.

---

## 7. Paso a paso de implementación

### Fase 0 — Descubrimiento (antes de escribir código de producción)

**Objetivo:** eliminar toda suposición sobre la estructura real de las respuestas.

1. **Obtener credenciales**
   - Solicitar acceso a wolkvox Manager con permisos de configuración.
   - Generar el token: Manager → ⚙️ Configuración → pestaña *Integraciones* → sub-pestaña *Tokens* → escribir descripción (ej. `automatizacion-rendimiento-prod`) → botón *Add Token*.
   - Generar un **segundo token** para desarrollo/pruebas, separado del de producción.

2. **Identificar el servidor**
   - Ingresar a `https://manager.wolkvox.com/`, escribir el nombre de la operación y hacer login.
   - La URL redirige a `https://wvNNNN.wolkvox.com/`. Los dígitos después de `wv` son el número de servidor.

3. **Explorar en Postman**
   - Importar el workspace oficial de Wolkvox.
   - Ejecutar cada uno de los 5 endpoints de Fase 1 con un rango de **un solo día de baja carga**.
   - Guardar cada respuesta JSON en `docs/samples/` para usarlas como fixtures de prueba.

4. **Documentar el contrato real** — crear `docs/contrato-api.md` con:
   - Nombre exacto de cada campo (Wolkvox no siempre coincide con la documentación web).
   - Tipo de dato real (⚠️ los tiempos suelen venir como *string* `"HH:MM:SS"`, no como entero de segundos).
   - Valores distintos que toma el campo de resultado y el de "quién termina la interacción".
   - Comportamiento con rangos vacíos (¿`data: []` o error?).
   - Volumen de registros de un día típico → determina si hace falta particionar la extracción.

5. **Validar la línea base**
   - Generar el mismo reporte manualmente desde el Manager para ese día.
   - Comparar totales contra la respuesta de la API. **Si no cuadran, entender por qué antes de continuar.** Es mucho más barato resolverlo ahora que después de construir el pipeline.

**Entregable de la fase:** `docs/contrato-api.md` + fixtures JSON + confirmación comercial del costo.

---

### Fase 1 — Estructura del proyecto

```
wolkvox-rendimiento/
├── config/
│   ├── settings.example.yaml    # plantilla versionada
│   ├── settings.yaml            # real, en .gitignore
│   └── categorias.yaml          # mapeo resultado → categoría de negocio
├── src/
│   ├── __init__.py
│   ├── config.py                # carga y valida configuración
│   ├── client.py                # cliente HTTP de Wolkvox
│   ├── extract.py               # un método por endpoint
│   ├── transform.py             # normalización y reglas de negocio
│   ├── storage.py               # capa de acceso a SQLite
│   ├── report.py                # generación del Excel
│   └── main.py                  # orquestador
├── tests/
│   ├── fixtures/                # JSON reales de Fase 0
│   ├── test_transform.py
│   └── test_storage.py
├── docs/
│   ├── contrato-api.md
│   └── samples/
├── output/                      # Excel generados
├── data/
│   └── wolkvox.db               # SQLite (en .gitignore)
├── logs/
├── requirements.txt
├── .gitignore
└── README.md
```

**Dependencias (`requirements.txt`):**

```
httpx==0.27.*
pandas==2.2.*
openpyxl==3.1.*
PyYAML==6.0.*
tenacity==8.5.*
python-dateutil==2.9.*
```

> `httpx` sobre `requests` por el manejo nativo de timeouts granulares. No se usa el modo async: el token obliga a serializar, así que la complejidad no compra nada.

---

### Fase 2 — Configuración

**`config/settings.example.yaml`**

```yaml
wolkvox:
  servidor: "0000"          # los 4 dígitos de tu servidor
  token: "REEMPLAZAR"       # nunca commitear el real
  timeout_seg: 90
  reintentos: 3

extraccion:
  dias_reproceso: 3         # ventana de reconciliación nocturna
  bloque_horas: 6           # partición si se supera el límite de registros
  zona_horaria: "America/Bogota"

almacenamiento:
  ruta_db: "data/wolkvox.db"

reporte:
  ruta_salida: "output"
  ruta_compartida: ""       # UNC de la carpeta de la operación, opcional
  jornada_inicio: 6         # hora de inicio de operación
  jornada_fin: 21
```

**Regla de seguridad:** el token **nunca** va en el repositorio. En producción se inyecta como variable de entorno (`WOLKVOX_TOKEN`) desde Jenkins Credentials o desde las variables de entorno del servicio de Windows. `settings.yaml` está en `.gitignore`.

```python
# src/config.py
import os
import yaml
from pathlib import Path

def cargar_config(ruta: str = "config/settings.yaml") -> dict:
    with open(ruta, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    # La variable de entorno siempre gana sobre el archivo
    token_env = os.getenv("WOLKVOX_TOKEN")
    if token_env:
        cfg["wolkvox"]["token"] = token_env

    if not cfg["wolkvox"]["token"] or cfg["wolkvox"]["token"] == "REEMPLAZAR":
        raise ValueError("Token de Wolkvox no configurado")

    Path(cfg["almacenamiento"]["ruta_db"]).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg["reporte"]["ruta_salida"]).mkdir(parents=True, exist_ok=True)
    return cfg
```

---

### Fase 3 — Cliente HTTP

Responsabilidad única: hablar con la API, reintentar y devolver el `data` crudo. **No transforma nada.**

```python
# src/client.py
import logging
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

log = logging.getLogger(__name__)

class WolkvoxError(Exception):
    pass

class WolkvoxClient:
    def __init__(self, servidor: str, token: str, timeout: int = 90, reintentos: int = 3):
        self.base_url = f"https://wv{servidor}.wolkvox.com/api/v2"
        self._client = httpx.Client(
            headers={"wolkvox-token": token},
            timeout=httpx.Timeout(timeout, connect=15.0),
        )
        self._reintentos = reintentos

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._client.close()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        reraise=True,
    )
    def _get(self, recurso: str, params: dict) -> dict:
        url = f"{self.base_url}/{recurso}"
        log.debug("GET %s params=%s", url, params)
        resp = self._client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    def consultar(self, recurso: str, params: dict) -> list[dict]:
        """Devuelve la lista de registros del campo 'data'."""
        payload = self._get(recurso, params)

        code = str(payload.get("code", ""))
        if code and code not in ("200", "0"):
            raise WolkvoxError(f"{recurso} {params}: code={code} msg={payload.get('msg')}")

        data = payload.get("data") or []
        if isinstance(data, dict):      # algunos endpoints devuelven objeto único
            data = [data]

        log.info("%s -> %d registros", params.get("api", recurso), len(data))
        return data
```

> ⚠️ La validación del campo `code` debe ajustarse a lo que se observe en Fase 0. Distintos endpoints de Wolkvox no siempre son consistentes en el código de éxito.

---

### Fase 4 — Extracción

```python
# src/extract.py
from datetime import datetime, timedelta

FMT = "%Y%m%d%H%M%S"

def _rango(dt_ini: datetime, dt_fin: datetime) -> dict:
    return {"date_ini": dt_ini.strftime(FMT), "date_end": dt_fin.strftime(FMT)}

def _extraer_particionado(client, recurso, api, dt_ini, dt_fin, bloque_horas=6):
    """
    Divide la consulta en bloques para no chocar contra el límite de 60.000
    registros por consumo. Devuelve la lista concatenada.
    """
    registros, cursor = [], dt_ini
    while cursor < dt_fin:
        corte = min(cursor + timedelta(hours=bloque_horas), dt_fin)
        params = {"api": api, **_rango(cursor, corte)}
        lote = client.consultar(recurso, params)

        if len(lote) >= 59_000:
            raise RuntimeError(
                f"{api} devolvió {len(lote)} registros entre {cursor} y {corte}. "
                "Reducir 'bloque_horas' en la configuración."
            )

        registros.extend(lote)
        cursor = corte
    return registros


def agentes_catalogo(client):
    return client.consultar("information.php", {"api": "agents"})

def agente_dia(client, dt_ini, dt_fin):
    return client.consultar("reports_manager.php", {"api": "agent_1", **_rango(dt_ini, dt_fin)})

def agente_hora(client, dt_ini, dt_fin):
    return client.consultar("reports_manager.php", {"api": "agent_8", **_rango(dt_ini, dt_fin)})

def llamadas_detalle(client, dt_ini, dt_fin, bloque_horas=6):
    return _extraer_particionado(client, "reports_manager.php", "cdr_1",
                                 dt_ini, dt_fin, bloque_horas)

def llamadas_no_conectadas(client, dt_ini, dt_fin, bloque_horas=6):
    return _extraer_particionado(client, "reports_manager.php", "cdr_6",
                                 dt_ini, dt_fin, bloque_horas)
```

**El guard de 59.000 registros es deliberado:** falla ruidosamente antes de truncar datos en silencio. Un reporte incompleto que parece completo es peor que una corrida fallida.

---

### Fase 5 — Transformación

```python
# src/transform.py
import pandas as pd

def hhmmss_a_segundos(valor) -> int:
    """Wolkvox suele entregar tiempos como 'HH:MM:SS'. Confirmar en Fase 0."""
    if pd.isna(valor) or valor in ("", None):
        return 0
    if isinstance(valor, (int, float)):
        return int(valor)
    partes = str(valor).split(":")
    try:
        if len(partes) == 3:
            h, m, s = (int(float(p)) for p in partes)
            return h * 3600 + m * 60 + s
        if len(partes) == 2:
            m, s = (int(float(p)) for p in partes)
            return m * 60 + s
        return int(float(valor))
    except (ValueError, TypeError):
        return 0


def categorizar(df: pd.DataFrame, mapeo: dict) -> pd.DataFrame:
    """
    Deriva la categoría de negocio. Preserva SIEMPRE los campos crudos:
    'resultado_tecnico' y 'cod_tipificacion' no se modifican.
    """
    df = df.copy()
    df["categoria_negocio"] = (
        df["resultado_tecnico"].astype(str).str.strip().str.lower()
          .map(mapeo).fillna("sin_clasificar")
    )
    return df


def indicadores_por_agente(df_agente: pd.DataFrame) -> pd.DataFrame:
    df = df_agente.copy()
    for col in ["tiempo_login", "tiempo_ready", "tiempo_acw", "tiempo_aux", "aht"]:
        df[col] = df[col].apply(hhmmss_a_segundos)

    df["total_llamadas"] = df[["llamadas_in", "llamadas_out", "llamadas_int"]].sum(axis=1)

    # Ocupación = tiempo productivo / tiempo logueado. Guardar contra división por cero.
    df["ocupacion"] = (
        (df["tiempo_login"] - df["tiempo_ready"] - df["tiempo_aux"])
        .div(df["tiempo_login"].replace(0, pd.NA))
        .fillna(0).clip(0, 1)
    )
    return df
```

> 🔎 **Nota sobre la fórmula de ocupación:** existen varias definiciones válidas (algunas operaciones excluyen ACW, otras lo incluyen). **La fórmula debe validarse con la jefatura de la operación antes de publicar el primer reporte.** Si el número no coincide con lo que ellos ya usan, el reporte pierde credibilidad completa aunque el pipeline sea impecable.

---

### Fase 6 — Persistencia

```python
# src/storage.py
import sqlite3
from contextlib import contextmanager
from pathlib import Path

ESQUEMA = Path("src/schema.sql").read_text(encoding="utf-8")

@contextmanager
def conexion(ruta_db: str):
    conn = sqlite3.connect(ruta_db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")      # lecturas concurrentes sin bloqueo
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def inicializar(ruta_db: str):
    with conexion(ruta_db) as conn:
        conn.executescript(ESQUEMA)


def upsert(conn, tabla: str, registros: list[dict], claves: list[str]):
    """Inserta o actualiza. Es lo que hace el reproceso seguro."""
    if not registros:
        return 0
    cols = list(registros[0].keys())
    placeholders = ",".join("?" * len(cols))
    actualizables = [c for c in cols if c not in claves]
    set_clause = ",".join(f"{c}=excluded.{c}" for c in actualizables)

    sql = (
        f"INSERT INTO {tabla} ({','.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT({','.join(claves)}) DO UPDATE SET {set_clause}"
    )
    conn.executemany(sql, [tuple(r[c] for c in cols) for r in registros])
    return len(registros)
```

`ON CONFLICT DO UPDATE` es lo que hace que reejecutar una hora ya procesada sea seguro. Es la pieza que convierte "automatización frágil" en "automatización que se auto-repara".

---

### Fase 7 — Generación del Excel

**Estructura del libro:**

| Hoja | Contenido |
|---|---|
| `Resumen` | KPIs generales del día + gráfico de torta de categorías |
| `Por agente` | Tabla por asesor: llamadas, AHT, ocupación, ACW, tipificaciones |
| `Por hora` | Curva horaria de llamadas y ocupación + gráfico de líneas |
| `Resultados` | Distribución de resultados de llamada + gráfico de barras |
| `Metadatos` | Fecha/hora de corrida, ventana extraída, registros por endpoint, versión |

```python
# src/report.py
from datetime import datetime
from pathlib import Path
from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, PieChart, Reference
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils.dataframe import dataframe_to_rows

FUENTE = "Arial"
ENCABEZADO_FILL = PatternFill("solid", fgColor="1F3864")
ENCABEZADO_FONT = Font(name=FUENTE, bold=True, color="FFFFFF", size=11)

def _escribir_df(ws, df, fila_inicio=1):
    for r_idx, fila in enumerate(dataframe_to_rows(df, index=False, header=True),
                                 start=fila_inicio):
        for c_idx, valor in enumerate(fila, start=1):
            celda = ws.cell(row=r_idx, column=c_idx, value=valor)
            celda.font = Font(name=FUENTE, size=10)
            if r_idx == fila_inicio:
                celda.font = ENCABEZADO_FONT
                celda.fill = ENCABEZADO_FILL
                celda.alignment = Alignment(horizontal="center", vertical="center")

    for col in ws.columns:
        ancho = max((len(str(c.value)) for c in col if c.value is not None), default=8)
        ws.column_dimensions[col[0].column_letter].width = min(ancho + 3, 40)
    ws.freeze_panes = ws.cell(row=fila_inicio + 1, column=1)


def generar(dfs: dict, metadatos: dict, ruta_salida: str) -> Path:
    wb = Workbook()

    # --- Resumen ---
    ws = wb.active
    ws.title = "Resumen"
    _escribir_df(ws, dfs["resumen"], fila_inicio=1)

    torta = PieChart()
    torta.title = "Distribución por categoría"
    n = len(dfs["categorias"])
    _escribir_df(wb.create_sheet("Resultados"), dfs["categorias"])
    hoja_cat = wb["Resultados"]
    torta.add_data(Reference(hoja_cat, min_col=2, min_row=1, max_row=n + 1), titles_from_data=True)
    torta.set_categories(Reference(hoja_cat, min_col=1, min_row=2, max_row=n + 1))
    ws.add_chart(torta, "H2")

    # --- Por agente ---
    ws_ag = wb.create_sheet("Por agente")
    _escribir_df(ws_ag, dfs["por_agente"])
    barras = BarChart()
    barras.title = "Llamadas por agente"
    barras.y_axis.title = "Llamadas"
    m = len(dfs["por_agente"])
    barras.add_data(Reference(ws_ag, min_col=3, min_row=1, max_row=m + 1), titles_from_data=True)
    barras.set_categories(Reference(ws_ag, min_col=1, min_row=2, max_row=m + 1))
    barras.height, barras.width = 10, 22
    ws_ag.add_chart(barras, f"A{m + 4}")

    # --- Por hora ---
    ws_h = wb.create_sheet("Por hora")
    _escribir_df(ws_h, dfs["por_hora"])
    linea = LineChart()
    linea.title = "Curva horaria"
    k = len(dfs["por_hora"])
    linea.add_data(Reference(ws_h, min_col=2, max_col=3, min_row=1, max_row=k + 1),
                   titles_from_data=True)
    linea.set_categories(Reference(ws_h, min_col=1, min_row=2, max_row=k + 1))
    linea.height, linea.width = 10, 22
    ws_h.add_chart(linea, f"A{k + 4}")

    # --- Metadatos ---
    ws_m = wb.create_sheet("Metadatos")
    for i, (clave, valor) in enumerate(metadatos.items(), start=1):
        ws_m.cell(row=i, column=1, value=clave).font = Font(name=FUENTE, bold=True)
        ws_m.cell(row=i, column=2, value=str(valor)).font = Font(name=FUENTE)
    ws_m.column_dimensions["A"].width = 28
    ws_m.column_dimensions["B"].width = 50

    marca = datetime.now().strftime("%Y%m%d_%H%M")
    destino = Path(ruta_salida) / f"rendimiento_{marca}.xlsx"
    wb.save(destino)

    # Copia estable para quien siempre abre "el último"
    wb.save(Path(ruta_salida) / "rendimiento_ultimo.xlsx")
    return destino
```

> **Por qué dos archivos:** el versionado con timestamp preserva el histórico y permite auditar; `rendimiento_ultimo.xlsx` da una ruta fija para quien solo quiere abrir el más reciente sin buscar. Cuesta una línea y evita la pregunta "¿cuál es el bueno?".

**La hoja `Metadatos` no es decorativa.** Cuando alguien cuestione una cifra dentro de tres semanas, esa hoja dice exactamente qué ventana se extrajo, cuándo y con qué versión del código. Sin ella, la discusión no tiene cómo cerrarse.

---

### Fase 8 — Orquestador

```python
# src/main.py
import argparse
import logging
import sys
import time
from datetime import datetime, timedelta

from . import config, client, extract, transform, storage, report

def configurar_logs():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(f"logs/wolkvox_{datetime.now():%Y%m%d}.log", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )

def ejecutar(dias_atras: int = 0, solo_extraer: bool = False):
    cfg = config.cargar_config()
    storage.inicializar(cfg["almacenamiento"]["ruta_db"])

    objetivo = datetime.now() - timedelta(days=dias_atras)
    dt_ini = objetivo.replace(hour=0, minute=0, second=0, microsecond=0)
    dt_fin = min(objetivo.replace(hour=23, minute=59, second=59), datetime.now())

    log = logging.getLogger("main")
    log.info("Ventana: %s → %s", dt_ini, dt_fin)

    resultados = {}
    with client.WolkvoxClient(**cfg["wolkvox"]) as api:
        tareas = [
            ("agentes",       lambda: extract.agentes_catalogo(api)),
            ("agente_dia",    lambda: extract.agente_dia(api, dt_ini, dt_fin)),
            ("agente_hora",   lambda: extract.agente_hora(api, dt_ini, dt_fin)),
            ("llamadas",      lambda: extract.llamadas_detalle(api, dt_ini, dt_fin)),
            ("no_conectadas", lambda: extract.llamadas_no_conectadas(api, dt_ini, dt_fin)),
        ]
        for nombre, fn in tareas:          # secuencial: el token no admite paralelismo
            t0 = time.perf_counter()
            try:
                resultados[nombre] = fn()
                estado, msg = "OK", None
            except Exception as e:
                resultados[nombre] = []
                estado, msg = "ERROR", str(e)
                log.error("Falló %s: %s", nombre, e)
            finally:
                with storage.conexion(cfg["almacenamiento"]["ruta_db"]) as conn:
                    conn.execute(
                        "INSERT INTO log_ejecucion "
                        "(endpoint, date_ini, date_end, estado, registros, mensaje, "
                        " duracion_seg, ejecutado_en) VALUES (?,?,?,?,?,?,?,?)",
                        (nombre, dt_ini.isoformat(), dt_fin.isoformat(), estado,
                         len(resultados[nombre]), msg,
                         round(time.perf_counter() - t0, 2), datetime.now().isoformat()),
                    )
            time.sleep(2)                  # respiro entre llamadas

    if not resultados["agente_dia"] and not resultados["llamadas"]:
        log.error("Sin datos en las fuentes principales. Se aborta sin generar reporte.")
        return 1

    # ... transformar, persistir con upsert y generar el Excel ...
    log.info("Corrida finalizada")
    return 0

if __name__ == "__main__":
    configurar_logs()
    p = argparse.ArgumentParser()
    p.add_argument("--dias-atras", type=int, default=0)
    p.add_argument("--solo-extraer", action="store_true")
    args = p.parse_args()
    sys.exit(ejecutar(args.dias_atras, args.solo_extraer))
```

**Decisión clave:** si fallan las fuentes principales, **no se genera Excel**. Es preferible que el archivo no se actualice (y alguien lo note) a publicar un reporte con ceros que parezca válido. Un reporte silenciosamente incorrecto erosiona la confianza en toda la automatización.

---

## 8. Programación de la ejecución

### Opción recomendada: Jenkins

Encaja con el entorno de automatizaciones existente y aporta historial de builds, logs centralizados, gestión de credenciales y notificación de fallos.

```groovy
pipeline {
    agent any
    triggers { cron('0 6-21 * * 1-6') }   // cada hora, 6am-9pm, lunes a sábado
    environment {
        WOLKVOX_TOKEN = credentials('wolkvox-token-prod')
    }
    stages {
        stage('Preparar') {
            steps { bat 'python -m venv .venv && .venv\\Scripts\\pip install -r requirements.txt' }
        }
        stage('Ejecutar') {
            steps { bat '.venv\\Scripts\\python -m src.main' }
        }
        stage('Publicar') {
            steps { archiveArtifacts artifacts: 'output/*.xlsx', fingerprint: true }
        }
    }
    post {
        failure { emailext subject: "Wolkvox: falló la corrida", to: 'equipo@empresa.com' }
        always  { archiveArtifacts artifacts: 'logs/*.log', allowEmptyArchive: true }
    }
}
```

**Job adicional de reconciliación** — diario a las 23:30:

```groovy
triggers { cron('30 23 * * *') }
// bat '.venv\\Scripts\\python -m src.main --dias-atras 1'
// bat '.venv\\Scripts\\python -m src.main --dias-atras 2'
// bat '.venv\\Scripts\\python -m src.main --dias-atras 3'
```

### Alternativa: Programador de tareas de Windows

Si Jenkins no está disponible en el servidor destino, un `.bat` invocado por el Task Scheduler funciona. Se pierde el historial de builds y la gestión de credenciales, que hay que resolver con variables de entorno del sistema.

---

## 9. Manejo de errores y operación

| Escenario | Comportamiento esperado |
|---|---|
| Timeout de un endpoint | 3 reintentos con backoff exponencial; si persiste, se registra en `log_ejecucion` y continúa con el resto |
| Token inválido / expirado | Falla la corrida completa y notifica. Sin reintento (no se va a arreglar solo) |
| Respuesta cerca de 60.000 registros | Excepción explícita pidiendo reducir `bloque_horas`. Nunca truncar en silencio |
| Cero registros en la ventana | Válido fuera de jornada. Se registra y no genera reporte |
| Campo nuevo o renombrado en la respuesta | La capa de mapeo lo ignora y lo registra en el log. No rompe la corrida |
| Excel abierto por un usuario al momento de escribir | Escribir a archivo temporal y renombrar; capturar `PermissionError` y reintentar |

**Monitoreo mínimo:** una consulta diaria sobre `log_ejecucion` que reporte corridas con estado `ERROR`. Sin esto, la automatización puede llevar semanas fallando en silencio mientras alguien sigue abriendo un Excel viejo.

---

## 10. Plan de pruebas

| Nivel | Qué se prueba |
|---|---|
| Unitario | `hhmmss_a_segundos` con formatos válidos, vacíos, nulos y basura |
| Unitario | `categorizar` con valores conocidos, desconocidos y nulos |
| Unitario | Cálculo de ocupación con `tiempo_login = 0` (división por cero) |
| Integración | `upsert` dos veces con los mismos datos → mismo conteo de filas |
| Integración | Extracción contra fixtures de Fase 0, sin red |
| Aceptación | Totales del Excel vs. reporte manual del Manager para el mismo día |
| Humo | Corrida completa en preproducción con el token de desarrollo |

**La prueba de aceptación es la que decide si el proyecto sirve.** Mientras los números no cuadren con lo que la operación ya conoce, el reporte no se publica.

---

## 11. Roadmap de escalamiento

| Fase | Alcance | Habilitado por |
|---|---|---|
| **1** | Excel horario con resumen y gráficos | Este documento |
| **2** | Más endpoints: tiempo auxiliar por tipo (`agent_3`), tipificaciones (`act_1`, `act_4`), nivel de servicio (`skill_1`, `skill_2`) | El cliente y el esquema ya soportan agregar fuentes |
| **3** | Alertas por umbral (ocupación baja, abandono alto) vía correo o Teams | El histórico en SQLite permite calcular la línea base |
| **4** | Carril de tiempo real: `real_time.php?api=calls_last_10` + `api=campaigns`, con proceso residente y latencia de 1-3 minutos | Requiere proceso residente, **no** un job de Jenkins por minuto |
| **5** | API de consulta con FastAPI + front liviano de supervisión | La capa `storage` ya aísla el acceso a datos |
| **6** | Migración a PostgreSQL o export a parquet para la capa analítica | La capa `storage` se reemplaza sin tocar lógica de negocio |
| **7** | Canales digitales (chat, WhatsApp) con `chat_4` y `diagram_9` | Mismo patrón de extracción |

**Sobre la Fase 4:** el "tiempo real" de Wolkvox es *polling*, no *streaming*. La recomendación del proveedor es consumir cada 5 minutos, y `calls_last_10` da una ventana de 10 minutos, lo que permite consultar cada 2-3 minutos con solapamiento y deduplicar por ID. La latencia realista es de **1 a 3 minutos**, no de segundos. Si el negocio pide "tiempo real" esperando segundos, esa expectativa debe alinearse antes de comprometer el desarrollo. Vale la pena consultar con el partner si la operación tiene habilitados **webhooks**, que cambiarían el modelo de *pull* a *push*.

---

## 12. Riesgos

| Riesgo | Impacto | Mitigación |
|---|---|---|
| Los campos de la API difieren de la documentación web | Alto | Fase 0 obligatoria: documentar el contrato real antes de codificar |
| El consumo de API tiene costo no previsto | Alto | Confirmar con el ejecutivo comercial antes de desarrollar |
| La fórmula de ocupación no coincide con la que usa la operación | Alto | Validar definiciones con la jefatura antes del primer reporte publicado |
| Wolkvox cambia el contrato de la API en un release | Medio | Capa de mapeo aislada; campos desconocidos se registran sin romper la corrida |
| El volumen de CDR supera el límite por consumo | Medio | Extracción particionada con guard explícito |
| Token comprometido | Medio | Fuera del repositorio, en credenciales gestionadas; token separado para desarrollo |
| El servidor se apaga y se pierden corridas | Bajo | La ventana deslizante recupera automáticamente en la siguiente ejecución |

---

## 13. Checklist antes de pasar a producción

- [ ] Costo del consumo de API confirmado por escrito con el proveedor
- [ ] Tokens separados de producción y desarrollo, ninguno en el repositorio
- [ ] `docs/contrato-api.md` completo, con campos y tipos reales
- [ ] Totales validados contra el Manager para al menos 3 días distintos
- [ ] Fórmula de ocupación aprobada por la jefatura de operación
- [ ] Pruebas unitarias y de integración en verde
- [ ] Job de reconciliación nocturna programado
- [ ] Alerta de fallo configurada y probada (forzar un fallo y verificar que llega)
- [ ] Respaldo del archivo `.db` incluido en la rutina de backup del servidor
- [ ] README con instrucciones de operación para un tercero

---

## Anexo A — Referencias

| Recurso | URL |
|---|---|
| Portal de desarrolladores | https://developers.wolkvox.com/es-apis-v2/ |
| Guía de consumo de API | https://wolkvox.helpjuice.com/es_CO/como-puedo-consumir-el-api-de-reportes |
| Colección de Postman | https://www.postman.com/wolkvox-api/team-workspace/documentation/0zzxgci/wolkvox-apis-es |

## Anexo B — Glosario

| Término | Definición |
|---|---|
| **ACW** | *After Call Work*. Tiempo de gestión posterior a la llamada |
| **AHT** | *Average Handle Time*. Tiempo promedio de gestión (conversación + hold + ACW) |
| **RPC** | *Right Party Contact*. Contacto efectivo con la persona correcta |
| **CDR** | *Call Detail Record*. Registro detallado de llamada |
| **Hit** | Gestión exitosa según la definición de la campaña |
| **Skill** | Cola de atención con enrutamiento por habilidad |
| **Ocupación** | Proporción del tiempo logueado dedicado a actividad productiva |
| **Tipificación** | Código de actividad con que el agente clasifica el resultado de la gestión |
