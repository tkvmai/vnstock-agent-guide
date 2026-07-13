"""Pytest configuration: put the breakout_app package root on sys.path so tests
can import `engine`, `data`, `config` directly."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
