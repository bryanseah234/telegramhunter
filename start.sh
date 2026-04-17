#!/usr/bin/env bash
# ================================================================
# Telegram Hunter — Launcher
# Run without arguments for the interactive menu.
# Or pass a command directly: ./start.sh start|stop|restart|etc.
# ================================================================

set -e

# ── colours ──────────────────────────────────────────────────────
RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'
CYAN='\033[0;36m'; BOLD='\033[1m'; DIM='\033[2m'; NC='\033[0m'

# ── port helpers ─────────────────────────────────────────────────
port_in_use() {
    if command -v ss &>/dev/null; then
        ss -tnl 2>/dev/null | grep -q ":${1} " && return 0 || return 1
    else
        lsof -iTCP:"${1}" -sTCP:LISTEN -n -P &>/dev/null && return 0 || return 1
    fi
}

find_free_port() {
    local port=$1
    while port_in_use "$port"; do
        echo -e "  ${YELLOW}⚠️  Port $port in use — trying $((port+1))...${NC}" >&2
        port=$((port+1))
    done
    echo "$port"
}

resolve_ports() {
    echo -e "${CYAN}🔍 Checking ports...${NC}"
    export API_PORT;   API_PORT=$(find_free_port   "${API_PORT:-8011}")
    export REDIS_PORT; REDIS_PORT=$(find_free_port "${REDIS_PORT:-6379}")
    echo -e "  ${GREEN}✅ API   → :$API_PORT${NC}"
    echo -e "  ${GREEN}✅ Redis → :$REDIS_PORT${NC}"
    echo ""
}

# ── commands ─────────────────────────────────────────────────────
cmd_start() {
    resolve_ports
    echo -e "${CYAN}🚀 Starting Telegram Hunter...${NC}"
    docker compose up -d "$@"
    echo ""
    docker compose ps
}

cmd_stop() {
    echo -e "${CYAN}🛑 Stopping Telegram Hunter...${NC}"
    docker compose down
    echo -e "${GREEN}✅ All containers stopped.${NC}"
}

cmd_restart() {
    cmd_stop; echo ""; cmd_start
}

cmd_rebuild() {
    resolve_ports
    echo -e "${CYAN}🔨 Rebuilding images and starting...${NC}"
    docker compose up -d --build
    echo ""
    docker compose ps
}

cmd_status() {
    echo -e "${BOLD}Container Status:${NC}"
    echo ""
    docker compose ps
}

cmd_logs() {
    echo -e "${DIM}Press Ctrl+C to exit logs.${NC}"
    echo ""
    docker compose logs -f "${@}"
}

cmd_update() {
    echo -e "${CYAN}📥 Pulling latest code from GitHub...${NC}"
    git fetch origin
    git pull origin main
    echo ""
    echo -e "${CYAN}🔨 Rebuilding images...${NC}"
    docker compose build
    echo ""
    cmd_restart
}

cmd_reset() {
    echo ""
    echo -e "${RED}${BOLD}⚠️  RESET WARNING${NC}"
    echo -e "${RED}This will:${NC}"
    echo -e "${RED}  • Stop all containers${NC}"
    echo -e "${RED}  • Wipe Redis data${NC}"
    echo -e "${RED}  • Hard-reset code to latest GitHub version${NC}"
    echo -e "${GREEN}This will NOT touch your Supabase data or session files.${NC}"
    echo ""
    read -r -p "Type YES to confirm: " confirm
    if [[ "$confirm" != "YES" ]]; then
        echo -e "${YELLOW}Aborted.${NC}"; return
    fi
    echo ""
    echo -e "${CYAN}1. Stopping containers and wiping volumes...${NC}"
    docker compose down -v
    echo -e "${CYAN}2. Resetting code to latest...${NC}"
    git fetch origin && git reset --hard origin/main
    echo -e "${CYAN}3. Rebuilding and starting...${NC}"
    resolve_ports
    docker compose up -d --build
    echo ""
    docker compose ps
}

cmd_health() {
    echo -e "${BOLD}Health Check:${NC}"
    echo ""
    local api_port="${API_PORT:-8011}"
    if curl -sf "http://localhost:${api_port}/health/" > /dev/null 2>&1; then
        echo -e "  ${GREEN}✅ API is responding on :${api_port}${NC}"
        curl -s "http://localhost:${api_port}/health/detailed" | python3 -m json.tool 2>/dev/null || true
    else
        echo -e "  ${RED}❌ API not responding on :${api_port}${NC}"
    fi
}

