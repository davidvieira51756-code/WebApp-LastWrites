[CmdletBinding()]
param(
    [string]$Location = "italynorth",
    [string]$Prefix = "lastwrites",
    [string]$ResourceGroupName = "rg-lastwrites-516251",
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


function Format-AzCliArgumentsForError {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $sensitiveOptions = @("--account-key", "--connection-string", "--password", "--value")
    $sensitiveSettingNames = @(
        "ACR_PASSWORD",
        "AUTH_SECRET_KEY",
        "BLOB_CONNECTION_STRING",
        "COSMOS_CONNECTION_STRING",
        "EVENT_GRID_KEY"
    )
    $redactedArguments = New-Object System.Collections.Generic.List[string]
    $skipNext = $false

    for ($index = 0; $index -lt $Arguments.Count; $index++) {
        if ($skipNext) {
            $skipNext = $false
            continue
        }

        $argument = [string]$Arguments[$index]
        if ($sensitiveOptions -contains $argument) {
            $redactedArguments.Add($argument)
            if (($index + 1) -lt $Arguments.Count) {
                $redactedArguments.Add("<redacted>")
                $skipNext = $true
            }
            continue
        }

        $redacted = $argument -replace "(?i)(AccountKey=)[^;]+", '$1<redacted>'
        foreach ($settingName in $sensitiveSettingNames) {
            if ($redacted -like "$settingName=*") {
                $redacted = "$settingName=<redacted>"
                break
            }
        }

        $redactedArguments.Add($redacted)
    }

    return ($redactedArguments -join ' ')
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
        $safeArguments = Format-AzCliArgumentsForError -Arguments $effectiveArguments
        throw "Azure CLI command failed (exit $exitCode): az $safeArguments`n$renderedOutput"
    }

    return $output
}

function Invoke-AzCliWithRetry {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [int]$Attempts = 6,
        [int]$DelaySeconds = 10,
        [string]$RetryDescription = "Azure CLI command"
    )

    $lastError = $null
    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try {
            return Invoke-AzCli -Arguments $Arguments
        }
        catch {
            $lastError = $_
            if ($attempt -ge $Attempts) {
                break
            }

            Write-Warning "$RetryDescription failed on attempt $attempt/$Attempts. Retrying in $DelaySeconds seconds."
            Start-Sleep -Seconds $DelaySeconds
        }
    }

    throw $lastError
}

function Normalize-AzCliOutput {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Output
    )

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
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $output = Invoke-AzCli -Arguments $Arguments
    if ($null -eq $output) {
        return ""
    }
    return Normalize-AzCliOutput -Output $output
}

function Get-AzCliTsv {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

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

function New-StorageAccountName {
    param(
        [Parameter(Mandatory = $true)][string]$PrefixCompact,
        [Parameter(Mandatory = $true)][string]$Suffix
    )

    $name = ("st" + $PrefixCompact + $Suffix).ToLower()
    if ($name.Length -gt 24) {
        $name = $name.Substring(0, 24)
    }

    return $name
}

function Test-BlobEndpointExists {
    param(
        [Parameter(Mandatory = $true)][string]$StorageAccountName
    )

    $blobEndpoint = "https://$StorageAccountName.blob.core.windows.net/?comp=list"
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $blobEndpoint -TimeoutSec 20 | Out-Null
        return $true
    }
    catch {
        $response = $_.Exception.Response
        if ($null -eq $response) {
            return $false
        }

        $statusCode = [int]$response.StatusCode
        if ($statusCode -eq 404) {
            return $false
        }

        # 400/403 means the blob service exists but rejected the anonymous request.
        return $true
    }
}

function Wait-BlobEndpointExists {
    param(
        [Parameter(Mandatory = $true)][string]$StorageAccountName,
        [int]$Attempts = 12,
        [int]$DelaySeconds = 10
    )

    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        if (Test-BlobEndpointExists -StorageAccountName $StorageAccountName) {
            return $true
        }

        if ($attempt -lt $Attempts) {
            Write-Warning "Blob endpoint for storage account '$StorageAccountName' returned 404 on attempt $attempt/$Attempts. Retrying in $DelaySeconds seconds."
            Start-Sleep -Seconds $DelaySeconds
        }
    }

    return $false
}

function Wait-StorageAccountSucceeded {
    param(
        [Parameter(Mandatory = $true)][string]$StorageAccountName,
        [Parameter(Mandatory = $true)][string]$ResourceGroupName,
        [int]$Attempts = 30,
        [int]$DelaySeconds = 5
    )

    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        $state = Get-AzCliTsv -Arguments @("storage", "account", "show", "--name", $StorageAccountName, "--resource-group", $ResourceGroupName, "--query", "provisioningState", "-o", "tsv")
        if ($state -eq "Succeeded") {
            return $true
        }

        if ($attempt -lt $Attempts) {
            Write-Warning "Storage account '$StorageAccountName' provisioningState is '$state' on attempt $attempt/$Attempts. Retrying in $DelaySeconds seconds."
            Start-Sleep -Seconds $DelaySeconds
        }
    }

    return $false
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

