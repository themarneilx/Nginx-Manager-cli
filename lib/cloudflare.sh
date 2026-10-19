#!/usr/bin/env bash
# shellcheck shell=bash
cf_credentials() {
    set +x
    if [[ -n ${CF_API_TOKEN_FILE:-} ]]; then
        CF_TOKEN=$(python3 - "$CF_API_TOKEN_FILE" <<'PY'
import os,pathlib,sys
p=pathlib.Path(sys.argv[1])
s=p.stat()
if p.is_symlink() or not p.is_file() or s.st_uid != os.geteuid() or s.st_mode & 0o077:
    raise SystemExit('Token file must be private (600) and owned by the invoking user')
print(p.read_text().strip())
PY
        ) || return
    elif [[ -n ${CF_API_TOKEN:-} ]]; then
        CF_TOKEN=$CF_API_TOKEN
    elif [[ -z ${CF_TOKEN:-} ]]; then
        printf 'Cloudflare scoped API token (hidden): ' >&2
        IFS= read -rs CF_TOKEN || return
        printf '\n' >&2
    fi
    [[ $CF_TOKEN =~ ^[a-zA-Z0-9_-]{8,256}$ ]] || { unset CF_TOKEN; fail 'Invalid API token format.'; return 1; }
}
cf_request() {
    set +x
    local method=$1 endpoint=$2 payload=${3:-} response
    [[ $endpoint =~ ^/zones/[a-f0-9]{32}(/dns_records(/[a-f0-9]{32})?)?(\?name=[a-zA-Z0-9.-]+\&per_page=100)?$ ]] || {
        fail 'Unsafe Cloudflare endpoint.'; return 1;
    }
    response=$(printf 'header = "Authorization: Bearer %s"\n' "$CF_TOKEN" | \
        curl --noproxy '*' --config - -sS --fail --connect-timeout 5 --max-time 30 \
        --request "$method" --header 'Content-Type: application/json' \
        --data "$payload" "https://api.cloudflare.com/client/v4$endpoint") || {
            fail 'Cloudflare request failed; token and raw response are suppressed.'; return 1;
        }
    python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("success") is True, "Cloudflare API rejected request (details suppressed)"; print(json.dumps(d))' <<< "$response" 2>/dev/null || {
        fail 'Cloudflare API rejected request or returned malformed JSON. Verify token scopes and zone.'; return 1;
    }
}
cf_verify_zone() {
    [[ ${SITE[zone_id]} =~ ^[a-f0-9]{32}$ ]] || { fail 'A valid zone ID is required.'; return 1; }
    local response
    response=$(cf_request GET "/zones/${SITE[zone_id]}") || return
    python3 -c 'import json,sys; d=json.load(sys.stdin)["result"]; n=sys.argv[1]; z=d["name"]; assert n==z or n.endswith("."+z), "Domain is outside this zone"' "${SITE[domain]}" <<< "$response" || return
}
cf_set_record() {
    local type=$1 content=$2 proxied=$3 response record payload id current method endpoint
    cf_credentials || return
    cf_verify_zone || return
    response=$(cf_request GET "/zones/${SITE[zone_id]}/dns_records?name=${SITE[domain]}&per_page=100") || return
    record=$(python3 -c '
import json,sys
r=json.load(sys.stdin); items=r["result"]
if len(items)>1 or r.get("result_info",{}).get("total_count",len(items))>1:
    raise SystemExit("DNS conflict: duplicate or multiple records; resolve explicitly before continuing")
if items and items[0]["type"] != sys.argv[1]:
    raise SystemExit("DNS conflict: existing record has another type (including CNAME); no automatic replacement")
print(json.dumps(items[0] if items else {}))
' "$type" <<< "$response") || return
    payload=$(python3 -c 'import json,sys; print(json.dumps(dict(type=sys.argv[1],name=sys.argv[2],content=sys.argv[3],proxied=sys.argv[4]=="true",ttl=1)))' "$type" "${SITE[domain]}" "$content" "$proxied") || return
    current=$(python3 -c 'import json,sys; r=json.loads(sys.argv[1]); p=json.loads(sys.argv[2]); print("same" if all(r.get(k)==p[k] for k in ["type","name","content","proxied"]) else "different")' "$record" "$payload") || return
    [[ $current != same ]] || { info 'Cloudflare record already matches; no DNS change.'; return 0; }
    printf 'Cloudflare DNS proposal: %s %s -> %s, proxied=%s, TTL=automatic\n' "$type" "${SITE[domain]}" "$content" "$proxied"
    if [[ $record != '{}' ]]; then
        info "Existing DNS record (nonsecret): $record"
        warn 'Changing a live proxy mode can expose the origin IP or alter traffic routing during DNS propagation.'
    fi
    [[ $DRY_RUN != true ]] || { info 'Dry-run: DNS record is unchanged.'; return 0; }
    confirm 'Apply this DNS record change?' || return 1
    id=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("id",""))' <<< "$record")
    method=POST endpoint="/zones/${SITE[zone_id]}/dns_records"
    if [[ -n $id ]]; then
        [[ $id =~ ^[a-f0-9]{32}$ ]] || { fail 'Invalid record ID returned by Cloudflare.'; return 1; }
        method=PUT endpoint="$endpoint/$id"
    fi
    response=$(cf_request "$method" "$endpoint" "$payload") || return
    # A DNS mutation is external and intentionally survives a later Nginx rollback.
    printf '%s\n' "$payload" | transaction_dns_receipt
    info 'Cloudflare DNS change succeeded. Allow propagation before validation.'
}
transaction_dns_receipt() {
    local payload
    payload=$(cat)
    secure_state || return
    local file
    file=$(mktemp "$STATE_DIR/transactions/dns-change-XXXXXXXX.json") || return
    printf '%s\n' "$payload" > "$file"
    chmod 600 "$file"
    info "DNS change receipt: $file"
}
cf_set_origin_record() {
    local type=A
    [[ ${SITE[origin_ip]} != *:* ]] || type=AAAA
    [[ -n ${SITE[origin_ip]} ]] || { fail 'DNS origin IP is required.'; return 1; }
    cf_set_record "$type" "${SITE[origin_ip]}" "$1"
}
configure_dns() {
    if [[ ${SITE[cloudflare]} == api ]]; then
        if [[ ${SITE[https]} == true ]] && ! find_certificate; then
            info 'Initial HTTP-01 validation uses DNS-only. Desired proxy mode can be enabled after HTTPS succeeds.'
            cf_set_origin_record false || return
        else
            cf_set_origin_record "${SITE[proxy_mode]}" || return
        fi
    else
        info "Manual Cloudflare DNS: name=${SITE[domain]}, content=${SITE[origin_ip]}, type=$([[ ${SITE[origin_ip]} == *:* ]] && printf AAAA || printf A), TTL=Auto."
        info 'For initial HTTP-01 choose DNS only (gray cloud). Use Full (strict) after the origin certificate is installed.'
        confirm 'Have you saved the required DNS records in the dashboard?' || return 1
    fi
    if [[ ${SITE[https]} == true ]] && ! find_certificate; then
        while ! check_dns; do
            warn 'Public DNS does not yet resolve exclusively to the configured origin. Check A/AAAA records and propagation.'
            confirm 'Retry DNS verification?' || return 1
        done
    fi
}
fetch_cf_cidrs() {
    local ipv4 ipv6
    ipv4=$(curl --noproxy '*' -fsS --connect-timeout 5 --max-time 15 https://www.cloudflare.com/ips-v4) || return
    ipv6=$(curl --noproxy '*' -fsS --connect-timeout 5 --max-time 15 https://www.cloudflare.com/ips-v6) || return
    CF_CIDRS=$(python3 -c '
import ipaddress,sys
lines=sys.stdin.read().split(); nets=[ipaddress.ip_network(x,strict=True) for x in lines]
assert len(nets)>=2 and any(n.version==4 for n in nets) and any(n.version==6 for n in nets)
assert all(n.is_global and n.prefixlen >= (8 if n.version==4 else 16) for n in nets)
print(",".join(str(n) for n in nets))' <<< "$ipv4 $ipv6") || { fail 'Invalid trusted-proxy CIDR response.'; return 1; }
}
cloudflare_menu() {
    local domain type content proxied
    prompt domain 'DNS record domain' ''
    validate_domain "$domain" || return
    SITE=([domain]=$domain)
    prompt 'SITE[zone_id]' 'Cloudflare zone ID' ''
    prompt type 'Record type (A, AAAA, CNAME)' A
    prompt content 'Record content (origin IP or CNAME target)' ''
    python3 - "$MANAGER_DIR/lib" "$type" "$content" <<'PY'
import ipaddress,sys
sys.path.insert(0,sys.argv[1]); from config import domain
kind,value=sys.argv[2:]
if kind=='CNAME': assert domain(value), 'Invalid CNAME target'
else:
    assert kind in {'A','AAAA'}, 'Invalid type'
    ip=ipaddress.ip_address(value); assert ip.is_global and ip.version==(4 if kind=='A' else 6), 'Invalid IP'
PY
    [[ $? == 0 ]] || return 1
    proxied=false
    if confirm 'Proxy through Cloudflare?'; then proxied=true; fi
    if [[ $DRY_RUN != true ]]; then acquire_lock || return; fi
    cf_set_record "$type" "$content" "$proxied"
}
