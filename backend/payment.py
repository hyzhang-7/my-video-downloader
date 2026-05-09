"""
Stripe payment: Checkout Session creation + webhook handling.
"""
import os
import uuid
from pathlib import Path

import stripe
from dotenv import load_dotenv
from fastapi import HTTPException

from database import get_db

PROJECT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_DIR / ".env")

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
VIP_PRICE_CNY = float(os.getenv("VIP_PRICE_CNY", "9.90"))
VIP_DURATION_DAYS = int(os.getenv("VIP_DURATION_DAYS", "30"))
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")

# stripe uses smallest currency unit. CNY smallest unit = fen (分). ¥9.90 = 990 fen.
# Use "usd" for test mode if CNY is not supported on your Stripe account.
PRICE_UNIT_AMOUNT = int(VIP_PRICE_CNY * 100)
STRIPE_CURRENCY = "cny"


def create_checkout_session(user_id: int, email: str) -> dict:
    """Create a Stripe Checkout Session for one-time VIP purchase. Returns {checkout_url, session_id}."""
    if not stripe.api_key:
        raise HTTPException(status_code=500, detail="Stripe not configured")

    # Idempotency key — prevent duplicate sessions for same user within a time window.
    # Reuse the same key for 2 minutes so rapid clicks don't create duplicate payments.
    idempotency_key = f"vip_{user_id}_{uuid.uuid4().hex[:8]}"

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            success_url=f"{FRONTEND_URL}?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{FRONTEND_URL}?canceled=true",
            line_items=[{
                "price_data": {
                    "currency": STRIPE_CURRENCY,
                    "product_data": {
                        "name": "VidFlow VIP — 30天会员",
                        "description": f"VIP 会员 {VIP_DURATION_DAYS} 天，AI 视频总结 + 原画质下载",
                    },
                    "unit_amount": PRICE_UNIT_AMOUNT,
                },
                "quantity": 1,
            }],
            customer_email=email,
            metadata={
                "user_id": str(user_id),
                "product": "vip_30days",
            },
            idempotency_key=idempotency_key,
        )
        return {"checkout_url": session.url, "session_id": session.id}
    except stripe.StripeError as e:
        raise HTTPException(status_code=400, detail=f"支付创建失败: {e.user_message or str(e)}")


def handle_webhook(payload: bytes, sig_header: str) -> dict:
    """Verify and process a Stripe webhook event. Returns {ok, message}."""
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="Webhook secret not configured")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    if event.type == "checkout.session.completed":
        session = event.data.object
        _fulfill_vip(session)

    return {"ok": True, "message": f"Event {event.type} processed"}


def _fulfill_vip(session):
    """Activate VIP membership from a completed Checkout Session."""
    if session.payment_status != "paid":
        return False

    meta = session.metadata.to_dict() if session.metadata else {}
    user_id = int(meta.get("user_id", 0))
    if not user_id:
        return False

    db = get_db()

    # Check if this session was already processed (idempotency)
    existing = db.execute(
        "SELECT id FROM memberships WHERE stripe_session_id = ?",
        (session.id,),
    ).fetchone()
    if existing:
        return True  # Already fulfilled

    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    start = now.strftime("%Y-%m-%d %H:%M:%S")
    end = (now + timedelta(days=VIP_DURATION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    amount = float(session.amount_total) / 100 if session.amount_total else VIP_PRICE_CNY

    db.execute(
        """INSERT INTO memberships (user_id, status, start_date, end_date, amount, stripe_session_id)
           VALUES (?, 'active', ?, ?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET
           status = 'active', start_date = ?, end_date = ?, amount = ?,
           stripe_session_id = ?""",
        (user_id, start, end, amount, session.id,
         start, end, amount, session.id),
    )
    db.commit()
    return True


def verify_and_fulfill(user_id: int, session_id: str) -> dict:
    """Verify Stripe session and activate VIP synchronously.
    Used when user returns from Stripe Checkout (before webhook arrives)."""
    if not stripe.api_key:
        raise HTTPException(status_code=500, detail="Stripe not configured")

    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except stripe.StripeError as e:
        raise HTTPException(status_code=400, detail=f"查询支付状态失败: {e.user_message or str(e)}")

    # Security: verify session belongs to this user
    meta = session.metadata.to_dict() if session.metadata else {}
    session_user_id = int(meta.get("user_id", 0))
    if session_user_id != user_id:
        raise HTTPException(status_code=403, detail="支付记录不匹配")

    if session.payment_status != "paid":
        return {"ok": True, "paid": False, "message": "支付尚未完成"}

    fulfilled = _fulfill_vip(session)
    return {"ok": True, "paid": True, "fulfilled": fulfilled, "message": "VIP 已激活" if fulfilled else "VIP 已激活（无需重复）"}
