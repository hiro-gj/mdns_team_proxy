import os
import sys
import time

# USB給電直後のハードウェア・電圧安定化、およびPC接続時のUSB CDC（シリアル）初期化ノイズによる
# KeyboardInterrupt（自動起動の中断）を防ぐため、十分な待ち時間（5秒）を設けます。
if sys.platform == 'rp2':
    time.sleep(5)

def safe_reboot(delay=5):
    try:
        import machine
        try:
            print(f"Rebooting in {delay} seconds...")
        except:
            pass
        time.sleep(delay)
        machine.reset()
    except ImportError:
        import sys
        sys.exit(1)

def get_platform():
    import sys
    if sys.platform == 'rp2':
        return 'pico'
    try:
        import machine
        return 'pico'
    except ImportError:
        return 'windows' if os.name == 'nt' else 'linux'

# ログディレクトリとDBディレクトリの作成
if get_platform() != 'pico':
    for dir_name in ['log', 'db']:
        os.makedirs(os.path.join(os.path.dirname(os.path.dirname(__file__)), dir_name), exist_ok=True)

try:
    import config
except ImportError:
    config = None

try:
    import database
    import scheduler
    import api_server
    import cli
except ImportError:
    database = None
    scheduler = None
    api_server = None
    cli = None

import mdns_server
from logger_config import logger

try:
    import argparse
except ImportError:
    argparse = None

# 排他制御用ロックファイルのパス
def get_lock_file_path():
    if get_platform() == 'pico':
        return 'mdns_proxy.lock'
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, 'db', 'mdns_proxy.lock')

def acquire_lock():
    if get_platform() == 'pico':
        return True # Picoではロックをスキップ
    lock_path = get_lock_file_path()
    try:
        # すでにロックファイルがあるか確認し、無効な（プロセスが存在しない）場合は削除
        if os.path.exists(lock_path):
            try:
                with open(lock_path, 'r') as f:
                    pid = int(f.read().strip())
                # プロセス生存確認 (Windows/Linux)
                if pid == os.getpid():
                    return True
                if get_platform() == 'linux':
                    os.kill(pid, 0)
                elif get_platform() == 'windows':
                    import ctypes
                    PROCESS_QUERY_INFORMATION = 0x0400
                    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
                    if handle:
                        ctypes.windll.kernel32.CloseHandle(handle)
                    else:
                        raise OSError()
                # プロセスが生きている場合は、二重起動エラー
                logger.error(f"Another instance is already running with PID: {pid}")
                return False
            except (ValueError, OSError):
                # プロセスが死んでいるので、ロックファイルを削除して再作成
                try:
                    os.remove(lock_path)
                except Exception:
                    pass
        
        # ロック作成
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, 'w') as f:
            f.write(str(os.getpid()))
        return True
    except Exception as e:
        logger.error(f"Failed to acquire lock: {e}")
        return False

def release_lock():
    if get_platform() == 'pico':
        return
    lock_path = get_lock_file_path()
    try:
        if os.path.exists(lock_path):
            with open(lock_path, 'r') as f:
                pid = int(f.read().strip())
            if pid == os.getpid():
                os.remove(lock_path)
    except Exception:
        pass

