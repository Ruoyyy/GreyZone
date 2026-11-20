#!/usr/bin/env python3
"""Summarize archived evaluations for this application."""

from pathlib import Path
import sys

application = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(application.parent / 'scripts'))
from summarize_archived_experiments import main

if __name__ == '__main__':
    main(application)
