#!/usr/bin/env bash
# Run scripts/provision.sh inside a Linux container — for control machines
# where Ansible cannot run natively (Windows: Ansible crashes in
# check_blocking_io with WinError 87).
#
# The container gets, read-only:
#   - the committed tree of HEAD (git archive, LF line endings)
#   - the gitignored deployment files from the main checkout
#     (ansible/group_vars/openclaw.yml, pulumi/Pulumi.<stack>.yaml)
#   - ~/.pulumi without plugins (local file backend), copied inside so the
#     container never writes to the host backend
#   - the SSH key and ~/.codex/auth.json
#   - a snapshot of `tailscale status --json` from the host; the container has
#     no Tailscale of its own, a shim replays the snapshot to provision.sh
#
# The Pulumi passphrase is read from PULUMI_CONFIG_PASSPHRASE or prompted
# (no echo) and passed to docker by name, so it never appears in argv.
#
# Usage (Git Bash on Windows, from anywhere in the repo or a worktree):
#   ./scripts/provision-docker.sh --tags plugins --check --diff
#   ./scripts/provision-docker.sh --tags plugins
#
# Uncommitted changes are NOT included — commit first.
set -euo pipefail

PULUMI_VERSION="3.219.0"
ANSIBLE_CORE_VERSION="2.20.10"
IMAGE="openclaw-provisioner:pulumi-${PULUMI_VERSION}-ansible-${ANSIBLE_CORE_VERSION}"
STACK="${PULUMI_STACK_NAME:-prod}"
SSH_KEY="${OPENCLAW_SSH_KEY:-$HOME/.ssh/id_ed25519}"

fail() { echo "ERROR: $*" >&2; exit 1; }
# Docker Desktop on Windows needs C:/... paths; elsewhere paths pass through.
hostpath() { if command -v cygpath >/dev/null; then cygpath -m "$1"; else printf '%s' "$1"; fi; }
for cmd in docker git jq tailscale; do
    command -v "$cmd" >/dev/null || fail "$cmd not found on PATH"
done
docker info >/dev/null 2>&1 || fail "Docker daemon not reachable (start Docker Desktop)"
[ -f "$SSH_KEY" ] || fail "SSH key $SSH_KEY not found (set OPENCLAW_SSH_KEY)"

REPO_DIR="$(git rev-parse --show-toplevel)"
# Gitignored deployment files live only in the main checkout, not in worktrees.
MAIN_DIR="$(cd "$(git rev-parse --git-common-dir)/.." && pwd)"
PRIVATE_VARS="$MAIN_DIR/ansible/group_vars/openclaw.yml"
PULUMI_STACK_FILE="$MAIN_DIR/pulumi/Pulumi.${STACK}.yaml"
[ -f "$PULUMI_STACK_FILE" ] || fail "$PULUMI_STACK_FILE not found"
[ -d "$HOME/.pulumi/stacks" ] || fail "No local Pulumi backend at ~/.pulumi"

if ! git -C "$REPO_DIR" diff --quiet HEAD -- ansible scripts; then
    echo "WARNING: uncommitted changes under ansible/ or scripts/ are not included." >&2
fi

if [ -z "${PULUMI_CONFIG_PASSPHRASE:-}" ]; then
    read -rsp "Pulumi passphrase for stack '$STACK': " PULUMI_CONFIG_PASSPHRASE
    echo
fi
[ -n "$PULUMI_CONFIG_PASSPHRASE" ] || fail "empty passphrase"
export PULUMI_CONFIG_PASSPHRASE

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
chmod 700 "$WORK"

git -C "$REPO_DIR" -c core.autocrlf=false archive -o "$WORK/repo.tar" HEAD
mkdir -p "$WORK/private"
[ -f "$PRIVATE_VARS" ] && cp "$PRIVATE_VARS" "$WORK/private/openclaw.yml"
cp "$PULUMI_STACK_FILE" "$WORK/private/Pulumi.${STACK}.yaml"
tailscale status --json > "$WORK/tailscale-status.json"

# Resolve MagicDNS names for the container (it cannot query 100.100.100.100).
ADD_HOSTS=()
while IFS=$'\t' read -r name ip; do
    [ -n "$name" ] && [ -n "$ip" ] && ADD_HOSTS+=(--add-host "$name:$ip")
done < <(jq -r '.Peer[] | select(.Online == true) | [(.DNSName | rtrimstr(".")), (.TailscaleIPs[0] // "")] | @tsv' \
    "$WORK/tailscale-status.json" | tr -d '\r')

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "=== Building $IMAGE ==="
    docker build -t "$IMAGE" - <<DOCKERFILE
FROM python:3.12-slim
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
      jq openssh-client git curl ca-certificates rsync && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "ansible-core==${ANSIBLE_CORE_VERSION}"
RUN curl -fsSL -o /tmp/pulumi.tgz \
      "https://get.pulumi.com/releases/sdk/pulumi-v${PULUMI_VERSION}-linux-x64.tar.gz" \
 && tar -xzf /tmp/pulumi.tgz -C /opt && rm /tmp/pulumi.tgz
ENV PATH="/opt/pulumi:\$PATH"
DOCKERFILE
fi

CODEX_MOUNT=()
[ -f "$HOME/.codex/auth.json" ] && CODEX_MOUNT=(-v "$(hostpath "$HOME/.codex/auth.json"):/mnt/codex-auth.json:ro")

INNER='
set -euo pipefail
mkdir -p /work && tar -xf /mnt/work/repo.tar -C /work
[ -f /mnt/work/private/openclaw.yml ] && cp /mnt/work/private/openclaw.yml /work/ansible/group_vars/openclaw.yml
cp /mnt/work/private/Pulumi.*.yaml /work/pulumi/
mkdir -p /root/.pulumi && rsync -a --exclude plugins /mnt/pulumi/ /root/.pulumi/
mkdir -p /root/.ssh && install -m 600 /mnt/ssh-key /root/.ssh/id_ed25519
if [ -f /mnt/codex-auth.json ]; then mkdir -p /root/.codex && install -m 600 /mnt/codex-auth.json /root/.codex/auth.json; fi
printf "#!/bin/sh\n[ \"\$1 \$2\" = \"status --json\" ] || { echo \"tailscale shim: only status --json\" >&2; exit 2; }\nexec cat /mnt/work/tailscale-status.json\n" > /usr/local/bin/tailscale
chmod 755 /usr/local/bin/tailscale
cd /work/pulumi
pulumi login --local >/dev/null
pulumi stack select "$STACK" --non-interactive >/dev/null
cd /work
exec ./scripts/provision.sh "$@"
'

echo "=== Running provision.sh in container (args: $*) ==="
MSYS_NO_PATHCONV=1 docker run --rm -i \
    -e PULUMI_CONFIG_PASSPHRASE -e STACK="$STACK" \
    -v "$(hostpath "$WORK"):/mnt/work:ro" \
    -v "$(hostpath "$HOME/.pulumi"):/mnt/pulumi:ro" \
    -v "$(hostpath "$SSH_KEY"):/mnt/ssh-key:ro" \
    "${CODEX_MOUNT[@]}" \
    "${ADD_HOSTS[@]}" \
    "$IMAGE" bash -c "$INNER" provision "$@"
