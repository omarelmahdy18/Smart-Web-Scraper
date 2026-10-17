"""
PyInstaller Wrapper Entrypoint for the Streamlit Smart Web Scraper (`app.py`).

Why this wrapper is required:
- Streamlit apps must be invoked through the Streamlit CLI runtime (`streamlit.web.cli`)
  rather than executed as plain Python scripts.
- When frozen by PyInstaller (`sys.frozen == True`), `global.developmentMode` must be
  explicitly set to `false` so Streamlit starts its internal Tornado/Uvicorn web server
  on port 8501 and automatically opens the user's default web browser.
"""

import os
import sys

# Explicit top-level imports ensure PyInstaller bundles all libraries used inside `app.py`
import bs4  # noqa: F401
import lxml  # noqa: F401
import pandas  # noqa: F401
import requests  # noqa: F401
import streamlit  # noqa: F401
import streamlit.runtime.scriptrunner.magic_funcs  # noqa: F401
import streamlit.web.cli as stcli


def resolve_app_path(filename: str = "app.py") -> str:
    """
    Resolves the path to `app.py`:
    1. Checks next to the `.exe` (allows hot-updating `app.py` in the `dist/` folder).
    2. Falls back to PyInstaller's temporary bundle directory (`sys._MEIPASS`).
    3. Falls back to the script's current directory during normal Python execution.
    """
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        external_candidate = os.path.join(exe_dir, filename)
        if os.path.exists(external_candidate):
            return external_candidate

        meipass_dir = getattr(sys, "_MEIPASS", exe_dir)
        return os.path.join(meipass_dir, filename)

    return os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)


def main() -> None:
    app_path = resolve_app_path("app.py")
    if not os.path.exists(app_path):
        print(f"[ERROR] Could not locate 'app.py' at: {app_path}")
        input("Press Enter to exit...")
        sys.exit(1)

    # Configure environment & CLI arguments for frozen Streamlit execution
    os.environ["STREAMLIT_GLOBAL_DEVELOPMENT_MODE"] = "false"
    os.environ["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"

    sys.argv = [
        "streamlit",
        "run",
        app_path,
        "--global.developmentMode=false",
        "--server.headless=false",
        "--browser.gatherUsageStats=false",
    ]
    sys.exit(stcli.main())


if __name__ == "__main__":
    main()
