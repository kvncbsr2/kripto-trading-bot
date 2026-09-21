import os
import sys

# Ensure strong production secrets for GoDaddy deployment
os.environ.setdefault('API_AUTH_SECRET', 'kripto_agent_super_secure_vault_key_2026_998877_godaddy')
os.environ.setdefault('API_ADMIN_KEY', 'kripto_agent_super_admin_key_2026_112233_godaddy')
os.environ.setdefault('ENVIRONMENT', 'production')
os.environ.setdefault('DEBUG', 'false')
os.environ.setdefault('ACTIVE_EXCHANGE', 'binance')
os.environ.setdefault('TRADING_MODE', 'paper')

project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)
sys.path.insert(0, os.path.join(project_root, 'apps', 'api'))

db_path = os.path.join(project_root, 'kripto_agent.db')
os.environ.setdefault('SQLITE_DB_PATH', db_path)
os.environ.setdefault('DATABASE_URL', f'sqlite+aiosqlite:///{db_path}')

from a2wsgi import ASGIMiddleware
from apps.api.app.main import app as asgi_app

application = ASGIMiddleware(asgi_app)
