import socket
import os
import subprocess

def resolve_all(db, sys_config):
    """static_hosts に登録されているホストを全て名前解決し、self_records に登録する"""
    with db.get_connection() as conn:
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
                # 127.0.0.1 の除外
                if ip == '127.0.0.1':
                    continue
                ttl = int(sys_config.get('system', 'ttl', fallback='120'))
                # 既存レコードがあれば更新、なければ追加
                cursor.execute('SELECT record_id FROM self_records WHERE hostname = ?', (hostname,))
                if cursor.fetchone():
                    cursor.execute('''
                        UPDATE self_records 
                        SET ip_address = ?, resolution_method = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE hostname = ?
                    ''', (ip, method, hostname))
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
            
            start_time = time.time()
            while time.time() - start_time < 2.0:
                try:
                    data, addr = sock.recvfrom(1024)
                    # Parse response (very simplified, looks for the IP in RDATA of an A record response)
                    # Skip header
                    if len(data) > 12:
                        flags = struct.unpack('!H', data[2:4])[0]
                        if (flags & 0x8000) != 0: # Is response
                            # For simplicity, we just search for the IP address signature in the packet.
                            # A real parser would be better, but this is a quick fallback.
                            # A record RDLENGTH is 4, followed by 4 bytes IP.
                            # We can look for \x00\x04 followed by 4 bytes.
                            idx = 12
                            # Skip question
                            while data[idx] != 0:
                                idx += data[idx] + 1
                            idx += 5 # skip null byte and QTYPE/QCLASS
                            
                            # Read answer
                            if len(data) > idx + 10:
                                # Name pointer or name
                                if (data[idx] & 0xC0) == 0xC0:
                                    idx += 2
                                else:
                                    while data[idx] != 0:
                                        idx += data[idx] + 1
                                    idx += 1
                                
                                atype, aclass, ttl, rdlength = struct.unpack('!HHIH', data[idx:idx+10])
                                idx += 10
                                if atype == 1 and rdlength == 4: # A record
                                    ip_bytes = data[idx:idx+4]
                                    ip_str = '.'.join(str(b) for b in ip_bytes)
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
