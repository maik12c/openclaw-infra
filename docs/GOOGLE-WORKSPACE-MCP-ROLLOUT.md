# Rollout: Google-MCP-Server auf workspace-mcp umstellen

Stand 2026-11-05. Vorbereitet auf Zweig `claude/workspace-mcp-3c500f`, auf dem VPS ist nichts geändert.

## Worum es geht

Drei MCP-Server im openclaw-mcp-adapter hängen an Paketen ohne Pflege und mit verwundbaren
Abhängigkeiten (nodemailer, proxy-addr):

| Typ | Paket | Zustand |
|-----|-------|---------|
| `gmail` | `@gongrzhe/server-gmail-autoauth-mcp` | Repo archiviert |
| `gdrive` | `@modelcontextprotocol/server-gdrive` | abgekündigt, lokal gepatcht |
| `gcal` | `mcp-google-calendar` 0.0.5 | ungepflegt, lokal gepatcht |

Ersatz ist ein einziger Server vom Typ `gworkspace`: `workspace-mcp` 2.3.0 (PyPI,
github.com/taylorwilsdon/google_workspace_mcp) über stdio. `google-contacts-mcp` (`gcontacts`)
bleibt, wie er ist.

## Was der Zweig ändert

**Schalter** (`ansible/group_vars/all.yml`, überschreibbar in `group_vars/openclaw.yml`):

| Variable | Standard | Wirkung |
|----------|----------|---------|
| `google_mcp_legacy_enabled` | `true` | erzeugt gmail/gdrive/gcal-Server, installiert deren npm-Pakete, wendet den Kalender-Patch an |
| `google_workspace_mcp_enabled` | `false` | erzeugt den `gworkspace`-Server, installiert uv und workspace-mcp |
| `google_mcp_legacy_uninstall` | `false` | entfernt die drei alten npm-Pakete, nur wenn Legacy aus ist |
| `google_workspace_user_email` | `""` | Google-Konto, als das der Agent handelt (`USER_GOOGLE_EMAIL`) |

Mit den Standardwerten erzeugt der Zweig genau die heutige Konfiguration.

Die Schalter greifen in `playbook.yml`: Abgeschaltete Typen fliegen aus
`openclaw_mcp_server_types`, bevor das Kreuzprodukt mit `openclaw_agents` gebildet wird. Damit
entstehen für einen abgeschalteten Typ weder Server noch Deny-Regeln noch `contracts.tools`-Einträge.
Wer `openclaw_mcp_servers` von Hand setzt, umgeht die Schalter.

**Plugins-Rolle** (`ansible/roles/plugins/tasks/main.yml`):

- `_mcp_contract_tools` ersetzt `openclaw_mcp_adapter.contract_tools` in Manifest-Patch und
  Sandbox-Allowlist: Grundliste + alte Google-Namen (wenn Legacy an) + `gworkspace_<tool>` (wenn neu an).
- uv 0.13.5 aus dem GitHub-Release, per SHA-256 geprüft, nach `~/.local/bin`.
- `uv tool install --exclude-newer 2026-10-31T00:00:00Z workspace-mcp==2.3.0`. Bewusst kein `uvx`
  bei jedem Start: Der Gateway-Start braucht so keine Registry-Abfrage, und transitive Abhängigkeiten
  bleiben auf dem Stand des Releases.
- `~/.google_workspace_mcp/client_secret.json` (0600) aus den vorhandenen Variablen `gmail_client_id`
  und `gmail_client_secret`. Client-ID und Secret stehen damit nicht in `openclaw.json`.
- Der Kalender-Patch läuft weiterhin nur, wenn gcal-Server erzeugt werden, also nur für den alten Typ.
- Nebenbei behoben: `npm_prefix_bin` wurde erst nach dem Kalender-Patch gesetzt, der es schon benutzt.

**Aufruf des neuen Servers** (so landet er in `plugins.entries.openclaw-mcp-adapter.config.servers`):

```
/home/ubuntu/.local/bin/workspace-mcp \
  --permissions gmail:full drive:readonly calendar:full \
  --tool-tier complete
```

