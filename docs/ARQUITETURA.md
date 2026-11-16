# ANTI CANJA — Arquitetura técnica

## 1. O problema

Um túnel WireGuard comum (o app do ProtonVPN, o WireGuard oficial) troca a **rota padrão** do Windows: *todo* o tráfego da máquina passa a sair pela VPN. Mudar `AllowedIPs` não resolve, porque o WireGuard filtra por **endereço IP de destino**, não por **programa**. E o Discord fala com centenas de IPs (Cloudflare para a API, Google Cloud para servidores de voz, CDNs) que mudam o tempo todo.

Para que **somente o Discord** use a VPN, a decisão precisa ser tomada **por processo**, a cada conexão.

## 2. Opções avaliadas

| Abordagem | Por processo? | Voz (UDP)? | Complexidade | Veredito |
|---|---|---|---|---|
| WireGuard + `AllowedIPs` com IPs do Discord | ❌ (por IP) | parcial | média | Frágil, vaza quando o Discord muda de IP, afeta navegadores que acessam os mesmos IPs |
| Proxy (`--proxy-server` no Discord) | ✅ | ❌ voz é UDP direto | baixa | Voz vaza — inaceitável |
| Driver WFP/callout próprio (o que o ProtonVPN usa no split-tunnel) | ✅ | ✅ | **muito alta** (driver assinado em kernel) | Fora de escopo para v1 |
| **sing-box: TUN (Wintun) + regras por processo + WireGuard userspace** | ✅ | ✅ | média | **Escolhida** |

## 3. Como funciona o roteamento exclusivo

```
                         ┌────────────────────────── sing-box.exe ───────────────────────────┐
 Discord.exe ──┐         │                                                                    │
 (qualquer     │         │  regra 1: caminho do processo ∈ pastas do Discord ──► endpoint WG ─┼──UDP 51820──► ProtonVPN ──► internet
  processo da  ├──► TUN ─┤          (IPv6 sem suporte no perfil ──► rejeitar)                 │
  pasta)       │ Wintun  │  regra 2: qualquer outro processo ──────────────► direct ──────────┼──► placa de rede ──► internet
 chrome.exe ───┤         │                                                                    │
 jogo.exe ─────┘         │  DNS (svchost): domínios do Discord ──► DNS 10.2.0.1 via túnel     │
                         │                 demais domínios     ──► resolvedor do sistema      │
                         └────────────────────────────────────────────────────────────────────┘
   LAN (192.168/16, 10/8, 172.16/12, multicast…) nunca entra no TUN (route_exclude_address)
```

