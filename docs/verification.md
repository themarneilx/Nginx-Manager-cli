# Verification — 2026-10-19

The original Arch implementation was pushed first as commit `7dd08ea`, with 40 tests
passing and one real-Nginx test skipped because that binary was initially unavailable.

The portability update was verified locally with:

```bash
PATH=/tmp/nginx-manager-test-tools/shellcheck-v0.11.0:/tmp/nginx-manager-test-tools/nginx-1.28.0/objs:$PATH ./tests/run.sh
```

- Bash syntax: passed for every .sh file.
- ShellCheck 0.10.0 and 0.11.0: passed. Narrow annotations identify globals shared by sourced modules.
- Automated suite: **58 tests passed, no skipped tests**.
- Actual Nginx 1.28.0: generated reverse proxy, static/API, streaming/WebSocket, verified
  HTTPS upstream and IPv6/TLS redirect variants passed `nginx -t` in temporary fixtures.
  Fixtures remap listeners to unprivileged loopback ports; no server is started.
- OS detection fixtures: Arch/Manjaro, Debian/Ubuntu/Mint, Fedora, Rocky/AlmaLinux,
  ordered ID_LIKE inheritance, missing-file fallback and unsupported Alpine.
- Dependency tests: native package selection, decline/dry-run refusal and no repository additions.
- Portability regressions: dynamic CA selection, invalid trust bundles, installed renewal
  timer discovery, inactive static timers, explicit competing-timer refusal, dynamic reload
  hook executables and Debian-style symlink include backups.
- The real Nginx check exposed an existing unexpanded IPV6 template marker; the renderer
  and regression tests now cover numeric placeholder names.
- Independent review findings were reproduced and fixed with passing regressions.

The suite also covers the original input validation, profile parsing, wildcard conflicts,
idempotency, transaction/rollback/recovery, manual edits, HTTPS bootstrap, issuer failures,
Cloudflare record conflicts/proxy restoration/range refresh and secret-redacted diagnostics.

GitHub Actions includes Arch, Debian, Ubuntu, Fedora and Rocky Linux container jobs.
The initial matrix passed Debian/Ubuntu/Rocky and exposed a stricter ShellCheck 0.11
associative-array inference warning on Arch/Fedora; an explicit declaration fixes it,
and the full local suite also passes with ShellCheck 0.11.
See the repository's Actions page for the result of each published commit. These jobs
check real Nginx syntax plus mocked package/service/ACME/API orchestration; they do not
represent booted-systemd production deployments on every distribution.

Nginx and ShellCheck were downloaded/built only under `/tmp`; no host packages were
installed. No production Nginx configuration, systemd service, DNS record, CA account or
certificate store was changed. Live HTTP-01 reachability, renewal scheduling, Cloudflare
permissions and SELinux/AppArmor policy still need checking on the target server.
