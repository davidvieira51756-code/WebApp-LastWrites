[CmdletBinding()]
param(
    [string]$Location = "italynorth",
    [string]$Prefix = "lastwrites",
    [string]$ResourceGroupName = "",
    [string]$GithubRepo = "",
    [switch]$SetGithubSecrets,
    [switch]$RerunWorkflowRuns,
    [switch]$SkipLocalFileUpdate,
    [switch]$CleanupOnFailure,
    [switch]$RecreateFunctionApp
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Write-Step {
    param([string]$Message)
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host $Message -ForegroundColor Green
}

function Write-Info {
    param([string]$Message)
    Write-Host $Message -ForegroundColor Yellow
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

function Normalize-AzCliOutput {
    param([Parameter(Mandatory = $true)][object]$Output)

    $rawText = ($Output | Out-String)
    $normalizedLines = @(
        $rawText -split "(`r`n|`n|`r)" |
            ForEach-Object { $_.TrimEnd() } |
            Where-Object {
                -not [string]::IsNullOrWhiteSpace($_) -and
                $_ -notmatch "^[\\/|\-]\s+Running" -and
                $_ -notmatch "^\s*(Running|\.+)$"
            }
    )

    return ($normalizedLines -join [Environment]::NewLine).Trim()
}

function Get-AzCliRaw {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $output = Invoke-AzCli -Arguments $Arguments
    return Normalize-AzCliOutput -Output $output
}

function Get-AzCliTsv {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $raw = Get-AzCliRaw -Arguments $Arguments
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return ""
    }

    $lines = @(
        $raw -split "(`r`n|`n|`r)" |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )

    if ($lines.Count -eq 0) {
        return ""
    }

    return $lines[-1].Trim()
}

function Get-AzCliJson {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $raw = Get-AzCliRaw -Arguments $Arguments
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return $null
    }

    return ($raw | ConvertFrom-Json)
}

function Invoke-GhCli {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $output = & gh @Arguments 2>&1
    $exitCode = $LASTEXITCODE

    if ($exitCode -ne 0) {
        $renderedOutput = ($output | Out-String).Trim()
        throw "GitHub CLI command failed (exit $exitCode): gh $($Arguments -join ' ')`n$renderedOutput"
    }

    return $output
}

function Get-GhRaw {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $output = Invoke-GhCli -Arguments $Arguments
    return ($output | Out-String).Trim()
}

function Set-OrUpdateGithubSecret {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value,
        [Parameter(Mandatory = $true)][string]$Repo
    )

    $output = $Value | & gh secret set $Name --repo $Repo 2>&1
    $exitCode = $LASTEXITCODE

    if ($exitCode -ne 0) {
        $renderedOutput = ($output | Out-String).Trim()
        throw "GitHub CLI secret update failed (exit $exitCode): gh secret set $Name --repo $Repo`n$renderedOutput"
    }
}

function New-RandomToken {
    param([int]$Length = 6)

    $chars = "abcdefghijklmnopqrstuvwxyz0123456789".ToCharArray()
    -join (1..$Length | ForEach-Object { $chars[(Get-Random -Minimum 0 -Maximum $chars.Length)] })
}

function New-StrongSecret {
    param([int]$ByteLength = 48)

    $bytes = New-Object byte[] $ByteLength
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()

    try {
        $rng.GetBytes($bytes)
    }
    finally {
        $rng.Dispose()
    }

    return [Convert]::ToBase64String($bytes)
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

function Test-IsGuid {
    param([Parameter(Mandatory = $true)][string]$Value)

    return $Value -match "^[0-9a-fA-F]{8}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{12}$"
}

function Ensure-ProviderRegistered {
    param([Parameter(Mandatory = $true)][string]$Namespace)

    $currentState = Get-AzCliTsv -Arguments @(
        "provider", "show",
        "--namespace", $Namespace,
        "--query", "registrationState",
        "-o", "tsv"
    )

    if ($currentState -eq "Registered") {
        return
    }

    Write-Info "Registering Azure provider '$Namespace' (current state: $currentState)"

    Invoke-AzCli -Arguments @(
        "provider", "register",
        "--namespace", $Namespace,
        "--wait",
        "-o", "none"
    ) | Out-Null

    $finalState = Get-AzCliTsv -Arguments @(
        "provider", "show",
        "--namespace", $Namespace,
        "--query", "registrationState",
        "-o", "tsv"
    )

    if ($finalState -ne "Registered") {
        throw "Provider '$Namespace' registration state is '$finalState' after registration attempt."
    }
}

function Ensure-RoleAssignment {
    param(
        [Parameter(Mandatory = $true)][string]$PrincipalId,
        [Parameter(Mandatory = $true)][string]$Role,
        [Parameter(Mandatory = $true)][string]$Scope
    )

    if (-not (Test-IsGuid -Value $PrincipalId)) {
        throw "Role assignment principalId is invalid: '$PrincipalId'"
    }

    $existingAssignments = Get-AzCliJson -Arguments @(
        "role", "assignment", "list",
        "--assignee-object-id", $PrincipalId,
        "--role", $Role,
        "--scope", $Scope,
        "-o", "json"
    )

    if ($null -ne $existingAssignments) {
        if ($existingAssignments -is [System.Array] -and $existingAssignments.Count -gt 0) {
            return
        }

        if ($existingAssignments -isnot [System.Array] -and -not [string]::IsNullOrWhiteSpace([string]$existingAssignments.id)) {
            return
        }
    }

    $createArguments = @(
        "role", "assignment", "create",
        "--assignee-object-id", $PrincipalId,
        "--assignee-principal-type", "ServicePrincipal",
        "--role", $Role,
        "--scope", $Scope,
        "-o", "none"
    )

    $maxAttempts = 12

    for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
        try {
            Invoke-AzCli -Arguments $createArguments | Out-Null
            return
        }
        catch {
            $message = $_.Exception.Message
            $isRetryable = $message -match "PrincipalNotFound|does not exist in the directory|Cannot find user or service principal|Insufficient privileges"

            if (-not $isRetryable -or $attempt -eq $maxAttempts) {
                throw
            }

            Write-Warning "Role assignment for principal '$PrincipalId' is not ready yet. Retrying in 10 seconds ($attempt/$maxAttempts)."
            Start-Sleep -Seconds 10
        }
    }
}

