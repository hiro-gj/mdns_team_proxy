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

echo "[2/4] ソースコードの取得と展開..."
TMP_DIR=$(mktemp -d)
ORIGINAL_PWD=$(pwd)

# ローカルにzipファイルが存在するか確認
LOCAL_ZIP=""
for f in "$ORIGINAL_PWD"/mdns_proxy-*.zip "$ORIGINAL_PWD"/mdns-proxy-*.zip; do
  if [ -f "$f" ]; then
    LOCAL_ZIP="$f"
    break
  fi
done

if [ -n "$LOCAL_ZIP" ]; then
  echo "[+] サーバ上の既存ZIPファイルを見つけました: $(basename "$LOCAL_ZIP")"
  echo "[+] このZIPファイルを使用してインストールします。"
  cp "$LOCAL_ZIP" "$TMP_DIR/mdns_proxy.zip"
else
  echo "[+] インターネットから最新リリースをダウンロードします..."
  # 最新リリース情報を取得
  RELEASE_JSON=$(curl -s "https://api.github.com/repos/${REPO}/releases/latest")

  # 最新リリースのZIPアセットのURLを動的に取得
  ZIP_URL=$(echo "$RELEASE_JSON" | jq -r '.assets[]? | select(.name | endswith(".zip")) | .browser_download_url')

  # もしリリースのZIPアセットのURLが直接取得できなかった場合、最新タグ名からActionsビルドのZIP URLを構築
  if [ -z "$ZIP_URL" ] || [ "$ZIP_URL" = "null" ]; then
    TAG_NAME=$(echo "$RELEASE_JSON" | jq -r '.tag_name')
    if [ -n "$TAG_NAME" ] && [ "$TAG_NAME" != "null" ]; then
      echo "GitHub Releaseのアセットから直接ZIP URLを取得できなかったため、タグ [${TAG_NAME}] のビルドZIP URLを構築してダウンロードします..."
      ZIP_URL="https://github.com/${REPO}/releases/download/${TAG_NAME}/mdns_proxy-${TAG_NAME}.zip"
    else
      echo "最新リリースのタグ情報を取得できなかったため、mainブランチのZIPにフォールバックします..."
      ZIP_URL="https://github.com/${REPO}/archive/refs/heads/main.zip"
    fi
  fi

  curl -L -o "$TMP_DIR/mdns_proxy.zip" "$ZIP_URL"
fi

cd "$TMP_DIR"
unzip -q mdns_proxy.zip

# インストール先の作成
sudo mkdir -p "$INSTALL_DIR"

# 展開されたファイルの構造を自動判定してコピー
if [ -d "src" ]; then
  # zipの直下にsrcディレクトリがある場合（ディレクトリなしの直展開構成）
  echo "[+] ZIPの直下にソースコードが格納されています。そのままコピーします。"
  # 元の cp コマンドを踏襲して既存ファイルの上書き・コピー
  sudo cp -r * "$INSTALL_DIR/"
else
  # zip内に親ディレクトリが存在する場合（例: mdns_proxy-develop/src など）
  EXTRACTED_DIR=$(ls -d */ | grep -v "mdns_proxy.zip" | head -n 1)
  if [ -n "$EXTRACTED_DIR" ] && [ -d "${EXTRACTED_DIR}src" ]; then
    echo "[+] 親ディレクトリ [${EXTRACTED_DIR}] を検出しました。このディレクトリの中身をコピーします。"
    sudo cp -r "${EXTRACTED_DIR}"* "$INSTALL_DIR/"
  else
    echo "[-] ソースコード(srcディレクトリ)が見つかりませんでした。展開に失敗した可能性があります。"
    exit 1
  fi
fi

# Linux環境では不要なPico用ポリフィルファイルを削除
echo "[+] Linux環境向けにPico用ポリフィルファイルを削除します..."
sudo rm -f "$INSTALL_DIR/src/sqlite3.py"
sudo rm -f "$INSTALL_DIR/src/threading.py"
sudo rm -f "$INSTALL_DIR/src/uuid.py"
sudo rm -f "$INSTALL_DIR/src/socketserver.py"
sudo rm -f "$INSTALL_DIR/src/contextlib.py"
sudo rm -f "$INSTALL_DIR/src/subprocess.py"
sudo rm -f "$INSTALL_DIR/src/ipaddress.py"
sudo rm -f "$INSTALL_DIR/src/os.py"
sudo rm -rf "$INSTALL_DIR/src/http"
sudo rm -rf "$INSTALL_DIR/src/urllib"

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
