#!/bin/sh
set -eu

# Keep the service's identity separate from interactive coding sessions.
if [ "${USER_UID:-1000}" = 1001 ] || [ "${USER_GID:-1000}" = 1001 ]; then
  echo 'Gitea USER_UID/USER_GID must not use the agent UID/GID 1001.' >&2
  exit 1
fi

mkdir -p /home/agent /workspace
chown agent:agent /home/agent /workspace
chmod 700 /home/agent
chmod 750 /workspace
exec /usr/bin/entrypoint "$@"