Env: `GOOGLE_CLIENT_SECRET_PATH`, `WORKSPACE_MCP_CREDENTIALS_DIR=~/.google_workspace_mcp/credentials`,
`WORKSPACE_MCP_HOST=127.0.0.1`, `WORKSPACE_MCP_PORT=8000`, `WORKSPACE_MCP_TRANSPORT=stdio`,
`MCP_SINGLE_USER_MODE=1`, optional `USER_GOOGLE_EMAIL`, dazu `HOME` und `PATH`.

Abweichungen vom ursprünglichen Auftrag (`uvx workspace-mcp --tools gmail drive calendar`):

- `--permissions` statt `--tools`. Beides zusammen verbietet workspace-mcp. `--permissions` wählt die
  Dienste genauso aus, begrenzt aber zusätzlich die OAuth-Scopes. `drive:readonly` entspricht dem
  alten gdrive-Server (nur suchen und lesen). Wer Drive-Schreibzugriff will, setzt `drive:full` und
  ergänzt `google_workspace_mcp.tools`.
- `gmail:full` heißt bei workspace-mcp: lesen, Labels, Drafts, Senden, Filter
  (`gmail.settings.basic`). Den Scope `https://mail.google.com/` fordert es nie an. Endgültiges Löschen
  geht also nicht mehr, nur Papierkorb.
- `WORKSPACE_MCP_HOST=127.0.0.1`: Ohne diese Variable bindet der OAuth-Callback-Server auf `0.0.0.0`,
  und UFW lässt `tailscale0` durch. Der Callback wäre dann im ganzen Tailnet erreichbar.

## Tool-Zuordnung alt → neu

Neue Namen tragen im Adapter das Präfix `gworkspace_`. Parameter sind jetzt snake_case
(`message_id` statt `messageId`). Ergebnisse kommen als formatierter Text, nicht als JSON.

### Gmail

| Alt | Neu | Hinweis |
|-----|-----|---------|
| `gmail_search_emails` | `gworkspace_search_gmail_messages` | `query`, `page_size`, `page_token` statt `maxResults` |
| `gmail_read_email` | `gworkspace_get_gmail_message_content` | mehrere: `gworkspace_get_gmail_messages_content_batch` |
| `gmail_send_email` | `gworkspace_send_gmail_message` | Antworten über `thread_id`/`in_reply_to`, Weiterleiten über `forward_message_id` |
| `gmail_draft_email` | `gworkspace_draft_gmail_message` | |
| `gmail_modify_email` | `gworkspace_modify_gmail_message_labels` | `add_label_ids`, `remove_label_ids` |
| `gmail_batch_modify_emails` | `gworkspace_batch_modify_gmail_message_labels` | `message_ids` oder `thread_ids` |
| `gmail_delete_email` | `gworkspace_modify_gmail_message_labels` mit `add_label_ids: ["TRASH"]` | **kein endgültiges Löschen mehr**, nur Papierkorb |
| `gmail_batch_delete_emails` | `gworkspace_batch_modify_gmail_message_labels` mit `TRASH` | wie oben |
| `gmail_list_email_labels` | `gworkspace_list_gmail_labels` | |
| `gmail_create_label` | `gworkspace_manage_gmail_label` (`action: create`) | |
| `gmail_update_label` | `gworkspace_manage_gmail_label` (`action: update`) | |
| `gmail_delete_label` | `gworkspace_manage_gmail_label` (`action: delete`) | |
| `gmail_get_or_create_label` | **keine Entsprechung** | erst `list_gmail_labels`, dann bei Bedarf `manage_gmail_label` |
| `gmail_create_filter` | `gworkspace_manage_gmail_filter` (`action: create`) | `criteria`, `filter_action`; neu: `apply` auf vorhandene Mails |
| `gmail_delete_filter` | `gworkspace_manage_gmail_filter` (`action: delete`) | |
| `gmail_list_filters` | `gworkspace_list_gmail_filters` | |
| `gmail_get_filter` | **keine Entsprechung** | `list_gmail_filters` liefert alle Filter mit ID |
| `gmail_create_filter_from_template` | **keine Entsprechung** | Kriterien explizit an `manage_gmail_filter` übergeben |
| `gmail_download_attachment` | `gworkspace_get_gmail_attachment_content` | speichert auf dem Host (`~/.workspace-mcp/attachments`), nicht im Workspace; Inhalt mit `return_base64: true` direkt |
| — | `gworkspace_get_gmail_thread_content`, `…_threads_content_batch` | neu: ganze Threads |
| — | `gworkspace_start_google_auth` | neu: OAuth-Anmeldung (siehe unten) |

