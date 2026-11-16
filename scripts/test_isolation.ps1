<#
  Teste manual de isolamento (rode com a VPN do ANTI CANJA CONECTADA).
  Copia o curl.exe do Windows para dentro da pasta do Discord: para o
  roteador, ele "vira" um processo do Discord. Compara os IPs públicos.
#>
$ErrorActionPreference = "Stop"
$sys = "$env:WINDIR\System32\curl.exe"
$dir = Join-Path $env:LOCALAPPDATA ("Discord\anticanja-selftest-" + [guid]::NewGuid().ToString("N").Substring(0,8))
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$fake = Join-Path $dir "curl.exe"
Copy-Item $sys $fake
try {
    $direct = & $sys -s -4 --max-time 15 https://api.ipify.org
    $viaDiscord = & $fake -s -4 --max-time 15 https://api.ipify.org
} finally {
    Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Host "IP de apps comuns (direto):  $direct"
Write-Host "IP de processo do Discord:   $viaDiscord"
if (-not $viaDiscord) { Write-Host "Discord sem saida (bloqueado ou VPN sem resposta) - sem vazamento." -ForegroundColor Yellow }
elseif ($viaDiscord -eq $direct) { Write-Host "FALHA: Discord saiu pelo IP real!" -ForegroundColor Red; exit 1 }
else { Write-Host "OK: somente o Discord passa pela VPN." -ForegroundColor Green }
