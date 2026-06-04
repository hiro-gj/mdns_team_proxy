import threading
import socket
import select
import database

MDNS_ADDR = '224.0.0.251'
MDNS_PORT = 5353

def start_listener(db):
    t = threading.Thread(target=_listen, args=(db,), daemon=True)
    t.start()
    return t

def _listen(db):
    # UDPソケットの作成
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    
    # OS依存のオプション
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except AttributeError:
        pass
        
    sock.bind(('', MDNS_PORT))
    
    from logger_config import logger

    # マルチキャストグループに参加
    try:
        mreq = socket.inet_aton(MDNS_ADDR) + socket.inet_aton('0.0.0.0')
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    except Exception as e:
        logger.error(f"[mDNS Server] Failed to join multicast group: {e}")
        return

    logger.info("[mDNS Server] Listening on UDP 5353...")
    
    while True:
        try:
            data, addr = sock.recvfrom(4096)
            _handle_query(db, sock, data, addr)
        except Exception as e:
            logger.error(f"[mDNS Server] Error: {e}")

def _get_my_ips():
    ips = ['127.0.0.1', 'localhost']
    try:
        # ホスト名から解決
        ips.append(socket.gethostbyname(socket.gethostname()))
    except Exception:
        pass
    try:
        # ルーティングされるメインIPを取得
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('8.8.8.8', 80))
            ips.append(s.getsockname()[0])
    except Exception:
        pass
    return list(set(ips))

def _handle_query(db, sock, data, addr):
    from logger_config import logger
    import ipaddress
    
    # 自己解決（自己参照）ループ防止ガード
    # 自分自身（プロキシノード本体やループバックアドレス全体）からの名前解決クエリに対しては応答を返さないようにする
    try:
        is_loop = ipaddress.ip_address(addr[0]).is_loopback
    except ValueError:
        is_loop = False

    my_ips = _get_my_ips()
    if is_loop or addr[0] in my_ips:
        return

    queried_hostname = _extract_hostname(data)
    if not queried_hostname:
        return
        
    logger.info(f"[mDNS Server] Received query for: {queried_hostname} from {addr}")

    # 自身のホスト名のクエリかチェック
    my_hostname = socket.gethostname()
    if queried_hostname.lower() == my_hostname.lower() or queried_hostname.lower() == my_hostname.lower() + '.local':
        # 自身のIPアドレスを取得
        try:
            # 簡易的にUDPソケットを使って外部に接続するふりをして自身のIPを取得する
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(('8.8.8.8', 80))
                ip = s.getsockname()[0]
            ttl = 120
            row = (ip, ttl)
        except Exception as e:
            row = None
    else:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            base_name = queried_hostname[:-6] if queried_hostname.endswith('.local') else queried_hostname
            local_name = base_name + '.local'
            cursor.execute(
                'SELECT ip_address, ttl FROM merged_records WHERE hostname = ? OR hostname = ? OR hostname = ?',
                (queried_hostname, base_name, local_name)
            )
            row = cursor.fetchone()
        
    if row:
        ip, ttl = row
        # 応答パケットの構築
        response = _build_response(data, queried_hostname, ip, ttl)
        if response:
            from logger_config import logger
            sock.sendto(response, (MDNS_ADDR, MDNS_PORT))
            logger.info(f"[mDNS Server] Replied to {addr} for {queried_hostname} -> {ip}")

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
    try:
        # トランザクションIDをコピー
        tx_id = query_data[0:2]
        
        # Flags: 0x8400 (Authoritative Response)
        flags = b'\x84\x00'
        
        # QDCOUNT=0, ANCOUNT=1, NSCOUNT=0, ARCOUNT=0
        counts = b'\x00\x00\x00\x01\x00\x00\x00\x00'
        
        header = tx_id + flags + counts
        
        # 応答名の構築
        name_parts = hostname.split('.')
        qname = b''
        for part in name_parts:
            qname += bytes([len(part)]) + part.encode('utf-8')
        qname += b'\x00'
        
        # Type A (1), Class IN (1) + Cache Flush (0x8000)
        type_class = b'\x00\x01\x80\x01'
        
        # TTL
        ttl_bytes = ttl.to_bytes(4, 'big')
        
        # RDLENGTH (4 bytes for IPv4)
        rdlength = b'\x00\x04'
        
        # RDATA (IP Address)
        ip_parts = ip.split('.')
        rdata = bytes([int(p) for p in ip_parts])
        
        response = header + qname + type_class + ttl_bytes + rdlength + rdata
        return response
    except Exception as e:
        return None