### Drive

| Alt | Neu | Hinweis |
|-----|-----|---------|
| `gdrive_search` | `gworkspace_search_drive_files` | |
| Ressource `gdrive:///<id>` | `gworkspace_get_drive_file_content` | |
| — | `gworkspace_list_drive_items`, `…_get_drive_file_download_url`, `…_get_drive_shareable_link`, `…_get_drive_file_permissions`, `…_check_drive_file_public_access` | neu, alle nur lesend |

### Kalender

| Alt | Neu | Hinweis |
|-----|-----|---------|
| `gcal_list_calendars` | `gworkspace_list_calendars` | |
| `gcal_list_calendar_events` | `gworkspace_get_events` | `time_min`, `time_max`, `query` |
| `gcal_get_calendar_event` | `gworkspace_get_events` mit `event_id` | |
| `gcal_create_calendar_event` | `gworkspace_manage_event` (`action: create`) | Zeitzone über `timezone`; Termine mit Uhrzeit gehen ohne Patch |
| `gcal_edit_calendar_event` | `gworkspace_manage_event` (`action: update`) | |
| `gcal_delete_calendar_event` | `gworkspace_manage_event` (`action: delete`) | |
| — | `gworkspace_query_freebusy`, `…_create_calendar`, `…_manage_out_of_office`, `…_manage_focus_time` | neu |

## Wo alte Tool-Namen stehen

**Repo (eingecheckt):** nur `ansible/group_vars/all.yml`, Liste `contract_tools_google_legacy`. Gilt nur
bei eingeschaltetem Legacy-Typ, bleibt bis zum Aufräumen.

**Repo, aber gitignored** (`ansible/group_vars/openclaw.yml`, nicht in diesem Zweig):
Cron-Job `main: Daily Standup` nennt `gmail_search_emails`. Der Job ist auf dem VPS deaktiviert.
Beim Umschalten im Prompt `gworkspace_search_gmail_messages` eintragen.

**VPS** (lesend geprüft am 2026-11-05):

| Fundstelle | Alte Namen | Handlungsbedarf |
|------------|------------|-----------------|
| Cron `Gmail Morning Check` (aktiv, nur auf dem VPS angelegt) | `gmail_search_emails`, `gmail_read_email`, `gmail_modify_email` | vor dem Umschalten anpassen |
| `~/.openclaw/workspace/memory/dreaming/light/2026-09-16.md` | `gmail_search_emails` | keiner, historische Notiz |
| `~/.openclaw/workspace/memory/.dreams/session-corpus/2026-09-15.txt` | `gmail_search_emails` | keiner, historisch |
| `~/.openclaw/workspace/gmail_check.js` | nur `gmail_list_failed`, kein Tool-Name | prüfen, ob das Skript Ausgaben alter Tools parst |
| `~/.openclaw/workspace-attestations` | — | keiner |

Cron `KI-Wochenrückblick` und alle übrigen Jobs nennen keine Google-Tool-Namen. Sie sprechen nur von
„Gmail“, das findet der Agent auch mit den neuen Namen.

## Ablauf

Ansible läuft nicht auf dem Windows-Rechner. `scripts/provision-docker.sh` startet `provision.sh` in
einem Linux-Container (Git Bash, Docker Desktop, fragt die Pulumi-Passphrase ab). Jede Phase braucht
Maiks OK.

**Probelauf 2026-11-06** (`--tags plugins --check --diff -e openclaw_workspace_backup_optouts=main`):
Verbindung, Pulumi-Secrets und Inventory funktionieren. Die Google-Schalter erzeugen genau die heutigen
Server, die alten npm-Pakete melden „ok“. Der Lauf zeigt aber Abweichungen, die nichts mit Google zu
tun haben und ein echter `--tags plugins`-Lauf mit ausrollen würde:

