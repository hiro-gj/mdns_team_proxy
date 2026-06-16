# type: ignore
import os
import sys

# MicroPython環境（Raspberry Pi Pico Wなど）での誤実行を検知して警告する
if sys.implementation.name == "micropython":
    print("\n" + "="*60)
    print("【エラー】このスクリプトは Raspberry Pi Pico W 上では直接実行できません！")
    print("このインストーラーは、お使いのPC（Windows / Mac / Linux等）上の")
    print("通常のPython環境で実行してください。")
    print("="*60 + "\n")
    sys.exit(1)

try:
    import urllib.request
    import json
    import zipfile
    import hashlib
    import subprocess
    import glob
    import shutil
    import base64
except ImportError as e:
    print(f"\n必要なモジュールのインポートに失敗しました: {e}")
    print("PCの通常のPython環境で実行しているか確認してください。")
    sys.exit(1)

# 実行中のスクリプトのディレクトリを基準とする
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = "hiro-gj/mdns_proxy"
EXTRACT_DIR = os.path.join(BASE_DIR, "extracted_mdns_proxy")

# 固定キーとIV（16バイト、Pico側のwifi_manager.pyと共通）
AES_KEY = b"mDNSProxyPicoKey"
AES_IV  = b"mDNSProxyPico_IV"


