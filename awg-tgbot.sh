#!/usr/bin/env bash
set -Eeuo pipefail

REPO_OWNER="Just1k13"
REPO_NAME="awg-tgbot"
REPO_BRANCH="${REPO_BRANCH:-main}"
REPO_URL="https://github.com/${REPO_OWNER}/${REPO_NAME}"
RAW_BASE_URL="https://raw.githubusercontent.com/${REPO_OWNER}/${REPO_NAME}/${REPO_BRANCH}"
TARBALL_URL="https://codeload.github.com/${REPO_OWNER}/${REPO_NAME}/tar.gz/refs/heads/${REPO_BRANCH}"
COMMIT_API_URL="https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/commits/${REPO_BRANCH}"

INSTALL_DIR="/opt/amnezia/bot"
BOT_DIR="${INSTALL_DIR}/bot"
ENV_FILE="${INSTALL_DIR}/.env"
VENV_DIR="${INSTALL_DIR}/.venv"
SERVICE_NAME="vpn-bot.service"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}"
STATE_DIR="${INSTALL_DIR}/.state"
VERSION_FILE="${STATE_DIR}/release_sha"
INSTALL_LOG="/var/log/awg-tgbot-install.log"
APP_LOG_DIR="/var/log/awg-tgbot"
APP_LOG_FILE="${APP_LOG_DIR}/bot.log"
PYTHON_BIN="/usr/bin/python3"
TTY_DEVICE="/dev/tty"
SELF_SYMLINK="/usr/local/bin/awg-tgbot"

print_line() {
  printf '%s\n' "------------------------------------------------------------"
}

info() {
  printf '[*] %s\n' "$*" >&2
}

ok() {
  printf '[+] %s\n' "$*" >&2
}

warn() {
  printf '[!] %s\n' "$*" >&2
}

trap 'printf "[!] Ошибка на строке %s. Подробности: %s\n" "$LINENO" "$INSTALL_LOG" >&2' ERR

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "Запусти скрипт от root: sudo bash awg-tgbot.sh"
    echo "Или одной командой: curl -fsSL ${RAW_BASE_URL}/awg-tgbot.sh | sudo bash"
    exit 1
  fi
}

setup_logging() {
  mkdir -p "$(dirname "$INSTALL_LOG")" "$APP_LOG_DIR"
  touch "$INSTALL_LOG" "$APP_LOG_FILE"
  chmod 640 "$INSTALL_LOG" "$APP_LOG_FILE" || true
  exec > >(tee -a "$INSTALL_LOG") 2>&1
}

has_tty() {
  [[ -r "$TTY_DEVICE" ]]
}

pause_if_tty() {
  if has_tty; then
    echo
    read -r -p "Нажми Enter, чтобы продолжить..." _dummy < "$TTY_DEVICE"
  fi
}

clear_if_tty() {
  if has_tty; then
    clear || true
  fi
}

prompt_raw() {
  local prompt="$1"
  local __resultvar="$2"
  local value=""
  if has_tty; then
    read -r -p "$prompt" value < "$TTY_DEVICE"
  fi
  printf -v "$__resultvar" '%s' "$value"
}

prompt_with_default() {
  local prompt="$1"
  local default="${2:-}"
  local value=""
  while true; do
    if [[ -n "$default" ]]; then
      prompt_raw "$prompt [$default]: " value
      value="${value:-$default}"
    else
      prompt_raw "$prompt: " value
    fi
    if [[ -n "$value" ]]; then
      printf '%s' "$value"
      return 0
    fi
    warn "Значение не может быть пустым."
  done
}

confirm() {
  local prompt="$1"
  local default="${2:-Y}"
  local value=""
  local suffix="[Y/n]"
  if [[ "$default" == "N" ]]; then
    suffix="[y/N]"
  fi
  while true; do
    prompt_raw "$prompt $suffix: " value
    value="${value:-$default}"
    case "${value,,}" in
      y|yes|д|да) return 0 ;;
      n|no|н|нет) return 1 ;;
      *) warn "Введите y или n." ;;
    esac
  done
}

require_command() {
  command -v "$1" >/dev/null 2>&1
}

service_exists() {
  [[ -f "$SERVICE_FILE" ]]
}

is_installed() {
  [[ -f "$SERVICE_FILE" && -d "$BOT_DIR" && -f "$BOT_DIR/app.py" ]]
}

get_env_value() {
  local key="$1"
  [[ -f "$ENV_FILE" ]] || return 0
  grep -m1 -E "^${key}=" "$ENV_FILE" | cut -d'=' -f2- || true
}