- `~/.profile` bekäme eine `export PATH="$HOME/.npm-global/bin:$PATH"`-Zeile.
- `@modelcontextprotocol/server-github` und `@openai/codex` würden global per npm installiert.
- Claude Code CLI, `~/.openclaw/claude-code-plugins`, `~/.git-credentials` (Plugin-PAT) und
  12 Claude-Code-Plugins aus `claude_code_plugins` in `openclaw.yml` würden eingerichtet.

Im Check-Modus bricht der Lauf an „Verify plugins directory exists“ ab (das Verzeichnis wird nur
simuliert); der Google-Teil der Rolle wurde deshalb nicht erreicht. Die Prüfung
„Assert all agents have GitHub workspace backup configured“ schlägt fehl, weil der Workspace-Sync von
Hand eingerichtet ist und `workspaceRepoUrl` nicht in Pulumi steht; dauerhaft gehört `main` dann in
`openclaw_workspace_backup_optouts` oder die URL in Pulumi.

Vor Phase 1 entscheiden: Abweichungen mitnehmen oder `claude_code_plugins` vorher ausklammern.

Die Plugins-Rolle ist nicht für den Check-Modus gebaut (über 20 Lese-Tasks liefern dort kein Ergebnis).
Den neuen Teil habe ich deshalb getrennt geprüft, **in einem lokalen `ubuntu:24.04`-Container**
(Python 3.12.3 wie auf dem VPS), mit denselben Befehlen, Argumenten und Env-Variablen wie die Rolle:

- uv 0.13.5: Prüfsumme stimmt; `uv tool install --exclude-newer … workspace-mcp==2.3.0` läuft durch,
  `uv tool list` meldet `workspace-mcp v2.3.0` (passt zur Versionsprüfung im Task).
- stdio-Handshake und `tools/list`: 29 Tools, exakt die Liste `google_workspace_mcp.tools`.
- `start_google_auth` (`service_name: gmail`) liefert einen Google-Anmeldelink mit Redirect
  `http://localhost:8000/oauth2callback`; der Callback lauscht nur auf `127.0.0.1:8000` und `[::1]:8000`.

Nicht geprüft bleibt das Zusammenspiel mit dem openclaw-mcp-adapter auf dem VPS. Das zeigt erst Phase 1.

### Phase 0: Vorbereitung

1. Sicherungen prüfen: `~/backups/npm-global-pre-sec-20261104-1358.tgz` (npm-global) liegt vor.
   Zusätzlich `cp ~/.openclaw/openclaw.json ~/backups/openclaw.json.pre-gworkspace`.
2. Google Cloud Console, OAuth-Client von `gmail_client_id`: Typ prüfen.
   - „Desktop“: Loopback-Redirects sind automatisch erlaubt, nichts zu tun.
   - „Web“: Redirect-URI `http://localhost:8000/oauth2callback` eintragen.
3. Gmail-, Drive- und Calendar-API sind im Projekt bereits aktiv (die alten Server nutzen sie).
4. Port 8000 auf dem VPS ist frei (am 2026-11-05 geprüft).

### Phase 1: Parallelbetrieb

In `group_vars/openclaw.yml`:

```yaml
google_mcp_legacy_enabled: true
google_workspace_mcp_enabled: true
google_workspace_user_email: "<Maiks Gmail-Adresse>"
```

Dann `./scripts/provision.sh --tags plugins`. Danach sieht der Agent alte und neue Tools.

Prüfen:

```bash
ssh ubuntu@openclaw-vps '/home/ubuntu/.local/bin/uv --version; /home/ubuntu/.local/bin/uv tool list'
ssh ubuntu@openclaw-vps 'XDG_RUNTIME_DIR=/run/user/1000 systemctl --user is-active openclaw-gateway'
ssh ubuntu@openclaw-vps "jq -r '.plugins.entries[\"openclaw-mcp-adapter\"].config.servers[].name' ~/.openclaw/openclaw.json"
ssh ubuntu@openclaw-vps 'XDG_RUNTIME_DIR=/run/user/1000 journalctl --user -u openclaw-gateway --since "5 min ago" | grep -i gworkspace'
```

