#!/usr/bin/env bash
# Globals assigned here are consumed by the other sourced modules.
# shellcheck disable=SC2034
set +x
set -Eeuo pipefail
umask 077
if ((BASH_VERSINFO[0] < 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] < 4))); then
    printf 'Bash 4.4 or later is required.\n' >&2
    exit 1
fi
MANAGER_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
for module in common os validation backups nginx certbot cloudflare diagnostics prompts; do
    # shellcheck source=/dev/null
    source "$MANAGER_DIR/lib/$module.sh"
done
init_defaults
usage() {
    cat <<'EOF'
Usage: sudo ./nginx-manager.sh [command] [options]
No command starts the interactive SSH-friendly menu.

Commands:
  deploy --profile FILE       Preview and deploy a new JSON profile
  update --profile FILE       Preview and update a manager-owned website
  os-info                    Show detected OS and dependency platform
  render --profile FILE       Print configuration only (no root/Nginx needed)
  wizard [--profile FILE]     Collect or edit settings interactively
  list                       List loaded websites and owned profiles
  inspect DOMAIN             Show a managed profile and config
  health DOMAIN              DNS, upstream, HTTP, TLS and redacted diagnostics
  ssl DOMAIN                 Configure/recheck HTTPS for a managed site
  renew                      Certbot renewal simulation (explicit confirmation)
  enable|disable DOMAIN      Activate/deactivate an owned website
  remove DOMAIN              Remove owned files; retain DNS and certificates
  cloudflare                 Confirmed A/AAAA/CNAME record management
  backup                     Snapshot loaded configs and managed state
  restore FILE               Restore reviewed backup files with rollback
  recover FILE               Roll back a pending transaction after a crash
  dependencies               Detect OS and ask before installing dependencies

Options:
  --profile FILE             Non-executable JSON profile (.conf)
  --dry-run                  Read/preview only; no local or DNS mutations
  --nginx-conf /PATH         Actual service config (-c), if not compiled default
  --nginx-bin /PATH          Nginx binary (default: nginx on PATH)
  --service NAME             systemd unit (default: nginx)
  --state-dir /PATH          Private state (default: /var/lib/nginx-manager)
  --acme-root /PATH          Public challenge webroot (default: sibling of state)
  --certbot-dir /PATH        Certbot config directory (default: /etc/letsencrypt)
  --ca-bundle /PATH          CA trust bundle (default: discover system/OpenSSL bundle)
  --renewal-timer NAME       Select an installed Certbot timer (default: discover)
  --help                     Show this help

Exit codes: 0 success, 1 failed/declined operation, 2 invalid CLI use,
130 interrupted, 143 terminated. No --yes option: mutations need review.
EOF
}
command_name='' argument='' acme_explicit=false
while (($#)); do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --dry-run) DRY_RUN=true; shift ;;
        --profile|--nginx-conf|--nginx-bin|--service|--state-dir|--acme-root|--certbot-dir|--ca-bundle|--renewal-timer)
            (($# >= 2)) || { usage >&2; exit 2; }
            case "$1" in
                --profile) PROFILE=$2 ;; --nginx-conf) NGINX_CONF=$2 ;; --nginx-bin) NGINX_BIN=$2 ;;
                --service) NGINX_SERVICE=$2 ;; --state-dir) STATE_DIR=$2 ;;
                --acme-root) ACME_ROOT=$2; acme_explicit=true ;; --certbot-dir) CERTBOT_DIR=$2 ;;
                --ca-bundle) CA_BUNDLE=$2 ;; --renewal-timer) RENEWAL_TIMER=$2 ;;
            esac
            shift 2 ;;
        --*) fail "Unknown option: $1"; exit 2 ;;
        *)
            if [[ -z $command_name ]]; then command_name=$1
            elif [[ -z $argument ]]; then argument=$1
            else fail 'Too many arguments'; exit 2; fi
            shift ;;
    esac
 done
command -v python3 >/dev/null || { printf 'Python 3.9+ is a prerequisite; install it with your distro package manager.\n' >&2; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else "Python 3.9 or later is required")'
[[ $acme_explicit == true ]] || ACME_ROOT="$(dirname "$STATE_DIR")/nginx-manager-acme"
[[ $NGINX_SERVICE =~ ^[A-Za-z0-9@_.-]+$ && $NGINX_SERVICE != -* ]] || { fail 'Invalid systemd service name'; exit 2; }
for path in "$STATE_DIR" "$ACME_ROOT" "$CERTBOT_DIR"; do helper cert-path "$path" || exit 2; done
if [[ -n $CA_BUNDLE ]]; then CA_BUNDLE=$(python3 -B "$MANAGER_DIR/lib/host.py" ca-bundle "$CA_BUNDLE"); fi
if [[ -n $RENEWAL_TIMER && ( ! $RENEWAL_TIMER =~ ^[A-Za-z0-9@_.-]+\.timer$ || $RENEWAL_TIMER == -* ) ]]; then
    fail 'Invalid renewal timer name'; exit 2
fi
case "$command_name" in
    os-info) os_info; exit ;;
    render)
        [[ -n $PROFILE ]] || { fail '--profile is required'; exit 2; }
        load_profile "$PROFILE"
        if [[ ${SITE[https]} == true ]]; then find_certificate || { fail 'No valid certificate available. Use an HTTP profile for an issuance-free preview.'; exit 1; }; fi
        if [[ ${SITE[real_ip]} == true ]]; then fetch_cf_cidrs; fi
        render_site "$PROFILE_JSON"
        exit ;;
    ''|deploy|update|wizard|list|inspect|health|ssl|renew|enable|disable|remove|cloudflare|backup|restore|recover|dependencies) ;;
    *) fail "Unknown command: $command_name"; usage >&2; exit 2 ;;
esac
if [[ $DRY_RUN != true && $command_name != health && $command_name != list && $command_name != inspect ]]; then require_root; fi
case "$command_name" in
    '') require_commands; interactive_menu ;;
    deploy|update)
        [[ -n $PROFILE ]] || { fail '--profile is required'; exit 2; }
        deploy_profile "$PROFILE" "$([[ $command_name == update ]] && printf update || printf create)" ;;
    wizard) site_wizard "$([[ -n $PROFILE ]] && printf update || printf create)" "$PROFILE" ;;
    list) list_sites ;;
    inspect|health|ssl|enable|disable|remove)
        [[ -n $argument ]] || { fail 'A domain is required'; exit 2; }
        validate_domain "$argument"
        case "$command_name" in
            inspect) inspect_site "$argument" ;;
            health) health_site "$argument" ;;
            ssl) ssl_site "$argument" ;;
            *) site_action "$command_name" "$argument" ;;
        esac ;;
    renew) renew_certificates ;;
    cloudflare) cloudflare_menu ;;
    backup) backup_configuration ;;
    restore) [[ -n $argument ]] || exit 2; restore_backup "$argument" ;;
    recover) [[ -n $argument ]] || exit 2; recover_transaction "$argument" ;;
    dependencies) install_dependencies ;;
esac
