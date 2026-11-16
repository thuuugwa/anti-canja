<#
.SYNOPSIS
  Gera o executável do ANTI CANJA para Windows.

.DESCRIPTION
  1. Cria um ambiente virtual Python (.venv) e instala as dependências.
  2. Baixa o sing-box (série 1.14 por padrão) do GitHub oficial e confere o SHA-256.
  3. Roda os testes automatizados e valida as configs geradas com o sing-box real.
  4. Empacota com PyInstaller (pasta dist\AntiCanja) com manifesto de administrador.
  5. Gera dist\AntiCanja-<versão>-win64.zip.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\build.ps1
  powershell -ExecutionPolicy Bypass -File .\build.ps1 -SingBoxVersion 1.14.4
#>
param(
    [string]$SingBoxVersion = "",
    [string]$SingBoxSeries = "1.14",
    [switch]$SkipTests,
    # Não incluir a pasta confs no pacote (OBRIGATÓRIO para publicar no GitHub:
    # os .conf contêm chaves privadas).
    [switch]$NoConfs
)

# "Continue": no PowerShell 5.1, stderr de programas nativos (pip, PyInstaller) vira erro
# fatal com "Stop". Falhas reais são tratadas por $LASTEXITCODE e -ErrorAction Stop.
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
Set-Location $PSScriptRoot

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Magenta }

# ---------------------------------------------------------------- Python
Step "Verificando Python"
$py = (Get-Command py -ErrorAction SilentlyContinue)
if ($py) { $python = "py"; $pyArgs = @("-3") } else { $python = "python"; $pyArgs = @() }
& $python @pyArgs -c "import sys; assert sys.version_info >= (3,10), 'Python 3.10+ necessario'; print(sys.version)"
if ($LASTEXITCODE -ne 0) { throw "Instale o Python 3.10+ (python.org) e marque 'Add to PATH'." }

if (-not (Test-Path .venv)) {
    Step "Criando ambiente virtual"
    & $python @pyArgs -m venv .venv
}
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $venvPy -m pip install --upgrade pip --quiet 2>&1 | Out-Null
& $venvPy -m pip install -r requirements.txt --quiet
if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar dependências." }

# ---------------------------------------------------------------- sing-box
Step "Obtendo sing-box"
New-Item -ItemType Directory -Force -Path bin | Out-Null
$headers = @{ "User-Agent" = "AntiCanja-build"; "Accept" = "application/vnd.github+json" }
if (-not $SingBoxVersion) {
    $releases = Invoke-RestMethod -Uri "https://api.github.com/repos/SagerNet/sing-box/releases?per_page=50" -Headers $headers -ErrorAction Stop
    $rel = $releases | Where-Object { -not $_.prerelease -and $_.tag_name -like "v$SingBoxSeries.*" } | Select-Object -First 1
    if (-not $rel) { throw "Nenhuma versão estável $SingBoxSeries.x encontrada." }
} else {
    $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/SagerNet/sing-box/releases/tags/v$SingBoxVersion" -Headers $headers -ErrorAction Stop
}
$ver = $rel.tag_name.TrimStart("v")
$assetName = "sing-box-$ver-windows-amd64.zip"
$asset = $rel.assets | Where-Object { $_.name -eq $assetName } | Select-Object -First 1
if (-not $asset) { throw "Arquivo $assetName não encontrado na release." }

