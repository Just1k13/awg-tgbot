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

DETECTED_CONTAINER=""
DETECTED_INTERFACE=""
DETECTED_CONFIG_PATH=""
DETECTED_PUBLIC_KEY=""
DETECTED_LISTEN_PORT=""
DETECTED_SERVER_IP=""
DETECTED_SERVER_NAME=""
DETECTED_PUBLIC_HOST=""
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

print_line() { printf '%s\n' "------------------------------------------------------------"; }
info() { printf '[*] %s\n' "$*" >&2; }
ok() { printf '[+] %s\n' "$*" >&2; }
warn() { printf '[!] %s\n' "$*" >&2; }
die() { warn "$*"; exit 1; }
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

setup_tty_fd() {
  if [[ -r "$TTY_DEVICE" ]]; then
    exec 3<>"$TTY_DEVICE"
  fi
}

has_tty() { [[ -e /proc/$$/fd/3 ]]; }

pause_if_tty() {
  if has_tty; then
    echo
    read -r -u 3 -p "Нажми Enter, чтобы продолжить..." _dummy || true
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
  local __input=""
  if has_tty; then
    if ! read -r -u 3 -p "$prompt" __input; then
      __input=""
    fi
  fi
  printf -v "$__resultvar" '%s' "$__input"
}

prompt_with_default() {
  local prompt="$1"
  local default="${2:-}"
  local __resultvar="$3"
  local value=""
  while true; do
    if [[ -n "$default" ]]; then
      prompt_raw "$prompt [$default]: " value
      value="${value:-$default}"
    else
      prompt_raw "$prompt: " value
    fi
    if [[ -n "$value" ]]; then
      printf -v "$__resultvar" '%s' "$value"
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
  [[ "$default" == "N" ]] && suffix="[y/N]"
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

require_command() { command -v "$1" >/dev/null 2>&1; }
service_exists() { [[ -f "$SERVICE_FILE" ]]; }
is_installed() { [[ -f "$SERVICE_FILE" && -d "$BOT_DIR" && -f "$BOT_DIR/app.py" ]]; }

has_residual_files() {
  [[ -d "$INSTALL_DIR" || -e "$SELF_SYMLINK" || -f "$SERVICE_FILE" ]]
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
  chmod 600 "$ENV_FILE" || true
  local escaped
  escaped="$(printf '%s' "$value" | sed -e 's/[\\/&|]/\\&/g')"
  if grep -q -E "^${key}=" "$ENV_FILE" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${escaped}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
  return 0
}

fetch_remote_sha() {
  local sha=""
  sha="$(curl -fsSL "$COMMIT_API_URL" 2>/dev/null | grep -m1 '"sha"' | sed -E 's/.*"sha": "([a-f0-9]+)".*/\1/' || true)"
  printf '%s' "$sha"
}

get_local_sha() { [[ -f "$VERSION_FILE" ]] && cat "$VERSION_FILE" || true; }

dpkg_lock_free() {
  ! fuser /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/lib/apt/lists/lock /var/cache/apt/archives/lock >/dev/null 2>&1
}

wait_for_apt_locks() {
  local waited=0 max_wait=300
  while ! dpkg_lock_free; do
    if (( waited == 0 )); then
      warn "apt/dpkg сейчас занят другим процессом. Жду освобождения блокировки..."
    fi
    sleep 5
    waited=$((waited + 5))
    if (( waited >= max_wait )); then
      die "Не удалось дождаться освобождения apt/dpkg lock за ${max_wait} секунд. Попробуй позже."
    fi
  done
  return 0
}

apt_get_safe() {
  wait_for_apt_locks
  apt-get "$@"
}

ensure_packages() {
  info "Проверяю и обновляю системные зависимости..."
  export DEBIAN_FRONTEND=noninteractive
  apt_get_safe update -y
  apt_get_safe install -y --no-install-recommends \
    ca-certificates curl tar gzip openssl python3 python3-venv python3-pip iproute2 psmisc
  if ! require_command docker; then
    warn "Docker не найден. Устанавливаю docker.io..."
    apt_get_safe install -y --no-install-recommends docker.io
  fi
  if require_command systemctl && systemctl list-unit-files docker.service >/dev/null 2>&1; then
    systemctl enable --now docker >/dev/null 2>&1 || systemctl start docker >/dev/null 2>&1 || true
    sleep 2
  fi
  return 0
}

docker_is_accessible() { require_command docker && docker ps >/dev/null 2>&1; }

ensure_docker_ready() {
  if docker_is_accessible; then
    return 0
  fi
  if require_command systemctl && systemctl list-unit-files docker.service >/dev/null 2>&1; then
    systemctl enable --now docker >/dev/null 2>&1 || systemctl start docker >/dev/null 2>&1 || true
    sleep 2
  fi
  if ! docker_is_accessible; then
    warn "Docker недоступен. Проверь, что docker установлен и daemon запущен."
    warn "Подсказка: systemctl status docker --no-pager"
    return 1
  fi
  return 0
}

pick_existing_or_default() {
  local current="$1" fallback="$2"
  if [[ -n "$current" ]]; then printf '%s' "$current"; else printf '%s' "$fallback"; fi
}

is_public_ipv4() {
  local value="$1"
  "$PYTHON_BIN" - "$value" <<'PY'
import ipaddress, sys
value = sys.argv[1].strip()
try:
    addr = ipaddress.ip_address(value)
    ok = addr.version == 4 and not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_unspecified or addr.is_reserved)
    print('1' if ok else '0')
except Exception:
    print('0')
PY
}

