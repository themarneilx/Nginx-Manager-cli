# Nginx deployment manager for Arch Linux

Interactive Bash management of new Nginx virtual hosts, static/SPA frontends, API routes,
local or remote proxies, Let's Encrypt HTTPS, Cloudflare DNS, backups and diagnostics.
No settings in Bash source need editing for another domain or VPS.

**Development verification is isolated.** This project has not been deployed to your real
server. Run the tests, review the preview, and deploy a disposable hostname on your own
Arch staging host before using production. Real Nginx/Certbot/Cloudflare behavior requires
that staging verification; mocked integration tests cannot establish production readiness.

## Install and launch

Copy this entire directory to a trusted, root-owned location on the Nginx host. Do not run
root code from a directory other users can modify. Bash 4.4+ and Python 3 are required.

```bash
sudo pacman -S --needed bash python
sudo ./nginx-manager.sh dependencies
sudo ./nginx-manager.sh
```

The dependency command asks before installing nginx, certbot, certbot-nginx, curl,
openssl, bind (dig), python and util-linux (flock) with pacman. It never refreshes package
databases in isolation. Perform normal Arch system upgrades separately. systemd must
already be available. No services or firewall rules are automatically started or opened
by installing dependencies. The menu expects Nginx to be active and valid before deployment.

Optional development packages: shellcheck and nginx for local syntax verification.
No npm, desktop, cloud SDK or Python packages are required.

## Menu and workflows

Run with no arguments for the SSH-friendly menu. Add asks for a domain, hosting machine,
DNS origin IP, application type, upstream or web root, API service, WebSockets, streaming,
timeouts, buffers, body limits, Cloudflare and HTTPS. IP detection uses api.ipify.org and
is advisory. Origin IP, upstream address and the machine running this tool are separate.
Remote applications are not administered over SSH and their ports are never opened.

Next.js, Node, API and generic proxy choices use the same safe proxy building blocks.
React/Angular builds use index.html fallback; static HTML defaults to real files/404.
API prefixes keep their URI prefix upstream: `/api/item` stays `/api/item`.
Static assets must already exist. The tool verifies index.html readability and directory traversal
as the configured Nginx worker user (using runuser), and does not build apps or alter ownership.
Use `namei -l /absolute/web/root/index.html` to check every parent directory's traversal
permission for your Nginx worker user. The final HTTP health check is authoritative.

Every deployment previews files and generated Nginx content, then asks for confirmation.
DNS, issuance, renewal hooks and restoring backups have their own prompts. There is no
unattended `--yes` override. A declined operation returns exit 1 without applying its
proposed change. Earlier separately confirmed external actions can still remain.

```bash
./nginx-manager.sh --help
./nginx-manager.sh render --profile examples/example-profile.conf
sudo ./nginx-manager.sh deploy --profile /root/profiles/my-app.conf --dry-run
sudo ./nginx-manager.sh deploy --profile /root/profiles/my-app.conf
sudo ./nginx-manager.sh update --profile /root/profiles/my-app.conf
sudo ./nginx-manager.sh health app.example.com
sudo ./nginx-manager.sh disable app.example.com
sudo ./nginx-manager.sh enable app.example.com
sudo ./nginx-manager.sh remove app.example.com
```

Removal shows the exact managed files. It does not delete DNS records or certificates.
Manual server blocks are never adopted or overwritten. Existing domains can be inspected
via `list` and their loaded source paths; use an independently reviewed migration to
move an existing manual deployment to manager ownership. Wildcards and regex names are
conservatively treated as conflicts. A regex host can require manual review even if its
pattern would not actually match the requested hostname.

## Profiles and configuration

Profiles use **JSON data**, including the `.conf` examples. Never source them as shell code.
Only known, nonsecret fields are accepted. Defaults are filled during validation.

| Field | Meaning/default |
| --- | --- |
| domain | Required lowercase ASCII domain; use punycode for IDNs |
| origin_ip | Public DNS destination; required for new HTTPS issuance |
| hosting | local or remote |
| app | nextjs, node, react, angular, static, api, proxy, custom |
| host / port / protocol | Nginx upstream, 127.0.0.1 / 8080 / http |
| root / spa | Static directory / optional HTML fallback |
| api / api_prefix | Separate API enabled / /api/ |
| api_host / api_port / api_protocol | API upstream, 127.0.0.1 / 3000 / http |
| websocket / streaming | Conditional upgrades / response body buffering off |
| buffer | standard=8k, auth=32k (default), large=64k |
| timeout / body_size | Read/send timeout 60 seconds / 10m request limit |
| https / email | HTTPS desired / Let's Encrypt contact email |
| cloudflare | none, manual, api |
| zone_id / proxy_mode | Nonsecret zone ID / desired proxied boolean |
| real_ip | Fetch and trust only official Cloudflare CIDRs for this server |
| custom_file | Optional reviewed server-body/location fragment |
| dns_confirmed | Nonsecret wizard note; never bypasses DNS verification |

