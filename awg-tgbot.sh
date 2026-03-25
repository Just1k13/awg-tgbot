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

if [[ "${EUID}" -ne 0 ]]; then
  echo "Запусти скрипт от root: sudo bash awg-tgbot.sh"
  echo "Или одной командой: curl -fsSL ${RAW_BASE_URL}/awg-tgbot.sh | sudo bash"
  exit 1
fi

mkdir -p "$(dirname "$INSTALL_LOG")" "$APP_LOG_DIR"
touch "$INSTALL_LOG" "$APP_LOG_FILE"
chmod 640 "$INSTALL_LOG" "$APP_LOG_FILE" || true
exec > >(tee -a "$INSTALL_LOG") 2>&1

trap 'echo "[!] Ошибка на строке ${LINENO}. Подробности: ${INSTALL_LOG}"' ERR

print_line() {
  printf '%s\n' "------------------------------------------------------------"
}

info() {
  printf '[*] %s\n' "$*"
}

ok() {
  printf '[+] %s\n' "$*"
}

warn() {
  printf '[!] %s\n' "$*"
}

has_tty() {
  [[ -r "$TTY_DEVICE" ]]
}

prompt_raw() {
  local prompt="$1"
  local __resultvar="$2"
  local value=""
  if has_tty; then
    read -r -p "$prompt" value < "$TTY_DEVICE"
  else
    value=""
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

is_installed() {
  [[ -f "$SERVICE_FILE" && -d "$BOT_DIR" && -f "$BOT_DIR/app.py" ]]
}

ensure_packages() {
  info "Проверяю системные зависимости..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y --no-install-recommends \
    ca-certificates curl tar gzip openssl python3 python3-venv python3-pip
  if ! require_command docker; then
    warn "Docker не найден. Устанавливаю docker.io..."
    apt-get install -y --no-install-recommends docker.io
  fi
}

download_repo() {
  local tmp_dir
  tmp_dir="$(mktemp -d)"
  info "Скачиваю код из ${REPO_URL} (${REPO_BRANCH})..."
  curl -fsSL "$TARBALL_URL" -o "$tmp_dir/repo.tar.gz"
  tar -xzf "$tmp_dir/repo.tar.gz" -C "$tmp_dir"
  local src_dir
  src_dir="$(find "$tmp_dir" -mindepth 1 -maxdepth 1 -type d | head -n1)"
  if [[ -z "$src_dir" || ! -d "$src_dir/bot" || ! -f "$src_dir/awg-tgbot.sh" ]]; then
    rm -rf "$tmp_dir"
    warn "Не удалось скачать корректную структуру репозитория."
    exit 1
  fi
  printf '%s' "$tmp_dir"
}

deploy_repo() {
  local tmp_dir="$1"
  local src_dir
  src_dir="$(find "$tmp_dir" -mindepth 1 -maxdepth 1 -type d | head -n1)"
  mkdir -p "$INSTALL_DIR" "$STATE_DIR"

  local backup_dir=""
  if [[ -d "$BOT_DIR" ]]; then
    backup_dir="$(mktemp -d "${INSTALL_DIR}/.backup.XXXXXX")"
    mv "$BOT_DIR" "$backup_dir/bot"
  fi

  if cp -a "$src_dir/bot" "$BOT_DIR" && cp -a "$src_dir/awg-tgbot.sh" "$INSTALL_DIR/awg-tgbot.sh"; then
    chmod +x "$INSTALL_DIR/awg-tgbot.sh"
    ln -sf "$INSTALL_DIR/awg-tgbot.sh" "$SELF_SYMLINK"
    if [[ -n "$backup_dir" ]]; then
      rm -rf "$backup_dir"
    fi
  else
    rm -rf "$BOT_DIR"
    if [[ -n "$backup_dir" && -d "$backup_dir/bot" ]]; then
      mv "$backup_dir/bot" "$BOT_DIR"
      rm -rf "$backup_dir"
    fi
    warn "Не удалось развернуть файлы репозитория."
    exit 1
  fi
}

ensure_env_template() {
  if [[ ! -f "$ENV_FILE" ]]; then
    if [[ -f "$BOT_DIR/.env.example" ]]; then
      cp "$BOT_DIR/.env.example" "$ENV_FILE"
    else
      touch "$ENV_FILE"
    fi
    chmod 600 "$ENV_FILE"
  fi
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
    secret="$(python3 - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
)"
  fi
  printf '%s' "$secret"
}