is_hostname_like() {
  local value="$1"
  "$PYTHON_BIN" - "$value" <<'PY'
import re, sys
value = sys.argv[1].strip()
ok = bool(value) and ' ' not in value and ':' not in value and len(value) <= 253 and value.lower() != 'localhost' and bool(re.fullmatch(r'[A-Za-z0-9.-]+', value))
print('1' if ok else '0')
PY
}

docker_exec_capture() {
  local container="$1"; shift
  docker exec -i "$container" "$@" 2>/dev/null || true
}

docker_exec_sh() {
  local container="$1" command="$2"
  docker exec -i "$container" sh -lc "$command" 2>/dev/null || true
}

find_awg_container() {
  local current lines line name image haystack score best_score=0 best_name=""
  current="$(get_env_value DOCKER_CONTAINER)"
  if [[ -n "$current" ]] && docker_is_accessible && docker inspect "$current" >/dev/null 2>&1; then
    printf '%s' "$current"
    return 0
  fi
  if ! docker_is_accessible; then
    printf '%s' "$current"
    return 0
  fi
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

extract_awg_show_value() {
  local label="$1" content="$2"
  awk -F': ' -v k="$label" '$1 == k {print substr($0, index($0, ": ")+2); exit}' <<< "$content"
}

parse_conf_value() {
  local key="$1" content="$2"
  awk -v key="$key" '
    function trim(s) { sub(/^[ \t]+/, "", s); sub(/[ \t\r]+$/, "", s); return s }
    $0 ~ "^[[:space:]]*" key "[[:space:]]*=" {
      val=$0
      sub(/^[^=]*=/, "", val)
      print trim(val)
      exit
    }
  ' <<< "$content"
}

find_awg_config_path() {
  local container="$1" interface_hint="$2" path=""
  if [[ -n "$interface_hint" ]]; then
    path="$(docker_exec_sh "$container" "[ -f '/opt/amnezia/awg/${interface_hint}.conf' ] && printf '%s' '/opt/amnezia/awg/${interface_hint}.conf' || true")"
  fi
  if [[ -z "$path" ]]; then
    path="$(docker_exec_sh "$container" "[ -f '/opt/amnezia/awg/awg0.conf' ] && printf '%s' '/opt/amnezia/awg/awg0.conf' || true")"
  fi
  if [[ -z "$path" ]]; then
    path="$(docker_exec_sh "$container" "find /opt/amnezia -maxdepth 4 -type f -name '*.conf' 2>/dev/null | grep '/awg/' | head -n1 || true")"
  fi
  printf '%s' "$path"
}

derive_public_key_from_private() {
  local container="$1" private_key="$2" out=""
  [[ -n "$private_key" ]] || return 0
  out="$(printf '%s\n' "$private_key" | docker exec -i "$container" awg pubkey 2>/dev/null | tr -d '\r' | head -n1 || true)"
  if [[ -z "$out" ]]; then
    out="$(printf '%s\n' "$private_key" | docker exec -i "$container" wg pubkey 2>/dev/null | tr -d '\r' | head -n1 || true)"
  fi
  printf '%s' "$out"
}

get_public_host() {
  local value route
  for value in "$(get_env_value PUBLIC_HOST)" "$(get_env_value SERVER_HOST)" "$(get_env_value SERVER_DOMAIN)" "${PUBLIC_HOST:-}" "${SERVER_HOST:-}" "${SERVER_DOMAIN:-}"; do
    value="$(printf '%s' "$value" | tr -d '[:space:]')"
    [[ -z "$value" ]] && continue
    if [[ "$(is_public_ipv4 "$value")" == "1" ]]; then
      printf '%s' "$value"
      return 0
    fi
  done
  if require_command curl; then
    local url
    for url in 'https://api.ipify.org' 'https://ifconfig.me/ip' 'https://ipv4.icanhazip.com'; do
      value="$(curl -4 -fsSL --connect-timeout 5 "$url" 2>/dev/null | tr -d '[:space:]' || true)"
      if [[ "$(is_public_ipv4 "$value")" == "1" ]]; then
        printf '%s' "$value"
        return 0
      fi
    done
  fi
  route="$(ip -4 route get 1.1.1.1 2>/dev/null || true)"
  value="$(grep -oE '\bsrc\s+[0-9.]+\b' <<< "$route" | awk '{print $2}' | head -n1 || true)"
  if [[ "$(is_public_ipv4 "$value")" == "1" ]]; then
    printf '%s' "$value"
    return 0
  fi
  printf '%s' ""
}

detect_awg_environment() {
  DETECTED_CONTAINER=""
  DETECTED_INTERFACE=""
  DETECTED_CONFIG_PATH=""
  DETECTED_PUBLIC_KEY=""
  DETECTED_LISTEN_PORT=""
  DETECTED_SERVER_IP=""
  DETECTED_SERVER_NAME=""
  DETECTED_PUBLIC_HOST=""
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

  local configured_container configured_interface show_output conf_output private_key interface_name
  configured_container="$(get_env_value DOCKER_CONTAINER)"
  configured_interface="$(get_env_value WG_INTERFACE)"
  DETECTED_CONTAINER="$(pick_existing_or_default "$configured_container" "$(find_awg_container)")"
  DETECTED_INTERFACE="${configured_interface:-awg0}"
  DETECTED_SERVER_NAME="$(pick_existing_or_default "$(get_env_value SERVER_NAME)" "$(hostname -f 2>/dev/null || hostname 2>/dev/null || echo 'My VPN')")"
  DETECTED_PUBLIC_HOST="$(get_public_host)"

  if [[ -n "$DETECTED_CONTAINER" ]] && docker_is_accessible && docker inspect "$DETECTED_CONTAINER" >/dev/null 2>&1; then
    show_output="$(docker_exec_capture "$DETECTED_CONTAINER" awg show "$DETECTED_INTERFACE")"
    [[ -n "$show_output" ]] || show_output="$(docker_exec_capture "$DETECTED_CONTAINER" awg show)"

    interface_name="$(extract_awg_show_value 'interface' "$show_output")"
    [[ -n "$interface_name" ]] && DETECTED_INTERFACE="$interface_name"

    DETECTED_PUBLIC_KEY="$(extract_awg_show_value 'public key' "$show_output")"
    DETECTED_LISTEN_PORT="$(extract_awg_show_value 'listening port' "$show_output")"
    DETECTED_CONFIG_PATH="$(find_awg_config_path "$DETECTED_CONTAINER" "$DETECTED_INTERFACE")"
    if [[ -n "$DETECTED_CONFIG_PATH" ]]; then
      conf_output="$(docker_exec_sh "$DETECTED_CONTAINER" "cat '$DETECTED_CONFIG_PATH'")"
      [[ -n "$DETECTED_LISTEN_PORT" ]] || DETECTED_LISTEN_PORT="$(parse_conf_value 'ListenPort' "$conf_output")"
      if [[ -z "$DETECTED_PUBLIC_KEY" ]]; then
        private_key="$(parse_conf_value 'PrivateKey' "$conf_output")"
        private_key="$(printf '%s' "$private_key" | tr -d '\r' | xargs 2>/dev/null || true)"
        DETECTED_PUBLIC_KEY="$(derive_public_key_from_private "$DETECTED_CONTAINER" "$private_key")"
      fi
      DETECTED_AWG_JC="$(parse_conf_value 'Jc' "$conf_output")"
      DETECTED_AWG_JMIN="$(parse_conf_value 'Jmin' "$conf_output")"
      DETECTED_AWG_JMAX="$(parse_conf_value 'Jmax' "$conf_output")"
      DETECTED_AWG_S1="$(parse_conf_value 'S1' "$conf_output")"
      DETECTED_AWG_S2="$(parse_conf_value 'S2' "$conf_output")"
      DETECTED_AWG_S3="$(parse_conf_value 'S3' "$conf_output")"
      DETECTED_AWG_S4="$(parse_conf_value 'S4' "$conf_output")"
      DETECTED_AWG_H1="$(parse_conf_value 'H1' "$conf_output")"
      DETECTED_AWG_H2="$(parse_conf_value 'H2' "$conf_output")"
      DETECTED_AWG_H3="$(parse_conf_value 'H3' "$conf_output")"
      DETECTED_AWG_H4="$(parse_conf_value 'H4' "$conf_output")"
      DETECTED_AWG_I1="$(parse_conf_value 'I1' "$conf_output")"
      DETECTED_AWG_I2="$(parse_conf_value 'I2' "$conf_output")"
      DETECTED_AWG_I3="$(parse_conf_value 'I3' "$conf_output")"
      DETECTED_AWG_I4="$(parse_conf_value 'I4' "$conf_output")"
      DETECTED_AWG_I5="$(parse_conf_value 'I5' "$conf_output")"
    fi
  fi

  if [[ -z "$DETECTED_PUBLIC_HOST" ]]; then
    DETECTED_PUBLIC_HOST="$(printf '%s' "$DETECTED_SERVER_NAME" | tr -d '[:space:]')"
    if [[ "$(is_hostname_like "$DETECTED_PUBLIC_HOST")" != "1" ]]; then
      DETECTED_PUBLIC_HOST=""
    fi
  fi

  if [[ -n "$DETECTED_PUBLIC_HOST" && -n "$DETECTED_LISTEN_PORT" ]]; then
    DETECTED_SERVER_IP="${DETECTED_PUBLIC_HOST}:${DETECTED_LISTEN_PORT}"
  else
    DETECTED_SERVER_IP="$(get_env_value SERVER_IP)"
  fi
}

print_detected_awg_summary() {
  print_line
  echo "Автоподбор AWG:"
  echo "Контейнер: ${DETECTED_CONTAINER:-не найден}"
  echo "Интерфейс: ${DETECTED_INTERFACE:-не найден}"
  echo "Конфиг: ${DETECTED_CONFIG_PATH:-не найден}"
  echo "Public key: ${DETECTED_PUBLIC_KEY:-не найден}"
  echo "Endpoint: ${DETECTED_SERVER_IP:-не найден}"
  echo "Имя сервера: ${DETECTED_SERVER_NAME:-не найдено}"
  print_line
  [[ -z "$DETECTED_PUBLIC_KEY" ]] && warn "Не удалось автоматически определить SERVER_PUBLIC_KEY."
  [[ -z "$DETECTED_SERVER_IP" ]] && warn "Не удалось автоматически определить внешний SERVER_IP."
  [[ -z "$DETECTED_PUBLIC_HOST" ]] && warn "Если внешний IP не определился — укажи PUBLIC_HOST / внешний IP вручную."
  return 0
}

download_repo() {
  local tmp_dir src_dir
  tmp_dir="$(mktemp -d)"
  info "Скачиваю код из ${REPO_URL} (${REPO_BRANCH})..."
  curl -fsSL --connect-timeout 20 --retry 3 --retry-delay 1 "$TARBALL_URL" -o "$tmp_dir/repo.tar.gz"
  tar -xzf "$tmp_dir/repo.tar.gz" -C "$tmp_dir"
  src_dir="$(find "$tmp_dir" -mindepth 1 -maxdepth 1 -type d | head -n1 || true)"
  if [[ -z "$src_dir" || ! -d "$src_dir/bot" || ! -f "$src_dir/awg-tgbot.sh" ]]; then
    warn "Не удалось скачать корректную структуру репозитория."
    ls -la "$tmp_dir" >&2 || true
    [[ -n "$src_dir" ]] && ls -la "$src_dir" >&2 || true
    rm -rf "$tmp_dir"
    return 1
  fi
  printf '%s' "$tmp_dir"
}

deploy_repo() {
  local tmp_dir="$1" src_dir backup_dir=""
  src_dir="$(find "$tmp_dir" -mindepth 1 -maxdepth 1 -type d | head -n1 || true)"
  if [[ -z "$src_dir" || ! -d "$src_dir/bot" || ! -f "$src_dir/awg-tgbot.sh" ]]; then
    warn "Не найдены файлы репозитория для развёртывания."
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
    chmod 600 "$ENV_FILE" || true
  fi
  return 0
}

ensure_secret() {
  local current secret
  current="$(get_env_value ENCRYPTION_SECRET)"
  if [[ -n "$current" ]]; then printf '%s' "$current"; return 0; fi
  if require_command openssl; then
    secret="$(openssl rand -hex 32)"
  else
    secret="$($PYTHON_BIN - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
)"
  fi
  printf '%s' "$secret"
}

