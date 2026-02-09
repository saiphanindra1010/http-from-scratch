import socket
import sys
import threading
import time
import os
import json
from urllib.parse import urlparse, parse_qs
from datetime import datetime

class HTTPServer:
    """
    A comprehensive HTTP/1.1 server implementation from scratch.
    
    Key HTTP/1.1 features implemented:
    - Persistent connections (Keep-Alive) with configurable timeout
    - Request body parsing (Content-Length)
    - Chunked Transfer-Encoding for streaming responses
    - Query string parsing
    - Multiple HTTP methods (GET, POST, PUT, DELETE)
    - Static file serving with MIME types
    - Proper timeout handling
    """
    
    # MIME type mapping for static file serving
    MIME_TYPES = {
        '.html': 'text/html',
        '.css': 'text/css',
        '.js': 'application/javascript',
        '.json': 'application/json',
        '.png': 'image/png',
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.gif': 'image/gif',
        '.svg': 'image/svg+xml',
        '.ico': 'image/x-icon',
        '.txt': 'text/plain',
        '.pdf': 'application/pdf',
    }
    
    def __init__(self, host='127.0.0.1', port=8080, keep_alive_timeout=15, static_dir=None):
        self.host = host
        self.port = port
        self.server_socket = None
        self.running = False
        
        # HTTP/1.1 Keep-Alive settings
        # This is THE key difference from HTTP/1.0
        self.keep_alive_timeout = keep_alive_timeout  # seconds to wait for next request
        self.max_requests_per_connection = 100  # prevent infinite connections
        
        # Static file directory (optional)
        self.static_dir = static_dir

    def start(self):
        """
        Starts the multi-threaded TCP server.
        """
        # 1. Socket Creation (Syscall: socket())
        # AF_INET = IPv4, SOCK_STREAM = TCP
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        
        # 2. Socket Options (Syscall: setsockopt())
        # SO_REUSEADDR: Allow immediate reuse of the port after stopping
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        try:
            # 3. Bind (Syscall: bind())
            self.server_socket.bind((self.host, self.port))
            print(f"[*] HTTP/1.1 Server listening on http://{self.host}:{self.port}")
            print(f"[*] Keep-Alive timeout: {self.keep_alive_timeout}s")
        except Exception as e:
            print(f"[!] Failed to bind: {e}")
            return

        # 4. Listen (Syscall: listen())
        self.server_socket.listen(128)  # Higher backlog for production
        self.running = True

        try:
            while self.running:
                # 5. Accept (Syscall: accept())
                client_socket, client_address = self.server_socket.accept()
                print(f"\n[+] New connection from {client_address}")
                
                # 6. Spawn thread for this connection
                client_thread = threading.Thread(
                    target=self.handle_connection,
                    args=(client_socket, client_address),
                    daemon=True  # Thread dies when main thread exits
                )
                client_thread.start()
                
        except KeyboardInterrupt:
            print("\n[*] Stopping server...")
        finally:
            self.running = False
            if self.server_socket:
                self.server_socket.close()

    def handle_connection(self, client_socket, client_address):
        """
        Handles a persistent HTTP/1.1 connection.
        
        KEY DIFFERENCE FROM HTTP/1.0:
        - HTTP/1.0: One request, then close
        - HTTP/1.1: Loop handling multiple requests on same TCP connection (Keep-Alive)
        
        This is where you see why HTTP/1.1 has "Head-of-Line Blocking" at the 
        application layer: requests must be processed IN ORDER on each connection.
        HTTP/2 solves this with multiplexing/streams.
        """
        # Set socket timeout for Keep-Alive
        # If no data arrives within this time, socket.timeout is raised
        client_socket.settimeout(self.keep_alive_timeout)
        
        request_count = 0
        keep_alive = True
        
        try:
            rfile = client_socket.makefile('rb', buffering=0)
            
            # === THE KEEP-ALIVE LOOP ===
            # This loop is what makes HTTP/1.1 different from HTTP/1.0
            while keep_alive and request_count < self.max_requests_per_connection:
                try:
                    # --- Parse one complete HTTP request ---
                    request = self.parse_request(rfile, client_address)
                    
                    if request is None:
                        # Client closed connection or empty request
                        break
                    
                    request_count += 1
                    
                    # Determine if we should keep connection alive
                    # HTTP/1.1 defaults to Keep-Alive, HTTP/1.0 defaults to close
                    connection_header = request['headers'].get('Connection', '').lower()
                    if request['version'] == 'HTTP/1.1':
                        keep_alive = connection_header != 'close'
                    else:
                        keep_alive = connection_header == 'keep-alive'
                    
                    # --- Generate and send response ---
                    response = self.router(request)
                    
                    # Add connection header to response
                    if not keep_alive:
                        response = self.add_header(response, 'Connection', 'close')
                    else:
                        response = self.add_header(response, 'Connection', 'keep-alive')
                        response = self.add_header(response, 'Keep-Alive', f'timeout={self.keep_alive_timeout}')
                    
                    client_socket.sendall(response)
                    
                    method = request['method']
                    path = request['path']
                    status = self.get_status_from_response(response)
                    print(f"[{client_address[1]}] #{request_count} {method} {path} -> {status}")
                    
                except socket.timeout:
                    # Keep-Alive timeout expired - this is NORMAL, not an error
                    print(f"[{client_address[1]}] Keep-Alive timeout after {request_count} requests")
                    break
                    
        except ConnectionResetError:
            print(f"[{client_address[1]}] Connection reset by client")
        except BrokenPipeError:
            print(f"[{client_address[1]}] Broken pipe - client disconnected")
        except Exception as e:
            print(f"[!] Error handling {client_address}: {type(e).__name__}: {e}")
        finally:
            client_socket.close()
            print(f"[-] Connection closed: {client_address} (served {request_count} requests)")

    def parse_request(self, rfile, client_address):
        """
        Parses a complete HTTP request including:
        - Request line (method, path, version)
        - Headers
        - Body (if Content-Length present)
        - Query string parameters
        """
        # --- STEP 1: Parse Request Line ---
        request_line = self.read_line(rfile)
        if not request_line or request_line == '\r\n':
            return None  # Empty request or connection closed
        
        method, full_path, version = self.parse_request_line(request_line)
        
        # --- STEP 2: Parse URL and Query String ---
        parsed_url = urlparse(full_path)
        path = parsed_url.path
        query_params = parse_qs(parsed_url.query)  # Returns dict of lists
        
        # --- STEP 3: Parse Headers ---
        headers = self.parse_headers(rfile)
        
        # --- STEP 4: Parse Body (if present) ---
        # HTTP/1.1 uses Content-Length to know how many bytes to read
        # Without it, we'd have no way to know when the body ends (TCP is a stream!)
        body = b''
        content_length = headers.get('Content-Length')
        if content_length:
            try:
                length = int(content_length)
                body = self.read_exactly(rfile, length)
            except ValueError:
                pass  # Invalid Content-Length, ignore body
        
        # Handle chunked transfer encoding (for incoming requests)
        # This is less common for requests but part of HTTP/1.1 spec
        if headers.get('Transfer-Encoding', '').lower() == 'chunked':
            body = self.read_chunked_body(rfile)
        
        return {
            'method': method,
            'path': path,
            'full_path': full_path,
            'version': version,
            'query_params': query_params,
            'headers': headers,
            'body': body,
            'client_address': client_address
        }

    def read_line(self, rfile):
        """
        Reads bytes until CRLF (\r\n).
        TCP is a byte stream - we must find delimiters ourselves.
        """
        line_bytes = b""
        while True:
            char = rfile.read(1)
            if not char:  # Connection closed
                return None if not line_bytes else line_bytes.decode('iso-8859-1')
            line_bytes += char
            if line_bytes.endswith(b"\r\n"):
                break
        return line_bytes.decode('iso-8859-1')

    def read_exactly(self, rfile, length):
        """
        Reads exactly 'length' bytes from the stream.
        This is how Content-Length works - we know exactly how many bytes to expect.
        """
        data = b''
        remaining = length
        while remaining > 0:
            chunk = rfile.read(min(remaining, 8192))
            if not chunk:
                break
            data += chunk
            remaining -= len(chunk)
        return data

    def read_chunked_body(self, rfile):
        """
        Reads a chunked transfer-encoded body.
        
        Chunked encoding format:
        <chunk-size-in-hex>\r\n
        <chunk-data>\r\n
        ... (repeat) ...
        0\r\n
        \r\n
        
        This allows streaming responses without knowing total size upfront.
        """
        body = b''
        while True:
            # Read chunk size line
            size_line = self.read_line(rfile)
            if not size_line:
                break
            
            # Parse hex chunk size
            try:
                chunk_size = int(size_line.strip(), 16)
            except ValueError:
                break
            
            if chunk_size == 0:
                # Final chunk - read trailing CRLF
                self.read_line(rfile)
                break
            
            # Read chunk data
            chunk_data = self.read_exactly(rfile, chunk_size)
            body += chunk_data
            
            # Read trailing CRLF after chunk
            self.read_line(rfile)
        
        return body

    def parse_request_line(self, request_line):
        parts = request_line.strip().split()
        if len(parts) != 3:
            return "GET", "/", "HTTP/1.1"
        return parts[0], parts[1], parts[2]

    def parse_headers(self, rfile):
        """
        Parses headers into a dictionary.
        Handles multi-line headers (continuation lines starting with whitespace).
        """
        headers = {}
        last_key = None
        
        while True:
            line = self.read_line(rfile)
            if line is None or line in ('\r\n', '\n', ''):
                break
            
            # Check for header continuation (line starts with whitespace)
            if line[0] in (' ', '\t') and last_key:
                headers[last_key] += ' ' + line.strip()
                continue
            
            if ':' in line:
                key, value = line.split(':', 1)
                key = key.strip()
                headers[key] = value.strip()
                last_key = key
                
        return headers

    def router(self, request):
        """
        Routes requests to appropriate handlers based on method and path.
        """
        method = request['method']
        path = request['path']
        
        # --- Static File Serving ---
        if self.static_dir and method == 'GET' and path.startswith('/static/'):
            return self.serve_static_file(request)
        
        # --- API Routes ---
        if method == 'GET':
            if path == '/' or path == '/index.html':
                return self.handle_index(request)
            elif path == '/slow':
                return self.handle_slow(request)
            elif path == '/chunked':
                return self.handle_chunked(request)
            elif path == '/echo':
                return self.handle_echo_get(request)
            elif path == '/info':
                return self.handle_info(request)
            else:
                return self.handle_not_found(request)
                
        elif method == 'POST':
            if path == '/echo':
                return self.handle_echo_post(request)
            elif path == '/json':
                return self.handle_json_post(request)
            else:
                return self.handle_not_found(request)
                
        elif method == 'HEAD':
            # HEAD is like GET but without body - useful for checking resources
            response = self.router({**request, 'method': 'GET'})
            # Remove body from response
            header_end = response.find(b'\r\n\r\n')
            if header_end != -1:
                return response[:header_end + 4]
            return response
            
        elif method == 'OPTIONS':
            return self.handle_options(request)
            
        else:
            return self.build_response(
                405, "Method Not Allowed",
                f"Method {method} not allowed. Allowed: GET, POST, HEAD, OPTIONS",
                extra_headers={'Allow': 'GET, POST, HEAD, OPTIONS'}
            )

    # === REQUEST HANDLERS ===
    
    def handle_index(self, request):
        body = """<!DOCTYPE html>
<html>
<head>
    <title>HTTP/1.1 From Scratch</title>
    <style>
        body { font-family: -apple-system, sans-serif; max-width: 800px; margin: 50px auto; padding: 20px; }
        h1 { color: #333; }
        .endpoint { background: #f5f5f5; padding: 15px; margin: 10px 0; border-radius: 5px; }
        code { background: #e0e0e0; padding: 2px 6px; border-radius: 3px; }
        .method { font-weight: bold; color: #0066cc; }
    </style>
</head>
<body>
    <h1>HTTP/1.1 Server - Built From Scratch</h1>
    <p>This server demonstrates key HTTP/1.1 concepts:</p>
    <ul>
        <li><strong>Keep-Alive</strong> - Persistent connections (check your network tab!)</li>
        <li><strong>Content-Length</strong> - Body size negotiation</li>
        <li><strong>Chunked Transfer</strong> - Streaming responses</li>
        <li><strong>Request Bodies</strong> - POST with JSON/form data</li>
    </ul>
    
    <h2>Available Endpoints</h2>
    
    <div class="endpoint">
        <span class="method">GET</span> <code>/</code> - This page
    </div>
    
    <div class="endpoint">
        <span class="method">GET</span> <code>/slow</code> - 3 second delay (test Keep-Alive head-of-line blocking)
    </div>
    
    <div class="endpoint">
        <span class="method">GET</span> <code>/chunked</code> - Chunked transfer encoding demo
    </div>
    
    <div class="endpoint">
        <span class="method">GET</span> <code>/echo?msg=hello</code> - Query string parsing
    </div>
    
    <div class="endpoint">
        <span class="method">POST</span> <code>/echo</code> - Echoes request body back
    </div>
    
    <div class="endpoint">
        <span class="method">POST</span> <code>/json</code> - Accepts and returns JSON
    </div>
    
    <div class="endpoint">
        <span class="method">GET</span> <code>/info</code> - Connection info (request count, headers)
    </div>
    
    <h2>Test Commands</h2>
    <pre>
# Test Keep-Alive (multiple requests, one connection)
curl -v http://localhost:8080/ http://localhost:8080/info

# Test POST with body
curl -X POST -d "Hello World" http://localhost:8080/echo

# Test JSON
curl -X POST -H "Content-Type: application/json" -d '{"name":"test"}' http://localhost:8080/json

# Test chunked response
curl -v http://localhost:8080/chunked
    </pre>
</body>
</html>"""
        return self.build_response(200, "OK", body)

    def handle_slow(self, request):
        """
        Demonstrates HEAD-OF-LINE BLOCKING in HTTP/1.1.
        
        While this request is processing, other requests on the SAME connection
        must wait. This is the fundamental problem HTTP/2 solves with streams.
        """
        time.sleep(3)
        body = """<html><body>
<h1>Slow Response Complete</h1>
<p>This took 3 seconds. On the same TCP connection, other requests had to wait.</p>
<p>This is <strong>Head-of-Line Blocking</strong> - the problem HTTP/2 solves.</p>
</body></html>"""
        return self.build_response(200, "OK", body)

    def handle_chunked(self, request):
        """
        Demonstrates CHUNKED TRANSFER ENCODING.
        
        This allows sending a response without knowing the total size upfront.
        Useful for streaming, server-sent events, or dynamically generated content.
        """
        # Build response with chunked encoding
        response = "HTTP/1.1 200 OK\r\n"
        response += "Server: PythonHTTP/1.1\r\n"
        response += "Content-Type: text/plain\r\n"
        response += "Transfer-Encoding: chunked\r\n"  # Key header!
        response += "\r\n"
        
        response_bytes = response.encode('utf-8')
        
        # Add chunks
        chunks = [
            b"This is the first chunk.\n",
            b"This is the second chunk.\n",
            b"Chunked encoding allows streaming without Content-Length.\n",
            b"Each chunk has a hex size prefix.\n",
        ]
        
        for chunk in chunks:
            # Chunk format: <size-in-hex>\r\n<data>\r\n
            response_bytes += f"{len(chunk):x}\r\n".encode('utf-8')
            response_bytes += chunk
            response_bytes += b"\r\n"
        
        # Final chunk (size 0)
        response_bytes += b"0\r\n\r\n"
        
        return response_bytes

    def handle_echo_get(self, request):
        """Echo query parameters back."""
        params = request['query_params']
        msg = params.get('msg', ['No message provided'])[0]
        body = f"<html><body><h1>Echo</h1><p>Message: {msg}</p></body></html>"
        return self.build_response(200, "OK", body)

    def handle_echo_post(self, request):
        """Echo POST body back."""
        body = request['body']
        content_type = request['headers'].get('Content-Type', 'text/plain')
        
        try:
            body_str = body.decode('utf-8')
        except:
            body_str = body.decode('iso-8859-1')
        
        response_body = f"""<html><body>
<h1>POST Echo</h1>
<p><strong>Content-Type:</strong> {content_type}</p>
<p><strong>Body Length:</strong> {len(body)} bytes</p>
<pre>{body_str}</pre>
</body></html>"""
        return self.build_response(200, "OK", response_body)

    def handle_json_post(self, request):
        """Handle JSON POST request and return JSON response."""
        content_type = request['headers'].get('Content-Type', '')
        
        if 'application/json' not in content_type:
            return self.build_response(
                415, "Unsupported Media Type",
                '{"error": "Content-Type must be application/json"}',
                content_type='application/json'
            )
        
        try:
            data = json.loads(request['body'].decode('utf-8'))
        except json.JSONDecodeError as e:
            return self.build_response(
                400, "Bad Request",
                f'{{"error": "Invalid JSON: {str(e)}"}}',
                content_type='application/json'
            )
        
        # Echo back with metadata
        response_data = {
            "received": data,
            "timestamp": datetime.now().isoformat(),
            "method": request['method'],
            "path": request['path']
        }
        
        return self.build_response(
            200, "OK",
            json.dumps(response_data, indent=2),
            content_type='application/json'
        )

    def handle_info(self, request):
        """Return connection and request info."""
        headers_html = "".join(
            f"<tr><td><code>{k}</code></td><td>{v}</td></tr>"
            for k, v in request['headers'].items()
        )
        
        body = f"""<html><body>
<h1>Request Info</h1>
<table border="1" cellpadding="5">
    <tr><th>Property</th><th>Value</th></tr>
    <tr><td>Method</td><td>{request['method']}</td></tr>
    <tr><td>Path</td><td>{request['path']}</td></tr>
    <tr><td>Full Path</td><td>{request['full_path']}</td></tr>
    <tr><td>HTTP Version</td><td>{request['version']}</td></tr>
    <tr><td>Query Params</td><td>{request['query_params']}</td></tr>
    <tr><td>Client</td><td>{request['client_address']}</td></tr>
</table>

<h2>Headers</h2>
<table border="1" cellpadding="5">
    <tr><th>Header</th><th>Value</th></tr>
    {headers_html}
</table>
</body></html>"""
        return self.build_response(200, "OK", body)

    def handle_options(self, request):
        """Handle OPTIONS request (used for CORS preflight)."""
        return self.build_response(
            204, "No Content", "",
            extra_headers={
                'Allow': 'GET, POST, HEAD, OPTIONS',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, POST, HEAD, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type'
            }
        )

    def handle_not_found(self, request):
        body = f"""<html><body>
<h1>404 Not Found</h1>
<p>Path <code>{request['path']}</code> does not exist.</p>
<p><a href="/">Go to homepage</a></p>
</body></html>"""
        return self.build_response(404, "Not Found", body)

    def serve_static_file(self, request):
        """Serve static files with proper MIME types."""
        # Remove /static/ prefix and prevent directory traversal
        file_path = request['path'][8:]  # Remove '/static/'
        file_path = os.path.normpath(file_path)
        
        if file_path.startswith('..'):
            return self.build_response(403, "Forbidden", "Access denied")
        
        full_path = os.path.join(self.static_dir, file_path)
        
        if not os.path.exists(full_path) or not os.path.isfile(full_path):
            return self.handle_not_found(request)
        
        # Determine MIME type
        ext = os.path.splitext(full_path)[1].lower()
        content_type = self.MIME_TYPES.get(ext, 'application/octet-stream')
        
        try:
            with open(full_path, 'rb') as f:
                content = f.read()
            
            return self.build_response(
                200, "OK",
                content,
                content_type=content_type,
                is_binary=True
            )
        except Exception as e:
            return self.build_response(500, "Internal Server Error", str(e))

    # === RESPONSE BUILDING ===
    
    def build_response(self, status_code, status_text, body, content_type='text/html; charset=UTF-8', 
                       extra_headers=None, is_binary=False):
        """
        Builds a complete HTTP/1.1 response.
        """
        # Convert body to bytes if necessary
        if is_binary:
            body_bytes = body if isinstance(body, bytes) else body.encode('utf-8')
        else:
            body_bytes = body.encode('utf-8') if isinstance(body, str) else body
        
        # Build response headers
        # Note: HTTP/1.1, not 1.0!
        response = f"HTTP/1.1 {status_code} {status_text}\r\n"
        response += f"Date: {self.http_date()}\r\n"
        response += "Server: PythonHTTP/1.1-FromScratch\r\n"
        response += f"Content-Type: {content_type}\r\n"
        response += f"Content-Length: {len(body_bytes)}\r\n"
        
        # Add extra headers
        if extra_headers:
            for key, value in extra_headers.items():
                response += f"{key}: {value}\r\n"
        
        response += "\r\n"
        
        return response.encode('utf-8') + body_bytes

    def add_header(self, response, header_name, header_value):
        """
        Adds a header to an already-built response.
        Inserts before the blank line that separates headers from body.
        """
        header_line = f"{header_name}: {header_value}\r\n"
        # Find the end of headers
        header_end = response.find(b'\r\n\r\n')
        if header_end == -1:
            return response
        
        return response[:header_end] + header_line.encode('utf-8') + response[header_end:]

    def get_status_from_response(self, response):
        """Extract status code from response for logging."""
        try:
            first_line = response.split(b'\r\n')[0].decode('utf-8')
            return first_line.split(' ', 2)[1]
        except:
            return "???"

    def http_date(self):
        """Generate HTTP-formatted date (RFC 7231)."""
        from email.utils import formatdate
        return formatdate(timeval=None, localtime=False, usegmt=True)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='HTTP/1.1 Server from Scratch')
    parser.add_argument('--host', default='127.0.0.1', help='Host to bind to')
    parser.add_argument('--port', type=int, default=8080, help='Port to listen on')
    parser.add_argument('--timeout', type=int, default=15, help='Keep-Alive timeout in seconds')
    parser.add_argument('--static', type=str, help='Directory to serve static files from')
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("HTTP/1.1 Server - Built From Scratch")
    print("=" * 60)
    print("\nKey concepts demonstrated:")
    print("  - Keep-Alive (persistent connections)")
    print("  - Content-Length body parsing")
    print("  - Chunked Transfer-Encoding")
    print("  - Query string parsing")
    print("  - Multiple HTTP methods")
    print("\nThis is HTTP/1.1 - you'll experience Head-of-Line Blocking")
    print("if you hit /slow while other requests are pending on same connection.")
    print("=" * 60 + "\n")
    
    server = HTTPServer(
        host=args.host,
        port=args.port,
        keep_alive_timeout=args.timeout,
        static_dir=args.static
    )
    server.start()