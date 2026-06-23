import sys
import os
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

# Unit tests must stay deterministic even when the developer has real API keys
# in .env. Integration smoke tests can opt into remote providers separately.
os.environ["TRIP_PROVIDER_MODE"] = "local"
os.environ["TRIP_LLM_ENABLED"] = "false"
