#!/usr/bin/env bash
# Configure a running Docker client without an interactive container session.
set -euo pipefail
set +x
umask 077
source "$(dirname -- "${BASH_SOURCE[0]}")/lib/common.sh"

if [ "${1:-}" = --help ] || [ "${1:-}" = -h ]; then
  printf '%s\n' \
    'Usage: ./connect-client.sh CLIENT_CONTAINER /absolute/project/path OWNER/REPO' \
    'Configure persistent, unattended SSH access to an existing writable Gitea repository.' \
    'Optional: CLIENT_USER=agent (or UID:GID); defaults to the container configured user.' \
    'Requires Python 3 on the Docker host and Git/OpenSSH in the client container.' \
    'The project Git directory must already be in a writable Docker volume or bind mount.'
  exit 0
fi
[ "$#" -eq 3 ] || die 'Use ./connect-client.sh CLIENT_CONTAINER /absolute/project/path OWNER/REPO'
command -v python3 >/dev/null || die 'Python 3 is required on the Docker host for this helper.'
require_docker
resolve_state_dir

gitea_user=${GITEA_USER:-gitadmin}
gitea_password=${GITEA_PASSWORD:-}
if [ -z "$gitea_password" ] && [ "$gitea_user" = gitadmin ] && [ -s "$STATE_DIR/admin-password.txt" ]; then
  IFS= read -r gitea_password < "$STATE_DIR/admin-password.txt"
fi
[ -n "$gitea_password" ] || die 'No Gitea admin credential available. Use the original deployment state or securely provide GITEA_PASSWORD.'

# Authentication is for provisioning only. Never pass it to the client container.
printf '%s\0' "$gitea_user" "$gitea_password" |
  python3 "$GITEA_REPO_DIR/lib/connect-client.py" "$@"
