import http.server
import os

PORT = int(os.environ.get('PORT', 8080))

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=os.path.dirname(os.path.abspath(__file__)), **kwargs)

    def log_message(self, format, *args):
        pass  # suppress access logs on Railway

if __name__ == '__main__':
    with http.server.HTTPServer(('', PORT), Handler) as httpd:
        print(f'sourcetruth.io serving on port {PORT}', flush=True)
        httpd.serve_forever()
