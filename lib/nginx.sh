#!/usr/bin/env bash
# shellcheck shell=bash
# Resolve the same binary, configuration and prefix as the selected systemd service.
resolve_nginx_conf() {
    local version service target
    command -v "$NGINX_BIN" >/dev/null || { fail 'Nginx binary not found.'; return 1; }
    version=$("$NGINX_BIN" -V 2>&1) || return
    service=$(systemctl show --property=ExecStart --value "$NGINX_SERVICE" 2>/dev/null) || {
        fail 'Cannot inspect systemd ExecStart. Select the actual Nginx service with --service.'; return 1;
    }
    target=$(python3 "$MANAGER_DIR/lib/service.py" "$NGINX_CONF" "$(command -v "$NGINX_BIN")" "$version" <<< "$service") || return
    NGINX_CONF=$(json_field config <<< "$target")
    NGINX_PREFIX=$(json_field prefix <<< "$target")
}
nginx_run() {
    local -a options=(-c "$NGINX_CONF")
    [[ -z ${NGINX_PREFIX:-} ]] || options+=(-p "$NGINX_PREFIX")
    "$NGINX_BIN" "$@" "${options[@]}"
}
discover_nginx() {
    local layout
    resolve_nginx_conf || return
    helper cert-path "$NGINX_CONF" || return
    NGINX_DUMP=$(nginx_run -T 2>/dev/null) || { fail 'nginx -T failed. Check the active service config path and nginx -t.'; return 1; }
    layout=$(helper layout "$NGINX_CONF" "$(dirname "$NGINX_CONF")/nginx-manager.d" <<< "$NGINX_DUMP") || return
    SITE_DIR=$(json_field site_dir <<< "$layout")
    INCLUDE_FILE=$(json_field file <<< "$layout")
    INCLUDE_CHANGED=$(json_field changed <<< "$layout")
    INCLUDE_CONTENT=$(json_field content <<< "$layout")
    helper cert-path "$SITE_DIR" || return
}
managed_metadata() {
    local domain=$1 meta
    validate_domain "$domain" || return
    meta="$STATE_DIR/metadata/$domain.json"
    [[ -f $meta && ! -L $meta ]] || { fail 'This domain is not manager-owned. Inspect the existing configuration; it will not be imported automatically.'; return 1; }
    META=$(fs verify "$meta") || return
    OWNED_PATH=$(json_field path <<< "$META")
    [[ $OWNED_PATH == "$SITE_DIR/$domain.conf" || $OWNED_PATH == "$SITE_DIR/$domain.conf.disabled" ]] || {
        fail 'Managed path no longer matches the active include layout.'; return 1;
    }
    CERT=$(json_field cert <<< "$META")
    KEY=$(json_field key <<< "$META")
    CF_CIDRS=$(json_field cidrs <<< "$META")
}
preview_deployment() {
    local rendered=$1
    printf '\n========== DEPLOYMENT SUMMARY ==========\n'
    printf 'Domain: %s\nDNS origin IP: %s\nManagement host: %s\nApplication: %s\nHosting: %s\n' \
        "${SITE[domain]}" "${SITE[origin_ip]:-(unspecified)}" "$(uname -n)" "${SITE[app]}" "${SITE[hosting]}"
    printf 'Application upstream: %s\nWeb root: %s\nAPI: %s\nHTTPS: %s\nCloudflare: %s (desired proxied=%s)\n' \
        "$(upstream_url "${SITE[host]}" "${SITE[port]}" "${SITE[protocol]}")" "${SITE[root]:-(none)}" "${SITE[api]}" "${SITE[https]}" "${SITE[cloudflare]}" "${SITE[proxy_mode]}"
    printf 'WebSocket: %s\nStreaming: %s\nBuffer profile: %s\nTimeout: %ss\nBody limit: %s\n' \
        "${SITE[websocket]}" "${SITE[streaming]}" "${SITE[buffer]}" "${SITE[timeout]}" "${SITE[body_size]}"
    printf 'Write: %s/%s.conf\nWrite: %s/profiles/%s.conf and metadata\n' "$SITE_DIR" "${SITE[domain]}" "$STATE_DIR" "${SITE[domain]}"
    if [[ $INCLUDE_CHANGED == true ]]; then
        printf 'Insert include inside http in: %s\n' "$INCLUDE_FILE"
    fi
    printf '\nGenerated virtual host:\n%s\n' "$rendered"
}
write_site_state() {
    local json=$1 target=$2 enabled=$3
    transaction_write "$STATE_DIR/profiles/${SITE[domain]}.conf" 600 <<< "$json" || return
    fs metadata "$target" "$enabled" "$CERT" "$KEY" "$CF_CIDRS" | transaction_write "$STATE_DIR/metadata/${SITE[domain]}.json" 600
}
apply_rendered_site() {
    local json=$1 rendered=$2 target="$SITE_DIR/${SITE[domain]}.conf"
    transaction_begin || return
    if [[ $INCLUDE_CHANGED == true ]]; then
        transaction_write "$INCLUDE_FILE" <<< "$INCLUDE_CONTENT" || return
    fi
    transaction_write "$target" <<< "$rendered" || return
    write_site_state "$json" "$target" true || return
    transaction_commit
}
deploy_profile() {
    local input=$1 mode=${2:-create}
    load_profile "$input" || return
    deploy_loaded_profile "$mode"
}
deploy_loaded_profile() {
    local mode=${1:-create} json rendered conflicts old_json
    json=$PROFILE_JSON
    discover_nginx || return
    CERT='' KEY='' CF_CIDRS=''
    if [[ -f $STATE_DIR/metadata/${SITE[domain]}.json ]]; then
        managed_metadata "${SITE[domain]}" || return
        [[ $(json_field enabled <<< "$META") == true ]] || { fail 'This site is disabled; explicitly enable it before updating.'; return 1; }
        old_json=$(helper validate "$STATE_DIR/profiles/${SITE[domain]}.conf") || return
        if [[ $mode != update && $old_json == "$json" && $(json_field enabled <<< "$META") == true ]]; then
            info 'Already deployed with these settings; no changes.'
            return 0
        fi
        [[ $mode == update ]] || { fail 'Managed site exists. Use update to change settings, or enable to activate it.'; return 1; }
    else
        [[ $mode != update ]] || { fail 'Cannot update an unmanaged site.'; return 1; }
        [[ ! -e $SITE_DIR/${SITE[domain]}.conf && ! -e $SITE_DIR/${SITE[domain]}.conf.disabled ]] || {
            fail 'Unowned file exists at target path.'; return 1;
        }
        OWNED_PATH=''
    fi
    if ! conflicts=$(helper conflicts "${SITE[domain]}" "$OWNED_PATH" <<< "$NGINX_DUMP"); then
        fail "Domain conflicts with existing configuration: $conflicts"; return 1
    fi
    validate_paths || return
    if [[ ${SITE[hosting]} == remote && ( ${SITE[protocol]} == http || ( ${SITE[api]} == true && ${SITE[api_protocol]} == http ) ) ]]; then
        warn 'Remote HTTP upstream traffic is unencrypted. Use a private/VPN route or verified HTTPS; application ports remain untouched.'
    fi
    if [[ ${SITE[real_ip]} == true ]]; then
        if [[ $DRY_RUN == true && -z $CF_CIDRS ]]; then
            info 'Dry-run: trusted real-IP ranges would be fetched and validated from Cloudflare.'
        fi
        fetch_cf_cidrs || return
    fi
    # Preview HTTPS bootstrap without inventing certificate paths.
    if [[ ${SITE[https]} == true ]] && ! find_certificate; then
        local original_https=${SITE[https]}
        SITE[https]=false
        rendered=$(render_site "$(site_json)") || return
        SITE[https]=$original_https
        preview_deployment "$rendered"
        info 'TLS phase: obtain a certificate via HTTP-01, then replace this host with HTTPS and an HTTP redirect.'
    else
        rendered=$(render_site "$json") || return
        if [[ $mode == update && ${old_json:-} == "$json" && -n ${OWNED_PATH:-} && $(cat "$OWNED_PATH") == "$rendered" ]]; then
            info 'Already deployed with these settings and current trusted-proxy ranges; no changes.'
            return 0
        fi
        preview_deployment "$rendered"
    fi
    if [[ $DRY_RUN == true ]]; then
        info 'Dry-run complete: no files, services, certificates or DNS records changed.'
        return 0
    fi
    confirm 'Proceed with deployment?' || return 1
    # Recheck authoritative files after the operator has reviewed the preview and while locked.
    acquire_lock || return
    local before_dump=$NGINX_DUMP
    discover_nginx || return
    [[ $before_dump == "$NGINX_DUMP" ]] || { fail 'Nginx changed during preview; retry to review current configuration.'; return 1; }
    check_upstreams || confirm 'Upstream is unavailable. Deploy the reviewed configuration anyway?' || return 1
    if [[ ${SITE[cloudflare]} != none ]]; then configure_dns || return; fi
    if [[ ${SITE[https]} == true ]]; then
        configure_ssl "$json" || return
    else
        apply_rendered_site "$json" "$rendered" || return
    fi
    if [[ ${SITE[cloudflare]} == api && ${SITE[proxy_mode]} == true ]]; then
        cf_set_origin_record true || warn 'Site is active; desired Cloudflare proxy activation is pending. Re-run DNS management.'
    fi
    info 'Deployment finished. Run health to verify public traffic and worker filesystem permissions.'
}
site_action() {
    local action=$1 domain=$2 target content json
    discover_nginx || return
    managed_metadata "$domain" || return
    load_profile "$STATE_DIR/profiles/$domain.conf" || return
    json=$PROFILE_JSON
    target="$SITE_DIR/$domain.conf"
    case "$action" in
        disable)
            [[ $OWNED_PATH != *.disabled ]] || { info 'Already disabled.'; return 0; }
            [[ ! -e $target.disabled ]] || { fail 'Disabled target already exists.'; return 1; }
            info "Move $target to $target.disabled; DNS and certificates remain."
            ;;
        enable)
            [[ $OWNED_PATH == *.disabled ]] || { info 'Already enabled.'; return 0; }
            [[ ! -e $target ]] || { fail 'Enabled target already exists.'; return 1; }
            helper conflicts "$domain" <<< "$NGINX_DUMP" || { fail 'A loaded host conflicts with this domain.'; return 1; }
            info "Move $OWNED_PATH to $target."
            ;;
        remove)
            info "Remove exactly: $OWNED_PATH, $STATE_DIR/profiles/$domain.conf, $STATE_DIR/metadata/$domain.json"
            info 'Affected DNS records: none. Certificates: retained.'
            ;;
        *) fail 'Unknown action'; return 1 ;;
    esac
    [[ $DRY_RUN != true ]] || return 0
    confirm "Confirm $action for $domain?" || return 1
    acquire_lock || return
    managed_metadata "$domain" || return
    transaction_begin || return
    content=$(cat "$OWNED_PATH") || return
    transaction_delete "$OWNED_PATH" || return
    case "$action" in
        disable)
            transaction_write "$target.disabled" <<< "$content" || return
            write_site_state "$json" "$target.disabled" false || return ;;
        enable)
            transaction_write "$target" <<< "$content" || return
            write_site_state "$json" "$target" true || return ;;
        remove)
            transaction_delete "$STATE_DIR/profiles/$domain.conf" || return
            transaction_delete "$STATE_DIR/metadata/$domain.json" || return ;;
    esac
    transaction_commit
}
list_sites() {
    discover_nginx || return
    info 'Loaded Nginx listeners and names (including unowned sites):'
    helper inventory <<< "$NGINX_DUMP"
    info 'Manager-owned profiles:'
    local file
    if [[ -d $STATE_DIR/profiles ]]; then
        for file in "$STATE_DIR"/profiles/*.conf; do
            [[ -f $file ]] || continue
            printf '%s\n' "$(basename "$file" .conf)"
        done
    fi
}
inspect_site() {
    discover_nginx || return
    managed_metadata "$1" || return
    helper validate "$STATE_DIR/profiles/$1.conf" || return
    info "Owned config: $OWNED_PATH"
    cat "$OWNED_PATH"
}
