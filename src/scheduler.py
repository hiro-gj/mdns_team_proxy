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
    # 起動時に一度マイグレーションを適用し、不整合な自ノード登録をクリーンアップする
    try:
        _clean_self_from_proxies(db, sys_config)
    except Exception as e:
        logger.error(f"[Scheduler] Failed to clean self from proxies at startup: {e}")

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

            # 3.5. 他のプロキシからレコードをプル（自発的プル同期）
            _pull_from_others(db, sys_config)

            # 4. レコードを他のプロキシに送信（中継・収束方式：self_records + 有効な other_records を同期）
            _sync_to_others(db, sys_config)

            # 5. マージ処理（self_records + other_records -> merged_records）
            _merge_records(db)

        except Exception as e:
            logger.error(f"[Scheduler] Error: {e}")
            interval = int(sys_config.get('system', 'interval', fallback='30'))
        
        time.sleep(interval)

def _clean_self_from_proxies(db, sys_config):
    my_node_id = _get_node_id(sys_config)
    import mdns_server
    my_ips = mdns_server._get_my_ips()
    with db.connection() as conn:
        cursor = conn.cursor()
        # 自分自身の node_id や IP アドレスを持つプロキシを削除
        for ip in my_ips:
            cursor.execute('DELETE FROM other_proxies WHERE node_id = ? OR ip_address = ?', (my_node_id, ip))
        cursor.execute('DELETE FROM other_proxies WHERE node_id = ?', (my_node_id,))
        conn.commit()

def start(db, sys_config):
    t = threading.Thread(target=loop_task, args=(db, sys_config), daemon=True)
    t.start()
    return t

def _discover_proxies(db, sys_config):
    # network セクションから external_proxies を取得し、other_proxies に登録する
    if not sys_config.has_option('network', 'external_proxies'):
        return
    proxies = sys_config.get('network', 'external_proxies').split(',')
    
    my_node_id = _get_node_id(sys_config)
    import mdns_server
    my_ips = mdns_server._get_my_ips()

    with db.connection() as conn:
        cursor = conn.cursor()
        for proxy in proxies:
            proxy = proxy.strip()
            if not proxy: continue
            
            # IPとポートを分離 (ex: 192.168.1.10:53080 or 192.168.1.10)
            if ':' in proxy:
                ip, port_str = proxy.split(':', 1)
                try:
                    port = int(port_str)
                except ValueError:
                    port = 53080
            else:
                ip = proxy
                port = 53080
                
            # 自分自身（IPアドレス）の場合は登録しない
            if ip in my_ips:
                continue

            # 旧DBスキーマでは ip_address に UNIQUE 制約がついているため、
            # ip_address で既存レコードを確認し、存在すれば UPDATE、なければ INSERT する
            cursor.execute('SELECT proxy_id, node_id FROM other_proxies WHERE ip_address = ?', (ip,))
            row = cursor.fetchone()
            if row:
                proxy_id, existing_node_id = row
                if existing_node_id == my_node_id:
                    # 万が一自分自身が登録されていれば削除
                    cursor.execute('DELETE FROM other_proxies WHERE proxy_id = ?', (proxy_id,))
                else:
                    # ポートや探索方法を更新
                    cursor.execute(
                        'UPDATE other_proxies SET port = ?, discovery_method = ? WHERE proxy_id = ?',
                        (port, 'fixed', proxy_id)
                    )
            else:
                cursor.execute(
                    'INSERT INTO other_proxies (ip_address, port, token, discovery_method) VALUES (?, ?, ?, ?)',
                    (ip, port, 'dummy_token', 'fixed')
                )
        conn.commit()

import urllib.request
import json

