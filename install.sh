#!/usr/bin/env bash
# =============================================================================
#  File Fetcher Bot — One-line Installer
#  https://github.com/biglittlefatskinny/FileFetcher_telegrambot
#
#  Usage (one-line):
#    bash <(curl -fsSL https://raw.githubusercontent.com/biglittlefatskinny/FileFetcher_telegrambot/main/install.sh)
#
#  Or after cloning:
#    sudo bash install.sh
# =============================================================================
set -euo pipefail

# ── Constants ─────────────────────────────────────────────────────────────────
REPO_URL="https://github.com/biglittlefatskinny/FileFetcher_telegrambot.git"
SERVICE_NAME="filefetcher-bot"
BOT_USER="filefetcher"
INSTALL_DIR="/opt/filefetcher-bot"
CONF_FILE="/etc/filefetcher-bot.conf"
MANAGE_BIN="/usr/local/bin/filefetcher"

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

# ── Logging ───────────────────────────────────────────────────────────────────
info()  { echo -e "${GREEN}  [+]${NC} $*"; }
warn()  { echo -e "${YELLOW}  [!]${NC} $*"; }
error() { echo -e "${RED}  [✗] ERROR:${NC} $*" >&2; exit 1; }
step()  { echo -e "\n${CYAN}${BOLD}▶ $*${NC}"; }

# ── Interactive input ──────────────────────────────────────────────────────────
# Works both when piped via curl and when run directly
if [[ ! -t 0 ]]; then
    exec < /dev/tty
fi

prompt() {
    # prompt VAR_NAME "Display text" "default_value"
    local __var="$1" __text="$2" __default="${3:-}" __val=""
    if [[ -n "$__default" ]]; then
        echo -ne "${YELLOW}    ${__text}${NC} [${BOLD}${__default}${NC}]: "
    else
        echo -ne "${YELLOW}    ${__text}${NC}: "
    fi
    read -r __val
    [[ -z "$__val" ]] && __val="$__default"
    printf -v "$__var" '%s' "$__val"
}

prompt_required() {
    local __var="$1" __text="$2" __val=""
    while [[ -z "$__val" ]]; do
        echo -ne "${YELLOW}    ${__text}${NC} ${RED}(required)${NC}: "
        read -r __val
        [[ -z "$__val" ]] && warn "This field cannot be empty."
    done
    printf -v "$__var" '%s' "$__val"
}

confirm() {
    # confirm "Question" → returns 0 for yes, 1 for no
    local __text="$1" __val=""
    echo -ne "${YELLOW}    ${__text}${NC} [y/N]: "
    read -r __val
    [[ "${__val,,}" == "y" || "${__val,,}" == "yes" ]]
}

# ── Header ────────────────────────────────────────────────────────────────────
print_header() {
    clear 2>/dev/null || true
    echo -e "${CYAN}${BOLD}"
    echo "  ╔═══════════════════════════════════════════════╗"
    echo "  ║        📥  File Fetcher Bot Installer         ║"
    echo "  ║  Fetch files via Telegram in restricted areas ║"
    echo "  ╚═══════════════════════════════════════════════╝"
    echo -e "${NC}"
    echo -e "  ${BOLD}Repo:${NC} ${REPO_URL}"
    echo ""
}

# ── Checks ────────────────────────────────────────────────────────────────────
check_root() {
    [[ $EUID -eq 0 ]] || error "Please run as root:  sudo bash install.sh"
}

check_os() {
    [[ "$(uname)" == "Linux" ]] || error "This installer only supports Linux."
    if   command -v apt-get &>/dev/null; then PKG_MGR="apt"
    elif command -v dnf     &>/dev/null; then PKG_MGR="dnf"
    elif command -v yum     &>/dev/null; then PKG_MGR="yum"
    else PKG_MGR="unknown"; warn "Unknown package manager — you may need to install Python 3.10+ and git manually."; fi
    info "OS: $(grep PRETTY_NAME /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '"') | Package manager: ${PKG_MGR}"
}

