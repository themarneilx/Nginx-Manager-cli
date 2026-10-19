#!/usr/bin/env bash
# Globals assigned here are consumed by the other sourced modules.
# shellcheck disable=SC2034
# shellcheck shell=bash
declare -gA SITE
explain_status() {
    case "$1" in
        000) info 'No HTTP response: investigate DNS, network, listener and TLS connectivity.' ;;
        301|302|303|307|308) info 'Redirect response: check the configured canonical URL, HTTPS redirect and application redirect rules. Location values are suppressed.' ;;
        502) info '502: Nginx could not use the application response. Check upstream reachability, crashes, protocol and response-header buffer size.' ;;
        521) info '521: Cloudflare reports that the origin refused its connection. Check Nginx listener/service and origin firewall.' ;;
        522) info '522: Cloudflare could not establish a timely origin connection. Check routes, packet filtering and origin load.' ;;
        523) info '523: Cloudflare cannot reach the origin. Verify the origin DNS address and network routing.' ;;
        524) info '524: Cloudflare connected, but the origin response exceeded its timeout. Inspect application work and streaming design.' ;;
        525) info '525: Cloudflare-to-origin TLS handshake failed. Check TLS listener, certificate and supported protocols.' ;;
        526) info '526: Cloudflare rejected the origin certificate. Check hostname coverage, chain and expiration in Full (strict) mode.' ;;
        *) info "HTTP status: $1" ;;
    esac
}
header_diagnostics() {
    local url=$1
    info 'Response header names, repetition and byte sizes only; cookies and all values are suppressed.'
    { curl --noproxy '*' -sD - -o /dev/null --connect-timeout 4 --max-time 10 "$url" || :; } | helper header-summary
    info 'An oversized-header error proves upstream response-header buffering failed. Supabase SSR cookies and repeated Set-Cookie headers are possible causes; the error alone does not identify the offending header.'
    info 'Use the auth (32k) or large (64k) profile after measuring. proxy_buffer_size still applies with proxy_buffering off.'
}
site_error_summary() {
    local domain=$1
    if command -v journalctl >/dev/null; then
        journalctl -u "$NGINX_SERVICE" --no-pager -n 500 -o cat 2>/dev/null | \
            python3 -c 'import sys; domain=sys.argv[1]; print("".join(x for x in sys.stdin if domain in x))' "$domain" | helper log-summary
    else
        warn 'journalctl unavailable; inspect error_log paths locally. Do not paste cookies, query strings or authorization values.'
    fi
}
health_site() {
    local domain=$1 status headers url
    validate_domain "$domain" || return
    discover_nginx || return
    if [[ -f $STATE_DIR/profiles/$domain.conf ]]; then
        load_profile "$STATE_DIR/profiles/$domain.conf" || return
    else
        SITE=([domain]=$domain [origin_ip]='' [app]=static [api]=false)
    fi
    info "Health for $domain"
    if command -v getenforce >/dev/null 2>&1 && [[ $(getenforce 2>/dev/null) == Enforcing ]]; then
        info 'SELinux is enforcing: check web-root labels and httpd network permissions if Nginx requests fail; host policy is unchanged.'
    fi
    info 'Public resolver results:'
    { dig +time=3 +tries=1 +short A "$domain"; dig +time=3 +tries=1 +short AAAA "$domain"; } | \
        python3 -c 'import ipaddress,sys
for line in sys.stdin:
 try: print(ipaddress.ip_address(line.strip()))
 except ValueError: pass'
    if [[ -n ${SITE[origin_ip]} ]]; then
        if check_dns; then info 'DNS matches the configured origin.'
        else warn 'DNS differs from the origin. Cloudflare proxying can explain this; stale A/AAAA records can also cause failures.'; fi
    fi
    if systemctl is-active --quiet "$NGINX_SERVICE"; then info 'Nginx service: active'; else warn 'Nginx service is not active.'; fi
    if nginx_run -t; then info 'Nginx syntax: valid'; else warn 'Nginx syntax: invalid'; fi
    check_upstreams || :
    for url in "http://$domain/" "https://$domain/"; do
        status=$(curl --noproxy '*' -s -o /dev/null --connect-timeout 5 --max-time 12 -w '%{http_code}' "$url") || status=000
        info "$url"
        explain_status "$status"
    done
    headers=$(curl --noproxy '*' -sI --connect-timeout 4 --max-time 8 "https://$domain/") || headers=''
    if [[ ${headers,,} == *$'\ncf-ray:'* || ${headers,,} == *'server: cloudflare'* ]]; then
        info 'Public response indicates Cloudflare proxying (inferred from response headers).'
    fi
    CERT='' KEY=''
    if find_certificate; then
        openssl x509 -in "$CERT" -noout -enddate
        openssl x509 -in "$CERT" -noout -checkend 2592000 >/dev/null || warn 'Certificate expires within 30 days.'
    else warn 'No locally valid Certbot certificate covering this hostname was found.'; fi
    if [[ ! ${SITE[app]} =~ ^(react|angular|static|custom)$ ]]; then
        header_diagnostics "$(upstream_url "${SITE[host]}" "${SITE[port]}" "${SITE[protocol]}")"
    fi
    site_error_summary "$domain"
}
