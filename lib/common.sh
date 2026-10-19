#!/usr/bin/env bash
# shellcheck shell=bash
init_defaults() {
    export PYTHONDONTWRITEBYTECODE=1
    : "${STATE_DIR:=/var/lib/nginx-manager}"
    : "${DRY_RUN:=false}"
    : "${NGINX_BIN:=nginx}"
    : "${NGINX_SERVICE:=nginx}"
    : "${NGINX_CONF:=}"
    : "${CERTBOT_DIR:=/etc/letsencrypt}"
    : "${ACME_ROOT:=$(dirname "$STATE_DIR")/nginx-manager-acme}"
    : "${PROFILE:=}"
    : "${TX:=}"
    : "${CF_CIDRS:=}"
    : "${CERT:=}"
    : "${KEY:=}"
    declare -gA SITE=()
    COLOR='' RESET=''
    if [[ -t 1 && ${TERM:-dumb} != dumb && -z ${NO_COLOR:-} ]]; then
        COLOR=$'\033[1;36m'; RESET=$'\033[0m'
    fi
}
info() { printf '%s%s%s\n' "$COLOR" "$*" "$RESET"; }
warn() { printf 'Warning: %s\n' "$*" >&2; }
fail() { printf 'Error: %s\n' "$*" >&2; return 1; }
helper() { python3 "$MANAGER_DIR/lib/config.py" "$@"; }
fs() { python3 "$MANAGER_DIR/lib/filesystem.py" "$@"; }
json_field() { python3 -c 'import json,sys; v=json.load(sys.stdin)[sys.argv[1]]; print(str(v).lower() if isinstance(v,bool) else v)' "$1"; }
confirm() {
    local answer
    printf '%s [y/N]: ' "$*" >&2
    IFS= read -r answer || return 1
    [[ $answer == y || $answer == Y || $answer == yes ]]
}
require_root() { [[ $EUID -eq 0 ]] || fail 'Run this operation with sudo on the Nginx host.'; }
secure_state() {
    python3 - "$STATE_DIR" <<'PY'
import os, pathlib, stat, sys
p=pathlib.Path(sys.argv[1])
if not p.is_absolute() or '..' in p.parts:
    raise SystemExit('State directory must be absolute')
for q in [p,*p.parents]:
    if q.is_symlink(): raise SystemExit('State directory cannot use symlinks')
    if q.exists() and q != pathlib.Path('/tmp') and q.stat().st_mode & stat.S_IWOTH:
        raise SystemExit('Unsafe world-writable ancestor of state directory')
if p.exists() and (p.stat().st_uid != os.geteuid() or p.stat().st_mode & 0o077):
    raise SystemExit('State directory must be owned by the invoking user with mode 700')
p.mkdir(parents=True,mode=0o700,exist_ok=True)
for name in ['profiles','metadata','backups','transactions']:
    q=p/name
    if q.is_symlink(): raise SystemExit('Refusing symlink in state')
    q.mkdir(mode=0o700,exist_ok=True)
    if q.stat().st_uid != os.geteuid() or q.stat().st_mode & 0o077:
        raise SystemExit('Unsafe state subdirectory permissions')
PY
}
acquire_lock() {
    secure_state || return
    exec {LOCK_FD}>"$STATE_DIR/lock"
    flock -n "$LOCK_FD" || fail 'Another manager is modifying this configuration.'
}
load_profile() {
    local content k v
    content=$(helper validate "$1") || return
    PROFILE_JSON=$content
    SITE=()
    while IFS=$'\t' read -r k v; do SITE[$k]=$v; done < <(helper load /dev/stdin <<< "$content")
}
site_json() {
    local -a args=()
    local k
    for k in "${!SITE[@]}"; do args+=("$k=${SITE[$k]}"); done
    helper make "${args[@]}"
}
render_site() {
    local json=$1
    helper render /dev/stdin "$ACME_ROOT" "$CERT" "$KEY" "$CF_CIDRS" <<< "$json"
}
validate_domain() {
    python3 - "$MANAGER_DIR/lib" "$1" <<'PY'
import sys
sys.path.insert(0,sys.argv[1])
from config import domain
raise SystemExit(0 if domain(sys.argv[2]) else 'Invalid domain')
PY
}
require_commands() {
    local cmd missing=()
    for cmd in python3 curl openssl dig flock systemctl "$NGINX_BIN"; do
        command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
    done
    ((${#missing[@]} == 0)) || fail "Missing commands: ${missing[*]}. Use the dependencies command to install Arch packages."
}
install_dependencies() {
    local packages=(nginx certbot certbot-nginx curl openssl bind python util-linux)
    info "Arch packages: ${packages[*]}. Installs use pacman -S --needed (no partial database refresh)."
    if [[ $DRY_RUN == true ]]; then return; fi
    require_root || return
    command -v pacman >/dev/null || { fail 'pacman is unavailable; install dependencies for your OS manually.'; return 1; }
    confirm 'Install these packages?' || return 1
    pacman -S --needed "${packages[@]}"
}
