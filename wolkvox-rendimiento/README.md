# wolkvox-rendimiento

Extrae indicadores de rendimiento de call center desde la API v2 de Wolkvox y
los entrega como un Excel con resumen y gráficos.

El flujo es lineal y sin base de datos:

```
extraer  ->  transformar  ->  exportar
(API v2)     (DataFrames)     (Excel + backup CSV de 3 días)
```

Los nombres de campo de la API se tomaron de la colección oficial de Postman
(`wolkvox APIs (ES).postman_collection.json`), no de la documentación web.

## Estructura

```
wolkvox-rendimiento/
├── main.py                   # orquestador — extraer / transformar / exportar
├── categorias.yaml            # mapeo de categoría de negocio (editable sin tocar código)
├── horarios.yaml               # horarios, tolerancia, festivos y umbrales de alerta
├── config/
│   ├── paths.py                # ROOT_DIR — raíz del proyecto, referencia única
│   ├── settings.py             # carga .env
│   └── logger_config.py        # setup_logger() — archivo + consola, purga logs > 3 días
├── src/
│   ├── api/
│   │   ├── client.py            # cliente HTTP de Wolkvox (auth, reintentos)
│   │   └── extract.py           # un método por endpoint + partición de ventanas grandes
│   ├── services/
│   │   ├── transform.py          # normaliza a DataFrames, tiempos a segundos, categoriza
│   │   ├── backup.py             # CSV de lo extraído, últimos 3 días
│   │   ├── report.py             # Excel del reporte diario
│   │   ├── horarios_excel.py     # lee los cronogramas de la operación
│   │   ├── asistencia.py         # puntualidad: login/logout contra el horario pactado
│   │   ├── gestion.py            # tiempos por estado, efectividad y cruce
│   │   └── reporte_analisis.py   # Excel del informe de análisis
│   ├── data/                       # backup CSV (en .gitignore)
│   └── output/                      # Excel generados (en .gitignore)
├── tests/
└── logs/
```

`config/paths.py` define `ROOT_DIR` una sola vez; el resto del proyecto lo
importa desde ahí en vez de recalcular rutas relativas por su cuenta.

## Lo único que falta para arrancar

1. Abrir `.env` (ya existe) y completar `WOLKVOX_SERVER` (los 4 dígitos del
   servidor, ej. `3211`) y `WOLKVOX_TOKEN` (wolkvox Manager → Configuración →
   Integraciones → Tokens).
2. `pip install -r requirements.txt`
3. `python main.py`

## Uso

```bash
# Reporte diario de operación
python main.py                  # procesa el día de hoy hasta la hora actual
python main.py --dias-atras 1   # reprocesa el día de ayer completo
python main.py --sin-excel      # extrae y respalda, sin generar el Excel

# Informe de gestión — tres modos. Sin fechas toma el último periodo CERRADO
python main.py --analisis --periodo mes       # el mes pasado completo
python main.py --analisis --periodo semana    # la semana pasada (lunes a domingo)
python main.py --analisis --periodo dia       # ayer

# Con rango explícito: un Excel por cada periodo dentro del rango
python main.py --analisis --periodo mes --desde 2026-07-01 --hasta 2026-08-31

# Regenerar el Excel desde el backup, sin consumir API ni tocar la red
python main.py --analisis --desde 2026-07-01 --hasta 2026-08-12 --desde-backup
```

Los tres modos producen el mismo informe con las mismas 11 hojas; lo que
cambia es la ventana que agregan y el nombre del archivo:

| Modo | Archivo | Umbral de asesor activo |
|---|---|---|
| `mes` | `analisis_gestion_2026-07.xlsx` | 5 días trabajados |
| `semana` | `analisis_gestion_2026-S31.xlsx` | 2 días |
| `dia` | `analisis_gestion_2026-08-12.xlsx` | 1 día |

El umbral se define por modo en `horarios.yaml`: exigir 5 días en un informe
diario dejaría a todos fuera de los promedios generales.

**El modo elegido gobierna la extracción, no solo el reporte.** `agent_1` y
`agent_3` agregan todo el rango que se les consulte sin desglose interno por
fecha, así que las cifras de una semana solo salen consultando esa semana.
Por eso una semana a caballo entre dos meses sale en **un** archivo, no dos.