### Phase 2: OAuth-Anmeldung (macht Maik selbst)

workspace-mcp braucht eine eigene Zustimmung mit den neuen Scopes. Der vorhandene Refresh-Token bleibt
unangetastet, `gcontacts` und die alten Server laufen damit weiter.

1. Tunnel öffnen und offen lassen:

   ```bash
   ssh -N -L 8000:127.0.0.1:8000 ubuntu@openclaw-vps
   ```

2. Im Chat mit dem Agenten: „Rufe `gworkspace_start_google_auth` mit `service_name: gmail` auf und gib
   mir den Link.“ Der Link enthält nur Client-ID und einen Zustandswert, keinen Token.
3. Link im eigenen Browser öffnen, mit dem richtigen Google-Konto anmelden, Scopes prüfen und zustimmen.
   Google leitet auf `http://localhost:8000/oauth2callback` um; der Tunnel bringt das zum VPS.
4. Ergebnis prüfen, ohne Inhalte auszugeben:

   ```bash
   ssh ubuntu@openclaw-vps 'ls -l ~/.google_workspace_mcp/credentials/'
   ```

   Erwartet: eine Datei `<adresse>.json` mit Rechten `-rw-------`.

5. Tunnel schließen.

### Phase 3: Test mit dem Agenten `main`

Es gibt nur den Agenten `main`. Getestet wird im Parallelbetrieb, mit ausdrücklich genannten neuen
Tool-Namen. Erst lesend, dann schreibend nur an Testobjekten.

| Bereich | Test | Erwartung |
|---------|------|-----------|
| Gmail lesen | `search_gmail_messages` mit `newer_than:1d in:inbox`, dann `get_gmail_message_content` | gleiche Mails wie mit `gmail_search_emails` |
| Labels | `list_gmail_labels`; `manage_gmail_label` create/delete „zz-test“ | Label erscheint und verschwindet |
| Ändern | `modify_gmail_message_labels` UNREAD an einer Testmail entfernen und wieder setzen | Status wechselt |
| Papierkorb | Testmail an sich selbst, dann `modify_gmail_message_labels` mit `TRASH` | Mail im Papierkorb |
| Senden | `draft_gmail_message`, dann `send_gmail_message` an Maiks eigene Adresse | Mail kommt an |
| Filter | `list_gmail_filters`; `manage_gmail_filter` create mit `dry_run`, dann delete eines Testfilters | |
| Anhang | `get_gmail_attachment_content` mit `return_base64: true` an einer Testmail | Inhalt kommt an |
| Drive | `search_drive_files`, `get_drive_file_content` | wie `gdrive_search` |
| Kalender | `list_calendars`, `get_events`; `manage_event` create/update/delete eines Testtermins mit Uhrzeit und `timezone: Europe/Berlin` | Termin mit Uhrzeit, kein Ganztag |

Danach den Cron `Gmail Morning Check` einmal mit neuen Namen von Hand laufen lassen und das Ergebnis
mit dem letzten regulären Lauf vergleichen.

### Phase 4: Prompts anpassen

Mit Maiks OK, vor dem Abschalten der alten Server:

- Cron `Gmail Morning Check` auf dem VPS: Tool-Namen nach der Tabelle oben ersetzen
  (`openclaw cron edit` bzw. über die Cron-Rolle, falls der Job dorthin übernommen wird).
- `group_vars/openclaw.yml`, Job `main: Daily Standup`: `gmail_search_emails` →
  `gworkspace_search_gmail_messages`.
- `~/.openclaw/workspace/gmail_check.js` prüfen.

### Phase 5: Umschalten

```yaml
google_mcp_legacy_enabled: false
google_workspace_mcp_enabled: true
```

`./scripts/provision.sh --tags plugins`, dann wie in Phase 1 prüfen: Serverliste ohne `gmail`,
`gdrive`, `gcal`, Gateway aktiv, keine MCP-Fehler im Journal. Ein Tag Beobachtung mit den echten
Cron-Läufen.

