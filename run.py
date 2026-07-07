"""One-command launcher for the browser UI:

    python run.py

Injects the project venv's site-packages (no activation needed), starts the
FastAPI server, and opens http://127.0.0.1:8000 in the default browser once
the Whisper model has loaded. On Windows you can also double-click start.bat.
"""

import glob
import os
import site
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
for _p in glob.glob(os.path.join(_ROOT, ".venv", "Lib", "site-packages")):
    site.addsitedir(_p)

HOST = "127.0.0.1"
PORT = 8000


def main():
    os.chdir(_ROOT)
    sys.path.insert(0, _ROOT)
    os.environ.setdefault("AI_TUTOR_OPEN_BROWSER", "1")
    os.environ.setdefault("AI_TUTOR_URL", f"http://{HOST}:{PORT}")

    import uvicorn
    uvicorn.run("web.server:app", host=HOST, port=PORT)


if __name__ == "__main__":
    main()
