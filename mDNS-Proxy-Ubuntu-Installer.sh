#!/bin/bash

# mDNS-Proxy-Ubuntu-Installer.sh
# mDNS Proxyの自動インストールスクリプト (Ubuntu 24.04向け)
# curl https://raw.githubusercontent.com/hiro-gj/mdns_proxy/main/mDNS-Proxy-Ubuntu-Installer.sh > mDNS-Proxy-Ubuntu-Installer.sh

set -e

# 変数定義
REPO="hiro-gj/mdns_proxy"
INSTALL_DIR="/opt/mdns_proxy"
SERVICE_FILE="/etc/systemd/system/mdns_proxy.service"

echo "=== mDNS Proxy Installer ==="

echo "[1/4] パッケージの更新と必要なツールのインストール..."
sudo apt -y update
sudo apt -y install python3 python3-pip unzip curl jq

echo "[2/4] ソースコードのダウンロードと展開..."
TMP_DIR=$(mktemp -d)
cd "$TMP_DIR"

# 最新リリース情報を取得
RELEASE_JSON=$(curl -s "https://api.github.com/repos/${REPO}/releases/latest")

# 最新リリースのZIPアセットのURLを動的に取得
ZIP_URL=$(echo "$RELEASE_JSON" | jq -r '.assets[]? | select(.name | endswith(".zip")) | .browser_download_url')

# もしリリースのZIPアセットのURLが取得できなかった場合、最新タグのZIPにフォールバック
if [ -z "$ZIP_URL" ] || [ "$ZIP_URL" = "null" ]; then
  TAG_NAME=$(echo "$RELEASE_JSON" | jq -r '.tag_name')
  if [ -n "$TAG_NAME" ] && [ "$TAG_NAME" != "null" ]; then
    echo "GitHub ReleaseのアセットからZIP URLを取得できなかったため、タグ [${TAG_NAME}] のソースコードZIPにフォールバックします..."
    ZIP_URL="https://github.com/${REPO}/archive/refs/tags/${TAG_NAME}.zip"
  else
    echo "最新リリースのタグ情報を取得できなかったため、mainブランチのZIPにフォールバックします..."
    ZIP_URL="https://github.com/${REPO}/archive/refs/heads/main.zip"
  fi
fi

curl -L -o mdns_proxy.zip "$ZIP_URL"
unzip -q mdns_proxy.zip

# 展開されたディレクトリ名を特定 (例: mdns_proxy-latest)
EXTRACTED_DIR=$(ls -d */ | head -n 1)

# インストール先の作成とファイルの配置
sudo mkdir -p "$INSTALL_DIR"
# 既存のファイルがある場合は上書き（あるいは削除してコピー）
sudo cp -rn "${EXTRACTED_DIR}"* "$INSTALL_DIR/" || sudo cp -r "${EXTRACTED_DIR}"* "$INSTALL_DIR/"

# データベースアクセス権限の適切な調整 (一般ユーザー手動実行のサポート)
# ディレクトリおよび SQLite WAL 作成のために適切な書き込み権限を付与
sudo mkdir -p "$INSTALL_DIR/db"
sudo chmod 777 "$INSTALL_DIR/db"
if [ -f "$INSTALL_DIR/db/mdns_proxy.sqlite3" ]; then
    sudo chmod 666 "$INSTALL_DIR/db/mdns_proxy.sqlite3"
fi

# 一時ディレクトリの削除
cd ~
rm -rf "$TMP_DIR"

echo "[3/4] systemdサービスファイルの作成..."
sudo bash -c "cat > $SERVICE_FILE" << 'EOF'
[Unit]
Description=mDNS Proxy Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/mdns_proxy
ExecStart=/usr/bin/python3 /opt/mdns_proxy/src/main.py --daemon
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

echo "[4/4] systemdデーモンのリロード..."
sudo systemctl daemon-reload

echo "=== インストール完了 ==="
echo "インストール先: $INSTALL_DIR"
echo "手動起動(対話メニュー): sudo python3 $INSTALL_DIR/src/main.py --cli"
echo "サービスファイル: $SERVICE_FILE"
echo ""
echo "※ サービスの起動は手動で行ってください。"
echo "自動起動の有効化と起動コマンド："
echo "sudo systemctl enable mdns_proxy.service"
echo "sudo systemctl start mdns_proxy.service"

