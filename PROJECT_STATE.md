# PROJECT_STATE.md — OpenClaw Infrastructure

> Persistenter Projektstatus für nahtlose Fortsetzung in neuen Sessions.
> Zuletzt aktualisiert: 2026-04-02

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
| **Node.js** | v22.x (npx, nodejs_major_version: 25 geplant) |

### Modelle

| Rolle | Modell | Auth |
|---|---|---|
| **Primär** | `anthropic/claude-sonnet-4-6` | Setup-Token (Flat-Fee) |
| **Fallback** | `openai/gpt-5.4-mini` | OpenAI API-Key (Pay-per-Token) |

- Modellwechsel im Chat möglich: "Wechsle auf openai/gpt-5.4-mini"
- Modellwechsel über Browser-Dropdown möglich (seit v2026.3.x)

### Konfiguration (Live auf Server)

```
agents.defaults.sandbox.mode: all
agents.defaults.sandbox.docker.network: bridge
agents.defaults.sandbox.docker.image: openclaw-sandbox-custom:latest
agents.defaults.heartbeat.directPolicy: allow
channels.telegram.silentErrorReplies: true
gateway.channelStaleEventThresholdMinutes: 15
gateway.controlUi.allowedOrigins: ["https://openclaw-vps.tail6aeb31.ts.net"]
tools.sandbox.tools.allow: enthält "session:model"
agents.defaults.elevatedDefault: off
models.primary: anthropic/claude-sonnet-4-6
models.fallback: ["openai/gpt-5.4-mini"]
openclaw_compaction_reserve_tokens_floor: 50000
openclaw_agent_timeout_seconds: 1500 (25 Minuten)
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
| **Telegram** | Aktiv (@openclaw_maik_bot), Cron: Daily Standup 09:30, Night Shift 23:00 |
| **WhatsApp** | Nicht konfiguriert |
| **Discord** | Nicht konfiguriert |
| **xAI Web Search** | Nicht konfiguriert (kein API-Key) |
| **Gemini Image Gen** | Nicht konfiguriert (kein API-Key) |
| **Obsidian Sync** | Nicht konfiguriert |
| **Workspace Git Sync** | Nicht konfiguriert |
| **OpenAI Whisper Voice** | Geplant (OpenAI Key vorhanden) |
| **Codex MCP** | ✅ Aktiv — OpenAI OAuth (ChatGPT Plus), `codex-mcp:latest`, `mcp-auth-proxy` läuft |
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
- Fork: `maik12c/openclaw-infra` (Push-Zugang via `***REMOVED***`)
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

---

## Offene Aufgaben

### Geplant (vom Nutzer bestätigt, aber aufgeschoben)
1. **OpenAI Whisper Voice** — Audio-Transkription via OpenAI (API-Key vorhanden)
   - Aktuell: `openclaw_audio_enabled: false`
   - Aktion: `openclaw config set audio.transcription.provider openai` + API-Key konfigurieren

2. **Web Search (xAI/Grok)** — Semantische Web-Suche
   - Benötigt: xAI API-Key (x.ai/api)
   - Aktion: `pulumi config set xaiApiKey --secret` → `provision.sh --tags config`

3. **GitHub MCP** — GitHub-Integration für den Agenten
   - Benötigt: GitHub Personal Access Token
   - Aktion: In `openclaw.yml` konfigurieren → `provision.sh --tags plugins`

### Infrastruktur
4. **PR mergen** — `maik12c/openclaw-infra` → `pandysp/openclaw-infra` (OpenAI-Fallback-Config)
5. **DEPLOYMENT-STATUS.md** aktualisieren (wurde in vorheriger Session erstellt, aber in diesem Worktree nicht sichtbar)

---

## Nächste Schritte (empfohlen)

```bash
# 1. Tailscale SSH auth (Browser-URL aus SSH-Output kopieren und öffnen)

# 2. OpenAI Whisper einrichten
ssh ubuntu@100.110.126.82 'export PATH=/home/ubuntu/.npm-global/bin:$PATH XDG_RUNTIME_DIR=/run/user/1000; \
  openclaw config set audio.transcription.provider openai && \
  openclaw config set audio.transcription.openai.apiKey "sk-proj-..."'

# 3. Server-Status prüfen
ssh ubuntu@100.110.126.82 'export PATH=/home/ubuntu/.npm-global/bin:$PATH XDG_RUNTIME_DIR=/run/user/1000; openclaw health'

# 4. Repo auf aktuellem Stand halten
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
| **Gateway-Token abrufen** | `pulumi stack output openclawGatewayToken --show-secrets` |
| **Tailscale-URL mit Token** | `pulumi stack output tailscaleUrlWithToken --show-secrets` |

### Nützliche SSH-Befehle

```bash
# Gateway-Status
ssh ubuntu@100.110.126.82 'XDG_RUNTIME_DIR=/run/user/1000 systemctl --user status openclaw-gateway'

# Gateway-Logs live
ssh ubuntu@100.110.126.82 'XDG_RUNTIME_DIR=/run/user/1000 journalctl --user -u openclaw-gateway -f'

# OpenClaw-Version
ssh ubuntu@100.110.126.82 'PATH=/home/ubuntu/.npm-global/bin:$PATH openclaw --version'

# Config-Wert lesen
ssh ubuntu@100.110.126.82 'PATH=/home/ubuntu/.npm-global/bin:$PATH XDG_RUNTIME_DIR=/run/user/1000 openclaw config get <key>'

# Config-Wert setzen + Gateway neu starten
ssh ubuntu@100.110.126.82 'PATH=/home/ubuntu/.npm-global/bin:$PATH XDG_RUNTIME_DIR=/run/user/1000; openclaw config set <key> <value> && systemctl --user restart openclaw-gateway'
```