write_base_env() {
  local api_token="$1"
  local admin_id="$2"
  local secret="$3"

  set_env_value API_TOKEN "$api_token"
  set_env_value ADMIN_ID "$admin_id"
  set_env_value ENCRYPTION_SECRET "$secret"
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

restart_service() {
  info "Запускаю сервис..."
  systemctl restart "$SERVICE_NAME"
  sleep 2
}

show_status() {
  print_line
  if is_installed; then
    ok "Бот установлен."
    echo "Репозиторий: ${REPO_URL}"
    echo "Папка: ${INSTALL_DIR}"
    echo "Сервис: ${SERVICE_NAME}"
    echo "Venv: ${VENV_DIR}"
    echo "App log: ${APP_LOG_FILE}"
    echo "Install log: ${INSTALL_LOG}"
    echo "Текущая версия: $(get_local_sha | cut -c1-12)"
    echo "Статус systemd: $(systemctl is-active "$SERVICE_NAME" 2>/dev/null || true)"
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
  if [[ -z "$remote_sha" ]]; then
    warn "Не удалось получить хеш последнего коммита из GitHub."
    print_line
    return 1
  fi
  echo "Remote: ${remote_sha}"
  if [[ -n "$local_sha" ]]; then
    echo "Local : ${local_sha}"
  else
    echo "Local : нет локальной версии"
  fi
  if [[ "$remote_sha" == "$local_sha" && -n "$local_sha" ]]; then
    ok "Обновлений нет."
  else
    warn "Доступно обновление или бот ещё не ставился этой версией скрипта."
  fi
  print_line
}

install_bot() {
  print_line
  info "Установка AWG Telegram Bot"
  ensure_packages
  local tmp_dir
  tmp_dir="$(download_repo)"
  deploy_repo "$tmp_dir"
  rm -rf "$tmp_dir"

  ensure_env_template
  local api_token admin_id secret
  api_token="$(prompt_api_token)"
  admin_id="$(prompt_admin_id)"
  secret="$(ensure_secret)"
  write_base_env "$api_token" "$admin_id" "$secret"

  ensure_venv_and_requirements
  write_service
  persist_remote_sha
  restart_service
  ok "Установка завершена."
  show_status
  echo "Быстрый запуск меню потом: sudo bash ${INSTALL_DIR}/awg-tgbot.sh"
  echo "Или: sudo awg-tgbot"
}

update_bot() {
  if ! is_installed; then
    warn "Бот не установлен. Сначала выбери установку."
    return 1
  fi
  print_line
  info "Обновление AWG Telegram Bot"
  ensure_packages
  local tmp_dir
  tmp_dir="$(download_repo)"
  deploy_repo "$tmp_dir"
  rm -rf "$tmp_dir"

  ensure_env_template
  local api_token admin_id secret
  api_token="$(get_env_value API_TOKEN)"
  admin_id="$(get_env_value ADMIN_ID)"
  secret="$(get_env_value ENCRYPTION_SECRET)"
  if [[ -z "$api_token" ]]; then api_token="$(prompt_api_token)"; fi
  if [[ -z "$admin_id" ]]; then admin_id="$(prompt_admin_id)"; fi
  if [[ -z "$secret" ]]; then secret="$(ensure_secret)"; fi
  write_base_env "$api_token" "$admin_id" "$secret"

  ensure_venv_and_requirements
  write_service
  persist_remote_sha
  restart_service
  ok "Обновление завершено."
  show_status
}

remove_bot() {
  print_line
  if ! is_installed && [[ ! -d "$INSTALL_DIR" ]]; then
    warn "Похоже, бот уже удалён."
    return 0
  fi

  echo "1) Удалить сервис и venv, но сохранить .env и базу"
  echo "2) Полностью удалить /opt/amnezia/bot и логи приложения"
  echo "0) Отмена"
  local choice=""
  prompt_raw "Выбор: " choice

  case "$choice" in
    1)
      systemctl disable --now "$SERVICE_NAME" 2>/dev/null || true
      rm -f "$SERVICE_FILE"
      systemctl daemon-reload
      systemctl reset-failed
      rm -rf "$VENV_DIR"
      rm -f "$SELF_SYMLINK"
      ok "Сервис и venv удалены. Код, .env и база остались в ${INSTALL_DIR}."
      ;;
    2)
      if ! confirm "Точно удалить весь бот, .env, базу и логи приложения?" "N"; then
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
    return 1
  fi
  echo "1) Последние 100 строк journalctl"
  echo "2) Смотреть journalctl -f"
  echo "3) Последние 100 строк файла ${APP_LOG_FILE}"
  echo "4) Смотреть tail -f ${APP_LOG_FILE}"
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

print_menu() {
  print_line
  echo "AWG Telegram Bot — ${REPO_OWNER}/${REPO_NAME}:${REPO_BRANCH}"
  echo "1) Установить"
  echo "2) Обновить"
  echo "3) Проверить обновления"
  echo "4) Статус"
  echo "5) Логи"
  echo "6) Удалить"
  echo "0) Выход"
  print_line
}

run_action() {
  local action="${1:-}"
  case "$action" in
    install) install_bot ;;
    update) update_bot ;;
    check-updates) check_updates ;;
    status) show_status ;;
    logs) show_logs ;;
    remove|uninstall) remove_bot ;;
    *) return 1 ;;
  esac
}

if [[ $# -gt 0 ]]; then
  run_action "$1"
  exit $?
fi

while true; do
  print_menu
  local_choice=""
  prompt_raw "Выбери действие: " local_choice
  case "$local_choice" in
    1) install_bot ;;
    2) update_bot ;;
    3) check_updates ;;
    4) show_status ;;
    5) show_logs ;;
    6) remove_bot ;;
    0) echo "Выход."; exit 0 ;;
    *) warn "Неизвестный пункт меню." ;;
  esac
  if has_tty; then
    echo
    prompt_raw "Нажми Enter, чтобы вернуться в меню..." _dummy
  fi
  clear || true

done