set_env_value() {
  local key="$1"
  local value="$2"
  mkdir -p "$INSTALL_DIR"
  touch "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  local escaped
  escaped="$(printf '%s' "$value" | sed -e 's/[\\/&]/\\&/g')"
  if grep -q -E "^${key}=" "$ENV_FILE" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${escaped}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

fetch_remote_sha() {
  local sha
  sha="$(curl -fsSL "$COMMIT_API_URL" | grep -m1 '"sha"' | sed -E 's/.*"sha": "([a-f0-9]+)".*/\1/' || true)"
  printf '%s' "$sha"
}

get_local_sha() {
  [[ -f "$VERSION_FILE" ]] && cat "$VERSION_FILE" || true
}

ensure_packages() {
  info "Проверяю и обновляю системные зависимости..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y --no-install-recommends \
    ca-certificates curl tar gzip openssl python3 python3-venv python3-pip iproute2
  if ! require_command docker; then
    warn "Docker не найден. Устанавливаю docker.io..."
    apt-get install -y --no-install-recommends docker.io
  fi
}

docker_is_accessible() {
  require_command docker && docker ps >/dev/null 2>&1
}

ensure_docker_ready() {
  if ! docker_is_accessible; then
    warn "Docker недоступен. Проверь, что docker установлен и daemon запущен."
    warn "Подсказка: systemctl status docker --no-pager"
    return 1
  fi
  return 0
}

pick_existing_or_default() {
  local current="$1"
  local fallback="$2"
  if [[ -n "$current" ]]; then
    printf '%s' "$current"
  else
    printf '%s' "$fallback"
  fi
}

is_public_ipv4() {
  local value="$1"
  "$PYTHON_BIN" - "$value" <<'PY'
import ipaddress, sys
value = sys.argv[1].strip()
try:
    addr = ipaddress.ip_address(value)
    ok = addr.version == 4 and not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_unspecified or addr.is_reserved)
    print("1" if ok else "0")
except ValueError:
    print("0")
PY
}

is_hostname_like() {
  local value="$1"
  "$PYTHON_BIN" - "$value" <<'PY'
import re, sys
value = sys.argv[1].strip()
ok = bool(value) and ' ' not in value and ':' not in value and len(value) <= 253 and value.lower() not in {"localhost"} and bool(re.fullmatch(r"[A-Za-z0-9.-]+", value))
print("1" if ok else "0")
PY
}

find_awg_container() {
  local current
  current="$(get_env_value DOCKER_CONTAINER)"
  if [[ -n "$current" ]] && docker_is_accessible && docker inspect "$current" >/dev/null 2>&1; then
    printf '%s' "$current"
    return 0
  fi
  if ! docker_is_accessible; then
    printf '%s' "$current"
    return 0
  fi
  local lines line name image haystack score best_score=0 best_name=""
  lines="$(docker ps --format '{{.Names}}\t{{.Image}}' 2>/dev/null || true)"
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    name="${line%%$'\t'*}"
    image="${line#*$'\t'}"
    haystack="${name,,} ${image,,}"
    score=0
    [[ "$haystack" == *"amnezia-awg"* ]] && score=$((score+100))
    [[ "$haystack" == *"awg"* ]] && score=$((score+70))
    [[ "$haystack" == *"wireguard"* ]] && score=$((score+60))
    [[ "$haystack" == *"vpn"* ]] && score=$((score+30))
    if (( score > best_score )); then
      best_score=$score
      best_name="$name"
    fi
  done <<< "$lines"
  printf '%s' "$best_name"
}

extract_awg_value() {
  local label="$1"
  local content="$2"
  awk -F': ' -v k="$label" '$1 == k {print substr($0, index($0, ": ")+2); exit}' <<< "$content"
}

get_public_host() {
  local current direct value fqdn
  current="$(get_env_value SERVER_IP)"
  if [[ -n "$current" && "$current" == *:* ]]; then
    printf '%s' "${current%:*}"
    return 0
  fi

  for direct in "$(get_env_value PUBLIC_HOST)" "${PUBLIC_HOST:-}" "$(get_env_value SERVER_HOST)" "${SERVER_HOST:-}" "$(get_env_value SERVER_DOMAIN)" "${SERVER_DOMAIN:-}"; do
    direct="${direct// /}"
    if [[ -z "$direct" ]]; then
      continue
    fi
    if [[ "$(is_public_ipv4 "$direct")" == "1" || "$(is_hostname_like "$direct")" == "1" ]]; then
      printf '%s' "$direct"
      return 0
    fi
  done

  if require_command curl; then
    for url in \
      "https://api.ipify.org" \
      "https://ifconfig.me/ip" \
      "https://ipv4.icanhazip.com"; do
      value="$(curl -4 -fsSL "$url" 2>/dev/null | tr -d '[:space:]' || true)"
      if [[ "$(is_public_ipv4 "$value")" == "1" ]]; then
        printf '%s' "$value"
        return 0
      fi
    done
  fi

  fqdn="$(hostname -f 2>/dev/null | tr -d '[:space:]' || true)"
  if [[ "$(is_hostname_like "$fqdn")" == "1" ]]; then
    printf '%s' "$fqdn"
    return 0
  fi

  printf '%s' ""
}