def _sync_to_others(db, sys_config):
    # self_records + 有効な other_records と static_hosts の内容を HTTP(POST) で other_proxies に送信する（中継・収束方式）
    my_node_id = _get_node_id(sys_config)
    my_port = sys_config.get('system', 'port', fallback='53080')

    with db.connection() as conn:
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

        # 中継・収束方式：他のノードから取得した other_records も、TTLが有効であれば再配布対象に含める
        # これにより、フルメッシュでなくても情報が中継される
        cursor.execute(
            '''
            SELECT r.hostname, r.ip_address, r.record_type, r.ttl 
            FROM other_records r
            JOIN other_proxies p ON r.source_proxy_id = p.proxy_id
            WHERE r.ttl > 0 AND (p.node_id IS NULL OR p.node_id != ?)
            ''',
            (my_node_id,)
        )
        for row in cursor.fetchall():
            # 重複を排除しつつ追加
            if not any(r['hostname'] == row[0] and r['ip_address'] == row[1] for r in records):
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
        
        # 同期先プロキシの取得
        cursor.execute('SELECT node_id, ip_address, port FROM other_proxies WHERE is_active = 1')
        proxies = cursor.fetchall()
        
    if not proxies:
        return
        
    token_prefix = sys_config.get('system', 'token_prefix', fallback='mDNSProxy_')
    import socket
    # ホスト名が衝突しても一意性を保つため、UUIDベースの短縮ID(node_id)を組み合わせる
    token = f"{token_prefix}{socket.gethostname()}_{my_node_id}"
    
    # other-records 送信
    if records:
        data_records = json.dumps({"records": records}).encode('utf-8')
        for dest_node_id, proxy_ip, port in proxies:
            # 自ノードへの送信ループバックを防止
            if dest_node_id == my_node_id:
                continue

            # コロン付きの古い ip_address 形式に対するパース処理
            if ':' in proxy_ip:
                actual_ip, port_str = proxy_ip.split(':', 1)
                try:
                    actual_port = int(port_str)
                except ValueError:
                    actual_port = 53080
            else:
                actual_ip = proxy_ip
                actual_port = port

            url = f"http://{actual_ip}:{actual_port}/api/other-records"
            req = urllib.request.Request(url, data=data_records, method='POST')
            req.add_header('Content-Type', 'application/json')
            req.add_header('Authorization', f'Token {token}')
            req.add_header('X-Sender-Node-ID', my_node_id)
            req.add_header('X-Sender-Port', str(my_port))
            req.add_header('Content-Length', str(len(data_records)))
            
            try:
                with urllib.request.urlopen(req, timeout=5) as response:
                    pass
            except Exception as e:
                logger.error(f"[_sync_to_others] Failed to sync records with {actual_ip}:{actual_port}: {e}")

    # static-hosts 送信
    if static_hosts:
        data_hosts = json.dumps({"hosts": static_hosts}).encode('utf-8')
        for dest_node_id, proxy_ip, port in proxies:
            if dest_node_id == my_node_id:
                continue

            # コロン付きの古い ip_address 形式に対するパース処理
            if ':' in proxy_ip:
                actual_ip, port_str = proxy_ip.split(':', 1)
                try:
                    actual_port = int(port_str)
                except ValueError:
                    actual_port = 53080
            else:
                actual_ip = proxy_ip
                actual_port = port

            url = f"http://{actual_ip}:{actual_port}/api/static-hosts"
            req = urllib.request.Request(url, data=data_hosts, method='POST')
            req.add_header('Content-Type', 'application/json')
            req.add_header('Authorization', f'Token {token}')
            req.add_header('X-Sender-Node-ID', my_node_id)
            req.add_header('X-Sender-Port', str(my_port))
            req.add_header('Content-Length', str(len(data_hosts)))
            
            try:
                with urllib.request.urlopen(req, timeout=5) as response:
                    pass
            except Exception as e:
                logger.error(f"[_sync_to_others] Failed to sync static_hosts with {actual_ip}:{actual_port}: {e}")

