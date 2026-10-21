from pathlib import Path

from fastapi.templating import Jinja2Templates

# Anchor package-relative resources (templates) to this file's location so
# the app works regardless of the working directory.
BASE_DIR = Path(__file__).resolve().parent

templates = Jinja2Templates(directory=BASE_DIR / "templates")
