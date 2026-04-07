# PROJECT_STATE.md — OpenClaw Infrastructure

> Persistenter Projektstatus für nahtlose Fortsetzung in neuen Sessions.
> Zuletzt aktualisiert: 2026-04-07

---

## Projektübersicht

**Repo:** `pandysp/openclaw-infra` (upstream) / Fork: `maik12c/openclaw-infra`
**Zweck:** Self-hosted AI-Agent-Gateway (OpenClaw) auf Hetzner VPS mit Tailscale Zero-Trust-Networking.
**Zugang:** Ausschließlich über Tailscale — keine öffentlichen Ports.

---

## Aktueller Stand

### Server

| Parameter | Wert |
|---|---|
| **OpenClaw Version** | v2026.3.31 |
| **Server** | Hetzner CX43 (8 vCPU, 16 GB RAM) |
| **Standort** | Nuremberg (nbg1) |
| **Tailscale IP** | 100.110.126.82 |
| **Tailscale Hostname** | openclaw-vps.tail6aeb31.ts.net |
| **Browser-URL** | https://openclaw-vps.tail6aeb31.ts.net/chat |
| **Gateway-Status** | aktiv (systemd user service) |
| **Node.js** | v22.x auf VPS / v24.x auf Windows-Laptop |

### Modelle

| Rolle | Modell | Auth |
|---|---|---|
| **Primär** | `openai/gpt-5.4-mini` | ChatGPT Plus OAuth (via mcp-auth-proxy) |
| **Verfügbar** | `openai/gpt-5.4-nano` | ChatGPT Plus OAuth (via mcp-auth-proxy) |
| **Fallback** | *(leer — kein Fallback konfiguriert)* | — |

- **Kein API-Key mehr nötig** (07.04.2026) — OpenClaw-OpenAI-Provider zeigt auf `http://172.18.0.1:8787` (mcp-auth-proxy). Der Proxy ersetzt den Dummy-apiKey `codex-oauth-proxy` durch das echte OAuth Bearer-Token aus `~/.codex/auth.json`.
- **Anthropic wurde komplett entfernt** (02.04.2026) — ToS-Verstoß: Anthropic verbietet Nutzung des Abo-Zugangs über Drittanbieter
- Anthropic API-Key als Fallback wird später manuell hinzugefügt
- Modellwechsel im Chat möglich: "Wechsle auf openai/gpt-5.4-nano"
- Modellwechsel über Browser-Dropdown möglich

### Konfiguration (Live auf Server)

```
# Modelle
agents.defaults.model.primary: openai/gpt-5.4-mini
agents.defaults.model.fallbacks: []
agents.defaults.heartbeat.model: openai/gpt-5.4-mini
models.providers.openai.api: openai-responses
models.providers.openai.baseUrl: http://172.18.0.1:8787  (mcp-auth-proxy)
models.providers.openai.apiKey: codex-oauth-proxy  (Proxy ersetzt durch OAuth-Token)

# Sandbox
agents.defaults.sandbox.mode: all
agents.defaults.sandbox.docker.network: bridge
agents.defaults.sandbox.docker.image: openclaw-sandbox-custom:latest

# Gateway
agents.defaults.heartbeat.directPolicy: allow
channels.telegram.silentErrorReplies: true
gateway.channelStaleEventThresholdMinutes: 15
gateway.controlUi.allowedOrigins: ["https://openclaw-vps.tail6aeb31.ts.net"]
tools.sandbox.tools.allow: enthält "session:model"
agents.defaults.elevatedDefault: off

# Limits
openclaw_compaction_reserve_tokens_floor: 50000
openclaw_agent_timeout_seconds: 1500 (25 Minuten)

# Plugins
plugins.allow: ["openclaw-mcp-adapter"]
plugins.entries.openclaw-mcp-adapter: Codex MCP Server (1 Server registriert)
```

### mcp-auth-proxy — OpenAI API Route

Eingerichtet am 07.04.2026. Der `mcp-auth-proxy` (Port 8787) hat eine neue Route `/v1/*`, die Anfragen an `api.openai.com` weiterleitet und das Codex OAuth Bearer-Token injiziert. Dadurch nutzt OpenClaw's primäres Modell (GPT-5.4-mini) das ChatGPT Plus-Abo statt eines Pay-per-Token API-Keys.

