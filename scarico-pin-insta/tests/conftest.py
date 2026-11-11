import os
import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RADICE.parent))  # per "import scarico_pin_insta.server" dall'esterno
sys.path.insert(0, str(RADICE))

os.environ.setdefault("APP_PASSWORD", "password-di-prova-lunga")
os.environ.setdefault("SECRET_KEY", "chiave-di-prova-stabile-per-i-test-0123456789")
os.environ.setdefault("SKIP_DNS_GUARD", "")
