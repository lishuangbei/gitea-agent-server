#!/usr/bin/env bash
# Run on the Docker host, from the cloned deployment repository.
set -euo pipefail
set +x
umask 077
source "$(dirname -- "${BASH_SOURCE[0]}")/lib/common.sh"

if [ "${1:-}" = --help ] || [ "${1:-}" = -h ]; then
  printf '%s\n' \
    'Usage: ./mirror-github.sh [https://github.com/OWNER/REPO.git] [MIRROR_NAME]' \
    '       ./mirror-github.sh --sync GITEA_OWNER/MIRROR_NAME' \
    'Creates a private, read-only pull mirror. Credentials are never printed.'
  exit 0
fi
require_docker
resolve_state_dir

mode=create
github_url=${1:-}
mirror_name=${2:-}
github_token=
if [ "$github_url" = --sync ]; then
  mode=sync
  [ -n "$mirror_name" ] || die 'Supply GITEA_OWNER/MIRROR_NAME after --sync.'
else
  if [ -z "$github_url" ]; then
    read -r -p 'GitHub repository URL: ' github_url
  fi
  # Restrict the destination of the supplied GitHub credential.
  [[ "$github_url" =~ ^https://github[.]com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?$ ]] || \
    die 'Use https://github.com/OWNER/REPO.git without credentials or query parameters.'
  if [ -z "$mirror_name" ]; then
    mirror_name=${github_url%/}
    mirror_name=${mirror_name##*/}
    mirror_name=${mirror_name%.git}
  fi
  [[ "$mirror_name" =~ ^[A-Za-z0-9_.-]+$ ]] || die 'Invalid mirror name.'
  if [ -n "${GH_TOKEN:-}" ]; then
    github_token=$GH_TOKEN
  elif [ -n "${GITHUB_TOKEN:-}" ]; then
    github_token=$GITHUB_TOKEN
  elif command -v gh >/dev/null 2>&1; then
    github_token=$(gh auth token --hostname github.com 2>/dev/null) || github_token=
    if [ -n "$github_token" ]; then
      printf 'Using the Docker host GitHub CLI credential; Gitea will retain it for scheduled sync.\n'
    fi
  fi
  if [ -z "$github_token" ]; then
    read -r -s -p 'GitHub token (hidden; Enter for a public source): ' github_token
    printf '\n'
  fi
fi

gitea_user=${GITEA_USER:-gitadmin}
gitea_password=${GITEA_PASSWORD:-}
if [ -z "$gitea_password" ] && [ "$gitea_user" = gitadmin ] && [ -s "$STATE_DIR/admin-password.txt" ]; then
  IFS= read -r gitea_password < "$STATE_DIR/admin-password.txt"
fi
if [ -z "$gitea_password" ]; then
  read -r -s -p "Gitea password for $gitea_user (hidden): " gitea_password
  printf '\n'
fi

run_request() {
  # No secrets in Docker arguments, environment, files, or shell history.
  printf '%s\0' "$mode" "$github_url" "$mirror_name" "$github_token" "$gitea_user" "$gitea_password" |
    docker exec -i --user 1001:1001 "$SERVER" \
      python3 -c "$(cat "$GITEA_REPO_DIR/lib/mirror-github.py")"
}
if run_request; then
  exit 0
else
  status=$?
  if [ "$status" -ne 3 ]; then exit "$status"; fi
fi
# A password changed after initial deployment should not require editing files.
read -r -s -p "Current Gitea password for $gitea_user (hidden): " gitea_password
printf '\n'
run_request
