import os, sys
os.chdir('/Users/dhruvsharma/Downloads/Projects/qiita-web/ezredbiom/frontend')
from http.server import HTTPServer, SimpleHTTPRequestHandler
port = int(os.environ.get('PORT', 5002))
HTTPServer(('', port), SimpleHTTPRequestHandler).serve_forever()
