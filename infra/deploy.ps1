[CmdletBinding()]
param(
    [string]$Location = "italynorth",
    [string]$Prefix = "lastwrites",
    [string]$ResourceGroupName = "",
    [string]$GithubRepo = "",
    [switch]$SetGithubSecrets,
    [switch]$RerunWorkflowRuns,
    [switch]$SkipLocalFileUpdate,
    [switch]$CleanupOnFailure
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Write-Step {
    param([string]$Message)
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Ensure-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found in PATH."
    }
}

function Invoke-AzCli {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    # Force Azure CLI to suppress warnings and avoid PowerShell treating stderr warnings as terminating failures.
    $effectiveArguments = @($Arguments + @("--only-show-errors"))
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = & az @effectiveArguments 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    if ($exitCode -ne 0) {
        $renderedOutput = ($output | Out-String).Trim()
        throw "Azure CLI command failed (exit $exitCode): az $($effectiveArguments -join ' ')`n$renderedOutput"
    }

    return $output
}

function Get-AzCliRaw {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $output = Invoke-AzCli -Arguments $Arguments
    return ($output | Out-String).Trim()
}

function Get-AzCliTsv {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    return (Get-AzCliRaw -Arguments $Arguments).Trim()
}

function Get-AzCliJson {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $raw = Get-AzCliRaw -Arguments $Arguments
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return $null
    }

    return ($raw | ConvertFrom-Json)
}

function Invoke-GhCli {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $output = & gh @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        $renderedOutput = ($output | Out-String).Trim()
        throw "GitHub CLI command failed (exit $exitCode): gh $($Arguments -join ' ')`n$renderedOutput"
    }

    return $output
}

function Get-GhRaw {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $output = Invoke-GhCli -Arguments $Arguments
    return ($output | Out-String).Trim()
}

function New-RandomToken {
    param([int]$Length = 6)

    $chars = "abcdefghijklmnopqrstuvwxyz0123456789".ToCharArray()
    -join (1..$Length | ForEach-Object { $chars[(Get-Random -Minimum 0 -Maximum $chars.Length)] })
}

function Normalize-CompactName {
    param([string]$Value)

    $normalized = ($Value.ToLower() -replace "[^a-z0-9]", "")
    if ([string]::IsNullOrWhiteSpace($normalized)) {
        return "lw"
    }
    return $normalized
}

function Normalize-DashedName {
    param([string]$Value)

    $normalized = ($Value.ToLower() -replace "[^a-z0-9-]", "-")
    $normalized = ($normalized -replace "-+", "-").Trim("-")
    if ([string]::IsNullOrWhiteSpace($normalized)) {
        return "lw"
    }
    return $normalized
}

function Set-OrUpdateGithubSecret {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Value,
        [Parameter(Mandatory = $true)][string]$Repo
    )

    $output = $Value | & gh secret set $Name --repo $Repo 2>&1
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        $renderedOutput = ($output | Out-String).Trim()
        throw "GitHub CLI secret update failed (exit $exitCode): gh secret set $Name --repo $Repo`n$renderedOutput"
    }
}

function Ensure-ProviderRegistered {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Namespace
    )

    $currentState = Get-AzCliTsv -Arguments @("provider", "show", "--namespace", $Namespace, "--query", "registrationState", "-o", "tsv")
    if ($currentState -eq "Registered") {
        return
    }

    Write-Host "Registering Azure provider '$Namespace' (current state: $currentState)" -ForegroundColor Yellow
    Invoke-AzCli -Arguments @("provider", "register", "--namespace", $Namespace, "--wait", "-o", "none") | Out-Null

    $finalState = Get-AzCliTsv -Arguments @("provider", "show", "--namespace", $Namespace, "--query", "registrationState", "-o", "tsv")
    if ($finalState -ne "Registered") {
        throw "Provider '$Namespace' registration state is '$finalState' after registration attempt."
    }
}

