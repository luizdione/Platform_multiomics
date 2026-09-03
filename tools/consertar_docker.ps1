# consertar_docker.ps1 — conserto do Docker Desktop após queda suja (Windows).
#
# Sintoma: Docker Desktop fecha com "unexpected error" citando um socket que não
# consegue remover (Inference manager, Secrets Engine, dockerInference,
# engine.sock...) ou "invalid character '\x00'" num config.
# Causa: desligamento sujo deixa sockets AF_UNIX órfãos INDELETÁVEIS e/ou
# configs zerados com null-bytes. Diagnóstico completo: memória do ambiente
# `null-bytes-fora-do-onedrive-docker` (2026-09-02/03).
# Conserto definitivo p/ sockets acumulados: REINICIAR O WINDOWS. Este script é
# o paliativo que destrava a subida sem reboot.
#
# Uso: botão direito → Executar com PowerShell (ou: pwsh tools\consertar_docker.ps1)

$ErrorActionPreference = "SilentlyContinue"
Write-Host "1/4 Encerrando processos do Docker..."
Get-Process *docker* | Stop-Process -Force
Start-Sleep 2

Write-Host "2/4 Limpando ninhos de sockets orfaos (rename — deletar falha no NTFS)..."
$stamp = Get-Date -Format "yyyyMMdd-HHmm"
$ninhos = @(
  "$env:LOCALAPPDATA\Docker\run",
  "$env:LOCALAPPDATA\docker-secrets-engine"
)
foreach ($n in $ninhos) {
  if ((Test-Path $n) -and (Get-ChildItem $n -Force).Count -gt 0) {
    Move-Item $n "$n.corrompido-$stamp" -Force
    New-Item -ItemType Directory $n | Out-Null
    Write-Host "   limpo: $n"
  }
}
foreach ($f in "backend.lock", "frontend.lock", "launcher.lock") {
  Remove-Item "$env:LOCALAPPDATA\Docker\$f" -Force
}

Write-Host "3/4 Checando configs por null-bytes..."
foreach ($cfg in "$env:USERPROFILE\.docker\daemon.json", "$env:USERPROFILE\.docker\windows-daemon.json") {
  if (Test-Path $cfg) {
    $b = [IO.File]::ReadAllBytes($cfg)
    if ($b.Length -gt 0 -and (($b | Where-Object { $_ -eq 0 }).Count) -eq $b.Length) {
      Move-Item $cfg "$cfg.corrompido-nulls-$stamp" -Force
      if ($cfg -like "*windows-daemon*") { [IO.File]::WriteAllText($cfg, "{}`n") }
      else { [IO.File]::WriteAllText($cfg, '{"builder":{"gc":{"defaultKeepStorage":"20GB","enabled":true}},"experimental":false}' + "`n") }
      Write-Host "   reescrito (estava 100% nulo): $cfg"
    }
  }
}

Write-Host "4/4 Relancando Docker Desktop e aguardando o daemon (ate 4 min)..."
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
foreach ($i in 1..80) {
  Start-Sleep 3
  docker info --format "{{.ServerVersion}}" 2>$null | Out-Null
  if ($LASTEXITCODE -eq 0) {
    Write-Host "OK: daemon respondendo apos $($i*3)s. Suba a plataforma com abrir_omica.bat."
    exit 0
  }
}
Write-Host "Daemon nao respondeu em 4 min. Conserto definitivo: reiniciar o Windows."
exit 1