Cada corrida reextrae el día completo. Como el backup es un CSV por dataset y
día (`llamadas_2026-08-11.csv`), reprocesar simplemente reescribe esos
archivos: no hay duplicados que deduplicar ni estado que corromper.

Si fallan las fuentes principales, **no se genera Excel**: es preferible que
el archivo no se actualice (y alguien lo note) a publicar un reporte en ceros
que parezca válido.

## Por qué sin base de datos

El reporte se calcula siempre desde lo que se acaba de extraer, así que no hay
nada que consultar entre corridas. El backup en CSV cubre lo único que sí hace
falta —poder auditar una cifra o rehacer un Excel sin volver a pegarle a la
API— y se puede abrir en Excel o cargar con `pd.read_csv` sin herramientas
extra. Si más adelante se necesita histórico largo o consultas cruzadas, el
punto de cambio es `src/services/backup.py`, sin tocar el resto.

## Endpoints usados

| Endpoint | Uso |
|---|---|
| `information.php?api=agents` | Catálogo de agentes |
| `reports_manager.php?api=agent_1` | Resumen de tiempos por agente en la ventana |
| `reports_manager.php?api=agent_8` | Tiempos por agente, hora a hora |
| `reports_manager.php?api=cdr_1` | Detalle de llamadas conectadas/tipificadas |
| `reports_manager.php?api=cdr_5` | Detalle de intentos **no** conectados |

Se usa `cdr_5` y no `cdr_6` para "no conectadas": `cdr_6` solo devuelve un
conteo agregado por resultado (`{result, count}`), sin fecha ni hora por
registro, lo que impide construir la curva horaria. `cdr_5` sí trae `date` y
`conn_id` por intento.

## Categorización de negocio

Wolkvox no entrega un único campo "efectiva / colgada / no contactada". Se
deriva de dos fuentes independientes, configurables en `categorias.yaml`:

- **Conectadas** (`cdr_1`): por el código de tipificación que el agente le da
  a la llamada (`cod_act`).
- **No conectadas** (`cdr_5`): por el resultado técnico que reporta la red
  (`result`: `Cancel`, `Chanunavail`, `Congestion`, `Busy`, `No answer`,
  `Tcpa`, `Do not call` — valores fijos documentados por Wolkvox).

Los campos crudos se conservan siempre; `categoria_negocio` se agrega al lado,
nunca reemplaza al dato original. Lo que no esté mapeado cae en
`sin_clasificar`, visible en el reporte en vez de perderse.

## Hojas del Excel

| Hoja | Contenido |
|---|---|
| `Resumen` | KPIs del día (llamadas, AHT, ocupación, hits, RPC) |
| `Resultados` | Distribución por categoría + gráfico de torta |
| `Por agente` | Tabla por asesor + gráfico de barras |
| `Por hora` | Curva horaria de llamadas + gráfico de líneas |
| `Metadatos` | Ventana extraída, fecha de corrida, registros por fuente |

Los tiempos se muestran como `HH:MM:SS` porque el Excel lo lee una persona;
los segundos crudos quedan en el backup CSV para cuando haya que calcular.

La hoja `Metadatos` no es decorativa: cuando alguien cuestione una cifra dentro
de tres semanas, dice exactamente qué ventana se extrajo y cuándo.

## Informe de análisis de gestión

Responde a tres problemas concretos de la operación: agentes que se conectan
tarde o se desconectan temprano, exceso de tiempo en auxiliares, y agentes
conectados en estados de gestión con poca efectividad.

```bash
python main.py --analisis --desde 2026-07-01 --hasta 2026-08-12
```

Genera **un Excel por mes** (`analisis_gestion_2026-07.xlsx`, `…-08.xlsx`) con
índice navegable y una hoja por tema: **Resumen general**, **Resumen por
agente** (incluye el desglose de auxiliares), **Puntualidad agente**,
**Puntualidad detalle** (día por día), **Tiempos por agente**, **Auxiliares
detalle** (tabla cruzada asesor × estado), **Auxiliares por tipo**,
**Efectividad** y **Gestión vs efectividad**. Las alertas van resaltadas en
color y las tablas traen filtro automático.

Los tiempos auxiliares se reportan en **horas y minutos** (`41h 06m`), no en
porcentaje. `Auxiliares detalle` repite la misma tabla en horas decimales
(numéricas) para poder sumarlas y graficarlas en Excel; el total de cada
asesor se calcula sobre los valores ya redondeados para que la fila cuadre.

