#!/usr/bin/env bash
# Test sandbox: a Home Assistant with Docker Apps inside Docker-in-Docker on a Docker host,
# isolated from the containers of that host.
#
#   scripts/sandbox.sh up      create (or start) the sandbox, onboard and set up Docker Apps
#   scripts/sandbox.sh deploy  copy the integration into the sandbox and restart Home Assistant
#   scripts/sandbox.sh token   print an access token of the test account
#   scripts/sandbox.sh tunnel  forward http://localhost:18123 to the sandbox (runs until stopped)
#   scripts/sandbox.sh down    remove the sandbox with all its containers, images and data
#
# Settings and the generated test account are kept in .sandbox.env (git ignored):
#   SANDBOX_SSH       ssh target of the Docker host, for example user@192.168.0.10 (required)
#   SANDBOX_SSH_KEY   ssh key (optional)
#   SANDBOX_DOCKER    docker command on the host (default: sudo -n docker)
#   SANDBOX_DIR       folder on the host (default: docker-apps-sandbox in the home folder)
#   SANDBOX_HA        Home Assistant image (default: ghcr.io/home-assistant/home-assistant:stable)
#   SANDBOX_STORE     app store repository added on setup (default: Big Bear CasaOS)
# cspell:words dind certdir urandom
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
ENV_FILE="$ROOT/.sandbox.env"
# shellcheck source=/dev/null
[[ -f $ENV_FILE ]] && . "$ENV_FILE"
: "${SANDBOX_SSH:?set SANDBOX_SSH in $ENV_FILE, for example SANDBOX_SSH=user@192.168.0.10}"
SANDBOX_DOCKER=${SANDBOX_DOCKER:-sudo -n docker}
SANDBOX_DIR=${SANDBOX_DIR:-docker-apps-sandbox}
SANDBOX_HA=${SANDBOX_HA:-ghcr.io/home-assistant/home-assistant:stable}
SANDBOX_STORE=${SANDBOX_STORE:-https://github.com/bigbeartechworld/big-bear-casaos}
SANDBOX_USER=${SANDBOX_USER:-sandbox}
DIND=docker:27.5.1-dind
PORT=18123
CLIENT=http://localhost:$PORT/

SSH=(ssh -o BatchMode=yes)
[[ -n ${SANDBOX_SSH_KEY:-} ]] && SSH+=(-i "$SANDBOX_SSH_KEY" -o IdentitiesOnly=yes)
SSH+=("$SANDBOX_SSH")

remote() { "${SSH[@]}" "D='$SANDBOX_DOCKER'; S=\$HOME/$SANDBOX_DIR; $1"; }

# api METHOD PATH BODY [TOKEN]: call the sandbox Home Assistant from the host
api() {
  local auth=""
  [[ -n ${4:-} ]] && auth="-H 'Authorization: Bearer $4'"
  local type=application/json
  [[ $3 == *=* && $3 != "{"* ]] && type=application/x-www-form-urlencoded
  printf %s "$3" | "${SSH[@]}" \
    "curl -sf -X $1 http://127.0.0.1:$PORT$2 -H 'Content-Type: $type' $auth --data-binary @-"
}

field() { grep -o "\"$1\":\"[^\"]*\"" | head -1 | cut -d'"' -f4; }

save_env() {
  mkdir -p "$(dirname "$ENV_FILE")"
  {
    echo "SANDBOX_SSH=$SANDBOX_SSH"
    [[ -n ${SANDBOX_SSH_KEY:-} ]] && echo "SANDBOX_SSH_KEY=$SANDBOX_SSH_KEY"
    echo "SANDBOX_URL=$CLIENT"
    echo "SANDBOX_USER=$SANDBOX_USER"
    echo "SANDBOX_PASSWORD=$SANDBOX_PASSWORD"
  } >"$ENV_FILE"
}

copy_integration() {
  tar -C "$ROOT/custom_components" --exclude=__pycache__ -cf - docker_apps |
    remote 'mkdir -p $S/workspace/ha-config/custom_components && tar -C $S/workspace/ha-config/custom_components -xf -'
}

wait_ready() {
  remote "for i in \$(seq 1 90); do curl -sf -o /dev/null http://127.0.0.1:$PORT/manifest.json && exit 0; sleep 2; done; exit 1" ||
    { echo "Home Assistant did not start" >&2; exit 1; }
}

token() {
  local flow code
  flow=$(api POST /auth/login_flow "{\"client_id\":\"$CLIENT\",\"handler\":[\"homeassistant\",null],\"redirect_uri\":\"$CLIENT\"}" | field flow_id)
  code=$(api POST "/auth/login_flow/$flow" "{\"client_id\":\"$CLIENT\",\"username\":\"$SANDBOX_USER\",\"password\":\"$SANDBOX_PASSWORD\"}" | field result)
  api POST /auth/token "grant_type=authorization_code&code=$code&client_id=$CLIENT" | field access_token
}

up() {
  copy_integration
  remote "mkdir -p \$S/workspace/apps/homeassistant \$S/workspace/data && cd \$S
cat > compose.yaml <<EOF
# Docker Apps sandbox: its own Docker engine, isolated from the host containers
name: docker-apps-sandbox
services:
  dind:
    image: $DIND
    container_name: docker-apps-sandbox
    privileged: true
    restart: unless-stopped
    environment:
      DOCKER_TLS_CERTDIR: ''
    # only on localhost: reach it through an SSH tunnel
    ports:
      - 127.0.0.1:$PORT:8123
      - 127.0.0.1:18080-18089:18080-18089
    volumes:
      - ./engine:/var/lib/docker
      - ./workspace:/workspace
EOF
cat > workspace/apps/homeassistant/compose.yaml <<EOF
services:
  homeassistant:
    container_name: homeassistant
    image: $SANDBOX_HA
    network_mode: host
    restart: unless-stopped
    volumes:
      - /workspace/ha-config:/config
      - /var/run/docker.sock:/var/run/docker.sock
      - /workspace/apps:/workspace/apps
      - /workspace/data:/workspace/data
EOF
\$D compose up -d --quiet-pull
for i in \$(seq 1 30); do \$D exec docker-apps-sandbox docker info >/dev/null 2>&1 && break; sleep 2; done
\$D exec docker-apps-sandbox docker compose -f /workspace/apps/homeassistant/compose.yaml up -d --quiet-pull"
  wait_ready

  if api GET /api/onboarding "" | grep -q '"step":"user","done":false'; then
    SANDBOX_PASSWORD=${SANDBOX_PASSWORD:-$(head -c 18 /dev/urandom | base64 | tr -d '/+=')}
    save_env
    local code access
    code=$(api POST /api/onboarding/users "{\"client_id\":\"$CLIENT\",\"name\":\"Sandbox\",\"username\":\"$SANDBOX_USER\",\"password\":\"$SANDBOX_PASSWORD\",\"language\":\"en\"}" | field auth_code)
    access=$(api POST /auth/token "grant_type=authorization_code&code=$code&client_id=$CLIENT" | field access_token)
    api POST /api/onboarding/core_config "{}" "$access" >/dev/null
    api POST /api/onboarding/analytics "{}" "$access" >/dev/null
    api POST /api/onboarding/integration "{\"client_id\":\"$CLIENT\",\"redirect_uri\":\"${CLIENT}?auth_callback=1\"}" "$access" >/dev/null
    echo "onboarded, test account in $ENV_FILE"
  fi

  local access flow entry
  access=$(token)
  if ! api GET /api/config/config_entries/entry "" "$access" | grep -q '"domain":"docker_apps"'; then
    flow=$(api POST /api/config/config_entries/flow '{"handler":"docker_apps"}' "$access" | field flow_id)
    api POST "/api/config/config_entries/flow/$flow" "{}" "$access" >/dev/null
    entry=$(api POST "/api/config/config_entries/flow/$flow" \
      '{"apps_root":"/workspace/apps","data_root":"/workspace/data","read_only":false}' "$access" | field entry_id)
    flow=$(api POST /api/config/config_entries/subentries/flow "{\"handler\":[\"$entry\",\"repository\"]}" "$access" | field flow_id)
    api POST "/api/config/config_entries/subentries/flow/$flow" "{\"url\":\"$SANDBOX_STORE\"}" "$access" >/dev/null ||
      echo "the app store repository was not added; add it in the integration" >&2
    echo "Docker Apps set up"
  fi
  echo "ready: run '$0 tunnel' and open $CLIENT"
}

case ${1:-} in
up) up ;;
deploy)
  copy_integration
  remote '$D exec docker-apps-sandbox docker restart homeassistant >/dev/null'
  wait_ready
  remote '$D exec docker-apps-sandbox docker logs --since 2m homeassistant 2>&1 | grep -E "ERROR|Traceback|WARNING.*docker_apps" | grep -v "not been tested" | tail -5 || true'
  echo deployed
  ;;
token) token ;;
tunnel) exec "${SSH[@]}" -N -o ExitOnForwardFailure=yes -L "$PORT:127.0.0.1:$PORT" ;;
down)
  # the engine folder belongs to root: delete it from a container
  remote "[ -d \$S ] || exit 0
\$D exec docker-apps-sandbox sh -c 'docker ps -aq | xargs -r docker rm -f >/dev/null; docker system prune -af --volumes >/dev/null' 2>/dev/null || true
cd \$S && \$D compose down -v
\$D run --rm -v \$S:/s $DIND sh -c 'rm -rf /s/engine /s/workspace'
cd && rm -rf \$S && \$D rmi $DIND >/dev/null"
  echo "sandbox removed"
  ;;
*)
  sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
  ;;
esac
