#!/usr/bin/env bash
# Globals assigned here are consumed by the other sourced modules.
# shellcheck disable=SC2034
# shellcheck shell=bash
declare -gA SITE
prompt() {
    local variable=$1 label=$2 default=${3:-} answer
    if [[ -n $default ]]; then printf '%s [%s]: ' "$label" "$default" >&2
    else printf '%s: ' "$label" >&2; fi
    IFS= read -r answer || return 1
    printf -v "$variable" '%s' "${answer:-$default}"
}
yes_default() {
    local answer
    printf '%s [Y/n]: ' "$*" >&2
    IFS= read -r answer || return 1
    [[ -z $answer || $answer == y || $answer == Y || $answer == yes ]]
}
choose_upstream() {
    local prefix=$1 hosting=$2 hostname port protocol
    prompt hostname 'Upstream hostname or IP (application listener, separate from DNS origin)' "$([[ $hosting == local ]] && printf 127.0.0.1)" || return
    prompt port 'Application port' '' || return
    prompt protocol 'Upstream protocol (http or https)' http || return
    if [[ $hosting == remote && $protocol == http ]]; then
        warn 'Remote HTTP is unencrypted. Prefer a private/VPN network or verified HTTPS. No application port will be opened.'
        confirm 'Use this remote HTTP upstream?' || return 1
    fi
    if [[ $prefix == api ]]; then SITE[api_host]=$hostname; SITE[api_port]=$port; SITE[api_protocol]=$protocol
    else SITE[host]=$hostname; SITE[port]=$port; SITE[protocol]=$protocol; fi
}
site_wizard() {
    local mode=${1:-create} domain choice detected json email origin
    local profile_path=${2:-}
    if [[ -n $profile_path ]]; then
        load_profile "$profile_path" || return
        domain=${SITE[domain]}
        info "Editing profile for $domain"
    else
        prompt domain 'Domain or subdomain (lowercase ASCII)' '' || return
        validate_domain "$domain" || return
        json=$(helper make "domain=$domain") || return
        load_profile /dev/stdin <<< "$json" || return
    fi
    discover_nginx || return
    if [[ $mode == create ]] && ! helper conflicts "$domain" <<< "$NGINX_DUMP"; then
        info 'This hostname is already configured. No duplicate will be created.'
        if confirm 'List its loaded configuration sources and listeners?'; then helper inventory <<< "$NGINX_DUMP"; fi
        return 1
    fi
    prompt choice 'Application hosted on [1] this server or [2] another server' "$([[ ${SITE[hosting]} == remote ]] && printf 2 || printf 1)" || return
    case "$choice" in 1) SITE[hosting]=local ;; 2) SITE[hosting]=remote ;; *) fail 'Select 1 or 2'; return 1 ;; esac
    detected=$(curl --noproxy '*' -fsS --connect-timeout 3 --max-time 5 https://api.ipify.org 2>/dev/null) || detected=''
    if ! helper ip "$detected" >/dev/null 2>&1; then detected=''; fi
    [[ -z $detected ]] || info "Detected management server public IP: $detected. This may differ from your DNS origin."
    prompt origin 'Public origin IP used for DNS' "${SITE[origin_ip]:-$detected}" || return
    helper ip "$origin" >/dev/null || return
    confirm "Is $origin the public DNS destination for Nginx (not the application upstream)?" || return 1
    SITE[origin_ip]=$origin SITE[dns_confirmed]=true
    if check_dns; then info 'DNS currently matches the origin.'; else warn 'DNS differs or is not yet published; HTTPS bootstrap will require a match.'; fi
    printf '\n[1] Next.js\n[2] Node.js / Express\n[3] React / Vite build\n[4] Angular build\n[5] Static HTML\n[6] API backend\n[7] Reverse proxy\n[8] Custom config\n'
    prompt choice 'Application type' '' || return
    case "$choice" in
        1) SITE[app]=nextjs ;; 2) SITE[app]=node ;; 3) SITE[app]=react ;; 4) SITE[app]=angular ;;
        5) SITE[app]=static ;; 6) SITE[app]=api ;; 7) SITE[app]=proxy ;; 8) SITE[app]=custom ;;
        *) fail 'Invalid application selection'; return 1 ;;
    esac
    case "${SITE[app]}" in
        react|angular|static)
            prompt 'SITE[root]' 'Absolute production web root' "${SITE[root]}" || return
            SITE[spa]=false
            if [[ ${SITE[app]} == static ]] && confirm 'Use index.html fallback for client-side routes?'; then SITE[spa]=true; fi ;;
        custom)
            info 'Custom file must be an absolute path containing reviewed locations/body settings, with no listen/server_name/include directives.'
            prompt 'SITE[custom_file]' 'Custom Nginx body file' "${SITE[custom_file]}" || return ;;
        *) choose_upstream main "${SITE[hosting]}" || return ;;
    esac
    SITE[api]=false
    if confirm 'Does this website have a separate API service?'; then
        SITE[api]=true
        prompt 'SITE[api_prefix]' 'API path prefix (preserved upstream)' "${SITE[api_prefix]}" || return
        choose_upstream api "${SITE[hosting]}" || return
    fi
    SITE[websocket]=false SITE[streaming]=false
    if confirm 'Enable WebSocket upgrades?'; then SITE[websocket]=true; fi
    if confirm 'Stream responses (disable body buffering, retain header buffer)?'; then SITE[streaming]=true; fi
    info 'Proxy timeout default is 60 seconds. Increase for long-running requests; this does not change Cloudflare plan limits.'
    prompt 'SITE[timeout]' 'Proxy read/send timeout in seconds' "${SITE[timeout]}" || return
    info 'Buffers: standard=8k, auth=32k (Supabase SSR cookies), large=64k. All are bounded; values remain effective for streaming.'
    prompt 'SITE[buffer]' 'Response-header buffer profile (standard/auth/large)' "${SITE[buffer]}" || return
    prompt 'SITE[body_size]' 'Maximum request body size (e.g. 10m)' "${SITE[body_size]}" || return
    SITE[cloudflare]=none SITE[proxy_mode]=false SITE[real_ip]=false
    if confirm 'Is this domain managed by Cloudflare?'; then
        prompt choice 'DNS setup: [1] API or [2] manual dashboard' 2 || return
        case "$choice" in
            1) SITE[cloudflare]=api
                prompt 'SITE[zone_id]' 'Cloudflare zone ID (not a token)' "${SITE[zone_id]}" || return ;;
            2) SITE[cloudflare]=manual ;;
            *) fail 'Invalid Cloudflare selection'; return 1 ;;
        esac
        if confirm 'Use Cloudflare proxied mode after successful setup (orange cloud)?'; then SITE[proxy_mode]=true; fi
        if confirm 'Restore visitor IPs only from verified Cloudflare proxy networks?'; then SITE[real_ip]=true; fi
    fi
    SITE[https]=false
    if yes_default "Enable HTTPS with Let's Encrypt?"; then
        SITE[https]=true
        prompt email "Let's Encrypt contact email" "${SITE[email]}" || return
        SITE[email]=$email
    fi
    json=$(site_json) || return
    PROFILE_JSON=$json
    # Keep terminal stdin available for deployment confirmations.
    deploy_loaded_profile "$mode"
}
manage_menu() {
    local domain choice json
    prompt domain 'Managed domain' '' || return
    validate_domain "$domain" || return
    printf '[1] Inspect\n[2] Edit all settings\n[3] Change upstream/port/buffers\n[4] Test upstream\n[5] Disable\n[6] Enable\n[7] SSL/health\n[8] Error summary\n'
    prompt choice 'Action' '' || return
    case "$choice" in
        1) inspect_site "$domain" ;;
        2) site_wizard update "$STATE_DIR/profiles/$domain.conf" ;;
        3)
            discover_nginx || return; managed_metadata "$domain" || return
            load_profile "$STATE_DIR/profiles/$domain.conf" || return
            prompt 'SITE[host]' 'Upstream host' "${SITE[host]}" || return
            prompt 'SITE[port]' 'Application port' "${SITE[port]}" || return
            prompt 'SITE[buffer]' 'Buffer profile (standard/auth/large)' "${SITE[buffer]}" || return
            json=$(site_json) || return
            PROFILE_JSON=$json
            deploy_loaded_profile update ;;
        4) load_profile "$STATE_DIR/profiles/$domain.conf" && check_upstreams ;;
        5) site_action disable "$domain" ;;
        6) site_action enable "$domain" ;;
        7) health_site "$domain" ;;
        8) site_error_summary "$domain" ;;
        *) fail 'Invalid selection' ;;
    esac
}
menu_operation() {
    local choice=$1 domain backup
    case "$choice" in
        1) site_wizard ;;
        2) manage_menu ;;
        3) prompt domain 'Domain to diagnose' '' && health_site "$domain" ;;
        4)
            printf '[1] Configure managed site HTTPS\n[2] Renewal dry-run\n'
            prompt choice 'Action' 1 || return
            case "$choice" in
                1) prompt domain 'Managed domain' '' && ssl_site "$domain" ;;
                2) renew_certificates ;;
                *) fail 'Invalid selection' ;;
            esac ;;
        5) list_sites ;;
        6) prompt domain 'Managed domain to remove' '' && site_action remove "$domain" ;;
        7) cloudflare_menu ;;
        8) backup_configuration ;;
        9) prompt backup 'Absolute backup manifest path' '' && restore_backup "$backup" ;;
        *) fail 'Invalid selection' ;;
    esac
}
interactive_menu() {
    local choice status
    detect_os || return
    while true; do
        printf '\n==========================================\n       NGINX DEPLOYMENT MANAGER\n==========================================\n[1] Add new domain / subdomain\n[2] Manage existing website\n[3] Check website health\n[4] Configure / renew SSL\n[5] View hosted websites\n[6] Remove a managed website\n[7] Cloudflare DNS management\n[8] Backup Nginx configuration\n[9] Restore a configuration backup\n[0] Exit\n'
        info "Detected OS: $OS_NAME ($OS_FAMILY)"
        prompt choice 'Select an option' '' || return 0
        [[ $choice != 0 ]] || return 0
        # Each operation gets an EXIT rollback trap and releases its flock before returning.
        set +e
        ( set -Eeuo pipefail; menu_operation "$choice" )
        status=$?
        set -e
        if ((status != 0)); then warn "Operation cancelled or failed (exit $status); returning to menu."; fi
    done
}
