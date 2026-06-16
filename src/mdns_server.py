try:
    import _thread as threading_fallback
    HAS_THREADING = False
except ImportError:
    threading_fallback = None

try:
    import threading
    HAS_THREADING = True
except ImportError:
    # MicroPython fallback
    pass

import socket
import select
try:
    import database
except ImportError:
    database = None

MDNS_ADDR = '224.0.0.251'
MDNS_PORT = 5353

def start_listener(db, sys_config=None):
    import sys
    if sys.platform == 'rp2' or not HAS_THREADING:
        if threading_fallback:
            threading_fallback.start_new_thread(_listen, (db, sys_config))
            return True
        else:
            # スレッドが使えない場合はメインスレッドでブロッキング実行する
            _listen(db, sys_config)
            return True
    else:
        t = threading.Thread(target=_listen, args=(db, sys_config), daemon=True)
        t.start()
        return t

def _setup_socket():
    import sys
    # UDPソケットの作成 (MicroPythonではsocket.IPPROTO_UDPが無い、または引数2つでもUDPになるためフォールバック)
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    except AttributeError:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except Exception:
        pass
    
    # OS依存のオプション
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except AttributeError:
        pass
        
    try:
        # Pico環境で既にOSが5353を掴んでいる場合は共有設定(SO_REUSEPORT相当)を利用してバインドを試みる
        if sys.platform == 'rp2':
            # SO_REUSEPORT (通常15だが環境による) を試す
            try:
                SO_REUSEPORT = getattr(socket, 'SO_REUSEPORT', 15)
                sock.setsockopt(socket.SOL_SOCKET, SO_REUSEPORT, 1)
            except Exception:
                pass
        sock.bind(('', MDNS_PORT))
    except OSError as e:
        import time
        from logger_config import logger
        logger.warning(f"[mDNS Server] Port 5353 already in use. Retrying in 5 seconds... ({e})")
        time.sleep(5)
        try:
            sock.bind(('', MDNS_PORT))
        except OSError as e2:
            logger.error(f"[mDNS Server] Could not bind to port 5353: {e2}. Listening skipped, relying on OS mDNS.")
            return None
    
    from logger_config import logger

    # マルチキャストグループに参加 (MicroPythonの定数不在エラーも考慮)
    try:
        # MicroPythonのsocketモジュールに定数がない場合は一般的な数値を直接指定する
        IPPROTO_IP = getattr(socket, 'IPPROTO_IP', 0)
        IP_ADD_MEMBERSHIP = getattr(socket, 'IP_ADD_MEMBERSHIP', 1024) # MicroPythonでの標準値、または1024等
        try:
            mreq = socket.inet_aton(MDNS_ADDR) + socket.inet_aton('0.0.0.0')
            sock.setsockopt(IPPROTO_IP, IP_ADD_MEMBERSHIP, mreq)
        except Exception:
            # もしinet_atonが無いなどの場合、4バイトのバイナリを直接構築
            ip_bin = bytes([224, 0, 0, 251]) + bytes([0, 0, 0, 0])
            sock.setsockopt(IPPROTO_IP, IP_ADD_MEMBERSHIP, ip_bin)
    except Exception as e:
        logger.error(f"[mDNS Server] Failed to join multicast group: {e}")
        return None

    # マルチキャスト送信設定: IP_MULTICAST_IF（送信IF明示）とTTL=255（②対応）
    try:
        tmp_s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            tmp_s.connect(('8.8.8.8', 80))
            primary_ip = tmp_s.getsockname()[0]
        except Exception:
            primary_ip = "0.0.0.0"
        finally:
            tmp_s.close()
        
        # Pico(MicroPython)環境ではinet_aton or IP_MULTICAST_IFが無い場合があるためtry-exceptで囲む
        try:
            IPPROTO_IP = getattr(socket, 'IPPROTO_IP', 0)
            IP_MULTICAST_IF = getattr(socket, 'IP_MULTICAST_IF', 9)
            IP_MULTICAST_TTL = getattr(socket, 'IP_MULTICAST_TTL', 10)
            
            try:
                ip_aton = socket.inet_aton(primary_ip)
            except Exception:
                ip_aton = bytes([int(p) for p in primary_ip.split('.')])
                
            sock.setsockopt(IPPROTO_IP, IP_MULTICAST_IF, ip_aton)
            sock.setsockopt(IPPROTO_IP, IP_MULTICAST_TTL, 255)
            logger.info(f"[mDNS Server] Multicast send interface set to: {primary_ip}")
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"[mDNS Server] Failed to set multicast send interface: {e}")

    logger.info("[mDNS Server] Listening on UDP 5353...")
    return sock

