# wolkvox-rendimiento

Extrae indicadores de rendimiento de call center desde la API v2 de Wolkvox y
los entrega en dos capas: un **Excel** auditable con el detalle (la maestra) y
un **tablero HTML** para coordinación, ambos calculados de la misma fuente.

El flujo es lineal y sin base de datos:

```
extraer  ->  transformar  ->  calcular  ->  publicar
(API v2)     (DataFrames)     (dominio)     (Excel + tablero + backup CSV)
```

Los nombres de campo de la API se tomaron de la colección oficial de Postman
(`wolkvox APIs (ES).postman_collection.json`), no de la documentación web.

## Arquitectura

Hexagonal, sin ceremonia: no hay clases abstractas ni inyección de
dependencias porque a esta escala serían andamio. Lo que sí se sostiene es la
regla de dependencia — **todo apunta hacia adentro**:

```
main.py          traduce la línea de comandos. Nada más.
   |
   v
src/aplicacion   casos de uso: qué se pide, en qué orden, a dónde va
   |                analisis.py   informe de gestión (diario, semanal, mensual, intradía)
   |
   +--> src/dominio       las reglas. Cero IO: pandas y nada más
   |       asistencia.py  puntualidad contra el horario pactado
   |       gestion.py     tiempos, efectividad y alertas
   |       nombres.py     identidad del asesor y días de la semana
   |
   +--> src/adaptadores   todo lo que sale del proceso
           wolkvox/       cliente HTTP, extracción por endpoint, traducción a DataFrames
           almacen/       horarios (YAML + Excel), respaldo CSV, candado de corridas
           publicacion/   Excel de análisis (la maestra), retención, tablero HTML
```

El dominio recibe el horario ya resuelto como un `dict` y no sabe que salió de
un Excel; recibe DataFrames y no sabe que vinieron de una API. Por eso sus
pruebas no montan nada: son datos en memoria.

La regla no es un acuerdo verbal, es
[tests/test_arquitectura.py](tests/test_arquitectura.py): falla si alguien
importa un adaptador desde el dominio o mete `yaml` en el núcleo. Un layout
hexagonal se degrada callado, y esa prueba avisa cuando arreglarlo todavía es
barato.

## Estructura

```
wolkvox-rendimiento/
├── main.py                        # CLI: traduce argumentos a un caso de uso
├── horarios.yaml                  # horarios, tolerancia, festivos y umbrales de alerta
├── config/
│   ├── paths.py                   # ROOT_DIR — raíz del proyecto, referencia única
│   ├── settings.py                # carga .env
│   └── logger_config.py           # setup_logger() — archivo + consola, purga logs > 3 días
├── src/
│   ├── dominio/                   # las reglas. Cero IO
│   │   ├── asistencia.py          # puntualidad: login/logout contra el horario pactado
│   │   ├── gestion.py             # tiempos por estado, efectividad, alertas y curva horaria
│   │   └── nombres.py             # identidad del asesor y días de la semana
│   ├── aplicacion/                # casos de uso
│   │   └── analisis.py            # informe de gestión (diario, semanal, mensual, intradía)
│   ├── adaptadores/
│   │   ├── wolkvox/
│   │   │   ├── cliente.py         # cliente HTTP (auth, reintentos)
│   │   │   ├── extraccion.py      # un método por endpoint + partición de ventanas
│   │   │   └── traduccion.py      # registros crudos -> DataFrames
│   │   ├── almacen/
│   │   │   ├── horarios.py        # horarios.yaml + los Excel de cronograma
│   │   │   ├── respaldo.py        # CSV de lo extraído, últimos 3 días
│   │   │   └── candado.py         # impide dos corridas simultáneas sobre el token
│   │   └── publicacion/
│   │       ├── excel_analisis.py  # Excel del informe de gestión (la maestra)
│   │       ├── retencion.py       # consolida cortes del día y purga por antigüedad
│   │       ├── tablero_datos.py   # almacén JSON por periodo
│   │       ├── tablero_html.py    # render del tablero
│   │       └── plantillas/
│   │           └── tablero.html   # la plantilla, autocontenida
│   ├── data/                      # backup CSV (en .gitignore)
│   └── output/                    # Excel y tablero generados (en .gitignore)
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
3. Copiar los cronogramas de la operación a `src/data/` (los `.xlsx` no se
   versionan: el `.gitignore` de la raíz excluye `*.xlsx` y `*.csv`).
4. `python main.py --analisis --periodo semana`

## Uso

```bash
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
punto de cambio es `src/adaptadores/almacen/respaldo.py`, sin tocar el resto.

