import socket
import os
import subprocess
import ipaddress

def is_loopback(ip_str: str) -> bool:
    try:
        return ipaddress.ip_address(ip_str).is_loopback
    except ValueError:
        return False

def resolve_all(db, sys_config):
    """static_hosts に登録されているホストを全て名前解決し、self_records に登録する"""
    with db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT host_id, hostname, ip_address FROM static_hosts')
        hosts = cursor.fetchall()

        for host_id, hostname, static_ip in hosts:
            if static_ip:
                ip = static_ip
                method = 'static'
            else:
                ip, method = _resolve_host(hostname)
                
            if ip:
                # ループバックアドレスの除外
                if is_loopback(ip):
                    continue
                ttl = int(sys_config.get('system', 'ttl', fallback='120'))
                # 既存レコードがあれば更新、なければ追加
                cursor.execute('SELECT record_id FROM self_records WHERE hostname = ?', (hostname,))
                if cursor.fetchone():
                    cursor.execute('''
                        UPDATE self_records 
                        SET ip_address = ?, ttl = ?, resolution_method = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE hostname = ?
                    ''', (ip, ttl, method, hostname))
                else:
                    cursor.execute('''
                        INSERT INTO self_records (hostname, ip_address, record_type, ttl, resolution_method)
                        VALUES (?, ?, 'A', ?, ?)
                    ''', (hostname, ip, ttl, method))
        
        conn.commit()

def _resolve_host(hostname):
    """
    複数手法で名前解決を試みる。
    OSキャッシュや自分自身のmDNS Proxyが返した古いレコードを誤って再解決（自己参照ループ）するのを防ぐため、
    システムのDNSリゾルバー（socket.gethostbyname）は使用せず、生mDNSクエリおよびpingのみで実在を確認する。
    """
    ip = None
    method = None

    # Pure Python による簡易mDNSクエリフォールバック (socketのみ使用)
    try:
        target = f"{hostname}.local" if not hostname.endswith('.local') else hostname
        import struct
        import select
        import time
        try:
            import urandom
        except ImportError:
            import random as urandom
        
        def _send_mdns_query(qname):
            MDNS_ADDR = '224.0.0.251'
            MDNS_PORT = 5353
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(2.0)
            
            # Build mDNS query packet
            tx_id = urandom.randint(0, 65535)
            header = struct.pack('!HHHHHH', tx_id, 0, 1, 0, 0, 0)
            
            qname_bytes = b''
            for part in qname.split('.'):
                qname_bytes += bytes([len(part)]) + part.encode('utf-8')
            qname_bytes += b'\x00'
            
            qtype_qclass = struct.pack('!HH', 1, 1) # A record, IN class
            packet = header + qname_bytes + qtype_qclass
            
            sock.sendto(packet, (MDNS_ADDR, MDNS_PORT))
            
            def parse_name(data, offset):
                parts = []
                init_offset = offset
                hopped = False
                visited = set()
                while True:
                    if offset >= len(data):
                        break
                    # Prevent infinite loops in corrupted packets
                    if offset in visited:
                        break
                    visited.add(offset)
                    
                    length = data[offset]
                    if (length & 0xC0) == 0xC0:
                        # Compression pointer
                        if offset + 1 >= len(data):
                            break
                        pointer = struct.unpack('!H', data[offset:offset+2])[0] & 0x3FFF
                        if not hopped:
                            init_offset = offset + 2
                            hopped = True
                        offset = pointer
                    elif length == 0:
                        offset += 1
                        break
                    else:
                        offset += 1
                        if offset + length > len(data):
                            break
                        parts.append(data[offset:offset+length].decode('utf-8', errors='ignore'))
                        offset += length
                
                name = '.'.join(parts)
                if not hopped:
                    init_offset = offset
                return name, init_offset

            start_time = time.time()
            while time.time() - start_time < 2.0:
                try:
                    data, addr = sock.recvfrom(1024)
                    if len(data) < 12:
                        continue
                    
                    tx_id_resp, flags, qdcount, ancount, nscount, arcount = struct.unpack('!HHHHHH', data[:12])
                    # Ensure it is a response
                    if (flags & 0x8000) == 0:
                        continue
                    
                    idx = 12
                    # Skip or parse questions
                    for _ in range(qdcount):
                        if idx >= len(data):
                            break
                        qname_parsed, idx = parse_name(data, idx)
                        idx += 4 # QTYPE (2) + QCLASS (2)
                    
                    # Parse answers
                    for _ in range(ancount):
                        if idx >= len(data):
                            break
                        aname, idx = parse_name(data, idx)
                        if idx + 10 > len(data):
                            break
                        atype, aclass, ttl, rdlength = struct.unpack('!HHIH', data[idx:idx+10])
                        idx += 10
                        if idx + rdlength > len(data):
                            break
                        rdata = data[idx:idx+rdlength]
                        idx += rdlength
                        
                        # Check if this is an A record matching the queried qname
                        if atype == 1 and rdlength == 4:
                            if aname.lower() == qname.lower():
                                ip_str = '.'.join(str(b) for b in rdata)
                                return ip_str
                except Exception:
                    pass
            return None

        ip = _send_mdns_query(target)
        if ip:
            method = 'pure_mdns'
            return ip, method
    except Exception as e:
        pass

    # pingによるフォールバック
    try:
        import re
        target = f"{hostname}.local" if not hostname.endswith('.local') else hostname
        if os.name == 'nt':
            cmd = ['ping', '-n', '1', '-4', target]
        else:
            cmd = ['ping', '-c', '1', target]
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=2).decode('utf-8', errors='ignore')
        
        # pingの出力からIPアドレスを抽出 (例: 192.168.1.3)
        if os.name == 'nt':
            match = re.search(r'\[([0-9\.]+)\]', output)
            if match:
                ip = match.group(1)
                method = 'ping'
        else:
            match = re.search(r'\((([0-9]{1,3}\.){3}[0-9]{1,3})\)', output)
            if match:
                ip = match.group(1)
                method = 'ping'
    except Exception:
        pass

    return ip, method
