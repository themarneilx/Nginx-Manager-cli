# Example walkthroughs (no production changes)

## Common hosting patterns

A typical Nginx configuration can contain several hosting patterns inside one `http` block:

| Pattern | Reference |
| --- | --- |
| Static SPA plus preserved API prefix | Static frontend → local API port 3000 |
| Angular plus API | Angular frontend → local API port 3001 |
| Generic/Next.js proxy | Application → local port 8080 |
| Auth-oriented response buffering | Application with authentication, 32k / eight buffers / 64k busy |
| Certbot TLS and separate HTTP redirects | HTTPS websites with optional www aliases |
| Private loopback proxy | Loopback-only listener → local backend with IP scrubbing |

Module includes belong at main scope and MIME settings inside `http`. The manager discovers
the live layout with `nginx -T` and introduces an http-context per-site include when needed.
It generates configuration from your answers rather than copying a sample configuration.
Existing loopback-only services and their header settings remain untouched.

New proxy hosts use conditional WebSocket headers, bounded response-header buffers even for
streaming, rebuilt forwarding headers and verified HTTPS upstreams. Existing Certbot options
and DH parameters are left intact; new hosts do not duplicate global Certbot declarations.
`worker_processes auto`, home-directory root permissions and trusted real-IP settings are
potential separate improvements to assess against the actual server.

## Deploy app.example.com with Cloudflare and HTTPS

`app.example.com` is a documentation placeholder. Replace it with a hostname you control
before configuring real DNS or requesting a certificate.

For a new application, the wizard answers would look like this:

```text
Domain: app.example.com
Hosting: This server
Public DNS origin IP: enter the actual public address of this staging Nginx host
Confirm that IP is the DNS destination: yes
Application: Next.js
Upstream: 127.0.0.1
Port: 8080 (replace with your application's actual port)
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

### If the domain already exists

If your real hostname already has a `server_name` in the loaded configuration, Add detects
it and offers to list its configuration sources. It does not create a duplicate server
block, request a certificate, alter Cloudflare, or claim ownership. Use `list` or
`health YOUR_DOMAIN` to inspect that deployment. Manually configured websites require a
separately reviewed migration before the manager can own them.

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
