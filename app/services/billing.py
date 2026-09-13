from __future__ import annotations

import calendar
from datetime import date
from typing import Optional

import httpx

from app.config import settings


def _headers() -> dict:
    return {
        "access_token": settings.asaas_api_key or "",
        "Content-Type": "application/json",
    }


def add_one_month(d: date) -> date:
    """Same day next month, clamped to the target month's last day (e.g. Jan 31 -> Feb 28)."""
    year, month = (d.year, d.month + 1) if d.month < 12 else (d.year + 1, 1)
    last_day = calendar.monthrange(year, month)[1]
    return d.replace(year=year, month=month, day=min(d.day, last_day))


async def get_or_create_customer(
    user_id: str,
    email: str,
    name: str,
    cpf_cnpj: str,
) -> str:
    """Return the Asaas customer ID, creating one if it doesn't exist yet."""
    async with httpx.AsyncClient() as client:
        # Search by externalReference (our user_id) to avoid duplicates
        r = await client.get(
            f"{settings.asaas_base_url}/customers",
            headers=_headers(),
            params={"externalReference": user_id},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("data"):
            return data["data"][0]["id"]

        # Create new customer
        r = await client.post(
            f"{settings.asaas_base_url}/customers",
            headers=_headers(),
            json={
                "name": name or email,
                "email": email,
                "cpfCnpj": cpf_cnpj,
                "externalReference": user_id,
            },
            timeout=10,
        )
        r.raise_for_status()
        return r.json()["id"]


async def create_subscription(
    customer_id: str,
    user_id: str,
    billing_type: str,
    success_url: str,
    next_due_date: Optional[str] = None,
) -> dict:
    """
    Create a monthly Pro subscription and return the payment URL for the first charge.
    billing_type: 'PIX' | 'CREDIT_CARD'

    `next_due_date` defers the first charge (e.g. a coupon granting a free first
    month) — when set, there's no invoice to pay yet, so the payment-URL lookup
    is skipped and `payment_url` comes back None.
    """
    async with httpx.AsyncClient() as client:
        next_due = next_due_date or date.today().isoformat()
        r = await client.post(
            f"{settings.asaas_base_url}/subscriptions",
            headers=_headers(),
            json={
                "customer": customer_id,
                "billingType": billing_type,
                "value": settings.pro_price,
                "nextDueDate": next_due,
                "cycle": "MONTHLY",
                "description": "Rota Otimizada Pro",
                "externalReference": user_id,
            },
            timeout=10,
        )
        r.raise_for_status()
        sub = r.json()
        sub_id = sub["id"]

        if next_due_date:
            return {"subscription_id": sub_id, "payment_url": None}

        # Fetch the first payment generated for this subscription
        r2 = await client.get(
            f"{settings.asaas_base_url}/payments",
            headers=_headers(),
            params={"subscription": sub_id},
            timeout=10,
        )
        r2.raise_for_status()
        payments = r2.json().get("data", [])

        payment_url: Optional[str] = None
        if payments:
            p = payments[0]
            payment_url = p.get("invoiceUrl") or p.get("bankSlipUrl")

        return {
            "subscription_id": sub_id,
            "payment_url": payment_url,
        }


async def get_active_subscription(customer_id: str) -> Optional[dict]:
    """Return the first ACTIVE subscription for a customer, or None."""
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{settings.asaas_base_url}/subscriptions",
            headers=_headers(),
            params={"customer": customer_id, "status": "ACTIVE"},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json().get("data", [])
        return data[0] if data else None


async def cancel_subscription(subscription_id: str) -> None:
    """Cancel (delete) a subscription. Pending charges are removed by Asaas automatically."""
    async with httpx.AsyncClient() as client:
        r = await client.delete(
            f"{settings.asaas_base_url}/subscriptions/{subscription_id}",
            headers=_headers(),
            timeout=10,
        )
        r.raise_for_status()


_UNPAID_STATUSES = ("PENDING", "AWAITING_RISK_ANALYSIS", "OVERDUE")


def current_period_end(payments: list) -> Optional[str]:
    """
    Return the due date (YYYY-MM-DD) of the earliest unpaid invoice — i.e. the
    last day of already-paid Pro access. Asaas advances the subscription's own
    `nextDueDate` as soon as the next invoice is generated, even before it's
    paid, so that field can't be trusted for "access until" — this can.
    """
    due_dates = [p["dueDate"] for p in payments if p.get("status") in _UNPAID_STATUSES and p.get("dueDate")]
    return min(due_dates) if due_dates else None


async def list_payments(customer_id: str, limit: int = 12) -> list:
    """Return recent payments for a customer, newest first."""
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{settings.asaas_base_url}/payments",
            headers=_headers(),
            params={"customer": customer_id, "limit": limit},
            timeout=10,
        )
        r.raise_for_status()
        payments = r.json().get("data", [])
        return [
            {
                "id":                   p.get("id"),
                "value":                p.get("value"),
                "status":               p.get("status"),
                "billingType":          p.get("billingType"),
                "dueDate":              p.get("dueDate"),
                "paymentDate":          p.get("paymentDate"),
                "invoiceUrl":           p.get("invoiceUrl"),
                "transactionReceiptUrl": p.get("transactionReceiptUrl"),
            }
            for p in payments
        ]
