"""Run one native deepteam candidate against Commonly."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from redteam.runner import main

if __name__ == "__main__":
    raise SystemExit(main("deepteam"))