def main():
    class DummyArgs:
        def __init__(self):
            self.daemon = False
            self.cli = False
            self.once = False

    if argparse is not None:
        parser = argparse.ArgumentParser(description="mDNS Proxy Daemon & CLI Utility")
        group = parser.add_mutually_exclusive_group(required=False)
        group.add_argument('--daemon', action='store_true', help='Run as service daemon')
        group.add_argument('--cli', action='store_true', help='Run interactive CLI only')
        group.add_argument('--once', action='store_true', help='Run scheduler tasks once and exit')
        
        args = parser.parse_args()

        # 引数無しで起動された場合は、デフォルトでcliモード扱いとする
        if not (args.daemon or args.cli or args.once):
            args.cli = True
    else:
        # Pico環境ではDaemonモード扱いとして動かす
        args = DummyArgs()
        args.daemon = True

    # 1. 設定読み込み
    try: 
        if config is None:
            raise ImportError("config module not available")
        sys_config = config.load_system_config()
        hosts_config = config.load_hosts_config()
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        # フォールバック用の簡易設定リーダー (Pico環境用)
        class DummyConfig:
            def __init__(self, filepath=None):
                self._data = {}
                if filepath:
                    try:
                        with open(filepath, 'r', encoding='utf-8') as f:
                            current_section = None
                            for line in f:
                                line = line.strip()
                                if not line or line.startswith('#') or line.startswith(';'):
                                    continue
                                if line.startswith('[') and line.endswith(']'):
                                    current_section = line[1:-1]
                                    self._data[current_section] = {}
                                elif '=' in line and current_section:
                                    key, val = line.split('=', 1)
                                    self._data[current_section][key.strip()] = val.strip()
                    except Exception as ex:
                        logger.error(f"DummyConfig failed to read {filepath}: {ex}")

            def get(self, section, option, fallback=None):
                if section in self._data and option in self._data[section]:
                    return self._data[section][option]
                # Default fallbacks
                if section == 'network' and option == 'mdns_hostname':
                    return 'mdns-pico1.local'
                if section == 'system' and option == 'interval':
                    return '30'
                if section == 'system' and option == 'port':
                    return '80'
                return fallback
            def has_section(self, section):
                return section in self._data
            def has_option(self, section, option):
                return section in self._data and option in self._data[section]
            def items(self, section):
                if section in self._data:
                    return list(self._data[section].items())
                return []

        def find_ini(filename):
            try:
                if os.path.exists(filename):
                    return filename
                parent_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), filename)
                if os.path.exists(parent_path):
                    return parent_path
            except Exception:
                pass
            return filename

        sys_config = DummyConfig(find_ini('system.ini'))
        hosts_config = DummyConfig(find_ini('search_hosts.ini'))

    # Picoの場合はWi-Fi接続
    if get_platform() == 'pico':
        import wifi_manager
        
        # MicroPythonのOS組み込みmDNS用に、システム設定からホスト名を抽出して渡す
        hostname_val = sys_config.get('network', 'mdns_hostname')
        if hostname_val:
            # ".local"が含まれていれば除去して生のホスト名にする
            hostname_clean = hostname_val.replace('.local', '')
        else:
            hostname_clean = "mdns-proxy"
            
        # 最大5回、wifi_manager.connect を呼び出して接続を試みる。
        # 毎回リトライの間に30秒の待機を入れることで、一時的なWi-Fi電波の揺らぎによる強制再起動を防ぐ。
        wifi_success = False
        for wifi_loop in range(5):
            success = wifi_manager.connect(
                sys_config.get('network', 'wifi_ssid'), 
                sys_config.get('network', 'wifi_password'),
                hostname=hostname_clean,
                retries=3,
                retry_interval=10
            )
            if success:
                wifi_success = True
                break
            logger.error(f"Wi-Fi connection loop {wifi_loop+1}/5 failed. Retrying in 30 seconds...")
            time.sleep(30)
            
        if not wifi_success:
            logger.error("Wi-Fi connection failed completely. Rebooting...")
            safe_reboot(5)

    # 2. データベース初期化
    if database is not None:
        db = database.Database()
        db.init_db()
        db.sync_static_hosts(hosts_config)
    else:
        db = None

    if args.cli and cli is not None:
        # CLIモード：常駐処理は起動せず、対話CLIを即座に動かす
        cli.run(db, sys_config)
        return

    if args.once:
        # 1回実行モード：単発でスケジューラ処理（TTL減算、解決、発見、同期、マージ）を実行して終了
        logger.info("Running tasks once...")
        try:
            scheduler._clean_self_from_proxies(db, sys_config)
            interval = int(sys_config.get('system', 'interval', fallback='30'))
            scheduler._cleanup_records(db, interval)
            import dns_resolver
            dns_resolver.resolve_all(db, sys_config)
            scheduler._discover_proxies(db, sys_config)
            scheduler._sync_to_others(db, sys_config)
            scheduler._merge_records(db)
            logger.info("One-time tasks completed successfully.")
        except Exception as e:
            logger.error(f"Error during one-time execution: {e}")
            sys.exit(1)
        return

    # Daemonモードのみ排他制御を行う
    logger.info("mDNS Proxy Starting in Daemon Mode...")
    if not acquire_lock():
        sys.exit(1)

    try:
        # 3. 各サーバーやプロセスの起動
        # APIサーバー(HTTP)をバックグラウンドまたはスレッド等で起動
        port = int(sys_config.get('system', 'port', fallback='80'))
        server = api_server.start_server(db, sys_config, port=port)
        if server is None:
            logger.error("API Server failed to start. Exiting.")
            sys.exit(1)
        
        # MicroPython (Pico) の場合、_threadモジュールは利用可能だが、
        # 内部的にはcore1しか使えず、すでにAPI Serverでcore1を使用しているため、
        # mDNS Serverとスケジューラーのバックグラウンド起動(さらにcore1を要求する処理)は
        # "core1 in use" エラーを引き起こす。
        # そこで、Picoの場合はmDNSリスナーとスケジューラーはメインループ上で非同期的に（手動で）回すか、
        # あるいはメインスレッドで動作させるようなアプローチが必要。
        # 今回は、APIサーバーはバックグラウンドスレッドで実行済であるため、
        # メインスレッド内で scheduler.tick() および mdns_server.tick() を呼ぶイベントループ方式にする。

        if get_platform() == 'pico':
            # 4&5. メインスレッドでのループ処理（スケジューラーとmDNSを受信ノンブロッキングで処理）
            import socket
            # mDNS初期化
            mdns_sock = mdns_server._setup_socket()
            
            # schedulerの初期化(初回実行のみ)は start に含まれていたが
            # scheduler自体のThreadを使わずに回すための仕組みが必要
            last_schedule_time = time.time()
            
            logger.info("Starting main loop for Pico (mDNS & Scheduler)...")
            while True:
                # mDNS処理(ノンブロッキング)
                if mdns_sock:
                    try:
                        mdns_sock.settimeout(0.1) # 100msでタイムアウトさせる
                        data, addr = mdns_sock.recvfrom(4096)
                        mdns_server._handle_query(db, mdns_sock, data, addr, sys_config)
                    except OSError as e:
                        # errno 110: ETIMEDOUT (MicroPython)
                        pass
                    except Exception as e:
                        logger.error(f"mDNS Error in main loop: {e}")

                # Scheduler処理 (30秒間隔等)
                current_time = time.time()
                interval = int(sys_config.get('system', 'interval', fallback='30'))
                if current_time - last_schedule_time >= interval:
                    try:
                        scheduler._clean_self_from_proxies(db, sys_config)
                        scheduler._cleanup_records(db, interval)
                        import dns_resolver
                        dns_resolver.resolve_all(db, sys_config)
                        scheduler._discover_proxies(db, sys_config)
                        scheduler._pull_from_others(db, sys_config)
                        scheduler._sync_to_others(db, sys_config)
                        scheduler._merge_records(db)
                    except Exception as e:
                        logger.error(f"Scheduler error in main loop: {e}")
                    last_schedule_time = time.time()
                    
                time.sleep(0.01) # 短くsleep
        else:
            # mDNSサーバーのリスナー起動 (Thread)
            mdns_thread = mdns_server.start_listener(db, sys_config)
            if mdns_thread is None or (hasattr(mdns_thread, "is_alive") and not mdns_thread.is_alive()):
                logger.error("mDNS Listener failed to start. Exiting.")
                sys.exit(1)

            # 4. スケジューラの開始（ループ） (Thread)
            scheduler.start(db, sys_config)

            # 5. メインループの維持（バックグラウンド動作をサポート）
            sleep_interval = 3600
            while True:
                time.sleep(sleep_interval)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        release_lock()

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        if get_platform() == 'pico':
            try:
                print(f"Fatal error: {e}")
            except:
                pass
            # エラー発生時はLEDを高速点滅させながら待機
            try:
                import machine
                led = machine.Pin("LED", machine.Pin.OUT)
                for _ in range(40): # 10秒間点滅
                    led.toggle()
                    time.sleep(0.25)
            except ImportError:
                time.sleep(5)
            safe_reboot(1)
        else:
            raise