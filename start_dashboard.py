import sys
sys.path.insert(0, "/home/rohit/agentharness")
from core.observe.dashboard import create_app
import uvicorn
app = create_app(data_dir="/home/rohit/agentharness/data")
uvicorn.run(app, host="0.0.0.0", port=9100)
