#!/usr/bin/env bash
# shellcheck shell=bash
certificate_valid() {
    local cert=$1 key=$2
    [[ -f $cert && -f $key ]] &&
        openssl x509 -in "$cert" -noout -checkend 0 >/dev/null 2>&1 &&
        openssl x509 -in "$cert" -noout -checkhost "${SITE[domain]}" >/dev/null 2>&1 &&
        openssl verify -untrusted "$cert" "$cert" >/dev/null 2>&1
}
find_certificate() {
    if [[ -n $CERT && -n $KEY ]] && certificate_valid "$CERT" "$KEY"; then return 0; fi
    local cert key
    CERT='' KEY=''
    for cert in "$CERTBOT_DIR"/live/*/fullchain.pem; do
        [[ -f $cert ]] || continue
        key="$(dirname "$cert")/privkey.pem"
        if certificate_valid "$cert" "$key"; then
            CERT=$cert KEY=$key
            return 0
        fi
    done
    return 1
}
challenge_preflight() {
    local token challenge_path result resolve_ip=${SITE[origin_ip]}
    token=$(openssl rand -hex 16) || return
    challenge_path="$ACME_ROOT/.well-known/acme-challenge/$token"
    fs acme-token "$ACME_ROOT" "$token" || return
    [[ $resolve_ip != *:* ]] || resolve_ip="[$resolve_ip]"
    result=$(curl --noproxy '*' -s --connect-timeout 5 --max-time 10 \
        --resolve "${SITE[domain]}:80:$resolve_ip" "http://${SITE[domain]}/.well-known/acme-challenge/$token") || result=''
    rm -f -- "$challenge_path"
    [[ $result == "$token" ]] || { fail 'Origin HTTP challenge probe failed. Check inbound TCP 80, NAT, firewall and DNS. No certificate requested.'; return 1; }
    info 'Origin challenge probe succeeded from this host. External reachability must also be verified.'
    confirm 'Have you verified inbound TCP 80 is reachable from the public internet for HTTP-01?'
}
configure_ssl() {
    local desired_json=$1 http_json rendered bootstrap_manifest certname
    command -v certbot >/dev/null || { fail 'Certbot is missing. Run dependencies first.'; return 1; }
    if find_certificate; then
        info 'Reusing an existing, unexpired certificate that covers this hostname.'
        rendered=$(render_site "$desired_json") || return
        apply_rendered_site "$desired_json" "$rendered" || return
    else
        [[ -n ${SITE[origin_ip]} && -n ${SITE[email]} ]] || { fail 'HTTPS issuance requires a public DNS origin IP and contact email.'; return 1; }
        check_dns || { fail 'Public A/AAAA resolution must match the configured origin. Remove stale records; use explicitly confirmed DNS-only bootstrap for Cloudflare.'; return 1; }
        warn 'HTTP-01 needs public inbound TCP 80. No firewall or application ports will be opened automatically.'
        confirm 'Start HTTP-01 bootstrap and request a certificate from Let’s Encrypt (rate limits apply)?' || return 1
        SITE[https]=false
        http_json=$(site_json) || return
        rendered=$(render_site "$http_json") || return
        SITE[https]=true
        apply_rendered_site "$http_json" "$rendered" || return
        bootstrap_manifest=$LAST_TX
        # A failed issuer restores the previous host while retaining Certbot's own artifacts.
        if ! challenge_preflight || ! certbot certonly --webroot -w "$ACME_ROOT" \
            --config-dir "$CERTBOT_DIR" --cert-name "${SITE[domain]}" -d "${SITE[domain]}" \
            --email "${SITE[email]}" --agree-tos --non-interactive --keep-until-expiring; then
            warn 'Certificate setup failed. Restoring the pre-bootstrap files. Certbot artifacts and confirmed DNS changes are retained.'
            fs rollback "$bootstrap_manifest" || return 1
            nginx_run -t && systemctl reload "$NGINX_SERVICE" || warn 'Prior configuration needs a manual service check.'
            return 1
        fi
        find_certificate || { fail 'Issuer returned without a valid matching certificate; HTTP bootstrap remains active for recovery.'; return 1; }
        discover_nginx || return
        rendered=$(render_site "$desired_json") || return
        apply_rendered_site "$desired_json" "$rendered" || return
    fi
    certname=$(basename "$(dirname "$CERT")")
    info "Certificate lineage: $certname"
    if ! systemctl is-enabled certbot-renew.timer >/dev/null 2>&1; then
        warn 'certbot-renew.timer is not enabled. An existing cron/alternate renewal service may be in use.'
        if confirm 'Enable and start the Arch certbot-renew.timer?'; then systemctl enable --now certbot-renew.timer || return; fi
    fi
    info 'Webroot certificates also need an Nginx reload after renewal.'
    install_renewal_hook || return
    if confirm 'Run a renewal dry-run now (contacts the staging CA)?'; then
        certbot renew --cert-name "$certname" --config-dir "$CERTBOT_DIR" --dry-run || warn 'Renewal simulation failed; fix renewal before relying on automatic HTTPS.'
    fi
    local code
    code=$(curl --noproxy '*' -s -o /dev/null --connect-timeout 5 --max-time 15 -w '%{http_code}' "https://${SITE[domain]}/") || {
        warn 'Origin TLS is configured; public HTTPS verification failed. Check DNS, firewall and Cloudflare mode.'; return 0;
    }
    info "Public HTTPS response: $code"
    [[ ${SITE[cloudflare]} == none ]] || info 'Set Cloudflare SSL/TLS mode to Full (strict). Zone-wide settings are not changed.'
}
install_renewal_hook() {
    local path="$CERTBOT_DIR/renewal-hooks/deploy/nginx-manager-reload" content
    local -a options=(-t -c "$NGINX_CONF")
    [[ -z ${NGINX_PREFIX:-} ]] || options+=(-p "$NGINX_PREFIX")
    content=$(printf '#!/bin/bash\n# nginx-manager renewal hook\n'; printf '%q ' "$(command -v "$NGINX_BIN")" "${options[@]}"; printf '&& /usr/bin/systemctl reload %q\n' "$NGINX_SERVICE")
    if [[ -f $path ]]; then
        [[ $(cat "$path") == "$content" ]] && return 0
        warn 'A renewal hook already exists at the manager path. It will not be overwritten; review renewal reload handling manually.'
        return 0
    fi
    info "Renewal hook to create: $path"
    confirm 'Install this syntax-check-and-reload deploy hook?' || { warn 'Install an appropriate renewal reload hook manually.'; return 0; }
    transaction_begin || return
    transaction_write "$path" 755 <<< "$content" || return
    fs commit "$TX" || return
    TX=''
}
renew_certificates() {
    command -v certbot >/dev/null || { fail 'Certbot missing.'; return 1; }
    info 'certbot renew --dry-run: staging validation, no replacement of live certificates.'
    [[ $DRY_RUN != true ]] || return
    confirm 'Run renewal simulation?' || return 1
    certbot renew --config-dir "$CERTBOT_DIR" --dry-run
}
ssl_site() {
    local domain=$1 json
    discover_nginx || return
    managed_metadata "$domain" || return
    [[ $(json_field enabled <<< "$META") == true ]] || { fail 'Enable the site before configuring HTTPS.'; return 1; }
    load_profile "$STATE_DIR/profiles/$domain.conf" || return
    SITE[https]=true
    if [[ -z ${SITE[email]} ]]; then prompt SITE_EMAIL 'Let’s Encrypt contact email' ''; SITE[email]=$SITE_EMAIL; fi
    if [[ -z ${SITE[origin_ip]} ]]; then prompt SITE_ORIGIN 'Public DNS origin IP' ''; SITE[origin_ip]=$SITE_ORIGIN; fi
    json=$(site_json) || return
    info "Enable/recheck HTTPS for $domain, preserving certificates for every other hostname."
    [[ $DRY_RUN != true ]] || { info 'Dry-run: certificate, DNS and challenge checks would precede issuance.'; return 0; }
    confirm 'Configure HTTPS?' || return 1
    acquire_lock || return
    [[ ${SITE[cloudflare]} == none ]] || configure_dns || return
    configure_ssl "$json" || return
    if [[ ${SITE[cloudflare]} == api && ${SITE[proxy_mode]} == true ]]; then
        cf_set_origin_record true || { warn 'HTTPS succeeded; desired Cloudflare proxy activation is pending.'; return 1; }
    fi
}