function Get-PolicyAllowedLocations {
    try {
        $assignments = Get-AzCliJson -Arguments @("policy", "assignment", "list", "--disable-scope-strict-match", "-o", "json")
    }
    catch {
        Write-Warning "Could not read policy assignments to detect allowed locations. Using requested location as-is."
        return @()
    }

    if ($null -eq $assignments) {
        return @()
    }

    if ($assignments -isnot [System.Array]) {
        $assignments = @($assignments)
    }

    $collected = @()
    foreach ($assignment in $assignments) {
        $parameters = $assignment.properties.parameters
        if ($null -eq $parameters) {
            continue
        }

        foreach ($parameterName in @("listOfAllowedLocations", "allowedLocations")) {
            if ($parameters.PSObject.Properties.Name -contains $parameterName) {
                $value = $parameters.$parameterName.value
                if ($value -is [System.Array]) {
                    $collected += $value
                }
                elseif ($null -ne $value) {
                    $collected += [string]$value
                }
            }
        }
    }

    return @(
        $collected |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            ForEach-Object { $_.ToLower() } |
            Sort-Object -Unique
    )
}

function Resolve-DeploymentLocation {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RequestedLocation
    )

    $requestedNormalized = $RequestedLocation.ToLower()
    $allowedLocations = Get-PolicyAllowedLocations

    if ($allowedLocations.Count -eq 0) {
        return $requestedNormalized
    }

    if ($allowedLocations -contains $requestedNormalized) {
        return $requestedNormalized
    }

    $fallback = $allowedLocations[0]
    Write-Warning "Location '$RequestedLocation' is not in policy-allowed locations. Falling back to '$fallback'."
    return $fallback
}

Ensure-Command -Name "az"
$account = Get-AzCliJson -Arguments @("account", "show", "-o", "json")
$subscriptionId = [string]$account.id

Write-Step "Ensuring required Azure resource providers are registered"
foreach ($namespace in @(
    "Microsoft.Web",
    "Microsoft.Storage",
    "Microsoft.DocumentDB",
    "Microsoft.EventGrid",
    "Microsoft.KeyVault",
    "Microsoft.ContainerRegistry"
)) {
    Ensure-ProviderRegistered -Namespace $namespace
}

if ($SetGithubSecrets -or $RerunWorkflowRuns) {
    Ensure-Command -Name "gh"
}

if (($SetGithubSecrets -or $RerunWorkflowRuns) -and [string]::IsNullOrWhiteSpace($GithubRepo)) {
    throw "-GithubRepo is required when using -SetGithubSecrets or -RerunWorkflowRuns. Example: owner/repo"
}

$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$backendEnvPath = Join-Path $projectRoot "backend/.env"
$frontendEnvPath = Join-Path $projectRoot "frontend/.env.local"
$functionsSettingsPath = Join-Path $projectRoot "functions/local.settings.json"

$prefixCompact = Normalize-CompactName -Value $Prefix
$prefixDashed = Normalize-DashedName -Value $Prefix

$resourceGroupPrefix = "rg-$prefixDashed-"
if ([string]::IsNullOrWhiteSpace($ResourceGroupName)) {
    $matchingGroups = Get-AzCliJson -Arguments @("group", "list", "--query", "[?starts_with(name, '$resourceGroupPrefix')].name", "-o", "json")
    if ($null -eq $matchingGroups) {
        $matchingGroups = @()
    }
    elseif ($matchingGroups -isnot [System.Array]) {
        $matchingGroups = @($matchingGroups)
    }

    $matchingGroups = @(
        $matchingGroups |
            Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) } |
            Sort-Object -Unique
    )

    if ($matchingGroups.Count -eq 1) {
        $ResourceGroupName = [string]$matchingGroups[0]
        Write-Warning "No -ResourceGroupName provided. Reusing existing resource group '$ResourceGroupName'."
    }
    else {
        if ($matchingGroups.Count -gt 1) {
            Write-Warning "Multiple matching resource groups were found. Creating a new one. Use -ResourceGroupName to explicitly reuse an existing group."
        }
        $ResourceGroupName = "$resourceGroupPrefix$(New-RandomToken -Length 6)"
    }
}

$resourceGroupPattern = "^rg-$([regex]::Escape($prefixDashed))-(?<suffix>[a-z0-9]+)$"
$token = ""
if ($ResourceGroupName.ToLower() -match $resourceGroupPattern) {
    $token = [string]$Matches["suffix"]
}

