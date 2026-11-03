from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core_engine import State, Session, ScienceClient, _download_games, _parse_games, select_games

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class RunRequest(BaseModel):
    token: str
    cookie: str
    hours: float = 10.0
    count: int = 50

@app.post("/api/run")
def run_bot(req: RunRequest):
    try:
        state = State()
        state.token = req.token
        state.cookie = req.cookie
        session = Session.new()
        client = ScienceClient(state, session)
        
        # Tải danh sách game từ CDN của Discord
        try:
            raw = _download_games()
            games = _parse_games(raw)
        except Exception:
            return {"success": False, "message": "Không thể tải danh sách game"}

        selected = select_games(games, state, req.count)
        events = []
        for game in selected:
            events.extend(client.build_session(game, int(req.hours * 3600 * 1000)))
            
        status = client.post(events)
        
        if status == 204:
            return {"success": True, "message": f"Đã gửi thành công {len(selected)} games."}
        else:
            return {"success": False, "message": f"Lỗi Discord API: HTTP {status}. Token/Cookie có thể đã hết hạn."}
    except Exception as e:
        return {"success": False, "message": str(e)}

# Trả về giao diện web HTML nếu vào link gốc
@app.get("/", response_class=HTMLResponse)
def read_index():
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    index_path = os.path.join(root_dir, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Lỗi: Không tìm thấy file index.html ở thư mục gốc!</h1>"