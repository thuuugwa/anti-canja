<#
.SYNOPSIS
  Publica uma nova versão do ANTI CANJA no GitHub (atualização automática para todos).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\publicar.ps1 -Versao 1.4.0 -Notas "Correções e melhorias"

.DESCRIPTION
  1. Atualiza o número da versão (src\anticanja\__init__.py e scripts\version_info.txt).
  2. Grava as notas em NOTAS_DA_VERSAO.md.
  3. Faz commit, cria a tag vX.Y.Z e envia ao GitHub (git push + push da tag).
  4. O GitHub Actions (.github\workflows\release.yml) compila e cria a Release.
     Sem Actions? Use -Local: compila aqui e publica com o GitHub CLI (gh).
#>
param(
    [Parameter(Mandatory)][string]$Versao,
    [string]$Notas = "Melhorias e correções.",
    [switch]$Local
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

# Garante o workflow do GitHub Actions no lugar certo
if (-not (Test-Path .github\workflows\release.yml)) {
    New-Item -ItemType Directory -Force -Path .github\workflows | Out-Null
    Copy-Item scripts\release.yml .github\workflows\release.yml
}

if ($Versao -notmatch '^\d+\.\d+\.\d+$') { throw "Use o formato 1.4.0" }
$p = $Versao.Split('.')

# 1) versão no código
$init = "src\anticanja\__init__.py"
(Get-Content $init -Raw) -replace '__version__ = "[^"]+"', "__version__ = `"$Versao`"" | Set-Content $init -NoNewline -Encoding utf8
$vi = "scripts\version_info.txt"
$t = Get-Content $vi -Raw
$t = $t -replace '\(\d+, \d+, \d+, 0\)', "($($p[0]), $($p[1]), $($p[2]), 0)"
$t = $t -replace "'\d+\.\d+\.\d+'", "'$Versao'"
Set-Content $vi $t -NoNewline -Encoding utf8

# 2) notas
Set-Content NOTAS_DA_VERSAO.md "## ANTI CANJA $Versao`n`n$Notas`n" -Encoding utf8

# Segurança: nunca publicar chaves privadas
$tracked = git ls-files | Select-String -Pattern '\.conf$'
if ($tracked) { throw "Há arquivos .conf no Git ($tracked). Remova-os: eles contêm chaves privadas." }

if ($Local) {
    & .\build.ps1 -NoConfs
    git add -A; git commit -m "Versão $Versao"; git tag "v$Versao"; git push; git push origin "v$Versao"
    gh release create "v$Versao" "dist\AntiCanja-$Versao-win64.zip" "dist\AntiCanja-$Versao-win64.zip.sha256" `
        --title "ANTI CANJA v$Versao" --notes-file NOTAS_DA_VERSAO.md
} else {
    git add -A; git commit -m "Versão $Versao"; git tag "v$Versao"; git push; git push origin "v$Versao"
    Write-Host "Tag v$Versao enviada. O GitHub Actions vai compilar e publicar a Release em ~5 minutos." -ForegroundColor Green
}