function Test-IsGuid {
    param(
        [Parameter(Mandatory = $true)][string]$Value
    )

    return $Value -match "^[0-9a-fA-F]{8}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{4}\-[0-9a-fA-F]{12}$"
}

function Ensure-RoleAssignment {
    param(
        [Parameter(Mandatory = $true)][string]$PrincipalId,
        [Parameter(Mandatory = $true)][string]$RoleName,
        [Parameter(Mandatory = $true)][string]$Scope
    )

    if (-not (Test-IsGuid -Value $PrincipalId)) {
        throw "Role assignment principalId is invalid: '$PrincipalId'"
    }

    $existingAssignments = Get-AzCliJson -Arguments @(
        "role", "assignment", "list",
        "--assignee-object-id", $PrincipalId,
        "--role", $RoleName,
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
        "--role", $RoleName,
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
            $isRetryable = $message -match "PrincipalNotFound|does not exist in the directory|Cannot find user or service principal"
            if (-not $isRetryable -or $attempt -eq $maxAttempts) {
                throw
            }

            Write-Warning "Role assignment for principal '$PrincipalId' is not available in Entra ID yet. Retrying in 10 seconds ($attempt/$maxAttempts)."
            Start-Sleep -Seconds 10
        }
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
            $message = $_.Exception.Message
            if ($message -notmatch "Precondition Failed" -or $attempt -eq $maxAttempts) {
                throw
            }

            Write-Warning "Container Apps job identity endpoint is not ready yet for '$JobName'. Retrying in 10 seconds ($attempt/$maxAttempts)."
            Start-Sleep -Seconds 10
            continue
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
                $message = $_.Exception.Message
                if ($message -notmatch "Precondition Failed" -or $attempt -eq $maxAttempts) {
                    throw
                }

                Write-Warning "Container Apps job resource view is not ready yet for '$JobName'. Retrying in 10 seconds ($attempt/$maxAttempts)."
                Start-Sleep -Seconds 10
                continue
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

function Test-FunctionExists {
    param(
        [Parameter(Mandatory = $true)][string]$FunctionAppName,
        [Parameter(Mandatory = $true)][string]$ResourceGroupName,
        [Parameter(Mandatory = $true)][string]$FunctionName
    )

    try {
        $function = Get-AzCliJson -Arguments @(
            "functionapp", "function", "show",
            "--name", $FunctionAppName,
            "--resource-group", $ResourceGroupName,
            "--function-name", $FunctionName,
            "-o", "json"
        )
    }
    catch {
        return $false
    }

    return $null -ne $function -and -not [string]::IsNullOrWhiteSpace([string]$function.id)
}

function Test-EventGridSubscriptionExists {
    param(
        [Parameter(Mandatory = $true)][string]$EventSubscriptionName,
        [Parameter(Mandatory = $true)][string]$SourceResourceId
    )

    try {
        $eventSubscription = Get-AzCliJson -Arguments @(
            "eventgrid", "event-subscription", "show",
            "--name", $EventSubscriptionName,
            "--source-resource-id", $SourceResourceId,
            "-o", "json"
        )
    }
    catch {
        return $false
    }

    return $null -ne $eventSubscription -and -not [string]::IsNullOrWhiteSpace([string]$eventSubscription.id)
}

function Get-OrCreateGithubActionsAzureCredentialsJson {
    param(
        [Parameter(Mandatory = $true)][string]$ResourceGroupScope,
        [Parameter(Mandatory = $true)][string]$PrincipalDisplayName
    )

    $existingAppId = Get-AzCliTsv -Arguments @(
        "ad", "sp", "list",
        "--display-name", $PrincipalDisplayName,
        "--query", "[0].appId",
        "-o", "tsv"
    )

    if ([string]::IsNullOrWhiteSpace($existingAppId)) {
        return Get-AzCliRaw -Arguments @(
            "ad", "sp", "create-for-rbac",
            "--name", $PrincipalDisplayName,
            "--role", "Contributor",
            "--scopes", $ResourceGroupScope,
            "--json-auth", "true",
            "-o", "json"
        )
    }

    $principalObjectId = Get-AzCliTsv -Arguments @(
        "ad", "sp", "show",
        "--id", $existingAppId,
        "--query", "id",
        "-o", "tsv"
    )
    Ensure-RoleAssignment -PrincipalId $principalObjectId -RoleName "Contributor" -Scope $ResourceGroupScope

    $credentialReset = Get-AzCliJson -Arguments @(
        "ad", "sp", "credential", "reset",
        "--id", $existingAppId,
        "-o", "json"
    )
    $accountInfo = Get-AzCliJson -Arguments @("account", "show", "-o", "json")

    $authPayload = [ordered]@{
        clientId       = [string]$credentialReset.appId
        clientSecret   = [string]$credentialReset.password
        subscriptionId = [string]$accountInfo.id
        tenantId       = [string]$credentialReset.tenant
    }

    return ($authPayload | ConvertTo-Json -Compress)
}

function Wait-GithubWorkflowRun {
    param(
        [Parameter(Mandatory = $true)][string]$RunId,
        [Parameter(Mandatory = $true)][string]$Repo,
        [Parameter(Mandatory = $true)][string]$WorkflowName,
        [int]$Attempts = 90,
        [int]$DelaySeconds = 10
    )

    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        $runState = Get-GhRaw -Arguments @(
            "run", "view", $RunId,
            "--repo", $Repo,
            "--json", "status,conclusion",
            "-q", "{status: .status, conclusion: .conclusion}"
        ) | ConvertFrom-Json

        if ($runState.status -eq "completed") {
            if ($runState.conclusion -ne "success") {
                throw "GitHub Actions workflow '$WorkflowName' finished with conclusion '$($runState.conclusion)'."
            }
            return
        }

        if ($attempt -lt $Attempts) {
            Write-Warning "Workflow '$WorkflowName' is '$($runState.status)'. Waiting $DelaySeconds seconds ($attempt/$Attempts)."
            Start-Sleep -Seconds $DelaySeconds
        }
    }

    throw "GitHub Actions workflow '$WorkflowName' did not complete in time."
}

