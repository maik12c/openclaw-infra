# OpenClaw Infrastructure

> AI assistant guide for deploying and managing OpenClaw on Hetzner Cloud with Tailscale.

## What This Project Is

OpenClaw is a self-hosted AI Agent gateway deployed on a Hetzner VPS with zero-trust networking via Tailscale. All access is through Tailscale—no public ports exposed.

## Repository

This repo is `pandysp/openclaw-infra`. Personal deployment config (Pulumi secrets, `group_vars/openclaw.yml`) stays gitignored or in Pulumi encrypted state.

## Architecture

Your Machine (Tailscale) → Hetzner VPS → Gateway (systemd, localhost:18789) via Tailscale Serve. No public ports. Hetzner firewall + UFW block all inbound except Tailscale.

## Security Model

| Layer | Measure |
|-------|---------|
| Network (infrastructure) | Hetzner cloud firewall blocks ALL inbound |
| Network (host) | UFW: deny incoming, allow only tailscale0 interface |
| Access | Tailscale-only (no public SSH, no public ports) |
| Process | Runs as unprivileged `ubuntu` user; all sessions [sandboxed](#sandboxing) in Docker (custom image with dev toolchain) |
| Auth | Tailscale identity + device pairing |
| Secrets | Pulumi encrypted config (never in git) |
| Gateway | Binds localhost only, proxied via Tailscale Serve |

For the full threat model, see [docs/SECURITY.md](./docs/SECURITY.md).

Gateway runs via systemd (not Docker) as unprivileged user. Docker runs sandbox sessions (`openclaw-sandbox-custom:latest`), MCP servers, and isolated workspace Git jobs. Auth: Tailscale identity + device pairing; no token needed. Resolve pairing requests over Tailscale SSH; do not print token-bearing URLs.

## Active Model Configuration

**Primary model:** `openai/gpt-5.6-sol` via **ChatGPT Plus OAuth** — no per-token billing, no API key required.
**Fallback model:** `openai/gpt-5.6-luna` (also via ChatGPT OAuth; also used for heartbeat).

> **Since 2026-09-09** the ChatGPT-account Codex backend rejects `gpt-5.4` and `gpt-5.4-mini` with HTTP 400 *"not supported when using Codex with a ChatGPT account"*. Supported there: `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, `gpt-5.5` (live list: `~/.openclaw/agents/main/agent/codex-home/models_cache.json` on the VPS). Since OpenClaw 2026.9.x the `openai/` provider routes chat through the ChatGPT OAuth profile, so an API-key fallback like `openai/gpt-5.4-mini` no longer helps.
>
> **Upgrading to 2026.9.x** requires a state DB migration: the gateway exits with status 78 (`audit-events-v2`) until you stop it and run `openclaw doctor --fix --non-interactive`, then `openclaw plugins enable openclaw-mcp-adapter --accept-capabilities`.
**Voice transcription (STT):** OpenAI `gpt-4o-transcribe` via the dedicated `openai:audio` API-key auth profile.
**Voice output (TTS):** OpenAI `tts-1`, voice `onyx` (own key under `tts.providers.openai.apiKey`) — agent replies with a Telegram voice bubble when user sends a voice message (`auto: "inbound"`).

| Surface | Model | Runtime | Auth profile | Billing |
|---------|-------|---------|--------------|---------|
| Chat, cron, heartbeat | `openai/gpt-5.6-sol`, fallback `openai/gpt-5.6-luna` | `codex` (implicit) | `openai:default` (ChatGPT OAuth) | ChatGPT Plus quota |
| Audio transcription | `gpt-4o-transcribe` | media | `openai:audio` (API key, pinned via `tools.media.models[].profile`) | Platform API |
| TTS | `tts-1` | media | `tts.providers.openai.apiKey` | Platform API |
| Memory embeddings | `text-embedding-3-small` | memory-core | OpenAI API key | Platform API |

Auth order: `openclaw models auth order set --agent main --provider openai openai:default openai:audio`.

**Anthropic has been removed** — Anthropic ToS prohibits subscription access via third-party gateways.

### Critical: never author `models.providers.openai` with an apiKey

An authored `openai` provider block (`api: "openai-completions"` + `apiKey`, the old Whisper/gpt-5.4-mini setup) makes every `openai/*` chat turn run on the OpenClaw runtime against `api.openai.com/v1/chat/completions` — **Platform API billing instead of the ChatGPT subscription** (happened 2026-09-14/15). Setting model-scoped `agentRuntime.id: "codex"` while that block exists fails closed ("Codex cannot reproduce the prepared provider route") and takes chat down. Fix: API key in its own `openai:audio` profile, OAuth profile first in auth order, `openclaw config unset models.providers.openai`, restart.

Check the route: `journalctl --user -u openclaw-gateway | grep model-fetch` (any `api.openai.com/.../chat/completions` = API billing) and `openclaw sessions --json` → `agentRuntime.id` should be `codex`. Sessions that remember `{"id":"openclaw","source":"session"}` are not reset by `doctor --fix`; send `/model default -s` to that session. Note: `openclaw doctor --fix` on a running gateway can stop the systemd unit without restarting it — always check `systemctl --user is-active openclaw-gateway` afterwards.

### How OAuth Auth Works

OpenClaw's built-in `openai-codex` provider uses a ChatGPT OAuth token stored in two places:

```
~/.openclaw/credentials/oauth.json       # merged into auth-profiles on load
~/.openclaw/agents/main/agent/auth-profiles.json  # direct profile: openai-codex:default
```

The token is sourced from `~/.codex/auth.json` (written by `codex login`). The Ansible `config` role syncs it automatically during provisioning.

### Token Lifecycle

- **Access token:** valid ~10 days; OpenClaw auto-refreshes via `https://auth.openai.com/oauth/token`
- **Refresh token:** valid until explicitly revoked (no hard expiry)
- **If token expires or refresh fails:**

```bash
codex login                             # run locally on Windows — opens browser OAuth flow
./scripts/provision.sh --tags config   # syncs new tokens to VPS + restarts gateway
```

### mcp-auth-proxy

A Node.js reverse proxy runs as a systemd user service on the VPS (port `172.18.0.1:8787`, only reachable from the host and `codex-proxy-net` Docker containers). It handles:

| Route | Target | Purpose |
|-------|--------|---------|
| `/health` | local | Health check |
| `/github-<agent>/*` | `github.com` | Per-agent GitHub PAT injection for MCP git ops |
| `/anthropic/*` | `api.anthropic.com` | Claude Code / Pi MCP containers (token injection) |
| `/v1/*` | `api.openai.com` | OpenAI API proxy with OAuth token (added, not currently used) |
| default | `chatgpt.com/backend-api/codex` | Codex MCP server requests |

```bash
# Check proxy health
ssh ubuntu@openclaw-vps 'curl -s http://172.18.0.1:8787/health | python3 -m json.tool'

# Check proxy logs
ssh ubuntu@openclaw-vps 'XDG_RUNTIME_DIR=/run/user/1000 journalctl --user -u mcp-auth-proxy -f'
```

## Directory Structure

```
openclaw-infra/
├── CLAUDE.md           # This file - AI assistant guide
├── README.md           # Human overview
├── package.json        # Node.js dependencies
├── tsconfig.json       # TypeScript config
│
├── pulumi/
│   ├── Pulumi.yaml     # Project definition
│   ├── Pulumi.prod.yaml  # Stack config (non-secrets)
│   ├── index.ts        # Main entrypoint (infra + Ansible trigger)
│   ├── server.ts       # Hetzner server resource
│   ├── firewall.ts     # Security rules (no inbound!)
│   └── user-data.ts    # Cloud-init (Tailscale-only bootstrap)
│
├── ansible/
│   ├── ansible.cfg         # Defaults; strict host-key checks for every deployment
│   ├── requirements.yml    # Ansible Galaxy collections
│   ├── playbook.yml        # Main playbook
│   ├── group_vars/all.yml  # Non-secret defaults (model, agent types, server templates)
│   ├── group_vars/openclaw.yml        # Deployment-specific overrides (gitignored)
│   ├── group_vars/openclaw.yml.example  # Template for openclaw.yml
│   ├── inventory/
│   │   └── pulumi_inventory.py  # Inventory consumes authenticated provisioner inputs
│   └── roles/
│       ├── system/    # apt packages, unattended-upgrades
│       ├── docker/    # Docker install, ubuntu→docker group
│       ├── ufw/       # Firewall rules
│       ├── openclaw/  # Binary install, onboard, daemon
│       ├── config/    # All `openclaw config set` commands + OAuth credential sync
│       ├── agents/    # Create non-default agents, set bindings (conditional)
│       ├── telegram/  # Telegram channel config, cron jobs (conditional)
│       ├── whatsapp/  # WhatsApp channel config (conditional)
│       ├── obsidian-headless/  # Obsidian Sync daemon per workspace (conditional)
│       ├── qmd/       # qmd semantic search: install, per-agent watchers
│       ├── plugins/   # MCP adapter, Codex/Claude Code/Pi/qmd servers, deny rules
│       ├── sandbox/   # Pull base image, build custom Docker image
│       └── workspace/ # Deploy key, git sync timer (conditional)
│
├── scripts/
│   ├── provision.sh          # Ansible wrapper (reads secrets from Pulumi)
│   ├── setup-mac-node.sh     # One-time Mac node host installation
│   ├── setup-workspace.sh    # Create workspace repo + deploy key + Pulumi config
│   ├── get-telegram-id.sh    # Discover Telegram user/group IDs
│   ├── verify.sh             # Post-deployment checks
│   └── backup.sh             # Data backup
│
└── docs/
    ├── AUTONOMOUS-SAFETY.md         # Multi-agent safety architecture design
    ├── BROWSER-CONTROL-PLANNING.md  # Future browser automation approaches
    ├── DOCS-REVIEW.md               # Official docs review tracking
    ├── INTEGRATIONS.md              # Telegram, WhatsApp, Discord, Obsidian setup detail
    ├── NODE-EXEC.md                 # Remote Mac node host: setup, config, operations
    ├── SECURITY.md                  # Threat model
    └── TROUBLESHOOTING.md
```

### Ansible Tags

Use `./scripts/provision.sh --tags <tag>` to run specific roles:

| Tag | Role(s) | Day-2 use case |
|-----|---------|----------------|
| `system` | system | Update system packages |
| `docker` | docker | Docker upgrade or group changes |
| `ufw` | ufw | Firewall rule changes |
| `openclaw` | openclaw | Reinstall/update OpenClaw binary |
| `config` | config | Change model, sandbox mode, tool allowlist, elevated tools, auth settings, node exec; **also syncs OAuth tokens** |
| `agents` | agents | Add/remove non-default agents, update Telegram bindings |
| `telegram` | telegram | Update cron prompts or Telegram channel config |
| `whatsapp` | whatsapp | Configure WhatsApp channel for agents using `deliver_channel: whatsapp` |
| `discord` | discord | Configure Discord channel (bot token, guild allowlist) |
| `obsidian-headless` | obsidian-headless | Update Obsidian Sync daemon config |
| `qmd` | qmd | Reinstall qmd, update watchers, force reindex |
| `plugins` | plugins | MCP adapter, Codex/Claude Code/Pi containers, GitHub MCP, deny rules |
| `sandbox` | sandbox | Rebuild custom Docker image |
| `workspace` | workspace | Deploy key rotation, sync changes |

## Local CLI

The OpenClaw CLI is installed locally and configured to talk to the remote gateway over Tailscale. **Prefer `openclaw` commands over SSH** for gateway operations — it's faster and avoids the SSH round-trip.

> **Note for the current Windows control machine:** no local `openclaw` CLI is installed there (`brew` is macOS-only), so every gateway operation runs over SSH instead — `ssh ubuntu@openclaw-vps 'XDG_RUNTIME_DIR=/run/user/1000 openclaw <cmd>'`. The instructions below assume a macOS or Linux control machine.

```bash
# Install
brew install openclaw-cli

# Configure once, from this repo with its backend environment loaded.
# Replace <tailnet> below. Credentials travel through stdin, never command arguments.
(
  set -euo pipefail
  GATEWAY_TOKEN=$(cd pulumi && pulumi stack output openclawGatewayToken --stack prod --show-secrets)
  printf '%s' "$GATEWAY_TOKEN" |
    jq -Rse 'if length == 0 then error("Gateway token is empty")
      else {gateway: {mode: "remote", remote: {
        url: "wss://openclaw-vps.<tailnet>.ts.net", token: .}}} end' |
    openclaw config patch --stdin
)

# Trigger pairing, then approve only this CLI's matching device/request.
openclaw health
tailscale ssh ubuntu@openclaw-vps.<tailnet>.ts.net 'openclaw devices list --json'
tailscale ssh ubuntu@openclaw-vps.<tailnet>.ts.net 'openclaw devices approve <request-id>'
```

After pairing, CLI commands work directly:

```bash
openclaw health              # Gateway health check
openclaw doctor              # Diagnostics and quick fixes
openclaw devices list        # List paired devices
openclaw cron list --all     # List enabled and disabled scheduled jobs
openclaw security audit      # Run security audit (add --deep for thorough scan)
openclaw status              # Session health
```

**When to still use SSH:** systemd service management (`systemctl`, `journalctl`), system-level operations (`sudo`), updating the OpenClaw binary on the server.

## Common Operations

### Phoenix Safety

- `STAGING_PRIVATE_REPOSITORY` names a dedicated private fixture (`pandysp/openclaw-staging-vault`: one README, no notes, no secrets) readable by the staging PAT. Real notes never enter staging. Direct MCP checks are read-only; inference disables gateway and native Claude tools; write tests belong in the staging workspace repositories. Public reads do not prove private access.
- Staging agents are permanently read-only: `openclaw_tools_allow` limits every session (heartbeats included) to the two `*_get_file_contents` MCP reads, `tools.alsoAllow` is cleared, and `openclaw_cli_backends` passes `--tools "" --strict-mcp-config` to the Claude CLI so no native tool can write private content into the publicly backed-up staging workspaces. The smoke test verifies this policy before any MCP read.
- Obsidian Sync is out of Phoenix scope (decided 2026-09-25). The role picks the cloud vault by agent ID (`<agent>-workspace`), so a staging run would attach the public staging backup to the real vault; staging therefore never runs it and receives no Obsidian credentials. Check it read-only on production instead: `systemctl --user status obsidian-headless-main`, `ob sync-status --path ~/.openclaw/workspace`.
- Phoenix runs `scripts/test-workspace-isolation.sh` on the staging VPS: a controlled Git hook in the public staging workspace proves the sync unit executes hooks as UID 1000 with no capabilities, no host config or Docker socket, and no route to the credential proxy (an unrestricted control container proves the proxy is reachable), then confirms the push by anonymous readback and removes its fixture.
- The staging server joins the tailnet as `tag:openclaw-staging` with a key minted per run by the CI OAuth client (`TS_OAUTH_CLIENT_ID`/`TS_OAUTH_SECRET`, tag `tag:ci`): single use, ephemeral, pre-authorized, one hour. No stored auth key exists to expire.
- The tailnet policy (Tailscale admin console, not this repo) makes `tag:ci` the owner of `tag:openclaw-staging`, grants that tag **no source rights** (a one-hour agent host with real credentials must not reach your devices, production or other servers), allows SSH as `ubuntu` from `tag:ci` and the owner, and pins all of this with policy tests. `tag:server` stays the other project's tag.
- `staging.yml` binds the run name, authenticated Tailscale peer and created server IP. Ansible also uses the peer's advertised host keys; a separate SSH probe alone does not authenticate Ansible's connection.
- Teardown first releases the run's own stale Pulumi lock (a timed-out step kills Pulumi and leaves one), then destroys. It deletes only the proven node's API device ID, then reads it back as absent. If deploy failed before proving a node, only a device with the run's exact name **and** exactly `tag:openclaw-staging` (mintable only via `tag:ci`) counts as the run's own; a name alone never does. Unrelated tailnet changes do not invalidate cleanup. Missing ownership or failed cleanup keeps the staging checkpoint for investigation.
- Deploy retries only on Hetzner `resource_unavailable` (placement rejected before any server exists): production's `cx43` in `nbg1 → fsn1 → hel1`, then the same-class `cpx42` in the same order. Every other failure stops immediately.
- Scheduled orphan cleanup only inventories leftovers. Never restore prefix-wide deletion or remove a checkpoint before all owned cleanup checks pass.

### Deploy Infrastructure (Fresh Server)

Load the [backend environment](./README.md#pulumi-backend) before any Pulumi or provisioning command. This deployment uses R2 and a stack passphrase, not Pulumi Cloud.

```bash
cd pulumi
pulumi up    # Creates server + auto-triggers Ansible provisioning
```

### Provision / Re-provision (Day-2 Operations)

```bash
# Full provision
./scripts/provision.sh

# Config only (model, sandbox, auth settings + OAuth token sync)
./scripts/provision.sh --tags config

# Rebuild sandbox image
./scripts/provision.sh --tags sandbox -e force_sandbox_rebuild=true

# Update cron prompts (edit ansible/group_vars/all.yml first)
./scripts/provision.sh --tags telegram

# Dry run — see what would change
./scripts/provision.sh --check --diff
```

### Refresh OAuth Token (when token expires)

The `openai-codex` OAuth token is valid ~10 days (auto-refreshed by OpenClaw). If the refresh token is ever invalidated:

```bash
# 1. On Windows — re-authenticate with ChatGPT
codex login

# 2. Deploy new token to VPS
./scripts/provision.sh --tags config
```

### Check Server Status

```bash
# Via local CLI (preferred)
openclaw health
openclaw status

# Via Tailscale ping
tailscale ping openclaw-vps

# Via SSH (for systemd-level details)
ssh ubuntu@openclaw-vps.<tailnet>.ts.net 'XDG_RUNTIME_DIR=/run/user/1000 systemctl --user status openclaw-gateway'
ssh ubuntu@openclaw-vps.<tailnet>.ts.net 'XDG_RUNTIME_DIR=/run/user/1000 journalctl --user -u openclaw-gateway -f'
```

### Update OpenClaw

**Important:** Always keep the local CLI, Mac node host, and VPS gateway on the same version. Version mismatches cause protocol errors (e.g., `system.run.prepare` not supported). After upgrading the gateway, upgrade local too:

```bash
# 1. Update VPS gateway (via Ansible — preferred)
./scripts/provision.sh --tags openclaw

# Or via SSH (manual)
ssh ubuntu@openclaw-vps.<tailnet>.ts.net 'OPENCLAW_NO_ONBOARD=1 OPENCLAW_NO_PROMPT=1 curl -fsSL https://openclaw.ai/install.sh | bash'
ssh ubuntu@openclaw-vps.<tailnet>.ts.net 'XDG_RUNTIME_DIR=/run/user/1000 systemctl --user restart openclaw-gateway'

# 2. Update local CLI + node host to match
brew upgrade openclaw-cli
openclaw node restart   # if node exec is enabled
```

### Run Security Audit

```bash
openclaw security audit --deep
```

**Expected output (as of 2026.6.1):** `0 critical · 3 warn · 2 info` — run it **on the VPS via SSH** (running locally audits your Mac instead). The 3 warnings flag deliberate config and are accepted: `dangerouslyAllowExternalBindSources` (sandbox bind mounts), `tools.exec.security=full` (gateway exec gated by `elevated=false` + node-side approvals), and the multi-user heuristic (Telegram/Discord group allowlists — personal deployment, one trusted operator).

### Destroy Infrastructure

```bash
cd pulumi
pulumi destroy
```

### Clean Up Stale Tailscale Devices

After redeploy, old devices appear as `openclaw-vps-N` (offline) in your Tailscale admin console.

1. Go to https://login.tailscale.com/admin/machines
2. Find offline `openclaw-vps*` devices
3. Click the device → Remove

## Cost Breakdown

Deployed server type is **CX33** (4 vCPU, 8 GB RAM, 80 GB NVMe, ~€6/mo — regional rates vary between €5.49 and €6.59). Change with `pulumi config set serverType <type>`.

| Resource | Cost |
|----------|------|
| Hetzner VPS (CX33) | ~€6/mo |
| Hetzner Backups (20% surcharge, enabled) | ~€1.20/mo |
| Tailscale | Free (personal) |
| ChatGPT Plus | ~$20/mo (covers all OpenClaw model usage) |
| **Total** | **~€7.20/mo + $20/mo** |

## Secrets Reference

| Secret | Purpose | Where to regenerate |
|--------|---------|---------------------|
| R2 access keys | Authenticate to the Pulumi state bucket | Cloudflare → R2 → API tokens (bucket-scoped) |
| Pulumi config passphrase | Decrypts this stack's secrets | Load the current value from private `.env`; coordinate any rotation |
| Hetzner API token | Creates/manages VPS | console.hetzner.cloud → Project → API Tokens |
| Tailscale auth key | Joins server to your network | login.tailscale.com/admin/settings/keys |
| Gateway token | Authenticates browser and CLI sessions (cached after first use) | Auto-generated by Pulumi, view with `pulumi stack output openclawGatewayToken --show-secrets` |
| **Codex auth** (`~/.codex/auth.json`) | **Primary model auth — ChatGPT Plus OAuth (`openai:default` profile) for `openai/gpt-5.6-sol`** | `codex login` locally, then `provision.sh --tags config` |
| Telegram bot token | (Optional) Sends messages via Telegram | @BotFather on Telegram |
| Telegram user/group ID | (Optional) Your Telegram recipient ID | `./scripts/get-telegram-id.sh` or @userinfobot |
| WhatsApp phone number | (Optional) Agent's WhatsApp number (E.164) | `pulumi config set whatsappNiciPhone "+491234567890"` |
| Discord bot token | (Optional) Connects to Discord | Discord Developer Portal → Bot → Token |
| Discord guild/user ID | (Optional) Guild and user IDs for allowlist | Discord Developer Mode → right-click → Copy ID |
| Workspace deploy key | (Optional) Pushes workspace to GitHub | Auto-generated by Pulumi, view public key with `pulumi stack output workspaceDeployPublicKey` |
| xAI API key | (Optional) Enables web search via Grok | x.ai/api → API Keys |
| **OpenAI API key** | **Voice STT (`openai:audio` profile) + TTS (`tts-1`) + memory embeddings — never chat** | platform.openai.com/api-keys |
| GitHub PAT `openclaw-infra-push` | mcp-auth-proxy GitHub token injection | GitHub → Settings → Developer settings → Personal access tokens; stored in `~/.openclaw/github-tokens/main` on VPS + Pulumi secret `githubToken`; **expires 2027-02-22**, scope: `repo` |
| **Google Contacts refresh token** | **Google People API auth for `gcontacts` MCP** | OAuth flow with contacts scope (see Known Technical Debt §5); stored in `~/.gcontacts-mcp/token.json` + Pulumi `gcontactsRefreshToken` |
| Groq API key | (Optional) Legacy — replaced by OpenAI Whisper | console.groq.com → API Keys |
| Gemini API key | (Optional) Enables image generation via Google Gemini | aistudio.google.com → API Keys |
| Obsidian auth token | (Optional) Authenticates with Obsidian Sync API | `ob login` locally, copy from `~/.obsidian-headless/auth_token` |
| Obsidian vault password | (Optional) E2EE encryption for Obsidian Sync vaults | User-chosen password |

## Security DO's and DON'Ts

### DO

- Use a **dedicated Hetzner project** for OpenClaw (isolation from other infra)
- Keep all access through Tailscale
- Use `pulumi config set --secret` for sensitive values
- Run `./scripts/verify.sh` after deployment
- Check that no public ports are exposed
- Scope R2 access keys to the state bucket; load backend credentials from private, ignored `.env` via `direnv exec`
- **Cloud-init log is minimal** (Tailscale bootstrap only, no secrets beyond auth key)
- **Monitor Tailscale admin console** for unauthorized devices: https://login.tailscale.com/admin/machines
- **Rotate Tailscale auth keys periodically** (see [Key Rotation](#key-rotation) below)
- **Review paired OpenClaw devices** regularly: `openclaw devices list` (via local CLI)

### DON'T

- Never share Hetzner tokens between high-risk and production projects
- Never add inbound firewall rules
- Never bind OpenClaw to 0.0.0.0
- Never commit `.env` files or API keys
- Never use password SSH authentication
- **Never use Anthropic Setup-Token in OpenClaw** — Anthropic ToS prohibits subscription access via third-party gateways

### Key Rotation

Update secret via `pulumi config set <key> --secret`, then `pulumi up`. Tailscale key: `tailscaleAuthKey`. OpenAI OAuth: `codex login` locally + `provision.sh --tags config`. Gateway token: redeploy + re-pair devices. Telegram bot: revoke via @BotFather, update `telegramBotToken`, redeploy.

## First-Time Setup

```bash
cd pulumi
pulumi login "${PULUMI_BACKEND_URL:?Load the backend environment from README.md first}"
pulumi stack init prod

# Required secrets
pulumi config set hcloud:token --secret
pulumi config set tailscaleAuthKey --secret

# OAuth model auth (required — run locally first)
codex login   # authenticates with ChatGPT Plus, writes ~/.codex/auth.json
# provision.sh will sync this automatically

# Optional features
pulumi config set xaiApiKey --secret               # web search via Grok
pulumi config set telegramBotToken --secret        # Telegram integration
pulumi config set telegramUserId "YOUR_USER_ID"
pulumi config set workspaceRepoUrl "git@github.com:YOU/openclaw-workspace.git"

pulumi up          # creates server + auto-runs Ansible
cd ..
./scripts/verify.sh

```

Connect the local CLI using the [one-time setup above](#local-cli).

### Device Pairing

New browser or CLI client requires one-time approval:

1. Open `https://openclaw-vps.<tailnet>.ts.net/chat` — you'll see "pairing required"
2. Approve via SSH (required for very first device) or paired CLI:
   ```bash
   ssh ubuntu@openclaw-vps.<tailnet>.ts.net 'openclaw devices list'
   ssh ubuntu@openclaw-vps.<tailnet>.ts.net 'openclaw devices approve <request-id>'
   ```
3. Refresh browser — authenticated via Tailscale identity

If pairing fails, inspect the matching request over Tailscale SSH. Do not print or share token-bearing URLs.

## Workspace Git Sync (Optional)

The agent's workspace (`~/.openclaw/workspace`) contains memories, notes, skills, and prompts. Syncing it to a private GitHub repo gives you version history, visibility into agent changes, and continuous backup.

**Multi-agent note:** Workspace definitions are auto-generated from `openclaw_agents` (see [Multi-Agent Setup](#multi-agent-setup-optional)). Each agent gets a workspace at `~/.openclaw/workspace-<id>` (or `~/.openclaw/workspace` for main). Run `setup-workspace.sh <agent-id>` for each agent that needs git sync.

### Setup

```bash
./scripts/setup-workspace.sh <agent-id>   # creates repo, deploy key, Pulumi config
pulumi up   # or: ./scripts/provision.sh --tags workspace
```

A per-agent hourly systemd timer (`workspace-git-sync-<agent-id>`) runs Git in a one-shot isolated container (only that workspace and its deploy key mounted, UID 1000, no capabilities, outbound SSH to GitHub only). It commits local changes, merges `origin/main`, and pushes; a conflict fails the run and preserves both histories. Deploy key: `pulumi stack output workspaceDeployPublicKey`.

### Verify Workspace Sync

```bash
# Requires SSH (systemd timer management). Replace `main` with the agent ID.
ssh ubuntu@openclaw-vps.<tailnet>.ts.net 'XDG_RUNTIME_DIR=/run/user/1000 systemctl --user status workspace-git-sync-main.timer'
ssh ubuntu@openclaw-vps.<tailnet>.ts.net 'XDG_RUNTIME_DIR=/run/user/1000 systemctl --user start workspace-git-sync-main.service'
ssh ubuntu@openclaw-vps.<tailnet>.ts.net 'XDG_RUNTIME_DIR=/run/user/1000 journalctl --user -u workspace-git-sync-main.service -n 20 --no-pager'
```

Do not run host-side Git inside an agent workspace: the agent can write `.git/config` and hooks, so even `git log` on the host executes agent-controlled code outside the sandbox. Inspect history on GitHub or in a fresh clone instead.

## Web Search (Optional)

Configured via Pulumi secret `xaiApiKey`. If not set, deployment proceeds without web search. Uses Grok (xAI) for agentic search — search + read + synthesize in one API call. Get an API key at [x.ai/api](https://x.ai/api) (only needs `/v1/responses` endpoint + Language models).

```bash
cd pulumi
pulumi config set xaiApiKey --secret   # From x.ai/api → API Keys
pulumi up                               # Or: ./scripts/provision.sh --tags config
```

### Verify Web Search

```bash
# Via local CLI
openclaw health   # Should show web search as enabled
```

To disable, remove the key and re-provision:

```bash
cd pulumi
pulumi config rm xaiApiKey
./scripts/provision.sh --tags config
```

## Two-Way Voice / TTS

The agent uses **OpenAI TTS** (`tts-1`, voice `onyx`) to respond with Telegram voice bubbles when the user sends a voice message. Voice → text (Whisper STT) → response → voice (TTS) forms a complete two-way voice loop.

### Config keys (on VPS `~/.openclaw/openclaw.json`)

| Key | Value | Notes |
|-----|-------|-------|
| `messages.tts.provider` | `"openai"` | In Ansible `all.yml` as `openclaw_tts_provider` |
| `messages.tts.auto` | `"inbound"` | Reply with voice only when user sent voice; in Ansible |
| `messages.tts.providers.openai.apiKey` | `sk-proj-…` | ⚠️ Set manually on VPS; NOT yet in Ansible |
| `messages.tts.providers.openai.model` | `"tts-1"` | ⚠️ Set manually on VPS; NOT yet in Ansible |
| `messages.tts.providers.openai.voice` | `"onyx"` | Deep/authoritative; ⚠️ Set manually on VPS; NOT yet in Ansible |

**`auto` modes:** `"inbound"` (default) = voice reply only when user sends voice · `"always"` = always voice · `"off"` = disabled

### ⚠️ Ansible gap — provider-specific TTS config is NOT idempotent

`ansible/roles/config/tasks/main.yml` sets `messages.tts.provider` and `messages.tts.auto` (both in `all.yml`), but does **NOT** set `messages.tts.providers.openai.apiKey/model/voice`. Running `./scripts/provision.sh --tags config` will **not** wipe the voice settings (they are separate keys), but if the VPS is rebuilt from scratch the TTS provider-specific config must be re-applied manually:

```bash
export PATH=/home/ubuntu/.npm-global/bin:/usr/local/bin:/usr/bin:/bin
ssh ubuntu@openclaw-vps "XDG_RUNTIME_DIR=/run/user/1000 openclaw config set messages.tts.providers.openai.apiKey <sk-proj-...>"
ssh ubuntu@openclaw-vps "XDG_RUNTIME_DIR=/run/user/1000 openclaw config set messages.tts.providers.openai.model tts-1"
ssh ubuntu@openclaw-vps "XDG_RUNTIME_DIR=/run/user/1000 openclaw config set messages.tts.providers.openai.voice onyx"
ssh ubuntu@openclaw-vps "XDG_RUNTIME_DIR=/run/user/1000 systemctl --user restart openclaw-gateway"
```

### Change voice

Available OpenAI voices: `alloy`, `echo`, `fable`, `onyx` (deep/male), `nova` (clear/female), `shimmer` (warm/female).

```bash
ssh ubuntu@openclaw-vps "XDG_RUNTIME_DIR=/run/user/1000 openclaw config set messages.tts.providers.openai.voice <voice>"
ssh ubuntu@openclaw-vps "XDG_RUNTIME_DIR=/run/user/1000 systemctl --user restart openclaw-gateway"
```

### Verify TTS

```bash
# Check config
ssh ubuntu@openclaw-vps "XDG_RUNTIME_DIR=/run/user/1000 openclaw config get messages.tts"
# Send a voice message via Telegram → agent should reply with a waveform voice bubble
```

## Telegram Integration (Optional)

Pulumi secrets: `telegramBotToken` (from @BotFather) + `telegramUserId`. Use `./scripts/get-telegram-id.sh` to discover user/group IDs. Declares two default cron jobs for the main agent (Europe/Berlin timezone); they remain disabled until the agent explicitly opts into scheduled automation:

| Job | Schedule | Purpose |
|-----|----------|---------|
| **Daily Standup** | 09:30 daily | Summarize what needs attention today |
| **Night Shift** | 23:00 daily | Review notes, organize, triage tasks, prepare morning summary |

```bash
pulumi config set telegramBotToken --secret && pulumi config set telegramUserId "123456789"
./scripts/provision.sh --tags telegram   # after editing group_vars/openclaw.yml for custom schedules
openclaw channels status && openclaw cron list
```

**Read [docs/INTEGRATIONS.md#telegram-integration](./docs/INTEGRATIONS.md#telegram-integration) in full when:** first-time Telegram setup, adding group chat routing, customizing cron schedules, or using `get-telegram-id.sh`.

## WhatsApp Integration (Optional)

Uses Baileys/WhatsApp Web protocol (not official Business API). Since openclaw 2026.5.12 the channel ships as the external `@openclaw/whatsapp` plugin — the whatsapp role installs it pinned to `openclaw_version` and the config role allowlists it; channel config stays at `channels.whatsapp.*`. **Sessions expire every ~14 days** — a health-check cron alerts via Telegram when re-authentication is needed. Set `deliver_channel: "whatsapp"` in the agent's `openclaw.yml` entry.

```bash
pulumi config set whatsappNiciPhone "+491234567890"
./scripts/provision.sh --tags config,agents,telegram,whatsapp
ssh ubuntu@openclaw-vps 'XDG_RUNTIME_DIR=/run/user/1000 openclaw channels login --channel whatsapp --qr-terminal'
ssh ubuntu@openclaw-vps 'XDG_RUNTIME_DIR=/run/user/1000 openclaw channels status --probe'
```

**Read [docs/INTEGRATIONS.md#whatsapp-integration](./docs/INTEGRATIONS.md#whatsapp-integration) in full when:** first-time WhatsApp setup (agent config, phone format) or re-scanning QR after session expiry.

## Discord Integration (Optional)

Built-in channel with automatic **per-channel session isolation** — each Discord channel gets its own session context with no extra config. No QR code; persistent bot token with no session expiry. Pulumi secrets: `discordBotToken`, `discordGuildId`, `discordUserId`.

```bash
pulumi config set discordBotToken --secret && pulumi config set discordGuildId "ID" && pulumi config set discordUserId "ID"
./scripts/provision.sh --tags discord
ssh ubuntu@openclaw-vps 'XDG_RUNTIME_DIR=/run/user/1000 openclaw channels status'
```

**Read [docs/INTEGRATIONS.md#discord-integration](./docs/INTEGRATIONS.md#discord-integration) in full when:** first-time Discord setup (bot creation, required intents, invite scopes) or troubleshooting Discord connection.

## Obsidian Headless Sync (Optional)

Two-way sync between agent workspaces and Obsidian Sync for mobile access. Requires Obsidian Sync subscription. **Auth token may expire if subscription lapses** — re-run `ob login` locally, update the Pulumi secret, and re-provision.

```bash
ob login   # locally, creates ~/.obsidian-headless/auth_token
pulumi config set obsidianAuthToken --secret && pulumi config set obsidianVaultPassword --secret
# Enable in openclaw.yml: obsidian_headless_enabled: true, obsidian_headless_agents: [main]
./scripts/provision.sh --tags obsidian-headless
ssh ubuntu@openclaw-vps 'XDG_RUNTIME_DIR=/run/user/1000 systemctl --user status obsidian-headless-main'
```

**Read [docs/INTEGRATIONS.md#obsidian-headless-sync](./docs/INTEGRATIONS.md#obsidian-headless-sync) in full when:** first-time Obsidian setup or diagnosing token expiry.

## Multi-Agent Setup (Optional)

By default, a single `main` agent is configured. To add more agents, define `openclaw_agents` in `openclaw.yml` (see `openclaw.yml.example`).

### How It Works

`openclaw_agents` is the **single source of truth**. The `playbook.yml` pre_tasks automatically derive:

| Derived variable | Generated from | Used by |
|---|---|---|
| `_openclaw_mcp_servers` | `openclaw_agents` x `openclaw_mcp_server_types` | plugins role (MCP server config, deny rules) |
| `_openclaw_workspaces` | `openclaw_agents` + provision.sh secrets | workspace, qmd, obsidian-headless, plugins roles |

Scheduled automation is separately opt-in. Agent creation keeps chat online but
does not enable heartbeats or cron jobs. Set
`scheduled_automation_enabled: true` on an agent to apply its latent
`heartbeat_every` cadence and enable its declared cron jobs. Setting it false
removes the agent heartbeat block and disables all of that agent's cron jobs in
place, preserving IDs and run history. A job-level `enabled: false` remains off
even while its agent is enabled.

**Naming conventions** (mechanical, from agent ID):

| Resource | main | other (e.g., `bob`) |
|---|---|---|
| MCP server | `github`, `codex`, `claude` | `github-bob`, `codex-bob`, `claude-bob` |
| Workspace dir | `~/.openclaw/workspace` | `~/.openclaw/workspace-bob` |
| Deploy key var | `workspace_deploy_key` | `workspace_bob_deploy_key` |
| GitHub token var | `github_token` | `github_token_bob` |

### Adding an Agent

1. Add the agent to `openclaw_agents` in `openclaw.yml`; set `scheduled_automation_enabled: true` only when autonomous work is intended
2. Wire per-agent secrets through `scripts/provision.sh` (Pulumi config or env vars)
3. Run `./scripts/provision.sh`

MCP servers, workspaces, deny rules, and token mappings are generated automatically. Cron jobs remain manual (personal config — add to `openclaw.yml`).

### Role Ordering

`config` -> `agents` -> `telegram` -> `whatsapp` -> `discord` -> `obsidian-headless` -> `qmd` -> `plugins` -> `sandbox` -> `workspace`

Telegram must run immediately after agents (prevents message misrouting). Plugins after qmd (qmd binary needed for MCP registration).

## Sandboxing

All sessions (including web chat) run in Docker containers with bridge networking and a custom sandbox image with a dev toolchain.

| | All sessions (web chat, cron, Telegram) |
|---|---|
| Runtime | Docker container (`openclaw-sandbox-custom:latest`) |
| Network | Bridge (outbound internet via Docker NAT) |
| Workspace | Read-write (mounted at `/workspace`) |
| Host filesystem | No access |
| Gateway config | Isolated (can't read `~/.openclaw/`) |
| Privilege escalation | Blocked (setuid bits stripped) |
| Dev toolchain | Python 3, Node.js, git, git-lfs, ripgrep, fd, jq, yq, just, uv, pnpm, bd, sqlite3, pandoc, build-essential, ffmpeg, imagemagick, tmux, htop, tree, curl, wget, openssh-client |

**Network:** Bridge (outbound internet for web research/git push). MCP containers (Codex, Claude Code, Pi) use a separate `codex-proxy-net`. Sandbox containers can't reach the credential proxy.

**Custom image:** Two layers built locally: base (`openclaw-sandbox:trixie`, Debian 13) + custom (`openclaw-sandbox-custom:latest`). Neither pulled from registry. Rebuild: `./scripts/provision.sh --tags sandbox -e force_sandbox_rebuild=true`.

**Config:**
```
agents.defaults.sandbox.mode: all
agents.defaults.sandbox.workspaceAccess: rw
agents.defaults.sandbox.docker.network: bridge
agents.defaults.sandbox.docker.image: openclaw-sandbox-custom:latest
agents.defaults.sandbox.docker.readOnlyRoot: false
```

**Writable rootfs** (`readOnlyRoot: false`): UID 1000 + `--cap-drop ALL` blocks writes to system dirs; only `/home/node/` writable. Runtime installs (`pip install`, `npm install -g`) persist for the container's lifetime. Persistent installs: `/workspace/.venv/` or `/workspace/.packages/`. See [docs/SECURITY.md](./docs/SECURITY.md#writable-rootfs-rationale).

**Tool access:** All standard tool groups enabled; elevated tools enabled (with Telegram approval gate if configured). Change via `./scripts/provision.sh --tags config`.

## Remote Node Control (Mac)

> **Disabled by default.** Node exec runs arbitrary shell commands on your Mac with full user permissions — no sandbox. Enable with `node_exec_enabled: true` in `group_vars/all.yml`. Read [docs/SECURITY.md](./docs/SECURITY.md) section 5 first.

Architecture: VPS sandbox → `node-exec-mcp` (OPENCLAW_GATEWAY_TOKEN auth, Tailscale Serve) → LaunchAgent on Mac. Each agent gets a scoped `mac_run` tool (`mac-<id>_run` for non-main agents).

**Key gotchas:**
- Two approval layers: gateway (`tools.exec.security/ask`) AND node (`~/.openclaw/exec-approvals.json`, must have `defaults.security: full`) — both must allow the command
- CWD defaults to `/tmp` — VPS workspace path doesn't exist on Mac; pass `workdir=/Users/<you>` explicitly
- LaunchAgent plist patched to `/opt/homebrew/bin/openclaw` symlink (survives `brew upgrade`)
- **Token wipe danger:** An empty remote token breaks the node host's authentication. The [Local CLI setup](#local-cli) rejects an empty token and feeds the config through stdin rather than exposing it in command arguments. `setup-mac-node.sh` detects and recovers a wiped token at setup time. Diagnosis: check `~/.openclaw/openclaw.json` → `gateway.remote.token` is non-empty; backups live in `.bak` files

```bash
./scripts/setup-mac-node.sh                     # one-time Mac setup (installs LaunchAgent, sets approvals)
./scripts/provision.sh --tags config,plugins    # install node-exec-mcp, pin node ID
openclaw node status / restart / stop           # manage Mac LaunchAgent
ssh ubuntu@openclaw-vps 'openclaw nodes status' # check from VPS side
```

**Read [docs/NODE-EXEC.md](./docs/NODE-EXEC.md) in full when:** first-time setup, debugging connection failures, resetting node ID after re-pairing, or changing exec approval settings.

## Semantic Search (qmd)

Each agent has a **qmd** instance providing local hybrid search (BM25 + vector + LLM reranking) over their workspace. Uses GGUF models (~1.5GB, auto-downloaded) — no API keys needed. Replaces the built-in `memorySearch` with 6 MCP tools per agent (6 × N_agents total).

**Collections per agent:**
- `workspace` — all `.md`, `.txt`, `.csv` files in the workspace
- `memory` — memory directory (`.md` files only)
- `extracted-content` — text extracted from PDFs, images, `.docx`, `.xlsx`

**Tool count:** `N_agents × Σ(tools_per_server_type)`. Per agent: github: 26, codex: 2, claude-code: 2, pi: 2, qmd: 6. Check `openclaw_mcp_server_types` in `group_vars/all.yml`.

**Operations:**
```bash
# Rebuild qmd index (force re-embed all documents)
./scripts/provision.sh --tags qmd -e force_qmd_reindex=true

# Check watcher status
ssh ubuntu@openclaw-vps 'XDG_RUNTIME_DIR=/run/user/1000 systemctl --user status qmd-watch-main'

# View watcher logs
ssh ubuntu@openclaw-vps 'XDG_RUNTIME_DIR=/run/user/1000 journalctl --user -u qmd-watch-main -f'

# Verify qmd MCP servers in plugin config
ssh ubuntu@openclaw-vps 'openclaw config get plugins.entries.openclaw-mcp-adapter.config' | jq '.servers[] | select(.name | startswith("qmd"))'
```

**RAM:** `deep_search` loads ~2.1GB GGUF models on-demand. The CX33 has **8 GB and no swap configured** (verified 2026-08-24), which is comfortable for the single `main` agent but leaves little headroom — check free memory before adding agents, or move to a larger server type.

## Known Technical Debt / Manual VPS Patches

These items are **active on the VPS** but not fully persisted in Ansible. They must be re-applied manually if the VPS is rebuilt from scratch.

### 1. TTS provider-specific config (OpenAI TTS)

See [Two-Way Voice / TTS](#two-way-voice--tts) above. Keys `messages.tts.providers.openai.apiKey/model/voice` are set directly on VPS — not in Ansible.

**TODO:** Add these to `ansible/roles/config/tasks/main.yml` inside the `openai_api_key` guard, using `check_set` commands, similar to the Whisper audio block.

### 2. Google Drive MCP patch (`@modelcontextprotocol/server-gdrive`)

**Problem:** v2025.1.14 of `@modelcontextprotocol/server-gdrive` creates an OAuth2 client without `client_id`/`client_secret`, causing all token refresh requests to fail with `invalid_request` (HTTP 400).

**VPS fix:** `/home/ubuntu/.npm-global/lib/node_modules/@modelcontextprotocol/server-gdrive/dist/index.js` was patched so `loadCredentialsAndRunServer()` reads `client_id`/`client_secret` from `GDRIVE_OAUTH_PATH` and passes them to `new google.auth.OAuth2()`. Backup at `index.js.bak`.

**Risk:** If `./scripts/provision.sh --tags plugins` is run (reinstalls npm packages), the patch will be overwritten. After plugins reprovisioning, re-apply the patch:

```bash
# Check if patch is still active
ssh ubuntu@openclaw-vps "grep -c 'oauthKeysPath' /home/ubuntu/.npm-global/lib/node_modules/@modelcontextprotocol/server-gdrive/dist/index.js"
# Returns 1 if patched, 0 if wiped

# Re-apply after plugins reprovisioning (use the backup as reference):
ssh ubuntu@openclaw-vps "cat /home/ubuntu/.npm-global/lib/node_modules/@modelcontextprotocol/server-gdrive/dist/index.js.bak"
```

**TODO:** Either pin the package to a fixed version that includes the fix, or automate the patch via Ansible post-install task. Track upstream: https://github.com/modelcontextprotocol/servers

### 3. GitHub PAT renewal reminder

Token `openclaw-infra-push` expires **2027-02-22**. When it expires:
1. Generate new token at GitHub → Settings → Developer settings → Fine-grained personal access tokens
2. Scope: `Contents: read/write`, `Metadata: read` on workspace repo
3. `pulumi config set githubToken ghp_... --secret`
4. `ssh ubuntu@openclaw-vps "echo 'ghp_...' > ~/.openclaw/github-tokens/main"` (hot-reloaded automatically by mcp-auth-proxy)

### 4. Google Calendar MCP patch (`mcp-google-calendar`)

**Problem:** v0.0.5 hardcodes `{ date: event.start }` in `createEvent` and `updateEvent`, forcing every calendar entry to be an all-day event. `timeZone` was not supported at all.

**VPS fix (2026-04-17):** Two files patched:
- `dist/tools/schemas.js` — `start`/`end` now accept both `YYYY-MM-DD` (all-day) and `YYYY-MM-DDTHH:MM:SS` (timed). New optional `timeZone` field (IANA, e.g. `Europe/Berlin`) added.
- `dist/services/google-calendar.js` — `createEvent` and `updateEvent` now detect `T` in the date string and switch between `{ dateTime, timeZone }` and `{ date }` accordingly.

**Ansible:** Patch is automated in `ansible/roles/plugins/tasks/main.yml` (after npm install) and survives reprovisioning. Backups at `schemas.js.bak` and `google-calendar.js.bak`.

```bash
# Verify patch is active
ssh ubuntu@openclaw-vps 'grep -c "includes.*T.*dateTime" /home/ubuntu/.npm-global/lib/node_modules/mcp-google-calendar/dist/services/google-calendar.js'
# Returns 2 if patched (createEvent + updateEvent), 0 if wiped
```

### 5. Google Contacts MCP (`google-contacts-mcp`)

**Added (2026-04-17):** New MCP server for Google People API. Provides `contact_create`, `contact_update`, `contact_get`, `contacts_list`, `contacts_search`, `contact_delete`, `directory_search`.

**Auth:** Uses a separate OAuth token (`~/.gcontacts-mcp/token.json`) obtained with `https://www.googleapis.com/auth/contacts` scope. A wrapper script (`google-contacts-mcp-wrapper`) exchanges the refresh token for a short-lived access token on each session start.

**Required:** Google People API must be enabled in the Google Cloud project:
`https://console.developers.google.com/apis/api/people.googleapis.com/overview?project=814055706430`

**Pulumi secret:** `gcontactsRefreshToken` — must be set after initial OAuth authorization. If VPS is rebuilt:
1. Copy credentials from `~/.gcal-mcp/credentials.json` to `~/.gcontacts-mcp/`
2. Re-run OAuth flow (contacts scope) to get new refresh token
3. `pulumi config set gcontactsRefreshToken <token> --secret`
4. `./scripts/provision.sh --tags plugins`

```bash
# Verify contacts MCP is running
ssh ubuntu@openclaw-vps 'PATH=/home/ubuntu/.npm-global/bin:$PATH timeout 4 google-contacts-mcp-wrapper 2>&1'
# Expected: "Google Contacts MCP server running on stdio"
```

### 6. GitHub PAT renewal reminder

Token `openclaw-infra-push` expires **2027-02-22**. When it expires:
1. Generate new token at GitHub → Settings → Developer settings → Fine-grained personal access tokens
2. Scope: `Contents: read/write`, `Metadata: read` on workspace repo
3. `pulumi config set githubToken ghp_... --secret`
4. `ssh ubuntu@openclaw-vps "echo 'ghp_...' > ~/.openclaw/github-tokens/main"` (hot-reloaded automatically by mcp-auth-proxy)

### 8. openclaw-mcp-adapter activation + tool registration fix (OpenClaw v2026.5.x)

**Problem (three-part):** OpenClaw v2026.5.x broke global plugin tool loading in three ways:
1. Plugins require `"activation": {"onStartup": true}` in manifest or are silently skipped
2. Stale `extensions/openclaw-mcp-adapter/` causes "duplicate plugin id" error
3. `api.registerTool(object)` form (v0.1.6 default) bypasses runtime plugin registry — tools don't reach agent bundles. `contracts.tools` missing from manifest means `resolvePluginToolRuntimePluginIds` never finds the plugin.

**Fix (automated in Ansible as of 2026-05-06):** All patches run automatically on `provision.sh --tags plugins`:
1. `activation.onStartup: true` added to manifest
2. `contracts.tools` with 35 tool names added to manifest (owned tools declared for OpenClaw tool pipeline)
3. `dist/index.js` patched: object form → factory form `api.registerTool((_ctx) => ({...}), { names: [toolName] })`
4. `tools.sandbox.tools.allow` updated: 35 individual tool names instead of non-functional `openclaw-mcp-adapter` entry
5. `plugin_dir` stat corrected to npm path — install block was always triggering (and failing) on the old extensions path

**If MCP tools disappear after a gateway update:**
```bash
./scripts/provision.sh --tags plugins
```

### 7. Upstream sync state (as of 2026-04-17)

Synced upstream `pandysp/openclaw-infra` → local (as of 2026-04-14). Key upstream change taken: `agents.defaults.skipBootstrap=true` (commit `4930402`) — prevents context file wipes (SOUL.md, IDENTITY.md etc.) during agent restart. Local customizations preserved: `openai-codex/gpt-5.4` primary model, OAuth sync task, Gmail MCP secrets, TTS additions, Google Calendar datetime patch, Google Contacts MCP.

Local commits are **not yet pushed** to origin. Run `git log --oneline origin/main..HEAD` to see what's ahead.

## Troubleshooting

See [docs/TROUBLESHOOTING.md](./docs/TROUBLESHOOTING.md) for all troubleshooting procedures.

Quick diagnostics:
```bash
# Via local CLI (preferred)
openclaw health
openclaw doctor

# Via SSH (for systemd-level details)
ssh ubuntu@openclaw-vps.<tailnet>.ts.net 'XDG_RUNTIME_DIR=/run/user/1000 systemctl --user status openclaw-gateway'
ssh ubuntu@openclaw-vps.<tailnet>.ts.net 'XDG_RUNTIME_DIR=/run/user/1000 journalctl --user -u openclaw-gateway -n 50'

# Verify deployment
./scripts/verify.sh
```

### OAuth Token Issues

```bash
# Check current auth profile on VPS
ssh ubuntu@openclaw-vps 'python3 -c "
import json; s = json.load(open(\"/home/ubuntu/.openclaw/agents/main/agent/auth-profiles.json\"))
p = s[\"profiles\"].get(\"openai-codex:default\", {})
import time; exp = p.get(\"expires\", 0) / 1000
print(\"Has token:\", bool(p.get(\"access\")))
print(\"Expires in:\", int(exp - time.time()), \"seconds\")
"'

# Check proxy health (includes openai_api status)
ssh ubuntu@openclaw-vps 'curl -s http://172.18.0.1:8787/health | python3 -m json.tool'

# Verify active model
ssh ubuntu@openclaw-vps 'PATH=/home/ubuntu/.npm-global/bin:$PATH XDG_RUNTIME_DIR=/run/user/1000 openclaw models list'

# Force token refresh if needed
codex login   # locally on Windows
./scripts/provision.sh --tags config
```

## Roadmap / Nächste Schritte

### Kurzfristig (nächste Session)

**Memory, Skills & Soul perfektionieren** — höchste Priorität. Ziel: Der Agent soll ein konsistentes, langlebiges Gedächtnis haben, das über Sessions hinaus trägt. Konkret:
- `SOUL.md` und `IDENTITY.md` im Agenten-Workspace überarbeiten und schärfen — Persönlichkeit, Arbeitsweise, Prioritäten des Nutzers
- Skills strukturieren: welche Fähigkeiten soll der Agent zuverlässig beherrschen, wie werden sie im Workspace hinterlegt
- Memory-Struktur: Kontakte, laufende Projekte, Präferenzen, Freigabe-Logik sauber als persistente Notizen im Workspace ablegen
- qmd-Semantic-Search als primäres Erinnerungswerkzeug nutzen und testen

**Notion-Integration** — offizieller MCP-Server (`@notionhq/notion-mcp-server`), braucht nur Notion API-Key. Für Aufgaben, Projekte, CRM aus dem Agenten heraus.

### Mittelfristig

**Browser-Steuerung** — Mac Node-Exec + Playwright lokal. Erlaubt dem Agenten, Websites, Formulare, CRM und Web-UIs wirklich zu bedienen statt nur Screenshots zu lesen.

**Freigabe-Logik definieren** — welche Aktionen darf der Agent direkt ausführen (E-Mails an bekannte Kontakte, Kalendereinträge), welche brauchen Bestätigung (externe unbekannte Empfänger, Dateioperationen). In OpenClaw als Elevated-Tools-Config konfigurierbar.

**Proaktive Automationen ausbauen** — Mail-Triagen, Nachfass-Erinnerungen, tägliche Kurzbriefings als zusätzliche Cron-Jobs in `group_vars/all.yml`.

### Bekannte Lücken (nice-to-have)

- Datev/lexoffice/Sevdesk-Integration (kein fertiger MCP — ggf. custom)
- LinkedIn/Xing (stark eingeschränkte APIs — wahrscheinlich nur via Browser-Steuerung)
- Banking (read-only via FinAPI oder ähnlichem)
