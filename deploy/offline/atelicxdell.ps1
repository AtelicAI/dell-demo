# Atelic x Dell methane demo: offline laptop launcher (Windows). macOS / Linux: atelicxdell.sh.
#   .\atelicxdell.ps1            update if online, start, load the data once, open the browser
#   .\atelicxdell.ps1 stop       stop the demo (data kept)
#   .\atelicxdell.ps1 reset      wipe this demo's data and start fresh
#   .\atelicxdell.ps1 status     show the containers
#   .\atelicxdell.ps1 logs       follow the logs
# Needs Docker Desktop running and ghcr-token.txt (read-only registry token) beside this script.
param([string]$Command = "start")
$ErrorActionPreference = "Stop"
$DemoName = "atelic-dell"
$HttpPort = 8080
$Registry = "ghcr.io/atelicai"
$Tag = "offline"
$LoginUser = "atelic"
$LoginPassword = "atelic-demo"
$Here = $PSScriptRoot
$State = Join-Path $env:LOCALAPPDATA "Atelic\$DemoName"
$Engine = "$Registry/dell-demo-engine:$Tag"
New-Item -ItemType Directory -Force -Path $State | Out-Null
$Compose = Join-Path $State "compose.yml"
$EnvFile = Join-Path $State ".env"
$Seeded = Join-Path $State "seeded"

function Dc { & docker compose --project-directory $State -f $Compose --env-file $EnvFile @args; if ($LASTEXITCODE -ne 0) { throw "docker compose $($args -join ' ') failed" } }
function Say($msg) { Write-Host "`n== $msg" -ForegroundColor Cyan }
function Die($msg) { Write-Host "`nERROR: $msg" -ForegroundColor Red; exit 1 }
function Rand([int]$bytes) { $b = New-Object byte[] $bytes; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); -join ($b | ForEach-Object { '{0:x2}' -f $_ }) }
# UTF-8 without a byte-order mark (Windows PowerShell 5.1's -Encoding utf8 writes one, which compose does not expect).
function Write-Text($path, $text) { [IO.File]::WriteAllText($path, $text, (New-Object Text.UTF8Encoding $false)) }

& docker info *> $null
if ($LASTEXITCODE -ne 0) { Die "Docker is not running. Open Docker Desktop, wait for 'Engine running', then run this again." }

if (-not (Test-Path $EnvFile)) {
  @(
    "DEMO_NAME=$DemoName", "HTTP_PORT=$HttpPort", "REGISTRY=$Registry", "TAG=$Tag",
    "NEO4J_PASSWORD=$(Rand 24)", "INFLUX_PASSWORD=$(Rand 24)", "INFLUX_TOKEN=$(Rand 24)", "POSTGRES_PASSWORD=$(Rand 24)",
    "WORKFLOW_RUNTIME_PASSWORD=$(Rand 24)", "QDRANT_API_KEY=$(Rand 24)", "ENGINEER_TOKEN=$(Rand 32)", "SESSION_SECRET=$(Rand 32)",
    "LOGIN_USER=$LoginUser", "LOGIN_PASSWORD=$LoginPassword"
  ) | Set-Content -Path $EnvFile -Encoding ascii
}

# Update from the registry when online; otherwise run what is already on this laptop.
function Update {
  $token = Join-Path $Here "ghcr-token.txt"
  if (-not (Test-Path $token)) { Write-Host "No ghcr-token.txt beside the launcher: skipping the update check."; return }
  (Get-Content $token -Raw).Trim() | & docker login ghcr.io -u atelic-demo --password-stdin *> $null
  if ($LASTEXITCODE -ne 0) { Write-Host "Registry not reachable (offline?): using the version already on this laptop."; return }
  & docker pull -q $Engine *> $null
  if ($LASTEXITCODE -ne 0) { Write-Host "Update download failed: using the version already on this laptop."; return }
  $yml = (& docker run --rm --entrypoint cat $Engine /srv/offline/compose.yml) -join "`n"
  if ($LASTEXITCODE -eq 0 -and $yml) { Write-Text $Compose ($yml + "`n") }
  # A newer launcher takes effect on the next run.
  $new = (& docker run --rm --entrypoint cat $Engine /srv/offline/atelicxdell.ps1) -join "`r`n"
  if ($LASTEXITCODE -eq 0 -and $new -and $new.Trim() -ne (Get-Content $PSCommandPath -Raw).Trim()) {
    Write-Text $PSCommandPath ($new + "`r`n")
    Write-Host "Launcher updated (used from the next run)."
  }
  Dc pull -q
}

function Start-Demo {
  Say "Checking for updates"
  try { Update } catch { Write-Host "Update skipped: $($_.Exception.Message)" }
  if (-not (Test-Path $Compose)) { Die "The demo has never been downloaded. Connect to the internet and run this again." }
  Say "Starting the demo (first start takes a few minutes)"
  try { Dc up -d --remove-orphans } catch { Die "Could not start the containers (see the messages above)." }
  $image = (& docker image inspect -f '{{.Id}}' $Engine).Trim()
  $done = if (Test-Path $Seeded) { (Get-Content $Seeded -Raw).Trim() } else { "" }
  if ($done -ne $image) {
    Say "Loading the demo data (once per version, about 2 minutes)"
    try { Dc --profile seed run --rm seed } catch { Die "Loading the data failed (see the messages above). Run this again to retry." }
    Dc restart engine backend | Out-Null
    $image | Set-Content -Path $Seeded -Encoding ascii
  }
  Say "Waiting for the demo to answer"
  $url = "http://localhost:$HttpPort"
  $up = $false
  for ($i = 0; $i -lt 120 -and -not $up; $i++) {
    try { $up = (Invoke-WebRequest -Uri "$url/login.html" -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200 } catch { Start-Sleep -Seconds 2 }
  }
  if (-not $up) { Die "The demo did not come up. Try: .\atelicxdell.ps1 logs" }
  # Every start is a clean slate: no open or closed incidents, cameras watching.
  try {
    $login = @{ username = $LoginUser; password = $LoginPassword } | ConvertTo-Json
    Invoke-WebRequest -Uri "$url/api/login" -Method Post -ContentType "application/json" -Body $login -SessionVariable web -UseBasicParsing | Out-Null
    Invoke-WebRequest -Uri "$url/api/demo/reset" -Method Post -ContentType "application/json" -Body "{}" -WebSession $web -UseBasicParsing | Out-Null
  } catch { Write-Host "Could not reset the demo; use Reset demo in the app." }
  Say "Ready: $url   (sign in: $LoginUser / $LoginPassword)"
  Start-Process $url
}

switch ($Command) {
  "start"  { Start-Demo }
  "stop"   { Dc down; Write-Host "Stopped. Data is kept; run .\atelicxdell.ps1 to start again." }
  "reset"  { Dc down -v; Remove-Item -Force -ErrorAction SilentlyContinue $Seeded; Start-Demo }
  "status" { Dc ps }
  "logs"   { Dc logs -f --tail 100 }
  default  { Die "Unknown command '$Command'. Use: start, stop, reset, status or logs." }
}
