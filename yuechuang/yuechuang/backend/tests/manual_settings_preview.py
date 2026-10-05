"""Isolated stage 7 browser fixture: no real credentials and no generation transport."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
STATE = Path(os.environ.get("YC_PHASE7_QA_DIR", "/tmp/yuechuang-stage7-preview"))
STATE.mkdir(parents=True, exist_ok=True)
os.environ.update(YC_STATE_DIR=str(STATE), YC_SETTINGS_DIR=str(STATE / "settings"),
                  YC_LEGACY_SESSION_DIR=str(STATE / "legacy"), YC_CONFIG_PATH=str(STATE / "config.yaml"),
                  YC_ENV_PATH=str(STATE / ".env"), YC_PHASE6_QA_DIR=str(STATE))
for provider in ("OPENAI", "GEMINI", "DEEPSEEK", "DASHSCOPE", "ARK", "KLING", "AGNES", "TUDOU"):
    for suffix in ("API_KEY", "ACCESS_KEY", "SECRET_KEY"):
        os.environ[f"{provider}_{suffix}"] = ""
if not (STATE / "config.yaml").exists():
    (STATE / "config.yaml").write_text("api_providers: {}\nmodels:\n  llm: deepseek-flash\n  vlm: ''\n  image_t2i: gemini-3-pro-image-preview\n  image_it2i: gemini-3-pro-image-preview\n  video_first_frame: grok-imagine-video-1.5\n  video_start_end: ''\n  video_reference: ''\n  video_text: grok-imagine-video-1.5\ngeneration:\n  video_generation_mode: text\n")

from tests.manual_project_preview import app

if __name__ == "__main__":
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    class ModelList(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"data": [{"id": "deepseek-flash"}]}).encode())
        def do_POST(self):
            self.send_error(405, "Offline fixture: generation forbidden")
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 8031), ModelList)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8030)
