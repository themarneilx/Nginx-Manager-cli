# Local verification — 2026-10-19

Command: `./tests/run.sh`

- Bash syntax: passed for every .sh file.
- Automated suite: 41 tests, 40 passed, 1 skipped.
- Skipped test: real-Nginx syntax checks; no Nginx binary installed in this workspace.
- ShellCheck: unavailable; the test runner reports its absence explicitly.
- Example proxy and static/API profiles: rendered and lexically parsed successfully.
- Supplied nginx.conf: parser simulation inserts only the new http-context include
  (apart from normalizing trailing blank lines); existing Pomoda is detected as a conflict;
  the Tor loopback listener and the rest of the configuration body remain intact.
- Independent safety review: important findings received, fixed, and covered by regression
  tests. Follow-up caught attached service options and bytecode writes during discovery;
  both have dedicated passing tests.

The suite exercises input injection/type errors, profiles as data, missing certificate
rejection, repeated headers without secret values, Nginx quoting/comments/context, wildcard
conflicts, idempotency, decline/dry-run, manually edited configs, enabled/disabled removal
protection, backup/restore, recovery from invalid config, concurrent-edit rollback protection,
validation/reload/issuer failures, TLS bootstrap, trust validation, API-token argv privacy,
Cloudflare CNAME conflicts/proxy restoration/range refresh, IPv6 listeners, worker permission
checks, ACME symlinks/umask behavior and matching systemd configuration arguments.

Nginx, systemd reloads, Certbot issuance and Cloudflare HTTP requests are mocked at external
boundaries. OpenSSL generates disposable test certificates. No production configuration,
DNS, CA accounts or services were changed. Real Nginx syntax, reload behavior, live DNS,
Cloudflare permissions and external HTTP-01 connectivity still require a disposable
hostname on an actual Arch staging host. Test-tool downloads were unavailable under the
workspace network restriction; access to the local Docker socket was also unavailable.
