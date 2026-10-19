# OS autodetection and Linux portability

Preserve the pushed Arch implementation as commit 7dd08ea. Extend the existing Bash modules
with a host adapter, retaining the same managed files, JSON profiles and transaction logic.

Support Arch-family pacman, Debian/Ubuntu apt-get, Fedora dnf, and RHEL-family dnf/yum.
Read /etc/os-release (fallback /usr/lib/os-release) as data, using ID and then ID_LIKE;
never source it or evaluate its contents. Unknown distros remain inspectable but package
installation refuses to guess. systemd, Bash 4.4+ and Python 3.9+ remain prerequisites;
OpenRC, runit and BSD are detected/reported as outside the service-management scope.

Select and preview distro package names before the existing explicit installation prompt.
Never add EPEL/repositories automatically, upgrade the OS, change firewall rules, disable
SELinux/AppArmor, or start/restart applications as part of portability detection.

Discover the CA bundle from the system/OpenSSL defaults, with a validated --ca-bundle
option for private CAs. Use it for verified HTTPS upstreams and certificate validation.
Detect installed Certbot timers by LoadState, prefer an already-enabled one and ask before
enabling an installed disabled timer. Never enable a guessed unit or duplicate schedulers.
Build renewal hooks using the detected systemctl executable.

Expose os-info and show the detected OS in the menu. Keep render/os-info non-root/read-only,
including on unsupported distro families. Existing Nginx service/config/prefix discovery
stays authoritative. Account for SELinux/AppArmor constraints in actionable diagnostics
without altering host policy.

Execution plan:
1. Add failing table-driven release parsing, package mapping and installer-boundary tests.
2. Implement lib/host.py and lib/os.sh, integrate OS reporting and dependency selection.
3. Add failing CA/timer/hook tests; implement validated trust and renewal discovery.
4. Update CLI/README and add a distro CI matrix with actual Nginx syntax checks.
5. Run syntax, ShellCheck and the full test suite; review; commit and push main.

Tests use os-release fixtures, mocked package managers/systemctl, temporary trust bundles,
and existing transaction mocks. Never install packages or reload production Nginx locally.

Completed local verification: 58 tests passed with real Nginx syntax checks and ShellCheck.
Review fixes distinguish enabled timers from static/indirect units and prevent an explicit
selection from adding a second discovered scheduler. Native dependency installation remains
an operator-confirmed action; no repositories or host security policies are modified.