**Technischer Ablauf:**
1. OpenClaw → `POST http://172.18.0.1:8787/v1/responses` (mit Dummy-Header `Authorization: Bearer codex-oauth-proxy`)
2. mcp-auth-proxy ersetzt den Header durch `Authorization: Bearer <OAuth-Token aus ~/.codex/auth.json>`
3. Anfrage geht an `https://api.openai.com/v1/responses`
4. Bei 401: Token automatisch erneuert (via `https://auth.openai.com/oauth/token`)

### Codex MCP (OpenAI OAuth)

Eingerichtet am 02.04.2026. Gibt dem Agenten Code-Ausführungstools powered by ChatGPT Plus-Abo.

| Komponente | Details |
|---|---|
| **Auth-Methode** | OpenAI OAuth (`auth_mode: chatgpt`, ChatGPT Plus) |
| **Auth-Datei VPS** | `/home/ubuntu/.codex/auth.json` (0600) |
| **Auth-Datei lokal** | `C:\Users\maik1\.codex\auth.json` |
| **Docker-Image** | `codex-mcp:latest` (node:20-slim + @openai/codex@0.116.0) |
| **Docker-Netzwerk** | `codex-proxy-net` (Gateway: 172.18.0.1) |
| **Auth-Proxy** | `mcp-auth-proxy` systemd-Service (Port 8787 auf 172.18.0.1) |
| **Token-Refresh** | Automatisch via `https://auth.openai.com/oauth/token` |
| **Config** | `/home/ubuntu/.openclaw/codex-config.toml` → routes durch Proxy |
| **Plugin** | `openclaw-mcp-adapter@0.1.6` |

**Wichtig:** Codex MCP ist ein **Code-Ausführungstool**, kein Chat-Modell-Ersatz. Der Main-Chat (GPT-5.4-mini) nutzt den API-Key. OpenClaw unterstützt kein OpenAI OAuth für Chat-Modelle.

**Token-Erneuerung (falls nötig):**
```powershell
# Auf Windows:
codex login
# Dann auf VPS übertragen:
cat /c/Users/maik1/.codex/auth.b64 | grep -v CERTIFICATE | tr -d '\r\n' | ssh ubuntu@100.110.126.82 "base64 -d > /home/ubuntu/.codex/auth.json && chmod 0600 /home/ubuntu/.codex/auth.json"
```

### SSH-Härtung

Datei `/etc/ssh/sshd_config.d/hardening.conf` aktiv:
```
PasswordAuthentication no
PermitRootLogin no
X11Forwarding no
```

### Integrationen

| Integration | Status |
|---|---|
| **Telegram** | ✅ Aktiv (@openclaw_maik_bot), Cron: Daily Standup 09:30, Night Shift 23:00 |
| **Codex MCP** | ✅ Aktiv — OpenAI OAuth (ChatGPT Plus), mcp-auth-proxy läuft |
| **WhatsApp** | Nicht konfiguriert |
| **Discord** | Nicht konfiguriert |
| **xAI Web Search** | Nicht konfiguriert (kein API-Key) |
| **Gemini Image Gen** | Nicht konfiguriert (kein API-Key) |
| **Obsidian Sync** | Nicht konfiguriert |
| **Workspace Git Sync** | Nicht konfiguriert |
| **OpenAI Whisper Voice** | Geplant (OpenAI Key vorhanden) |
| **GitHub MCP** | Nicht konfiguriert |
| **Mac Node Exec** | Nicht konfiguriert |

---

## Struktur & Dateien

```
openclaw-infra/
├── PROJECT_STATE.md        # Diese Datei
├── CLAUDE.md               # AI-Assistent-Guide (vollständige Doku)
├── pulumi/                 # Infra-as-Code (Hetzner + Tailscale)
│   ├── Pulumi.prod.yaml    # Stack-Config (Secrets verschlüsselt)
│   └── index.ts            # Haupt-Entrypoint
├── ansible/
│   ├── group_vars/all.yml  # Nicht-geheime Defaults (Modelle, Versionen)
│   ├── group_vars/openclaw.yml  # Deployment-spezifisch (gitignored)
│   └── roles/              # config, agents, telegram, plugins, sandbox, ...
├── scripts/
│   ├── provision.sh        # Ansible-Wrapper (liest Secrets aus Pulumi)
│   └── verify.sh           # Post-Deployment-Checks
└── docs/                   # SECURITY.md, TROUBLESHOOTING.md, NODE-EXEC.md
```

