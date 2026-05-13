param(
    [string]$HostName = "0.0.0.0",
    [int]$Port = 8787,
    [string]$OllamaBaseUrl = "http://localhost:11434",
    [string]$ProjectsDir = ""
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $ScriptDir
if (-not $ProjectsDir) {
    $ProjectsDir = Split-Path -Parent $Root
}

$LogDir = Join-Path $Root "data\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir "web.out.log"

try {
    "[$(Get-Date -Format s)] Starting DevServer Web" | Out-File -FilePath $LogFile -Encoding utf8
    "Root: $Root" | Out-File -FilePath $LogFile -Encoding utf8 -Append
    "Projects: $ProjectsDir" | Out-File -FilePath $LogFile -Encoding utf8 -Append
    "Host: $HostName" | Out-File -FilePath $LogFile -Encoding utf8 -Append
    "Port: $Port" | Out-File -FilePath $LogFile -Encoding utf8 -Append

    $env:DEVSERVER_WEB_HOST = $HostName
    $env:DEVSERVER_WEB_PORT = [string]$Port
    $env:OLLAMA_BASE_URL = $OllamaBaseUrl
    $env:DEVSERVER_START_CWD = $ProjectsDir
    $env:DEVSERVER_PROJECTS_DIR = $ProjectsDir

    $Python = Join-Path $Root "venv\Scripts\python.exe"
    if (-not (Test-Path $Python)) {
        $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if (-not $PythonCommand) {
            throw "Python was not found. Create the venv or add Python to PATH."
        }
        $Python = $PythonCommand.Source
    }

    "Python: $Python" | Out-File -FilePath $LogFile -Encoding utf8 -Append

    Set-Location $Root
    $ErrorActionPreference = "Continue"
    & $Python "ollama_cli.py" web >> $LogFile 2>&1
    exit $LASTEXITCODE
} catch {
    "[$(Get-Date -Format s)] Failed to start DevServer Web" | Out-File -FilePath $LogFile -Encoding utf8 -Append
    ($_ | Out-String) | Out-File -FilePath $LogFile -Encoding utf8 -Append
    exit 1
}