prompt_api_token() {
  local __resultvar="$1" __token=""
  while true; do
    prompt_with_default 'Введите токен Telegram-бота' '' __token
    if [[ "$__token" == *:* ]]; then
      printf -v "$__resultvar" '%s' "$__token"
      return 0
    fi
    warn "Нужен токен в формате 123456:ABCDEF..."
  done
}

prompt_admin_id() {
  local __resultvar="$1" __admin_input=""
  while true; do
    prompt_with_default 'Введите Telegram user_id администратора' '' __admin_input
    if [[ "$__admin_input" =~ ^[0-9]+$ ]]; then
      printf -v "$__resultvar" '%s' "$__admin_input"
      return 0
    fi
    warn "ADMIN_ID должен быть числом."
  done
}

write_common_env() {
  local api_token="$1" admin_id="$2" server_name="$3" secret="$4"
  set_env_value API_TOKEN "$api_token"
  set_env_value ADMIN_ID "$admin_id"
  set_env_value SERVER_NAME "$server_name"
  set_env_value ENCRYPTION_SECRET "$secret"
  set_env_value DB_PATH "vpn_bot.db"
  return 0
}

write_detected_awg_env() {
  [[ -n "$DETECTED_CONTAINER" ]] && set_env_value DOCKER_CONTAINER "$DETECTED_CONTAINER"
  [[ -n "$DETECTED_INTERFACE" ]] && set_env_value WG_INTERFACE "$DETECTED_INTERFACE"
  [[ -n "$DETECTED_PUBLIC_KEY" ]] && set_env_value SERVER_PUBLIC_KEY "$DETECTED_PUBLIC_KEY"
  [[ -n "$DETECTED_SERVER_IP" ]] && set_env_value SERVER_IP "$DETECTED_SERVER_IP"
  [[ -n "$DETECTED_PUBLIC_HOST" ]] && set_env_value PUBLIC_HOST "$DETECTED_PUBLIC_HOST"
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
  return 0
}

