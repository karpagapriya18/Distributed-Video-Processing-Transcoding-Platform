import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.models import OWNERS

service = os.environ.get('SERVICE', 'gateway')
if service not in OWNERS:
    raise SystemExit(f'Unknown service {service}')
if OWNERS[service]:
    # For replicated deployments, run this once as a release job before replicas.
    subprocess.run([sys.executable, '-m', 'alembic', '-x', f'service={service}', 'upgrade', 'head'], check=True)
os.execvp('uvicorn', ['uvicorn', f'services.{service}:app', '--host', '0.0.0.0', '--port', os.getenv('PORT', '8000')])