def _get_my_ips_with_masks():
    import sys
    import socket
    res = []
    
    # Pico (MicroPython) の場合
    if sys.platform == 'rp2':
        try:
            import network
            wlan = network.WLAN(network.STA_IF)
            if wlan.isconnected():
                ifconfig = wlan.ifconfig()
                res.append((ifconfig[0], ifconfig[1]))
        except Exception:
            pass
        return res
        
    # 通常の Python (Linux / Windows) の場合
    ips = []
    try:
        ips.append(socket.gethostbyname(socket.gethostname()))
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(('8.8.8.8', 80))
            ips.append(s.getsockname()[0])
        finally:
            s.close()
    except Exception:
        pass
        
    ips = list(set(ips))
    
    if sys.platform != 'win32':
        try:
            import subprocess
            out = subprocess.check_output(['ip', '-o', 'addr', 'show'], stderr=subprocess.DEVNULL).decode('utf-8')
            for line in out.splitlines():
                parts = line.split()
                if 'inet' in parts:
                    idx = parts.index('inet')
                    ip_cidr = parts[idx+1]
                    if '/' in ip_cidr:
                        ip, cidr = ip_cidr.split('/')
                        if not ip.startswith('127.'):
                            cidr_num = int(cidr)
                            mask_int = (0xffffffff << (32 - cidr_num)) & 0xffffffff
                            mask = f"{(mask_int >> 24) & 0xff}.{(mask_int >> 16) & 0xff}.{(mask_int >> 8) & 0xff}.{mask_int & 0xff}"
                            res.append((ip, mask))
        except Exception:
            pass
            
    if not res:
        for ip in ips:
            if not ip.startswith('127.'):
                res.append((ip, "255.255.255.0"))
                
    return res

def is_in_my_subnet(target_ip, my_ips_with_masks):
    if target_ip.startswith('127.') or target_ip == '::1':
        return True
    try:
        target_parts = [int(p) for p in target_ip.split('.')]
        target_int = (target_parts[0] << 24) + (target_parts[1] << 16) + (target_parts[2] << 8) + target_parts[3]
    except Exception:
        return False
        
    for my_ip, mask in my_ips_with_masks:
        try:
            my_parts = [int(p) for p in my_ip.split('.')]
            mask_parts = [int(p) for p in mask.split('.')]
            
            my_int = (my_parts[0] << 24) + (my_parts[1] << 16) + (my_parts[2] << 8) + my_parts[3]
            mask_int = (mask_parts[0] << 24) + (mask_parts[1] << 16) + (mask_parts[2] << 8) + mask_parts[3]
            
            if (target_int & mask_int) == (my_int & mask_int):
                return True
        except Exception:
            continue
    return False

def _merge_records(db):
    my_ips_with_masks = _get_my_ips_with_masks()

    with db.connection() as conn:
        cursor = conn.cursor()
        
        # 1. すべての self_records と other_records を読み出す
        cursor.execute('SELECT hostname, ip_address, record_type, ttl, resolution_method, updated_at, record_id FROM self_records')
        self_rows = cursor.fetchall()
        
        cursor.execute('SELECT hostname, ip_address, record_type, ttl, received_at, record_id FROM other_records')
        other_rows = cursor.fetchall()

        candidates = []
        
        # self_records からの候補
        for row in self_rows:
            hostname, ip, record_type, ttl, resolution_method, updated_at, record_id = row
            if ip.startswith('127.') or ip == '::1':
                continue
                
            is_self = is_in_my_subnet(ip, my_ips_with_masks)
            source_type = 'self' if is_self else 'other'
            
            if resolution_method == 'static':
                priority = 1
            elif is_self:
                priority = 2
            else:
                priority = 3
                
            candidates.append({
                'hostname': hostname,
                'ip_address': ip,
                'record_type': record_type,
                'ttl': ttl,
                'source_type': source_type,
                'source_record_id': record_id,
                'registered_at': updated_at,
                'priority': priority
            })

        # other_records からの候補
        for row in other_rows:
            hostname, ip, record_type, ttl, received_at, record_id = row
            if ip.startswith('127.') or ip == '::1':
                continue
                
            is_self = is_in_my_subnet(ip, my_ips_with_masks)
            source_type = 'self' if is_self else 'other'
            
            priority = 2 if is_self else 3
            
            candidates.append({
                'hostname': hostname,
                'ip_address': ip,
                'record_type': record_type,
                'ttl': ttl,
                'source_type': source_type,
                'source_record_id': record_id,
                'registered_at': received_at,
                'priority': priority
            })

        by_host = {}
        for c in candidates:
            host = c['hostname']
            if host not in by_host:
                by_host[host] = []
            by_host[host].append(c)

        merged = []
        for host, items in by_host.items():
            # 優先順位：
            # 1. priority ASC (1=static_self, 2=dynamic_self, 3=other)
            # 2. registered_at DESC (最新の登録日時を優先)
            # 3. source_record_id DESC (同一日時ならIDが大きい方を優先)
            items.sort(key=lambda x: (x['priority'], x['registered_at'] or '', -x['source_record_id']))
            best = items[0]
            merged.append(best)

        # 2. merged_records テーブルをクリアして、新しいマージ結果を書き込む
        cursor.execute('DELETE FROM merged_records')
        for m in merged:
            cursor.execute(
                '''
                INSERT INTO merged_records (hostname, ip_address, record_type, ttl, source_type, source_record_id)
                VALUES (?, ?, ?, ?, ?, ?)
                ''',
                (m['hostname'], m['ip_address'], m['record_type'], m['ttl'], m['source_type'], m['source_record_id'])
            )
        conn.commit()