$current = ""
if (Test-Path bin\sing-box.exe) { $current = (& .\bin\sing-box.exe version | Select-Object -First 1) }
if ($current -match [regex]::Escape($ver)) {
    Write-Host "sing-box $ver já presente."
} else {
    $tmp = Join-Path $env:TEMP "anticanja-singbox"
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path $tmp | Out-Null
    $zip = Join-Path $tmp $assetName
    Write-Host "Baixando $assetName…"
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zip -Headers @{ "User-Agent" = "AntiCanja-build" } -ErrorAction Stop
    $hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
    if ($asset.digest) {
        $expected = $asset.digest.Replace("sha256:", "").ToLower()
        if ($hash -ne $expected) { throw "SHA-256 não confere! esperado $expected, obtido $hash" }
        Write-Host "SHA-256 conferido: $hash" -ForegroundColor Green
    } else {
        Write-Warning "A release não publica digest; SHA-256 do arquivo baixado: $hash"
    }
    Expand-Archive $zip -DestinationPath $tmp -Force -ErrorAction Stop
    $exe = Get-ChildItem $tmp -Recurse -Filter sing-box.exe | Select-Object -First 1
    Copy-Item $exe.FullName bin\sing-box.exe -Force -ErrorAction Stop
    $lic = Get-ChildItem $tmp -Recurse -Filter LICENSE | Select-Object -First 1
    if ($lic) { Copy-Item $lic.FullName bin\LICENSE-sing-box.txt -Force }
    Remove-Item $tmp -Recurse -Force
}
& .\bin\sing-box.exe version | Select-Object -First 1

# ---------------------------------------------------------------- testes
if (-not $SkipTests) {
    Step "Testes automatizados"
    & $venvPy -m pytest -q tests
    if ($LASTEXITCODE -ne 0) { throw "Testes falharam." }
    Step "Validando configs geradas com o sing-box real"
    & $venvPy scripts\selfcheck.py
    if ($LASTEXITCODE -ne 0) { throw "O sing-box rejeitou a configuração gerada." }
}

# ---------------------------------------------------------------- ícone
if (-not (Test-Path assets\anticanja.ico)) { & $venvPy scripts\make_icon.py }

# ---------------------------------------------------------------- empacotamento
Step "Empacotando com PyInstaller"
$appVer = (& $venvPy -c "import sys; sys.path.insert(0,'src'); import anticanja; print(anticanja.__version__)").Trim()
Remove-Item build, dist\AntiCanja -Recurse -Force -ErrorAction SilentlyContinue
& $venvPy -m PyInstaller --log-level WARN `
    --noconfirm --clean --windowed `
    --name AntiCanja `
    --icon assets\anticanja.ico `
    --uac-admin `
    --paths src `
    --add-data "assets;assets" `
    --exclude-module tkinter `
    --exclude-module PySide6.QtWebEngineCore `
    --exclude-module PySide6.Qt3DCore `
    --version-file scripts\version_info.txt `
    run_anticanja.py 2>&1 | ForEach-Object { "$_" }
if ($LASTEXITCODE -ne 0) { throw "PyInstaller falhou." }

New-Item -ItemType Directory -Force -Path dist\AntiCanja\bin | Out-Null
Copy-Item bin\sing-box.exe dist\AntiCanja\bin\ -Force
if (Test-Path bin\LICENSE-sing-box.txt) { Copy-Item bin\LICENSE-sing-box.txt dist\AntiCanja\bin\ -Force }
Copy-Item README.md dist\AntiCanja\ -Force
if ((Test-Path confs) -and -not $NoConfs) {
    New-Item -ItemType Directory -Force -Path dist\AntiCanja\confs | Out-Null
    Copy-Item confs\*.conf dist\AntiCanja\confs\ -Force -ErrorAction SilentlyContinue
    Write-Host "Perfis incluidos no pacote: $((Get-ChildItem dist\AntiCanja\confs -Filter *.conf).Count)"
}
Copy-Item docs dist\AntiCanja\docs -Recurse -Force

$zipOut = "dist\AntiCanja-$appVer-win64.zip"
Remove-Item $zipOut -ErrorAction SilentlyContinue
Compress-Archive -Path dist\AntiCanja\* -DestinationPath $zipOut -ErrorAction Stop
$sha = (Get-FileHash $zipOut -Algorithm SHA256).Hash.ToLower()
Set-Content -Path "$zipOut.sha256" -Value "$sha  $(Split-Path $zipOut -Leaf)" -Encoding ascii -NoNewline
Step "Pronto!"
Write-Host "Executável: dist\AntiCanja\AntiCanja.exe" -ForegroundColor Green
Write-Host "Pacote:     $zipOut" -ForegroundColor Green
