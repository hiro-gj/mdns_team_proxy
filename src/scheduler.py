import threading
import time
import dns_resolver

from logger_config import logger

def _get_node_id(sys_config):
    import os
    import uuid
    from config import get_base_dir
    # system.ini から node_id を取得
    if sys_config.has_section('system') and sys_config.has_option('system', 'node_id'):
        return sys_config.get('system', 'node_id')
    
    # なければ自動生成
    node_id = str(uuid.uuid4())[:8]
    if not sys_config.has_section('system'):
        sys_config.add_section('system')
    sys_config.set('system', 'node_id', node_id)
    
    # system.ini に書き戻す
    try:
        path = os.path.join(get_base_dir(), 'system.ini')
        with open(path, 'w', encoding='utf-8') as f:
            sys_config.write(f)
    except Exception as e:
        logger.error(f"[_get_node_id] Failed to save node_id to system.ini: {e}")
    return node_id

def loop_task(db, sys_config):
    while True:
        try:
            logger.info("[Scheduler] Running periodic tasks...")
            interval = int(sys_config.get('system', 'interval', fallback='30'))

            # 1. TTL減算とクリーンアップ（期限切れを先に排除し、同期やマージへの混入を防ぐ）
            _cleanup_records(db, interval)

            # 2. 独自DNS名前解決（static_hosts -> self_records）
            dns_resolver.resolve_all(db, sys_config)

            # 3. プロキシ発見（固定IPから）
            _discover_proxies(db, sys_config)

            # 4. 自レコード(self_records)を他のプロキシに送信
            _sync_to_others(db, sys_config)

            # 5. マージ処理（self_records + other_records -> merged_records）
            _merge_records(db)

        except Exception as e:
            logger.error(f"[Scheduler] Error: {e}")
            interval = int(sys_config.get('system', 'interval', fallback='30'))
        
        time.sleep(interval)

def start(db, sys_config):
    t = threading.Thread(target=loop_task, args=(db, sys_config), daemon=True)
    t.start()
    return t

def _discover_proxies(db, sys_config):
    # network セクションから external_proxies を取得し、other_proxies に登録する簡易実装
    if not sys_config.has_option('network', 'external_proxies'):
        return
    proxies = sys_config.get('network', 'external_proxies').split(',')
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        for proxy in proxies:
            proxy = proxy.strip()
            if not proxy: continue
            
            # IPとポートを分離 (ex: 192.168.1.10:8080 or 192.168.1.10)
            if ':' in proxy:
                ip, port = proxy.split(':', 1)
            else:
                ip = proxy
                port = '80'
                
            # TODO: dbのother_proxiesスキーマにport列があれば保存するが、現状ip_addressとして扱うか？
            # 一旦、ip_address列に「ip:port」の形式で保存するように変更する。（後続の通信処理でポート番号を利用できるように）
            # もしスキーマにportがないなら、ip_addressにコロン付きで登録しておく
            cursor.execute('SELECT proxy_id FROM other_proxies WHERE ip_address = ?', (proxy,))
            if not cursor.fetchone():
                cursor.execute(
                    'INSERT INTO other_proxies (ip_address, token, discovery_method) VALUES (?, ?, ?)',
                    (proxy, 'dummy_token', 'fixed')
                )
        conn.commit()

import urllib.request
import json