def _pull_from_others(db, sys_config):
    # network セクションから external_proxies を取得し、それらの /api/merged-records からレコードをプルする
    if not sys_config.has_option('network', 'external_proxies'):
        return
    proxies = sys_config.get('network', 'external_proxies').split(',')
    
    my_node_id = _get_node_id(sys_config)
    import mdns_server
    my_ips = mdns_server._get_my_ips()

    # MicroPython の場合は urequests を使い、それ以外（通常の Python）は urllib.request を使う
    is_pico = False
    try:
        import urequests
        is_pico = True
    except ImportError:
        import urllib.request
        import json

    for proxy in proxies:
        proxy = proxy.strip()
        if not proxy: continue
        
        if ':' in proxy:
            ip, port_str = proxy.split(':', 1)
            try:
                port = int(port_str)
            except ValueError:
                port = 53080
        else:
            ip = proxy
            port = 53080
            
        if ip in my_ips:
            continue

        url = f"http://{ip}:{port}/api/merged-records"
        try:
            records = []
            if is_pico:
                res = urequests.get(url, timeout=5)
                if res.status_code == 200:
                    records = res.json()
                res.close()
            else:
                req = urllib.request.Request(url, method='GET')
                with urllib.request.urlopen(req, timeout=5) as response:
                    if response.status == 200:
                        records = json.loads(response.read().decode('utf-8'))
            
            if not records:
                continue

            with db.connection() as conn:
                cursor = conn.cursor()
                # 相手プロキシの ID を取得または挿入
                cursor.execute('SELECT proxy_id FROM other_proxies WHERE ip_address = ?', (ip,))
                row = cursor.fetchone()
                if row:
                    proxy_id = row[0]
                else:
                    cursor.execute(
                        'INSERT INTO other_proxies (ip_address, port, token, discovery_method) VALUES (?, ?, ?, ?)',
                        (ip, port, 'dummy_token', 'fixed')
                    )
                    proxy_id = cursor.lastrowid
                
                # 取得したレコードを other_records に格納 (既存のそのプロキシからのレコードを更新)
                cursor.execute('DELETE FROM other_records WHERE source_proxy_id = ?', (proxy_id,))
                for r in records:
                    cursor.execute(
                        'INSERT INTO other_records (source_proxy_id, hostname, ip_address, record_type, ttl) VALUES (?, ?, ?, ?, ?)',
                        (proxy_id, r['hostname'], r['ip_address'], r['record_type'], r['ttl'])
                    )
                conn.commit()
                logger.info(f"[_pull_from_others] Pulled {len(records)} records from {ip}:{port}")
        except Exception as e:
            logger.error(f"[_pull_from_others] Failed to pull records from {ip}:{port}: {e}")

def _cleanup_records(db, interval):
    with db.connection() as conn:
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
