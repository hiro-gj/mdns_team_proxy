import usocket as socket
import _thread
import io
import re

class BaseHTTPRequestHandler:
    def __init__(self, client_sock, client_address, server):
        self.connection = client_sock
        self.client_address = client_address
        self.server = server
        self.headers = {}
        self.rfile = None
        self.wfile = io.BytesIO()
        self.path = ""
        self.command = ""
        self.handle()

    def handle(self):
        try:
            req_data = b""
            while b"\r\n\r\n" not in req_data:
                chunk = self.connection.recv(1024)
                if not chunk:
                    break
                req_data += chunk
                if len(req_data) > 8192:
                    break
            
            if not req_data:
                return

            header_part, body_part = req_data.split(b"\r\n\r\n", 1)
            lines = header_part.decode('utf-8', 'ignore').split("\r\n")
            if not lines or not lines[0]:
                return
            
            req_line = lines[0].split()
            if len(req_line) < 2:
                return
            self.command = req_line[0]
            self.path = req_line[1]

            self.headers = {}
            for line in lines[1:]:
                if ":" in line:
                    k, v = line.split(":", 1)
                    self.headers[k.strip().lower()] = v.strip()
                    self.headers[k.strip()] = v.strip()

            content_length = int(self.headers.get("content-length", 0))
            body_read = len(body_part)
            if body_read < content_length:
                try:
                    remaining = content_length - body_read
                    while remaining > 0:
                        chunk = self.connection.recv(min(remaining, 1024))
                        if not chunk:
                            break
                        body_part += chunk
                        remaining -= len(chunk)
                except Exception as ex:
                    print("Error reading body remaining:", ex)

            self.rfile = io.BytesIO(body_part[:content_length])
            self.wfile = io.BytesIO()

            if self.command == "GET":
                self.do_GET()
            elif self.command == "POST":
                self.do_POST()
                
            response_bytes = self.wfile.getvalue()
            self.connection.sendall(response_bytes)
        except Exception as e:
            print("HTTP Handler error:", e)
        finally:
            try:
                self.connection.close()
            except:
                pass

    def send_response(self, code, message=None):
        self.wfile.write(f"HTTP/1.1 {code} OK\r\n".encode())

    def send_header(self, keyword, value):
        self.wfile.write(f"{keyword}: {value}\r\n".encode())

    def end_headers(self):
        self.wfile.write(b"\r\n")

    def send_error(self, code, message=None):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(f"Error {code}: {message or 'Unknown'}\r\n".encode())

class HTTPServer:
    def __init__(self, server_address, RequestHandlerClass):
        self.server_address = server_address
        self.RequestHandlerClass = RequestHandlerClass
        self.db = None
        self.sys_config = None

    def serve_forever(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(self.server_address)
            s.listen(5)
            print(f"HTTP Server serving on {self.server_address[0]}:{self.server_address[1]}")
        except Exception as e:
            print("Failed to bind HTTP server port:", e)
            return

        while True:
            try:
                client_sock, client_addr = s.accept()
                _thread.start_new_thread(self._handle_client, (client_sock, client_addr))
            except Exception as e:
                print("HTTP Server accept loop error:", e)

    def _handle_client(self, client_sock, client_addr):
        self.RequestHandlerClass(client_sock, client_addr, self)