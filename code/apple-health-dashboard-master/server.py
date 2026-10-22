#!/usr/bin/env python3
"""Boji Health local server: static files, authentication, and SQLite persistence."""

from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import sys
import time
from contextlib import contextmanager
from difflib import SequenceMatcher
from functools import lru_cache
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen

from config_env import load_env
from agent_orchestrator import run_agent
from agent_tools import TOOL_CARDS
from analytics import analyze
from metric_definitions import catalog
from record_metadata import normalize_state_records
from record_store import KINDS, RecordConflict, delete_record, get_record, insert_record, list_records, summaries, update_record


APP_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = APP_DIR.parents[1]
ENV_PATH = WORKSPACE_DIR / ".env"
load_env(ENV_PATH)
KNOWLEDGE_DIR = WORKSPACE_DIR / "knowledge-base"
DATA_DIR = WORKSPACE_DIR / "data"
DB_PATH = Path(os.environ.get("BOJI_DB_PATH", str(DATA_DIR / "boji_health.db"))).resolve()
SESSION_SECONDS = 30 * 24 * 60 * 60
MAX_BODY = 20 * 1024 * 1024
USERNAME_RE = re.compile(r"^[\w\-.\u4e00-\u9fff]{2,32}$", re.UNICODE)
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
TOOL_REGISTRY = TOOL_CARDS + [
    {
        "id": "food_nutrition_lookup",
        "name": "食物卡路里查询",
        "description": "按食物名称、重量及消费备注查询热量和三大营养素；组合餐会从备注拆分，优先本地知识库，未完整命中时使用 AI 估算。",
        "inputs": ["食物名称", "重量（克）", "备注（可含多道食物）"],
    }
]


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_data (
                username TEXT PRIMARY KEY,
                data_json TEXT NOT NULL DEFAULT '{}',
                updated_at INTEGER NOT NULL,
                FOREIGN KEY (username) REFERENCES users(username) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                FOREIGN KEY (username) REFERENCES users(username) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);
            """
        )


def password_hash(password: str, salt: bytes) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 240_000)
    return digest.hex()


def deepseek_api_key() -> str:
    return os.environ.get("DEEPSEEK_API_KEY", "").strip()


@lru_cache(maxsize=1)
def knowledge_foods() -> list[dict]:
    bundle = (KNOWLEDGE_DIR / "browser-bundle.js").read_text(encoding="utf-8")
    marker = "window.BOJI_KB = "
    if not bundle.startswith(marker):
        return []
    payload = bundle[len(marker):].strip()
    if payload.endswith(";"):
        payload = payload[:-1]
    return json.loads(payload).get("foods", [])


def normalized_food_name(value: str) -> str:
    return re.sub(r"[\s（）()\[\]【】/\\·,，.。\-]+", "", value.lower())


def find_knowledge_food(query: str) -> tuple[dict | None, float]:
    needle = normalized_food_name(query)
    if not needle:
        return None, 0.0
    best, best_score = None, 0.0
    for food in knowledge_foods():
        candidate = normalized_food_name(str(food.get("name", "")))
        if needle == candidate:
            return food, 1.0
        containment = 0.9 if len(needle) >= 2 and (needle in candidate or candidate in needle) else 0.0
        score = max(containment, SequenceMatcher(None, needle, candidate).ratio())
        if score > best_score:
            best, best_score = food, score
    return (best, best_score) if best_score >= 0.62 else (None, best_score)


def calculate_food(food: dict, amount_grams: float) -> dict:
    if food.get("unit") == "serving":
        factor = amount_grams / max(float(food.get("grams") or 100), 1)
    else:
        factor = amount_grams / 100
    return {
        "name": food.get("name"),
        "amount": amount_grams,
        "unit": "g",
        "kcal": round(float(food.get("kcal") or 0) * factor, 1),
        "carb": round(float(food.get("carb") or 0) * factor, 1),
        "protein": round(float(food.get("protein") or 0) * factor, 1),
        "fat": round(float(food.get("fat") or 0) * factor, 1),
        "source": "knowledge",
        "sourceRef": food.get("source"),
        "confidence": "high",
        "note": food.get("reference_note") or "本地知识库匹配",
    }


def measured_note_items(description: str) -> list[dict] | None:
    parts = [part.strip() for part in re.split(r"[,，、;；+＋\n]+", description) if part.strip()]
    if len(parts) < 2:
        return None
    results = []
    for part in parts:
        match = re.match(r"^(.+?)[x×*]?\s*(\d+(?:\.\d+)?)\s*(g|克|ml|毫升|个|份|盒|杯)\s*$", part, re.IGNORECASE)
        if not match:
            return None
        item_name, number, unit = match.group(1).strip(), float(match.group(2)), match.group(3).lower()
        food, _ = find_knowledge_food(item_name)
        if not food:
            return None
        if unit in ("g", "克", "ml", "毫升"):
            grams = number
        elif food.get("unit") == "serving" and food.get("grams"):
            grams = number * float(food["grams"])
        else:
            return None
        item = calculate_food(food, grams)
        item["input"] = part
        results.append(item)
    return results


def combined_food_result(name: str, items: list[dict], source: str, confidence: str, note: str) -> dict:
    return {
        "name": name,
        "amount": round(sum(float(item.get("amount") or 0) for item in items), 1),
        "unit": "g",
        "kcal": round(sum(float(item.get("kcal") or 0) for item in items), 1),
        "carb": round(sum(float(item.get("carb") or 0) for item in items), 1),
        "protein": round(sum(float(item.get("protein") or 0) for item in items), 1),
        "fat": round(sum(float(item.get("fat") or 0) for item in items), 1),
        "source": source,
        "confidence": confidence,
        "note": note,
        "items": items,
    }


class BojiHandler(BaseHTTPRequestHandler):
    server_version = "BojiHealth/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stdout.write(f"[{self.log_date_time_string()}] {fmt % args}\n")

    def json_response(self, payload: object, status: int = 200, cookie: str | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("请求长度无效") from exc
        if length <= 0 or length > MAX_BODY:
            raise ValueError("请求内容为空或过大")
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("请求内容不是有效 JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("请求内容必须是对象")
        return value

    def session_username(self) -> str | None:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        token = cookie.get("boji_session")
        if not token:
            return None
        now = int(time.time())
        with connect() as db:
            db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            row = db.execute(
                "SELECT username FROM sessions WHERE token = ? AND expires_at > ?",
                (token.value, now),
            ).fetchone()
        return row["username"] if row else None

    @staticmethod
    def session_cookie(token: str, max_age: int = SESSION_SECONDS) -> str:
        return f"boji_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}"

    def create_session(self, db: sqlite3.Connection, username: str) -> str:
        token = secrets.token_urlsafe(32)
        db.execute(
            "INSERT INTO sessions(token, username, expires_at) VALUES(?, ?, ?)",
            (token, username, int(time.time()) + SESSION_SECONDS),
        )
        return token

    def user_payload(self, db: sqlite3.Connection, username: str, created: bool = False) -> dict:
        row = db.execute("SELECT data_json, updated_at FROM user_data WHERE username = ?", (username,)).fetchone()
        data = json.loads(row["data_json"]) if row and row["data_json"] else {}
        if db.execute("PRAGMA user_version").fetchone()[0] >= 2:
            for kind, collection in KINDS.items():
                data[collection] = list_records(db, username, kind)
        else:
            data, _ = normalize_state_records(data, username, migration=True)
        return {"ok": True, "user": {"username": username}, "data": data, "updatedAt": row["updated_at"] if row else None, "created": created}

    def handle_register(self) -> None:
        body = self.read_json()
        username = str(body.get("username", "")).strip()
        password = str(body.get("password", ""))
        if not USERNAME_RE.fullmatch(username):
            self.json_response({"ok": False, "error": "用户名需为 2–32 位，可使用中文、字母、数字、点、横线或下划线"}, 400)
            return
        if len(password) < 6 or len(password) > 128:
            self.json_response({"ok": False, "error": "密码长度需为 6–128 位"}, 400)
            return
        salt = os.urandom(16)
        try:
            with connect() as db:
                db.execute(
                    "INSERT INTO users(username, password_hash, salt, created_at) VALUES(?, ?, ?, ?)",
                    (username, password_hash(password, salt), salt.hex(), int(time.time())),
                )
                db.execute("INSERT INTO user_data(username, data_json, updated_at) VALUES(?, '{}', ?)", (username, int(time.time())))
                token = self.create_session(db, username)
                payload = self.user_payload(db, username, created=True)
        except sqlite3.IntegrityError:
            self.json_response({"ok": False, "error": "该用户名已存在，请换一个用户名"}, 409)
            return
        self.json_response(payload, 201, self.session_cookie(token))

    def handle_login(self) -> None:
        body = self.read_json()
        username = str(body.get("username", "")).strip()
        password = str(body.get("password", ""))
        with connect() as db:
            row = db.execute("SELECT password_hash, salt FROM users WHERE username = ?", (username,)).fetchone()
            valid = bool(row) and hmac.compare_digest(password_hash(password, bytes.fromhex(row["salt"])), row["password_hash"])
            if not valid:
                self.json_response({"ok": False, "error": "用户名或密码不正确"}, 401)
                return
            token = self.create_session(db, username)
            payload = self.user_payload(db, username)
        self.json_response(payload, cookie=self.session_cookie(token))

    def handle_session(self) -> None:
        username = self.session_username()
        if not username:
            self.json_response({"ok": False, "authenticated": False}, 401)
            return
        with connect() as db:
            payload = self.user_payload(db, username)
        payload["authenticated"] = True
        self.json_response(payload)

    def handle_save(self) -> None:
        username = self.session_username()
        if not username:
            self.json_response({"ok": False, "error": "登录已失效，请重新登录"}, 401)
            return
        body = self.read_json()
        data = body.get("data")
        if not isinstance(data, dict):
            self.json_response({"ok": False, "error": "数据格式无效"}, 400)
            return
        expected = body.get("expectedUpdatedAt")
        if isinstance(expected, bool) or not isinstance(expected, int):
            self.json_response({"ok": False, "error": "缺少数据版本，请刷新页面后重试"}, 400)
            return
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT updated_at FROM user_data WHERE username = ?", (username,)).fetchone()
            if row is None or row["updated_at"] != expected:
                self.json_response({"ok": False, "error": "数据已在其他位置更新，请刷新页面后重试", "updatedAt": row["updated_at"] if row else None}, 409)
                return
            old = db.execute("SELECT data_json FROM user_data WHERE username = ?", (username,)).fetchone()
            previous = json.loads(old["data_json"] or "{}")
            if db.execute("PRAGMA user_version").fetchone()[0] >= 2:
                # The legacy record arrays stay untouched as a migration snapshot.
                for key in ("profile", "chat", "settings", "health"):
                    if key in data:
                        previous[key] = data[key]
                encoded = json.dumps(previous, ensure_ascii=False, separators=(",", ":"))
            else:
                data, _ = normalize_state_records(data, username, previous=previous)
                encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            now = max(int(time.time() * 1000), expected + 1)
            db.execute("UPDATE user_data SET data_json = ?, updated_at = ? WHERE username = ?", (encoded, now, username))
        self.json_response({"ok": True, "updatedAt": now})

    def handle_records(self, path: str) -> None:
        username = self.session_username()
        if not username:
            self.json_response({"ok": False, "error": "登录已失效，请重新登录"}, 401)
            return
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[2] == "summary" and self.command == "GET":
            query = parse_qs(urlparse(self.path).query)
            days = int(query.get("days", ["7"])[0])
            if days not in (7, 30):
                raise ValueError("汇总仅支持 7 或 30 天")
            include_demo = query.get("includeDemo", ["1"])[0] != "0"
            with connect() as db:
                result = summaries(db, username, days, include_demo=include_demo)
            self.json_response({"ok": True, "summary": result})
            return
        if len(parts) < 3 or len(parts) > 5 or parts[2] not in KINDS:
            self.json_response({"ok": False, "error": "记录接口不存在"}, 404)
            return
        kind = parts[2]
        record_id = parts[3] if len(parts) >= 4 else None
        if len(parts) == 5:
            if parts[4] != "events" or self.command != "GET":
                self.json_response({"ok": False, "error": "记录接口不存在"}, 404)
                return
            with connect() as db:
                if get_record(db, username, kind, record_id) is None:
                    self.json_response({"ok": False, "error": "记录不存在"}, 404)
                    return
                rows = db.execute("SELECT event_type,actor,occurred_at,before_json,after_json,version FROM record_events WHERE username=? AND record_id=? ORDER BY id", (username, record_id)).fetchall()
            self.json_response({"ok": True, "events": [dict(row) for row in rows]})
            return
        if self.command == "GET":
            with connect() as db:
                if record_id:
                    record = get_record(db, username, kind, record_id)
                    if record is None:
                        self.json_response({"ok": False, "error": "记录不存在"}, 404)
                        return
                    self.json_response({"ok": True, "record": record})
                else:
                    include_demo = parse_qs(urlparse(self.path).query).get("includeDemo", ["1"])[0] != "0"
                    self.json_response({"ok": True, "records": list_records(db, username, kind, include_demo=include_demo)})
            return
        if self.command == "POST" and not record_id:
            body = self.read_json()
            record = body.get("record")
            if not isinstance(record, dict):
                raise ValueError("记录必须是对象")
            try:
                with connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    created = insert_record(db, username, kind, record)
            except sqlite3.IntegrityError as exc:
                self.json_response({"ok": False, "error": f"记录 ID 重复或关联无效：{exc}"}, 409)
                return
            self.json_response({"ok": True, "record": created}, 201)
            return
        if self.command in ("PATCH", "DELETE") and record_id:
            body = self.read_json()
            expected = body.get("expectedVersion")
            if isinstance(expected, bool) or not isinstance(expected, int) or expected < 1:
                raise ValueError("请提供记录版本 expectedVersion")
            try:
                with connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    if self.command == "PATCH":
                        result = update_record(db, username, kind, record_id, body.get("changes"), expected)
                    else:
                        result = delete_record(db, username, kind, record_id, expected)
            except RecordConflict as exc:
                self.json_response({"ok": False, "error": str(exc), "currentVersion": exc.current_version}, 409)
                return
            if result is None:
                self.json_response({"ok": False, "error": "记录不存在"}, 404)
            else:
                self.json_response({"ok": True, "record": result if self.command == "PATCH" else None})
            return
        self.json_response({"ok": False, "error": "不支持的记录操作"}, 405)

    def handle_analytics(self, path: str) -> None:
        username = self.session_username()
        if not username:
            self.json_response({"ok": False, "error": "登录已失效，请重新登录"}, 401)
            return
        if path == "/api/analytics/metrics":
            self.json_response({"ok": True, **catalog()})
            return
        if path != "/api/analytics":
            self.json_response({"ok": False, "error": "分析接口不存在"}, 404)
            return
        query = parse_qs(urlparse(self.path).query)
        try:
            days = int(query.get("days", ["7"])[0])
        except (TypeError, ValueError) as exc:
            raise ValueError("分析窗口必须为 7 或 30 天") from exc
        include_demo = query.get("includeDemo", ["1"])[0] != "0"
        with connect() as db:
            report = analyze(db, username, days, include_demo=include_demo)
        self.json_response({"ok": True, "report": report})

    def handle_logout(self) -> None:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        token = cookie.get("boji_session")
        if token:
            with connect() as db:
                db.execute("DELETE FROM sessions WHERE token = ?", (token.value,))
        self.json_response({"ok": True}, cookie=self.session_cookie("", 0))

    def handle_agent_status(self) -> None:
        if not self.session_username():
            self.json_response({"ok": False, "error": "请先登录"}, 401)
            return
        self.json_response({"ok": True, "configured": bool(deepseek_api_key()), "model": DEEPSEEK_MODEL})

    def handle_agent_tools(self) -> None:
        if not self.session_username():
            self.json_response({"ok": False, "error": "请先登录"}, 401)
            return
        self.json_response({"ok": True, "tools": TOOL_REGISTRY})

    def handle_food_tool(self) -> None:
        if not self.session_username():
            self.json_response({"ok": False, "error": "请先登录"}, 401)
            return
        body = self.read_json()
        name = str(body.get("name", "")).strip()[:120]
        description = str(body.get("description", "")).strip()[:1000]
        try:
            amount = float(body.get("amount", 0))
        except (TypeError, ValueError):
            amount = 0
        if not name or not 0 < amount <= 5000:
            self.json_response({"ok": False, "error": "请提供食物名称和 0–5000g 之间的重量"}, 400)
            return
        is_composite = bool(
            description
            and (
                re.search(r"[,，、;；+＋\n]", description)
                or len(re.findall(r"\d+(?:\.\d+)?\s*(?:g|克|ml|毫升|个|份|盒|杯)", description, re.IGNORECASE)) >= 2
            )
        )
        if is_composite:
            local_items = measured_note_items(description)
            if local_items:
                result = combined_food_result(name, local_items, "knowledge", "high", "已从备注拆分；各项均由本地知识库计算")
                self.json_response({"ok": True, "result": result})
                return

            api_key = deepseek_api_key()
            if not api_key:
                self.json_response({"ok": False, "error": "备注包含多道食物，但知识库无法完整匹配，且 DeepSeek API Key 尚未配置"}, 503)
                return
            prompt = (
                "你是组合餐营养拆分工具。读取消费备注，把其中实际吃到的每一种食物或饮料拆开，"
                "并按常见中国成品份量估算可食重量与营养。若备注写明重量或数量，优先采用备注。"
                "只返回合法 JSON，不要 Markdown，不要解释。格式必须为："
                '{"items":[{"name":"标准食物名","amount":克数,"kcal":数字,"carb":数字,"protein":数字,"fat":数字}],'
                '"confidence":"low或medium","note":"估算依据简述"}。'
                "items 为 2–12 项；营养数值均为该项本次食用总量，单位分别为 kcal 和 g，不能为负。"
                f"\n消费名称：{name}\n消费备注：{description}"
            )
            request_body = json.dumps({"model": DEEPSEEK_MODEL, "messages": [{"role": "user", "content": prompt}], "stream": False, "temperature": 0.1}, ensure_ascii=False).encode("utf-8")
            request = Request(f"{DEEPSEEK_BASE_URL}/chat/completions", data=request_body, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, method="POST")
            try:
                with urlopen(request, timeout=75) as response:
                    model_result = json.loads(response.read().decode("utf-8"))
                content = model_result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.IGNORECASE)
                estimate = json.loads(content)
                raw_items = estimate.get("items")
                if not isinstance(raw_items, list) or not 2 <= len(raw_items) <= 12:
                    raise ValueError("未能拆分出 2–12 道食物")
                confidence = estimate.get("confidence") if estimate.get("confidence") in ("low", "medium") else "low"
                note = str(estimate.get("note") or "AI 根据消费备注拆分并估算，实际以份量和包装营养表为准")[:240]
                items = []
                for raw in raw_items:
                    item_amount = round(max(0.1, float(raw.get("amount", 0))), 1)
                    if item_amount > 5000:
                        raise ValueError("单项重量超出合理范围")
                    item = {
                        "name": str(raw.get("name") or "未命名食物")[:120],
                        "amount": item_amount,
                        "unit": "g",
                        "source": "ai_estimate",
                        "confidence": confidence,
                        "note": note,
                    }
                    for key in ("kcal", "carb", "protein", "fat"):
                        item[key] = round(max(0.0, float(raw.get(key, 0))), 1)
                    if item["kcal"] > item_amount * 10 or any(item[key] > item_amount * 1.5 for key in ("carb", "protein", "fat")):
                        raise ValueError(f"{item['name']} 的估算值超出合理范围")
                    items.append(item)
                result = combined_food_result(name, items, "ai_estimate", confidence, f"已从备注拆分为 {len(items)} 项；{note}")
                self.json_response({"ok": True, "result": result, "model": model_result.get("model", DEEPSEEK_MODEL)})
            except HTTPError as exc:
                self.json_response({"ok": False, "error": f"AI 组合餐拆分请求失败（{exc.code}）"}, 502)
            except (URLError, TimeoutError) as exc:
                self.json_response({"ok": False, "error": f"无法连接 AI 营养估算服务：{exc.reason if hasattr(exc, 'reason') else exc}"}, 502)
            except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                self.json_response({"ok": False, "error": f"AI 返回的组合餐数据无法使用：{exc}"}, 502)
            return
        food, score = find_knowledge_food(name)
        if food:
            result = calculate_food(food, amount)
            result["matchScore"] = round(score, 2)
            self.json_response({"ok": True, "result": result})
            return
        api_key = deepseek_api_key()
        if not api_key:
            self.json_response({"ok": False, "error": "知识库未找到该食物，且 DeepSeek API Key 尚未配置"}, 503)
            return
        prompt = (
            "你是食物营养估算工具。根据常见中国食物成品的可食部分，估算指定重量对应的营养。"
            "只返回合法 JSON，不要 Markdown，不要解释。字段必须为："
            '{"name":"标准食物名","kcal":数字,"carb":数字,"protein":数字,"fat":数字,"confidence":"low或medium","note":"估算依据简述"}。'
            "所有营养数值均为本次指定重量的总量，单位分别为 kcal 和 g；数值不能为负。"
            f"\n待估算：{name}，重量：{amount:g}g"
        )
        request_body = json.dumps({"model": DEEPSEEK_MODEL, "messages": [{"role": "user", "content": prompt}], "stream": False, "temperature": 0.1}, ensure_ascii=False).encode("utf-8")
        request = Request(f"{DEEPSEEK_BASE_URL}/chat/completions", data=request_body, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=75) as response:
                model_result = json.loads(response.read().decode("utf-8"))
            content = model_result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.IGNORECASE)
            estimate = json.loads(content)
            result = {"name": str(estimate.get("name") or name)[:120], "amount": amount, "unit": "g", "source": "ai_estimate", "confidence": estimate.get("confidence") if estimate.get("confidence") in ("low", "medium") else "low", "note": str(estimate.get("note") or "AI 估算，实际以包装营养表为准")[:240]}
            for key in ("kcal", "carb", "protein", "fat"):
                result[key] = round(max(0.0, float(estimate.get(key, 0))), 1)
            if result["kcal"] > amount * 10 or any(result[key] > amount * 1.5 for key in ("carb", "protein", "fat")):
                raise ValueError("估算值超出合理范围")
            self.json_response({"ok": True, "result": result, "model": model_result.get("model", DEEPSEEK_MODEL)})
        except HTTPError as exc:
            self.json_response({"ok": False, "error": f"AI 营养估算请求失败（{exc.code}）"}, 502)
        except (URLError, TimeoutError) as exc:
            self.json_response({"ok": False, "error": f"无法连接 AI 营养估算服务：{exc.reason if hasattr(exc, 'reason') else exc}"}, 502)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            self.json_response({"ok": False, "error": f"AI 返回的营养数据无法使用：{exc}"}, 502)

    def handle_chat(self) -> None:
        username = self.session_username()
        if not username:
            self.json_response({"ok": False, "error": "登录已失效，请重新登录"}, 401)
            return
        api_key = deepseek_api_key()
        if not api_key:
            self.json_response({"ok": False, "error": "DeepSeek API Key 尚未配置，请运行“配置DeepSeek.bat”"}, 503)
            return
        body = self.read_json()
        incoming = body.get("messages")
        if not isinstance(incoming, list) or not incoming:
            self.json_response({"ok": False, "error": "聊天内容不能为空"}, 400)
            return
        messages = []
        for item in incoming[-12:]:
            if not isinstance(item, dict) or item.get("role") not in ("user", "assistant"):
                continue
            content = str(item.get("content", "")).strip()[:4000]
            if content:
                messages.append({"role": item["role"], "content": content})
        if not messages or messages[-1]["role"] != "user":
            self.json_response({"ok": False, "error": "最后一条消息必须来自用户"}, 400)
            return
        try:
            with connect() as db:
                row = db.execute("SELECT data_json FROM user_data WHERE username = ?", (username,)).fetchone()
                user_data = json.loads(row["data_json"]) if row and row["data_json"] else {}
                result = run_agent(db, username, messages, api_key=api_key, base_url=DEEPSEEK_BASE_URL,
                                   model=DEEPSEEK_MODEL, profile=user_data.get("profile"),
                                   find_food=find_knowledge_food, calculate_food=calculate_food)
            structured = result["response"]
            self.json_response({"ok": True, "message": structured["answer"], **result})
        except HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("error", {}).get("message", "")
            except Exception:
                detail = ""
            self.json_response({"ok": False, "error": detail or f"DeepSeek API 请求失败（{exc.code}）"}, 502)
        except (URLError, TimeoutError) as exc:
            self.json_response({"ok": False, "error": f"无法连接 DeepSeek API：{exc.reason if hasattr(exc, 'reason') else exc}"}, 502)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            self.json_response({"ok": False, "error": f"Agent 工具调用或回答校验失败：{exc}"}, 502)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/health":
            self.json_response({"ok": True, "service": "boji-health", "database": "ready"})
        elif path == "/api/session":
            self.handle_session()
        elif path == "/api/agent/status":
            self.handle_agent_status()
        elif path == "/api/agent/tools":
            self.handle_agent_tools()
        elif path == "/api/analytics" or path == "/api/analytics/metrics":
            try:
                self.handle_analytics(path)
            except ValueError as exc:
                self.json_response({"ok": False, "error": str(exc)}, 400)
        elif path.startswith("/api/records/"):
            try:
                self.handle_records(path)
            except ValueError as exc:
                self.json_response({"ok": False, "error": str(exc)}, 400)
        else:
            self.serve_static(path)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/auth/register":
                self.handle_register()
            elif path == "/api/auth/login":
                self.handle_login()
            elif path == "/api/auth/logout":
                self.handle_logout()
            elif path == "/api/data":
                self.handle_save()
            elif path == "/api/chat":
                self.handle_chat()
            elif path == "/api/tools/food-nutrition":
                self.handle_food_tool()
            elif path.startswith("/api/records/"):
                self.handle_records(path)
            else:
                self.json_response({"ok": False, "error": "接口不存在"}, 404)
        except ValueError as exc:
            self.json_response({"ok": False, "error": str(exc)}, 400)

    do_PUT = do_POST

    def do_PATCH(self) -> None:
        self.do_POST()

    def do_DELETE(self) -> None:
        self.do_POST()

    def serve_static(self, raw_path: str) -> None:
        if raw_path in ("", "/"):
            raw_path = "/boji-health.html"
        decoded = unquote(raw_path)
        if decoded.startswith("/knowledge-base/"):
            root = KNOWLEDGE_DIR
            relative = decoded.removeprefix("/knowledge-base/")
        else:
            root = APP_DIR
            relative = decoded.lstrip("/")
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not candidate.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        mime, _ = mimetypes.guess_type(candidate.name)
        content = candidate.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{mime or 'application/octet-stream'}{'; charset=utf-8' if (mime or '').startswith('text/') or mime in ('application/javascript', 'application/json') else ''}")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(content)


def main() -> None:
    init_db()
    from migrate_records import migrate
    migration = migrate(DB_PATH)
    if migration["status"] == "migrated":
        print(f"记录级数据库迁移完成：{migration['backup'] or '新数据库'}", flush=True)
    port = int(os.environ.get("BOJI_PORT", "4173"))
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), BojiHandler)
    except OSError as exc:
        print(f"无法启动：端口 {port} 已被占用。请先关闭原来的预览服务，再重新运行。", file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"Boji Health 本地服务已启动：http://127.0.0.1:{port}/boji-health.html", flush=True)
    print(f"数据库：{DB_PATH}", flush=True)
    print(f"Chat Agent：{'已配置' if deepseek_api_key() else '未配置（请运行 配置DeepSeek.bat）'}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