No domains or real addresses are runtime constants. Examples are reserved documentation
domains. Supported paths are absolute and limited to letters, digits, slash, underscore,
dash and dot; paths with whitespace, traversal or Nginx metacharacters are rejected.
Public IPv4 and IPv6 origins and IP/hostname upstreams are supported. Generated listeners
use IPv4, and additionally IPv6 when the selected origin is IPv6. Verify IPv6 routing and
firewall rules before publishing AAAA records; the tool does not enable network interfaces.
HTTPS upstreams verify certificates using `/etc/ssl/cert.pem`, the Arch CA trust store;
private CAs need separate trust-store setup. The tool never disables TLS verification.

State defaults to `/var/lib/nginx-manager` (700); profiles and manifests are private.
Use the menu's edit operation to reload a saved profile. A successfully applied profile is
saved under `profiles/DOMAIN.conf`; copy and edit that JSON to reuse it on another host.
Custom snippets are reviewed text, restricted to locations and supported body directives;
they cannot create listeners, server names, includes, certificate directives or global
settings. Their contents are shown in the deployment preview. They are an advanced option,
not a sandbox for untrusted code. They are not automatically copied into saved profiles;
keep the referenced snippet available when updating or moving servers.

## Active Nginx discovery and safety

The manager inspects systemd ExecStart, checks that its executable matches the selected binary,
and resolves the service's `-c` and `-p` settings or compiled `--conf-path`. An explicit
`--nginx-conf` must match the service target. The same config and prefix are used for tests,
dumps and renewal hooks. `nginx -T` provides the actual loaded files. Use `--service` and
`--nginx-bin` to select another Nginx unit. Global `-g` flags that define includes/modules/
configuration blocks are refused; normalize those into the main config first. Custom service
wrappers and unusual argument encodings may require a separately reviewed integration.

The manager reuses an immediate `http` context `conf.d/*.conf` include when found; otherwise
it inserts a dedicated `nginx-manager.d/*.conf` include immediately inside the discovered
http block. Quoted text/comments and included files are parsed, not edited with a block
regex. There must be exactly one unambiguous http block. All other configuration remains
in place, including loopback Tor services. Your supplied reference nginx.conf is never
used as the live configuration and never modified by tests.

Files are replaced atomically, with private snapshots recorded before each change.
`nginx -t` must succeed before `systemctl reload nginx`. On a reload failure the previous
files are restored and a reload of the prior valid configuration is attempted. No stop
or restart commands run. Content hashes prevent automatic rollback from overwriting a
concurrent manual edit. Symlink configuration targets and symlinked parent directories
are rejected; Certbot's normal live certificate symlinks are supported for reading.
Modified manager-owned configs are refused until the operator investigates them. Disabled
sites must be explicitly enabled before updating, so updates cannot silently reactivate them.
A lock serializes manager mutations; coordinate manual changes with the operator too.

`--dry-run` does no filesystem writes, dependency installs, DNS mutations, Certbot calls
or service reloads. It can read Nginx, DNS, existing certificates and public Cloudflare
information. `render` needs neither root nor Nginx and only prints a generated host.
Do not confuse Certbot's explicitly requested `renew --dry-run` (network/staging CA and
Certbot state/log activity) with the manager's fully non-mutating `--dry-run`.

## HTTPS and renewal

An unexpired existing Certbot certificate covering the hostname and verifying against the
system trust store is reused. New issuance uses
`certbot certonly --webroot`, avoiding plugin rewrites of unrelated virtual hosts. An HTTP
host with a challenge location is installed first, the origin route is probed, then the
operator confirms external TCP 80 reachability. DNS A/AAAA must match the expected origin.
All published addresses must be valid; stale AAAA records block issuance.

On success the manager installs TLS 1.2/1.3 and a separate HTTP redirect with an ACME
exception. It never references nonexistent certificate files. The challenge webroot defaults
to `/var/lib/nginx-manager-acme`, outside private state so workers can traverse it.
The ACME location must remain available for renewals. `--acme-root` can override it.

If issuance fails, pre-bootstrap files are restored. Certbot's own account/certificate
artifacts and confirmed DNS changes are retained; repeated retries can hit CA rate limits.
If a later HTTPS activation fails, a working HTTP bootstrap may remain; inspect its profile,
fix the cause, then run `ssl DOMAIN`. Never delete certificate stores to retry.

The manager checks `certbot-renew.timer`, asks before enabling it, offers a guarded Nginx
reload deploy hook, and offers `certbot renew --dry-run`. Existing differing hooks are
not overwritten. Alternate timers/cron can be used; verify their reload behavior yourself.
Timer/hook/renewal checks that are declined are reported and require operator follow-up.

```bash
sudo ./nginx-manager.sh ssl app.example.com
sudo ./nginx-manager.sh renew
systemctl list-timers certbot-renew.timer
```

## Cloudflare credentials and DNS

Use a token restricted to the selected zone with **Zone / DNS / Edit** and **Zone / Zone /
Read** permissions. Keep the token out of profiles and command-line arguments.