## Endpoints usados

| Endpoint | Uso |
|---|---|
| `reports_manager.php?api=agent_1` | Resumen de tiempos por agente en la ventana |
| `reports_manager.php?api=agent_3` | Tiempo auxiliar desglosado por tipo de pausa |
| `reports_manager.php?api=agent_7` | Login/logout por agente y día (base de la puntualidad) |
| `reports_manager.php?api=cdr_1` | Detalle de llamadas conectadas/tipificadas |
| `reports_manager.php?api=chat_1` | Detalle de conversaciones (WhatsApp/chat) |
| `reports_manager.php?api=chat_16` | Productividad digital por agente |
| `information.php?api=activity_codes` | Inventario de códigos con sus banderas hit/rpc |

La efectividad sale de `activity_codes`, no de un mapeo mantenido a mano: son
las banderas que la propia operación ya configuró en Wolkvox.


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

## Tablero HTML para coordinación

El Excel es la capa de **evidencia** (filtros, detalle día a día, trazabilidad);
el tablero es la capa de **decisión** para el coordinador: a quién acompañar hoy
y por qué. Los dos salen de la misma corrida.

**Una sola fuente de cálculo.** El tablero recibe el mismo dict `cuadros` que
alimenta el Excel y no recalcula nada: `tablero_datos.construir()` solo
selecciona, aplana y serializa. Si un número difiere entre los dos, es un error
de presentación, no dos verdades. Cualquier cifra nueva se agrega en
`gestion.py` o `asistencia.py`, nunca en el renderizador.

**Un archivo único con selector.** `tablero.html` acumula el histórico y trae
selector Día / Semana, así que la operación conserva un solo enlace. Cada
corrida deja el JSON de su periodo en `tablero/{etiqueta}.json` y el HTML se
rearma leyendo la carpeta completa. Ese mismo archivo resuelve los deltas
("vs. semana 31"): se busca el periodo previo del mismo tipo en el almacén, sin
volver a consultar la API. Retención por defecto: 21 días, 16 semanas, 13 meses.

**`RUTA_TABLERO` tiene que apuntar a la carpeta compartida.** El workspace de
Jenkins no sirve: el job diario y el semanal tienen workspaces distintos y cada
uno publicaría un tablero con solo sus propios periodos.

**No archivar el tablero como artefacto de Jenkins.** Jenkins sirve lo archivado
con una CSP que bloquea el CSS y el JS embebidos, y la página sale en blanco.
`main.py` lo escribe directo en `RUTA_TABLERO`; el `.xlsx` sí se archiva.

Las escrituras (JSON y HTML) son atómicas (`.tmp` + `os.replace`): una corrida
que muera a mitad deja el tablero anterior intacto, nunca un archivo truncado.

Los umbrales del semáforo salen de `horarios.yaml` y se muestran impresos en
cada columna (`meta ≤ 30 %`), porque un semáforo cuyo umbral nadie conoce no
sirve para nada.

## Corridas intradía

Coordinación monitorea la operación durante el día, así que además del diario y
el semanal hay ocho corridas de jornada en curso:

```bash
python main.py --analisis --periodo dia --hoy
```

`--hoy` fija la ventana en `00:00 → ahora`. Cortes: **08:10, 08:40, 09:10,
09:40, 12:10, 14:10, 16:10 y 18:10** — a los diez minutos en punto para que el
login del turno ya esté registrado cuando la corrida lo lee.

Cada corte **regenera todo**: login, tiempos, auxiliares, llamadas y digital. No
hay extracción parcial ni acumulación de estado; es preferible gastar consumos y
tener la certeza de que nada quedó viejo. Son ~8 consumos por corrida, ~65 al día.