function Ensure-RoleAssignmentWithFallback {
    param(
        [Parameter(Mandatory = $true)][string]$PrincipalId,
        [Parameter(Mandatory = $true)][string[]]$Roles,
        [Parameter(Mandatory = $true)][string]$Scope
    )

    $errors = @()

    foreach ($role in $Roles) {
        try {
            Ensure-RoleAssignment -PrincipalId $PrincipalId -Role $role -Scope $Scope
            return
        }
        catch {
            $errors += "$role => $($_.Exception.Message)"
        }
    }

    throw "Could not assign any acceptable role at scope '$Scope'. Errors:`n$($errors -join "`n")"
}

function Test-AzResourceExists {
    param(
        [Parameter(Mandatory = $true)][string[]]$ShowArguments
    )

    try {
        $existing = Get-AzCliJson -Arguments $ShowArguments
        return $null -ne $existing
    }
    catch {
        return $false
    }
}

function Resolve-ContainerAppJobPrincipalId {
    param(
        [Parameter(Mandatory = $true)][string]$JobName,
        [Parameter(Mandatory = $true)][string]$ResourceGroupName
    )

    $maxAttempts = 12

    for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
        $principalId = ""

        try {
            $principalId = Get-AzCliTsv -Arguments @(
                "containerapp", "job", "identity", "show",
                "--name", $JobName,
                "--resource-group", $ResourceGroupName,
                "--query", "principalId",
                "-o", "tsv"
            )
        }
        catch {
            $principalId = ""
        }

        if (-not (Test-IsGuid -Value $principalId)) {
            try {
                $principalId = Get-AzCliTsv -Arguments @(
                    "containerapp", "job", "show",
                    "--name", $JobName,
                    "--resource-group", $ResourceGroupName,
                    "--query", "identity.principalId",
                    "-o", "tsv"
                )
            }
            catch {
                $principalId = ""
            }
        }

        if (Test-IsGuid -Value $principalId) {
            return $principalId
        }

        Write-Warning "Container Apps job principalId is not available yet. Retrying in 10 seconds ($attempt/$maxAttempts)."
        Start-Sleep -Seconds 10
    }

    throw "Container Apps job managed identity principalId could not be resolved after waiting for propagation."
}

function Test-AcrTagExists {
    param(
        [Parameter(Mandatory = $true)][string]$RegistryName,
        [Parameter(Mandatory = $true)][string]$RepositoryName,
        [Parameter(Mandatory = $true)][string]$Tag
    )

    try {
        $matchingTag = Get-AzCliTsv -Arguments @(
            "acr", "repository", "show-tags",
            "--name", $RegistryName,
            "--repository", $RepositoryName,
            "--query", "[?@=='$Tag'] | [0]",
            "-o", "tsv"
        )
    }
    catch {
        return $false
    }

    return [string]::Equals($matchingTag.Trim(), $Tag, [System.StringComparison]::OrdinalIgnoreCase)
}

function Get-PolicyAllowedLocations {
    try {
        $assignments = Get-AzCliJson -Arguments @(
            "policy", "assignment", "list",
            "--disable-scope-strict-match",
            "-o", "json"
        )
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
    param([Parameter(Mandatory = $true)][string]$RequestedLocation)

    $requestedNormalized = $RequestedLocation.ToLower()
    $allowedLocations = Get-PolicyAllowedLocations

    if ($allowedLocations.Count -eq 0) {
        return $requestedNormalized
    }

    if ($allowedLocations -contains $requestedNormalized) {
        return $requestedNormalized
    }

    throw "Location '$RequestedLocation' is not allowed by the current Azure Policy. Allowed locations detected: $($allowedLocations -join ', ')"
}

function Assert-FlexConsumptionLocation {
    param([Parameter(Mandatory = $true)][string]$RequestedLocation)

    try {
        $flexLocations = Get-AzCliJson -Arguments @(
            "functionapp", "list-flexconsumption-locations",
            "--runtime", "python",
            "-o", "json"
        )
    }
    catch {
        Write-Warning "Could not verify Flex Consumption supported regions. Continuing with requested location '$RequestedLocation'."
        return $RequestedLocation.ToLower()
    }

    if ($null -eq $flexLocations) {
        Write-Warning "Flex Consumption supported region list was empty. Continuing with requested location '$RequestedLocation'."
        return $RequestedLocation.ToLower()
    }

    if ($flexLocations -isnot [System.Array]) {
        $flexLocations = @($flexLocations)
    }

    $supportedLocations = @(
        $flexLocations |
            ForEach-Object {
                if ($_ -is [string]) {
                    $_
                }
                elseif ($null -ne $_ -and $_.PSObject.Properties.Name -contains "name") {
                    [string]$_.name
                }
                elseif ($null -ne $_ -and $_.PSObject.Properties.Name -contains "location") {
                    [string]$_.location
                }
            } |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            ForEach-Object { $_.ToLower() } |
            Sort-Object -Unique
    )

    $requestedNormalized = $RequestedLocation.ToLower()

    if ($supportedLocations.Count -eq 0) {
        Write-Warning "Could not parse Flex Consumption supported regions. Continuing with requested location '$RequestedLocation'."
        return $requestedNormalized
    }

    if ($supportedLocations -contains $requestedNormalized) {
        return $requestedNormalized
    }

    throw "Location '$RequestedLocation' does not currently support Azure Functions Flex Consumption for Python according to Azure CLI. Run: az functionapp list-flexconsumption-locations --runtime python --query `"sort_by(@, &name)[].{Region:name}`" -o table"
}

function Set-AppSettings {
    param(
        [Parameter(Mandatory = $true)][string]$AppKind,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$ResourceGroupName,
        [Parameter(Mandatory = $true)][string[]]$Settings
    )

    if ($AppKind -eq "webapp") {
        Invoke-AzCli -Arguments @(
            "webapp", "config", "appsettings", "set",
            "--name", $Name,
            "--resource-group", $ResourceGroupName,
            "--settings"
        ) + $Settings + @("-o", "none") | Out-Null
        return
    }

    if ($AppKind -eq "functionapp") {
        Invoke-AzCli -Arguments @(
            "functionapp", "config", "appsettings", "set",
            "--name", $Name,
            "--resource-group", $ResourceGroupName,
            "--settings"
        ) + $Settings + @("-o", "none") | Out-Null
        return
    }

    throw "Unknown AppKind '$AppKind'."
}

Ensure-Command -Name "az"

$account = Get-AzCliJson -Arguments @("account", "show", "-o", "json")

