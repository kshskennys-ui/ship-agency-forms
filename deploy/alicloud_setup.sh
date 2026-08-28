#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/ship-agency-forms"
SERVICE_USER="shipagency"
DB_NAME="ship_agency"
DB_USER="shipagency"
ENV_FILE="/etc/ship-agency-forms.env"
REPO_URL="https://github.com/kshskennys-ui/ship-agency-forms.git"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "请使用 root 执行此脚本。" >&2
  exit 1
fi

echo "[1/8] 安装系统依赖"
dnf install -y git gcc gcc-c++ make libffi-devel openssl-devel zlib-devel bzip2-devel \
  python3.11 python3.11-pip python3.11-devel nodejs npm nginx \
  postgresql-server postgresql-contrib mesa-libGL

echo "[2/8] 初始化 PostgreSQL"
if [[ ! -f /var/lib/pgsql/data/PG_VERSION ]]; then
  postgresql-setup --initdb
fi
systemctl enable --now postgresql

# 应用服务与 PostgreSQL 在同一台服务器上运行，使用同名系统用户和
# 数据库用户通过 Unix Socket 认证，避免把数据库密码写入连接 URL。
PG_HBA="/var/lib/pgsql/data/pg_hba.conf"
sed -i '/^[[:space:]]*host[[:space:]]\+all[[:space:]]\+all[[:space:]]\+127\.0\.0\.1\/32[[:space:]]/d' "$PG_HBA"
sed -i '/^[[:space:]]*host[[:space:]]\+all[[:space:]]\+all[[:space:]]\+::1\/128[[:space:]]/d' "$PG_HBA"
sed -i '1ihost    all             all             127.0.0.1/32            scram-sha-256\nhost    all             all             ::1/128                 scram-sha-256' "$PG_HBA"
sed -i '/^[[:space:]]*local[[:space:]]\+all[[:space:]]\+shipagency[[:space:]]/d' "$PG_HBA"
sed -i '1ilocal   all             shipagency                              peer' "$PG_HBA"
systemctl reload postgresql

if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$APP_DIR" --shell /sbin/nologin "$SERVICE_USER"
fi

# 仍为数据库角色设置随机密码；应用实际使用同名系统用户的 peer 认证。
DB_PASSWORD="$(openssl rand -hex 24)"
runuser -u postgres -- psql -v ON_ERROR_STOP=1 --dbname=postgres <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$DB_USER') THEN
    CREATE ROLE $DB_USER LOGIN PASSWORD '$DB_PASSWORD';
  ELSE
    ALTER ROLE $DB_USER WITH LOGIN PASSWORD '$DB_PASSWORD';
  END IF;
END
\$\$;
SELECT 'CREATE DATABASE $DB_NAME OWNER $DB_USER'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$DB_NAME')\gexec
SQL

echo "[3/8] 获取项目源码"
if [[ -d "$APP_DIR/.git" ]]; then
  git -C "$APP_DIR" fetch --depth 1 origin main
  git -C "$APP_DIR" reset --hard origin/main
else
  if [[ -e "$APP_DIR" ]]; then
    mv "$APP_DIR" "${APP_DIR}.backup.$(date +%Y%m%d%H%M%S)"
  fi
  git clone --depth 1 --branch main "$REPO_URL" "$APP_DIR"
fi
mkdir -p "$APP_DIR/data" "$APP_DIR/.playwright-browsers"

echo "[4/8] 安装 Python 和 Node 依赖"
python3.11 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/python" -m pip install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"
(cd "$APP_DIR" && npm ci --omit=dev --no-audit --no-fund)

echo "[5/8] 安装 Chromium 运行时"
PLAYWRIGHT_BROWSERS_PATH="$APP_DIR/.playwright-browsers" \
  "$APP_DIR/.venv/bin/python" -m playwright install chromium

cat > "$ENV_FILE" <<EOF
PYTHONPATH=$APP_DIR/backend
DATABASE_URL=postgresql+psycopg:///$DB_NAME
SHIP_AGENCY_ROOT=$APP_DIR
SHIP_AGENCY_DATA_DIR=$APP_DIR/data
SHIP_AGENCY_TEMPLATE_DIR=$APP_DIR/templates
SHIP_AGENCY_FRONTEND_DIR=$APP_DIR/frontend
SHIP_AGENCY_NODE=/usr/bin/node
PLAYWRIGHT_BROWSERS_PATH=$APP_DIR/.playwright-browsers
EOF
chmod 600 "$ENV_FILE"

echo "[6/8] 配置服务自动启动"
cat > /etc/systemd/system/ship-agency-forms.service <<EOF
[Unit]
Description=Ship Agency Forms
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$APP_DIR/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"
systemctl daemon-reload
systemctl enable --now ship-agency-forms

echo "[7/8] 配置 Nginx"
# 禁用发行版自带的默认站点（保留为 .disabled，可随时恢复），避免与
# 本应用的 default_server 发生冲突并导致访问到 Nginx 欢迎页。
for DEFAULT_CONF in /etc/nginx/conf.d/default.conf /etc/nginx/sites-enabled/default; do
  if [[ -f "$DEFAULT_CONF" ]]; then
    mv -f "$DEFAULT_CONF" "${DEFAULT_CONF}.disabled"
  fi
done
cat > /etc/nginx/conf.d/ship-agency-forms.conf <<EOF
server {
    listen 80 default_server;
    server_name _;
    client_max_body_size 20m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
nginx -t
systemctl enable --now nginx
systemctl restart nginx

echo "[8/8] 检查服务"
for attempt in {1..30}; do
  if curl --fail --silent http://127.0.0.1/api/health; then
    echo
    echo "部署完成。请用浏览器访问服务器公网 IP。"
    exit 0
  fi
  sleep 1
done

echo "服务未在 30 秒内启动，请查看：journalctl -u ship-agency-forms -n 100 --no-pager" >&2
exit 1
