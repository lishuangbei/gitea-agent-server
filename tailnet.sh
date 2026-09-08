#!/usr/bin/env bash
# Runs all Tailscale commands inside git-server, never on the Docker host.
set -euo pipefail
set +x
umask 077
source "$(dirname -- "${BASH_SOURCE[0]}")/lib/common.sh"

action=${1:-login}
case "$action" in
  -h|--help)
    printf '%s\n' \
      'Usage: ./tailnet.sh login [--auth-key]' \
      '       ./tailnet.sh serve' \
      '       ./tailnet.sh status' \
      'login: authorize the container, enable private HTTPS, update Gitea links.' \
      '--auth-key: read an existing Tailscale auth key without echoing it.' \
      'Optional initial node name: GITEA_TS_HOSTNAME=gitea ./tailnet.sh login'
    exit 0 ;;
  login|serve|status) ;;
  *) die 'Use login, serve, status, or --help.' ;;
esac
if [ "$#" -gt 1 ]; then
  [ "$action" = login ] && [ "$#" -eq 2 ] && [ "$2" = --auth-key ] || die 'Unexpected argument.'
fi
require_docker
resolve_state_dir
docker exec --user 0:0 "$SERVER" test -S /run/tailscale/tailscaled.sock || \
  die 'Tailscale is not ready in git-server; update and run ./setup-gitea.sh first.'

ts() {
  docker exec --user 0:0 "$SERVER" \
    tailscale --socket=/run/tailscale/tailscaled.sock "$@"
}
if [ "$action" = status ]; then
  ts status
  ts serve status
  exit 0
fi

backend=$(docker exec --user 0:0 "$SERVER" sh -ec \
  'tailscale --socket=/run/tailscale/tailscaled.sock status --json | jq -er .BackendState')
if [ "$backend" != Running ]; then
  [ "$action" = login ] || die 'The container is not connected; run ./tailnet.sh login.'
  node_name=${GITEA_TS_HOSTNAME:-gitea}
  [[ "$node_name" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$ ]] || die 'Invalid GITEA_TS_HOSTNAME.'
  if [ "${2:-}" = --auth-key ]; then
    read -r -s -p 'Tailscale auth key (hidden): ' auth_key
    printf '\n'
    [ -n "$auth_key" ] || die 'An auth key is required.'
    # file:/dev/stdin avoids putting the key in argv, Docker env, or a file.
    printf '%s\n' "$auth_key" | docker exec -i --user 0:0 "$SERVER" \
      tailscale --socket=/run/tailscale/tailscaled.sock up \
        --auth-key=file:/dev/stdin --accept-dns=false --hostname="$node_name" --timeout=5m
    unset auth_key
  else
    printf 'Authorize the container using the link below; the host needs no Tailscale.\n'
    ts up --accept-dns=false --hostname="$node_name" --timeout=5m
  fi
fi

# Preserve any manually configured service at the default HTTPS root.
# Refuse an existing public Funnel configuration instead of silently reusing it.
docker exec --user 0:0 "$SERVER" sh -ec '
  tailscale --socket=/run/tailscale/tailscaled.sock serve status --json | jq -e '\''
    ([.AllowFunnel // {} | .[] | select(. == true)] | length == 0) and
    ([.Web // {} | to_entries[] | select(.key | endswith(":443")) |
      .value.Handlers["/"] | select(. != null and . != {"Proxy":"http://127.0.0.1:3000"})] | length == 0)
  '\'' >/dev/null
' || die 'Existing Serve/Funnel settings differ; inspect ./tailnet.sh status before changing them.'

ts serve --bg --https=443 http://127.0.0.1:3000
node_dns=$(docker exec --user 0:0 "$SERVER" sh -ec \
  'tailscale --socket=/run/tailscale/tailscaled.sock status --json | jq -er .Self.DNSName')
public_url=$(normalize_public_url "https://${node_dns%.}/")
write_tailnet_config "$public_url"
prepare_compose_env
compose config --quiet
compose up -d --no-build --pull never git-server

for ((attempt=0; attempt<60; attempt++)); do
  if docker exec "$SERVER" wget -q -T 3 -O /dev/null http://127.0.0.1:3000/api/healthz 2>/dev/null && \
    docker exec --user 0:0 "$SERVER" sh -ec \
      'tailscale --socket=/run/tailscale/tailscaled.sock status --json | jq -e '\''.BackendState == "Running"'\'' >/dev/null' 2>/dev/null; then
    printf '\nContainer tailnet HTTPS: %s\n' "$public_url"
    printf '%s\n' \
      'Gitea and Tailscale are ready inside git-server; login state survives recreation.' \
      'Docker clients keep using http://git-server:3000.' \
      'Verify from another tailnet machine: curl -fsS '"${public_url}api/healthz" \
      'After setup-gitea.sh, Git HTTP access needs no client credentials; web/API login remains enabled.'
    exit 0
  fi
  sleep 2
done
die 'Configuration saved, but readiness timed out. Inspect ./manage.sh logs and ./tailnet.sh status.'
