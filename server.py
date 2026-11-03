from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
import urllib.parse
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

from core_engine import (
    ROOT, GAMES_FILE, State, Session, Game,
    ScienceClient, ensure_token, load_games, select_games,
    generate_fingerprint, _user_id_from_token, _chunked,
    BATCH_SIZE, BATCH_DELAY, load_user_stats, save_user_stats
)

STATIC_DIR = ROOT / "web" / "static"

class ClientWorker:
    def __init__(self, client_id: str, games: list[Game]):
        self.client_id = client_id
        self.games = games
        self.lock = threading.RLock()
        self.state = State()
        self.session: Session | None = None
        self.client: ScienceClient | None = None
        self.is_running = False
        self.should_stop = False
        self.bot_status = "Bot ready"
        now_ts = time.strftime("%H:%M:%S")
        self.log_history: list[dict] = [
            {"timestamp": now_ts, "level": "ok", "tag": "READY", "message": f"Hệ thống sẵn sàng. Đã nạp {len(self.games)} game."},
            {"timestamp": now_ts, "level": "info", "tag": "INFO", "message": "Nhập token và cookie discord để bắt đầu."}
        ]
        self.listeners: list[queue.Queue] = []
        self.progress = {
            "current": 0,
            "total": 0,
            "hours": 0.0,
            "claimed_hours": 0.0
        }
        self.last_active = time.time()

    def emit_log(self, level: str, message: str, tag: str = "SYSTEM"):
        timestamp = time.strftime("%H:%M:%S")
        entry = {
            "timestamp": timestamp,
            "level": level,
            "tag": tag,
            "message": message
        }
        with self.lock:
            self.last_active = time.time()
            self.log_history.append(entry)
            if len(self.log_history) > 500:
                self.log_history.pop(0)
            dead = []
            for q in self.listeners:
                try:
                    q.put_nowait({"type": "log", "data": entry})
                except Exception:
                    dead.append(q)
            for d in dead:
                if d in self.listeners:
                    self.listeners.remove(d)

    def emit_status(self):
        status_data = self.get_status_data()
        with self.lock:
            self.last_active = time.time()
            dead = []
            for q in self.listeners:
                try:
                    q.put_nowait({"type": "status", "data": status_data})
                except Exception:
                    dead.append(q)
            for d in dead:
                if d in self.listeners:
                    self.listeners.remove(d)

    def get_status_data(self) -> dict:
        uid = _user_id_from_token(self.state.token)
        return {
            "status_text": self.bot_status,
            "is_running": self.is_running,
            "games_loaded": len(self.games),
            "total_games_claimed": self.state.total_games_claimed,
            "total_hours_claimed": round(self.state.total_hours_claimed, 2),
            "has_token": self.state.has_token,
            "has_cookie": self.state.has_cookie,
            "has_fingerprint": bool(self.state.fingerprint),
            "user_id": uid,
            "token_masked": (self.state.token[:6] + "..." + self.state.token[-4:]) if len(self.state.token) > 10 else ("Set" if self.state.token else "None"),
            "cookie_masked": (self.state.cookie[:10] + "...") if self.state.cookie else "None",
            "progress": self.progress
        }

    def new_session(self):
        self.session = Session.new()
        self.client = ScienceClient(self.state, self.session)

    def refresh_auth(self) -> tuple[bool, str]:
        if not self.state.has_token:
            return False, "Chưa có Token Discord"
        self.emit_log("info", "Đang làm mới analytics_token từ Discord Science...", "AUTH")
        self.new_session()
        try:
            self.state.fetched_at = 0
            ensure_token(self.state, self.session.super_props)
            
            uid = _user_id_from_token(self.state.token)
            stats = load_user_stats(uid)
            self.state.total_games_claimed = stats["games"]
            self.state.total_hours_claimed = stats["hours"]
            
            self.emit_status()
            return True, "Analytics token refreshed"
        except Exception as e:
            self.emit_log("err", f"Làm mới Auth thất bại: {e}", "AUTH")
            return False, str(e)

    def stop_bot(self):
        with self.lock:
            if not self.is_running:
                return
            self.should_stop = True
            self.bot_status = "Đang dừng..."
        self.emit_log("warn", "Nhận lệnh dừng. Đang chờ hoàn tất đợt batch hiện tại...", "RUNNER")
        self.emit_status()

    def start_bot(self, game_count: int | None, hours: float, mode: str = "both") -> tuple[bool, str, str | None]:
        with self.lock:
            if self.is_running:
                return False, "Bot đang chạy một tiến trình khác!", None
            if not self.state.has_token:
                return False, "Vui lòng nhập Discord Token trước!", "token"
            if not self.state.has_cookie:
                return False, "Vui lòng nhập Cookie cf_clearance trước!", "cookie"
            if not self.games:
                return False, "Danh sách trò chơi trống hoặc chưa tải được!", None

            self.is_running = True
            self.should_stop = False
            self.bot_status = "Bot running"
            
            uid = _user_id_from_token(self.state.token)
            stats = load_user_stats(uid)
            self.state.total_games_claimed = stats["games"]
            self.state.total_hours_claimed = stats["hours"]

        threading.Thread(target=self._run_worker, args=(game_count, hours, mode), daemon=True).start()
        self.emit_status()
        return True, "Đã khởi chạy Bot thành công", None

    def _run_worker(self, game_count: int | None, hours: float, mode: str):
        try:
            mode_title = {
                "both": "CẢ HAI (TĂNG GIỜ + SỐ GAME)",
                "playtime": "CLAIM PLAYTIME (TĂNG GIỜ)",
                "games_played": "GAMES PLAYED (SỐ GAME)"
            }.get(mode, mode.upper())
            self.emit_log("info", f"Khởi động tác vụ Spoofing [Chế độ: {mode_title}]...", "RUNNER")
            self.new_session()
            try:
                ensure_token(self.state, self.session.super_props)
            except Exception as e:
                self.emit_log("err", f"Xác thực tài khoản thất bại: {e}", "AUTH")
                with self.lock:
                    self.bot_status = "Auth error"
                    self.is_running = False
                self.emit_status()
                return

            count = len(self.games) if (game_count is None or game_count <= 0) else min(game_count, len(self.games))
            selected = select_games(self.games, self.state, count)
            if not selected:
                self.emit_log("err", "Không có trò chơi nào được chọn.", "RUNNER")
                with self.lock:
                    self.bot_status = "Bot ready"
                    self.is_running = False
                self.emit_status()
                return

            duration_ms = int(hours * 3600 * 1000)
            total_hours = len(selected) * hours
            self.emit_log("info", f"Cày {hours:g}h cho {len(selected)} games (~{total_hours:,.0f}h tổng)", "RUNNER")

            with self.lock:
                self.progress = {
                    "current": 0,
                    "total": len(selected),
                    "hours": hours,
                    "claimed_hours": 0.0
                }
            self.emit_status()

            sent_ok = 0
            uid = _user_id_from_token(self.state.token)

            for request_no, chunk in enumerate(_chunked(selected, BATCH_SIZE), start=1):
                if self.should_stop:
                    self.emit_log("warn", "Tác vụ đã dừng theo yêu cầu của người dùng.", "RUNNER")
                    break

                events = []
                for game in chunk:
                    if mode == "games_played":
                        events.extend(self.client.build_session(game, 60000))
                    else:
                        events.extend(self.client.build_session(game, duration_ms))

                status = self.client.post(events)
                
                if self.should_stop:
                    break

                if status == 204:
                    sent_ok += len(chunk)
                    claimed_now = sent_ok * (0.016 if mode == "games_played" else hours)
                    
                    with self.lock:
                        self.progress["current"] = sent_ok
                        self.progress["claimed_hours"] = round(claimed_now, 2)
                        
                        self.state.total_games_claimed += len(chunk)
                        self.state.total_hours_claimed += (len(chunk) * (0.016 if mode == "games_played" else hours))
                        
                        save_user_stats(uid, self.state.total_games_claimed, self.state.total_hours_claimed)
                        
                    self.emit_log("sent", f"Batch #{request_no} hoàn tất ({sent_ok}/{len(selected)} games)", "SCIENCE")
                    self.emit_status()
                elif status in (401, 403):
                    self.emit_log("warn", "Vui lòng làm mới cookie cf_clearance.", "SCIENCE")
                    break
                else:
                    self.emit_log("warn", f"Batch #{request_no} phản hồi mã không xác định: {status}", "SCIENCE")

                for _ in range(int(BATCH_DELAY * 10)):
                    if self.should_stop: break
                    time.sleep(0.1)

            self.emit_log("ok", f"Hoàn thành: Đã cày xong {sent_ok}/{len(selected)} games.", "COMPLETE")
        except Exception as exc:
            self.emit_log("err", f"Lỗi hệ thống: {exc}", "ERROR")
        finally:
            with self.lock:
                self.is_running = False
                self.should_stop = False
                self.bot_status = "Bot ready"
            self.emit_status()


class SessionManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.games: list[Game] = []
        self.clients: dict[str, ClientWorker] = {}

    def init_data(self):
        self.games = load_games()

    def get_worker(self, client_id: str) -> ClientWorker:
        cid = client_id.strip() if client_id and client_id.strip() else "default"
        with self.lock:
            now = time.time()
            stale = [k for k, w in self.clients.items() if not w.is_running and (now - w.last_active) > 7200]
            for k in stale:
                del self.clients[k]

            if cid not in self.clients:
                self.clients[cid] = ClientWorker(cid, self.games)
            worker = self.clients[cid]
            worker.last_active = now
            return worker


session_manager = SessionManager()

class DashboardHandler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def log_message(self, format, *args):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        client_id = qs.get("clientId", ["default"])[0]

        if parsed.path == "/api/events":
            worker = session_manager.get_worker(client_id)
            self._handle_sse(worker)
        elif parsed.path == "/api/status":
            worker = session_manager.get_worker(client_id)
            self._json_response(worker.get_status_data())
        elif parsed.path == "/api/logs":
            worker = session_manager.get_worker(client_id)
            with worker.lock:
                self._json_response({"logs": worker.log_history})
        elif parsed.path == "/api/games":
            query = qs.get("q", [""])[0].lower()
            matching = []
            for g in session_manager.games:
                if not query or query in g.name.lower() or query in g.id:
                    matching.append({"id": g.id, "name": g.name, "exe": g.exe})
                    if len(matching) >= 50:
                        break
            self._json_response({"total": len(session_manager.games), "games": matching})
        else:
            if parsed.path == "/":
                self.path = "/index.html"
            super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        # SỬA LỖI TẠI ĐÂY: Lấy ID từ URL thay vì Body để lệnh Stop hoạt động đúng
        qs = urllib.parse.parse_qs(parsed.query)
        client_id = qs.get("clientId", ["default"])[0]

        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else "{}"
        try:
            data = json.loads(body)
        except Exception:
            data = {}

        worker = session_manager.get_worker(client_id)

        if parsed.path == "/api/run":
            token = data.get("token")
            cookie = data.get("cookie")
            fingerprint = data.get("fingerprint")
            with worker.lock:
                if token is not None:
                    worker.state.token = token.strip()
                if cookie is not None:
                    worker.state.cookie = cookie.strip()
                if fingerprint is not None:
                    worker.state.fingerprint = fingerprint.strip()

            mode = data.get("mode", "both")
            hours = float(data.get("hours", 10.0))
            count_val = data.get("count", None)
            count = int(count_val) if count_val not in (None, "", "all") else None

            ok, msg, missing = worker.start_bot(count, hours, mode)
            self._json_response({"success": ok, "message": msg, "missing": missing})

        elif parsed.path == "/api/stop":
            worker.stop_bot()
            self._json_response({"success": True, "message": "Đang gửi tín hiệu dừng..."})

        elif parsed.path == "/api/refresh_auth":
            token = data.get("token")
            cookie = data.get("cookie")
            with worker.lock:
                if token:
                    worker.state.token = token.strip()
                if cookie:
                    worker.state.cookie = cookie.strip()
            ok, msg = worker.refresh_auth()
            self._json_response({"success": ok, "message": msg})

        elif parsed.path == "/api/logs/clear":
            with worker.lock:
                worker.log_history.clear()
            self._json_response({"success": True})
        else:
            self.send_error(404, "Endpoint not found")

    def _json_response(self, obj: dict, status_code: int = 200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _handle_sse(self, worker: ClientWorker):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        q = queue.Queue(maxsize=100)
        with worker.lock:
            worker.listeners.append(q)

        try:
            init_msg = json.dumps({"type": "init", "status": worker.get_status_data(), "logs": worker.log_history[-100:]})
            self.wfile.write(("data: " + init_msg + "\n\n").encode("utf-8"))
            self.wfile.flush()
        except Exception:
            return

        try:
            while True:
                try:
                    event = q.get(timeout=20)
                    payload = json.dumps(event)
                    self.wfile.write(("data: " + payload + "\n\n").encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with worker.lock:
                if q in worker.listeners:
                    worker.listeners.remove(q)

def run_web_server(port: int = 7860, host: str = "0.0.0.0"):
    print("\n==================================================")
    print("  DISCORD BADGE SPOOFER")
    print("  Skid by Yusei")
    print("--------------------------------------------------")
    print(f"  Local Server: http://127.0.0.1:{port}")
    print("==================================================\n")
    
    session_manager.init_data()
    
    try:
        server = ThreadingHTTPServer((host, port), DashboardHandler)
    except OSError as e:
        print(f"\n[!] LỖI: Không thể mở cổng {port}.")
        print("    Nguyên nhân: Có thể một tiến trình Python khác đang chạy ngầm (bị kẹt).")
        print("    Cách sửa: Mở Task Manager (Ctrl+Shift+Esc), tìm 'Python' và End Task.")
        print("    Sau đó chạy lại lệnh python server.py.")
        sys.exit(1)

    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        server.server_close()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "7860"))
    run_web_server(port=port)