function Wait-FunctionAvailable {
    param(
        [Parameter(Mandatory = $true)][string]$FunctionAppName,
        [Parameter(Mandatory = $true)][string]$ResourceGroupName,
        [Parameter(Mandatory = $true)][string]$FunctionName,
        [int]$Attempts = 18,
        [int]$DelaySeconds = 10
    )

    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        if (Test-FunctionExists -FunctionAppName $FunctionAppName -ResourceGroupName $ResourceGroupName -FunctionName $FunctionName) {
            return $true
        }

        if ($attempt -lt $Attempts) {
            Write-Warning "Function '$FunctionName' is not available yet in '$FunctionAppName'. Retrying in $DelaySeconds seconds ($attempt/$Attempts)."
            Start-Sleep -Seconds $DelaySeconds
        }
    }

    return $false
}

function Set-ContainerAppJobRegistry {
    param(
        [Parameter(Mandatory = $true)][string]$JobName,
        [Parameter(Mandatory = $true)][string]$ResourceGroupName,
        [Parameter(Mandatory = $true)][string]$RegistryServer,
        [Parameter(Mandatory = $true)][string]$RegistryUsername,
        [Parameter(Mandatory = $true)][string]$RegistryPassword
    )

    try {
        Invoke-AzCliWithRetry -Arguments @(
            "containerapp", "job", "registry", "set",
            "--name", $JobName,
            "--resource-group", $ResourceGroupName,
            "--server", $RegistryServer,
            "--identity", "system",
            "-o", "none"
        ) -Attempts 3 -DelaySeconds 10 -RetryDescription "Configuring Container Apps job registry with managed identity" | Out-Null
        return
    }
    catch {
        $message = $_.Exception.Message
        if ($message -notmatch "Precondition Failed") {
            throw
        }

        Write-Warning "Container Apps job registry managed identity configuration returned 'Precondition Failed'. Falling back to ACR admin credentials for image pulls."
    }

    Invoke-AzCliWithRetry -Arguments @(
        "containerapp", "job", "registry", "set",
        "--name", $JobName,
        "--resource-group", $ResourceGroupName,
        "--server", $RegistryServer,
        "--username", $RegistryUsername,
        "--password", $RegistryPassword,
        "-o", "none"
    ) -Attempts 3 -DelaySeconds 10 -RetryDescription "Configuring Container Apps job registry with ACR credentials" | Out-Null
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

function Resolve-FlexConsumptionLocation {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RequestedLocation
    )

    try {
        $flexLocations = Get-AzCliJson -Arguments @("functionapp", "list-flexconsumption-locations", "-o", "json")
    }
    catch {
        Write-Warning "Could not determine Flex Consumption supported regions. Using requested location '$RequestedLocation' as-is."
        return $RequestedLocation.ToLower()
    }

    if ($null -eq $flexLocations) {
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
            } |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            ForEach-Object { $_.ToLower() } |
            Sort-Object -Unique
    )

    $requestedNormalized = $RequestedLocation.ToLower()
    if ($supportedLocations -contains $requestedNormalized) {
        return $requestedNormalized
    }

    $allowedLocations = Get-PolicyAllowedLocations
    $candidateLocations = @($supportedLocations)
    if ($allowedLocations.Count -gt 0) {
        $candidateLocations = @($supportedLocations | Where-Object { $allowedLocations -contains $_ })
    }

    if ($candidateLocations.Count -gt 0) {
        $fallback = $candidateLocations[0]
        Write-Warning "Location '$RequestedLocation' does not support Azure Functions Flex Consumption. Falling back to '$fallback'."
        return $fallback
    }

    throw "Location '$RequestedLocation' does not support Azure Functions Flex Consumption, and no policy-allowed fallback region was found. Run 'az functionapp list-flexconsumption-locations --query ""sort_by(@, &name)[].{Region:name}"" -o table' to choose a supported region."
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
    "Microsoft.ContainerRegistry",
    "Microsoft.App",
    "Microsoft.OperationalInsights"
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
$Location = Resolve-FlexConsumptionLocation -RequestedLocation $Location

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
$pythonRuntime = "PYTHON:3.11"
$nodeRuntime = "NODE:22-lts"

