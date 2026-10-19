#!/usr/bin/env bash
# shellcheck shell=bash
# OS metadata is parsed by Python; it is never sourced into the root shell.
detect_os() {
    local metadata
    metadata=$(python3 -B "$MANAGER_DIR/lib/host.py" detect "${OS_RELEASE_FILE:-}") || return
    OS_ID=$(json_field id <<< "$metadata")
    OS_NAME=$(json_field name <<< "$metadata")
    OS_VERSION=$(json_field version <<< "$metadata")
    OS_FAMILY=$(json_field family <<< "$metadata")
    PACKAGE_MANAGER=$(json_field package_manager <<< "$metadata")
    mapfile -t OS_PACKAGES < <(python3 -c 'import json,sys; print("\n".join(json.load(sys.stdin)["packages"]))' <<< "$metadata")
}
os_info() {
    detect_os || return
    printf 'OS: %s\nID: %s\nVersion: %s\nFamily: %s\nPackage manager: %s\n' \
        "$OS_NAME" "$OS_ID" "${OS_VERSION:-(rolling/unspecified)}" "$OS_FAMILY" "${PACKAGE_MANAGER:-(unsupported)}"
    local trust
    if trust=$(python3 -B "$MANAGER_DIR/lib/host.py" ca-bundle "${CA_BUNDLE:-}" 2>/dev/null); then
        printf 'CA bundle: %s\n' "$trust"
    else
        printf 'CA bundle: unavailable (install ca-certificates or select --ca-bundle)\n'
    fi
    printf 'Service requirement: running systemd\n'
    if [[ $OS_FAMILY == rhel ]]; then
        info 'Certbot availability depends on configured repositories (often EPEL). No repository is added automatically.'
    elif [[ $OS_FAMILY == unsupported ]]; then
        info 'Automatic package installation is unavailable for this OS; service operations still require systemd.'
    fi
}
install_dependencies() {
    detect_os || return
    [[ -n $PACKAGE_MANAGER ]] || { fail "Automatic installation is unsupported on $OS_NAME; install the prerequisites manually."; return 1; }
    if [[ $OS_FAMILY == rhel ]] && ! command -v dnf >/dev/null 2>&1 && command -v yum >/dev/null 2>&1; then
        PACKAGE_MANAGER=yum
    fi
    local -a command_line=("$PACKAGE_MANAGER")
    case "$PACKAGE_MANAGER" in
        pacman) command_line+=(-S --needed) ;;
        apt-get|dnf|yum) command_line+=(install) ;;
        *) fail 'Unsupported package manager'; return 1 ;;
    esac
    command_line+=("${OS_PACKAGES[@]}")
    info "Detected OS: $OS_NAME ($OS_FAMILY)"
    printf 'Package command: '; printf '%q ' "${command_line[@]}"; printf '\n'
    info 'This installs dependencies from existing repositories; no OS upgrade, repository addition or package-database refresh runs.'
    info 'Native package-maintainer scripts may run service actions; review the package manager prompts.'
    if [[ $OS_FAMILY == rhel ]]; then
        warn 'Certbot may require EPEL on this distribution. Configure the appropriate repository yourself if the package is unavailable.'
    fi
    [[ $DRY_RUN != true ]] || return 0
    require_root || return
    command -v "$PACKAGE_MANAGER" >/dev/null || { fail "$PACKAGE_MANAGER is unavailable; verify the detected OS or install dependencies manually."; return 1; }
    confirm 'Install these packages?' || return 1
    "${command_line[@]}" || { fail 'Package installation failed. Check repositories and package availability; no other installer will be attempted.'; return 1; }
}

configure_renewal_timer() {
    local listing unit state selected='' disabled='' requested=${RENEWAL_TIMER:-} requested_loaded=false
    local -a candidates=() scheduled=()
    local -A seen=()
    listing=$(systemctl list-unit-files --type=timer --no-legend --no-pager 'certbot*.timer' 'snap.certbot*.timer' 2>/dev/null) || listing=''
    while read -r unit _; do
        [[ $unit =~ ^[A-Za-z0-9@_.-]+\.timer$ && $unit != -* ]] || continue
        candidates+=("$unit")
    done <<< "$listing"
    [[ -z $requested ]] || candidates=("$requested" "${candidates[@]}")
    candidates+=(certbot.timer certbot-renew.timer certbot-renewal.timer snap.certbot.renew.timer)
    for unit in "${candidates[@]}"; do
        [[ $unit =~ ^[A-Za-z0-9@_.-]+\.timer$ && $unit != -* ]] || { fail 'Invalid renewal timer name.'; return 1; }
        [[ -z ${seen[$unit]:-} ]] || continue
        seen[$unit]=true
        state=$(systemctl show --property=LoadState --value "$unit" 2>/dev/null) || continue
        [[ $state == loaded ]] || continue
        if [[ $unit == "$requested" ]]; then requested_loaded=true; fi
        state=$(systemctl show --property=UnitFileState --value "$unit" 2>/dev/null) || state=''
        if [[ $state == enabled || $state == enabled-runtime ]] || systemctl is-active --quiet "$unit" >/dev/null 2>&1; then
            scheduled+=("$unit")
            if [[ $unit == "$requested" || -z $selected ]]; then selected=$unit; fi
        elif [[ $state == disabled && ( -z $requested || $unit == "$requested" ) && -z $disabled ]]; then
            disabled=$unit
        fi
    done
    if [[ -n $requested && $requested_loaded != true ]]; then
        fail "Requested renewal timer $requested is not loaded; no unit will be enabled."
        return 1
    fi
    if [[ -n $selected ]]; then
        if [[ -n $requested && $selected != "$requested" ]]; then
            fail "Another Certbot scheduler is enabled or active: $selected. No second timer will be enabled; review the existing scheduler manually."
            return 1
        fi
        info "Existing Certbot renewal timer: $selected (enabled or active); scheduler unchanged."
        if ((${#scheduled[@]} > 1)); then warn 'Multiple renewal timers already exist; review them manually. No scheduler was added or disabled.'; fi
        return 0
    fi
    if [[ -z $disabled ]]; then
        if [[ -n $requested ]]; then
            fail "Requested timer $requested is inactive and not enableable as a disabled unit; configure renewal manually."
            return 1
        fi
        warn 'No installed, enableable Certbot renewal timer was detected. Check existing cron/snap scheduling or configure renewal manually; no timer was guessed or created.'
        return 0
    fi
    info "Installed Certbot renewal timer: $disabled (currently disabled)."
    [[ $DRY_RUN != true ]] || { info "Dry-run: would offer to enable $disabled."; return 0; }
    if confirm "Enable and start $disabled?"; then
        systemctl enable --now "$disabled" || return
    else
        warn 'Renewal timer unchanged. Verify your existing scheduler or configure renewal manually.'
    fi
}