# ── menu ─────────────────────────────────────────────────────────
show_menu() {
    clear
    echo -e "${BOLD}${CYAN}"
    echo "  ████████╗███████╗██╗     ███████╗██╗  ██╗██╗   ██╗███╗   ██╗████████╗███████╗██████╗ "
    echo "     ██╔══╝██╔════╝██║     ██╔════╝██║  ██║██║   ██║████╗  ██║╚══██╔══╝██╔════╝██╔══██╗"
    echo "     ██║   █████╗  ██║     █████╗  ███████║██║   ██║██╔██╗ ██║   ██║   █████╗  ██████╔╝"
    echo "     ██║   ██╔══╝  ██║     ██╔══╝  ██╔══██║██║   ██║██║╚██╗██║   ██║   ██╔══╝  ██╔══██╗"
    echo "     ██║   ███████╗███████╗███████╗██║  ██║╚██████╔╝██║ ╚████║   ██║   ███████╗██║  ██║"
    echo "     ╚═╝   ╚══════╝╚══════╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═══╝   ╚═╝   ╚══════╝╚═╝  ╚═╝"
    echo -e "${NC}"
    echo -e "${DIM}  Automated OSINT System — Telegram Bot Token Hunter${NC}"
    echo ""
    echo -e "${BOLD}  ┌─────────────────────────────────────┐${NC}"
    echo -e "${BOLD}  │           MAIN MENU                 │${NC}"
    echo -e "${BOLD}  ├─────────────────────────────────────┤${NC}"
    echo -e "  │  ${GREEN}1)${NC} Start                          │"
    echo -e "  │  ${GREEN}2)${NC} Start (rebuild images)         │"
    echo -e "  │  ${GREEN}3)${NC} Stop                           │"
    echo -e "  │  ${GREEN}4)${NC} Restart                        │"
    echo -e "  │  ${CYAN}5)${NC} Status                         │"
    echo -e "  │  ${CYAN}6)${NC} View Logs                       │"
    echo -e "  │  ${CYAN}7)${NC} Health Check                    │"
    echo -e "  │  ${YELLOW}8)${NC} Update (pull + rebuild)        │"
    echo -e "  │  ${RED}9)${NC} Reset (wipe + fresh start)     │"
    echo -e "  │  ${DIM}0) Exit${NC}                            │"
    echo -e "${BOLD}  └─────────────────────────────────────┘${NC}"
    echo ""
    read -r -p "  Choose an option [0-9]: " choice
    echo ""

    case "$choice" in
        1) cmd_start ;;
        2) cmd_rebuild ;;
        3) cmd_stop ;;
        4) cmd_restart ;;
        5) cmd_status ;;
        6)
            echo -e "${BOLD}  Log Options:${NC}"
            echo "  a) All services"
            echo "  b) API only"
            echo "  c) Workers only"
            echo "  d) Bot only"
            echo ""
            read -r -p "  Choose [a-d]: " log_choice
            echo ""
            case "$log_choice" in
                a) cmd_logs ;;
                b) cmd_logs api ;;
                c) cmd_logs worker-core worker-scanners worker-scrape ;;
                d) cmd_logs bot ;;
                *) echo -e "${RED}Invalid choice.${NC}" ;;
            esac
            ;;
        7) cmd_health ;;
        8) cmd_update ;;
        9) cmd_reset ;;
        0) echo -e "${DIM}Bye!${NC}"; exit 0 ;;
        *) echo -e "${RED}Invalid option.${NC}" ;;
    esac

    echo ""
    read -r -p "Press Enter to return to menu..." _
    show_menu
}

# ── dispatch ─────────────────────────────────────────────────────
case "${1:-menu}" in
    menu|"")   show_menu ;;
    start)     cmd_start ;;
    build|--build) cmd_rebuild ;;
    stop|down) cmd_stop ;;
    restart)   cmd_restart ;;
    status|ps) cmd_status ;;
    logs)      shift; cmd_logs "$@" ;;
    health)    cmd_health ;;
    update)    cmd_update ;;
    reset)     cmd_reset ;;
    *)
        echo -e "${RED}Unknown command: $1${NC}"
        echo "Usage: ./start.sh [start|build|stop|restart|status|logs|health|update|reset]"
        echo "       ./start.sh        (interactive menu)"
        exit 1
        ;;
esac
