import os
import sys

project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)
sys.path.insert(0, os.path.join(project_root, 'apps', 'api'))

from a2wsgi import ASGIMiddleware
from apps.api.app.main import app as asgi_app

application = ASGIMiddleware(asgi_app)
