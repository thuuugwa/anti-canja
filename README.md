# CANJA

**Gerenciador de VPN exclusivo para o Discord no Windows.**
Somente o Discord sai pela VPN (perfis WireGuard `.conf` do ProtonVPN). Navegadores, jogos e o resto do Windows continuam usando a sua internet normal.

- Roteamento **por processo** de verdade (sing-box + Wintun + WireGuard userspace), incluindo voz (UDP) e processos auxiliares do Discord.
- DNS do Discord resolvido pela VPN; IPv6 do Discord bloqueado quando o perfil não tem IPv6.
- Kill-switch: se o túnel cair, o Discord não vaza.
- Nada é alterado permanentemente no Windows: ao desconectar, adaptador, rotas e filtros somem.

Detalhes técnicos: [docs/ARQUITETURA.md](docs/ARQUITETURA.md).

---

## Gerar o executável

Requisitos no PC de build: **Windows 10/11 x64**, **Python 3.10+** (python.org, marque *Add to PATH*) e internet.

```powershell
cd anti-canja
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

O script:
1. cria `.venv` e instala PySide6, psutil, Pillow, PyInstaller e pytest;
2. baixa o **sing-box 1.14.x** oficial (GitHub SagerNet) para `bin\` e confere o SHA-256;
3. roda os testes e valida as configs geradas com o `sing-box check` real;
4. gera `dist\AntiCanja\AntiCanja.exe` e `dist\AntiCanja-1.0.0-win64.zip`.

Para fixar uma versão: `.\build.ps1 -SingBoxVersion 1.14.4`.

## Usar

1. Copie a pasta `dist\AntiCanja` para onde quiser (ex.: `C:\Program Files\AntiCanja`) e abra **AntiCanja.exe** (pede administrador — necessário para criar o adaptador do túnel).
2. No painel do ProtonVPN: *Downloads → WireGuard configuration* → plataforma Windows → gere e baixe os `.conf`.
3. **Adicionar .conf** (pode selecionar vários de uma vez). Os arquivos originais não são alterados — o app guarda uma cópia.
4. Selecione um perfil → **Conectar VPN** (ou duplo clique).
5. **Abrir Discord**. Se ele já estava aberto, o app oferece reiniciá-lo (conexões abertas antes da VPN não entram no túnel).
6. Aba *Atividade* → **Testar isolamento**: compara o IP público de um app comum com o de um processo "Discord".

Botão direito em um perfil: conectar, ver detalhes (sem chave privada), renomear, atualizar com um `.conf` novo, excluir.

### Configurações

| Opção | O que faz |
|---|---|
| Iniciar com o Windows | Tarefa Agendada com privilégios elevados (sem UAC a cada logon) |
| Conectar automaticamente (último perfil) | Ao abrir o app |
| Abrir o Discord após conectar | — |
| Minimizar para a bandeja / iniciar minimizado | Fechar a janela mantém o app na bandeja |
| Kill-switch | Se o túnel cair, o Discord é encerrado e o túnel reiniciado |
| Bloquear o Discord com a VPN desligada | Discord só acessa a internet com a VPN conectada |
| DNS do Discord pela VPN | Evita vazamento de DNS |
| Rota estrita | Recomendado; desative só se algum app de LAN quebrar |

Discord instalado em pasta fora do padrão? Edite `%APPDATA%\AntiCanja\settings.json`:
```json
"extra_discord_dirs": ["D:\\Apps\\Discord"]
```

## Desenvolvimento

```powershell
.\build.ps1 -SkipTests         # primeira vez: cria .venv e baixa o sing-box
.\run_dev.ps1                  # roda a partir do código (pede UAC)
.venv\Scripts\python -m pytest -q tests
.venv\Scripts\python scripts\selfcheck.py   # valida configs com o sing-box real
```

Estrutura: `src/anticanja/core` (lógica, sem interface — testável), `src/anticanja/ui` (PySide6), `tests/`, `scripts/`.

### Testes manuais de isolamento (checklist de release)

1. Conectar → `scripts\test_isolation.ps1` → IPs diferentes ✅
2. Navegador em ipinfo.io → mostra o IP real ✅
3. Discord: Configurações → Voz → entrar numa call → região/servidor de voz funcionando ✅
4. Desconectar → `Get-NetAdapter | ? Name -eq AntiCanja` → nada ✅
5. Kill-switch: com VPN ativa, finalizar `sing-box.exe` no Gerenciador de Tarefas → Discord é fechado e o túnel volta ✅
6. "Bloquear com VPN desligada" → desconectar → teste de isolamento mostra Discord bloqueado ✅

## Licenças

O ANTI CANJA usa o [sing-box](https://github.com/SagerNet/sing-box) (GPL-3.0) como processo externo, distribuído sem modificações em `bin\` junto com sua licença. ProtonVPN e Discord são marcas de seus respectivos donos; este projeto não é afiliado a eles.