configure_manual_awg_only() {
  local value default
  default="$(pick_existing_or_default "$(get_env_value DOCKER_CONTAINER)" "$DETECTED_CONTAINER")"
  prompt_with_default 'DOCKER_CONTAINER' "$default" value
  set_env_value DOCKER_CONTAINER "$value"
  default="$(pick_existing_or_default "$(get_env_value WG_INTERFACE)" "$DETECTED_INTERFACE")"
  prompt_with_default 'WG_INTERFACE' "$default" value
  set_env_value WG_INTERFACE "$value"
  default="$(pick_existing_or_default "$(get_env_value SERVER_PUBLIC_KEY)" "$DETECTED_PUBLIC_KEY")"
  prompt_with_default 'SERVER_PUBLIC_KEY' "$default" value
  set_env_value SERVER_PUBLIC_KEY "$value"
  default="$(pick_existing_or_default "$(get_env_value PUBLIC_HOST)" "$DETECTED_PUBLIC_HOST")"
  prompt_with_default 'PUBLIC_HOST / внешний IP' "$default" value
  set_env_value PUBLIC_HOST "$value"
  default="$(pick_existing_or_default "$(get_env_value SERVER_IP)" "$DETECTED_SERVER_IP")"
  prompt_with_default 'SERVER_IP (IP:port)' "$default" value
  set_env_value SERVER_IP "$value"
  return 0
}