**Wichtig:** `group_vars/openclaw.yml` ist gitignored — enthält deployment-spezifische Overrides.

---

## Regeln & Constraints

### Provisioning
- **Kein Ansible auf Windows** → alle Server-Änderungen via SSH + `openclaw config set`
- Provisioning nur über `./scripts/provision.sh` (Linux/Mac) oder manuell via SSH
- Tailscale SSH erfordert periodische Browser-Authentifizierung (URL im SSH-Output)

### Git-Workflow
- Upstream: `pandysp/openclaw-infra` (kein Push-Zugang)
- Fork: `maik12c/openclaw-infra` (Push-Zugang)
- Arbeits-Branch: `brave-boyd` (Worktree: `C:\Users\maik1\.claude-worktrees\openclaw-infra\brave-boyd`)
- Haupt-Repo: `C:\Users\maik1\openclaw-infra`

### Sicherheit
- Nie `0.0.0.0` als Bind-Adresse
- Keine inbound Firewall-Regeln hinzufügen
- Secrets nur via `pulumi config set --secret`
- `OPENCLAW_NO_ONBOARD=1 OPENCLAW_NO_PROMPT=1` beim manuellen Install (verhindert Token-Überschreiben)

### Bekannte Fallstricke
- **Windows CRLF**: Skripte aus Windows haben `\r` — Befehle inline mit `;` verketten, kein Heredoc über SSH
- **OpenAI API-Typ**: Muss `openai-responses` sein (nicht `openai` oder `openai-chat`)
- **Token-Wipe-Gefahr**: `openclaw onboard --mode remote` mit leerem Token überschreibt `gateway.remote.token` lautlos
- **controlUi.allowedOrigins**: Muss Tailscale-URL enthalten, sonst "origin not allowed" im Browser
- **Codex OAuth Client-IDs**: Login-Flow nutzt `app_EMoamEEZ73f0CkXaXp7hrann`, Token-Refresh nutzt `DRivsnm2Mu42T3KOpqdL9p3q9ByGaHfy` — das ist beabsichtigt

---

## Änderungshistorie (07.04.2026)

1. **OpenAI OAuth als primäres Modell** — mcp-auth-proxy `/v1/*`-Route hinzugefügt; GPT-5.4-mini nutzt jetzt ChatGPT Plus-Abo statt API-Key
2. **Legacy Telegram-Config** — `openclaw doctor --fix` hat `streamMode` → `streaming` migriert

---

## Änderungshistorie (02.04.2026)

1. **Repo rebased** — 53 Upstream-Commits von `pandysp/openclaw-infra` gepullt (e3417b5 → 5bcf907)
2. **OpenClaw upgraded** — v2026.2.17 → v2026.3.31 auf dem VPS
3. **controlUi.allowedOrigins** — Tailscale-URL hinzugefügt (Browser-Zugriff funktioniert wieder)
4. **Anthropic komplett entfernt** — Setup-Token gelöscht, Auth-Profile bereinigt, Provider entfernt
5. **OpenAI als primäres Modell** — GPT-5.4-mini primär, GPT-5.4-nano freigeschaltet
6. **Codex MCP eingerichtet** — OpenAI OAuth Login, Docker-Image, Auth-Proxy, MCP-Adapter installiert
7. **Config-Fixes** — `heartbeat.directPolicy: allow`, `telegram.silentErrorReplies`, `channelStaleEventThresholdMinutes: 15`

---

## Offene Aufgaben

### Geplant (vom Nutzer bestätigt, aber aufgeschoben)
1. **OpenAI Whisper Voice** — Audio-Transkription via OpenAI (API-Key vorhanden)
   - Aktuell: `openclaw_audio_enabled: false`
   - Aktion: `openclaw config set tools.media.audio.enabled true` + Whisper-Modell konfigurieren

