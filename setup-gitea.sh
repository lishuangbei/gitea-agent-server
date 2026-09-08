#!/usr/bin/env bash
# Deploy on the Docker host that runs the client containers.
# Usage: bash setup-gitea.sh [existing-client-container ...]
# Optional: GITEA_DIR=/path/to/state bash setup-gitea.sh worker-1 worker-2
# Optional manual URL override: GITEA_PUBLIC_URL=https://gitea.tailnet-name.ts.net/
# Tailscale runs inside this container. After setup: ./tailnet.sh login
# References:
# https://docs.gitea.com/installation/install-with-docker/
# https://docs.gitea.com/administration/command-line/
set -euo pipefail
set +x
umask 077
source "$(dirname -- "${BASH_SOURCE[0]}")/lib/common.sh"
require_docker
resolve_state_dir
ADMIN_USER=gitadmin
public_url=${GITEA_PUBLIC_URL:-}
if [ -n "$public_url" ]; then
  public_url=$(normalize_public_url "$public_url")
fi
mkdir -p "$STATE_DIR"
cd "$STATE_DIR"
STATE_DIR=$PWD

# Check client names before making changes.
for client in "$@"; do
  docker container inspect "$client" >/dev/null 2>&1 || die "Client container does not exist: $client"
done

if ! docker network inspect "$NETWORK" >/dev/null 2>&1; then
  docker network create --driver bridge "$NETWORK" >/dev/null
fi

# Keep existing configuration on repeat runs.
if [ ! -e compose.yaml ]; then
  cat > compose.yaml <<'YAML'
# Created by setup-gitea.sh. Edit the pinned image tag to upgrade explicitly.
services:
  git-server:
    image: docker.gitea.com/gitea:1.27.3
    container_name: git-server
    restart: unless-stopped
    environment:
      GITEA__database__DB_TYPE: sqlite3
      GITEA__database__PATH: /data/gitea/gitea.db
      GITEA__security__INSTALL_LOCK: "true"
      GITEA__service__DISABLE_REGISTRATION: "true"
      GITEA__server__DOMAIN: git-server
      GITEA__server__ROOT_URL: http://git-server:3000/
      GITEA__server__SSH_DOMAIN: git-server
      GITEA__server__SSH_PORT: "22"
    volumes:
      - git-data:/data
    networks:
      - git-net
    ports:
      # Optional management access on the Docker host only.
      - "127.0.0.1:3000:3000"
volumes:
  git-data:
networks:
  git-net:
    external: true
    name: git-net
YAML
fi

# Persist the canonical web URL without replacing an existing base Compose file.
# Both network paths still reach the same server and data volume. Backend HTTP
# stays enabled for Docker clients and the in-container Tailscale reverse proxy.
if [ -n "$public_url" ]; then
  write_tailnet_config "$public_url"
fi

cat > compose.harness.yaml <<'YAML'
services:
  git-server:
    image: gitea-agent-server:local
    environment:
      TRUSTED_GIT_ENABLED: "1"
      GITEA__server__PROTOCOL: "http"
      GITEA__server__HTTP_ADDR: "127.0.0.1"
      GITEA__server__HTTP_PORT: "3001"
      GITEA__server__LOCAL_ROOT_URL: "http://127.0.0.1:3001/"
      GITEA__server__PUBLIC_URL_DETECTION: "auto"
      GITEA__server__LFS_START_SERVER: "true"
      GITEA__repository__ENABLE_PUSH_CREATE_USER: "true"
      GITEA__repository__DEFAULT_PUSH_CREATE_PRIVATE: "true"
    build:
      context: "${GITEA_BUILD_CONTEXT:?Run setup-gitea.sh or manage.sh}"
      args:
        GITEA_BASE_IMAGE: "${GITEA_BASE_IMAGE:?Run setup-gitea.sh or manage.sh}"
    volumes:
      - agent-home:/home/agent
      - agent-workspace:/workspace
      - tailscale-state:/var/lib/tailscale
volumes:
  agent-home:
  agent-workspace:
  tailscale-state:
YAML
prepare_compose_env
gitea_cli() {
  compose exec -T --user git git-server \
    gitea --config /data/gitea/conf/app.ini "$@"
}

compose config --quiet
# Finish building before replacing a running server; preserve its data volumes.
compose build git-server
compose up -d --no-build --pull never