configure_auto_install() {
  local api_token admin_id server_name secret value default
  prompt_api_token api_token
  prompt_admin_id admin_id
  default="$(pick_existing_or_default "$(get_env_value SERVER_NAME)" "$DETECTED_SERVER_NAME")"
  prompt_with_default 'Введите название сервера' "$default" server_name
  secret="$(ensure_secret)"
  write_common_env "$api_token" "$admin_id" "$server_name" "$secret"
  write_detected_awg_env
  if [[ -z "$(get_env_value SERVER_PUBLIC_KEY)" ]]; then
    warn "Не удалось автоматически определить SERVER_PUBLIC_KEY. Нужен один ручной шаг."
    prompt_with_default 'SERVER_PUBLIC_KEY' "$DETECTED_PUBLIC_KEY" value
    set_env_value SERVER_PUBLIC_KEY "$value"
  fi
  if [[ -z "$(get_env_value SERVER_IP)" ]]; then
    warn "Не удалось автоматически определить SERVER_IP. Укажи внешний IP и порт."
    default="$(pick_existing_or_default "$(get_env_value PUBLIC_HOST)" "$DETECTED_PUBLIC_HOST")"
    prompt_with_default 'PUBLIC_HOST / внешний IP' "$default" value
    set_env_value PUBLIC_HOST "$value"
    if [[ -n "$DETECTED_LISTEN_PORT" && -n "$value" ]]; then
      set_env_value SERVER_IP "${value}:${DETECTED_LISTEN_PORT}"
    else
      prompt_with_default 'SERVER_IP (IP:port)' "$DETECTED_SERVER_IP" value
      set_env_value SERVER_IP "$value"
    fi
  fi
  return 0
}

