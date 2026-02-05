class HttpServer:
    
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
    def __init__(self,host,port,keep_alive_timeout=15,static_dir=None):
        self.host= host
        self.port= port
        self.server_socket= None
        self.running= False
    
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