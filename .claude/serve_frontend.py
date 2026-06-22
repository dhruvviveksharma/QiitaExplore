import os, sys
os.chdir('/Users/dhruvsharma/Downloads/Projects/qiita-web/ezredbiom/frontend')
from http.server import HTTPServer, SimpleHTTPRequestHandler
HTTPServer(('', 5002), SimpleHTTPRequestHandler).serve_forever()
