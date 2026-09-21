import subprocess
import os
import sys

project_root = os.path.dirname(os.path.abspath(__file__))
repo_url = 'https://github.com/kvncbsr2/kripto-trading-bot.git'

try:
    if not os.path.exists(os.path.join(project_root, '.git')):
        subprocess.run(['git', 'init'], cwd=project_root, check=True)
        subprocess.run(['git', 'remote', 'add', 'origin', repo_url], cwd=project_root, check=True)
        subprocess.run(['git', 'fetch', 'origin', 'main'], cwd=project_root, check=True)
        subprocess.run(['git', 'reset', '--hard', 'origin/main'], cwd=project_root, check=True)
        print('SUCCESS: Git repository initialized and synced to origin/main')
    else:
        subprocess.run(['git', 'fetch', 'origin', 'main'], cwd=project_root, check=True)
        subprocess.run(['git', 'reset', '--hard', 'origin/main'], cwd=project_root, check=True)
        print('SUCCESS: Git repository updated to latest origin/main')
except Exception as e:
    print('ERROR:', str(e))