detect_awg_environment() {
  DETECTED_CONTAINER=""
  DETECTED_INTERFACE=""
  DETECTED_PUBLIC_KEY=""
  DETECTED_LISTEN_PORT=""
  DETECTED_SERVER_IP=""
  DETECTED_SERVER_NAME=""
  DETECTED_AWG_JC=""
  DETECTED_AWG_JMIN=""
  DETECTED_AWG_JMAX=""
  DETECTED_AWG_S1=""
  DETECTED_AWG_S2=""
  DETECTED_AWG_S3=""
  DETECTED_AWG_S4=""
  DETECTED_AWG_H1=""
  DETECTED_AWG_H2=""
  DETECTED_AWG_H3=""
  DETECTED_AWG_H4=""
  DETECTED_AWG_I1=""
  DETECTED_AWG_I2=""
  DETECTED_AWG_I3=""
  DETECTED_AWG_I4=""
  DETECTED_AWG_I5=""

  local configured_container configured_interface show_output host
  configured_container="$(get_env_value DOCKER_CONTAINER)"
  configured_interface="$(get_env_value WG_INTERFACE)"
  DETECTED_CONTAINER="$(pick_existing_or_default "$configured_container" "$(find_awg_container)")"
  DETECTED_SERVER_NAME="$(pick_existing_or_default "$(get_env_value SERVER_NAME)" "$(hostname 2>/dev/null || echo 'My VPN')")"

  if [[ -n "$DETECTED_CONTAINER" ]] && docker_is_accessible && docker inspect "$DETECTED_CONTAINER" >/dev/null 2>&1; then
    show_output="$(docker exec -i "$DETECTED_CONTAINER" awg show "${configured_interface:-awg0}" 2>/dev/null || true)"
    if [[ -z "$show_output" ]]; then
      show_output="$(docker exec -i "$DETECTED_CONTAINER" awg show 2>/dev/null || true)"
    fi
    if [[ -n "$show_output" && "$show_output" == *"interface:"* ]]; then
      DETECTED_INTERFACE="$(extract_awg_value 'interface' "$show_output")"
      DETECTED_PUBLIC_KEY="$(extract_awg_value 'public key' "$show_output")"
      DETECTED_LISTEN_PORT="$(extract_awg_value 'listening port' "$show_output")"
      DETECTED_AWG_JC="$(extract_awg_value 'jc' "$show_output")"
      DETECTED_AWG_JMIN="$(extract_awg_value 'jmin' "$show_output")"
      DETECTED_AWG_JMAX="$(extract_awg_value 'jmax' "$show_output")"
      DETECTED_AWG_S1="$(extract_awg_value 's1' "$show_output")"
      DETECTED_AWG_S2="$(extract_awg_value 's2' "$show_output")"
      DETECTED_AWG_S3="$(extract_awg_value 's3' "$show_output")"
      DETECTED_AWG_S4="$(extract_awg_value 's4' "$show_output")"
      DETECTED_AWG_H1="$(extract_awg_value 'h1' "$show_output")"
      DETECTED_AWG_H2="$(extract_awg_value 'h2' "$show_output")"
      DETECTED_AWG_H3="$(extract_awg_value 'h3' "$show_output")"
      DETECTED_AWG_H4="$(extract_awg_value 'h4' "$show_output")"
      DETECTED_AWG_I1="$(extract_awg_value 'i1' "$show_output")"
      DETECTED_AWG_I2="$(extract_awg_value 'i2' "$show_output")"
      DETECTED_AWG_I3="$(extract_awg_value 'i3' "$show_output")"
      DETECTED_AWG_I4="$(extract_awg_value 'i4' "$show_output")"
      DETECTED_AWG_I5="$(extract_awg_value 'i5' "$show_output")"
    fi
  fi

  [[ -z "$DETECTED_INTERFACE" ]] && DETECTED_INTERFACE="${configured_interface:-awg0}"
  host="$(get_public_host)"
  if [[ -n "$host" && -n "$DETECTED_LISTEN_PORT" ]]; then
    DETECTED_SERVER_IP="${host}:${DETECTED_LISTEN_PORT}"
  else
    DETECTED_SERVER_IP="$(get_env_value SERVER_IP)"
  fi
}

