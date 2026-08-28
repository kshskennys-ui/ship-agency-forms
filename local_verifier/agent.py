"""Windows 本地海员证核验助手。

仅监听 127.0.0.1，供云端网页在用户明确点击后调用。
核验浏览器在用户电脑上可见运行，结果由网页回传云端保存。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
if not getattr(sys, "frozen", False):
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / "runtime" / "playwright-browsers"))
os.environ.setdefault("SHIP_AGENCY_SEAFARER_HEADLESS", "false")
if os.name == "nt":
    os.environ.setdefault(
        "SHIP_AGENCY_SEAFARER_USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    )

from app.services.seafarer_verifier import SeafarerQueryRunner  # noqa: E402


HOST = "127.0.0.1"
PORT = 17321
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _snapshot(job: dict) -> dict:
    with _jobs_lock:
        return {
            "task_id": job["task_id"],
            "status": job["status"],
            "processed": job["processed"],
            "total": job["total"],
            "error": job.get("error"),
            "items": list(job["items"].values()),
        }


def _run(job: dict):
    with _jobs_lock:
        job["status"] = "查询中"

    def on_result(result: dict):
        member_id = str(result.get("crew_member_id"))
        with _jobs_lock:
            item = job["items"].get(member_id)
            if item is not None:
                item.update(result)
            job["processed"] += 1

    try:
        SeafarerQueryRunner(stop_event=job["stop_event"]).run(list(job["items"].values()), on_result)
        with _jobs_lock:
            job["status"] = "已停止" if job["stop_event"].is_set() else "已完成"
    except Exception as exc:
        with _jobs_lock:
            job["status"] = "失败"
            job["error"] = str(exc)[:500]
            for item in job["items"].values():
                if item.get("status") in {"待查询", "查询中"}:
                    item["status"] = "失败"
                    item["error_info"] = str(exc)[:256]


class Handler(BaseHTTPRequestHandler):
    server_version = "ShipAgencyVerifier/1.0"

    def _headers(self, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def _write(self, data: dict, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._headers(status)
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > 2_000_000:
            raise ValueError("请求数据过大")
        return json.loads(self.rfile.read(length) or b"{}")

    def do_OPTIONS(self):
        self._headers(204)

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/")
        if path == "/health":
            self._write({"status": "ok", "service": "ship-agency-seafarer-agent", "version": "1.0"})
            return
        if path.startswith("/jobs/"):
            task_id = path.split("/", 2)[-1]
            with _jobs_lock:
                job = _jobs.get(task_id)
            if not job:
                self._write({"error": "核验任务不存在"}, 404)
                return
            self._write(_snapshot(job))
            return
        self._write({"error": "Not Found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/")
        try:
            payload = self._read_json()
        except Exception as exc:
            self._write({"error": str(exc)}, 400)
            return

        if path == "/verify":
            rows = payload.get("rows")
            if not isinstance(rows, list) or not rows:
                self._write({"error": "没有可核验人员"}, 400)
                return
            task_id = str(payload.get("task_id") or uuid.uuid4().hex)
            items = {}
            for row in rows:
                try:
                    member_id = int(row["crew_member_id"])
                except (KeyError, TypeError, ValueError):
                    continue
                items[str(member_id)] = {
                    "crew_member_id": member_id,
                    "name": str(row.get("name") or ""),
                    "nationality": str(row.get("nationality") or ""),
                    "rank": str(row.get("rank") or ""),
                    "document_no": str(row.get("document_no") or ""),
                    "status": "待查询",
                    "error_info": None,
                    "certificate_status": None,
                    "attempts": 0,
                }
            if not items:
                self._write({"error": "没有有效的核验人员"}, 400)
                return
            job = {
                "task_id": task_id,
                "status": "排队中",
                "processed": 0,
                "total": len(items),
                "error": None,
                "stop_event": threading.Event(),
                "items": items,
            }
            with _jobs_lock:
                _jobs[task_id] = job
            threading.Thread(target=_run, args=(job,), daemon=True).start()
            self._write(_snapshot(job))
            return

        if path.startswith("/jobs/") and path.endswith("/stop"):
            task_id = path.split("/")[2]
            with _jobs_lock:
                job = _jobs.get(task_id)
                if job and job["status"] in {"排队中", "查询中"}:
                    job["status"] = "停止中"
                    job["stop_event"].set()
            if not job:
                self._write({"error": "核验任务不存在"}, 404)
            else:
                self._write(_snapshot(job))
            return

        self._write({"error": "Not Found"}, 404)

    def log_message(self, _format, *_args):
        return


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
