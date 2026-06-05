import os
import sys
import time

def get_platform():
    try:
        import machine
        return 'pico'
    except ImportError:
        return 'windows' if os.name == 'nt' else 'linux'

# ログディレクトリとDBディレクトリの作成
if get_platform() != 'pico':
    for dir_name in ['log', 'db']:
        os.makedirs(os.path.join(os.path.dirname(os.path.dirname(__file__)), dir_name), exist_ok=True)

import config
import database
import scheduler
import mdns_server
import api_server
import cli
from logger_config import logger

import argparse

# 排他制御用ロックファイルのパス
def get_lock_file_path():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, 'db', 'mdns_proxy.lock')

def acquire_lock():
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
    parser = argparse.ArgumentParser(description="mDNS Proxy Daemon & CLI Utility")
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument('--daemon', action='store_true', help='Run as service daemon')
    group.add_argument('--cli', action='store_true', help='Run interactive CLI only')
    group.add_argument('--once', action='store_true', help='Run scheduler tasks once and exit')
    
    args = parser.parse_args()

    # 引数無しで起動された場合は、デフォルトでcliモード扱いとする
    if not (args.daemon or args.cli or args.once):
        args.cli = True

    # 1. 設定読み込み
    sys_config = config.load_system_config()
    hosts_config = config.load_hosts_config()

    # Picoの場合はWi-Fi接続
    if get_platform() == 'pico':
        import wifi_manager
        wifi_manager.connect(sys_config.get('network', 'wifi_ssid'), sys_config.get('network', 'wifi_password'))

    # 2. データベース初期化
    db = database.Database()
    db.init_db()
    db.sync_static_hosts(hosts_config)

    if args.cli:
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
        
        # mDNSサーバーのリスナー起動
        mdns_thread = mdns_server.start_listener(db)
        if mdns_thread is None or not mdns_thread.is_alive():
            logger.error("mDNS Listener failed to start. Exiting.")
            sys.exit(1)

        # 4. スケジューラの開始（ループ）
        scheduler.start(db, sys_config)

        # 5. メインループの維持（バックグラウンド動作をサポート）
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        release_lock()

if __name__ == '__main__':
    main()
