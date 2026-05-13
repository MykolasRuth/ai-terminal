param(
    [string]$TaskName = "DevServer AI Terminal Web",
    [string]$HostName = "0.0.0.0",
    [int]$Port = 8787,
    [string]$OllamaBaseUrl = "http://localhost:11434",
    [string]$ProjectsDir = "",
    [switch]$AtLogon
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$StartScript = Join-Path $ScriptDir "start-web.ps1"

if (-not (Test-Path $StartScript)) {
    throw "Missing start script: $StartScript"
}

$Arguments = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", "`"$StartScript`"",
    "-HostName", "`"$HostName`"",
    "-Port", $Port,
    "-OllamaBaseUrl", "`"$OllamaBaseUrl`""
)

if ($ProjectsDir) {
    $Arguments += @("-ProjectsDir", "`"$ProjectsDir`"")
}

$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument ($Arguments -join " ")
$Trigger = if ($AtLogon) {
    New-ScheduledTaskTrigger -AtLogOn
} else {
    New-ScheduledTaskTrigger -AtStartup
}
$CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$LogonType = if ($AtLogon) { "Interactive" } else { "S4U" }
$Principal = New-ScheduledTaskPrincipal -UserId $CurrentUser -LogonType $LogonType -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Days 30) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -ErrorAction Stop `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop

Write-Host "Installed scheduled task: $TaskName"
Write-Host "User: $CurrentUser"
Write-Host "Trigger: $(if ($AtLogon) { 'At logon' } else { 'At startup' })"
Write-Host "URL: http://<server-tailscale-name-or-ip>:$Port"
Write-Host "Logs: data\logs\web.out.log"