if ($null -eq $account -or [string]::IsNullOrWhiteSpace([string]$account.id)) {
    throw "Azure CLI is not logged in. Run: az login"
}

$subscriptionId = [string]$account.id

Write-Step "Ensuring required Azure resource providers are registered"

foreach ($namespace in @(
    "Microsoft.Web",
    "Microsoft.Storage",
    "Microsoft.DocumentDB",
    "Microsoft.EventGrid",
    "Microsoft.KeyVault",
    "Microsoft.ContainerRegistry",
    "Microsoft.App",
    "Microsoft.OperationalInsights",
    "Microsoft.Authorization"
)) {
    Ensure-ProviderRegistered -Namespace $namespace
}

try {
    Invoke-AzCli -Arguments @(
        "extension", "add",
        "--name", "containerapp",
        "--upgrade",
        "-o", "none"
    ) | Out-Null
}
catch {
    Write-Warning "Could not install/update Azure CLI containerapp extension automatically. If Container Apps commands fail, run: az extension add --name containerapp --upgrade"
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
    $matchingGroups = Get-AzCliJson -Arguments @(
        "group", "list",
        "--query", "[?starts_with(name, '$resourceGroupPrefix')].name",
        "-o", "json"
    )

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
$Location = Assert-FlexConsumptionLocation -RequestedLocation $Location

$planName = "plan-$prefixDashed-$token"
$backendAppName = "api-$prefixDashed-$token"
$frontendAppName = "web-$prefixDashed-$token"
$functionAppName = "func-$prefixDashed-$token"
$cosmosAccountName = "cdb-$prefixDashed-$token"
$cosmosDatabaseName = "last-writes-db"
$cosmosContainerName = "vaults"
$eventGridTopicName = "eg-$prefixDashed-$token"
$eventSubscriptionName = "es-grace-$token"
$keyVaultName = "kv-$prefixDashed-$token"
$containerAppsEnvironmentName = "cae-$prefixDashed-$token"
$containerAppsJobName = "job-$prefixDashed-$token"
$containerAppsJobContainerName = "delivery-worker"
$deploymentContainerName = "function-releases"
$pythonRuntime = "PYTHON:3.11"
$nodeRuntime = "NODE:20-lts"

if ($containerAppsEnvironmentName.Length -gt 32) {
    $containerAppsEnvironmentName = $containerAppsEnvironmentName.Substring(0, 32).TrimEnd('-')
}

if ($containerAppsJobName.Length -gt 32) {
    $containerAppsJobName = $containerAppsJobName.Substring(0, 32).TrimEnd('-')
}

if ($eventSubscriptionName.Length -gt 64) {
    $eventSubscriptionName = $eventSubscriptionName.Substring(0, 64).TrimEnd('-')
}

$storageAccountName = ("st" + $prefixCompact + $token)

if ($storageAccountName.Length -gt 24) {
    $storageAccountName = $storageAccountName.Substring(0, 24)
}

$storageAccountName = $storageAccountName.ToLower()

$acrName = ("acr" + $prefixCompact + $token)

if ($acrName.Length -gt 50) {
    $acrName = $acrName.Substring(0, 50)
}

$acrName = $acrName.ToLower()

$blobConnectionString = ""
$cosmosConnectionString = ""
$eventGridEndpoint = ""
$eventGridKey = ""
$keyVaultUrl = ""
$acrLoginServer = ""
$acrUsername = ""
$acrPassword = ""
$acrId = ""
$storageAccountId = ""
$backendUrl = ""
$frontendUrl = ""
$authSecretKey = ""
$frontendVerifyEmailUrl = ""
$backendPrincipalId = ""
$functionPrincipalId = ""
$deliveryJobPrincipalId = ""
$deliveryJobResourceId = ""

$deploymentStarted = $false

try {
    Write-Step "Creating or reusing resource group '$ResourceGroupName' in '$Location'"

    Invoke-AzCli -Arguments @(
        "group", "create",
        "--name", $ResourceGroupName,
        "--location", $Location,
        "-o", "none"
    ) | Out-Null

    $deploymentStarted = $true

    Write-Step "Creating or reusing App Service plan and web apps"

    $planExists = Test-AzResourceExists -ShowArguments @(
        "appservice", "plan", "show",
        "--name", $planName,
        "--resource-group", $ResourceGroupName,
        "-o", "json"
    )

    if (-not $planExists) {
        Invoke-AzCli -Arguments @(
            "appservice", "plan", "create",
            "--name", $planName,
            "--resource-group", $ResourceGroupName,
            "--is-linux",
            "--sku", "B1",
            "--location", $Location,
            "-o", "none"
        ) | Out-Null
    }
    else {
        Write-Info "App Service plan '$planName' already exists. Reusing it."
    }

    $backendExists = Test-AzResourceExists -ShowArguments @(
        "webapp", "show",
        "--name", $backendAppName,
        "--resource-group", $ResourceGroupName,
        "-o", "json"
    )

    if (-not $backendExists) {
        Invoke-AzCli -Arguments @(
            "webapp", "create",
            "--name", $backendAppName,
            "--resource-group", $ResourceGroupName,
            "--plan", $planName,
            "--runtime", $pythonRuntime,
            "-o", "none"
        ) | Out-Null
    }
    else {
        Write-Info "Backend web app '$backendAppName' already exists. Reusing it."
    }

    $frontendExists = Test-AzResourceExists -ShowArguments @(
        "webapp", "show",
        "--name", $frontendAppName,
        "--resource-group", $ResourceGroupName,
        "-o", "json"
    )

    if (-not $frontendExists) {
        Invoke-AzCli -Arguments @(
            "webapp", "create",
            "--name", $frontendAppName,
            "--resource-group", $ResourceGroupName,
            "--plan", $planName,
            "--runtime", $nodeRuntime,
            "-o", "none"
        ) | Out-Null
    }
    else {
        Write-Info "Frontend web app '$frontendAppName' already exists. Reusing it."
    }

    Write-Step "Creating or reusing storage account for blobs, Function host state, and Flex deployment packages"

    $storageExists = Test-AzResourceExists -ShowArguments @(
        "storage", "account", "show",
        "--name", $storageAccountName,
        "--resource-group", $ResourceGroupName,
        "-o", "json"
    )

    if (-not $storageExists) {
        Invoke-AzCli -Arguments @(
            "storage", "account", "create",
            "--name", $storageAccountName,
            "--resource-group", $ResourceGroupName,
            "--location", $Location,
            "--sku", "Standard_LRS",
            "--kind", "StorageV2",
            "--allow-blob-public-access", "false",
            "--min-tls-version", "TLS1_2",
            "-o", "none"
        ) | Out-Null
    }
    else {
        Write-Info "Storage account '$storageAccountName' already exists. Reusing it."
    }

    $storageAccountId = Get-AzCliTsv -Arguments @(
        "storage", "account", "show",
        "--resource-group", $ResourceGroupName,
        "--name", $storageAccountName,
        "--query", "id",
        "-o", "tsv"
    )

    $blobConnectionString = Get-AzCliTsv -Arguments @(
        "storage", "account", "show-connection-string",
        "--resource-group", $ResourceGroupName,
        "--name", $storageAccountName,
        "--query", "connectionString",
        "-o", "tsv"
    )

    if ([string]::IsNullOrWhiteSpace($blobConnectionString)) {
        throw "Storage connection string retrieval returned empty output."
    }

    foreach ($containerName in @("vaults", "deliveries", $deploymentContainerName)) {
        Invoke-AzCli -Arguments @(
            "storage", "container", "create",
            "--name", $containerName,
            "--connection-string", $blobConnectionString,
            "--auth-mode", "key",
            "-o", "none"
        ) | Out-Null
    }

    Write-Step "Creating or reusing Function App (Flex Consumption)"

    $functionAppExists = Test-AzResourceExists -ShowArguments @(
        "functionapp", "show",
        "--name", $functionAppName,
        "--resource-group", $ResourceGroupName,
        "-o", "json"
    )

    if ($functionAppExists -and $RecreateFunctionApp) {
        Write-Warning "RecreateFunctionApp is enabled. Deleting existing Function App '$functionAppName'."
        Invoke-AzCli -Arguments @(
            "functionapp", "delete",
            "--name", $functionAppName,
            "--resource-group", $ResourceGroupName,
            "-o", "none"
        ) | Out-Null

        $functionAppExists = $false
    }

    if (-not $functionAppExists) {
        Invoke-AzCli -Arguments @(
            "functionapp", "create",
            "--name", $functionAppName,
            "--resource-group", $ResourceGroupName,
            "--storage-account", $storageAccountName,
            "--flexconsumption-location", $Location,
            "--runtime", "python",
            "--runtime-version", "3.11",
            "--functions-version", "4",
            "--deployment-storage-name", $storageAccountName,
            "--deployment-storage-container-name", $deploymentContainerName,
            "--deployment-storage-auth-type", "StorageAccountConnectionString",
            "--deployment-storage-auth-value", "DEPLOYMENT_STORAGE_CONNECTION_STRING",
            "-o", "none"
        ) | Out-Null
    }
    else {
        Write-Info "Function App '$functionAppName' already exists. Reusing it. If it was created with the wrong plan, rerun with -RecreateFunctionApp or use a clean resource group."
    }

    Write-Step "Enabling publishing credentials where supported"

    foreach ($siteName in @($backendAppName, $frontendAppName, $functionAppName)) {
        try {
            Invoke-AzCli -Arguments @(
                "resource", "update",
                "--resource-group", $ResourceGroupName,
                "--namespace", "Microsoft.Web",
                "--resource-type", "basicPublishingCredentialsPolicies",
                "--parent", "sites/$siteName",
                "--name", "scm",
                "--set", "properties.allow=true",
                "-o", "none"
            ) | Out-Null

            Invoke-AzCli -Arguments @(
                "resource", "update",
                "--resource-group", $ResourceGroupName,
                "--namespace", "Microsoft.Web",
                "--resource-type", "basicPublishingCredentialsPolicies",
                "--parent", "sites/$siteName",
                "--name", "ftp",
                "--set", "properties.allow=true",
                "-o", "none"
            ) | Out-Null
        }
        catch {
            Write-Warning "Could not enable basic publishing credentials for '$siteName': $($_.Exception.Message)"
        }
    }

    Write-Step "Creating or reusing Cosmos DB account, database, and container"

    $cosmosExists = Test-AzResourceExists -ShowArguments @(
        "cosmosdb", "show",
        "--name", $cosmosAccountName,
        "--resource-group", $ResourceGroupName,
        "-o", "json"
    )

    if (-not $cosmosExists) {
        Invoke-AzCli -Arguments @(
            "cosmosdb", "create",
            "--name", $cosmosAccountName,
            "--resource-group", $ResourceGroupName,
            "--kind", "GlobalDocumentDB",
            "--locations", "regionName=$Location", "failoverPriority=0",
            "--default-consistency-level", "Session",
            "-o", "none"
        ) | Out-Null
    }
    else {
        Write-Info "Cosmos DB account '$cosmosAccountName' already exists. Reusing it."
    }

    Invoke-AzCli -Arguments @(
        "cosmosdb", "sql", "database", "create",
        "--account-name", $cosmosAccountName,
        "--resource-group", $ResourceGroupName,
        "--name", $cosmosDatabaseName,
        "-o", "none"
    ) | Out-Null

    Invoke-AzCli -Arguments @(
        "cosmosdb", "sql", "container", "create",
        "--account-name", $cosmosAccountName,
        "--resource-group", $ResourceGroupName,
        "--database-name", $cosmosDatabaseName,
        "--name", $cosmosContainerName,
        "--partition-key-path", "/user_id",
        "--throughput", "400",
        "-o", "none"
    ) | Out-Null

    $cosmosConnectionString = Get-AzCliTsv -Arguments @(
        "cosmosdb", "keys", "list",
        "--type", "connection-strings",
        "--name", $cosmosAccountName,
        "--resource-group", $ResourceGroupName,
        "--query", "connectionStrings[0].connectionString",
        "-o", "tsv"
    )

    if ([string]::IsNullOrWhiteSpace($cosmosConnectionString)) {
        throw "Cosmos connection string retrieval returned empty output."
    }

    Write-Step "Creating or reusing Event Grid topic"

    $eventGridTopicExists = Test-AzResourceExists -ShowArguments @(
        "eventgrid", "topic", "show",
        "--name", $eventGridTopicName,
        "--resource-group", $ResourceGroupName,
        "-o", "json"
    )

    if (-not $eventGridTopicExists) {
        Invoke-AzCli -Arguments @(
            "eventgrid", "topic", "create",
            "--name", $eventGridTopicName,
            "--resource-group", $ResourceGroupName,
            "--location", $Location,
            "--input-schema", "eventgridschema",
            "-o", "none"
        ) | Out-Null
    }
    else {
        Write-Info "Event Grid topic '$eventGridTopicName' already exists. Reusing it."
    }

    $eventGridEndpoint = Get-AzCliTsv -Arguments @(
        "eventgrid", "topic", "show",
        "--name", $eventGridTopicName,
        "--resource-group", $ResourceGroupName,
        "--query", "endpoint",
        "-o", "tsv"
    )

    $eventGridKey = Get-AzCliTsv -Arguments @(
        "eventgrid", "topic", "key", "list",
        "--name", $eventGridTopicName,
        "--resource-group", $ResourceGroupName,
        "--query", "key1",
        "-o", "tsv"
    )

    Write-Step "Creating or reusing Key Vault and saving connection-string secrets"

    $keyVaultExists = Test-AzResourceExists -ShowArguments @(
        "keyvault", "show",
        "--name", $keyVaultName,
        "--resource-group", $ResourceGroupName,
        "-o", "json"
    )

    if (-not $keyVaultExists) {
        Invoke-AzCli -Arguments @(
            "keyvault", "create",
            "--name", $keyVaultName,
            "--resource-group", $ResourceGroupName,
            "--location", $Location,
            "--enable-rbac-authorization", "false",
            "-o", "none"
        ) | Out-Null
    }
    else {
        Write-Info "Key Vault '$keyVaultName' already exists. Reusing it."
    }

    $keyVaultUrl = Get-AzCliTsv -Arguments @(
        "keyvault", "show",
        "--name", $keyVaultName,
        "--resource-group", $ResourceGroupName,
        "--query", "properties.vaultUri",
        "-o", "tsv"
    )

    Invoke-AzCli -Arguments @(
        "keyvault", "secret", "set",
        "--vault-name", $keyVaultName,
        "--name", "COSMOS-CONNECTION-STRING",
        "--value", $cosmosConnectionString,
        "-o", "none"
    ) | Out-Null

    Invoke-AzCli -Arguments @(
        "keyvault", "secret", "set",
        "--vault-name", $keyVaultName,
        "--name", "BLOB-CONNECTION-STRING",
        "--value", $blobConnectionString,
        "-o", "none"
    ) | Out-Null

    Write-Step "Enabling managed identities for backend and functions"

    $backendPrincipalId = Get-AzCliTsv -Arguments @(
        "webapp", "identity", "assign",
        "--name", $backendAppName,
        "--resource-group", $ResourceGroupName,
        "--query", "principalId",
        "-o", "tsv"
    )

    if ([string]::IsNullOrWhiteSpace($backendPrincipalId)) {
        throw "Backend web app managed identity principalId was empty."
    }

    $functionPrincipalId = Get-AzCliTsv -Arguments @(
        "functionapp", "identity", "assign",
        "--name", $functionAppName,
        "--resource-group", $ResourceGroupName,
        "--query", "principalId",
        "-o", "tsv"
    )

    if ([string]::IsNullOrWhiteSpace($functionPrincipalId)) {
        throw "Function app managed identity principalId was empty."
    }

    Write-Step "Granting backend access to Key Vault secrets and RSA key operations"

    Invoke-AzCli -Arguments @(
        "keyvault", "set-policy",
        "--name", $keyVaultName,
        "--object-id", $backendPrincipalId,
        "--secret-permissions", "get", "list",
        "--key-permissions", "get", "create", "decrypt", "unwrapKey", "encrypt", "wrapKey",
        "-o", "none"
    ) | Out-Null

    Write-Step "Creating or reusing Azure Container Registry for worker image"

    $acrExists = Test-AzResourceExists -ShowArguments @(
        "acr", "show",
        "--name", $acrName,
        "--resource-group", $ResourceGroupName,
        "-o", "json"
    )

    if (-not $acrExists) {
        Invoke-AzCli -Arguments @(
            "acr", "create",
            "--name", $acrName,
            "--resource-group", $ResourceGroupName,
            "--location", $Location,
            "--sku", "Basic",
            "--admin-enabled", "true",
            "-o", "none"
        ) | Out-Null
    }
    else {
        Write-Info "ACR '$acrName' already exists. Reusing it."
    }

    $acrLoginServer = Get-AzCliTsv -Arguments @(
        "acr", "show",
        "--name", $acrName,
        "--resource-group", $ResourceGroupName,
        "--query", "loginServer",
        "-o", "tsv"
    )

    $acrId = Get-AzCliTsv -Arguments @(
        "acr", "show",
        "--name", $acrName,
        "--resource-group", $ResourceGroupName,
        "--query", "id",
        "-o", "tsv"
    )

    $acrUsername = Get-AzCliTsv -Arguments @(
        "acr", "credential", "show",
        "--name", $acrName,
        "--resource-group", $ResourceGroupName,
        "--query", "username",
        "-o", "tsv"
    )

    $acrPassword = Get-AzCliTsv -Arguments @(
        "acr", "credential", "show",
        "--name", $acrName,
        "--resource-group", $ResourceGroupName,
        "--query", "passwords[0].value",
        "-o", "tsv"
    )

    Write-Step "Creating or reusing Container Apps environment for delivery jobs"

    $containerAppsEnvironmentExists = Test-AzResourceExists -ShowArguments @(
        "containerapp", "env", "show",
        "--name", $containerAppsEnvironmentName,
        "--resource-group", $ResourceGroupName,
        "-o", "json"
    )

    if (-not $containerAppsEnvironmentExists) {
        Invoke-AzCli -Arguments @(
            "containerapp", "env", "create",
            "--name", $containerAppsEnvironmentName,
            "--resource-group", $ResourceGroupName,
            "--location", $Location,
            "-o", "none"
        ) | Out-Null
    }
    else {
        Write-Info "Container Apps environment '$containerAppsEnvironmentName' already exists. Reusing it."
    }

    Write-Step "Creating or updating Container Apps delivery job"

    $deliveryJobExists = Test-AzResourceExists -ShowArguments @(
        "containerapp", "job", "show",
        "--name", $containerAppsJobName,
        "--resource-group", $ResourceGroupName,
        "-o", "json"
    )

    $workerImage = "$acrLoginServer/lastwrites-worker:latest"

    $workerEnvVars = @(
        "LOCAL_DEV_MODE=false",
        "COSMOS_CONNECTION_STRING=$cosmosConnectionString",
        "COSMOS_DATABASE_NAME=$cosmosDatabaseName",
        "COSMOS_VAULTS_CONTAINER=$cosmosContainerName",
        "BLOB_CONNECTION_STRING=$blobConnectionString",
        "KEY_VAULT_URL=$keyVaultUrl",
        "DELIVERIES_CONTAINER=deliveries"
    )

    if (-not $deliveryJobExists) {
        Invoke-AzCli -Arguments @(
            "containerapp", "job", "create",
            "--name", $containerAppsJobName,
            "--resource-group", $ResourceGroupName,
            "--environment", $containerAppsEnvironmentName,
            "--trigger-type", "Manual",
            "--replica-timeout", "3600",
            "--replica-retry-limit", "1",
            "--replica-completion-count", "1",
            "--parallelism", "1",
            "--container-name", $containerAppsJobContainerName,
            "--image", "mcr.microsoft.com/k8se/quickstart-jobs:latest",
            "--cpu", "0.5",
            "--memory", "1.0Gi",
            "--env-vars"
        ) + $workerEnvVars + @("-o", "none") | Out-Null
    }
    else {
        Write-Info "Container Apps job '$containerAppsJobName' already exists. Updating configuration."
    }

    Invoke-AzCli -Arguments @(
        "containerapp", "job", "identity", "assign",
        "--name", $containerAppsJobName,
        "--resource-group", $ResourceGroupName,
        "--system-assigned",
        "-o", "none"
    ) | Out-Null

    $deliveryJobPrincipalId = Resolve-ContainerAppJobPrincipalId -JobName $containerAppsJobName -ResourceGroupName $ResourceGroupName

    $deliveryJobResourceId = Get-AzCliTsv -Arguments @(
        "containerapp", "job", "show",
        "--name", $containerAppsJobName,
        "--resource-group", $ResourceGroupName,
        "--query", "id",
        "-o", "tsv"
    )

    Ensure-RoleAssignment -PrincipalId $deliveryJobPrincipalId -Role "AcrPull" -Scope $acrId

    Ensure-RoleAssignmentWithFallback `
        -PrincipalId $functionPrincipalId `
        -Roles @("Container Apps Jobs Operator", "Azure Container Apps Contributor", "Contributor") `
        -Scope $deliveryJobResourceId

    Invoke-AzCli -Arguments @(
        "containerapp", "job", "registry", "set",
        "--name", $containerAppsJobName,
        "--resource-group", $ResourceGroupName,
        "--server", $acrLoginServer,
        "--identity", "system",
        "-o", "none"
    ) | Out-Null

    if (Test-AcrTagExists -RegistryName $acrName -RepositoryName "lastwrites-worker" -Tag "latest") {
        Invoke-AzCli -Arguments @(
            "containerapp", "job", "update",
            "--name", $containerAppsJobName,
            "--resource-group", $ResourceGroupName,
            "--image", $workerImage,
            "--container-name", $containerAppsJobContainerName,
            "--replace-env-vars"
        ) + $workerEnvVars + @("-o", "none") | Out-Null
    }
    else {
        Write-Warning "Worker image '$workerImage' was not found in ACR yet. The delivery job will stay on the placeholder image until the worker image is pushed. Run the worker deploy workflow after infra provisioning."
    }

    Write-Step "Granting delivery worker access to Key Vault RSA decrypt operations"

    Invoke-AzCli -Arguments @(
        "keyvault", "set-policy",
        "--name", $keyVaultName,
        "--object-id", $deliveryJobPrincipalId,
        "--key-permissions", "get", "decrypt", "unwrapKey",
        "-o", "none"
    ) | Out-Null

    $backendHost = Get-AzCliTsv -Arguments @(
        "webapp", "show",
        "--name", $backendAppName,
        "--resource-group", $ResourceGroupName,
        "--query", "defaultHostName",
        "-o", "tsv"
    )

    $frontendHost = Get-AzCliTsv -Arguments @(
        "webapp", "show",
        "--name", $frontendAppName,
        "--resource-group", $ResourceGroupName,
        "--query", "defaultHostName",
        "-o", "tsv"
    )

    $backendUrl = "https://$backendHost"
    $frontendUrl = "https://$frontendHost"
    $frontendVerifyEmailUrl = "$frontendUrl/verify-email"
    $authSecretKey = New-StrongSecret -ByteLength 48

    Write-Step "Applying runtime settings to backend, frontend, and function apps"

    Set-AppSettings -AppKind "webapp" -Name $backendAppName -ResourceGroupName $ResourceGroupName -Settings @(
        "KEY_VAULT_URL=$keyVaultUrl",
        "KEY_VAULT_RSA_KEY_SIZE=2048",
        "KEY_VAULT_RSA_HARDWARE_PROTECTED=false",
        "COSMOS_DATABASE_NAME=$cosmosDatabaseName",
        "COSMOS_VAULTS_CONTAINER=$cosmosContainerName",
        "FRONTEND_ORIGINS=$frontendUrl",
        "COSMOS_CONNECTION_STRING_SECRET_NAME=COSMOS-CONNECTION-STRING",
        "BLOB_CONNECTION_STRING_SECRET_NAME=BLOB-CONNECTION-STRING",
        "AUTH_SECRET_KEY=$authSecretKey",
        "FRONTEND_VERIFY_EMAIL_URL=$frontendVerifyEmailUrl",
        "AUTH_ACCESS_TOKEN_TTL_MINUTES=120",
        "AUTH_REQUIRE_EMAIL_VERIFICATION=true",
        "AUTH_EXPOSE_VERIFICATION_TOKEN=false",
        "EMAIL_VERIFICATION_TOKEN_TTL_MINUTES=1440",
        "AUTH_PASSWORD_PBKDF2_ITERATIONS=260000",
        "SCM_DO_BUILD_DURING_DEPLOYMENT=true",
        "ENABLE_ORYX_BUILD=true",
        "WEBSITES_PORT=8000"
    )

    Set-AppSettings -AppKind "webapp" -Name $frontendAppName -ResourceGroupName $ResourceGroupName -Settings @(
        "NEXT_PUBLIC_API_URL=$backendUrl"
    )

    Invoke-AzCli -Arguments @(
        "webapp", "config", "set",
        "--name", $backendAppName,
        "--resource-group", $ResourceGroupName,
        "--startup-file", "python -m uvicorn main:app --host 0.0.0.0 --port 8000",
        "-o", "none"
    ) | Out-Null

    Invoke-AzCli -Arguments @(
        "webapp", "config", "set",
        "--name", $frontendAppName,
        "--resource-group", $ResourceGroupName,
        "--startup-file", "node server.js",
        "-o", "none"
    ) | Out-Null

    Set-AppSettings -AppKind "functionapp" -Name $functionAppName -ResourceGroupName $ResourceGroupName -Settings @(
        "DEPLOYMENT_STORAGE_CONNECTION_STRING=$blobConnectionString",
        "AzureWebJobsStorage=$blobConnectionString",
        "COSMOS_CONNECTION_STRING=$cosmosConnectionString",
        "COSMOS_DATABASE_NAME=$cosmosDatabaseName",
        "COSMOS_VAULTS_CONTAINER=$cosmosContainerName",
        "EVENT_GRID_ENDPOINT=$eventGridEndpoint",
        "EVENT_GRID_KEY=$eventGridKey",
        "BLOB_CONNECTION_STRING=$blobConnectionString",
        "AZURE_SUBSCRIPTION_ID=$subscriptionId",
        "CONTAINER_APPS_RESOURCE_GROUP=$ResourceGroupName",
        "CONTAINER_APPS_JOB_NAME=$containerAppsJobName",
        "CONTAINER_APPS_API_VERSION=2024-03-01"
    )

    Write-Step "Ensuring Event Grid subscription to start_delivery_job function"

    $startDeliveryJobFunctionExists = $false

    try {
        $startDeliveryJobFunction = Get-AzCliJson -Arguments @(
            "functionapp", "function", "show",
            "--name", $functionAppName,
            "--resource-group", $ResourceGroupName,
            "--function-name", "start_delivery_job",
            "-o", "json"
        )

        $startDeliveryJobFunctionExists = $null -ne $startDeliveryJobFunction -and -not [string]::IsNullOrWhiteSpace([string]$startDeliveryJobFunction.id)
    }
    catch {
        $startDeliveryJobFunctionExists = $false
    }

    if ($startDeliveryJobFunctionExists) {
        $eventGridTopicId = Get-AzCliTsv -Arguments @(
            "eventgrid", "topic", "show",
            "--name", $eventGridTopicName,
            "--resource-group", $ResourceGroupName,
            "--query", "id",
            "-o", "tsv"
        )

        $startDeliveryJobFunctionResourceId = "/subscriptions/$subscriptionId/resourceGroups/$ResourceGroupName/providers/Microsoft.Web/sites/$functionAppName/functions/start_delivery_job"

        $eventSubscriptionExists = Test-AzResourceExists -ShowArguments @(
            "eventgrid", "event-subscription", "show",
            "--name", $eventSubscriptionName,
            "--source-resource-id", $eventGridTopicId,
            "-o", "json"
        )

        if (-not $eventSubscriptionExists) {
            Invoke-AzCli -Arguments @(
                "eventgrid", "event-subscription", "create",
                "--name", $eventSubscriptionName,
                "--source-resource-id", $eventGridTopicId,
                "--endpoint-type", "azurefunction",
                "--endpoint", $startDeliveryJobFunctionResourceId,
                "--included-event-types", "GracePeriodExpired",
                "-o", "none"
            ) | Out-Null
        }
        else {
            Invoke-AzCli -Arguments @(
                "eventgrid", "event-subscription", "update",
                "--name", $eventSubscriptionName,
                "--source-resource-id", $eventGridTopicId,
                "--endpoint-type", "azurefunction",
                "--endpoint", $startDeliveryJobFunctionResourceId,
                "--included-event-types", "GracePeriodExpired",
                "-o", "none"
            ) | Out-Null
        }
    }
    else {
        Write-Warning "Function 'start_delivery_job' was not found in '$functionAppName'. This is normal before deploying the Functions code. Deploy functions first, then run this script again to attach Event Grid."
    }

    if (-not $SkipLocalFileUpdate) {
        Write-Step "Writing local development config files"

$backendEnvContent = @"
KEY_VAULT_URL=
KEY_VAULT_RSA_KEY_SIZE=2048
KEY_VAULT_RSA_HARDWARE_PROTECTED=false

COSMOS_CONNECTION_STRING=$cosmosConnectionString
COSMOS_DATABASE_NAME=$cosmosDatabaseName
COSMOS_VAULTS_CONTAINER=$cosmosContainerName

BLOB_CONNECTION_STRING=$blobConnectionString

FRONTEND_ORIGINS=http://localhost:3000
FRONTEND_VERIFY_EMAIL_URL=http://localhost:3000/verify-email

AUTH_SECRET_KEY=local-dev-change-me-auth-secret
AUTH_ACCESS_TOKEN_TTL_MINUTES=120
AUTH_REQUIRE_EMAIL_VERIFICATION=true
AUTH_EXPOSE_VERIFICATION_TOKEN=true
EMAIL_VERIFICATION_TOKEN_TTL_MINUTES=1440
AUTH_PASSWORD_PBKDF2_ITERATIONS=260000
"@

        Set-Content -Path $backendEnvPath -Value $backendEnvContent -Encoding UTF8

$frontendEnvContent = @"
NEXT_PUBLIC_API_URL=http://localhost:8000
"@

        Set-Content -Path $frontendEnvPath -Value $frontendEnvContent -Encoding UTF8

        $functionsSettings = [ordered]@{
            IsEncrypted = $false
            Values = [ordered]@{
                AzureWebJobsStorage = $blobConnectionString
                FUNCTIONS_WORKER_RUNTIME = "python"
                DEPLOYMENT_STORAGE_CONNECTION_STRING = $blobConnectionString
                COSMOS_CONNECTION_STRING = $cosmosConnectionString
                COSMOS_DATABASE_NAME = $cosmosDatabaseName
                COSMOS_VAULTS_CONTAINER = $cosmosContainerName
                EVENT_GRID_ENDPOINT = $eventGridEndpoint
                EVENT_GRID_KEY = $eventGridKey
                BLOB_CONNECTION_STRING = $blobConnectionString
                AZURE_SUBSCRIPTION_ID = $subscriptionId
                CONTAINER_APPS_RESOURCE_GROUP = $ResourceGroupName
                CONTAINER_APPS_JOB_NAME = $containerAppsJobName
                CONTAINER_APPS_API_VERSION = "2024-03-01"
            }
        }

        ($functionsSettings | ConvertTo-Json -Depth 5) | Set-Content -Path $functionsSettingsPath -Encoding UTF8
    }

    if ($SetGithubSecrets) {
        Write-Step "Reading publish profiles and writing GitHub secrets for workflows"

        $backendPublishProfile = Get-AzCliRaw -Arguments @(
            "webapp", "deployment", "list-publishing-profiles",
            "--name", $backendAppName,
            "--resource-group", $ResourceGroupName,
            "--xml"
        )

        $frontendPublishProfile = Get-AzCliRaw -Arguments @(
            "webapp", "deployment", "list-publishing-profiles",
            "--name", $frontendAppName,
            "--resource-group", $ResourceGroupName,
            "--xml"
        )

        $functionsPublishProfile = ""
        $canSetFunctionsPublishProfile = $true

        try {
            $functionsPublishProfile = Get-AzCliRaw -Arguments @(
                "functionapp", "deployment", "list-publishing-profiles",
                "--name", $functionAppName,
                "--resource-group", $ResourceGroupName,
                "--xml"
            )
        }
        catch {
            $canSetFunctionsPublishProfile = $false
            Write-Warning "Skipping AZURE_FUNCTIONAPP_PUBLISH_PROFILE for '$functionAppName'. If your Functions workflow uses publish-profile auth, use Azure login/OIDC or generate the profile from the portal if available."
        }

        Set-OrUpdateGithubSecret -Name "AZURE_BACKEND_WEBAPP_NAME" -Value $backendAppName -Repo $GithubRepo
        Set-OrUpdateGithubSecret -Name "AZURE_BACKEND_WEBAPP_PUBLISH_PROFILE" -Value $backendPublishProfile -Repo $GithubRepo

        Set-OrUpdateGithubSecret -Name "AZURE_FRONTEND_WEBAPP_NAME" -Value $frontendAppName -Repo $GithubRepo
        Set-OrUpdateGithubSecret -Name "AZURE_FRONTEND_WEBAPP_PUBLISH_PROFILE" -Value $frontendPublishProfile -Repo $GithubRepo
        Set-OrUpdateGithubSecret -Name "NEXT_PUBLIC_API_URL" -Value $backendUrl -Repo $GithubRepo

        Set-OrUpdateGithubSecret -Name "AZURE_FUNCTIONAPP_NAME" -Value $functionAppName -Repo $GithubRepo

        if ($canSetFunctionsPublishProfile -and -not [string]::IsNullOrWhiteSpace($functionsPublishProfile)) {
            Set-OrUpdateGithubSecret -Name "AZURE_FUNCTIONAPP_PUBLISH_PROFILE" -Value $functionsPublishProfile -Repo $GithubRepo
        }

        Set-OrUpdateGithubSecret -Name "ACR_LOGIN_SERVER" -Value $acrLoginServer -Repo $GithubRepo
        Set-OrUpdateGithubSecret -Name "ACR_USERNAME" -Value $acrUsername -Repo $GithubRepo
        Set-OrUpdateGithubSecret -Name "ACR_PASSWORD" -Value $acrPassword -Repo $GithubRepo
        Set-OrUpdateGithubSecret -Name "AZURE_CONTAINERAPPS_RESOURCE_GROUP" -Value $ResourceGroupName -Repo $GithubRepo
        Set-OrUpdateGithubSecret -Name "AZURE_CONTAINERAPPS_JOB_NAME" -Value $containerAppsJobName -Repo $GithubRepo
        Set-OrUpdateGithubSecret -Name "AZURE_RESOURCE_GROUP" -Value $ResourceGroupName -Repo $GithubRepo
        Set-OrUpdateGithubSecret -Name "AZURE_SUBSCRIPTION_ID" -Value $subscriptionId -Repo $GithubRepo
    }

    if ($RerunWorkflowRuns) {
        Write-Step "Re-running latest workflow runs"

        $workflows = @(
            "deploy-backend.yml",
            "deploy-frontend.yml",
            "deploy-functions.yml",
            "deploy-worker.yml"
        )

        foreach ($workflow in $workflows) {
            $runId = Get-GhRaw -Arguments @(
                "run", "list",
                "--repo", $GithubRepo,
                "--workflow", $workflow,
                "--limit", "1",
                "--json", "databaseId",
                "-q", ".[0].databaseId"
            )

            if (-not [string]::IsNullOrWhiteSpace($runId)) {
                Invoke-GhCli -Arguments @(
                    "run", "rerun",
                    $runId,
                    "--repo", $GithubRepo
                ) | Out-Null
            }
        }
    }

    $resourceGroupPortalUrl = "https://portal.azure.com/#resource/subscriptions/$subscriptionId/resourceGroups/$ResourceGroupName/overview"

    Write-Host "`nDeployment complete." -ForegroundColor Green
    Write-Host "Resource Group: $ResourceGroupName"
    Write-Host "Location:       $Location"
    Write-Host "Backend URL:   $backendUrl"
    Write-Host "Frontend URL:  $frontendUrl"
    Write-Host "Function App:  $functionAppName"
    Write-Host "Job Env:       $containerAppsEnvironmentName"
    Write-Host "Delivery Job:  $containerAppsJobName"
    Write-Host "ACR:           $acrLoginServer"
    Write-Host "Portal:        $resourceGroupPortalUrl"

    Write-Host "`nNext steps:" -ForegroundColor Cyan
    Write-Host "1. Push/run deploy-backend.yml and deploy-frontend.yml."
    Write-Host "2. Push/run deploy-worker.yml so '$workerImage' exists."
    Write-Host "3. Push/run deploy-functions.yml so 'start_delivery_job' exists."
    Write-Host "4. Run this script once more to attach Event Grid to 'start_delivery_job' if it was missing."
}
catch {
    Write-Host "`nDeployment failed." -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red

    if ($CleanupOnFailure -and $deploymentStarted) {
        Write-Warning "CleanupOnFailure is enabled. Deleting resource group '$ResourceGroupName'."

        try {
            Invoke-AzCli -Arguments @(
                "group", "delete",
                "--name", $ResourceGroupName,
                "--yes",
                "--no-wait"
            ) | Out-Null
        }
        catch {
            Write-Warning "Failed to start resource group deletion: $($_.Exception.Message)"
        }
    }

    throw
}