configure_manual_install() {
  local api_token admin_id server_name secret value default
  prompt_api_token api_token
  prompt_admin_id admin_id
  default="$(pick_existing_or_default "$(get_env_value SERVER_NAME)" "$DETECTED_SERVER_NAME")"
  prompt_with_default 'Введите название сервера' "$default" server_name
  secret="$(ensure_secret)"
  write_common_env "$api_token" "$admin_id" "$server_name" "$secret"
  configure_manual_awg_only
  default="$(pick_existing_or_default "$(get_env_value STARS_PRICE_7_DAYS)" "15")"
  prompt_with_default 'Цена 7 дней в Telegram Stars' "$default" value
  set_env_value STARS_PRICE_7_DAYS "$value"
  default="$(pick_existing_or_default "$(get_env_value STARS_PRICE_30_DAYS)" "50")"
  prompt_with_default 'Цена 30 дней в Telegram Stars' "$default" value
  set_env_value STARS_PRICE_30_DAYS "$value"
  default="$(pick_existing_or_default "$(get_env_value DOWNLOAD_URL)" "https://amnezia.org")"
  prompt_with_default 'Ссылка на Amnezia / инструкцию скачивания' "$default" value
  set_env_value DOWNLOAD_URL "$value"
  default="$(get_env_value SUPPORT_USERNAME)"
  prompt_with_default 'Username поддержки (можно @username)' "${default:-@support}" value
  set_env_value SUPPORT_USERNAME "$value"
  return 0
}

ensure_venv_and_requirements() {
  info "Настраиваю Python окружение..."
  [[ -d "$VENV_DIR" ]] || "$PYTHON_BIN" -m venv "$VENV_DIR" || return 1
  "$VENV_DIR/bin/pip" install --upgrade pip wheel || return 1
  "$VENV_DIR/bin/pip" install -r "$BOT_DIR/requirements.txt" || return 1
  return 0
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
  return 0
}

persist_remote_sha() {
  local sha
  sha="$(fetch_remote_sha)"
  if [[ -n "$sha" ]]; then
    mkdir -p "$STATE_DIR"
    printf '%s\n' "$sha" > "$VERSION_FILE"
  fi
  return 0
}