print_detected_awg_summary() {
  local pk_summary="не найден"
  [[ -n "$DETECTED_PUBLIC_KEY" ]] && pk_summary="${DETECTED_PUBLIC_KEY:0:16}..."
  print_line
  echo "Автоподбор AWG:"
  echo "Контейнер: ${DETECTED_CONTAINER:-не найден}"
  echo "Интерфейс: ${DETECTED_INTERFACE:-не найден}"
  echo "Public key: ${pk_summary}"
  echo "Endpoint: ${DETECTED_SERVER_IP:-не найден}"
  echo "Имя сервера: ${DETECTED_SERVER_NAME:-не найдено}"
  print_line
}

validate_awg_detection() {
  local ok=0
  if [[ -n "$DETECTED_CONTAINER" ]]; then
    ok=1
  else
    warn "Не удалось автоматически найти AWG-контейнер."
  fi
  if [[ -z "$DETECTED_PUBLIC_KEY" ]]; then
    warn "Не удалось автоматически определить SERVER_PUBLIC_KEY."
  fi
  if [[ -z "$DETECTED_SERVER_IP" ]]; then
    warn "Не удалось автоматически определить внешний SERVER_IP."
    warn "Если у сервера домен — лучше указать PUBLIC_HOST / домен вручную."
  fi
  return 0
}

download_repo() {
  local tmp_dir src_dir
  tmp_dir="$(mktemp -d)"
  info "Скачиваю код из ${REPO_URL} (${REPO_BRANCH})..."
  curl -fL --connect-timeout 20 --retry 3 --retry-delay 1 "$TARBALL_URL" -o "$tmp_dir/repo.tar.gz"
  tar -xzf "$tmp_dir/repo.tar.gz" -C "$tmp_dir"
  src_dir="$(find "$tmp_dir" -mindepth 1 -maxdepth 1 -type d -name "${REPO_NAME}-*" | head -n1 || true)"
  if [[ -z "$src_dir" || ! -d "$src_dir/bot" || ! -f "$src_dir/awg-tgbot.sh" ]]; then
    warn "Не удалось скачать корректную структуру репозитория."
    warn "Содержимое временной папки:"
    ls -la "$tmp_dir" >&2 || true
    [[ -n "$src_dir" ]] && ls -la "$src_dir" >&2 || true
    rm -rf "$tmp_dir"
    return 1
  fi
  printf '%s' "$tmp_dir"
}

deploy_repo() {
  local tmp_dir="$1"
  local src_dir backup_dir=""
  src_dir="$(find "$tmp_dir" -mindepth 1 -maxdepth 1 -type d -name "${REPO_NAME}-*" | head -n1 || true)"

  if [[ -z "$src_dir" || ! -d "$src_dir/bot" || ! -f "$src_dir/awg-tgbot.sh" ]]; then
    warn "Не найдены файлы репозитория для развёртывания."
    ls -la "$tmp_dir" >&2 || true
    [[ -n "$src_dir" ]] && ls -la "$src_dir" >&2 || true
    return 1
  fi

  mkdir -p "$INSTALL_DIR" "$STATE_DIR" "$(dirname "$SELF_SYMLINK")"

  if [[ -d "$BOT_DIR" || -f "$INSTALL_DIR/awg-tgbot.sh" ]]; then
    backup_dir="$(mktemp -d "${INSTALL_DIR}/.backup.XXXXXX")"
    [[ -d "$BOT_DIR" ]] && mv "$BOT_DIR" "$backup_dir/bot"
    [[ -f "$INSTALL_DIR/awg-tgbot.sh" ]] && mv "$INSTALL_DIR/awg-tgbot.sh" "$backup_dir/awg-tgbot.sh"
  fi

  rm -rf "$BOT_DIR"
  mkdir -p "$BOT_DIR"

  if cp -a "$src_dir/bot/." "$BOT_DIR/" \
    && cp "$src_dir/awg-tgbot.sh" "$INSTALL_DIR/awg-tgbot.sh" \
    && chmod +x "$INSTALL_DIR/awg-tgbot.sh" \
    && ln -sfn "$INSTALL_DIR/awg-tgbot.sh" "$SELF_SYMLINK"; then
    [[ -n "$backup_dir" ]] && rm -rf "$backup_dir"
    return 0
  fi

  warn "Не удалось развернуть файлы репозитория. Выполняю откат."
  rm -rf "$BOT_DIR"
  rm -f "$INSTALL_DIR/awg-tgbot.sh"
  if [[ -n "$backup_dir" && -d "$backup_dir" ]]; then
    [[ -d "$backup_dir/bot" ]] && mv "$backup_dir/bot" "$BOT_DIR"
    [[ -f "$backup_dir/awg-tgbot.sh" ]] && mv "$backup_dir/awg-tgbot.sh" "$INSTALL_DIR/awg-tgbot.sh"
    rm -rf "$backup_dir"
  fi
  return 1
}

