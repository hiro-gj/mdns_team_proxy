import sys
import time
from logger_config import disable_console_logging, enable_console_logging

def run(db, sys_config):
    disable_console_logging()
    while True:
        print("\n--- mDNS Proxy CLI ---")
        print("1. マージ済みレコードの表示")
        print("2. 静的ホストの表示")
        print("3. 静的ホストの追加")
        print("4. 静的ホストの削除")
        print("5. インスタント実行 (Ctrl+Cで終了)")
        print("6. 終了")
        
        try:
            choice = input("オプションを選択してください (1-6): ")
            
            if choice == '1':
                _show_merged_records(db)
            elif choice == '2':
                _show_static_hosts(db)
            elif choice == '3':
                _add_static_host(db)
            elif choice == '4':
                _remove_static_host(db)
            elif choice == '5':
                _instant_execution_mode(db, sys_config)
            elif choice == '6':
                break
            else:
                print("無効な選択です。もう一度お試しください。")
        except KeyboardInterrupt:
            print("\n終了します。")
            break
        except EOFError:
            print("\n終了します。")
            break

def _instant_execution_mode(db, sys_config):
    enable_console_logging()
    print("\n--- インスタント実行モード ---")
    print("Ctrl+C を押すと終了します。")
    try:
        while True:
            # Here we just keep the CLI active or you can integrate with some execution logic.
            # Usually instant execution means running the main proxy loop or a specific task repeatedly.
            # Assuming it just waits or triggers some instant syncs. We will just sleep and print status.
            print("実行中... (終了するには Ctrl+C)")
            time.sleep(int(sys_config.get('system', 'interval', fallback=30)))
    except KeyboardInterrupt:
        print("\nインスタント実行モードを終了しました。")
    finally:
        disable_console_logging()

def _show_merged_records(db):
    print("\n--- マージ済みレコード ---")
    with db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT hostname, ip_address, source_type FROM merged_records')
        rows = cursor.fetchall()
        if not rows:
            print("レコードが見つかりません。")
        for row in rows:
            print(f"[{row[2]}] {row[0]} -> {row[1]}")

def _show_static_hosts(db):
    print("\n--- 静的ホスト ---")
    with db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT host_id, hostname FROM static_hosts')
        rows = cursor.fetchall()
        if not rows:
            print("静的ホストが見つかりません。")
        for row in rows:
            print(f"ID: {row[0]}, ホスト: {row[1]}")

def _add_static_host(db):
    hostname = input("ホスト名 (FQDN) を入力してください: ")
    if not hostname:
        return
    with db.connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute('INSERT INTO static_hosts (hostname) VALUES (?)', (hostname,))
            conn.commit()
            print(f"{hostname} を追加しました。")
        except Exception as e:
            print(f"エラー: {e}")

def _remove_static_host(db):
    _show_static_hosts(db)
    host_id = input("削除するIDを入力してください: ")
    if not host_id.isdigit():
        return
    with db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM static_hosts WHERE host_id = ?', (host_id,))
        if cursor.rowcount > 0:
            conn.commit()
            print(f"ID {host_id} を削除しました。")
        else:
            print("指定されたIDは見つかりません。")