1. **Captura** — o sing-box cria o adaptador virtual `AntiCanja` (Wintun, embutido no sing-box) e, com `auto_route`, recebe as rotas padrão. Toda **nova** conexão TCP/UDP chega até ele.
2. **Identificação do processo** — para cada conexão, o sing-box consulta as tabelas TCP/UDP do Windows (`GetExtendedTcpTable` / `GetExtendedUdpTable`) e obtém o PID e o caminho do executável.
3. **Decisão** — a regra `process_path_regex` casa com qualquer `.exe` dentro de
   `…\AppData\Local\Discord\`, `DiscordPTB\`, `DiscordCanary\` ou `DiscordDevelopment\`.
   Isso cobre o processo principal, os processos auxiliares (GPU, *network service*, renderer), o `Update.exe` e os helpers nativos — e **nada além disso**. Um navegador abrindo discord.com tem outro caminho e sai pela conexão normal.
4. **Saída**
   - Discord → endpoint **WireGuard em userspace** dentro do próprio sing-box (`system: false`): não cria um segundo adaptador no Windows.
   - Todo o resto → `direct`, que sai pela placa física (`auto_detect_interface`), como se a VPN não existisse. O sing-box só repassa — não altera conteúdo.
   - Tráfego de LAN não passa nem pelo sing-box.
5. **DNS** — no Windows, quem faz as consultas DNS é o serviço *Dnscache* (svchost), não o Discord. Sem tratamento, os nomes que o Discord consulta vazariam para o DNS do provedor. Por isso:
   - consultas DNS são capturadas (`hijack-dns`);
   - domínios do Discord (`discord.com`, `discord.gg`, `discord.media`, `discordapp.net`…) são resolvidos pelo DNS do ProtonVPN (o `DNS =` do `.conf`) **através do túnel**;
   - os demais domínios usam o resolvedor do sistema.
   - `strict_route` impede que o Windows mande DNS por outras interfaces (vazamento *multihomed*).
6. **IPv6** — perfis gratuitos do ProtonVPN normalmente são só IPv4. Nesse caso, conexões IPv6 do Discord são **rejeitadas** (o Discord cai para IPv4 pela VPN em milissegundos) em vez de saírem por fora.

### Por que não modifica o Windows permanentemente

Rotas, adaptador Wintun e filtros WFP do `strict_route` pertencem ao processo `sing-box.exe` e somem quando ele termina. O ANTI CANJA ainda:
- coloca o sing-box num **Job Object** com `KILL_ON_JOB_CLOSE` (se o ANTI CANJA travar ou for morto, o Windows mata o sing-box junto);
- para o sing-box com **CTRL+BREAK no console oculto dele** (limpeza graciosa) e só usa `TerminateProcess` se não responder;
- mata instâncias órfãs do sing-box antes de conectar (evita duplicidade).

## 4. Proteção contra vazamento (kill-switch)

| Situação | Comportamento |
|---|---|
| VPN conectada, servidor parou de responder | Regras são fixas: o Discord continua indo para o endpoint WG, que não responde → **sem vazamento**. Health-check a cada 45 s; após 3 falhas, reconecta. |
| `sing-box.exe` morreu inesperadamente | Sem TUN o Discord sairia direto. Com o kill-switch ligado, o ANTI CANJA **encerra o Discord imediatamente** e reinicia o túnel (até 3 tentativas). Se não conseguir, ativa o modo bloqueio. |
| VPN desligada + "Bloquear o Discord enquanto a VPN estiver desligada" | O sing-box roda em **modo bloqueio**: Discord → `reject`; demais apps → direto. |
| Discord já aberto antes de conectar | Conexões já estabelecidas não passam por um TUN criado depois. O app oferece **reiniciar o Discord** (perguntar / sempre / nunca). |

## 5. Componentes

```
src/anticanja/
├── app.py                 instância única, elevação (UAC), tema, argumentos (--autostart)
├── core/                  (sem dependência de interface — testável)
│   ├── wgconf.py          parser tolerante de .conf (ProtonVPN), validação de chaves
│   ├── profiles.py        importar (cópia), renomear, excluir, atualizar; ACL restrita
│   ├── singbox_config.py  gera a config do sing-box (VPN e bloqueio)
│   ├── tunnel.py          ciclo de vida do sing-box, watchdog, health-check, reconexão
│   ├── discord.py         canais do Discord, detecção de processos, abrir/reiniciar
│   ├── winproc.py         admin, Job Object, CTRL+BREAK gracioso
│   ├── autostart.py       Tarefa Agendada "privilégios mais altos" no logon
│   ├── selftest.py        teste de isolamento (curl como "Discord" vs. curl comum)
│   ├── logbus.py          logs com redação de chaves/senhas
│   └── settings.py        settings.json
└── ui/                    PySide6 (Qt 6): janela, bandeja, tema dark/roxo, logo
```

### Dados em disco

| Local | Conteúdo |
|---|---|
| `%APPDATA%\AntiCanja\profiles\*.conf` | **cópias** dos `.conf` importados (ACL: usuário + Administradores + SYSTEM) |
| `%APPDATA%\AntiCanja\settings.json` | preferências (sem segredos) |
| `%APPDATA%\AntiCanja\logs\anticanja.log` | log rotativo (1 MB × 4), com chaves redigidas |
| `%APPDATA%\AntiCanja\runtime\active.json` | config do sing-box — existe **só durante a inicialização** (contém a chave privada) e é apagada logo depois |

## 6. Segurança

- **Logs**: toda linha passa por `redact()`: remove `PrivateKey`, `PresharedKey`, qualquer string com formato de chave WireGuard (base64 de 32 bytes), credenciais em URLs e a senha aleatória do proxy de verificação.
- **Proxy de verificação**: um inbound `mixed` em `127.0.0.1`, porta aleatória, usuário/senha aleatórios a cada conexão. Serve só para o próprio ANTI CANJA confirmar o handshake e descobrir o IP de saída da VPN.
- **Discord sem privilégios**: o ANTI CANJA roda como administrador (exigido para criar o TUN), mas abre o Discord via `explorer.exe`, que o executa com o token normal do usuário.
- **Download do sing-box**: o `build.ps1` baixa do GitHub oficial (SagerNet) e confere o SHA-256 publicado pela release.

## 7. Limitações conhecidas (e honestas)

- Todo tráfego de internet (não-LAN) atravessa o sing-box em modo `direct`. O impacto é pequeno (encaminhamento em espaço de usuário), mas existe. Eliminar isso exige um driver WFP em kernel — candidato para uma v2.
- Outra VPN com TUN/rota padrão ativa ao mesmo tempo (ProtonVPN app, Radmin, Hamachi em modo gateway) pode conflitar.
- Se o Discord estiver instalado fora de `%LOCALAPPDATA%`, adicione a pasta em `extra_discord_dirs` no `settings.json`.
- Se o usuário não for administrador e elevar com a conta de outro usuário, `%LOCALAPPDATA%` aponta para o perfil do administrador; a regex de caminho continua funcionando, mas "Abrir Discord" pode não encontrar a instalação.