ensure_env_file() {
  mkdir -p "$INSTALL_DIR"
  if [[ ! -f "$ENV_FILE" ]]; then
    if [[ -f "$BOT_DIR/.env.example" ]]; then
      cp "$BOT_DIR/.env.example" "$ENV_FILE"
    else
      touch "$ENV_FILE"
    fi
    chmod 600 "$ENV_FILE"
  fi
}

ensure_secret() {
  local current secret
  current="$(get_env_value ENCRYPTION_SECRET)"
  if [[ -n "$current" ]]; then
    printf '%s' "$current"
    return 0
  fi
  if require_command openssl; then
    secret="$(openssl rand -hex 32)"
  else
    secret="$("$PYTHON_BIN" - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
)"
  fi
  printf '%s' "$secret"
}

prompt_api_token() {
  local current token
  current="$(get_env_value API_TOKEN)"
  while true; do
    token="$(prompt_with_default 'Введите токен Telegram-бота' "$current")"
    if [[ "$token" == *:* ]]; then
      printf '%s' "$token"
      return 0
    fi
    warn "Нужен токен в формате 123456:ABCDEF..."
  done
}

prompt_admin_id() {
  local current admin_id
  current="$(get_env_value ADMIN_ID)"
  while true; do
    admin_id="$(prompt_with_default 'Введите Telegram user_id администратора' "$current")"
    if [[ "$admin_id" =~ ^[0-9]+$ ]]; then
      printf '%s' "$admin_id"
      return 0
    fi
    warn "ADMIN_ID должен быть числом."
  done
}

write_common_env() {
  local api_token="$1"
  local admin_id="$2"
  local server_name="$3"
  local secret="$4"

  set_env_value API_TOKEN "$api_token"
  set_env_value ADMIN_ID "$admin_id"
  set_env_value SERVER_NAME "$server_name"
  set_env_value ENCRYPTION_SECRET "$secret"
  set_env_value DB_PATH "vpn_bot.db"
}

write_detected_awg_env() {
  [[ -n "$DETECTED_CONTAINER" ]] && set_env_value DOCKER_CONTAINER "$DETECTED_CONTAINER"
  [[ -n "$DETECTED_INTERFACE" ]] && set_env_value WG_INTERFACE "$DETECTED_INTERFACE"
  [[ -n "$DETECTED_PUBLIC_KEY" ]] && set_env_value SERVER_PUBLIC_KEY "$DETECTED_PUBLIC_KEY"
  [[ -n "$DETECTED_SERVER_IP" ]] && set_env_value SERVER_IP "$DETECTED_SERVER_IP"
  [[ -n "$DETECTED_AWG_JC" ]] && set_env_value AWG_JC "$DETECTED_AWG_JC"
  [[ -n "$DETECTED_AWG_JMIN" ]] && set_env_value AWG_JMIN "$DETECTED_AWG_JMIN"
  [[ -n "$DETECTED_AWG_JMAX" ]] && set_env_value AWG_JMAX "$DETECTED_AWG_JMAX"
  [[ -n "$DETECTED_AWG_S1" ]] && set_env_value AWG_S1 "$DETECTED_AWG_S1"
  [[ -n "$DETECTED_AWG_S2" ]] && set_env_value AWG_S2 "$DETECTED_AWG_S2"
  [[ -n "$DETECTED_AWG_S3" ]] && set_env_value AWG_S3 "$DETECTED_AWG_S3"
  [[ -n "$DETECTED_AWG_S4" ]] && set_env_value AWG_S4 "$DETECTED_AWG_S4"
  [[ -n "$DETECTED_AWG_H1" ]] && set_env_value AWG_H1 "$DETECTED_AWG_H1"
  [[ -n "$DETECTED_AWG_H2" ]] && set_env_value AWG_H2 "$DETECTED_AWG_H2"
  [[ -n "$DETECTED_AWG_H3" ]] && set_env_value AWG_H3 "$DETECTED_AWG_H3"
  [[ -n "$DETECTED_AWG_H4" ]] && set_env_value AWG_H4 "$DETECTED_AWG_H4"
  [[ -n "$DETECTED_AWG_I1" ]] && set_env_value AWG_I1 "$DETECTED_AWG_I1"
  [[ -n "$DETECTED_AWG_I2" ]] && set_env_value AWG_I2 "$DETECTED_AWG_I2"
  [[ -n "$DETECTED_AWG_I3" ]] && set_env_value AWG_I3 "$DETECTED_AWG_I3"
  [[ -n "$DETECTED_AWG_I4" ]] && set_env_value AWG_I4 "$DETECTED_AWG_I4"
  [[ -n "$DETECTED_AWG_I5" ]] && set_env_value AWG_I5 "$DETECTED_AWG_I5"
}

