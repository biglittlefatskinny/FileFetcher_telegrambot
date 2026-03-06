#!/usr/bin/env bash
# =============================================================================
#  File Fetcher Bot — Management Script
#  Installed to /usr/local/bin/filefetcher by the installer.
#
#  Usage: filefetcher <command>
#  Commands: start | stop | restart | status | logs | config | update | uninstall | help
# =============================================================================
set -euo pipefail

CONF_FILE="/etc/filefetcher-bot.conf"

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()  { echo -e "${GREEN}  [+]${NC} $*"; }
warn()  { echo -e "${YELLOW}  [!]${NC} $*"; }
error() { echo -e "${RED}  [✗]${NC} $*" >&2; exit 1; }

# ── Load install config ───────────────────────────────────────────────────────
load_conf() {
    [[ -f "$CONF_FILE" ]] || error "Bot is not installed. Run the installer first."
    # shellcheck source=/dev/null
    source "$CONF_FILE"
    [[ -n "${INSTALL_DIR:-}" ]] || error "Corrupt config at ${CONF_FILE}"
}

require_root() {
    [[ $EUID -eq 0 ]] || error "This command requires root. Use: sudo filefetcher $1"
}

# ── Commands ──────────────────────────────────────────────────────────────────

cmd_start() {
    require_root "start"
    load_conf
    systemctl start "$SERVICE_NAME"
    sleep 1
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        info "Bot ${GREEN}${BOLD}started${NC}"
    else
        warn "Bot may not have started. Check: filefetcher logs"
    fi
}

cmd_stop() {
    require_root "stop"
    load_conf
    systemctl stop "$SERVICE_NAME"
    info "Bot stopped"
}

cmd_restart() {
    require_root "restart"
    load_conf
    systemctl restart "$SERVICE_NAME"
    sleep 1
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        info "Bot ${GREEN}${BOLD}restarted${NC}"
    else
        warn "Bot may not have restarted. Check: filefetcher logs"
    fi
}

cmd_status() {
    load_conf

    echo ""
    echo -e "${CYAN}${BOLD}  ╔═══════════════════════════════════════╗"
    echo -e "  ║       📥  File Fetcher Bot Status      ║"
    echo -e "  ╚═══════════════════════════════════════╝${NC}"
    echo ""

    # Service state
    local state active_since
    state=$(systemctl is-active "$SERVICE_NAME" 2>/dev/null || echo "inactive")
    if [[ "$state" == "active" ]]; then
        echo -e "  ${BOLD}Service:${NC}      ${GREEN}${BOLD}● running${NC}"
        active_since=$(systemctl show "$SERVICE_NAME" --property=ActiveEnterTimestamp \
                       | cut -d= -f2)
        echo -e "  ${BOLD}Since:${NC}        ${active_since}"
    else
        echo -e "  ${BOLD}Service:${NC}      ${RED}● ${state}${NC}"
    fi

    # Restart count
    local restarts
    restarts=$(systemctl show "$SERVICE_NAME" --property=NRestarts | cut -d= -f2 2>/dev/null || echo "?")
    echo -e "  ${BOLD}Restarts:${NC}     ${restarts}"

    echo ""
    echo -e "  ${BOLD}Install dir:${NC}  ${INSTALL_DIR}"

    # Read limits from .env
    if [[ -f "${INSTALL_DIR}/.env" ]]; then
        echo ""
        echo -e "  ${BOLD}── Current Limits ────────────────────────────────────${NC}"
        local max_size hourly daily concurrent
        max_size=$(  grep "^FILEFETCHER_MAX_FILE_SIZE_MB"  "${INSTALL_DIR}/.env" | cut -d= -f2)
        hourly=$(    grep "^FILEFETCHER_MAX_HOURLY_MB"     "${INSTALL_DIR}/.env" | cut -d= -f2)
        daily=$(     grep "^FILEFETCHER_MAX_DAILY_MB"      "${INSTALL_DIR}/.env" | cut -d= -f2)
        concurrent=$(grep "^FILEFETCHER_MAX_CONCURRENT"    "${INSTALL_DIR}/.env" | cut -d= -f2)
        rpm=$(       grep "^FILEFETCHER_RATE_LIMIT_RPM"    "${INSTALL_DIR}/.env" | cut -d= -f2)
        allowlist=$( grep "^FILEFETCHER_DOMAIN_ALLOWLIST"  "${INSTALL_DIR}/.env" | cut -d= -f2 || echo "")
        echo -e "  Max file size:    ${BOLD}${max_size} MB${NC}"
        echo -e "  Hourly quota:     ${BOLD}${hourly} MB${NC} per user"
        echo -e "  Daily quota:      ${BOLD}${daily} MB${NC} per user"
        echo -e "  Max concurrent:   ${BOLD}${concurrent}${NC} downloads"
        echo -e "  Rate limit:       ${BOLD}${rpm}${NC} requests/min per user"
        echo -e "  Domain allowlist: ${BOLD}${allowlist:-all domains allowed}${NC}"
    fi

    # Recent log summary
    echo ""
    echo -e "  ${BOLD}── Recent Activity (last 10 log lines) ───────────────${NC}"
    journalctl -u "$SERVICE_NAME" -n 10 --no-pager --output=short 2>/dev/null \
        | sed 's/^/  /' || echo "  (no logs)"
    echo ""
    echo -e "  Run ${CYAN}filefetcher logs${NC} for live output."
    echo ""
}

