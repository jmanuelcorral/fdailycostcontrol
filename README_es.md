# fdailyCostControl — Azure Function de Monitorización de Costes

Azure Function (Python v2) que monitoriza los costes diarios de Azure por Resource Group y envía alertas vía webhook a Pandora FMS cuando se superan los umbrales configurados.

---

## Índice

1. [Arquitectura general](#arquitectura-general)
2. [Estructura del proyecto](#estructura-del-proyecto)
3. [Flujo de ejecución](#flujo-de-ejecución)
4. [Módulos](#módulos)
5. [Configuración](#configuración)
6. [Autenticación](#autenticación)
7. [Control de alertas duplicadas (Cooldown)](#control-de-alertas-duplicadas-cooldown)
8. [Payload de alerta](#payload-de-alerta)
9. [Tests](#tests)
10. [Despliegue en Azure](#despliegue-en-azure)
11. [Desarrollo local](#desarrollo-local)
12. [Troubleshooting](#troubleshooting)

---

## Arquitectura general

```
┌─────────────────────────────────────────────────────────────────────┐
│                      Azure Function (Timer)                        │
│                      Ejecuta cada 5 minutos                        │
│                                                                     │
│  ┌──────────────┐    ┌──────────────┐    ┌────────────────────┐    │
│  │ cost_client   │───▶│ function_app │───▶│ webhook_notifier   │    │
│  │ (Cost Mgmt   │    │ (orquestador)│    │ (HTTP GET/POST)    │    │
│  │  API query)  │    │              │    │                    │    │
│  └──────────────┘    └──────┬───────┘    └────────┬───────────┘    │
│                             │                      │                │
│                    ┌────────▼────────┐              │                │
│                    │ alert_tracker   │              │                │
│                    │ (Table Storage) │              │                │
│                    └─────────────────┘              │                │
└─────────────────────────────────────────────────────┼────────────────┘
                                                      │
                                            ┌─────────▼──────────┐
                                            │   Pandora FMS      │
                                            │   (Webhooks)       │
                                            └────────────────────┘
```

---

## Estructura del proyecto

```
fdailyCostControl/
├── modules/                     # Paquete de lógica de negocio
│   ├── __init__.py              # Exporta las funciones públicas
│   ├── cost_client.py           # Cliente de Azure Cost Management API
│   ├── webhook_notifier.py      # Envío de alertas HTTP a Pandora FMS
│   └── alert_tracker.py         # Control de cooldown con Azure Table Storage
├── tests/                       # Tests unitarios (pytest)
│   ├── __init__.py
│   ├── conftest.py              # Fixtures compartidas
│   ├── test_cost_client.py
│   ├── test_webhook_notifier.py
│   ├── test_alert_tracker.py
│   └── test_function_app.py
├── deployment/                  # Scripts de despliegue e IaC
│   └── deploy.ps1               # Script PowerShell: crea SP + despliega infra
├── function_app.py              # Punto de entrada — Timer trigger + orquestación
├── host.json                    # Configuración del host de Azure Functions
├── local.settings.json          # Variables de entorno para desarrollo local
├── pyproject.toml               # Metadatos del proyecto y dependencias (uv)
├── uv.lock                      # Lockfile generado por uv
├── requirements.txt             # Dependencias de producción (generado por uv export)
├── .funcignore                  # Exclusiones del paquete de despliegue
├── .env.example                 # Plantilla de variables de entorno
├── .gitignore
└── README.md                    # Esta documentación
```

---

## Flujo de ejecución

Cada 5 minutos, la función ejecuta el siguiente flujo:

1. **Carga de configuración**: Lee las variables de entorno `AZURE_SUBSCRIPTION_ID`, `COST_MONITOR_CONFIG`, `WEBHOOK_ENDPOINTS` y `ALERT_COOLDOWN_MINUTES`.

2. **Autenticación**: Inicializa `DefaultAzureCredential()` que automáticamente usa Managed Identity en Azure o Service Principal en entorno local.

3. **Para cada Resource Group configurado**:
   - **Consulta de costes**: Llama a la API de Azure Cost Management para obtener el coste acumulado del día actual (00:00 UTC → ahora), filtrado por el Resource Group.
   - **Evaluación de umbral**: Compara el coste obtenido con el `threshold` configurado.
   - **Si se supera el umbral**:
     - **Verificación de cooldown**: Consulta Azure Table Storage para comprobar si ya se envió una alerta reciente para ese RG (dentro del periodo de cooldown).
     - **Envío de alerta**: Si no hay cooldown activo, construye el payload de alerta y lo envía a todos los endpoints de Pandora FMS configurados.
     - **Registro**: Si al menos un endpoint responde con éxito (HTTP 2xx), registra la alerta en Table Storage.

4. **Resumen**: Registra en logs el total de alertas enviadas, omitidas por cooldown y errores.

---

## Módulos

### `function_app.py` — Orquestador

- **Timer trigger**: Expresión CRON `0 */5 * * * *` (cada 5 minutos).
- **`_load_json_env(var_name)`**: Utilidad para parsear variables de entorno que contienen JSON arrays.
- **`timer_trigger()`**: Función principal que orquesta todo el flujo descrito arriba.

### `modules/cost_client.py` — Cliente de Cost Management

- **`get_daily_cost(credential, subscription_id, resource_group) -> float`**
  - Usa el SDK `azure-mgmt-costmanagement` para consultar la API.
  - Genera una `QueryDefinition` de tipo `ActualCost` con timeframe personalizado (día actual).
  - Filtra por Resource Group usando `QueryFilter` con operador `In`.
  - Agrupa por `ResourceGroup` y suma el coste total (`Sum` de `Cost`).
  - Retorna `0.0` si no hay datos de coste.

### `modules/webhook_notifier.py` — Notificador HTTP

- **`send_alert(endpoints_config, alert_data) -> list`**
  - Itera sobre todos los endpoints configurados.
  - Soporta métodos HTTP **POST** (envía JSON body) y **GET** (envía query parameters).
  - Cada endpoint puede tener sus propios headers (ej: `Authorization`).
  - Maneja errores individuales por endpoint sin bloquear los demás.
  - Timeout de 30s por petición.
  - Retorna lista de resultados con estado de éxito/fallo por endpoint.

- **`build_alert_data(...) -> dict`**
  - Construye el payload de alerta con todos los campos relevantes.

### `modules/alert_tracker.py` — Control de Cooldown

- **`should_send_alert(resource_group, cooldown_minutes) -> bool`**
  - Consulta la tabla `CostAlertTracker` en Azure Table Storage.
  - Particionada por fecha (`YYYY-MM-DD`), clave de fila por resource group.
  - Si la última alerta fue hace menos de `cooldown_minutes`, retorna `False`.
  - En caso de error, aplica "fail open" (permite la alerta).

- **`record_alert_sent(resource_group, current_cost, threshold)`**
  - Registra/actualiza la entidad en Table Storage con timestamp y datos del coste.
  - Usa `upsert_entity` para crear o sobrescribir la entrada.

---

## Configuración

Toda la configuración se gestiona mediante **variables de entorno** (App Settings en Azure, `local.settings.json` en local).

### Variables obligatorias

| Variable | Tipo | Descripción |
|---|---|---|
| `AZURE_SUBSCRIPTION_ID` | `string` | ID de la suscripción Azure a monitorizar |
| `COST_MONITOR_CONFIG` | `JSON array` | Lista de Resource Groups con sus umbrales |
| `WEBHOOK_ENDPOINTS` | `JSON array` | Lista de endpoints de Pandora FMS |

### Variables opcionales

| Variable | Tipo | Default | Descripción |
|---|---|---|---|
| `ALERT_COOLDOWN_MINUTES` | `integer` | `60` | Minutos mínimos entre alertas para el mismo RG |
| `AzureWebJobsStorage` | `string` | `UseDevelopmentStorage=true` | Connection string de Storage Account (para Table Storage y el propio runtime) |

### Variables para desarrollo local (Service Principal)

| Variable | Descripción |
|---|---|
| `AZURE_TENANT_ID` | ID del tenant de Azure AD |
| `AZURE_CLIENT_ID` | ID de la App Registration |
| `AZURE_CLIENT_SECRET` | Secreto de la App Registration |

### Formato de `COST_MONITOR_CONFIG`

```json
[
  {
    "resource_group": "rg-produccion",
    "threshold": 50.0,
    "currency": "EUR"
  },
  {
    "resource_group": "rg-staging",
    "threshold": 20.0,
    "currency": "EUR"
  },
  {
    "resource_group": "rg-dev",
    "threshold": 10.0,
    "currency": "EUR"
  }
]
```

| Campo | Tipo | Obligatorio | Descripción |
|---|---|---|---|
| `resource_group` | `string` | Sí | Nombre del Resource Group a monitorizar |
| `threshold` | `float` | No (default: 0.0) | Coste diario máximo permitido |
| `currency` | `string` | No (default: EUR) | Moneda para referencia en el payload de alerta |

### Formato de `WEBHOOK_ENDPOINTS`

```json
[
  {
    "url": "https://pandora.miempresa.com/api/webhook/cost-alert",
    "method": "POST",
    "headers": {
      "Authorization": "Bearer mi-token-secreto",
      "X-Custom-Header": "valor"
    }
  },
  {
    "url": "https://pandora.miempresa.com/api/alert?type=cost",
    "method": "GET"
  }
]
```

| Campo | Tipo | Obligatorio | Descripción |
|---|---|---|---|
| `url` | `string` | Sí | URL del endpoint webhook |
| `method` | `string` | No (default: POST) | Método HTTP: `POST` o `GET` |
| `headers` | `object` | No | Headers HTTP adicionales (ej: Authorization) |

---

## Autenticación

La función usa `DefaultAzureCredential` del SDK `azure-identity`, que intenta autenticarse en este orden:

1. **Variables de entorno** (`AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`) — usado en desarrollo local.
2. **Managed Identity** — usado automáticamente cuando la Function App tiene una identidad asignada en Azure.
3. **Azure CLI** — si hay una sesión `az login` activa (útil para debugging local).
4. **Visual Studio Code** — si hay una sesión autenticada en VS Code.

### Permisos requeridos

La identidad (Service Principal o Managed Identity) necesita el rol:

- **`Cost Management Reader`** en el scope de la **suscripción**.

Comando para asignar el rol a la Managed Identity de la Function App:

```bash
az role assignment create \
  --assignee <function-app-managed-identity-object-id> \
  --role "Cost Management Reader" \
  --scope /subscriptions/<subscription-id>
```

---

## Control de alertas duplicadas (Cooldown)

Como la función se ejecuta cada 5 minutos, sin control de cooldown enviaría una alerta cada 5 minutos mientras el coste supere el umbral. El módulo `alert_tracker.py` resuelve esto:

- Usa **Azure Table Storage** (tabla `CostAlertTracker`) para persistir el timestamp de la última alerta por Resource Group y día.
- Antes de enviar una alerta, verifica si han pasado al menos `ALERT_COOLDOWN_MINUTES` desde la última.
- Si el cooldown no ha expirado, la alerta se omite y se registra en los logs.
- Si el Table Storage no está disponible, la política es "fail open": la alerta se envía igualmente.

### Estructura de la tabla

| PartitionKey | RowKey | ResourceGroup | LastAlertTime | CurrentCost | Threshold |
|---|---|---|---|---|---|
| 2026-02-20 | rg-produccion | rg-produccion | 2026-02-20T14:30:00 | 55.2341 | 50.0 |

---

## Payload de alerta

El JSON enviado a los webhooks contiene:

```json
{
  "alert_type": "azure_cost_threshold_exceeded",
  "resource_group": "rg-produccion",
  "current_cost": 55.2341,
  "threshold": 50.0,
  "currency": "EUR",
  "subscription_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "exceeded_by": 5.2341,
  "exceeded_by_percent": 10.47,
  "timestamp": "2026-02-20T14:35:22.123456Z",
  "date": "2026-02-20"
}
```

| Campo | Descripción |
|---|---|
| `alert_type` | Tipo de alerta (siempre `azure_cost_threshold_exceeded`) |
| `resource_group` | Nombre del Resource Group que superó el umbral |
| `current_cost` | Coste acumulado del día actual |
| `threshold` | Umbral configurado |
| `currency` | Moneda configurada |
| `subscription_id` | ID de la suscripción Azure |
| `exceeded_by` | Diferencia absoluta (coste - umbral) |
| `exceeded_by_percent` | Porcentaje de exceso sobre el umbral |
| `timestamp` | Momento exacto de la alerta (ISO 8601 UTC) |
| `date` | Fecha del día evaluado |

Para endpoints con **método GET**, estos campos se envían como query parameters en la URL.

---

## Tests

El proyecto incluye tests unitarios con **pytest** en la carpeta `tests/`. Todos los módulos están mockeados para no requerir conexión a Azure.

### Ejecutar tests

```bash
# Instalar dependencias (producción + dev)
uv sync

# Ejecutar todos los tests
uv run pytest tests/ -v

# Ejecutar con cobertura
uv run pytest tests/ -v --cov=modules --cov=function_app --cov-report=term-missing

# Ejecutar solo un módulo
uv run pytest tests/test_cost_client.py -v
```

### Estructura de tests

| Fichero | Qué testea |
|---|---|
| `tests/conftest.py` | Fixtures compartidas: configuraciones de ejemplo, limpieza de env vars |
| `tests/test_cost_client.py` | Consultas a Cost Management API (respuestas mockeadas) |
| `tests/test_webhook_notifier.py` | Envío de alertas HTTP, `build_alert_data`, manejo de errores |
| `tests/test_alert_tracker.py` | Lógica de cooldown y registro en Table Storage |
| `tests/test_function_app.py` | Orquestación: `_load_json_env`, flujos de `timer_trigger` |

---

## Despliegue en Azure

### Opción 1: Script automatizado (recomendado)

El script `deployment/deploy.ps1` automatiza toda la creación de infraestructura:

1. Crea el Resource Group.
2. Crea la Storage Account.
3. Crea la Function App (Consumption plan, Linux, Python 3.11).
4. Habilita Managed Identity.
5. Asigna el rol "Cost Management Reader" a la Managed Identity.
6. Crea un Service Principal para desarrollo local / CI/CD.
7. Configura las App Settings.
8. Despliega el código.

```powershell
cd deployment

.\deploy.ps1 `
    -SubscriptionId "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" `
    -ResourceGroupName "rg-cost-control" `
    -FunctionAppName "func-daily-cost-control" `
    -StorageAccountName "stcostcontrol001" `
    -CostMonitorConfig '[{"resource_group":"rg-produccion","threshold":50.0,"currency":"EUR"}]' `
    -WebhookEndpoints '[{"url":"https://pandora.example.com/webhook","method":"POST","headers":{"Authorization":"Bearer xxx"}}]'
```

#### Parámetros del script

| Parámetro | Obligatorio | Default | Descripción |
|---|---|---|---|
| `SubscriptionId` | Sí | — | Suscripción donde desplegar la infraestructura |
| `ResourceGroupName` | Sí | — | Nombre del Resource Group para la Function App |
| `Location` | No | `westeurope` | Región de Azure |
| `FunctionAppName` | Sí | — | Nombre de la Function App (único globalmente) |
| `StorageAccountName` | Sí | — | Nombre de la Storage Account (3-24 chars, minúsculas/números) |
| `ServicePrincipalName` | No | `sp-fdailyCostControl-local` | Nombre del Service Principal |
| `CostMonitorConfig` | No | — | JSON con los RGs a monitorizar |
| `WebhookEndpoints` | No | — | JSON con los endpoints de Pandora FMS |
| `AlertCooldownMinutes` | No | `60` | Cooldown entre alertas |
| `MonitoredSubscriptionId` | No | = SubscriptionId | Si la suscripción a monitorizar es diferente |

### Opción 2: Despliegue manual

```bash
# 1. Desplegar la Function App
func azure functionapp publish <nombre-function-app>

# 2. Configurar las variables de entorno (App Settings)
az functionapp config appsettings set \
  --name <nombre-function-app> \
  --resource-group <rg-de-la-function> \
  --settings \
    AZURE_SUBSCRIPTION_ID="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" \
    COST_MONITOR_CONFIG='[{"resource_group":"rg-produccion","threshold":50.0,"currency":"EUR"}]' \
    WEBHOOK_ENDPOINTS='[{"url":"https://pandora.example.com/webhook","method":"POST","headers":{"Authorization":"Bearer xxx"}}]' \
    ALERT_COOLDOWN_MINUTES="60"

# 3. Asignar rol de Cost Management Reader a la Managed Identity
IDENTITY_ID=$(az functionapp identity show --name <nombre-function-app> --resource-group <rg> --query principalId -o tsv)
az role assignment create \
  --assignee $IDENTITY_ID \
  --role "Cost Management Reader" \
  --scope /subscriptions/<subscription-id>
```

### Verificación

- Revisar los logs en **Application Insights** o con `func azure functionapp logstream <nombre>`.
- Para forzar una alerta de prueba, configurar un `threshold` muy bajo (ej: `0.01`).

---

## Desarrollo local

### Requisitos

1. **Python 3.11+**
2. **[uv](https://docs.astral.sh/uv/)** — gestor de paquetes y entornos virtuales.
3. **Azure Functions Core Tools** (`func`)
4. **Azurite** (emulador de Storage local) — necesario para Table Storage y el runtime.
5. Un **Service Principal** con permisos de Cost Management Reader (creado por `deploy.ps1` o manualmente), o bien una sesión `az login` activa.

### Pasos

```bash
# 1. Instalar uv (si no está instalado)
#    Windows:  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
#    macOS/Linux:  curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Crear el entorno virtual e instalar dependencias (producción + dev)
uv sync

# 3. Activar el entorno virtual
#    Windows (PowerShell):  .venv\Scripts\Activate.ps1
#    Windows (CMD):         .venv\Scripts\activate.bat
#    macOS/Linux:           source .venv/bin/activate

# 4. Configurar local.settings.json con los valores reales
#    (o copiar .env.example y renombrar)

# 5. Iniciar Azurite (en otra terminal)
azurite --silent

# 6. Ejecutar tests
uv run pytest tests/ -v

# 7. Ejecutar la function localmente
func start
```

### Gestión de dependencias con uv

Las dependencias se definen en `pyproject.toml`. El fichero `requirements.txt` se genera automáticamente para el despliegue en Azure Functions (que todavía lo requiere para el remote build).

```bash
# Añadir una dependencia de producción
uv add <paquete>

# Añadir una dependencia de desarrollo
uv add --group dev <paquete>

# Actualizar todas las dependencias
uv lock --upgrade
uv sync

# Regenerar requirements.txt antes de desplegar
uv export --no-hashes --no-dev --no-emit-project -o requirements.txt
```

### Tip: forzar ejecución inmediata

Cambiar `run_on_startup=True` temporalmente en `function_app.py` para que la función se ejecute al arrancar sin esperar al timer.

---

## Troubleshooting

| Problema | Causa posible | Solución |
|---|---|---|
| `AZURE_SUBSCRIPTION_ID is not configured` | Variable de entorno no definida | Configurar en `local.settings.json` o App Settings |
| `Invalid JSON in 'COST_MONITOR_CONFIG'` | JSON malformado en la variable | Validar el JSON (comillas dobles, escapado correcto) |
| `Error querying cost for RG` | Sin permisos o RG inexistente | Verificar rol Cost Management Reader y nombre del RG |
| `All webhook endpoints failed` | URLs incorrectas o Pandora no accesible | Verificar URLs y conectividad de red |
| `Alert skipped: last alert was X min ago` | Cooldown activo (comportamiento esperado) | Reducir `ALERT_COOLDOWN_MINUTES` o esperar |
| `Could not create table 'CostAlertTracker'` | Azurite no está corriendo (local) o Storage no accesible | Iniciar Azurite o verificar `AzureWebJobsStorage` |
| `Failed to initialize Azure credential` | Credenciales no configuradas | Verificar `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` (local) o Managed Identity (Azure) |