# --- Pure Python AES-128 Encryption Implementation ---
# 外部ライブラリ無しで動作させるためのAES-128-CBC暗号化（暗号化のみ）
class PureAES128:
    s_box = [
        0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
        0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0,
        0xb7, 0xfd, 0x93, 0x26, 0x36, 0x3f, 0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15,
        0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07, 0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75,
        0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0, 0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84,
        0x53, 0xd1, 0x00, 0xed, 0x20, 0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf,
        0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8,
        0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5, 0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2,
        0xcd, 0x0c, 0x13, 0xec, 0x5f, 0x97, 0x44, 0x17, 0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73,
        0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88, 0x46, 0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb,
        0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c, 0xc2, 0xd3, 0xac, 0x62, 0x91, 0x95, 0xe4, 0x79,
        0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a, 0xae, 0x08,
        0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f, 0x4b, 0xbd, 0x8b, 0x8a,
        0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e, 0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e,
        0xe1, 0xf8, 0x98, 0x11, 0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf,
        0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68, 0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16
    ]

    r_con = [
        0x00, 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1b, 0x36
    ]

    def __init__(self, key: bytes):
        if len(key) != 16:
            raise ValueError("Key must be 16 bytes")
        self.round_keys = self._expand_key(key)

    def _expand_key(self, key: bytes):
        w = list(key)
        for i in range(4, 44):
            temp = w[(i-1)*4 : i*4]
            if i % 4 == 0:
                # RotWord
                temp = temp[1:] + temp[:1]
                # SubWord
                temp = [self.s_box[b] for b in temp]
                # XOR Rcon
                temp[0] ^= self.r_con[i // 4]
            # XOR with w[i-4]
            prev = w[(i-4)*4 : (i-3)*4]
            w.extend([x ^ y for x, y in zip(prev, temp)])
        return w

    def _sub_bytes(self, state):
        for i in range(16):
            state[i] = self.s_box[state[i]]

    def _shift_rows(self, state):
        # 0 4 8 12 -> Row 0 (no shift)
        # 1 5 9 13 -> Row 1 (shift left 1) -> 5 9 13 1
        # 2 6 10 14 -> Row 2 (shift left 2) -> 10 14 2 6
        # 3 7 11 15 -> Row 3 (shift left 3) -> 15 3 7 11
        state[1], state[5], state[9], state[13] = state[5], state[9], state[13], state[1]
        state[2], state[6], state[10], state[14] = state[10], state[14], state[2], state[6]
        state[3], state[7], state[11], state[15] = state[15], state[3], state[7], state[11]

    @staticmethod
    def _galois_mul(a, b):
        p = 0
        for _ in range(8):
            if b & 1:
                p ^= a
            hi_bit = a & 0x80
            a = (a << 1) & 0xff
            if hi_bit:
                a ^= 0x1b
            b >>= 1
        return p

    def _mix_columns(self, state):
        for i in range(4):
            col = state[i*4 : (i+1)*4]
            state[i*4]     = self._galois_mul(2, col[0]) ^ self._galois_mul(3, col[1]) ^ col[2] ^ col[3]
            state[i*4 + 1] = col[0] ^ self._galois_mul(2, col[1]) ^ self._galois_mul(3, col[2]) ^ col[3]
            state[i*4 + 2] = col[0] ^ col[1] ^ self._galois_mul(2, col[2]) ^ self._galois_mul(3, col[3])
            state[i*4 + 3] = self._galois_mul(3, col[0]) ^ col[1] ^ col[2] ^ self._galois_mul(2, col[3])

    def _add_round_key(self, state, round_num):
        round_key = self.round_keys[round_num*16 : (round_num+1)*16]
        for i in range(16):
            state[i] ^= round_key[i]

    def encrypt_block(self, block: bytes) -> bytes:
        # Convert state from column-major to our list representation
        # State represents:
        # [s0, s1, s2, s3,
        #  s4, s5, s6, s7,
        #  s8, s9, s10,s11,
        #  s12,s13,s14,s15]
        state = list(block)
        
        self._add_round_key(state, 0)
        
        for r in range(1, 10):
            self._sub_bytes(state)
            self._shift_rows(state)
            self._mix_columns(state)
            self._add_round_key(state, r)
            
        self._sub_bytes(state)
        self._shift_rows(state)
        self._add_round_key(state, 10)
        
        return bytes(state)

    def encrypt_cbc(self, plaintext: bytes, iv: bytes) -> bytes:
        if len(iv) != 16:
            raise ValueError("IV must be 16 bytes")
        
        # PKCS#7 padding
        pad_len = 16 - (len(plaintext) % 16)
        plaintext += bytes([pad_len] * pad_len)
        
        ciphertext = b""
        prev_block = iv
        
        for i in range(0, len(plaintext), 16):
            block = plaintext[i:i+16]
            # XOR with previous block
            xored = bytes(x ^ y for x, y in zip(block, prev_block))
            encrypted_block = self.encrypt_block(xored)
            ciphertext += encrypted_block
            prev_block = encrypted_block
            
        return ciphertext


def encrypt_password(password: str) -> str:
    """
    AES-128-CBC でパスワードを暗号化し、Base64でエンコードして返す。
    cryptography や pycryptodome があれば優先し、無ければ PureAES128 を使う。
    """
    data = password.encode('utf-8')
    
    # 1) Try cryptography library (common on modern PC environments)
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives import padding
        
        padder = padding.PKCS7(128).padder()
        padded_data = padder.update(data) + padder.finalize()
        
        cipher = Cipher(algorithms.AES(AES_KEY), modes.CBC(AES_IV))
        encryptor = cipher.encryptor()
        ciphertext = encryptor.update(padded_data) + encryptor.finalize()
        return base64.b64encode(ciphertext).decode('utf-8')
    except ImportError:
        pass

    # 2) Try pycryptodome library
    try:
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import pad
        
        cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
        ciphertext = cipher.encrypt(pad(data, 16))
        return base64.b64encode(ciphertext).decode('utf-8')
    except ImportError:
        pass

    # 3) Fallback to Pure Python AES implementation
    pure_aes = PureAES128(AES_KEY)
    ciphertext = pure_aes.encrypt_cbc(data, AES_IV)
    return base64.b64encode(ciphertext).decode('utf-8')


def get_latest_release_url():
    url = f"https://api.github.com/repos/{REPO}/releases/latest"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            
            # アセットからzipファイルを探す
            for asset in data.get("assets", []):
                if asset.get("name", "").endswith(".zip"):
                    return asset.get("browser_download_url")
            
            # アセットがない場合、タグ名からURLを構築
            tag_name = data.get("tag_name")
            if tag_name:
                return f"https://github.com/{REPO}/releases/download/{tag_name}/mdns_proxy-{tag_name}.zip"
    except Exception as e:
        print(f"GitHub APIからの最新リリース情報の取得に失敗しました: {e}")
    
    # 最終フォールバック
    return f"https://github.com/{REPO}/archive/refs/heads/main.zip"

def download_and_extract():
    if os.path.exists(EXTRACT_DIR):
        choice = input(f"既に展開されたフォルダ '{EXTRACT_DIR}' が存在します。再ダウンロードして上書きしますか？ (y/N): ").strip().lower()
        if choice not in ["y", "yes"]:
            print("既存のフォルダを使用します。ダウンロードと展開をスキップします。")
            return True
        else:
            print("既存のフォルダを削除しています...")
            try:
                shutil.rmtree(EXTRACT_DIR)
            except Exception as e:
                print(f"フォルダの削除中にエラーが発生しました（上書き展開を試みます）: {e}")

    print("[1/2] ソースコードのダウンロードと展開...")
    zip_url = get_latest_release_url()
    print(f"ダウンロード元: {zip_url}")
    
    # URLからファイル名を取得 (例: mdns_proxy-v0.3.2_20260615.zip)
    zip_name = zip_url.split("/")[-1]
    if not zip_name.endswith(".zip"):
        zip_name = "mdns_proxy_latest.zip"
        
    zip_path = os.path.join(BASE_DIR, zip_name)
    print(f"保存ファイル名: {zip_path}")
    
    try:
        urllib.request.urlretrieve(zip_url, zip_path)
        print("ダウンロードが完了しました。")
    except Exception as e:
        print(f"ダウンロードに失敗しました: {e}")
        return False

    print("ZIPファイルを展開中...")
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(EXTRACT_DIR)
        print(f"'{EXTRACT_DIR}' ディレクトリに展開しました。")
        
        # 不要になったダウンロード済みのZIPファイルをクリーンアップ
        try:
            os.remove(zip_path)
        except OSError:
            pass
            
        return True
    except Exception as e:
        print(f"展開に失敗しました: {e}")
        return False

def find_target_dir():
    # 展開されたディレクトリを特定する
    if not os.path.exists(EXTRACT_DIR):
        return None
    
    # 展開ディレクトリ直下に system.ini や src がある場合（フラットな展開）
    if os.path.exists(os.path.join(EXTRACT_DIR, "system.ini")) or os.path.exists(os.path.join(EXTRACT_DIR, "src")):
        return EXTRACT_DIR

    dirs = [d for d in os.listdir(EXTRACT_DIR) if os.path.isdir(os.path.join(EXTRACT_DIR, d))]
    if dirs:
        # 最も mdns_proxy らしい名前のディレクトリを選択
        for d in dirs:
            if "mdns_proxy" in d:
                return os.path.join(EXTRACT_DIR, d)
        
        # db や src などの内部フォルダは除外してそれ以外のサブディレクトリを探す
        valid_dirs = [d for d in dirs if d not in ["db", "src", ".git", "__pycache__"]]
        if valid_dirs:
            return os.path.join(EXTRACT_DIR, valid_dirs[0])
            
        return os.path.join(EXTRACT_DIR, dirs[0])
    return None

def update_wifi_settings(target_dir):
    system_ini_path = os.path.join(target_dir, "system.ini")
    if not os.path.exists(system_ini_path):
        print(f"エラー: {system_ini_path} が見つかりません。先に準備を完了させてください。")
        return

    # Picoのホスト名設定 (mDNS)
    print("Picoのホスト名（mDNS、例: pico.local）を設定します。")
    hostname = input("ホスト名を入力してください (スキップするにはEnter): ").strip()

    ssid = input("Wi-Fi SSIDを入力してください: ")
    password = input("Wi-Fi パスワードを入力してください: ")

    # パスワードの暗号化 (AES-128-CBC + Base64)
    # 従来の Base64 エンコードを AES-128-CBC による可逆な暗号化にアップグレード
    encoded_password = encrypt_password(password)

    # system.ini の書き換え
    # コメント行や既存フォーマットを壊さないようにテキスト置換で処理
    try:
        with open(system_ini_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        new_lines = []
        has_hostname_field = any(line.strip().startswith("mdns_hostname =") for line in lines)
        inserted_hostname = False

        for line in lines:
            if line.strip().startswith("wifi_ssid ="):
                # mdns_hostname フィールドが存在せず、wifi_ssid設定行を見つけたら、その前にホスト名設定を挿入する
                if not has_hostname_field and hostname and not inserted_hostname:
                    new_lines.append(f"# ホスト名（Pico用）\n")
                    new_lines.append(f"mdns_hostname = {hostname}\n\n")
                    inserted_hostname = True
                new_lines.append(f"wifi_ssid = {ssid}\n")
            elif line.strip().startswith("wifi_password ="):
                new_lines.append(f"wifi_password = {encoded_password}\n")
            elif line.strip().startswith("mdns_hostname ="):
                if hostname:
                    new_lines.append(f"mdns_hostname = {hostname}\n")
                else:
                    new_lines.append(line)
                inserted_hostname = True
            else:
                new_lines.append(line)

        # 万が一 wifi_ssid = も見つからず挿入されなかった場合、ファイルの末尾に挿入
        if hostname and not inserted_hostname:
            new_lines.append(f"\n# ホスト名（Pico用）\nmdns_hostname = {hostname}\n")

        with open(system_ini_path, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)

        print("system.ini の設定を更新しました。")
        if hostname:
            print(f"Hostname (mDNS): {hostname}")
        print(f"SSID: {ssid}")
        print(f"Password (AES-128-CBC + Base64): {encoded_password}")
    except Exception as e:
        print(f"system.ini の更新中にエラーが発生しました: {e}")

def check_and_install_mpremote():
    # mpremoteが利用可能かチェック
    try:
        subprocess.run(["mpremote", "version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("mpremote が見つかりません。pipでのインストールを試みます...")
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "mpremote"], check=True)
            return True
        except subprocess.CalledProcessError:
            print("mpremote のインストールに失敗しました。Python環境とインターネット接続を確認してください。")
            return False

PICO_PORT = None

def run_mpremote(args, **kwargs):
    # PICO_PORT が特定されている場合は connect <port> を自動挿入して実行する
    cmd = ["mpremote"]
    if PICO_PORT and PICO_PORT != "auto":
        cmd.extend(["connect", PICO_PORT])
    cmd.extend(args)
    return subprocess.run(cmd, **kwargs)

def check_pico_connection():
    global PICO_PORT
    if not check_and_install_mpremote():
        return False
    
    print("Raspberry Pi Pico W の接続を確認しています...")
    try:
        # mpremote devs を実行して接続機器を取得
        result = subprocess.run(["mpremote", "devs"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        output = result.stdout
        
        # 各行を解析してポート名を取得
        for line in output.splitlines():
            line_str = line.strip()
            if not line_str:
                continue
            # PicoのUSB VendorID:ProductID(2e8a:0005 等) や 'Raspberry Pi' 'Pico' を探す
            if "2e8a:" in line_str or "Raspberry Pi" in line_str or "Pico" in line_str:
                parts = line_str.split()
                if parts:
                    PICO_PORT = parts[0]
                    print(f"Raspberry Pi Pico W を検出しました (ポート: {PICO_PORT})")
                    return True
        
        # デバイス一覧から見つからない場合でも、直接接続できるか試行する
        test_run = subprocess.run(["mpremote", "exec", "import sys; print(sys.platform)"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
        if test_run.returncode == 0 and "rp2" in test_run.stdout:
            PICO_PORT = "auto"
            print("Raspberry Pi Pico W を検出しました（応答確認成功）。")
            return True
            
        print("【警告】Raspberry Pi Pico W が検出されませんでした。USBケーブルで接続されているか、またMicroPythonが書き込み済みか確認してください。")
        PICO_PORT = None
        return False
    except Exception as e:
        print(f"Picoの接続確認中にエラーが発生しました: {e}")
        PICO_PORT = None
        return False

def upload_to_pico(target_dir):
    if not check_pico_connection():
        return

    src_dir = os.path.join(target_dir, "src")
    system_ini_path = os.path.join(target_dir, "system.ini")
    search_hosts_path = os.path.join(target_dir, "search_hosts.ini")

    if not os.path.exists(src_dir):
        print(f"エラー: {src_dir} が存在しません。")
        return

    print("Raspberry Pi Pico Wにファイルを転送しています...")

    # src内のすべての .py ファイルを取得
    py_files = glob.glob(os.path.join(src_dir, "**", "*.py"), recursive=True)

    try:
        # 1. Pythonファイルの転送 (ルート直下に配置)
        for py_file in py_files:
            rel_path = os.path.relpath(py_file, src_dir)
            # サブディレクトリ構造がある場合、Pico側でもディレクトリを作成
            remote_path = rel_path.replace(os.sep, "/")
            remote_dir = os.path.dirname(remote_path)
            
            if remote_dir:
                # ディレクトリ作成を試みる (mpremote fs mkdir)
                # エラーが出ても既存ディレクトリの場合は無視して進む
                run_mpremote(["fs", "mkdir", remote_dir], stderr=subprocess.PIPE)
            
            print(f"転送中: {py_file} -> :{remote_path}")
            run_mpremote(["fs", "cp", py_file, f":{remote_path}"], check=True)

        # 2. INIファイルの転送
        for ini_file in [system_ini_path, search_hosts_path]:
            if os.path.exists(ini_file):
                filename = os.path.basename(ini_file)
                print(f"転送中: {ini_file} -> :{filename}")
                run_mpremote(["fs", "cp", ini_file, f":{filename}"], check=True)
            else:
                print(f"警告: {ini_file} が見つかりません。転送をスキップします。")

        print("アップロードが完了しました！")
    except subprocess.CalledProcessError as e:
        print(f"\n[エラー] ファイル転送中にエラーが発生しました: {e}")
        
        # ポート占有や一般的なエラーのヒントを表示
        print("\n" + "="*60)
        print("【トラブルシューティング：転送エラーへの対策】")
        print("Raspberry Pi Pico W のシリアルポートが占有されている可能性があります。")
        print("以下の項目を確認し、改善したのち再度アップロード(2番)をお試しください：")
        print("1. VS Codeの「MicroPico」拡張機能などのPico連携アドオンが有効な場合、")
        print("   シリアル接続を切断（Disconnect）するか、VS Codeを一度閉じてください。")
        print("2. Thonny やその他のシリアルモニター（Tera Term、PuTTYなど）が")
        print("   起動してPicoと通信している場合は、それらのアプリを終了してください。")
        print("="*60 + "\n")
    except Exception as e:
        print(f"予期しないエラーが発生しました: {e}")

def main():
    print("=============================================")
    print(" mDNS-Proxy-RasPiPicoW-Installer")
    print("=============================================")

    # 起動時に無条件で最新版をダウンロードして展開
    if not download_and_extract():
        print("最新版のダウンロードまたは展開に失敗したため、処理を中断します。")
        return

    target_dir = find_target_dir()
    if not target_dir:
        print("展開ディレクトリの特定に失敗したため、処理を中断します。")
        return

    # 起動時に前提としてPico Wの接続状態を確認して表示
    pico_connected = check_pico_connection()
    if not pico_connected:
        print("\n※ Raspberry Pi Pico W が接続されていないようです。")
        print("無線LAN設定(1番)はオフラインで行えますが、Pico Wにアップロードする(2番)前に必ずUSB接続してください。\n")

    while True:
        print("\n--- メニュー ---")
        print("1) 無線LANの設定")
        print("2) mdns_proxyのアップロード")
        print("q) 終了")
        
        choice = input("メニュー番号を選択してください: ").strip().lower()
        
        if choice == "1":
            update_wifi_settings(target_dir)
        elif choice == "2":
            upload_to_pico(target_dir)
        elif choice in ["q", "quit", "exit"]:
            print("プログラムを終了します。")
            break
        else:
            print("無効な選択です。もう一度入力してください。")

if __name__ == "__main__":
    main()
