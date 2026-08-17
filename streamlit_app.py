"""Public Streamlit Community Cloud entrypoint."""
import os

# Public deployment hides write-oriented/manual data controls and uses a shared,
# TTL-protected source cache. Local users can still run app.py directly.
os.environ.setdefault("MOEX_PUBLIC_DEPLOYMENT", "1")

import app  # noqa: F401,E402
