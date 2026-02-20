<#
.SYNOPSIS
    Despliega la infraestructura de fdailyCostControl en Azure usando Bicep (Flex Consumption).

.DESCRIPTION
    Este script realiza las siguientes operaciones usando Azure CLI + Bicep:
    1. Crea un Resource Group.
    2. Despliega infraestructura mediante el template Bicep (main.bicep):
       - Storage Account (con allowSharedKeyAccess=false, compatible con Azure Policy).
       - App Service Plan (Flex Consumption FC1).
       - Function App con System-Assigned Managed Identity.
       - Role Assignments sobre el Storage (Blob Owner, Contributor, Queue, Table).
    3. Asigna el rol "Cost Management Reader" a la Managed Identity sobre la suscripcion.
    4. (Opcional) Crea un Service Principal para desarrollo local.
    5. Configura las App Settings (COST_MONITOR_CONFIG, WEBHOOK_ENDPOINTS, etc.).
    6. Despliega el codigo de la Function App.

    NOTA: Usa Flex Consumption (FC1) en lugar de Consumption (Y1) para evitar
    problemas con Azure Policy que fuerza allowSharedKeyAccess=false en Storage Accounts.
    Flex Consumption usa Blob Storage (identity-based) en vez de File Shares (shared key).

.PARAMETER SubscriptionId
    ID de la suscripcion Azure donde desplegar.

.PARAMETER ResourceGroupName
    Nombre del Resource Group donde crear la infraestructura.

.PARAMETER Location
    Region de Azure (default: westeurope).

.PARAMETER FunctionAppName
    Nombre de la Function App (debe ser unico globalmente).

.PARAMETER StorageAccountName
    Nombre de la Storage Account (unico globalmente, 3-24 chars, solo minusculas y numeros).

.PARAMETER ServicePrincipalName
    Nombre del Service Principal para desarrollo local / CI/CD (opcional).

.PARAMETER SkipServicePrincipal
    Si se especifica, no se intenta crear el Service Principal.

.PARAMETER CostMonitorConfig
    JSON string con la configuracion de monitorizacion de costes.

.PARAMETER WebhookEndpoints
    JSON string con los endpoints de Pandora FMS.

.PARAMETER AlertCooldownMinutes
    Minutos de cooldown entre alertas (default: 60).

.PARAMETER MonitoredSubscriptionId
    ID de la suscripcion a monitorizar. Si no se especifica, se usa SubscriptionId.

.EXAMPLE
    .\deploy.ps1 `
        -SubscriptionId "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" `
        -ResourceGroupName "rg-cost-control" `
        -FunctionAppName "func-daily-cost-control" `
        -StorageAccountName "stcostcontrol001" `
        -CostMonitorConfig '[{"resource_group":"rg-produccion","daily_threshold":50.0}]' `
        -WebhookEndpoints '[{"url":"https://pandora.example.com/webhook","method":"POST"}]'
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SubscriptionId,

    [Parameter(Mandatory = $true)]
    [string]$ResourceGroupName,

    [Parameter(Mandatory = $false)]
    [string]$Location = "westeurope",

    [Parameter(Mandatory = $true)]
    [string]$FunctionAppName,

    [Parameter(Mandatory = $true)]
    [string]$StorageAccountName,

    [Parameter(Mandatory = $false)]
    [string]$ServicePrincipalName = "sp-fdailyCostControl-local",

    [Parameter(Mandatory = $false)]
    [switch]$SkipServicePrincipal,

    [Parameter(Mandatory = $false)]
    [string]$CostMonitorConfig = "",

    [Parameter(Mandatory = $false)]
    [string]$WebhookEndpoints = "",

    [Parameter(Mandatory = $false)]
    [int]$AlertCooldownMinutes = 60,

    [Parameter(Mandatory = $false)]
    [string]$MonitoredSubscriptionId = ""
)

# --- Strict error handling ---
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# --- Helper ---
function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "  $Message" -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan
}

function Write-Success {
    param([string]$Message)
    Write-Host "  [OK] $Message" -ForegroundColor Green
}

function Write-Info {
    param([string]$Message)
    Write-Host "  [..] $Message" -ForegroundColor Yellow
}

# --- Derived values ---
if ([string]::IsNullOrEmpty($MonitoredSubscriptionId)) {
    $MonitoredSubscriptionId = $SubscriptionId
}

$bicepTemplatePath = Join-Path $PSScriptRoot "main.bicep"

# =============================================================================
# PRE-FLIGHT CHECKS
# =============================================================================
Write-Step "Pre-flight checks"

# Verify az CLI is installed
if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    Write-Error "Azure CLI (az) is not installed. Install from https://aka.ms/installazurecli"
}
Write-Success "Azure CLI found"