```bash
# Read silently, keeping the secret out of command history.
read -rs CF_API_TOKEN
export CF_API_TOKEN
sudo --preserve-env=CF_API_TOKEN ./nginx-manager.sh cloudflare
unset CF_API_TOKEN
```

Alternatively create a root-owned mode-600 token file using a trusted editor, then pass
its path with `CF_API_TOKEN_FILE` through sudo. Do not put a token in shell command text.
Tokens can also be entered silently at the prompt. Curl reads authentication through stdin
configuration, never argv. Do not run under an external shell tracer or log terminal input.

The API verifies zone membership and existing records before proposing a write. Multiple
records, alternate types and CNAME conflicts are refused for explicit operator resolution.
Standalone DNS management supports A, AAAA and CNAME; deployment uses an origin A/AAAA.
Each create/update shows the record and asks for confirmation, including live proxy changes.
Initial HTTP-01 uses a confirmed DNS-only change; desired proxied mode is offered only
after successful origin HTTPS. A DNS change receipt is saved for recovery. The manager
does not auto-delete or auto-undo DNS records across a failed deployment.

Manual mode prints record settings, waits for dashboard confirmation, then verifies DNS
before initial issuance. After a valid certificate is installed, use **Full (strict)**.
Zone-wide SSL/TLS settings are never changed. Optional real-IP restoration trusts freshly
fetched official Cloudflare CIDRs only inside the new server; update the site periodically
to refresh ranges. Direct connections cannot override the client IP using forwarding
headers. Backends receive X-Forwarded-For rebuilt from Nginx's trusted `$remote_addr`.

## Backups and recovery

```bash
sudo ./nginx-manager.sh backup
sudo ./nginx-manager.sh restore /var/lib/nginx-manager/backups/backup-XXXXXXXX.json --dry-run
sudo ./nginx-manager.sh restore /var/lib/nginx-manager/backups/backup-XXXXXXXX.json
sudo ./nginx-manager.sh recover /var/lib/nginx-manager/transactions/transaction-XXXXXXXX.json
```

Backups contain loaded config files, owned profiles/metadata and disabled managed hosts.
They exclude certificates, credential files and Cloudflare state. Keep them private: manual
Nginx configs can contain application secrets. Restore shows exactly which files it writes,
snapshots the current files, validates, then reloads. It does not wipe whole directories or
remove unrelated sites created after the backup. Backups can only restore under the active
config tree and the manager's profile/metadata directories. A loaded include outside those
trees causes backup to refuse; copy such configurations separately under operator control.

Pending transaction manifests survive crashes. `recover` uses before/after hashes and
refuses to overwrite concurrent edits. Inspect conflicting files locally, restore the
appropriate bytes from the private manifest, run `nginx -t`, then reload. SIGINT/SIGTERM and
ordinary failures attempt rollback; SIGKILL/power loss requires explicit recovery. Always
investigate an incomplete rollback before making another configuration change.

## Troubleshooting

`health DOMAIN` checks public DNS/origin mismatch, Nginx status/syntax, upstream connectivity,
HTTP/HTTPS, Cloudflare response indicators, local certificate expiry and a redacted journal
summary. It diagnoses 502 and Cloudflare 521/522/523/524/525/526 by network/application/TLS
layer. It does not print raw request paths, tokens, cookies, redirect URLs or header values.
Generated error logs go to stderr (usually the Nginx systemd journal); access logging is
disabled for generated hosts to avoid collecting authenticated request query strings.
Existing sites retain their logging configuration; the journal summary will not cover
separate legacy log files. Inspect those privately if necessary.

For `upstream sent too big header`, measure response header counts and sizes. The error
proves upstream response-header buffering failed, without identifying the responsible
header. Supabase SSR auth cookies and repeated Set-Cookie response headers are possible
causes. Incognito succeeding does not prove a particular cookie caused the failure.
Use `auth` (32k, eight buffers, busy 64k), then measured `large` (64k, eight, busy 128k) if
needed. Body streaming does not eliminate the response-header buffer requirement. No
unlimited buffer profile is provided. The health workflow prints names/counts/bytes only.

## Tests and demonstrations

```bash
./tests/run.sh
./nginx-manager.sh render --profile examples/example-profile.conf
./nginx-manager.sh render --profile examples/static-api.conf
```

Tests use temporary files and mocked nginx/systemctl/curl/Certbot boundaries; real OpenSSL
creates disposable test certificates. They never change real Nginx, systemd or DNS. ShellCheck
runs when installed. See [the walkthrough](docs/walkthrough.md) for Pomoda and a new-domain
example; Pomoda is a reference demonstration and is never blindly redeployed.

Implementation references: [Nginx proxy directives](https://nginx.org/en/docs/http/ngx_http_proxy_module.html),
[Certbot webroot and renewal](https://eff-certbot.readthedocs.io/en/stable/using.html),
[Cloudflare DNS API](https://developers.cloudflare.com/api/resources/dns/subresources/records/methods/list/).
