# install_soak_task.ps1 — registra un soak diario de conflux como tarea
# programada de Windows. La máquina del usuario ES el entorno de
# producción de un proxy local: un soak multi-hora aquí mide
# estabilidad donde realmente corre el servicio (los runners de CI
# tienen cap de ~6h).
#
# Uso (PowerShell normal, sin admin):
#   powershell -File scripts\install_soak_task.ps1 [-Minutes 180] [-Workers 4] [-At 04:00]
#   powershell -File scripts\install_soak_task.ps1 -Remove
param(
    [int]$Minutes = 180,
    [int]$Workers = 4,
    [string]$At = '04:00',
    [switch]$Remove
)

$TaskName = 'ConfluxSoak'
$Repo = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $Repo 'soak-logs'

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Tarea $TaskName eliminada"
    exit 0
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# python del repo (.venv si existe, si no el de PATH)
$Py = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) { $Py = (Get-Command python).Source }

# el soak corre en modo light por defecto: suficiente carga para medir
# fugas/latencia sin martillear upstreams durante horas.
$Cmd = "`"$Py`" `"$Repo\scripts\soak.py`" $Minutes $Workers --light >> `"$LogDir\soak-`$(Get-Date -Format yyyyMMdd).log`" 2>&1"

$Action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c $Cmd" -WorkingDirectory $Repo
$Trigger = New-ScheduledTaskTrigger -Daily -At $At
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes ($Minutes + 20))

Register-ScheduledTask -TaskName $TaskName -Action $Action `
    -Trigger $Trigger -Settings $Settings `
    -Description "Soak ${Minutes}min x${Workers}w de conflux (scripts/soak.py) — log en soak-logs\" | Out-Null

Write-Host "Tarea '$TaskName' registrada: diaria a las $At, soak de ${Minutes}min x${Workers} workers"
Write-Host "Logs: $LogDir"
Write-Host "Ejecutar ahora:  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Quitar:          powershell -File $PSCommandPath -Remove"
