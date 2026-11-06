# setup_win.ps1 — monta el entorno completo de conflux en una máquina Windows.
# Uso:  powershell -ExecutionPolicy Bypass -File scripts\setup_win.ps1
#       .\scripts\setup_win.ps1 -NoAddon            (solo el proxy, sin tocar Stremio)
#       .\scripts\setup_win.ps1 -Env backup.tar.gz  (restaura config/sources del backup)
# También restaura automáticamente un dir env-backup\ descomprimido en el repo.
# Equivalente Windows de setup.sh: en vez de systemd usa un acceso directo en la
# carpeta Inicio del usuario (arranca el proxy al iniciar sesión, sin admin).
param(
    [switch]$NoAddon,
    [string]$Env,
    [switch]$Prowlarr       # instalar Prowlarr y conectar torznab automáticamente
)
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')          # raíz del repo

Write-Host '== entorno (config/sources) =='
if ($Env) {
    tar -xzf $Env -C .                               # bsdtar viene con Windows 10+
    Write-Host "restaurado desde $Env"
} elseif (Test-Path 'env-backup') {
    Copy-Item env-backup\* . -ErrorAction SilentlyContinue
    Write-Host 'restaurado desde env-backup\'
} else {
    Write-Host 'sin backup — defaults del repo'
}

Write-Host '== python =='
$py = $null
foreach ($c in @('py -3', 'python3', 'python')) {
    $exe, $arg = $c -split ' ', 2
    $v = & $exe $arg -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>$null
    if ($LASTEXITCODE -eq 0 -and [version]$v -ge [version]'3.10') { $py = $c; break }
}
if (-not $py) {
    Write-Host 'python >=3.10 no encontrado — instalando con winget…'
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    $py = 'py -3'
}
Write-Host "usando: $py"

Write-Host '== dependencias =='
Invoke-Expression "$py -m venv .venv"
$venvPy = Join-Path $PWD '.venv\Scripts\python.exe'
& $venvPy -m pip install --disable-pip-version-check -q -r requirements.txt

Write-Host '== integraciones (.env) =='
$envFile = Join-Path $PWD '.env'
$envCfg = @{}
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*#' -or $_ -notmatch '=') { return }
        $k, $v = $_ -split '=', 2
        $envCfg[$k.Trim().ToUpper()] = $v.Trim().Trim('"').Trim("'")
    }
} else {
    Write-Host 'sin .env — copia .env.example a .env para debrid/torznab/subs…'
}

# Prowlarr: instala el indexer manager y saca su ApiKey del config.xml → torznab
$prowlarrOn = $Prowlarr -or $envCfg['PROWLARR'] -eq '1'
if ($prowlarrOn) {
    Write-Host '== prowlarr (torznab) =='
    $svc = Get-Service Prowlarr -ErrorAction SilentlyContinue
    if (-not $svc) {
        Write-Host 'instalando Prowlarr con winget (puede pedir UAC)…'
        winget install -e --id TeamProwlarr.Prowlarr --accept-source-agreements --accept-package-agreements
        Start-Sleep -Seconds 15
    }
    foreach ($i in 1..25) {
        try { Invoke-WebRequest 'http://127.0.0.1:9696/ping' -TimeoutSec 3 -UseBasicParsing | Out-Null; break }
        catch { Start-Sleep -Seconds 3 }
    }
    $pcfg = "$env:ProgramData\Prowlarr\config.xml"
    if (Test-Path $pcfg) {
        $ak = ([xml](Get-Content $pcfg)).Config.ApiKey
        if ($ak -and -not $envCfg['TORZNAB_KEY']) {
            Write-Host 'ApiKey detectada — conectando torznab'
            $envCfg['TORZNAB_KEY'] = $ak
            if (-not (Test-Path $envFile)) { New-Item $envFile -ItemType File | Out-Null }
            "TORZNAB_URL=http://127.0.0.1:9696/0/api`nTORZNAB_KEY=$ak" |
                Add-Content $envFile
        }
    }
}

if (Test-Path $envFile) {
    & $venvPy scripts\apply_env.py $envFile
}

# Sembrar indexers públicos por API (PROWLARR_INDEXERS="a,b,c" personaliza la lista)
if ($prowlarrOn -and $envCfg['TORZNAB_KEY']) {
    Write-Host '== sembrando indexers públicos en Prowlarr =='
    & $venvPy scripts\prowlarr_indexers.py
}

Write-Host '== lanzador + autostart =='
$bat = Join-Path $PWD 'start-conflux.bat'
@"
@echo off
cd /d "%~dp0"
if exist .venv\Scripts\pythonw.exe (
  start "" /min "%~dp0.venv\Scripts\pythonw.exe" proxy.py
) else (
  start "" /min pythonw.exe proxy.py
)
"@ | Set-Content -Encoding ASCII $bat
$startup = [Environment]::GetFolderPath('Startup')
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut((Join-Path $startup 'Conflux.lnk'))
$lnk.TargetPath = $bat
$lnk.WindowStyle = 7          # minimizada
$lnk.WorkingDirectory = $PWD
$lnk.Save()
Write-Host "autostart: $startup\Conflux.lnk"

Write-Host '== proxy =='
& $bat | Out-Null
$ok = $false
foreach ($i in 1..30) {
    Start-Sleep -Seconds 2
    try { Invoke-RestMethod 'http://127.0.0.1:9700/health' -TimeoutSec 5 | Out-Null; $ok = $true; break }
    catch { }
}
if ($ok) { Write-Host 'proxy: OK (127.0.0.1:9700)' } else { Write-Warning 'el proxy no responde — revisa conflux.log' }

if (-not $NoAddon) {
    Write-Host '== addon en Stremio =='
    $stremio = @(
        "$env:LOCALAPPDATA\Programs\LNV\Stremio-4\stremio.exe",
        "$env:LOCALAPPDATA\Programs\Stremio\stremio.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($stremio) {
        & $stremio | Out-Null
        Start-Sleep -Seconds 8
        Start-Process 'stremio://127.0.0.1:9700/manifest.json'
        Write-Host 'Stremio abierto — confirma «Instalar» en el diálogo del addon'
    } else {
        Write-Host 'Stremio no encontrado — instálalo (winget install Stremio.Stremio) y abre:'
        Write-Host '  stremio://127.0.0.1:9700/manifest.json'
    }
}

Write-Host '== listo =='
Write-Host 'Descubrir -> cada pestaña muestra los catálogos unificados; Calendario -> series/anime/estrenos.'
