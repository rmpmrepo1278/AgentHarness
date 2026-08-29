import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uvicorn

from core.observe.dashboard import create_app

app = create_app(data_dir=os.environ.get("AH_DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")))
uvicorn.run(app, host="0.0.0.0", port=9100)