if ($containerAppsEnvironmentName.Length -gt 32) {
    $containerAppsEnvironmentName = $containerAppsEnvironmentName.Substring(0, 32).TrimEnd('-')
}
if ($containerAppsJobName.Length -gt 32) {
    $containerAppsJobName = $containerAppsJobName.Substring(0, 32).TrimEnd('-')
}

if ($eventSubscriptionName.Length -gt 64) {
    $eventSubscriptionName = $eventSubscriptionName.Substring(0, 64).TrimEnd('-')
}

$storageAccountName = New-StorageAccountName -PrefixCompact $prefixCompact -Suffix $token

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
$acrId = ""
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
    Write-Step "Creating resource group '$ResourceGroupName' in '$Location'"
    Invoke-AzCli -Arguments @("group", "create", "--name", $ResourceGroupName, "--location", $Location, "-o", "none") | Out-Null
    $deploymentStarted = $true

    Write-Step "Creating App Service plan and web apps"
    Invoke-AzCli -Arguments @("appservice", "plan", "create", "--name", $planName, "--resource-group", $ResourceGroupName, "--is-linux", "--sku", "B1", "--location", $Location, "-o", "none") | Out-Null
    Invoke-AzCli -Arguments @("webapp", "create", "--name", $backendAppName, "--resource-group", $ResourceGroupName, "--plan", $planName, "--runtime", $pythonRuntime, "-o", "none") | Out-Null
    Invoke-AzCli -Arguments @("webapp", "create", "--name", $frontendAppName, "--resource-group", $ResourceGroupName, "--plan", $planName, "--runtime", $nodeRuntime, "-o", "none") | Out-Null

    Write-Step "Creating storage account for blobs and function host"
    $storageAccountReady = $false
    for ($storageAttempt = 1; $storageAttempt -le 3; $storageAttempt++) {
        Invoke-AzCli -Arguments @("storage", "account", "create", "--name", $storageAccountName, "--resource-group", $ResourceGroupName, "--location", $Location, "--sku", "Standard_LRS", "--kind", "StorageV2", "--allow-blob-public-access", "false", "--min-tls-version", "TLS1_2", "-o", "none") | Out-Null
        if (-not (Wait-StorageAccountSucceeded -StorageAccountName $storageAccountName -ResourceGroupName $ResourceGroupName -Attempts 30 -DelaySeconds 5)) {
            throw "Storage account '$storageAccountName' did not reach provisioningState 'Succeeded'."
        }

        if (Wait-BlobEndpointExists -StorageAccountName $storageAccountName -Attempts 12 -DelaySeconds 10) {
            $storageAccountReady = $true
            break
        }

        if ($storageAttempt -lt 3) {
            $previousStorageAccountName = $storageAccountName
            $storageAccountName = New-StorageAccountName -PrefixCompact $prefixCompact -Suffix (New-RandomToken -Length 10)
            Write-Warning "Blob endpoint for storage account '$previousStorageAccountName' is still unavailable. Retrying with new storage account '$storageAccountName'."
        }
    }

    if (-not $storageAccountReady) {
        throw "Blob endpoint for storage account '$storageAccountName' did not become available. Try a new -ResourceGroupName or rerun later."
    }

    $blobConnectionString = Get-AzCliTsv -Arguments @("storage", "account", "show-connection-string", "--resource-group", $ResourceGroupName, "--name", $storageAccountName, "--query", "connectionString", "-o", "tsv")
    if ([string]::IsNullOrWhiteSpace($blobConnectionString)) {
        throw "Storage connection string retrieval returned empty output."
    }
    Invoke-AzCliWithRetry -Arguments @("storage", "container", "create", "--name", "vaults", "--connection-string", $blobConnectionString, "--auth-mode", "key", "-o", "none") -Attempts 12 -DelaySeconds 10 -RetryDescription "Creating storage container 'vaults'" | Out-Null
    Invoke-AzCliWithRetry -Arguments @("storage", "container", "create", "--name", "deliveries", "--connection-string", $blobConnectionString, "--auth-mode", "key", "-o", "none") -Attempts 12 -DelaySeconds 10 -RetryDescription "Creating storage container 'deliveries'" | Out-Null

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
        Invoke-AzCli -Arguments @("functionapp", "create", "--name", $functionAppName, "--resource-group", $ResourceGroupName, "--storage-account", $storageAccountName, "--flexconsumption-location", $Location, "--runtime", "python", "--runtime-version", "3.11", "-o", "none") | Out-Null
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

    Write-Step "Enabling managed identities for backend and functions"
    $backendPrincipalId = Get-AzCliTsv -Arguments @("webapp", "identity", "assign", "--name", $backendAppName, "--resource-group", $ResourceGroupName, "--query", "principalId", "-o", "tsv")
    if ([string]::IsNullOrWhiteSpace($backendPrincipalId)) {
        throw "Backend web app managed identity principalId was empty."
    }
    $functionPrincipalId = Get-AzCliTsv -Arguments @("functionapp", "identity", "assign", "--name", $functionAppName, "--resource-group", $ResourceGroupName, "--query", "principalId", "-o", "tsv")
    if ([string]::IsNullOrWhiteSpace($functionPrincipalId)) {
        throw "Function app managed identity principalId was empty."
    }

    Write-Step "Granting backend access to Key Vault secrets and RSA key operations"
    Invoke-AzCli -Arguments @("keyvault", "set-policy", "--name", $keyVaultName, "--object-id", $backendPrincipalId, "--secret-permissions", "get", "list", "--key-permissions", "get", "create", "decrypt", "unwrapKey", "-o", "none") | Out-Null

    Write-Step "Creating Azure Container Registry for worker image"
    Invoke-AzCli -Arguments @("acr", "create", "--name", $acrName, "--resource-group", $ResourceGroupName, "--location", $Location, "--sku", "Basic", "--admin-enabled", "true", "-o", "none") | Out-Null
    $acrLoginServer = Get-AzCliTsv -Arguments @("acr", "show", "--name", $acrName, "--resource-group", $ResourceGroupName, "--query", "loginServer", "-o", "tsv")
    $acrId = Get-AzCliTsv -Arguments @("acr", "show", "--name", $acrName, "--resource-group", $ResourceGroupName, "--query", "id", "-o", "tsv")
    $acrUsername = Get-AzCliTsv -Arguments @("acr", "credential", "show", "--name", $acrName, "--resource-group", $ResourceGroupName, "--query", "username", "-o", "tsv")
    $acrPassword = Get-AzCliTsv -Arguments @("acr", "credential", "show", "--name", $acrName, "--resource-group", $ResourceGroupName, "--query", "passwords[0].value", "-o", "tsv")

    Write-Step "Creating Container Apps environment for delivery jobs"
    $containerAppsEnvironmentExists = $false
    try {
        $existingEnvironment = Get-AzCliJson -Arguments @("containerapp", "env", "show", "--name", $containerAppsEnvironmentName, "--resource-group", $ResourceGroupName, "-o", "json")
        $containerAppsEnvironmentExists = $null -ne $existingEnvironment -and -not [string]::IsNullOrWhiteSpace([string]$existingEnvironment.id)
    }
    catch {
        $containerAppsEnvironmentExists = $false
    }

    if (-not $containerAppsEnvironmentExists) {
        Invoke-AzCli -Arguments @("containerapp", "env", "create", "--name", $containerAppsEnvironmentName, "--resource-group", $ResourceGroupName, "--location", $Location, "-o", "none") | Out-Null
    }
    else {
        Write-Host "Container Apps environment '$containerAppsEnvironmentName' already exists. Reusing existing environment." -ForegroundColor Yellow
    }

    Write-Step "Creating or updating Container Apps delivery job"
    $deliveryJobExists = $false
    try {
        $existingDeliveryJob = Get-AzCliJson -Arguments @("containerapp", "job", "show", "--name", $containerAppsJobName, "--resource-group", $ResourceGroupName, "-o", "json")
        $deliveryJobExists = $null -ne $existingDeliveryJob -and -not [string]::IsNullOrWhiteSpace([string]$existingDeliveryJob.id)
    }
    catch {
        $deliveryJobExists = $false
    }

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
    $createJobArguments = @(
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
    ) + $workerEnvVars + @("-o", "none")
    $updateJobArguments = @(
        "containerapp", "job", "update",
        "--name", $containerAppsJobName,
        "--resource-group", $ResourceGroupName,
        "--image", $workerImage,
        "--container-name", $containerAppsJobContainerName,
        "--replace-env-vars"
    ) + $workerEnvVars + @("-o", "none")

    if (-not $deliveryJobExists) {
        Invoke-AzCli -Arguments $createJobArguments | Out-Null
    }
    else {
        Write-Host "Container Apps job '$containerAppsJobName' already exists. Updating configuration." -ForegroundColor Yellow
    }

    Invoke-AzCli -Arguments @(
        "containerapp", "job", "identity", "assign",
        "--name", $containerAppsJobName,
        "--resource-group", $ResourceGroupName,
        "--system-assigned",
        "-o", "none"
    ) | Out-Null
    $deliveryJobPrincipalId = Resolve-ContainerAppJobPrincipalId -JobName $containerAppsJobName -ResourceGroupName $ResourceGroupName

    $deliveryJobResourceId = Get-AzCliTsv -Arguments @("containerapp", "job", "show", "--name", $containerAppsJobName, "--resource-group", $ResourceGroupName, "--query", "id", "-o", "tsv")
    Ensure-RoleAssignment -PrincipalId $deliveryJobPrincipalId -RoleName "AcrPull" -Scope $acrId
    Ensure-RoleAssignment -PrincipalId $functionPrincipalId -RoleName "Container Apps Jobs Operator" -Scope $deliveryJobResourceId

    Set-ContainerAppJobRegistry -JobName $containerAppsJobName -ResourceGroupName $ResourceGroupName -RegistryServer $acrLoginServer -RegistryUsername $acrUsername -RegistryPassword $acrPassword

    if (Test-AcrTagExists -RegistryName $acrName -RepositoryName "lastwrites-worker" -Tag "latest") {
        Invoke-AzCli -Arguments $updateJobArguments | Out-Null
    }
    else {
        Write-Warning "Worker image '$workerImage' was not found in ACR yet. The delivery job will stay on the placeholder image until the worker image is pushed. Run the worker deploy workflow after infra provisioning."
    }

    Write-Step "Granting delivery worker access to Key Vault RSA decrypt operations"
    Invoke-AzCli -Arguments @("keyvault", "set-policy", "--name", $keyVaultName, "--object-id", $deliveryJobPrincipalId, "--key-permissions", "get", "decrypt", "unwrapKey", "-o", "none") | Out-Null

    $backendHost = Get-AzCliTsv -Arguments @("webapp", "show", "--name", $backendAppName, "--resource-group", $ResourceGroupName, "--query", "defaultHostName", "-o", "tsv")
    $frontendHost = Get-AzCliTsv -Arguments @("webapp", "show", "--name", $frontendAppName, "--resource-group", $ResourceGroupName, "--query", "defaultHostName", "-o", "tsv")
    $backendUrl = "https://$backendHost"
    $frontendUrl = "https://$frontendHost"
    $frontendVerifyEmailUrl = "$frontendUrl/verify-email"
    $authSecretKey = New-StrongSecret -ByteLength 48

    Write-Step "Applying runtime settings to backend, frontend, and function apps"
    Invoke-AzCli -Arguments @("webapp", "config", "appsettings", "set", "--name", $backendAppName, "--resource-group", $ResourceGroupName, "--settings", "KEY_VAULT_URL=$keyVaultUrl", "KEY_VAULT_RSA_KEY_SIZE=2048", "KEY_VAULT_RSA_HARDWARE_PROTECTED=false", "COSMOS_DATABASE_NAME=$cosmosDatabaseName", "COSMOS_VAULTS_CONTAINER=$cosmosContainerName", "FRONTEND_ORIGINS=$frontendUrl", "COSMOS_CONNECTION_STRING_SECRET_NAME=COSMOS-CONNECTION-STRING", "BLOB_CONNECTION_STRING_SECRET_NAME=BLOB-CONNECTION-STRING", "AUTH_SECRET_KEY=$authSecretKey", "FRONTEND_VERIFY_EMAIL_URL=$frontendVerifyEmailUrl", "AUTH_ACCESS_TOKEN_TTL_MINUTES=120", "AUTH_REQUIRE_EMAIL_VERIFICATION=true", "AUTH_EXPOSE_VERIFICATION_TOKEN=false", "EMAIL_VERIFICATION_TOKEN_TTL_MINUTES=1440", "AUTH_PASSWORD_PBKDF2_ITERATIONS=260000", "SCM_DO_BUILD_DURING_DEPLOYMENT=true", "ENABLE_ORYX_BUILD=true", "-o", "none") | Out-Null
    Invoke-AzCli -Arguments @("webapp", "config", "appsettings", "set", "--name", $frontendAppName, "--resource-group", $ResourceGroupName, "--settings", "NEXT_PUBLIC_API_URL=$backendUrl", "-o", "none") | Out-Null
    Invoke-AzCli -Arguments @("webapp", "config", "set", "--name", $backendAppName, "--resource-group", $ResourceGroupName, "--startup-file", "python -m uvicorn main:app --host 0.0.0.0 --port 8000", "-o", "none") | Out-Null
    Invoke-AzCli -Arguments @("webapp", "config", "set", "--name", $frontendAppName, "--resource-group", $ResourceGroupName, "--startup-file", "HOSTNAME=0.0.0.0 node /home/site/wwwroot/server.js", "-o", "none") | Out-Null
    Invoke-AzCli -Arguments @("functionapp", "config", "appsettings", "set", "--name", $functionAppName, "--resource-group", $ResourceGroupName, "--settings", "COSMOS_CONNECTION_STRING=$cosmosConnectionString", "COSMOS_DATABASE_NAME=$cosmosDatabaseName", "COSMOS_VAULTS_CONTAINER=$cosmosContainerName", "EVENT_GRID_ENDPOINT=$eventGridEndpoint", "EVENT_GRID_KEY=$eventGridKey", "BLOB_CONNECTION_STRING=$blobConnectionString", "AZURE_SUBSCRIPTION_ID=$subscriptionId", "CONTAINER_APPS_RESOURCE_GROUP=$ResourceGroupName", "CONTAINER_APPS_JOB_NAME=$containerAppsJobName", "CONTAINER_APPS_API_VERSION=2024-03-01", "-o", "none") | Out-Null

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

        $backendPublishProfile = Get-AzCliRaw -Arguments @("webapp", "deployment", "list-publishing-profiles", "--name", $backendAppName, "--resource-group", $ResourceGroupName, "--xml")
        $frontendPublishProfile = Get-AzCliRaw -Arguments @("webapp", "deployment", "list-publishing-profiles", "--name", $frontendAppName, "--resource-group", $ResourceGroupName, "--xml")
        $resourceGroupScope = "/subscriptions/$subscriptionId/resourceGroups/$ResourceGroupName"
        $githubActionsPrincipalName = "gha-$prefixDashed-$token"
        $azureCredentialsJson = Get-OrCreateGithubActionsAzureCredentialsJson -ResourceGroupScope $resourceGroupScope -PrincipalDisplayName $githubActionsPrincipalName

        Set-OrUpdateGithubSecret -Name "AZURE_BACKEND_WEBAPP_NAME" -Value $backendAppName -Repo $GithubRepo
        Set-OrUpdateGithubSecret -Name "AZURE_BACKEND_WEBAPP_PUBLISH_PROFILE" -Value $backendPublishProfile -Repo $GithubRepo

        Set-OrUpdateGithubSecret -Name "AZURE_FRONTEND_WEBAPP_NAME" -Value $frontendAppName -Repo $GithubRepo
        Set-OrUpdateGithubSecret -Name "AZURE_FRONTEND_WEBAPP_PUBLISH_PROFILE" -Value $frontendPublishProfile -Repo $GithubRepo
        Set-OrUpdateGithubSecret -Name "NEXT_PUBLIC_API_URL" -Value $backendUrl -Repo $GithubRepo

        Set-OrUpdateGithubSecret -Name "AZURE_FUNCTIONAPP_NAME" -Value $functionAppName -Repo $GithubRepo
        Set-OrUpdateGithubSecret -Name "AZURE_CREDENTIALS" -Value $azureCredentialsJson -Repo $GithubRepo

        Set-OrUpdateGithubSecret -Name "ACR_LOGIN_SERVER" -Value $acrLoginServer -Repo $GithubRepo
        Set-OrUpdateGithubSecret -Name "ACR_USERNAME" -Value $acrUsername -Repo $GithubRepo
        Set-OrUpdateGithubSecret -Name "ACR_PASSWORD" -Value $acrPassword -Repo $GithubRepo
        Set-OrUpdateGithubSecret -Name "AZURE_CONTAINERAPPS_RESOURCE_GROUP" -Value $ResourceGroupName -Repo $GithubRepo
        Set-OrUpdateGithubSecret -Name "AZURE_CONTAINERAPPS_JOB_NAME" -Value $containerAppsJobName -Repo $GithubRepo
    }

    if ($RerunWorkflowRuns) {
        Write-Step "Re-running latest workflow runs"
        $workflows = @("deploy-backend.yml", "deploy-frontend.yml", "deploy-functions.yml", "deploy-worker.yml")
        $queuedRuns = New-Object System.Collections.Generic.List[object]

        foreach ($workflow in $workflows) {
            $runId = Get-GhRaw -Arguments @("run", "list", "--repo", $GithubRepo, "--workflow", $workflow, "--limit", "1", "--json", "databaseId", "-q", ".[0].databaseId")
            if (-not [string]::IsNullOrWhiteSpace($runId)) {
                Invoke-GhCli -Arguments @("run", "rerun", $runId, "--repo", $GithubRepo) | Out-Null
                $queuedRuns.Add([pscustomobject]@{ Workflow = $workflow; RunId = $runId }) | Out-Null
            }
            else {
                Write-Warning "No previous run was found for '$workflow'. Dispatching a new workflow run instead."
                Invoke-GhCli -Arguments @("workflow", "run", $workflow, "--repo", $GithubRepo, "--ref", "main") | Out-Null
                Start-Sleep -Seconds 5
                $dispatchedRunId = Get-GhRaw -Arguments @("run", "list", "--repo", $GithubRepo, "--workflow", $workflow, "--limit", "1", "--json", "databaseId", "-q", ".[0].databaseId")
                if (-not [string]::IsNullOrWhiteSpace($dispatchedRunId)) {
                    $queuedRuns.Add([pscustomobject]@{ Workflow = $workflow; RunId = $dispatchedRunId }) | Out-Null
                }
            }
        }

        Write-Step "Waiting for GitHub Actions workflows to finish"
        foreach ($queuedRun in $queuedRuns) {
            Wait-GithubWorkflowRun -RunId ([string]$queuedRun.RunId) -Repo $GithubRepo -WorkflowName ([string]$queuedRun.Workflow)
        }
    }
    else {
        Write-Warning "Infrastructure is ready, but GitHub workflow re-run was skipped. Automatic application deployment and delivery automation can remain incomplete until backend, frontend, functions, and worker workflows finish successfully."
    }

    Write-Step "Ensuring Event Grid subscription to start_delivery_job function"
    if (-not (Wait-FunctionAvailable -FunctionAppName $functionAppName -ResourceGroupName $ResourceGroupName -FunctionName "start_delivery_job")) {
        throw "Azure Function 'start_delivery_job' was not detected in '$functionAppName' after deployment."
    }

    $eventGridTopicId = Get-AzCliTsv -Arguments @(
        "eventgrid", "topic", "show",
        "--name", $eventGridTopicName,
        "--resource-group", $ResourceGroupName,
        "--query", "id",
        "-o", "tsv"
    )
    $startDeliveryJobFunctionResourceId = "/subscriptions/$subscriptionId/resourceGroups/$ResourceGroupName/providers/Microsoft.Web/sites/$functionAppName/functions/start_delivery_job"

    if (-not (Test-EventGridSubscriptionExists -EventSubscriptionName $eventSubscriptionName -SourceResourceId $eventGridTopicId)) {
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
        Write-Host "Event subscription '$eventSubscriptionName' already exists. Updating endpoint to start_delivery_job." -ForegroundColor Yellow
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

    Write-Step "Validating delivery automation prerequisites"
    $deliveryReadinessIssues = New-Object System.Collections.Generic.List[string]

    if (-not (Test-FunctionExists -FunctionAppName $functionAppName -ResourceGroupName $ResourceGroupName -FunctionName "start_delivery_job")) {
        $deliveryReadinessIssues.Add("Azure Function 'start_delivery_job' is not deployed in '$functionAppName'.")
    }

    if (-not (Test-AcrTagExists -RegistryName $acrName -RepositoryName "lastwrites-worker" -Tag "latest")) {
        $deliveryReadinessIssues.Add("Worker image '$acrLoginServer/lastwrites-worker:latest' is missing from ACR.")
    }

    $eventGridTopicId = Get-AzCliTsv -Arguments @(
        "eventgrid", "topic", "show",
        "--name", $eventGridTopicName,
        "--resource-group", $ResourceGroupName,
        "--query", "id",
        "-o", "tsv"
    )
    if (-not (Test-EventGridSubscriptionExists -EventSubscriptionName $eventSubscriptionName -SourceResourceId $eventGridTopicId)) {
        $deliveryReadinessIssues.Add("Event Grid subscription '$eventSubscriptionName' is not attached to topic '$eventGridTopicName'.")
    }

    if ($deliveryReadinessIssues.Count -gt 0) {
        $joinedIssues = $deliveryReadinessIssues -join [Environment]::NewLine
        throw "Deployment finished provisioning infrastructure, but the application delivery pipeline is incomplete:`n$joinedIssues`nRerun after publishing application code (backend, frontend, functions, worker) so the environment becomes fully operational."
    }

    $resourceGroupPortalUrl = "https://portal.azure.com/#resource/subscriptions/$subscriptionId/resourceGroups/$ResourceGroupName/overview"

    Write-Host "`nInfrastructure deployment complete." -ForegroundColor Green
    if ($RerunWorkflowRuns) {
        Write-Host "GitHub Actions deployment workflows were queued. The URLs may take a few minutes to become healthy." -ForegroundColor Yellow
    }
    Write-Host "Resource Group: $ResourceGroupName"
    Write-Host "Backend URL:   $backendUrl"
    Write-Host "Frontend URL:  $frontendUrl"
    Write-Host "Function App:  $functionAppName"
    Write-Host "Job Env:       $containerAppsEnvironmentName"
    Write-Host "Delivery Job:  $containerAppsJobName"
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
