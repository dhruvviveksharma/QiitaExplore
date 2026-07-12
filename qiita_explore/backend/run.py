# backend/run.py
import sys
import os
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger().setLevel(logging.INFO)

# Ensure the backend directory is always first in sys.path so that local
# modules (config, store, helpers/, routes/) are found even if the qiita
# environment manipulates sys.path before our imports run.
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from flask import Flask
from flask_cors import CORS
from concurrent.futures import ThreadPoolExecutor

import config
from helpers.auth_middleware import register_auth_middleware

app = Flask(__name__)
CORS(app, supports_credentials=True, origins=config.ALLOWED_ORIGINS)
register_auth_middleware(app)

_bg_executor = ThreadPoolExecutor(max_workers=4)

# When executed as __main__, sys.modules only contains this module under
# '__main__'. Route files do `from run import app`, which would trigger a
# second import of this file unless we also register it under 'run'.
sys.modules.setdefault('run', sys.modules[__name__])

# Import route modules to register their @app.route decorators.
# These must come AFTER app and _bg_executor are defined.
import routes.auth_routes         # noqa: F401, E402
import routes.study_routes        # noqa: F401, E402
import routes.project_routes      # noqa: F401, E402
import routes.chat_routes         # noqa: F401, E402
import routes.global_chat_routes  # noqa: F401, E402
import routes.merge_routes        # noqa: F401, E402
import routes.artifact_routes     # noqa: F401, E402 (split out of merge_routes)

if __name__ == '__main__':
    print("QIITA SEARCH API -- http://localhost:5001 (DEBUG MODE)")
    app.run(debug=False, host='0.0.0.0', port=5001, use_reloader=False)