start_service() {
  info "Запускаю сервис..."
  systemctl restart "$SERVICE_NAME"
  sleep 2
  return 0
}

stop_service_if_exists() {
  if service_exists; then
    systemctl disable --now "$SERVICE_NAME" 2>/dev/null || true
  fi
  return 0
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
  return 0
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
  return 0
}

install_or_reinstall_flow() {
  local mode="$1" tmp_dir choice
  print_line
  if [[ "$mode" == "install" ]]; then
    info "Установка AWG Telegram Bot"
    echo "1) Автоматическая установка"
    echo "2) Ручная установка"
    echo "0) Отмена"
  else
    info "Переустановка AWG Telegram Bot"
    echo "1) Автоматическая переустановка"
    echo "2) Ручная переустановка"
    echo "0) Отмена"
  fi
  prompt_raw "Выбор: " choice
  case "$choice" in
    1|2) ;;
    *) warn "Действие отменено."; return 0 ;;
  esac

  ensure_packages || die "Не удалось установить системные зависимости."
  ensure_docker_ready || die "Docker недоступен."
  detect_awg_environment
  print_detected_awg_summary

  tmp_dir="$(download_repo)" || die "Не удалось скачать код проекта из GitHub."
  stop_service_if_exists
  deploy_repo "$tmp_dir" || { rm -rf "$tmp_dir"; die "Не удалось развернуть файлы проекта."; }
  rm -rf "$tmp_dir"
  ensure_env_file

  detect_awg_environment
  print_detected_awg_summary
  if [[ "$choice" == "1" ]]; then
    configure_auto_install || die "Автоматическая настройка не завершилась."
  else
    configure_manual_install || die "Ручная настройка не завершилась."
  fi

  ensure_venv_and_requirements || die "Не удалось установить Python зависимости."
  write_service || die "Не удалось создать systemd сервис."
  persist_remote_sha
  start_service || die "Не удалось запустить сервис."
  ok "Готово. Бот установлен/переустановлен."
  show_status
  echo "Быстрый запуск меню потом: sudo bash ${INSTALL_DIR}/awg-tgbot.sh"
  echo "Или коротко: sudo awg-tgbot"
  return 0
}

update_bot() {
  local tmp_dir api_token admin_id server_name secret
  if ! is_installed; then
    warn "Бот не установлен."
    return 0
  fi
  print_line
  info "Обновление AWG Telegram Bot"
  ensure_packages || die "Не удалось обновить системные зависимости."
  ensure_docker_ready || die "Docker недоступен."
  check_updates

  tmp_dir="$(download_repo)" || die "Не удалось скачать код проекта из GitHub."
  stop_service_if_exists
  deploy_repo "$tmp_dir" || { rm -rf "$tmp_dir"; die "Не удалось развернуть обновление."; }
  rm -rf "$tmp_dir"
  ensure_env_file

  api_token="$(get_env_value API_TOKEN)"
  admin_id="$(get_env_value ADMIN_ID)"
  server_name="$(pick_existing_or_default "$(get_env_value SERVER_NAME)" "$(hostname -f 2>/dev/null || hostname 2>/dev/null || echo 'My VPN')")"
  secret="$(ensure_secret)"
  [[ -n "$api_token" ]] || prompt_api_token api_token
  [[ -n "$admin_id" ]] || prompt_admin_id admin_id
  write_common_env "$api_token" "$admin_id" "$server_name" "$secret"

  detect_awg_environment
  write_detected_awg_env
  ensure_venv_and_requirements || die "Не удалось обновить Python зависимости."
  write_service || die "Не удалось обновить systemd сервис."
  persist_remote_sha
  start_service || die "Не удалось перезапустить сервис."
  ok "Обновление завершено."
  show_status
  return 0
}

remove_everything() {
  systemctl disable --now "$SERVICE_NAME" 2>/dev/null || true
  rm -f "$SERVICE_FILE"
  systemctl daemon-reload || true
  systemctl reset-failed || true
  rm -f "$SELF_SYMLINK"
  rm -rf "$INSTALL_DIR" "$APP_LOG_DIR"
  rm -f "$INSTALL_LOG"
  return 0
}