**La nómina manda.** `asistencia.detalle()` enumera los asesores desde
`horarios.yaml`, no desde los datos de login. Quien no se ha conectado aparece
con su fila marcada en vez de desaparecer del informe — si el ausente es
invisible, el coordinador ve "todo bien" justo cuando hay algo que atender. Es
lo que hace útil el corte de las 08:10. Aplica igual al Excel: un asesor sin
actividad en todo el periodo sale con `Activo: No` y no mueve ningún promedio.

Una jornada sin ningún login no aborta la corrida: publica la nómina completa
sin conexión, que es exactamente la información que se necesita a esa hora.

### Por qué cada corte escribe un archivo nuevo

En Windows, un `.xlsx` abierto por alguien queda tomado y `wb.save()` falla. Con
ocho corridas al día y coordinación consultando el detalle, eso pasa. La
solución no es manejar el error sino que la colisión no pueda ocurrir:

```
analisis_gestion_2026-08-17_0810.xlsx
analisis_gestion_2026-08-17_1210.xlsx
analisis_gestion_2026-08-17_1610.xlsx   <- el corte actual
analisis_gestion_2026-08-16.xlsx        <- ayer, consolidado
```

Nadie puede tener abierto un archivo que hace un segundo no existía. El tablero
enlaza al Excel del corte que está mostrando, así que el nombre largo no le
estorba a nadie.

`excel_purga.limpiar()` corre en cada ejecución y hace dos cosas: consolida los
días ya cerrados —conserva un solo archivo por fecha y borra sus demás cortes— y
aplica la retención de `DIAS_EXCEL`. Un día se da por cerrado si es anterior a
hoy, si ya tiene su consolidado o si alguno de sus cortes es de
`HORA_CIERRE_JORNADA` en adelante; así la corrida de las 18:10 limpia las
anteriores sin bandera especial, y si esa corrida falla, cualquier ejecución
posterior termina el trabajo. Un borrado que falla porque alguien tiene el
archivo abierto se registra y se salta.

El tablero, en cambio, no corre ese riesgo: los navegadores leen el HTML y
sueltan el archivo. Aun así la escritura reintenta tres veces, porque el
antivirus o el indexador de Windows sí pueden retenerlo un instante.

## El pipeline no depende del orquestador

Todo lo que hace falta para operar vive en `main.py`: dónde publicar, la
exclusión mutua entre corridas y la limpieza de archivos. Jenkins solo trae el
último cambio del repo y ejecuta el comando; lo mismo funciona desde el
Programador de tareas de Windows o lanzado a mano.

**Exclusión mutua.** El token de Wolkvox no admite consumos en paralelo, y con
ocho corridas intradía más la diaria y la semanal que dos se pisen deja de ser
hipotético. `bloqueo.tomar()` escribe un candado en `RUTA_TABLERO` —la carpeta
compartida, único punto por el que pasan todas las corridas— antes de consumir
API. Si otra corrida lo tiene, esta se omite y **termina en verde**: la otra va
a dejar los datos igual, así que no hay nada que reportar en rojo. Un candado
huérfano caduca a los 45 minutos, para que una corrida muerta de madrugada no
deje la operación sin tablero.

Está en el programa y no en el `Jenkinsfile` a propósito: un lock de Jenkins
solo protege de Jenkins.

**Publicación.** `RUTA_SALIDA` y `RUTA_TABLERO` apuntan a la carpeta compartida
en el `.env` del servidor. No hay paso de copia posterior ni artefactos que
mover: el programa escribe donde la operación lee.

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

1. **Festivos.** `horarios.yaml` trae `festivos: []`. Sin cargarlos, cada
   festivo deja las ocho corridas intradía en rojo.
2. **Totales contra el Manager.** Comparar los totales del Excel contra un
   reporte generado manualmente, para al menos 3 días distintos.


## Pruebas

```bash
python -m pip install -r requirements-dev.txt   # requirements + pytest
python -m pytest -q
```

No consumen API y se pueden correr en cualquier equipo recién clonado. En un
clon sin `src/data` dan **172 passed, 4 skipped**: las cuatro que leen los
cronogramas reales se saltan solas en vez de fallar. Con los Excel copiados
son 176.

Cubren el parseo de tiempos, la puntualidad contra el cronograma, la
efectividad por canal, la rotación del backup, la retención de maestras, el
candado de corridas, la generación del tablero y la regla de dependencia de
la arquitectura.