configure_manual_awg_only() {
  local value host port default_endpoint
  value="$(prompt_with_default 'DOCKER_CONTAINER' "$(pick_existing_or_default "$(get_env_value DOCKER_CONTAINER)" "$DETECTED_CONTAINER")")"
  set_env_value DOCKER_CONTAINER "$value"

  value="$(prompt_with_default 'WG_INTERFACE' "$(pick_existing_or_default "$(get_env_value WG_INTERFACE)" "$DETECTED_INTERFACE")")"
  set_env_value WG_INTERFACE "$value"

  value="$(prompt_with_default 'SERVER_PUBLIC_KEY' "$(pick_existing_or_default "$(get_env_value SERVER_PUBLIC_KEY)" "$DETECTED_PUBLIC_KEY")")"
  set_env_value SERVER_PUBLIC_KEY "$value"

  host="$(get_public_host)"
  port="${DETECTED_LISTEN_PORT:-}"
  default_endpoint="$(pick_existing_or_default "$(get_env_value SERVER_IP)" "$DETECTED_SERVER_IP")"
  if [[ -z "$default_endpoint" && -n "$host" && -n "$port" ]]; then
    default_endpoint="${host}:${port}"
  fi
  value="$(prompt_with_default 'SERVER_IP (host:port)' "$default_endpoint")"
  set_env_value SERVER_IP "$value"
}

configure_auto_install() {
  local api_token admin_id server_name secret

  api_token="$(prompt_api_token)"
  admin_id="$(prompt_admin_id)"
  server_name="$(prompt_with_default 'Введите название сервера' "$(pick_existing_or_default "$(get_env_value SERVER_NAME)" "$DETECTED_SERVER_NAME")")"
  secret="$(ensure_secret)"

  write_common_env "$api_token" "$admin_id" "$server_name" "$secret"
  write_detected_awg_env

  if [[ -z "$(get_env_value SERVER_PUBLIC_KEY)" || -z "$(get_env_value SERVER_IP)" ]]; then
    warn "Не всё удалось определить автоматически из AWG. Сейчас будут запрошены только недостающие значения."
    configure_manual_awg_only
  fi
}

configure_manual_install() {
  local api_token admin_id server_name secret value default
  api_token="$(prompt_api_token)"
  admin_id="$(prompt_admin_id)"
  server_name="$(prompt_with_default 'Введите название сервера' "$(pick_existing_or_default "$(get_env_value SERVER_NAME)" "$DETECTED_SERVER_NAME")")"
  secret="$(ensure_secret)"
  write_common_env "$api_token" "$admin_id" "$server_name" "$secret"

  configure_manual_awg_only

  default="$(pick_existing_or_default "$(get_env_value STARS_PRICE_7_DAYS)" "15")"
  value="$(prompt_with_default 'Цена 7 дней в Telegram Stars' "$default")"
  set_env_value STARS_PRICE_7_DAYS "$value"

  default="$(pick_existing_or_default "$(get_env_value STARS_PRICE_30_DAYS)" "50")"
  value="$(prompt_with_default 'Цена 30 дней в Telegram Stars' "$default")"
  set_env_value STARS_PRICE_30_DAYS "$value"

  default="$(pick_existing_or_default "$(get_env_value DOWNLOAD_URL)" "https://amnezia.org")"
  value="$(prompt_with_default 'Ссылка на Amnezia / страницу скачивания' "$default")"
  set_env_value DOWNLOAD_URL "$value"

  default="$(get_env_value SUPPORT_USERNAME)"
  value="$(prompt_with_default 'Username поддержки (можно @username)' "${default:-@support}")"
  set_env_value SUPPORT_USERNAME "$value"
}

ensure_venv_and_requirements() {
  info "Настраиваю Python окружение..."
  if [[ ! -d "$VENV_DIR" ]]; then
    "$PYTHON_BIN" -m venv "$VENV_DIR"
  fi
  "$VENV_DIR/bin/pip" install --upgrade pip wheel
  "$VENV_DIR/bin/pip" install -r "$BOT_DIR/requirements.txt"
}