def _listen(db, sys_config=None):
    from logger_config import logger
    sock = _setup_socket()
    if not sock:
        return

    while True:
        try:
            data, addr = sock.recvfrom(4096)
            _handle_query(db, sock, data, addr, sys_config)
        except Exception as e:
            logger.error(f"[mDNS Server] Error: {e}")

def _get_my_ips():
    import sys
    # Pico環境の場合
    if sys.platform == 'rp2':
        try:
            import network
            wlan = network.WLAN(network.STA_IF)
            if wlan.isconnected():
                return [wlan.ifconfig()[0]]
        except Exception:
            pass
        return []
    ips = ['127.0.0.1', 'localhost']
    try:
        # ホスト名から解決
        ips.append(socket.gethostbyname(socket.gethostname()))
    except Exception:
        pass
    try:
        # ルーティングされるメインIPを取得
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(('8.8.8.8', 80))
            ips.append(s.getsockname()[0])
        finally:
            s.close()
    except Exception:
        pass
    return list(set(ips))

def _handle_query(db, sock, data, addr, sys_config=None):
    from logger_config import logger
    
    # QRビット確認: QR=1（応答パケット）は無視して早期リターン（自己ループ防止）
    if len(data) >= 3 and (data[2] & 0x80):
        return
    
    queried_hostname = _extract_hostname(data)
    if not queried_hostname:
        return

    # サービスタイプクエリ（アンダースコアで始まるサービス名）は名前解決プロキシの対象外として早期リターン
    if queried_hostname.startswith('_'):
        return

    # 自己解決（自己参照）ループ防止ガード
    # ipaddressモジュールはMicroPythonに存在しないため、文字列で簡易判定する
    is_loop = addr[0].startswith('127.') or addr[0] == '::1'

    my_ips = _get_my_ips()
    
    if sys_config and sys_config.has_section('network') and sys_config.has_option('network', 'mdns_hostname'):
        my_hostname = sys_config.get('network', 'mdns_hostname')
    else:
        try:
            my_hostname = socket.gethostname()
        except Exception:
            my_hostname = "mdns-proxy"
            
    is_query_for_me = (queried_hostname.lower() == my_hostname.lower() or 
                       queried_hostname.lower() == my_hostname.lower() + '.local')

    if (is_loop or addr[0] in my_ips) and is_query_for_me:
        return
        
    logger.info(f"[mDNS Server] Received query for: {queried_hostname} from {addr}")

    # 自身のホスト名のクエリかチェック
    if is_query_for_me:
        # 自身のIPアドレスを取得
        ip = None
        if my_ips:
            ip = my_ips[0]
        else:
            try:
                # 簡易的にUDPソケットを使って外部に接続するふりをして自身のIPを取得する
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    s.connect(('8.8.8.8', 80))
                    ip = s.getsockname()[0]
                finally:
                    s.close()
            except Exception as e:
                pass
        
        if ip:
            ttl = 120
            row = (ip, ttl)
        else:
            row = None
    else:
        if db is not None:
            with db.connection() as conn:
                cursor = conn.cursor()
                base_name = queried_hostname[:-6] if queried_hostname.endswith('.local') else queried_hostname
                local_name = base_name + '.local'
                cursor.execute(
                    'SELECT ip_address, ttl FROM merged_records WHERE hostname = ? OR hostname = ? OR hostname = ?',
                    (queried_hostname, base_name, local_name)
                )
                row = cursor.fetchone()
        else:
            row = None
        
    if row:
        ip, ttl = row
        # 応答パケットの構築
        response = _build_response(data, queried_hostname, ip, ttl)
        if response:
            from logger_config import logger
            # 受信用のソケット(5353ポートにバインド済み)を再利用して送信する
            # ※mDNSクライアントは送信元ポートが5353以外の応答を無視するため
            try:
                # クエリ送信元へユニキャスト
                sock.sendto(response, addr)
                # mDNSマルチキャストグループへも送信（ポート5353）
                # ここで IP_MULTICAST_IF の設定等が必要かもしれないが、簡易的に別ソケットから送信
                try:
                    mc_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    try:
                        try:
                            IPPROTO_IP = getattr(socket, 'IPPROTO_IP', 0)
                            IP_MULTICAST_TTL = getattr(socket, 'IP_MULTICAST_TTL', 10)
                            mc_sock.setsockopt(IPPROTO_IP, IP_MULTICAST_TTL, 255)
                        except Exception:
                            pass
                        mc_sock.sendto(response, ('224.0.0.251', 5353))
                    finally:
                        mc_sock.close()
                except Exception as me:
                    logger.warning(f"[mDNS Server] Failed to send multicast: {me}")
            except Exception as e:
                logger.error(f"[mDNS Server] Failed to send response: {e}")
            logger.info(f"[mDNS Server] Replied to {addr} and multicast for {queried_hostname} -> {ip}")

