#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/lib/common.sh"
command -v docker >/dev/null || die 'Docker is required.'
docker container inspect "$SERVER" >/dev/null 2>&1 || die 'Run setup-gitea.sh first.'
flags=(-i)
if [ -t 0 ] && [ -t 1 ]; then flags+=(-t); fi
if [ "$#" -eq 0 ]; then set -- bash; fi
exec docker exec "${flags[@]}" --user 1001:1001 \
  --env HOME=/home/agent --env USER=agent --env LOGNAME=agent \
  --workdir /workspace "$SERVER" "$@"