install_system_deps() {
    step "Installing system dependencies"
    case "$PKG_MGR" in
        apt) apt-get update -qq && apt-get install -y -qq python3 python3-pip python3-venv git curl ;;
        dnf) dnf install -y -q python3 python3-pip git curl ;;
        yum) yum install -y -q python3 python3-pip git curl ;;
        *)   warn "Skipping automatic package install — please ensure python3, pip, and git are installed." ;;
    esac

    # Verify Python version >= 3.10
    local py_major py_minor
    py_major=$(python3 -c "import sys; print(sys.version_info.major)")
    py_minor=$(python3 -c "import sys; print(sys.version_info.minor)")
    local py_ver
    py_ver=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')")
    if [[ "$py_major" -lt 3 || ( "$py_major" -eq 3 && "$py_minor" -lt 10 ) ]]; then
        error "Python 3.10+ required, found ${py_ver}"
    fi
    info "Python ${py_ver} — OK"
}

# ── User ──────────────────────────────────────────────────────────────────────
create_bot_user() {
    if id "$BOT_USER" &>/dev/null; then
        info "System user '${BOT_USER}' already exists"
    else
        useradd --system --no-create-home --shell /usr/sbin/nologin "$BOT_USER"
        info "Created system user '${BOT_USER}'"
    fi
}

# ── Files ─────────────────────────────────────────────────────────────────────
setup_files() {
    step "Setting up bot files in ${INSTALL_DIR}"

    if [[ -d "${INSTALL_DIR}/.git" ]]; then
        info "Existing installation found — pulling latest changes..."
        git -C "$INSTALL_DIR" pull --quiet
    else
        info "Cloning repository..."
        git clone --quiet "$REPO_URL" "$INSTALL_DIR"
    fi

    info "Creating Python virtual environment..."
    python3 -m venv "${INSTALL_DIR}/.venv"
    "${INSTALL_DIR}/.venv/bin/pip" install --quiet --upgrade pip
    "${INSTALL_DIR}/.venv/bin/pip" install --quiet -r "${INSTALL_DIR}/requirements.txt"
    info "Python dependencies installed"

    chown -R "${BOT_USER}:${BOT_USER}" "$INSTALL_DIR"
}

# ── Configuration ─────────────────────────────────────────────────────────────
configure() {
    step "Bot Configuration"
    echo ""
    echo -e "  Press ${BOLD}Enter${NC} to accept the default shown in [brackets]."
    echo ""

    echo -e "  ${BOLD}── Telegram ──────────────────────────────────────────${NC}"
    prompt_required BOT_TOKEN   "Bot token (from @BotFather)"
    echo ""

    echo -e "  ${BOLD}── Download Limits ───────────────────────────────────${NC}"
    echo -e "  ${YELLOW}  ⚠  Telegram bots have a hard 50 MB upload limit."
    echo -e "      Set max file size to 50 or below, or files will be rejected by Telegram.${NC}"
    echo ""
    prompt MAX_FILE_SIZE_MB     "Max file size MB        (must be ≤ 50)" "45"
    # Silently cap at 50 to prevent Telegram rejections
    if [[ "$MAX_FILE_SIZE_MB" -gt 50 ]]; then
        warn "Capped to 50 MB (Telegram's hard limit)."
        MAX_FILE_SIZE_MB=50
    fi
    prompt MAX_HOURLY_MB        "Per-user hourly quota MB"                      "200"
    prompt MAX_DAILY_MB         "Per-user daily quota MB"                       "1000"
    echo ""

    echo -e "  ${BOLD}── Performance ───────────────────────────────────────${NC}"
    prompt MAX_CONCURRENT       "Max simultaneous downloads"                    "6"
    prompt RATE_LIMIT_RPM       "Max URL requests/min per user"                 "10"
    prompt RATE_LIMIT_BURST     "Per-user burst allowance"                      "3"
    prompt DOWNLOAD_TIMEOUT     "Download timeout (seconds)"                    "120"
    echo ""

    echo -e "  ${BOLD}── Security ──────────────────────────────────────────${NC}"
    prompt DOMAIN_ALLOWLIST     "Allowed domains, comma-separated (empty = all)" ""
    echo ""
}