2. **Web Search (xAI/Grok)** — Semantische Web-Suche
   - Benötigt: xAI API-Key (x.ai/api)

3. **GitHub MCP** — GitHub-Integration für den Agenten
   - Benötigt: GitHub Personal Access Token

4. **Anthropic API-Key als Fallback** — Separater API-Key (nicht das Abo), wird später hinzugefügt
   - Aktion: `openclaw config set models.providers.anthropic.apiKey "sk-ant-api03-..."` + Fallback setzen

5. **OpenAI Plus OAuth als Primärmodell** — ✅ Erledigt (07.04.2026): mcp-auth-proxy `/v1/*`-Route. Kein separater API-Key mehr nötig.

### Infrastruktur
6. **Codex MCP testen** — Funktionstest im Browser: Agent auffordern, Code zu schreiben/auszuführen
7. **PR erstellen** — Änderungen von `brave-boyd` nach `maik12c/openclaw-infra` pushen

---

## Nächste Schritte (empfohlen)

```bash
# 1. Tailscale SSH auth (Browser-URL aus SSH-Output kopieren und öffnen)

# 2. Server-Status prüfen
ssh ubuntu@100.110.126.82 'export PATH=/home/ubuntu/.npm-global/bin:$PATH XDG_RUNTIME_DIR=/run/user/1000; openclaw health'

# 3. Auth-Proxy Status
ssh ubuntu@100.110.126.82 'curl -s http://172.18.0.1:8787/health'

# 4. Codex MCP Funktionstest: Im Browser eine Coding-Aufgabe stellen

# 5. Repo auf aktuellem Stand halten
cd "C:\Users\maik1\.claude-worktrees\openclaw-infra\brave-boyd"
git fetch origin && git rebase origin/main
```

---

## Referenzen

| Ressource | Details |
|---|---|
| **Server SSH** | `ssh ubuntu@100.110.126.82` |
| **Browser** | https://openclaw-vps.tail6aeb31.ts.net/chat |
| **Tailscale Admin** | https://login.tailscale.com/admin/machines |
| **Hetzner Console** | https://console.hetzner.cloud |
| **Pulumi Stack** | `prod` (in `pulumi/` Verzeichnis) |
| **Upstream Repo** | https://github.com/pandysp/openclaw-infra |
| **Fork Repo** | https://github.com/maik12c/openclaw-infra |
| **OpenClaw Docs** | https://openclaw.ai/docs |
| **Gateway-Token** | `pulumi stack output openclawGatewayToken --show-secrets` |
| **Tailscale-URL mit Token** | `pulumi stack output tailscaleUrlWithToken --show-secrets` |

### Nützliche SSH-Befehle

```bash
# Gateway-Status
ssh ubuntu@100.110.126.82 'XDG_RUNTIME_DIR=/run/user/1000 systemctl --user status openclaw-gateway'

# Gateway-Logs live
ssh ubuntu@100.110.126.82 'XDG_RUNTIME_DIR=/run/user/1000 journalctl --user -u openclaw-gateway -f'

# Auth-Proxy-Logs
ssh ubuntu@100.110.126.82 'XDG_RUNTIME_DIR=/run/user/1000 journalctl --user -u mcp-auth-proxy -f'

# Auth-Proxy Health
ssh ubuntu@100.110.126.82 'curl -s http://172.18.0.1:8787/health | jq .'

# OpenClaw-Version
ssh ubuntu@100.110.126.82 'PATH=/home/ubuntu/.npm-global/bin:$PATH openclaw --version'

# Config-Wert lesen
ssh ubuntu@100.110.126.82 'PATH=/home/ubuntu/.npm-global/bin:$PATH XDG_RUNTIME_DIR=/run/user/1000 openclaw config get <key>'

# Config-Wert setzen + Gateway neu starten
ssh ubuntu@100.110.126.82 'PATH=/home/ubuntu/.npm-global/bin:$PATH XDG_RUNTIME_DIR=/run/user/1000; openclaw config set <key> <value> && systemctl --user restart openclaw-gateway'

# Codex-Token erneuern (auf Windows PowerShell zuerst: codex login)
# Dann: base64-encoded auth.json per SSH übertragen (siehe Codex MCP Sektion oben)
```
