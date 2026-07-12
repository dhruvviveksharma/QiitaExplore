import os, sys
os.chdir('/Users/dhruvsharma/Downloads/Projects/qiita-web/qiita_explore/frontend')
from http.server import HTTPServer, SimpleHTTPRequestHandler
port = int(os.environ.get('PORT', 5002))
HTTPServer(('', port), SimpleHTTPRequestHandler).serve_forever()
