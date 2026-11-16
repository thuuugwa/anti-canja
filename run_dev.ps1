# Executa o ANTI CANJA a partir do código-fonte (abre o UAC para pedir administrador).
# Pré-requisito: rodar o build.ps1 uma vez (cria .venv e baixa bin\sing-box.exe).
Set-Location $PSScriptRoot
$py = Join-Path $PSScriptRoot ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $py)) { Write-Error "Rode .\build.ps1 primeiro."; exit 1 }
Start-Process -FilePath $py -ArgumentList "`"$PSScriptRoot\run_anticanja.py`"" -Verb RunAs -WorkingDirectory $PSScriptRoot
