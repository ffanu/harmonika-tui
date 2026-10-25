"""Private inventory starter. Set admin/session secrets before using; never defaults to an account."""

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
import threading
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ConfigDict

BASE = Path(__file__).resolve().parent
DB = Path(os.environ.get("APP_DATABASE", "data/app.sqlite"))
ADMIN = os.environ.get("APP_ADMIN_PASSWORD", "")
SECRET = os.environ.get("APP_SESSION_SECRET", "")
SECURE = os.environ.get("APP_COOKIE_SECURE", "true") == "true"
ATTEMPTS = {}
ATTEMPT_LOCK = threading.Lock()
app = FastAPI(title="Private Inventory", docs_url=None, redoc_url=None)


def connection():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=5)
    c.row_factory = sqlite3.Row
    c.executescript(
        "CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY,name TEXT NOT NULL,quantity INTEGER NOT NULL); CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,expires REAL);"
    )
    return c


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=200)


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    quantity: int = Field(ge=0, le=1000000, strict=True)


def origin(req):
    value = req.headers.get("origin")
    if value and value.rstrip("/") != str(req.base_url).rstrip("/"):
        raise HTTPException(403, "origin_rejected")


def settings():
    if len(ADMIN) < 12 or len(SECRET) < 32:
        raise HTTPException(503, "owner_configuration_required")


def csrf(token):
    return hmac.new(SECRET.encode(), token.encode(), hashlib.sha256).hexdigest()


def session(req, write=False):
    settings()
    token = req.cookies.get("hms_session", "")
    if len(token) != 64:
        raise HTTPException(401, "login_required")
    c = connection()
    try:
        r = c.execute(
            "SELECT expires FROM sessions WHERE token_hash=?",
            (hashlib.sha256(token.encode()).hexdigest(),),
        ).fetchone()
    finally:
        c.close()
    if not r or r["expires"] < time.time():
        raise HTTPException(401, "login_required")
    if write:
        origin(req)
        if not hmac.compare_digest(req.headers.get("x-csrf-token", ""), csrf(token)):
            raise HTTPException(403, "csrf_required")
    return token


@app.get("/")
def home():
    return FileResponse(
        BASE / "static/index.html",
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'self'",
        },
    )


@app.get("/app.js")
def js():
    return FileResponse(
        BASE / "static/app.js",
        media_type="text/javascript",
        headers={"X-Content-Type-Options": "nosniff"},
    )


@app.get("/style.css")
def style():
    return FileResponse(BASE / "static/style.css", media_type="text/css")


@app.get("/favicon.svg")
def favicon():
    return FileResponse(BASE / "static/favicon.svg", media_type="image/svg+xml")


@app.get("/healthz")
def health():
    return {"ok": True, "configured": len(ADMIN) >= 12 and len(SECRET) >= 32}


@app.post("/api/login")
def login(v: Login, req: Request, res: Response):
    origin(req)
    settings()
    peer = req.client.host if req.client else "unknown"
    now = time.monotonic()
    with ATTEMPT_LOCK:
        for key in list(ATTEMPTS):
            if ATTEMPTS[key][0] < now - 60:
                ATTEMPTS.pop(key, None)
        started, count = ATTEMPTS.get(peer, (now, 0))
        if count >= 10 or peer not in ATTEMPTS and len(ATTEMPTS) >= 1024:
            raise HTTPException(429, "login_rate_limited")
        ATTEMPTS[peer] = (started, count + 1)
    if not hmac.compare_digest(v.password.encode(), ADMIN.encode()):
        raise HTTPException(401, "invalid_login")
    token = secrets.token_hex(32)
    c = connection()
    try:
        c.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
        c.execute(
            "INSERT INTO sessions VALUES(?,?)",
            (hashlib.sha256(token.encode()).hexdigest(), time.time() + 3600),
        )
        c.commit()
    finally:
        c.close()
    res.set_cookie(
        "hms_session",
        token,
        max_age=3600,
        httponly=True,
        samesite="strict",
        secure=SECURE,
    )
    res.headers["Cache-Control"] = "no-store"
    return {"csrf_token": csrf(token)}


@app.get("/api/session")
def who(req: Request):
    return {"csrf_token": csrf(session(req))}


@app.post("/api/logout")
def logout(req: Request, res: Response):
    token = session(req, True)
    c = connection()
    try:
        c.execute(
            "DELETE FROM sessions WHERE token_hash=?",
            (hashlib.sha256(token.encode()).hexdigest(),),
        )
        c.commit()
    finally:
        c.close()
    res.delete_cookie("hms_session")
    return {"ok": True}


@app.get("/api/items")
def items(req: Request, offset: int = 0):
    session(req)
    if offset < 0 or offset > 100000:
        raise HTTPException(400, "invalid_offset")
    c = connection()
    try:
        return {
            "items": [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM items ORDER BY id DESC LIMIT 50 OFFSET ?", (offset,)
                )
            ],
            "offset": offset,
        }
    finally:
        c.close()


@app.post("/api/items", status_code=201)
def create(v: Item, req: Request):
    session(req, True)
    if not v.name.strip():
        raise HTTPException(422, "name_required")
    c = connection()
    try:
        n = c.execute(
            "INSERT INTO items(name,quantity) VALUES(?,?)", (v.name.strip(), v.quantity)
        )
        c.commit()
        return {"id": n.lastrowid, **v.model_dump()}
    finally:
        c.close()


@app.put("/api/items/{id}")
def update(id: int, v: Item, req: Request):
    session(req, True)
    if not v.name.strip():
        raise HTTPException(422, "name_required")
    c = connection()
    try:
        n = c.execute(
            "UPDATE items SET name=?,quantity=? WHERE id=?",
            (v.name.strip(), v.quantity, id),
        )
        c.commit()
        if n.rowcount != 1:
            raise HTTPException(404, "item_not_found")
        return {"id": id, **v.model_dump()}
    finally:
        c.close()


@app.delete("/api/items/{id}")
def delete(id: int, req: Request):
    session(req, True)
    c = connection()
    try:
        n = c.execute("DELETE FROM items WHERE id=?", (id,))
        c.commit()
        if n.rowcount != 1:
            raise HTTPException(404, "item_not_found")
        return {"ok": True}
    finally:
        c.close()