if ([string]::IsNullOrWhiteSpace($token)) {
    $token = New-RandomToken -Length 6
}

if ($token.Length -lt 6) {
    $token = $token.PadRight(6, '0')
}
elseif ($token.Length -gt 12) {
    $token = $token.Substring(0, 12)
}

$Location = Resolve-DeploymentLocation -RequestedLocation $Location

$planName = "plan-$prefixDashed-$token"
$backendAppName = "api-$prefixDashed-$token"
$frontendAppName = "web-$prefixDashed-$token"
$functionAppName = "func-$prefixDashed-$token"
$cosmosAccountName = "cdb-$prefixDashed-$token"
$cosmosDatabaseName = "last-writes-db"
$cosmosContainerName = "vaults"
$eventGridTopicName = "eg-$prefixDashed-$token"
$keyVaultName = "kv-$prefixDashed-$token"
$pythonRuntime = "PYTHON:3.11"
$nodeRuntime = "NODE:20-lts"

$storageAccountName = ("st" + $prefixCompact + $token)
if ($storageAccountName.Length -gt 24) { $storageAccountName = $storageAccountName.Substring(0, 24) }
$storageAccountName = $storageAccountName.ToLower()

$acrName = ("acr" + $prefixCompact + $token)
if ($acrName.Length -gt 50) { $acrName = $acrName.Substring(0, 50) }
$acrName = $acrName.ToLower()

$blobConnectionString = ""
$cosmosConnectionString = ""
$eventGridEndpoint = ""
$eventGridKey = ""
$keyVaultUrl = ""
$acrLoginServer = ""
$acrUsername = ""
$acrPassword = ""
$backendUrl = ""
$frontendUrl = ""
$canRerunFunctionsWorkflow = $true

$deploymentStarted = $false