`Auxiliares día a día` abre el desglose fecha por fecha. Como `agent_3`
agrega todo el rango consultado, esa vista se extrae consultando un día a la
vez: son ~25 consumos extra por mes, irrelevantes frente a la cuota diaria.

### Horarios reales (`horarios.yaml` + Excel de la operación)

Los horarios se leen de los cronogramas de la operación
(`src/data/Horarios *.xlsx`), que traen una jornada distinta por día y por
grupo de asesores. `horarios.yaml` define qué archivos leer, qué asesores
cubre cada grupo, quiénes entran al informe, la tolerancia y los umbrales de
alerta.

**El mes de cada hoja no se asume por su nombre**: se deduce validando los
nombres de día contra el calendario real. Si ninguno cuadra, el proceso falla
en vez de calcular tardanzas contra un horario corrido. Eso fue lo que
detectó que la hoja `Ajustados final 1 vuelta` es en realidad el cronograma
de **junio** — y que su último bloque es el único que cubre el 1, 2 y 3 de
julio, días que la hoja `JULIO` no trae.

Las anotaciones `Festivo`, `DESCANSO`, `vacaciones` e `incapacidad` anulan el
día aunque la fila de horario conserve el "8 a 5" de la plantilla. `En casa`
es teletrabajo, o sea día laboral normal.

### Decisiones que afectan los números

| Decisión | Por qué |
|---|---|
| Solo se analizan los asesores listados en `agentes:` | Cuentas de prueba, puntos de enrutamiento y personal de otras áreas distorsionaban todos los promedios |
| Los festivos del cronograma no son días laborales | El archivo los marca arriba pero deja la jornada de la plantilla debajo; sin esto, un festivo contaba como ausencia |
| El día en curso no cuenta para "salió temprano" | A media jornada todos parecerían haberse ido antes de tiempo |
| Un día laboral sin login es "sin conexión", no "entrada tarde" | Separa vacaciones e incapacidades de la impuntualidad |
| Los asesores con menos de N días trabajados quedan fuera de los promedios | Cuentas de prueba o ingresos recientes distorsionaban los indicadores generales; siguen visibles marcados `Activo: No` |
| La efectividad de voz se mide sobre las llamadas **con contacto** | La mayoría de la marcación no contesta; medir sobre el total confunde "no contesté a nadie" con "contacté y fallé" |
| Las gestiones sin tipificar se cuentan aparte y salen del denominador | `TIMEOUTCHAT`/`TIMEOUTACW` es un problema de disciplina, no de resultado. Contarlas como "no efectivas" lo escondería |
| Los promedios generales son razones agregadas, no promedios de porcentajes | Un asesor que tipificó 4 chats de 3.000 no puede aportar un "100 %" a la media |

### Efectividad: no se inventa

Wolkvox ya marca cada código de tipificación con banderas `hit` (cumplió el
objetivo) y `rpc` (hubo contacto real) en `information.php?api=activity_codes`.
El informe usa esa configuración, que es la que la propia operación definió.

## Despliegue en Jenkins

El `Jenkinsfile` de la raíz cubre los tres modos con un parámetro `PERIODO`.
Sin fechas, cada corrida procesa el **último periodo cerrado**, así que el
cron no lleva fechas cableadas: en agosto el job mensual saca julio solo.

**Antes del primer build:**

1. **Credencial**: crear en Jenkins una *Secret text* con id
   `wolkvox-token-prod` y el token de Wolkvox. El pipeline la inyecta como
   `WOLKVOX_TOKEN`, y **la variable de entorno tiene prioridad sobre `.env`**,
   que ni siquiera existe en el workspace (está en `.gitignore`).
2. **Servidor**: ajustar `WOLKVOX_SERVER` en el bloque `environment` si no es
   `0010`.
3. **Correo de fallo**: cambiar el destinatario en el bloque `post { failure }`.
4. **Cronogramas**: los `src/data/Horarios *.xlsx` **sí se versionan** (el
   `.gitignore` solo excluye los `.csv` de backup). Cada mes hay que subir el
   cronograma nuevo, o el informe caerá al horario por defecto.

**Un job por modo**, todos apuntando a este mismo `Jenkinsfile`:

| Job | `PERIODO` | Cron sugerido |
|---|---|---|
| Mensual | `mes` | `H 6 1 * *` — día 1 de cada mes |
| Semanal | `semana` | `H 6 * * 1` — todos los lunes |
| Diario | `dia` | `H 6 * * 2-7` — martes a domingo |

**Lo que ya está resuelto del lado del código:**

- Sale con código `0` en éxito y `1` en fallo, y cualquier excepción no
  controlada queda con traza en el log y marca el build en rojo.
- `disableConcurrentBuilds()` porque el token de Wolkvox no admite consumo en
  paralelo.
- Las pruebas corren antes de gastar tokens: si algo se rompió, el build falla
  sin publicar cifras equivocadas.
- `PYTHONIOENCODING=utf-8` porque los nombres de asesor llevan tildes y ñ.

> El proyecto todavía **no es un repositorio git**. Antes de conectar Jenkins
> hay que hacer `git init`, commit y subirlo al remoto.

## Consumo de tokens

La cuota diaria es **licencias × 1000** y se comparte entre *todos* los tokens
de la operación (consultable con `information.php?api=token_info`). Cada
llamada a la API cuesta exactamente **1 token**, sin importar cuántos
registros devuelva.

Una corrida consume `3 + 2 × bloques`, donde `bloques` es el número de tramos
de `BLOQUE_HORAS` en que se parte el día para `cdr_1` y `cdr_5`: tres
endpoints se consultan de un tirón (`agents`, `agent_1`, `agent_8`) y los dos
de CDR se parten para no chocar contra el límite de 60.000 registros.

| Escenario | Tokens |
|---|---|
| Jornada 6am-9pm, 1 corrida por hora (16 corridas) | 134 /día |
| Reconciliación nocturna (3 días completos) | 33 /día |
| **Total del reporte diario** | **167 /día** |
| Informe de gestión — modo `mes` | ~40 por mes procesado |
| Informe de gestión — modo `semana` | ~15 por semana |
| Informe de gestión — modo `dia` | ~9 por día |

Los tres jobs programados juntos consumen menos de 100 tokens al día sobre
una cuota de 8.000. El grueso del modo `mes` es la extracción día a día de
auxiliares (~25 consumos), que es lo que alimenta la hoja `Auxiliares día a
día`.

Con `BLOQUE_HORAS=6` el pipeline usa **~2 % de una cuota de 8.000**. Bajar el
bloque sube el consumo pero sigue siendo marginal: `=3` → 259/día, `=2` →
353/día, `=1` → 633/día (8 %).

## ✅ Verificado contra la API real

- **Conexión y token**: OK contra `wv0010`.
- **Formato de los tiempos**: `"HH:MM:SS"` confirmado (`login_time: '02:02:47'`).
  Todos los campos llegan como `String`, incluidos los numéricos.
- **Ocupación**: Wolkvox ya la calcula y la entrega en `occupancy` como
  `'34.64 %'`. **Se usa ese valor en vez de recalcularlo**, así el reporte
  cuadra con el Manager por construcción. Su fórmula, verificada contra datos
  reales, es `(inbound_time + outbound_time + acw_time) / (login_time - aux_time)`
  — que no coincide con la que suele asumirse (`(login - ready - aux) / login`)
  y habría dado 24,08 % donde el Manager muestra 34,64 %.

## ⚠️ Pendientes antes de publicar el primer reporte

1. **Códigos de tipificación reales.** `categorias.yaml` trae códigos de
   ejemplo (`VENTA`, `NO_INTERES`…). Reemplazarlos por el inventario real de
   "Códigos de actividad" de la operación (Manager → Configuración → Códigos
   de actividad, o vía la API `act_1`). Mientras tanto las llamadas caen en
   `sin_clasificar`, visible en el reporte.
2. **Totales contra el Manager.** Comparar los totales del Excel contra un
   reporte generado manualmente, para al menos 3 días distintos.

Pendiente de infraestructura: la programación horaria (Task Scheduler o
Jenkins) se agrega cuando el pipeline esté validado con datos reales.

## Pruebas

```bash
python -m pytest -q
```

Cubren el parseo de tiempos, la ocupación, la categorización, la rotación del
backup y la generación del Excel de punta a punta (incluido el caso de un día
sin datos).
