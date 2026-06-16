import usocket as socket

class Request:
    def __init__(self, url, data=None, headers=None, method='GET'):
        self.url = url
        self.data = data
        self.headers = headers or {}
        self.method = method or ('POST' if data else 'GET')

    def add_header(self, key, val):
        self.headers[key] = val

class HTTPResponse:
    def __init__(self, socket_file=None):
        pass
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass
    def read(self):
        return b""

def urlopen(request, timeout=5):
    url = request.url
    if not url.startswith("http://"):
        raise ValueError("Only http is supported")
    
    url_body = url[7:]
    if "/" in url_body:
        host_port, path = url_body.split("/", 1)
        path = "/" + path
    else:
        host_port = url_body
        path = "/"
        
    if ":" in host_port:
        host, port_str = host_port.split(":", 1)
        port = int(port_str)
    else:
        host = host_port
        port = 80
        
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    
    try:
        addr = socket.getaddrinfo(host, port)[0][-1]
        s.connect(addr)
        
        req_lines = [f"{request.method} {path} HTTP/1.1", f"Host: {host_port}"]
        for k, v in request.headers.items():
            req_lines.append(f"{k}: {v}")
            
        if request.data:
            req_lines.append(f"Content-Length: {len(request.data)}")
            
        req_lines.append("")
        req_lines.append("")
        
        req_bytes = "\r\n".join(req_lines).encode('utf-8')
        if request.data:
            req_bytes += request.data
            
        s.sendall(req_bytes)
        
        res_data = s.recv(1024)
        if not res_data:
            raise Exception("No response from server")
            
        first_line = res_data.decode('utf-8', 'ignore').split("\r\n")[0]
        parts = first_line.split()
        if len(parts) >= 2:
            status_code = int(parts[1])
            if status_code < 200 or status_code >= 300:
                raise Exception(f"HTTP error: {status_code}")
                
        return HTTPResponse()
    finally:
        try:
            s.close()
        except:
            pass