def _sync_to_others(db, sys_config):
    # self_records と static_hosts の内容を HTTP(POST) で other_proxies に送信する
    with db.get_connection() as conn:
        cursor = conn.cursor()
        
        # 1. self_records の取得
        cursor.execute('SELECT hostname, ip_address, record_type, ttl FROM self_records')
        records = []
        for row in cursor.fetchall():
            records.append({
                "hostname": row[0],
                "ip_address": row[1],
                "record_type": row[2],
                "ttl": row[3]
            })
            
        # 2. static_hosts の取得
        cursor.execute('SELECT hostname FROM static_hosts')
        static_hosts = []
        for row in cursor.fetchall():
            static_hosts.append({"hostname": row[0]})
        
        cursor.execute('SELECT ip_address FROM other_proxies')
        proxies = cursor.fetchall()
        
    if not proxies:
        return
        
    token_prefix = sys_config.get('system', 'token_prefix', fallback='mDNSProxy_')
    import socket
    node_id = _get_node_id(sys_config)
    # ホスト名が衝突しても一意性を保つため、UUIDベースの短縮ID(node_id)を組み合わせる
    token = f"{token_prefix}{socket.gethostname()}_{node_id}"
    
    # other-records 送信
    if records:
        data_records = json.dumps({"records": records}).encode('utf-8')
        for (proxy_ip,) in proxies:
            url = f"http://{proxy_ip}/api/other-records"
            req = urllib.request.Request(url, data=data_records, method='POST')
            req.add_header('Content-Type', 'application/json')
            req.add_header('Authorization', f'Token {token}')
            req.add_header('Content-Length', str(len(data_records)))
            
            try:
                with urllib.request.urlopen(req, timeout=5) as response:
                    pass
            except Exception as e:
                logger.error(f"[_sync_to_others] Failed to sync records with {proxy_ip}: {e}")

    # static-hosts 送信
    if static_hosts:
        data_hosts = json.dumps({"hosts": static_hosts}).encode('utf-8')
        for (proxy_ip,) in proxies:
            url = f"http://{proxy_ip}/api/static-hosts"
            req = urllib.request.Request(url, data=data_hosts, method='POST')
            req.add_header('Content-Type', 'application/json')
            req.add_header('Authorization', f'Token {token}')
            req.add_header('Content-Length', str(len(data_hosts)))
            
            try:
                with urllib.request.urlopen(req, timeout=5) as response:
                    pass
            except Exception as e:
                logger.error(f"[_sync_to_others] Failed to sync static_hosts with {proxy_ip}: {e}")

def _merge_records(db):
    with db.get_connection() as conn:
        cursor = conn.cursor()
        # 一旦全クリア
        cursor.execute('DELETE FROM merged_records')
        
        # self_records（自ノード解決）と other_records（他ノード同期）から、
        # ホスト名（hostname）ごとに最良の1件のIPアドレスのみを決定してマージする（最新・最良優先マージアルゴリズム）。
        # 優先順位：1. 手動固定IP（resolution_method = 'static'）を最優先。
        #           2. それ以外（自動名前解決）は、登録・更新日時（registered_at）がより新しいものを優先。
        cursor.execute('''
            WITH candidates AS (
                SELECT 
                    hostname, 
                    ip_address, 
                    record_type, 
                    ttl, 
                    'self' as source_type, 
                    record_id as source_record_id,
                    updated_at as registered_at,
                    CASE WHEN resolution_method = 'static' THEN 1 ELSE 2 END as priority
                FROM self_records
                WHERE ip_address NOT LIKE '127.%' AND ip_address != '::1'

                UNION ALL

                SELECT 
                    hostname, 
                    ip_address, 
                    record_type, 
                    ttl, 
                    'other' as source_type, 
                    record_id as source_record_id,
                    received_at as registered_at,
                    2 as priority
                FROM other_records
                WHERE ip_address NOT LIKE '127.%' AND ip_address != '::1'
            ),
            ranked AS (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY hostname 
                           ORDER BY priority ASC, registered_at DESC, source_record_id DESC
                       ) as rn
                FROM candidates
            )
            INSERT INTO merged_records (hostname, ip_address, record_type, ttl, source_type, source_record_id)
            SELECT hostname, ip_address, record_type, ttl, source_type, source_record_id
            FROM ranked
            WHERE rn = 1
        ''')
        
        conn.commit()

def _cleanup_records(db, interval):
    with db.get_connection() as conn:
        cursor = conn.cursor()
        
        # self_records の TTL 減算と削除
        cursor.execute('UPDATE self_records SET ttl = ttl - ?', (interval,))
        cursor.execute('DELETE FROM self_records WHERE ttl <= 0')
        deleted_self = cursor.rowcount
        
        # other_records の TTL 減算と削除
        cursor.execute('UPDATE other_records SET ttl = ttl - ?', (interval,))
        cursor.execute('DELETE FROM other_records WHERE ttl <= 0')
        deleted_other = cursor.rowcount
        
        conn.commit()
        
        total_deleted = deleted_self + deleted_other
        if total_deleted > 0:
            logger.info(f"[Scheduler] Removed {total_deleted} expired records during cleanup")
