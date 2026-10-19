#!/usr/bin/env bash
# shellcheck shell=bash
validate_paths() {
    if [[ ${SITE[app]} =~ ^(react|angular|static)$ ]]; then
        [[ -d ${SITE[root]} && -r ${SITE[root]}/index.html ]] || { fail 'Web root must contain readable index.html.'; return 1; }
        local version worker runner directory
        version=$("$NGINX_BIN" -V 2>&1) || return
        worker=$(helper worker "$NGINX_CONF" "$version" <<< "$NGINX_DUMP") || return
        runner=$(command -v runuser) || { fail 'runuser is required to verify Nginx worker permissions.'; return 1; }
        if ! env -i PATH=/usr/bin:/bin "$runner" -u "$worker" -- /usr/bin/test -r "${SITE[root]}/index.html" 2>/dev/null; then
            fail "Nginx worker $worker cannot read index.html. Check namei -l and permissions; no chmod is performed."
            return 1
        fi
        directory=${SITE[root]}
        while true; do
            env -i PATH=/usr/bin:/bin "$runner" -u "$worker" -- /usr/bin/test -x "$directory" 2>/dev/null || {
                fail "Nginx worker $worker cannot traverse $directory"; return 1;
            }
            [[ $directory != / ]] || break
            directory=$(dirname "$directory")
        done
        info "Nginx worker $worker can read index.html and traverse its parent directories."
    fi
    if [[ ${SITE[app]} == custom ]]; then
        [[ -f ${SITE[custom_file]} && -r ${SITE[custom_file]} ]] || { fail 'Custom config is unreadable.'; return 1; }
    fi
}
check_dns() {
    local v4 v6 addresses
    v4=$(dig +time=3 +tries=1 +short A "${SITE[domain]}" 2>/dev/null) || return 1
    v6=$(dig +time=3 +tries=1 +short AAAA "${SITE[domain]}" 2>/dev/null) || return 1
    addresses=$(printf '%s\n%s\n' "$v4" "$v6")
    python3 - "${SITE[origin_ip]}" "$addresses" <<'PY'
import ipaddress,sys
try:
    expected=ipaddress.ip_address(sys.argv[1])
except ValueError:
    raise SystemExit(1)
ips=[]
for line in sys.argv[2].splitlines():
    try: ips.append(ipaddress.ip_address(line))
    except ValueError: pass
# Every published address must reach this configured origin for reliable HTTP-01.
raise SystemExit(0 if ips and all(ip == expected for ip in ips) else 1)
PY
}
upstream_url() {
    local host=$1 port=$2 protocol=$3
    [[ $host != *:* ]] || host="[$host]"
    printf '%s://%s:%s/' "$protocol" "$host" "$port"
}
check_upstreams() {
    local url status
    if [[ ! ${SITE[app]} =~ ^(react|angular|static|custom)$ ]]; then
        url=$(upstream_url "${SITE[host]}" "${SITE[port]}" "${SITE[protocol]}")
        status=$(curl --noproxy '*' -s -o /dev/null --connect-timeout 3 --max-time 8 -w '%{http_code}' "$url") || {
            warn "Upstream unavailable at $url. Keep application ports private; check the local listener or private network."
            return 1
        }
        info "Upstream HTTP status: $status"
    fi
    if [[ ${SITE[api]} == true ]]; then
        url=$(upstream_url "${SITE[api_host]}" "${SITE[api_port]}" "${SITE[api_protocol]}")
        curl --noproxy '*' -s -o /dev/null --connect-timeout 3 --max-time 8 "$url" || { warn 'API upstream unavailable.'; return 1; }
    fi
}
