import os
import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RADICE))

CHIAVE = "chiave-di-prova-lunga-abcdefgh"
os.environ.setdefault("ACCESS_KEY", CHIAVE)
