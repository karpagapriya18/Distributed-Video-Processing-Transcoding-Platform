"""Run from the project root: python scripts/export_openapi.py."""
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from services.gateway import app

destination = root / 'docs' / 'openapi.json'
destination.write_text(json.dumps(app.openapi(), indent=2), encoding='utf-8')
print(destination)
