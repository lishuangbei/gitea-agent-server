#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/lib/common.sh"
require_docker
resolve_state_dir
prepare_compose_env
if [ "$#" -eq 0 ]; then set -- ps; fi
compose "$@"