write_service() {
  mkdir -p "$APP_LOG_DIR"
  touch "$APP_LOG_FILE"
  chmod 640 "$APP_LOG_FILE" || true

  cat > "$SERVICE_FILE" <<SERVICE
[Unit]
Description=AWG Telegram Bot
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
Environment=PYTHONUNBUFFERED=1
ExecStart=${VENV_DIR}/bin/python -u ${BOT_DIR}/app.py
Restart=always
RestartSec=3
User=root
StandardOutput=append:${APP_LOG_FILE}
StandardError=append:${APP_LOG_FILE}

[Install]
WantedBy=multi-user.target
SERVICE

  systemctl daemon-reload
  systemctl enable "$SERVICE_NAME" >/dev/null
}

persist_remote_sha() {
  local sha
  sha="$(fetch_remote_sha)"
  if [[ -n "$sha" ]]; then
    mkdir -p "$STATE_DIR"
    printf '%s\n' "$sha" > "$VERSION_FILE"
  fi
}

start_service() {
  info "Запускаю сервис..."
  systemctl restart "$SERVICE_NAME"
  sleep 2
}

stop_service_if_exists() {
  if service_exists; then
    systemctl disable --now "$SERVICE_NAME" 2>/dev/null || true
  fi
}

show_status() {
  print_line
  if is_installed; then
    ok "Бот установлен."
    echo "Репозиторий: ${REPO_URL}"
    echo "Папка: ${INSTALL_DIR}"
    echo "Сервис: ${SERVICE_NAME}"
    echo "Версия: $(get_local_sha | cut -c1-12)"
    echo "Статус: $(systemctl is-active "$SERVICE_NAME" 2>/dev/null || true)"
    echo "Автозапуск: $(systemctl is-enabled "$SERVICE_NAME" 2>/dev/null || true)"
    systemctl --no-pager --full status "$SERVICE_NAME" || true
  else
    warn "Бот пока не установлен."
  fi
  print_line
}

check_updates() {
  local remote_sha local_sha
  remote_sha="$(fetch_remote_sha)"
  local_sha="$(get_local_sha)"
  print_line
  echo "Remote: ${remote_sha:-не удалось получить}"
  echo "Local : ${local_sha:-нет локальной версии}"
  if [[ -n "$remote_sha" && -n "$local_sha" && "$remote_sha" == "$local_sha" ]]; then
    ok "Установлена актуальная версия."
  else
    warn "Есть новая версия или локальная версия ещё не зафиксирована."
  fi
  print_line
}

install_or_reinstall_flow() {
  local mode="$1" tmp_dir choice
  print_line
  if [[ "$mode" == "install" ]]; then
    info "Установка AWG Telegram Bot"
  else
    info "Переустановка AWG Telegram Bot"
  fi

  ensure_packages
  ensure_docker_ready || warn "Продолжаю дальше, но автоподбор AWG может не сработать."
  detect_awg_environment
  print_detected_awg_summary
  validate_awg_detection

  if [[ "$mode" == "install" ]]; then
    echo "1) Автоматическая установка"
    echo "2) Ручная установка"
    echo "0) Отмена"
  else
    echo "1) Автоматическая переустановка"
    echo "2) Ручная переустановка"
    echo "0) Отмена"
  fi
  prompt_raw "Выбор: " choice
  case "$choice" in
    1|2) ;;
    *) warn "Действие отменено."; return 0 ;;
  esac

  tmp_dir="$(download_repo)" || return 1
  stop_service_if_exists
  deploy_repo "$tmp_dir" || { rm -rf "$tmp_dir"; return 1; }
  rm -rf "$tmp_dir"
  ensure_env_file

  if [[ "$choice" == "1" ]]; then
    configure_auto_install
  else
    configure_manual_install
  fi

  ensure_venv_and_requirements
  write_service
  persist_remote_sha
  start_service
  ok "Готово. Бот установлен/переустановлен."
  show_status
  echo "Быстрый запуск меню потом: sudo bash ${INSTALL_DIR}/awg-tgbot.sh"
  echo "Или коротко: sudo awg-tgbot"
}

update_bot() {
  if ! is_installed; then
    warn "Бот не установлен."
    return 0
  fi
  print_line
  info "Обновление AWG Telegram Bot"
  ensure_packages
  ensure_docker_ready || warn "Docker сейчас недоступен. Обновление кода продолжится, но автоподбор AWG может быть неполным."
  check_updates

  local tmp_dir api_token admin_id server_name secret
  tmp_dir="$(download_repo)" || return 1
  stop_service_if_exists
  deploy_repo "$tmp_dir" || { rm -rf "$tmp_dir"; return 1; }
  rm -rf "$tmp_dir"
  ensure_env_file

  api_token="$(get_env_value API_TOKEN)"
  admin_id="$(get_env_value ADMIN_ID)"
  server_name="$(pick_existing_or_default "$(get_env_value SERVER_NAME)" "$(hostname 2>/dev/null || echo 'My VPN')")"
  secret="$(ensure_secret)"

  if [[ -z "$api_token" ]]; then api_token="$(prompt_api_token)"; fi
  if [[ -z "$admin_id" ]]; then admin_id="$(prompt_admin_id)"; fi
  write_common_env "$api_token" "$admin_id" "$server_name" "$secret"

  detect_awg_environment
  write_detected_awg_env
  ensure_venv_and_requirements
  write_service
  persist_remote_sha
  start_service
  ok "Обновление завершено."
  show_status
}