Hinweis: Ein Lauf nur mit `--tags plugins` ergänzt die Sandbox-Allowlist, entfernt aber keine alten
Namen daraus. Sie bleiben wirkungslos stehen, bis die `config`-Rolle die Liste neu setzt.

### Phase 6: Aufräumen (frühestens eine Woche nach Phase 5)

1. `google_mcp_legacy_uninstall: true`, `./scripts/provision.sh --tags plugins`. Danach
   `npm ls -g --depth=0` auf dem VPS: die drei Pakete sind weg.
2. Alte Credential-Dateien löschen: `~/.gmail-mcp/`, `~/.gdrive-mcp/`, `~/.gcal-mcp/`.
   `~/.gcontacts-mcp/` bleibt.
3. Den alten Refresh-Token **nicht** widerrufen: `gcontacts` nutzt ihn weiter. Aus demselben Grund
   in myaccount.google.com/permissions nicht den Zugriff der App entziehen; das trifft alle Tokens
   dieses OAuth-Clients, auch den neuen.
4. Im Repo: `contract_tools_google_legacy`, die Legacy-Tasks und Abschnitt 2 und 4 der Known Technical
   Debt in `CLAUDE.md` entfernen.

## Rückweg

| Ab | Schritte |
|----|----------|
| Phase 1–4 | `google_workspace_mcp_enabled: false`, `--tags plugins`. Alte Server liefen die ganze Zeit. |
| Phase 5 | `google_mcp_legacy_enabled: true`, `--tags plugins`. Pakete und Credentials sind noch da. Prompts aus Phase 4 zurückdrehen. |
| Phase 6 | Erst npm-global wiederherstellen, **nicht** neu aus der Registry installieren (neues zod, OpenClaw findet die Kalender-Tools nicht mehr): `tar tzf ~/backups/npm-global-pre-sec-20261104-1358.tgz \| head` prüfen, dann passend entpacken. Credential-Dateien neu schreiben lassen (`--tags plugins` mit Legacy an). |

Notfall ohne Ansible: `~/backups/openclaw.json.pre-gworkspace` zurückkopieren,
`systemctl --user restart openclaw-gateway`, `systemctl --user is-active openclaw-gateway`.

## Offene Punkte

- Keine Entsprechung für `gmail_get_or_create_label`, `gmail_get_filter`,
  `gmail_create_filter_from_template`; endgültiges Löschen (`gmail_delete_email`,
  `gmail_batch_delete_emails`) gibt es nicht mehr, nur Papierkorb. Ob `TRASH` über
  `modify_gmail_message_labels` wirkt, zeigt der Test in Phase 3.
- Anhänge landen auf dem Host, die Sandbox sieht sie nicht. `google_workspace_mcp.attachment_dir` auf
  einen Workspace-Pfad zu setzen würde helfen, schickt Anhänge aber durch Workspace-Git-Sync und
  Obsidian Sync.
- Mehrere Agenten: Jeder bekäme einen eigenen gworkspace-Prozess mit demselben Google-Konto. Ports
  weichen auf 8001–8004 aus, der OAuth-Redirect funktioniert aber nur auf 8000. Heute gibt es nur `main`.
- `contracts.tools` enthält wie bisher nur die Namen des Agenten `main`.
- workspace-mcp 2.3.0 ist vom 2026-10-30. Patch-Releases beobachten und die Version bewusst anheben
  (`version`, `exclude_newer`, Tool-Liste aus `core/tool_tiers.yaml` gegenprüfen).
- Ob der Adapter die Env des Kindprozesses ersetzt oder ergänzt, ist nicht geprüft; `HOME` und `PATH`
  sind deshalb gesetzt.
- Geprüft (Linux-Container, wie CI): Playbook-Syntax, `check-task-syntax.sh`,
  `check-openclaw-contract.sh`, `test-scheduled-automation.sh`, shellcheck, Unittests, dazu ein
  Render-Test aller vier Schalterkombinationen. Zwei Fehler in `test_session_cleanup` treten auch auf
  dem Stand vor dieser Änderung auf. Ein echter Lauf gegen einen Host fehlt.
