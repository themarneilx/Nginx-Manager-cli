#!/usr/bin/env bash
# shellcheck shell=bash
transaction_begin() {
    TX=$(mktemp "$STATE_DIR/transactions/transaction-XXXXXXXX.json") || return
    fs begin "$TX" || return
    trap 'transaction_exit $?' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
}
transaction_write() { fs write "$TX" "$1" "${2:-644}"; }
transaction_delete() { fs delete "$TX" "$1"; }
transaction_rollback() {
    [[ -n $TX ]] || return 0
    local manifest=$TX
    TX=''
    warn "Rolling back owned files. Recovery manifest: $manifest"
    fs rollback "$manifest" || { warn 'Rollback incomplete: inspect the manifest before any reload.'; return 1; }
}
transaction_exit() {
    local status=$1
    if [[ -n $TX ]]; then transaction_rollback || status=1; fi
    trap - EXIT
    exit "$status"
}
transaction_commit() {
    if ! nginx_run -t; then
        fail 'Nginx validation failed; no reload attempted.' || :
        transaction_rollback
        return 1
    fi
    if ! systemctl reload "$NGINX_SERVICE"; then
        warn 'Reload failed. Restoring previous files, then attempting to reload the prior valid configuration.'
        transaction_rollback || return 1
        if nginx_run -t; then
            systemctl reload "$NGINX_SERVICE" || warn 'Prior files restored; systemd still failed. Inspect service status manually.'
        fi
        return 1
    fi
    fs commit "$TX" || return
    info "Configuration applied. Recovery manifest: $TX"
    LAST_TX=$TX
    TX=''
}
backup_configuration() {
    discover_nginx || return
    info 'Backup includes loaded Nginx configs, managed profiles and metadata; certificates and credentials are excluded.'
    [[ $DRY_RUN != true ]] || { printf '%s\n' "$NGINX_DUMP" | helper files; return; }
    confirm 'Create a private configuration backup?' || return 1
    acquire_lock || return
    local destination
    destination=$(mktemp "$STATE_DIR/backups/backup-XXXXXXXX.json") || return
    {
        printf '%s\n' "$NGINX_DUMP" | helper files
        find "$STATE_DIR/profiles" "$STATE_DIR/metadata" -maxdepth 1 -type f -name '*.conf' -print
        find "$STATE_DIR/metadata" -maxdepth 1 -type f -name '*.json' -print
        [[ ! -d $SITE_DIR ]] || find "$SITE_DIR" -maxdepth 1 -type f -name '*.disabled' -print
        local metadata domain
        for metadata in "$STATE_DIR"/metadata/*.json; do
            [[ -f $metadata ]] || continue
            domain=$(basename "$metadata" .json)
            validate_domain "$domain" || return
            # Record absent counterparts too, so enabled/disabled restoration is consistent.
            printf '%s\n' "$SITE_DIR/$domain.conf" "$SITE_DIR/$domain.conf.disabled"
        done
    } | fs backup "$destination" "$(dirname "$NGINX_CONF")" "$STATE_DIR/profiles" "$STATE_DIR/metadata" || return
    info "Backup: $destination"
}
restore_backup() {
    local backup=$1
    resolve_nginx_conf || return
    [[ -f $backup && ! -L $backup ]] || { fail 'Backup must be a regular file.'; return 1; }
    fs list "$backup" || return
    warn 'Restore replaces the listed files with backup contents. Certificates and DNS records are unaffected.'
    [[ $DRY_RUN != true ]] || return 0
    confirm 'Restore exactly these files?' || return 1
    acquire_lock || return
    transaction_begin || return
    fs restore "$backup" "$TX" "$(dirname "$NGINX_CONF")" "$STATE_DIR/profiles" "$STATE_DIR/metadata" || return
    transaction_commit
}

recover_transaction() {
    local manifest=$1
    [[ $manifest == "$STATE_DIR"/transactions/transaction-*.json && -f $manifest && ! -L $manifest ]] || {
        fail 'Select a private manager transaction manifest.'; return 1;
    }
    fs list "$manifest" || return
    [[ $DRY_RUN != true ]] || return 0
    confirm 'Recover owned changes from this transaction?' || return 1
    acquire_lock || return
    resolve_nginx_conf || return
    fs rollback "$manifest" || return
    nginx_run -t && systemctl reload "$NGINX_SERVICE"
}