printf 'Waiting for Gitea to initialize...\n'
ready=false
for ((attempt=0; attempt<90; attempt++)); do
  if compose exec -T git-server \
    wget -q -T 3 -O /dev/null http://127.0.0.1:3000/api/healthz 2>/dev/null; then
    ready=true
    break
  fi
  sleep 2
done
if [ "$ready" != true ]; then
  compose logs --tail=50 git-server >&2
  die 'Gitea did not become ready. Data has been preserved.'
fi

# Never reset an existing account's password.
users=$(gitea_cli admin user list)
if ! printf '%s\n' "$users" | awk -v user="$ADMIN_USER" '$2 == user { found=1 } END { exit !found }'; then
  if [ ! -s admin-password.txt ]; then
    # Prefix guarantees upper/lowercase, a digit and punctuation.
    password="Gt9!$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')"
    printf '%s\n' "$password" > admin-password.txt
    unset password
  fi
  chmod 600 admin-password.txt
  # Pass the password through stdin rather than host command-line arguments.
  compose exec -T --user git git-server sh -c '
    IFS= read -r password
    exec gitea --config /data/gitea/conf/app.ini admin user create \
      --username gitadmin --email gitadmin@example.invalid \
      --password "$password" --admin --must-change-password=false
  ' < admin-password.txt
fi

# Local administration can create a dedicated server token without knowing the
# user's password. Reuse a valid saved authorization on subsequent runs.
compose exec -T --user 0:0 git-server \
  python3 /usr/local/libexec/configure-git-gateway.py --auto "$ADMIN_USER"
compose exec -T --user 0:0 git-server nginx -t
compose exec -T --user 0:0 git-server nginx -s reload
compose exec -T --user 0:0 git-server \
  python3 /usr/local/libexec/configure-git-gateway.py --wait-ready "$ADMIN_USER"

# Connect existing containers without disconnecting their current networks.
for client in "$@"; do
  attached=$(docker inspect --format '{{with index .NetworkSettings.Networks "git-net"}}yes{{end}}' "$client")
  if [ "$attached" != yes ]; then
    docker network connect "$NETWORK" "$client"
  fi
done

printf '%s\n' "$STATE_DIR" > "$GITEA_REPO_DIR/.gitea-state-dir"
printf '\nGitea is ready.\n'
printf 'State directory: %s\n' "$STATE_DIR"
printf 'Administrator: %s\n' "$ADMIN_USER"
if [ -s admin-password.txt ]; then
  printf 'Initial password file: %s/admin-password.txt (not printed)\n' "$STATE_DIR"
else
  printf 'Web administrator already exists; its password was left unchanged.\n'
fi
printf '%s\n' \
  'Internal HTTP: http://git-server:3000' \
  'Internal SSH: git-server:22' \
  'Host-local management: http://127.0.0.1:3000' \
  'Tailscale runs inside git-server; the Docker host needs no Tailscale.'
if [ -f compose.tailnet.yaml ]; then
  printf '%s\n' \
    'Canonical web URL: see ROOT_URL in compose.tailnet.yaml.' \
    'Configure or refresh the container tailnet endpoint: ./tailnet.sh login' \
    'Use manage.sh for Compose commands so all configuration files are included.'
else
  printf '%s\n' \
    'Canonical web links use git-server; resolve that name on the host if using the UI.' \
    'Log in and configure the container tailnet endpoint: ./tailnet.sh login'
fi
printf '%s\n' \
  'Claude Code and DeepSeek Harness: run ./agent-shell.sh from the cloned repository.' \
  'Agent home and workspace persist in separate Docker volumes.' \
  'Trusted Git HTTP access is ready: clients need no password, token, or SSH key.' \
  'Push to http://git-server:3000/gitadmin/REPO.git to create a private repository.' \
  'Anyone who can reach this Git endpoint can read/write repositories accessible to gitadmin.' \
  'Web management and API access still require Gitea login.' \
  'Optional GitHub pull mirrors remain read-only; see mirror-github.sh.' \
  'For recreated clients, also declare external network git-net in their Compose files.' \
  'Repository data is stored in Docker volume local-git-server_git-data.' \
  'Do not delete that volume or run docker compose down -v if you need the data.'
