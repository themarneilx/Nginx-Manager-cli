# Example walkthroughs (no production changes)

## Reference configuration analysis

The supplied `../../nginx.conf` has a single `http` block containing:

| Pattern | Reference |
| --- | --- |
| Static SPA plus preserved API prefix | Library frontend → local API port 3000 |
| Angular plus API | Hotel frontend → local API port 3001 |
| Generic/Next.js proxy | mc host → local port 3003 |
| Auth-oriented response buffering | Pomoda → local port 3107, 32k / eight buffers / 64k busy |
| Certbot TLS and separate HTTP redirects | Four HTTPS deployments, plus hotel www alias |
| Private Tor proxy | 127.0.0.1:8080 → 127.0.0.1:6900 with IP scrubbing |

There is a module include at main scope and MIME include inside http, but no per-site include.
The manager introduces its own http-context include when needed. It never copies the reference
to the live server. A live `nginx -T` is authoritative. The Tor listener and its header scrubbing
remain untouched. Its unconditional Connection upgrade could be reviewed separately by the
operator; it is deliberately excluded from this tool's changes.

New proxy hosts use conditional WebSocket headers, bounded response-header buffers even for
streaming, rebuilt forwarding headers and verified HTTPS upstreams. Existing Certbot options
and DH parameters are left intact; new hosts do not duplicate global Certbot declarations.
`worker_processes auto`, home-directory root permissions and trusted real-IP settings are
potential separate improvements to assess against the actual server.

## Pomoda: reference only

If the live server matches the reference, entering `pomoda.marneilx.org` in Add finds the
existing `server_name` and offers to list sources. It does not create another server block,
request a certificate, alter Cloudflare, or claim ownership. Use `list` or
`health pomoda.marneilx.org` to inspect the live deployment.

For a **disposable staging host without that manual vhost**, the equivalent wizard answers are:

```text
Domain: pomoda.marneilx.org
Hosting: This server
Public DNS origin IP: enter the actual public address of this staging Nginx host
Confirm that IP is the DNS destination: yes
Application: Next.js
Upstream: 127.0.0.1
Port: 3107
Protocol: HTTP (loopback)
Separate API: no (unless your app has one)
WebSocket: yes if required
Streaming: yes if required
Timeout: 60
Buffer profile: auth
Body limit: 10m
Cloudflare: yes
DNS configuration: API (or manual without a token)
Zone ID: actual zone ID, not an API token
Desired proxy mode: proxied
Trusted real IP: optional
HTTPS: yes
Contact email: actual operator email
```

The preview shows a new host and an initial HTTP challenge route, followed by a TLS phase.
After deployment confirmation, the API token is collected silently. The zone and existing
DNS record are verified. A live proxied record requires a separate confirmation before a
DNS-only bootstrap change. Public DNS is checked; the HTTP host is validated/reloaded; the
origin challenge is probed; the operator verifies external TCP 80. Certbot obtains or reuses
a certificate, then Nginx gets TLS and an ACME-capable HTTP redirect. Proxy activation is
separately confirmed after TLS succeeds. Select Cloudflare Full (strict) in the dashboard.

This sequence is a demonstration, not authorization to redeploy the existing Pomoda site.
No production DNS or certificate requests were made during development.

## A completely new domain

For a new hostname you control, copy `examples/example-profile.conf`, replace the example
hostname, origin IP and upstream with real values, and set HTTPS/email/Cloudflare fields as
needed. Do not publish the reserved example.com/example.net domains.

A deterministic local demonstration needs no DNS, root, Nginx or running application:

```bash
./nginx-manager.sh render --profile examples/example-profile.conf
```

It prints an `app.example.com` reverse proxy to `127.0.0.1:8080`, conditional WebSockets,
streaming with 32k header buffers and an ACME route, without writing configuration.

```bash
./nginx-manager.sh render --profile examples/static-api.conf
```

This prints a React SPA root at `/srv/www/example`, `index.html` fallback, and `/api/` proxied
to `127.0.0.1:5000` while retaining the URI prefix. Rendering does not require the example
root to exist; actual deployment validates its files and worker permissions.

For a real new application, use `deploy --profile FILE --dry-run` on the actual Nginx host
before running without `--dry-run`. Review all paths, generated directives and external
DNS/HTTPS steps. Repeating an identical managed deployment does not reload Nginx or request
another certificate. Updates use `update`, never an accidental duplicate Add.