remove_keep_db_and_env() {
  local db_path db_file db_tmp env_tmp restored_dir
  db_path="$(get_env_value DB_PATH)"
  [[ -n "$db_path" ]] || db_path="vpn_bot.db"
  if [[ "$db_path" = /* ]]; then
    db_file="$db_path"
  else
    db_file="$INSTALL_DIR/$db_path"
  fi
  db_tmp=""
  env_tmp=""
  if [[ -f "$db_file" ]]; then
    db_tmp="$(mktemp)"
    cp -a "$db_file" "$db_tmp"
  fi
  if [[ -f "$ENV_FILE" ]]; then
    env_tmp="$(mktemp)"
    cp -a "$ENV_FILE" "$env_tmp"
  fi
  remove_everything
  mkdir -p "$INSTALL_DIR"
  chmod 755 "$INSTALL_DIR" || true
  if [[ -n "$db_tmp" && -f "$db_tmp" ]]; then
    if [[ "$db_path" = /* ]]; then
      restored_dir="$(dirname "$db_path")"
      mkdir -p "$restored_dir"
      cp -a "$db_tmp" "$db_path"
    else
      cp -a "$db_tmp" "$INSTALL_DIR/$db_path"
    fi
    rm -f "$db_tmp"
  fi
  if [[ -n "$env_tmp" && -f "$env_tmp" ]]; then
    cp -a "$env_tmp" "$ENV_FILE"
    chmod 600 "$ENV_FILE" || true
    rm -f "$env_tmp"
  fi
  return 0
}

remove_bot() {
  local choice=""
  print_line
  if ! has_residual_files; then
    warn "Бот уже удалён."
    return 0
  fi
  echo "1) Удалить всё"
  echo "2) Удалить всё, кроме БД и .env (нужно для расшифровки данных)"
  echo "0) Отмена"
  prompt_raw "Выбор: " choice
  case "$choice" in
    1)
      if ! confirm "Точно удалить все файлы, БД и логи?" "N"; then
        warn "Удаление отменено."
        return 0
      fi
      remove_everything
      ok "Удалено всё."
      ;;
    2)
      if ! confirm "Точно удалить всё, кроме БД и .env?" "N"; then
        warn "Удаление отменено."
        return 0
      fi
      remove_keep_db_and_env
      ok "Удалено всё, кроме БД и .env."
      ;;
    *)
      warn "Удаление отменено."
      ;;
  esac
  print_line
  return 0
}

show_logs() {
  local choice=""
  print_line
  if ! has_residual_files; then
    warn "Бот не установлен."
    print_line
    return 0
  fi
  echo "1) Последние 100 строк journalctl"
  echo "2) Смотреть journalctl -f"
  echo "3) Последние 100 строк bot.log"
  echo "4) Смотреть bot.log в реальном времени"
  echo "0) Назад"
  prompt_raw "Выбор: " choice
  case "$choice" in
    1) journalctl -u "$SERVICE_NAME" -n 100 --no-pager || true ;;
    2) journalctl -u "$SERVICE_NAME" -f || true ;;
    3) tail -n 100 "$APP_LOG_FILE" || true ;;
    4) tail -f "$APP_LOG_FILE" || true ;;
    *) ;;
  esac
  print_line
  return 0
}

print_not_installed_menu() {
  print_line
  echo "AWG Telegram Bot — ${REPO_OWNER}/${REPO_NAME}:${REPO_BRANCH}"
  echo "Бот сейчас не установлен."
  echo "1) Установить"
  echo "2) Отмена / Выход"
  print_line
}

print_residual_menu() {
  print_line
  echo "AWG Telegram Bot — ${REPO_OWNER}/${REPO_NAME}:${REPO_BRANCH}"
  echo "Найдены остаточные файлы прошлой установки."
  echo "1) Продолжить установку / переустановку поверх остатков"
  echo "2) Удалить всё"
  echo "3) Удалить всё, кроме БД и .env"
  echo "0) Выход"
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
    elif has_residual_files; then
      print_residual_menu
      prompt_raw "Выбери действие: " choice
      case "$choice" in
        1) install_or_reinstall_flow install ;;
        2) remove_everything; ok "Удалено всё." ;;
        3) remove_keep_db_and_env; ok "Удалено всё, кроме БД и .env." ;;
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
setup_tty_fd

if [[ $# -gt 0 ]]; then
  run_action "$1"
  exit 0
fi

main_menu