cmd_logs() {
    load_conf
    echo -e "${CYAN}  Following logs for ${SERVICE_NAME} — Ctrl+C to exit${NC}"
    echo ""
    journalctl -u "$SERVICE_NAME" -f --output=short
}

cmd_config() {
    require_root "config"
    load_conf
    local editor="${EDITOR:-nano}"
    echo -e "${CYAN}  Opening ${INSTALL_DIR}/.env with ${editor}${NC}"
    echo -e "${YELLOW}  Restart the bot after saving: filefetcher restart${NC}"
    sleep 1
    "$editor" "${INSTALL_DIR}/.env"
}

cmd_update() {
    require_root "update"
    load_conf
    echo ""
    echo -e "${CYAN}${BOLD}▶ Updating File Fetcher Bot${NC}"
    echo ""

    info "Pulling latest code..."
    git -C "$INSTALL_DIR" pull
    info "Updating Python dependencies..."
    "${INSTALL_DIR}/.venv/bin/pip" install --quiet --upgrade -r "${INSTALL_DIR}/requirements.txt"

    # Re-install manage script in case it changed
    if [[ -f "${INSTALL_DIR}/manage.sh" ]]; then
        cp "${INSTALL_DIR}/manage.sh" /usr/local/bin/filefetcher
        chmod +x /usr/local/bin/filefetcher
    fi

    systemctl daemon-reload
    systemctl restart "$SERVICE_NAME"
    sleep 1

    if systemctl is-active --quiet "$SERVICE_NAME"; then
        info "Bot updated and ${GREEN}${BOLD}restarted${NC}"
    else
        warn "Bot may not have started after update. Check: filefetcher logs"
    fi
}

cmd_uninstall() {
    require_root "uninstall"
    load_conf
    echo ""
    echo -e "${RED}${BOLD}  ⚠  This will stop the bot and remove all files.${NC}"
    echo -ne "${YELLOW}    Are you sure? Type 'yes' to confirm: ${NC}"
    read -r confirm
    [[ "$confirm" == "yes" ]] || { echo "Aborted."; exit 0; }

    echo ""
    info "Stopping and disabling service..."
    systemctl stop    "$SERVICE_NAME" 2>/dev/null || true
    systemctl disable "$SERVICE_NAME" 2>/dev/null || true
    rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
    systemctl daemon-reload

    info "Removing bot files..."
    rm -rf "$INSTALL_DIR"

    info "Removing management script..."
    rm -f /usr/local/bin/filefetcher

    info "Removing config..."
    rm -f "$CONF_FILE"
    rmdir /etc/filefetcher-bot 2>/dev/null || true

    info "Removing system user '${BOT_USER}'..."
    userdel "$BOT_USER" 2>/dev/null || true

    echo ""
    echo -e "${GREEN}${BOLD}  ✔ Uninstall complete. Bot fully removed.${NC}"
    echo ""
}

cmd_help() {
    echo ""
    echo -e "${CYAN}${BOLD}  📥 File Fetcher Bot — Management${NC}"
    echo ""
    echo -e "  ${BOLD}Usage:${NC}  filefetcher <command>"
    echo ""
    echo -e "  ${BOLD}Commands:${NC}"
    echo -e "    ${CYAN}status${NC}      Show service state, current limits, and recent logs"
    echo -e "    ${CYAN}start${NC}       Start the bot service"
    echo -e "    ${CYAN}stop${NC}        Stop the bot service"
    echo -e "    ${CYAN}restart${NC}     Restart the bot service"
    echo -e "    ${CYAN}logs${NC}        Follow live log output  (Ctrl+C to exit)"
    echo -e "    ${CYAN}config${NC}      Edit configuration in \$EDITOR  (then restart)"
    echo -e "    ${CYAN}update${NC}      Pull latest code and restart"
    echo -e "    ${CYAN}uninstall${NC}   Stop the bot and remove all files"
    echo -e "    ${CYAN}help${NC}        Show this message"
    echo ""
    echo -e "  ${BOLD}Commands that need sudo:${NC} start, stop, restart, config, update, uninstall"
    echo ""
}

# ── Dispatch ──────────────────────────────────────────────────────────────────
case "${1:-help}" in
    start)     cmd_start     ;;
    stop)      cmd_stop      ;;
    restart)   cmd_restart   ;;
    status)    cmd_status    ;;
    logs)      cmd_logs      ;;
    config)    cmd_config    ;;
    update)    cmd_update    ;;
    uninstall) cmd_uninstall ;;
    help|--help|-h) cmd_help ;;
    *) echo -e "${RED}Unknown command: $1${NC}"; cmd_help; exit 1 ;;
esac
