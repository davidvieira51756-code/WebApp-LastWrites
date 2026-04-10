$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDir = Join-Path $projectRoot "backend"
$frontendDir = Join-Path $projectRoot "frontend"
$functionsDir = Join-Path $projectRoot "functions"
$backendEnvFile = Join-Path $backendDir ".env"
$backendEnvExample = Join-Path $backendDir ".env.example"
$frontendEnvFile = Join-Path $frontendDir ".env.local"
$frontendEnvExample = Join-Path $frontendDir ".env.local.example"

function Start-DevWindow {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Title,
        [Parameter(Mandatory = $true)]
        [string]$WorkingDirectory,
        [Parameter(Mandatory = $true)]
        [string]$Command
    )

    $windowScript = @"
`$host.UI.RawUI.WindowTitle = '$Title'
Set-Location '$WorkingDirectory'
$Command
"@

    Start-Process -FilePath "powershell.exe" `
        -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-Command", $windowScript `
        -WorkingDirectory $WorkingDirectory
}

function Ensure-FileFromExample {
    param(
        [Parameter(Mandatory = $true)]
        [string]$TargetPath,
        [Parameter(Mandatory = $true)]
        [string]$ExamplePath
    )

    if ((-not (Test-Path $TargetPath)) -and (Test-Path $ExamplePath)) {
        Copy-Item -Path $ExamplePath -Destination $TargetPath
        Write-Host "Created template file: $TargetPath" -ForegroundColor Yellow
    }
}

function Get-DotEnvMap {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath
    )

    $values = @{}
    if (-not (Test-Path $FilePath)) {
        return $values
    }

    foreach ($rawLine in Get-Content -Path $FilePath) {
        $line = $rawLine.Trim()
        if ([string]::IsNullOrWhiteSpace($line)) {
            continue
        }
        if ($line.StartsWith("#")) {
            continue
        }
        if (-not $line.Contains("=")) {
            continue
        }

        $parts = $line.Split("=", 2)
        $key = $parts[0].Trim()
        $value = $parts[1].Trim()
        if ($key) {
            $values[$key] = $value
        }
    }

    return $values
}

function Test-ConnectionStringFormat {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ConnectionString,
        [Parameter(Mandatory = $true)]
        [string[]]$RequiredTokens
    )

    if ([string]::IsNullOrWhiteSpace($ConnectionString)) {
        return $false
    }

    foreach ($token in $RequiredTokens) {
        if ($ConnectionString -notlike "*$token*") {
            return $false
        }
    }

    return $true
}

if (-not (Test-Path $backendDir)) {
    throw "Backend directory not found at: $backendDir"
}
if (-not (Test-Path $frontendDir)) {
    throw "Frontend directory not found at: $frontendDir"
}
if (-not (Test-Path $functionsDir)) {
    throw "Functions directory not found at: $functionsDir"
}

Ensure-FileFromExample -TargetPath $backendEnvFile -ExamplePath $backendEnvExample
Ensure-FileFromExample -TargetPath $frontendEnvFile -ExamplePath $frontendEnvExample

if (-not (Test-Path $backendEnvFile)) {
    throw "Missing backend .env file. Create backend/.env and set required values before running."
}

$backendEnv = Get-DotEnvMap -FilePath $backendEnvFile
$localDevMode = (($backendEnv["LOCAL_DEV_MODE"] | ForEach-Object { "$_".Trim().ToLower() }) -in @("1", "true", "yes", "on"))
$cosmosConnectionString = [string]$backendEnv["COSMOS_CONNECTION_STRING"]
$blobConnectionString = [string]$backendEnv["BLOB_CONNECTION_STRING"]
$missingBackendVars = @()
if (-not $localDevMode) {
    foreach ($requiredVar in @("COSMOS_CONNECTION_STRING", "BLOB_CONNECTION_STRING")) {
        if ((-not $backendEnv.ContainsKey($requiredVar)) -or [string]::IsNullOrWhiteSpace($backendEnv[$requiredVar])) {
            $missingBackendVars += $requiredVar
        }
    }
}

$preflightErrors = @()
if ($missingBackendVars.Count -gt 0) {
    $preflightErrors += "Missing required values in backend/.env: $($missingBackendVars -join ', ')"
}

if (
    (-not $localDevMode) -and
    (-not [string]::IsNullOrWhiteSpace($cosmosConnectionString)) -and
    (-not (Test-ConnectionStringFormat -ConnectionString $cosmosConnectionString -RequiredTokens @("AccountEndpoint=", "AccountKey=")))
) {
    $preflightErrors += "COSMOS_CONNECTION_STRING in backend/.env does not look valid (expected AccountEndpoint and AccountKey)."
}

if (
    (-not $localDevMode) -and
    (-not [string]::IsNullOrWhiteSpace($blobConnectionString)) -and
    (-not (Test-ConnectionStringFormat -ConnectionString $blobConnectionString -RequiredTokens @("DefaultEndpointsProtocol=", "AccountName=", "AccountKey=")))
) {
    $preflightErrors += "BLOB_CONNECTION_STRING in backend/.env does not look valid (expected DefaultEndpointsProtocol, AccountName, and AccountKey)."
}

$funcCommand = Get-Command func -ErrorAction SilentlyContinue
$shouldStartFunctions = $null -ne $funcCommand

if (-not $shouldStartFunctions) {
    Write-Host "Azure Functions Core Tools not found. Functions host will be skipped." -ForegroundColor Yellow
}

if ($preflightErrors.Count -gt 0) {
    $errorMessage = "Pre-flight checks failed:" + [Environment]::NewLine + " - " + ($preflightErrors -join ([Environment]::NewLine + " - "))
    throw $errorMessage
}

$backendCommand = @"
if (-not (Test-Path '.\.venv\Scripts\python.exe')) { python -m venv .venv }
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --port 8000 --env-file .env
"@

$frontendCommand = @"
npm install
npm run dev
"@

$functionsCommand = @"
if (-not (Test-Path '.\.venv\Scripts\python.exe')) { python -m venv .venv }
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
`$env:languageWorkers__python__defaultExecutablePath = (Resolve-Path '.\.venv\Scripts\python.exe').Path
func start
"@

Start-DevWindow `
    -Title "Last Writes - Backend" `
    -WorkingDirectory $backendDir `
    -Command $backendCommand

Start-DevWindow `
    -Title "Last Writes - Frontend" `
    -WorkingDirectory $frontendDir `
    -Command $frontendCommand

if ($shouldStartFunctions) {
    Start-DevWindow `
        -Title "Last Writes - Functions" `
        -WorkingDirectory $functionsDir `
        -Command $functionsCommand
}

Write-Host ""
Write-Host "  HEAVY WORKER (DOCKER) " -ForegroundColor Black -BackgroundColor Yellow
Write-Host "Run these commands from project root when you need to trigger heavy processing:" -ForegroundColor Yellow
Write-Host "docker build -t last-writes-worker ./worker_container" -ForegroundColor Cyan
Write-Host "docker run --env-file ./worker_container/.env last-writes-worker" -ForegroundColor Cyan
Write-Host ""
