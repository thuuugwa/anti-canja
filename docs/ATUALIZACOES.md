# Atualizações automáticas (GitHub Releases)

## Como funciona para o usuário

1. Ao abrir, o ANTI CANJA consulta `https://api.github.com/repos/<UPDATE_REPO>/releases/latest`.
2. Se a versão da Release for maior que a instalada, aparece **"Atualização disponível"** com as notas da versão
   (ou instala direto, se "Instalar atualizações automaticamente" estiver ligado).
3. O pacote `AntiCanja-<versão>-win64.zip` é baixado com barra de progresso e o **SHA-256** é conferido.
4. O programa fecha (desliga a VPN), um script copia os arquivos novos por cima da instalação e reabre o ANTI CANJA.
5. Perfis, configurações e logs (`%APPDATA%\AntiCanja`) e a pasta `confs` do usuário **não são apagados**.

Em caso de falha (sem internet, arquivo corrompido, GitHub fora), o programa avisa e continua na versão atual.
Registro da aplicação: `%APPDATA%\AntiCanja\logs\update.log`.

Também há o botão **Verificar atualizações** (aba Sobre e Configurações) e a opção "Pular esta versão".

## Configuração (uma vez)

1. Crie um repositório **público** no GitHub (ex.: `seuusuario/anti-canja`).
2. Em `src/anticanja/__init__.py`, preencha:
   ```python
   UPDATE_REPO = "seuusuario/anti-canja"
   ```
3. Envie o projeto para o repositório:
   ```powershell
   git init
   git add -A
   git commit -m "ANTI CANJA"
   git branch -M main
   git remote add origin https://github.com/seuusuario/anti-canja.git
   git push -u origin main
   ```
   O `.gitignore` já impede o envio de `confs/` e `*.conf` — **eles contêm chaves privadas e nunca podem ir para o GitHub**.
4. Compile e distribua essa versão (com `UPDATE_REPO` preenchido) para os usuários. A partir dela, tudo é automático.

## Publicar uma nova versão

```powershell
powershell -ExecutionPolicy Bypass -File scripts\publicar.ps1 -Versao 1.4.0 -Notas "O que mudou nesta versão"
```

O script:
1. atualiza o número da versão no código e no executável;
2. grava as notas em `NOTAS_DA_VERSAO.md`;
3. faz commit, cria a tag `v1.4.0` e envia ao GitHub;
4. o **GitHub Actions** (`.github/workflows/release.yml`) compila no Windows, gera
   `AntiCanja-1.4.0-win64.zip` + `.sha256` (sem perfis) e cria a Release.

Em ~5 minutos todos os usuários recebem a atualização ao abrir o programa.

Sem GitHub Actions? Use `-Local` (compila no seu PC e publica com o GitHub CLI `gh`, que precisa estar logado: `gh auth login`).

### Regras
- A versão deve sempre **aumentar** (1.3.0 → 1.3.1 → 1.4.0).
- O arquivo da Release precisa se chamar `AntiCanja-<versão>-win64.zip`.
- Publicar à mão pelo site também funciona: crie a Release com a tag `vX.Y.Z` e anexe o `.zip` e o `.zip.sha256` gerados por `build.ps1 -NoConfs`.