remove_bot() {
  print_line
  if ! is_installed && [[ ! -d "$INSTALL_DIR" ]]; then
    warn "Бот уже удалён."
    return 0
  fi
  echo "1) Полностью удалить бота"
  echo "2) Удалить только сервис и venv, оставить .env и базу"
  echo "0) Отмена"
  local choice=""
  prompt_raw "Выбор: " choice
  case "$choice" in
    1)
      if ! confirm "Точно удалить бот, .env, базу и логи?" "N"; then
        warn "Удаление отменено."
        return 0
      fi
      systemctl disable --now "$SERVICE_NAME" 2>/dev/null || true
      rm -f "$SERVICE_FILE"
      systemctl daemon-reload
      systemctl reset-failed
      rm -f "$SELF_SYMLINK"
      rm -rf "$INSTALL_DIR" "$APP_LOG_DIR"
      ok "Бот полностью удалён."
      ;;
    2)
      systemctl disable --now "$SERVICE_NAME" 2>/dev/null || true
      rm -f "$SERVICE_FILE"
      systemctl daemon-reload
      systemctl reset-failed
      rm -rf "$VENV_DIR"
      rm -f "$SELF_SYMLINK"
      ok "Сервис и venv удалены. .env и база сохранены."
      ;;
    *)
      warn "Удаление отменено."
      ;;
  esac
  print_line
}

show_logs() {
  print_line
  if ! is_installed; then
    warn "Бот не установлен."
    print_line
    return 0
  fi
  echo "1) Последние 100 строк journalctl"
  echo "2) Смотреть journalctl -f"
  echo "3) Последние 100 строк bot.log"
  echo "4) Смотреть bot.log в реальном времени"
  echo "0) Назад"
  local choice=""
  prompt_raw "Выбор: " choice
  case "$choice" in
    1) journalctl -u "$SERVICE_NAME" -n 100 --no-pager ;;
    2) journalctl -u "$SERVICE_NAME" -f ;;
    3) tail -n 100 "$APP_LOG_FILE" ;;
    4) tail -f "$APP_LOG_FILE" ;;
    *) ;;
  esac
  print_line
}

print_not_installed_menu() {
  print_line
  echo "AWG Telegram Bot — ${REPO_OWNER}/${REPO_NAME}:${REPO_BRANCH}"
  echo "Бот сейчас не установлен."
  echo "1) Установить"
  echo "2) Отмена / Выход"
  print_line
}

print_installed_menu() {
  print_line
  echo "AWG Telegram Bot — ${REPO_OWNER}/${REPO_NAME}:${REPO_BRANCH}"
  echo "Бот уже установлен."
  echo "1) Переустановить"
  echo "2) Обновить"
  echo "3) Удалить"
  echo "4) Проверить обновления"
  echo "5) Статус"
  echo "6) Логи"
  echo "0) Выход"
  print_line
}

run_action() {
  local action="${1:-}"
  case "$action" in
    install) install_or_reinstall_flow install ;;
    reinstall) install_or_reinstall_flow reinstall ;;
    update) update_bot ;;
    check-updates) check_updates ;;
    status) show_status ;;
    logs) show_logs ;;
    remove|uninstall) remove_bot ;;
    *) return 0 ;;
  esac
}

main_menu() {
  local choice=""
  while true; do
    if is_installed; then
      print_installed_menu
      prompt_raw "Выбери действие: " choice
      case "$choice" in
        1) install_or_reinstall_flow reinstall ;;
        2) update_bot ;;
        3) remove_bot ;;
        4) check_updates ;;
        5) show_status ;;
        6) show_logs ;;
        0) echo "Выход."; exit 0 ;;
        *) warn "Неизвестный пункт меню." ;;
      esac
    else
      print_not_installed_menu
      prompt_raw "Выбери действие: " choice
      case "$choice" in
        1) install_or_reinstall_flow install ;;
        2|0) echo "Выход."; exit 0 ;;
        *) warn "Неизвестный пункт меню." ;;
      esac
    fi
    pause_if_tty
    clear_if_tty
  done
}

require_root
setup_logging

if [[ $# -gt 0 ]]; then
  run_action "$1"
  exit 0
fi

main_menu