write_env() {
    cat > "${INSTALL_DIR}/.env" <<EOF
FILEFETCHER_BOT_TOKEN=${BOT_TOKEN}
FILEFETCHER_LOG_LEVEL=INFO
FILEFETCHER_JSON_LOGS=0
FILEFETCHER_MAX_FILE_SIZE_MB=${MAX_FILE_SIZE_MB}
FILEFETCHER_MAX_HOURLY_MB=${MAX_HOURLY_MB}
FILEFETCHER_MAX_DAILY_MB=${MAX_DAILY_MB}
FILEFETCHER_MAX_CONCURRENT=${MAX_CONCURRENT}
FILEFETCHER_RATE_LIMIT_RPM=${RATE_LIMIT_RPM}
FILEFETCHER_RATE_LIMIT_BURST=${RATE_LIMIT_BURST}
FILEFETCHER_DOWNLOAD_TIMEOUT=${DOWNLOAD_TIMEOUT}
FILEFETCHER_DOMAIN_ALLOWLIST=${DOMAIN_ALLOWLIST}
EOF
    chmod 600 "${INSTALL_DIR}/.env"
    chown "${BOT_USER}:${BOT_USER}" "${INSTALL_DIR}/.env"
    info ".env written (chmod 600)"
}

# ── Systemd ───────────────────────────────────────────────────────────────────
write_service() {
    cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=File Fetcher Telegram Bot
Documentation=${REPO_URL}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${BOT_USER}
Group=${BOT_USER}
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${INSTALL_DIR}/.env
ExecStart=${INSTALL_DIR}/.venv/bin/python -m filefetcher.main
Restart=on-failure
RestartSec=10s
StartLimitIntervalSec=120
StartLimitBurst=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=${INSTALL_DIR} /tmp
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}

[Install]
WantedBy=multi-user.target
EOF
    info "systemd service written"
}

# ── Management script ─────────────────────────────────────────────────────────
install_manage_script() {
    cp "${INSTALL_DIR}/manage.sh" "$MANAGE_BIN"
    chmod +x "$MANAGE_BIN"

    # Store install metadata so manage.sh can find everything
    mkdir -p /etc/filefetcher-bot
    cat > "$CONF_FILE" <<EOF
INSTALL_DIR=${INSTALL_DIR}
SERVICE_NAME=${SERVICE_NAME}
BOT_USER=${BOT_USER}
REPO_URL=${REPO_URL}
EOF
    info "Management script installed → run 'filefetcher help' anytime"
}

# ── Start ─────────────────────────────────────────────────────────────────────
start_service() {
    step "Starting the bot service"
    systemctl daemon-reload
    systemctl enable --quiet "$SERVICE_NAME"
    systemctl restart "$SERVICE_NAME"
    sleep 2
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        info "Service is ${GREEN}${BOLD}running${NC}"
    else
        warn "Service may not have started. Run: journalctl -u ${SERVICE_NAME} -n 30"
    fi
}

# ── Summary ───────────────────────────────────────────────────────────────────
print_summary() {
    echo ""
    echo -e "${GREEN}${BOLD}  ✔ Installation complete!${NC}"
    echo ""
    echo -e "  ${BOLD}Bot is running.${NC} Users can now send file URLs to your bot."
    echo ""
    echo -e "  ${BOLD}Useful commands:${NC}"
    echo -e "    ${CYAN}filefetcher status${NC}    — service status + stats"
    echo -e "    ${CYAN}filefetcher logs${NC}      — live log output"
    echo -e "    ${CYAN}filefetcher config${NC}    — edit configuration"
    echo -e "    ${CYAN}filefetcher update${NC}    — pull latest version"
    echo -e "    ${CYAN}filefetcher stop${NC}      — stop the bot"
    echo -e "    ${CYAN}filefetcher uninstall${NC} — remove everything"
    echo ""
    echo -e "  ${BOLD}Logs:${NC}  journalctl -u ${SERVICE_NAME} -f"
    echo -e "  ${BOLD}Config:${NC} ${INSTALL_DIR}/.env"
    echo ""
}

# ── Main ──────────────────────────────────────────────────────────────────────
main() {
    print_header
    check_root
    check_os

    # Check for existing installation
    if [[ -d "${INSTALL_DIR}" ]]; then
        warn "Existing installation detected at ${INSTALL_DIR}"
        if confirm "Update existing installation? (config will be preserved)"; then
            UPDATING=true
        else
            error "Aborted."
        fi
    else
        UPDATING=false
    fi

    install_system_deps
    create_bot_user
    setup_files

    if [[ "$UPDATING" == "true" && -f "${INSTALL_DIR}/.env" ]]; then
        info "Keeping existing configuration"
        if confirm "Reconfigure settings?"; then
            configure
            write_env
        fi
    else
        configure
        write_env
    fi

    write_service
    install_manage_script
    start_service
    print_summary
}

main "$@"