try {
    Write-Step "Creating resource group '$ResourceGroupName' in '$Location'"
    Invoke-AzCli -Arguments @("group", "create", "--name", $ResourceGroupName, "--location", $Location, "-o", "none") | Out-Null
    $deploymentStarted = $true

    Write-Step "Creating App Service plan and web apps"
    Invoke-AzCli -Arguments @("appservice", "plan", "create", "--name", $planName, "--resource-group", $ResourceGroupName, "--is-linux", "--sku", "B1", "--location", $Location, "-o", "none") | Out-Null
    Invoke-AzCli -Arguments @("webapp", "create", "--name", $backendAppName, "--resource-group", $ResourceGroupName, "--plan", $planName, "--runtime", $pythonRuntime, "-o", "none") | Out-Null
    Invoke-AzCli -Arguments @("webapp", "create", "--name", $frontendAppName, "--resource-group", $ResourceGroupName, "--plan", $planName, "--runtime", $nodeRuntime, "-o", "none") | Out-Null

    Write-Step "Creating storage account for blobs and function host"
    Invoke-AzCli -Arguments @("storage", "account", "create", "--name", $storageAccountName, "--resource-group", $ResourceGroupName, "--location", $Location, "--sku", "Standard_LRS", "--kind", "StorageV2", "--allow-blob-public-access", "false", "--min-tls-version", "TLS1_2", "-o", "none") | Out-Null
    $blobConnectionString = Get-AzCliTsv -Arguments @("storage", "account", "show-connection-string", "--resource-group", $ResourceGroupName, "--name", $storageAccountName, "--query", "connectionString", "-o", "tsv")
    if ([string]::IsNullOrWhiteSpace($blobConnectionString)) {
        throw "Storage connection string retrieval returned empty output."
    }
    Invoke-AzCli -Arguments @("storage", "container", "create", "--name", "vaults", "--connection-string", $blobConnectionString, "--auth-mode", "key", "-o", "none") | Out-Null
    Invoke-AzCli -Arguments @("storage", "container", "create", "--name", "deliveries", "--connection-string", $blobConnectionString, "--auth-mode", "key", "-o", "none") | Out-Null

    Write-Step "Creating Function App (Flex Consumption)"
    $functionAppExists = $false
    try {
        $existingFunctionApp = Get-AzCliJson -Arguments @("functionapp", "show", "--name", $functionAppName, "--resource-group", $ResourceGroupName, "-o", "json")
        $functionAppExists = $null -ne $existingFunctionApp -and -not [string]::IsNullOrWhiteSpace([string]$existingFunctionApp.id)
    }
    catch {
        $functionAppExists = $false
    }

    if (-not $functionAppExists) {
        Invoke-AzCli -Arguments @("functionapp", "create", "--name", $functionAppName, "--resource-group", $ResourceGroupName, "--storage-account", $storageAccountName, "--flexconsumption-location", $Location, "--runtime", "python", "--runtime-version", "3.11", "--functions-version", "4", "-o", "none") | Out-Null
    }
    else {
        Write-Host "Function App '$functionAppName' already exists. Reusing existing app." -ForegroundColor Yellow
    }

    Write-Step "Enabling basic publishing credentials for SCM deployment"
    foreach ($siteName in @($backendAppName, $frontendAppName, $functionAppName)) {
        Invoke-AzCli -Arguments @("resource", "update", "--resource-group", $ResourceGroupName, "--namespace", "Microsoft.Web", "--resource-type", "basicPublishingCredentialsPolicies", "--parent", "sites/$siteName", "--name", "scm", "--set", "properties.allow=true", "-o", "none") | Out-Null
        Invoke-AzCli -Arguments @("resource", "update", "--resource-group", $ResourceGroupName, "--namespace", "Microsoft.Web", "--resource-type", "basicPublishingCredentialsPolicies", "--parent", "sites/$siteName", "--name", "ftp", "--set", "properties.allow=true", "-o", "none") | Out-Null
    }

    Write-Step "Creating Cosmos DB account, database, and container"
    Invoke-AzCli -Arguments @("cosmosdb", "create", "--name", $cosmosAccountName, "--resource-group", $ResourceGroupName, "--kind", "GlobalDocumentDB", "--locations", "regionName=$Location", "failoverPriority=0", "--default-consistency-level", "Session", "-o", "none") | Out-Null
    Invoke-AzCli -Arguments @("cosmosdb", "sql", "database", "create", "--account-name", $cosmosAccountName, "--resource-group", $ResourceGroupName, "--name", $cosmosDatabaseName, "-o", "none") | Out-Null
    Invoke-AzCli -Arguments @("cosmosdb", "sql", "container", "create", "--account-name", $cosmosAccountName, "--resource-group", $ResourceGroupName, "--database-name", $cosmosDatabaseName, "--name", $cosmosContainerName, "--partition-key-path", "/user_id", "--throughput", "400", "-o", "none") | Out-Null
    $cosmosConnectionString = Get-AzCliTsv -Arguments @("cosmosdb", "keys", "list", "--type", "connection-strings", "--name", $cosmosAccountName, "--resource-group", $ResourceGroupName, "--query", "connectionStrings[0].connectionString", "-o", "tsv")
    if ([string]::IsNullOrWhiteSpace($cosmosConnectionString)) {
        throw "Cosmos connection string retrieval returned empty output."
    }

    Write-Step "Creating Event Grid topic"
    Invoke-AzCli -Arguments @("eventgrid", "topic", "create", "--name", $eventGridTopicName, "--resource-group", $ResourceGroupName, "--location", $Location, "--input-schema", "eventgridschema", "-o", "none") | Out-Null
    $eventGridEndpoint = Get-AzCliTsv -Arguments @("eventgrid", "topic", "show", "--name", $eventGridTopicName, "--resource-group", $ResourceGroupName, "--query", "endpoint", "-o", "tsv")
    $eventGridKey = Get-AzCliTsv -Arguments @("eventgrid", "topic", "key", "list", "--name", $eventGridTopicName, "--resource-group", $ResourceGroupName, "--query", "key1", "-o", "tsv")

    Write-Step "Creating Key Vault and saving connection-string secrets"
    $keyVaultExists = $false
    try {
        $existingKeyVault = Get-AzCliJson -Arguments @("keyvault", "show", "--name", $keyVaultName, "--resource-group", $ResourceGroupName, "-o", "json")
        $keyVaultExists = $null -ne $existingKeyVault -and -not [string]::IsNullOrWhiteSpace([string]$existingKeyVault.id)
    }
    catch {
        $keyVaultExists = $false
    }

    if (-not $keyVaultExists) {
        Invoke-AzCli -Arguments @("keyvault", "create", "--name", $keyVaultName, "--resource-group", $ResourceGroupName, "--location", $Location, "--enable-rbac-authorization", "false", "-o", "none") | Out-Null
    }
    else {
        Write-Host "Key Vault '$keyVaultName' already exists. Reusing existing vault." -ForegroundColor Yellow
    }

    $keyVaultUrl = Get-AzCliTsv -Arguments @("keyvault", "show", "--name", $keyVaultName, "--resource-group", $ResourceGroupName, "--query", "properties.vaultUri", "-o", "tsv")
    Invoke-AzCli -Arguments @("keyvault", "secret", "set", "--vault-name", $keyVaultName, "--name", "COSMOS-CONNECTION-STRING", "--value", $cosmosConnectionString, "-o", "none") | Out-Null
    Invoke-AzCli -Arguments @("keyvault", "secret", "set", "--vault-name", $keyVaultName, "--name", "BLOB-CONNECTION-STRING", "--value", $blobConnectionString, "-o", "none") | Out-Null

    Write-Step "Enabling backend managed identity and granting Key Vault secret access"
    $backendPrincipalId = Get-AzCliTsv -Arguments @("webapp", "identity", "assign", "--name", $backendAppName, "--resource-group", $ResourceGroupName, "--query", "principalId", "-o", "tsv")
    if ([string]::IsNullOrWhiteSpace($backendPrincipalId)) {
        throw "Backend web app managed identity principalId was empty."
    }
    Invoke-AzCli -Arguments @("keyvault", "set-policy", "--name", $keyVaultName, "--object-id", $backendPrincipalId, "--secret-permissions", "get", "list", "-o", "none") | Out-Null

    Write-Step "Creating Azure Container Registry for worker image"
    Invoke-AzCli -Arguments @("acr", "create", "--name", $acrName, "--resource-group", $ResourceGroupName, "--location", $Location, "--sku", "Basic", "--admin-enabled", "true", "-o", "none") | Out-Null
    $acrLoginServer = Get-AzCliTsv -Arguments @("acr", "show", "--name", $acrName, "--resource-group", $ResourceGroupName, "--query", "loginServer", "-o", "tsv")
    $acrUsername = Get-AzCliTsv -Arguments @("acr", "credential", "show", "--name", $acrName, "--resource-group", $ResourceGroupName, "--query", "username", "-o", "tsv")
    $acrPassword = Get-AzCliTsv -Arguments @("acr", "credential", "show", "--name", $acrName, "--resource-group", $ResourceGroupName, "--query", "passwords[0].value", "-o", "tsv")

    $backendHost = Get-AzCliTsv -Arguments @("webapp", "show", "--name", $backendAppName, "--resource-group", $ResourceGroupName, "--query", "defaultHostName", "-o", "tsv")
    $frontendHost = Get-AzCliTsv -Arguments @("webapp", "show", "--name", $frontendAppName, "--resource-group", $ResourceGroupName, "--query", "defaultHostName", "-o", "tsv")
    $backendUrl = "https://$backendHost"
    $frontendUrl = "https://$frontendHost"

    Write-Step "Applying runtime settings to backend, frontend, and function apps"
    Invoke-AzCli -Arguments @("webapp", "config", "appsettings", "set", "--name", $backendAppName, "--resource-group", $ResourceGroupName, "--settings", "KEY_VAULT_URL=$keyVaultUrl", "COSMOS_DATABASE_NAME=$cosmosDatabaseName", "COSMOS_VAULTS_CONTAINER=$cosmosContainerName", "FRONTEND_ORIGINS=$frontendUrl", "COSMOS_CONNECTION_STRING_SECRET_NAME=COSMOS-CONNECTION-STRING", "BLOB_CONNECTION_STRING_SECRET_NAME=BLOB-CONNECTION-STRING", "-o", "none") | Out-Null
    Invoke-AzCli -Arguments @("webapp", "config", "appsettings", "set", "--name", $frontendAppName, "--resource-group", $ResourceGroupName, "--settings", "NEXT_PUBLIC_API_URL=$backendUrl", "NEXT_PUBLIC_DEFAULT_USER_ID=academic-demo-user", "-o", "none") | Out-Null
    Invoke-AzCli -Arguments @("webapp", "config", "set", "--name", $frontendAppName, "--resource-group", $ResourceGroupName, "--startup-file", "node server.js", "-o", "none") | Out-Null
    Invoke-AzCli -Arguments @("functionapp", "config", "appsettings", "set", "--name", $functionAppName, "--resource-group", $ResourceGroupName, "--settings", "COSMOS_CONNECTION_STRING=$cosmosConnectionString", "COSMOS_DATABASE_NAME=$cosmosDatabaseName", "COSMOS_VAULTS_CONTAINER=$cosmosContainerName", "EVENT_GRID_ENDPOINT=$eventGridEndpoint", "EVENT_GRID_KEY=$eventGridKey", "BLOB_CONNECTION_STRING=$blobConnectionString", "-o", "none") | Out-Null

    if (-not $SkipLocalFileUpdate) {
        Write-Step "Writing local development config files"

        $requiredValues = [ordered]@{
            COSMOS_CONNECTION_STRING = $cosmosConnectionString
            BLOB_CONNECTION_STRING = $blobConnectionString
            EVENT_GRID_ENDPOINT = $eventGridEndpoint
            EVENT_GRID_KEY = $eventGridKey
        }
        foreach ($entry in $requiredValues.GetEnumerator()) {
            if ([string]::IsNullOrWhiteSpace([string]$entry.Value)) {
                throw "Cannot write local config files because '$($entry.Key)' is empty."
            }
        }

        $backendEnvContent = @"
KEY_VAULT_URL=

COSMOS_CONNECTION_STRING=$cosmosConnectionString
COSMOS_DATABASE_NAME=$cosmosDatabaseName
COSMOS_VAULTS_CONTAINER=$cosmosContainerName

BLOB_CONNECTION_STRING=$blobConnectionString

FRONTEND_ORIGINS=http://localhost:3000
"@
        Set-Content -Path $backendEnvPath -Value $backendEnvContent -Encoding UTF8

        $frontendEnvContent = @"
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_DEFAULT_USER_ID=academic-demo-user
"@
        Set-Content -Path $frontendEnvPath -Value $frontendEnvContent -Encoding UTF8

        $functionsSettings = [ordered]@{
            IsEncrypted = $false
            Values = [ordered]@{
                AzureWebJobsStorage = $blobConnectionString
                FUNCTIONS_WORKER_RUNTIME = "python"
                COSMOS_CONNECTION_STRING = $cosmosConnectionString
                COSMOS_DATABASE_NAME = $cosmosDatabaseName
                COSMOS_VAULTS_CONTAINER = $cosmosContainerName
                EVENT_GRID_ENDPOINT = $eventGridEndpoint
                EVENT_GRID_KEY = $eventGridKey
                BLOB_CONNECTION_STRING = $blobConnectionString
            }
        }
        ($functionsSettings | ConvertTo-Json -Depth 5) | Set-Content -Path $functionsSettingsPath -Encoding UTF8
    }

    if ($SetGithubSecrets) {
        Write-Step "Reading publish profiles and writing GitHub secrets for workflows"

        $backendPublishProfile = Get-AzCliRaw -Arguments @("webapp", "deployment", "list-publishing-profiles", "--name", $backendAppName, "--resource-group", $ResourceGroupName, "--xml")
        $frontendPublishProfile = Get-AzCliRaw -Arguments @("webapp", "deployment", "list-publishing-profiles", "--name", $frontendAppName, "--resource-group", $ResourceGroupName, "--xml")
        $functionsPublishProfile = ""
        try {
            $functionsPublishProfile = Get-AzCliRaw -Arguments @("functionapp", "deployment", "list-publishing-profiles", "--name", $functionAppName, "--resource-group", $ResourceGroupName, "--xml")
        }
        catch {
            $canRerunFunctionsWorkflow = $false
            Write-Warning "Skipping AZURE_FUNCTIONAPP_PUBLISH_PROFILE for '$functionAppName'. Flex Consumption does not support publish profiles and the deploy-functions workflow should be updated to use identity-based deployment."
        }

        Set-OrUpdateGithubSecret -Name "AZURE_BACKEND_WEBAPP_NAME" -Value $backendAppName -Repo $GithubRepo
        Set-OrUpdateGithubSecret -Name "AZURE_BACKEND_WEBAPP_PUBLISH_PROFILE" -Value $backendPublishProfile -Repo $GithubRepo

        Set-OrUpdateGithubSecret -Name "AZURE_FRONTEND_WEBAPP_NAME" -Value $frontendAppName -Repo $GithubRepo
        Set-OrUpdateGithubSecret -Name "AZURE_FRONTEND_WEBAPP_PUBLISH_PROFILE" -Value $frontendPublishProfile -Repo $GithubRepo
        Set-OrUpdateGithubSecret -Name "NEXT_PUBLIC_API_URL" -Value $backendUrl -Repo $GithubRepo

        Set-OrUpdateGithubSecret -Name "AZURE_FUNCTIONAPP_NAME" -Value $functionAppName -Repo $GithubRepo
        if (-not [string]::IsNullOrWhiteSpace($functionsPublishProfile)) {
            Set-OrUpdateGithubSecret -Name "AZURE_FUNCTIONAPP_PUBLISH_PROFILE" -Value $functionsPublishProfile -Repo $GithubRepo
        }

        Set-OrUpdateGithubSecret -Name "ACR_LOGIN_SERVER" -Value $acrLoginServer -Repo $GithubRepo
        Set-OrUpdateGithubSecret -Name "ACR_USERNAME" -Value $acrUsername -Repo $GithubRepo
        Set-OrUpdateGithubSecret -Name "ACR_PASSWORD" -Value $acrPassword -Repo $GithubRepo
    }

    if ($RerunWorkflowRuns) {
        Write-Step "Re-running latest workflow runs"
        $workflows = @("deploy-backend.yml", "deploy-frontend.yml", "deploy-functions.yml", "deploy-worker.yml")
        if (-not $canRerunFunctionsWorkflow) {
            $workflows = $workflows | Where-Object { $_ -ne "deploy-functions.yml" }
        }

        foreach ($workflow in $workflows) {
            $runId = Get-GhRaw -Arguments @("run", "list", "--repo", $GithubRepo, "--workflow", $workflow, "--limit", "1", "--json", "databaseId", "-q", ".[0].databaseId")
            if (-not [string]::IsNullOrWhiteSpace($runId)) {
                Invoke-GhCli -Arguments @("run", "rerun", $runId, "--repo", $GithubRepo) | Out-Null
            }
        }
    }

    $resourceGroupPortalUrl = "https://portal.azure.com/#resource/subscriptions/$subscriptionId/resourceGroups/$ResourceGroupName/overview"

    Write-Host "`nDeployment complete." -ForegroundColor Green
    Write-Host "Resource Group: $ResourceGroupName"
    Write-Host "Backend URL:   $backendUrl"
    Write-Host "Frontend URL:  $frontendUrl"
    Write-Host "Function App:  $functionAppName"
    Write-Host "ACR:           $acrLoginServer"
    Write-Host "Portal:        $resourceGroupPortalUrl"
}
catch {
    Write-Host "`nDeployment failed." -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red

    if ($CleanupOnFailure -and $deploymentStarted) {
        Write-Warning "CleanupOnFailure is enabled. Deleting resource group '$ResourceGroupName'."
        try {
            Invoke-AzCli -Arguments @("group", "delete", "--name", $ResourceGroupName, "--yes", "--no-wait") | Out-Null
        }
        catch {
            Write-Warning "Failed to start resource group deletion: $($_.Exception.Message)"
        }
    }

    throw
}
