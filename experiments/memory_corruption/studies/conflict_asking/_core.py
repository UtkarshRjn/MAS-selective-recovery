"""Put experiments/memory_corruption/core on the import path. Every study module imports this first."""
import os
import sys

CORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'core')
CORE = os.path.normpath(CORE)
if CORE not in sys.path:
    sys.path.insert(0, CORE)
