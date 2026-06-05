# mDNS Proxy

mDNS Proxyは、ネットワークセグメントを跨いだmDNS名前解決を実現する分散型プロキシシステムです。
各ネットワークセグメントに配置されたプロキシが互いに通信し、mDNSのホスト情報を交換・マージすることで、別セグメントのデバイスを名前解決できるようにします。

## ディレクトリ構成
- `db/`: SQLite3データベースファイル（`mdns_proxy.sqlite3`）が保存されます。
- `log/`: ログファイル（`mdns_proxy.log`）が保存されます。
- `src/`: プログラムのソースコード。
- `system.ini`: システム全体の設定ファイル。
- `search_hosts.ini`: 検索対象となるローカルホスト名（ローカルFQDN）のリスト。

## 設定ファイル (*.ini)

システムを動作させるために、以下の設定ファイルを適切に編集してください。
各環境への導入時、これらは共通の設定ファイルとして使用されます。

### system.ini
システム全体の動作設定やネットワーク設定を定義します。

```ini
[system]
# 実行間隔（秒）
interval = 10
# 発信トークン接頭語
token_prefix = mDNSProxy_
# HTTP待ち受けポート
port = 53080
# TTL初期値（秒）
ttl = 120
# ノード識別ID（複数サーバで重複しない値となる）
# 初回起動時に自動生成されるため手動記入不要
node_id =

[network]
# 外部mDNSプロキシIPアドレスとポート（カンマ区切りで複数指定可能）
# 中継・収束方式に対応しており、フルメッシュで到達できない環境でも中継可能なプロキシを定義することで、全ノード間で自動同期されます
external_proxies = 192.168.1.10:53080,192.168.2.10:53080

# Wi-Fi設定（Raspberry Pi Pico用）
wifi_ssid = your_wifi_ssid
wifi_password = your_wifi_password
```

### search_hosts.ini
検索対象とするローカルホスト名、またはローカルFQDNのリストを定義します。
`.local` の記述を省略した場合でも、自動的に `.local` が補完されて対象になります。
ホスト名の後に `= IPアドレス` を指定することで、mDNSの検索を行わずに指定した固定IPアドレスを応答させることも可能です。

```ini
[hosts]
# 検索対象とするローカルホスト名またはローカルFQDNを記載します
# 例:
# host1             (host1.localとして扱われます)
# serverA           (serverA.localとして扱われます)
# printer1.local
test-device1
test-device2.local

# IPアドレスを固定で指定する場合
test-device3 = 192.168.3.10
```

## 起動モードとCLIツールによる管理

`src/main.py` は起動時にコマンドライン引数（オプション）を指定することで動作モードを切り替えられます。

- **`--daemon`**: 常駐サービスデーモンとして起動（API・mDNS・Schedulerをすべて起動します。このモードのみ二重起動防止の排他ロック制御が有効になります）。
- **引数なし または `--cli`**: 対話型CLIとして起動。バックエンドの常駐処理は一切起動せず、安全にデータの参照や静的ホストの追加削除が可能です（**手動起動の際、引数（オプション）は不要です**）。
- **`--once`**: スケジューラの同期や解決、マージ等の定期実行タスクを1回だけ実行して、即時終了する単発実行モードです。

### CLI (cli.py) について

対話型CLIは、mDNS Proxyのデータベースに保存されている情報の確認や、静的ホストの管理を行うためのツールです。
オプションなし、もしくは `--cli` を付加して実行すると自動的に対話メニューが立ち上がります。

**実行例:**
```bash
sudo python3 /opt/mdns_proxy/src/main.py
```

起動すると、コンソール上に以下のような実際の対話メニューが表示されます：

```text
--- mDNS Proxy CLI ---
1. マージ済みレコードの表示
2. 静的ホストの表示
3. 静的ホストの追加
4. 静的ホストの削除
5. インスタント実行 (Ctrl+Cで終了)
6. 終了
オプションを選択してください (1-6): 
```

**メニュー各項目の機能説明:**

1. **マージ済みレコードの表示**: プロキシが収集・マージしたmDNSレコード（ホスト名とIPアドレスの対応）を一覧表示します。
2. **静的ホストの表示**: 手動で登録された静的ホストの一覧を表示します。
3. **静的ホストの追加**: 対話プロンプトに従い、新しい静的ホスト（FQDN）を追加登録します。
4. **静的ホストの削除**: 登録済みの静的ホストをID指定で削除します。
5. **インスタント実行**: インスタント実行モードを開始します。実行中は `Ctrl+C` で終了してメニューに戻ることができます。
6. **終了**: CLIツールを終了します。(`Ctrl+C` での終了も可能です)

