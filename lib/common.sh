#!/usr/bin/env bash
# Shared by setup-gitea.sh, manage.sh, and agent-shell.sh.
GITEA_REPO_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
PROJECT=local-git-server
NETWORK=git-net
SERVER=git-server

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

require_docker() {
  command -v docker >/dev/null || die 'Docker is required.'
  docker info >/dev/null 2>&1 || die 'Cannot connect to Docker.'
  docker compose version >/dev/null 2>&1 || die 'Docker Compose v2 is required.'
}

resolve_state_dir() {
  local owner configs existing_dir requested remembered
  STATE_DIR=${GITEA_DIR:-"$GITEA_REPO_DIR/gitea-internal"}
  if [ -z "${GITEA_DIR:-}" ] && [ -f "$GITEA_REPO_DIR/.gitea-state-dir" ]; then
    IFS= read -r remembered < "$GITEA_REPO_DIR/.gitea-state-dir" || die 'Cannot read saved deployment path.'
    [ -n "$remembered" ] || die 'The saved deployment path is empty.'
    STATE_DIR=$remembered
  fi
  if docker container inspect "$SERVER" >/dev/null 2>&1; then
    owner=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$SERVER")
    [ "$owner" = "$PROJECT" ] || die 'git-server already belongs to another deployment.'
    configs=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}' "$SERVER")
    existing_dir=$(dirname -- "${configs%%,*}")
    [ -f "$existing_dir/compose.yaml" ] || \
      die "Existing deployment configuration is unavailable: $existing_dir/compose.yaml. Run on the original Docker host."
    existing_dir=$(cd -- "$existing_dir" && pwd)
    if [ -n "${GITEA_DIR:-}" ]; then
      requested=$(cd -- "$GITEA_DIR" 2>/dev/null && pwd) || die 'GITEA_DIR does not exist.'
      [ "$requested" = "$existing_dir" ] || die "Use the existing state directory: $existing_dir"
    fi
    STATE_DIR=$existing_dir
  fi
}

compose_base() {
  local files=(-f "$STATE_DIR/compose.yaml")
  if [ -f "$STATE_DIR/compose.tailnet.yaml" ]; then
    files+=(-f "$STATE_DIR/compose.tailnet.yaml")
  fi
  docker compose -p "$PROJECT" "${files[@]}" "$@"
}

normalize_public_url() {
  local url="${1%/}/"
  [[ "$url" =~ ^https://[A-Za-z0-9][A-Za-z0-9.-]*[.]ts[.]net/$ ]] || \
    die 'GITEA_PUBLIC_URL must be an HTTPS *.ts.net root URL.'
  printf '%s\n' "$url"
}

write_tailnet_config() {
  local url host
  url=$(normalize_public_url "$1")
  host=${url#https://}
  host=${host%/}
  cat > "$STATE_DIR/compose.tailnet.yaml" <<YAML
services:
  git-server:
    environment:
      GITEA__server__ROOT_URL: "$url"
      GITEA__server__DOMAIN: "$host"
      GITEA__server__LOCAL_ROOT_URL: "http://localhost:3000/"
      GITEA__server__PUBLIC_URL_DETECTION: "auto"
      GITEA__server__PROTOCOL: "http"
YAML
}

prepare_compose_env() {
  [ -f "$STATE_DIR/compose.yaml" ] || die 'No deployment configuration; run setup-gitea.sh first.'
  # Read the original service image, excluding our generated build overlay.
  GITEA_BASE_IMAGE=$(compose_base config --images git-server)
  [[ "$GITEA_BASE_IMAGE" =~ ^[A-Za-z0-9][A-Za-z0-9._/:@-]+$ ]] || \
    die 'Could not resolve a single base image for the git-server service.'
  export GITEA_BASE_IMAGE
  export GITEA_BUILD_CONTEXT="$GITEA_REPO_DIR"
}

compose() {
  local files=(-f "$STATE_DIR/compose.yaml")
  if [ -f "$STATE_DIR/compose.tailnet.yaml" ]; then
    files+=(-f "$STATE_DIR/compose.tailnet.yaml")
  fi
  if [ -f "$STATE_DIR/compose.harness.yaml" ]; then
    files+=(-f "$STATE_DIR/compose.harness.yaml")
  fi
  docker compose -p "$PROJECT" "${files[@]}" "$@"
}