def _extract_hostname(data):
    try:
        if len(data) < 12:
            return None
        # ヘッダー (12 bytes)
        # 質問数を取得
        qdcount = (data[4] << 8) | data[5]
        if qdcount == 0:
            return None
            
        offset = 12
        parts = []
        while True:
            if offset >= len(data):
                return None
            length = data[offset]
            if length == 0:
                offset += 1
                break
            if (length & 0xC0) == 0xC0:
                # ポインタ（ここでは簡易的に無視、通常クエリでは先頭に来るため）
                offset += 2
                break
            offset += 1
            parts.append(data[offset:offset+length].decode('utf-8'))
            offset += length
            
        if parts:
            return ".".join(parts)
    except Exception as e:
        pass
    return None

def _build_response(query_data, hostname, ip, ttl):
    from logger_config import logger
    try:
        # TTL値が無効な時の安全な補完（デフォルト値を120とする）
        if ttl is None or not isinstance(ttl, int) or ttl < 0:
            ttl = 120

        # トランザクションIDをコピー
        tx_id = query_data[0:2]
        
        # Flags: 0x8400 (Authoritative Response)
        flags = (0x8400).to_bytes(2, 'big')
        
        # QDCOUNT=1, ANCOUNT=1, NSCOUNT=0, ARCOUNT=0（①対応: QDCOUNTを1に修正）
        counts = (1).to_bytes(2, 'big') + (1).to_bytes(2, 'big') + (0).to_bytes(2, 'big') + (0).to_bytes(2, 'big')
        
        header = tx_id + flags + counts
        
        # QNAME の構築（質問セクション用）
        name_parts = hostname.split('.')
        qname = bytes()
        for part in name_parts:
            qname += bytes([len(part)]) + part.encode('utf-8')
        qname += bytes([0])  # ルートラベル終端 (0x00)
        
        # 質問セクション: QNAME + QTYPE=A(1) + QCLASS=IN(1)（①対応: 質問セクションを追加）
        question_section = qname + (1).to_bytes(2, 'big') + (1).to_bytes(2, 'big')
        
        # アンサーセクション
        # NAME: ヘッダー(12バイト)直後のQNAMEへのDNS圧縮ポインタ (0xC00C)
        ans_name = bytes([0xC0, 0x0C])
        # Type A (1), Class IN (0x0001) - cache-flush bit なし（③対応）
        type_class = (1).to_bytes(2, 'big') + (0x0001).to_bytes(2, 'big')
        
        # TTL
        ttl_bytes = ttl.to_bytes(4, 'big')
        
        # RDLENGTH (4 bytes for IPv4)
        rdlength = (4).to_bytes(2, 'big')
        
        # RDATA (IP Address)
        ip_parts = ip.split('.')
        rdata = bytes([int(p) for p in ip_parts])
        
        answer_section = ans_name + type_class + ttl_bytes + rdlength + rdata
        
        response = header + question_section + answer_section
        return response
    except Exception as e:
        logger.error(f"[mDNS Server] Failed to build response for hostname={hostname}, ip={ip}, ttl={ttl}: {e}", exc_info=True)
        return None