# Verify func CLI is installed
if (-not (Get-Command func -ErrorAction SilentlyContinue)) {
    Write-Error "Azure Functions Core Tools (func) is not installed. Install from https://aka.ms/azfunc-install"
}
Write-Success "Azure Functions Core Tools found"

# Verify Bicep template exists
if (-not (Test-Path $bicepTemplatePath)) {
    Write-Error "Bicep template not found at '$bicepTemplatePath'. Ensure main.bicep is in the deployment/ folder."
}
Write-Success "Bicep template found: $bicepTemplatePath"

# Verify logged in
$account = az account show --output json 2>$null | ConvertFrom-Json
if (-not $account) {
    Write-Error "Not logged in to Azure. Run 'az login' first."
}
Write-Success "Logged in as: $($account.user.name)"

# Set subscription
Write-Info "Setting subscription to $SubscriptionId..."
az account set --subscription $SubscriptionId
Write-Success "Subscription set"

# =============================================================================
# STEP 1: Create Resource Group
# =============================================================================
Write-Step "Step 1: Creating Resource Group '$ResourceGroupName'"

$rgExists = az group exists --name $ResourceGroupName
if ($rgExists -eq "true") {
    Write-Info "Resource Group already exists, skipping creation."
} else {
    az group create `
        --name $ResourceGroupName `
        --location $Location `
        --output none
    Write-Success "Resource Group created in $Location"
}

# =============================================================================
# STEP 2: Deploy Infrastructure via Bicep
# =============================================================================
Write-Step "Step 2: Deploying infrastructure via Bicep (Storage + FC1 Plan + Function App)"

Write-Info "This deploys: Storage Account, Flex Consumption Plan (FC1), Function App,"
Write-Info "System-Assigned Managed Identity, and Storage Role Assignments."
Write-Info "Flex Consumption uses blob-based deployment (no shared key required)."

$deployOutput = az deployment group create `
    --resource-group $ResourceGroupName `
    --template-file $bicepTemplatePath `
    --parameters functionAppName=$FunctionAppName storageAccountName=$StorageAccountName location=$Location `
    --query "properties.outputs" `
    --output json | ConvertFrom-Json

$managedIdentityPrincipalId = $deployOutput.functionAppPrincipalId.value
$functionHostName = $deployOutput.functionAppDefaultHostName.value

Write-Success "Bicep deployment completed successfully"
Write-Success "Function App hostname: $functionHostName"
Write-Success "Managed Identity Principal: $managedIdentityPrincipalId"

# =============================================================================
# STEP 3: Assign "Cost Management Reader" role to Managed Identity
# =============================================================================
Write-Step "Step 3: Assigning 'Cost Management Reader' role to Managed Identity"

$roleScope = "/subscriptions/$MonitoredSubscriptionId"

# Check if role is already assigned
$existingRole = az role assignment list `
    --assignee $managedIdentityPrincipalId `
    --role "Cost Management Reader" `
    --scope $roleScope `
    --output json | ConvertFrom-Json

if ($existingRole -and $existingRole.Count -gt 0) {
    Write-Info "Role 'Cost Management Reader' already assigned, skipping."
} else {
    az role assignment create `
        --assignee-object-id $managedIdentityPrincipalId `
        --assignee-principal-type ServicePrincipal `
        --role "Cost Management Reader" `
        --scope $roleScope `
        --output none
    Write-Success "Role 'Cost Management Reader' assigned on subscription $MonitoredSubscriptionId"
}

# =============================================================================
# STEP 4: (Optional) Create Service Principal for local development / CI/CD
# =============================================================================
$spAppId = "N/A"

if ($SkipServicePrincipal) {
    Write-Step "Step 4: Service Principal creation SKIPPED"
    Write-Info "Use 'az login' with your user account for local development."
} else {
    Write-Step "Step 4: Creating Service Principal '$ServicePrincipalName'"
    Write-Info "Creating Service Principal with 'Cost Management Reader' role..."

    try {
        $spOutput = az ad sp create-for-rbac `
            --name $ServicePrincipalName `
            --role "Cost Management Reader" `
            --scopes $roleScope `
            --output json | ConvertFrom-Json

        $spAppId = $spOutput.appId
        $spPassword = $spOutput.password
        $spTenant = $spOutput.tenant

        Write-Success "Service Principal created"
        Write-Host ""
        Write-Host "  +--------------------------------------------------------------+" -ForegroundColor Magenta
        Write-Host "  |  SERVICE PRINCIPAL CREDENTIALS (save securely!)               |" -ForegroundColor Magenta
        Write-Host "  +--------------------------------------------------------------+" -ForegroundColor Magenta
        Write-Host "  |  AZURE_TENANT_ID     = $spTenant" -ForegroundColor Magenta
        Write-Host "  |  AZURE_CLIENT_ID     = $spAppId" -ForegroundColor Magenta
        Write-Host "  |  AZURE_CLIENT_SECRET = $spPassword" -ForegroundColor Magenta
        Write-Host "  +--------------------------------------------------------------+" -ForegroundColor Magenta
        Write-Host ""
        Write-Host "  WARNING: These credentials will NOT be shown again." -ForegroundColor Red
        Write-Host "           Store them in local.settings.json or a secure vault." -ForegroundColor Red
    } catch {
        Write-Host "  [WARN] Could not create Service Principal (insufficient permissions)." -ForegroundColor DarkYellow
        Write-Host "         Use 'az login' for local development instead." -ForegroundColor DarkYellow
    }
}

# =============================================================================
# STEP 5: Configure App Settings
# =============================================================================
Write-Step "Step 5: Configuring App Settings"

# Basic settings
az functionapp config appsettings set `
    --name $FunctionAppName `
    --resource-group $ResourceGroupName `
    --settings "MONITORED_SUBSCRIPTION_ID=$MonitoredSubscriptionId" "ALERT_COOLDOWN_MINUTES=$AlertCooldownMinutes" "AzureWebJobsStorage__accountName=$StorageAccountName" `
    --output none

Write-Success "Basic settings configured (MONITORED_SUBSCRIPTION_ID, ALERT_COOLDOWN_MINUTES)"

# JSON settings must be set with escaped quotes for PowerShell
if (-not [string]::IsNullOrEmpty($CostMonitorConfig)) {
    az functionapp config appsettings set `
        --name $FunctionAppName `
        --resource-group $ResourceGroupName `
        --settings "COST_MONITOR_CONFIG=$CostMonitorConfig" `
        --output none
    Write-Success "COST_MONITOR_CONFIG configured"
} else {
    Write-Info "COST_MONITOR_CONFIG not provided - configure it in Azure Portal."
}

if (-not [string]::IsNullOrEmpty($WebhookEndpoints)) {
    az functionapp config appsettings set `
        --name $FunctionAppName `
        --resource-group $ResourceGroupName `
        --settings "WEBHOOK_ENDPOINTS=$WebhookEndpoints" `
        --output none
    Write-Success "WEBHOOK_ENDPOINTS configured"
} else {
    Write-Info "WEBHOOK_ENDPOINTS not provided - configure it in Azure Portal."
}

# =============================================================================
# STEP 6: Deploy Function App code
# =============================================================================
Write-Step "Step 6: Deploying Function App code"

# Navigate to project root (one level up from deployment/)
$projectRoot = Split-Path -Parent $PSScriptRoot

if (-not (Test-Path (Join-Path $projectRoot "function_app.py"))) {
    Write-Error "Could not find function_app.py in '$projectRoot'. Run this script from the deployment/ folder."
}

Write-Info "Publishing from: $projectRoot"

Push-Location $projectRoot
try {
    func azure functionapp publish $FunctionAppName --python
    Write-Success "Function App deployed successfully"
} finally {
    Pop-Location
}

# =============================================================================
# SUMMARY
# =============================================================================
Write-Step "Deployment Complete!"

Write-Host ""
Write-Host "  Resource Group:      $ResourceGroupName" -ForegroundColor White
Write-Host "  Storage Account:     $StorageAccountName (allowSharedKeyAccess=false)" -ForegroundColor White
Write-Host "  Function App:        $FunctionAppName" -ForegroundColor White
Write-Host "  Hosting Plan:        Flex Consumption (FC1)" -ForegroundColor White
Write-Host "  URL:                 https://$functionHostName" -ForegroundColor White
Write-Host "  Managed Identity:    $managedIdentityPrincipalId" -ForegroundColor White
Write-Host "  Service Principal:   $spAppId" -ForegroundColor White
Write-Host "  Monitored Sub:       $MonitoredSubscriptionId" -ForegroundColor White
Write-Host "  Cooldown:            $AlertCooldownMinutes min" -ForegroundColor White
Write-Host ""
Write-Host "  Next steps:" -ForegroundColor Yellow
Write-Host "    1. If not set, configure COST_MONITOR_CONFIG and WEBHOOK_ENDPOINTS" -ForegroundColor Yellow
Write-Host "       in Azure Portal > Function App > Configuration." -ForegroundColor Yellow
Write-Host "    2. For local dev, run 'az login' or set SP credentials in local.settings.json." -ForegroundColor Yellow
Write-Host "    3. Monitor logs: func azure functionapp logstream $FunctionAppName" -ForegroundColor Yellow
Write-Host ""
