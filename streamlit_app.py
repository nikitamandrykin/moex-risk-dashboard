"""Public Streamlit Community Cloud entrypoint."""
from pathlib import Path
import os
import runpy

os.environ.setdefault("MOEX_PUBLIC_DEPLOYMENT", "1")

app_path = Path(__file__).with_name("app.py")
runpy.run_path(str(app_path), run_name="__main__")