**Windows環境での利用について:**
Windows向けにビルドされた `mdns_proxy.exe` をコマンドプロンプトやPowerShell等から直接実行した（引数なしで起動した）際にも、標準のコンソールアプリとしてこの対話メニューが利用可能な状態で実装されています（サービスとしてバックグラウンドで起動している場合は、二重起動排他ロックのため別途 `--cli` モードなどで起動して安全に対話メニューにアクセスします）。

## 導入方法

### Windowsの場合（EXE化・サービス起動）
Python環境がないWindows PCでも動作するように、PyInstallerを用いてEXE化できます。

1. **ビルド（EXE化）**:
   開発環境（Pythonインストール済み）で以下のコマンドを実行します。
   ```bash
   pip install pyinstaller
   pyinstaller --onefile --name mdns_proxy src/main.py
   ```
   ビルドが成功すると、`dist/mdns_proxy.exe` が生成されます。

2. **配置**:
   `mdns_proxy.exe` と同じディレクトリに `db/`, `log/`, `system.ini`, `search_hosts.ini` を配置してください。

3. **サービスとして起動する**:
   NSSM (Non-Sucking Service Manager) や Windowsの `sc` コマンドを使用して、バックグラウンドサービスとして登録できます。
   例（NSSMを使用）:
   ```bash
   nssm install mDNSProxy "C:\path\to\mdns_proxy.exe"
   nssm start mDNSProxy
   ```

### Raspberry Pi Picoの場合（MicroPython）
Raspberry Pi Pico W（Wi-Fi対応モデル）を使用します。

1. **MicroPythonの導入**:
   Pico Wに最新のMicroPythonファームウェアを書き込みます。
2. **ファイルの転送**:
   Thonny IDEや `mpremote` ツールを使用して、以下のファイルとディレクトリをPicoのルートに転送します。
   - `src/` ディレクトリ内のすべてのPythonファイル（拡張子 `.py`）をPicoのルート、または `lib/` 等適切な階層に配置
   - `system.ini`
   - `search_hosts.ini`
3. **自動起動の設定**:
   エントリーポイントとなるスクリプト（例: `main.py`）をPicoのルートディレクトリに配置します。これにより、Picoの電源ON時に自動的に実行されます。

### Linux (Ubuntu 24.04) の場合
Python 3を使用して直接実行するか、systemdサービスとして登録します。

1. **環境構築**:
   ```bash
   sudo apt update
   sudo apt install python3 python3-pip
   ```
2. **ファイルの配置**:
   適当なディレクトリ（例: `/opt/mdns_proxy/`）にファイルを配置します。
3. **実行**:
   ```bash
   cd /opt/mdns_proxy
   # 対話CLIの起動（引数なし、または --cli）
   python3 src/main.py
   ```
4. **systemdサービス化（自動起動）**:
   `/etc/systemd/system/mdns_proxy.service` を作成します。
   ```ini
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
   ```
   サービスを有効化して起動します。
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable mdns_proxy
   sudo systemctl start mdns_proxy
   ```

## ログ出力内容

`log/mdns_proxy.log` には、システムの動作状況に関するログが出力されます。主な出力内容は以下の通りです。

- **起動・停止ログ**: プログラムの起動時および終了時のメッセージ。
  - 出力例: `INFO - mDNS Proxy started on 0.0.0.0`
- **エラー・警告ログ**: データベースのアクセスエラー、ネットワーク通信の失敗、設定ファイルの読み込みエラーなど。
  - 出力例: `ERROR - Failed to connect to external proxy at 192.168.1.10`
- **名前解決ログ**: ローカルネットワークでのmDNS名前解決リクエストおよびレスポンスの処理状況。
  - 出力例: `DEBUG - Resolved test-device1.local to 192.168.0.50`
- **同期・マージログ**: 他のセグメントのプロキシから取得したホスト情報の同期およびデータベースへのマージに関する記録。
  - 出力例: `INFO - Merged 3 records from external proxy 192.168.1.10`
- **スケジューラー実行ログ**: 定期実行される外部プロキシとの通信や、期限切れ（TTLオーバー）レコードのクリーンアップ処理の記録。
  - 出力例: `INFO - Removed 2 expired records during cleanup`

### ログローテーション
ログファイルは1日ごとにローテーションされます。
- 古いログはZIP圧縮されて保存されます。
- 最大で3世代前（3日分）までのアーカイブが保持され、それより古いログファイルは自動的に削除されます。
