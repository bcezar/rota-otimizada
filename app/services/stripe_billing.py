from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

import stripe

from app.config import settings


def _api_key() -> str:
    return settings.stripe_secret_key or ""


async def create_checkout_session(
    user_id: str,
    email: str,
    success_url: str,
    cancel_url: str,
) -> dict:
    def _create():
        stripe.api_key = _api_key()
        return stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": settings.stripe_price_id, "quantity": 1}],
            customer_email=email,
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={"user_id": user_id},
            subscription_data={"metadata": {"user_id": user_id}},
        )

    session = await asyncio.to_thread(_create)
    return {"payment_url": session.url, "session_id": session.id}


async def get_active_subscription(customer_id: str) -> Optional[dict]:
    def _fetch():
        stripe.api_key = _api_key()
        subs = stripe.Subscription.list(customer=customer_id, status="active", limit=1)
        if not subs.data:
            return None
        sub = subs.data[0]
        item = sub["items"]["data"][0] if sub["items"]["data"] else None
        amount = (item["price"]["unit_amount"] or 0) / 100 if item else 0
        currency = (item["price"]["currency"] or "usd").upper() if item else "USD"
        next_date = None
        if sub.get("current_period_end"):
            next_date = datetime.fromtimestamp(
                sub["current_period_end"], tz=timezone.utc
            ).strftime("%Y-%m-%d")
        return {
            "id": sub["id"],
            "status": "ACTIVE",
            "value": amount,
            "currency": currency,
            "nextDueDate": next_date,
            "billingType": "STRIPE",
            "provider": "stripe",
        }

    return await asyncio.to_thread(_fetch)


async def cancel_subscription(subscription_id: str) -> str:
    """Cancel at period end. Returns the period-end datetime string."""
    def _cancel():
        stripe.api_key = _api_key()
        sub = stripe.Subscription.modify(subscription_id, cancel_at_period_end=True)
        return datetime.fromtimestamp(
            sub["current_period_end"], tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S")

    return await asyncio.to_thread(_cancel)


async def cancel_subscription_immediately(subscription_id: str) -> None:
    def _cancel():
        stripe.api_key = _api_key()
        stripe.Subscription.cancel(subscription_id, prorate=False)

    await asyncio.to_thread(_cancel)


async def latest_paid_invoice(customer_id: str) -> Optional[dict]:
    def _fetch():
        stripe.api_key = _api_key()
        invoices = stripe.Invoice.list(customer=customer_id, status="paid", limit=1)
        if not invoices.data:
            return None
        invoice = invoices.data[0]
        payment_intent = invoice.get("payment_intent")
        if isinstance(payment_intent, dict):
            payment_intent = payment_intent.get("id")
        return {
            "id": invoice.get("id"),
            "payment_intent": payment_intent,
            "amount": (invoice.get("amount_paid") or 0) / 100,
            "currency": (invoice.get("currency") or "usd").upper(),
            "paid_at": (invoice.get("status_transitions") or {}).get("paid_at"),
            "period_start": invoice.get("period_start"),
            "period_end": invoice.get("period_end"),
        }

    return await asyncio.to_thread(_fetch)


async def refund_payment(payment_intent: str, amount: Optional[float] = None) -> dict:
    def _refund():
        stripe.api_key = _api_key()
        existing = stripe.Refund.list(payment_intent=payment_intent, limit=100)
        wanted = round(amount * 100) if amount is not None else None
        for refund in existing.data:
            metadata = refund.get("metadata") or {}
            if metadata.get("reason") == "account_deletion" and refund.get("status") in ("pending", "succeeded") and (wanted is None or refund.get("amount") == wanted):
                return {"id": refund.get("id"), "status": refund.get("status")}
        params = {"payment_intent": payment_intent}
        if wanted is not None:
            params["amount"] = wanted
        params["metadata"] = {"reason": "account_deletion"}
        refund = stripe.Refund.create(**params)
        return {"id": refund.get("id"), "status": refund.get("status")}

    return await asyncio.to_thread(_refund)


async def list_refunds(payment_intent: str) -> list[dict]:
    def _list():
        stripe.api_key = _api_key()
        refunds = stripe.Refund.list(payment_intent=payment_intent, limit=100)
        return [item.to_dict() for item in refunds.data]

    return await asyncio.to_thread(_list)


def verify_webhook(payload: bytes, sig_header: str) -> Optional[dict]:
    """Return parsed event dict or None on failure."""
    try:
        stripe.api_key = _api_key()
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.stripe_webhook_secret or ""
        )
        return dict(event)
    except (stripe.SignatureVerificationError, ValueError):
        return None
