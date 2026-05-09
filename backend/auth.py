"""
Authentication: password hashing, JWT tokens, VIP/daily-limit checks.
"""
import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import jwt
from dotenv import load_dotenv
from fastapi import Header, HTTPException

from database import get_db

PROJECT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_DIR / ".env")

JWT_SECRET = os.getenv("JWT_SECRET", "fallback-secret-change-me")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 72


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 600000)
    return f"{salt}${dk.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    salt, h = password_hash.split("$", 1)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 600000)
    return secrets.compare_digest(dk.hex(), h)


def create_token(user_id: int, email: str, is_admin: bool = False) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "is_admin": is_admin,
        "iat": now,
        "exp": now + timedelta(hours=JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid authorization header")
    token = authorization[7:]
    payload = decode_token(token)
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (int(payload["sub"]),)).fetchone()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return dict(user)


def get_optional_user(authorization: Optional[str] = Header(None)) -> Optional[dict]:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    try:
        token = authorization[7:]
        payload = decode_token(token)
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE id = ?", (int(payload["sub"]),)).fetchone()
        return dict(user) if user else None
    except HTTPException:
        return None


def check_vip(user_id: int) -> Optional[dict]:
    db = get_db()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    row = db.execute(
        "SELECT * FROM memberships WHERE user_id = ? AND status = 'active' AND end_date >= ?",
        (user_id, now),
    ).fetchone()
    return dict(row) if row else None


def check_daily_limit(user_id: int, limit: int) -> bool:
    """Check if user has remaining daily AI usage. Returns True if allowed, False if exceeded."""
    db = get_db()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = db.execute(
        "SELECT count FROM daily_usage WHERE user_id = ? AND usage_date = ?",
        (user_id, today),
    ).fetchone()

    current = row["count"] if row else 0
    if current >= limit:
        return False

    db.execute(
        "INSERT INTO daily_usage (user_id, usage_date, count) VALUES (?, ?, 1) "
        "ON CONFLICT(user_id, usage_date) DO UPDATE SET count = count + 1",
        (user_id, today),
    )
    db.commit()
    return True
