import logging
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel

from app import storage
from app.config import settings
from app.i18n import get_strings
from app.limiter import limiter
from app.services import billing
from app.services import stripe_billing

logger = logging.getLogger(__name__)

router = APIRouter()


async def _send_welcome_email(to_email: str, tier: str) -> None:
    """tier: 'pro' or 'exclusive'. Fire-and-forget — never blocks the caller on failure."""
    if not settings.resend_api_key:
        logger.warning("RESEND_API_KEY not set — welcome email skipped for %s", to_email)
        return
    s = get_strings(settings.locale)
    try:
        import resend
        resend.api_key = settings.resend_api_key
        resend.Emails.send({
            "from": f"{s['email_from_name']} <{settings.resend_from_email}>",
            "to": [to_email],
            "subject": s[f"welcome_email_subject_{tier}"],
            "html": f"""
            <div style="font-family:system-ui,sans-serif;max-width:480px;margin:0 auto;padding:2rem">
              <img src="{settings.app_base_url}/{s['logo']}"
                   width="40" style="border-radius:10px;margin-bottom:1.5rem" />
              <h2 style="color:#111;margin:0 0 .5rem">{s[f'welcome_email_heading_{tier}']}</h2>
              <p style="color:#6b7280;margin:0 0 1.5rem">
                {s[f'welcome_email_body_{tier}']}
              </p>
              <a href="{settings.app_base_url}/"
                 style="display:inline-block;background:#1d4ed8;color:#fff;
                        padding:.8rem 1.5rem;border-radius:10px;text-decoration:none;
                        font-weight:700;font-size:1rem">
                {s['welcome_email_cta']}
              </a>
              <p style="color:#9ca3af;font-size:.8rem;margin-top:2rem">
                {s['welcome_email_footer']}
              </p>
            </div>
            """,
        })
        logger.info("welcome email (%s) sent to %s", tier, to_email)
    except Exception as exc:
        logger.error("failed to send welcome email to %s: %s", to_email, exc)


class CheckoutRequest(BaseModel):
    cpf_cnpj: str
    billing_type: Literal["PIX", "CREDIT_CARD"] = "PIX"
    coupon_code: Optional[str] = None


def _check_digit(base: str, weights: "list[int] | range") -> str:
    total = sum(int(d) * w for d, w in zip(base, weights))
    remainder = total % 11
    return "0" if remainder < 2 else str(11 - remainder)


def _is_valid_cpf(cpf: str) -> bool:
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    d1 = _check_digit(cpf[:9], range(10, 1, -1))
    d2 = _check_digit(cpf[:9] + d1, range(11, 1, -1))
    return cpf[-2:] == d1 + d2


def _is_valid_cnpj(cnpj: str) -> bool:
    if len(cnpj) != 14 or cnpj == cnpj[0] * 14:
        return False
    d1 = _check_digit(cnpj[:12], [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    d2 = _check_digit(cnpj[:12] + d1, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return cnpj[-2:] == d1 + d2


def _is_valid_cpf_cnpj(digits: str) -> bool:
    if len(digits) == 11:
        return _is_valid_cpf(digits)
    if len(digits) == 14:
        return _is_valid_cnpj(digits)
    return False


async def _require_auth(request: Request) -> dict:
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Autenticação necessária.")
    user = await storage.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Sessão inválida ou expirada.")
    return user


@router.post("/billing/checkout")
@limiter.limit("5/minute")
async def checkout(request: Request, body: CheckoutRequest = Body(...)):
    if not settings.asaas_api_key:
        raise HTTPException(status_code=501, detail="Pagamentos não configurados.")

    user = await _require_auth(request)

    if user.get("is_pro"):
        raise HTTPException(status_code=400, detail="Você já possui o Plano Pro.")

    cpf_cnpj = body.cpf_cnpj.replace(".", "").replace("-", "").replace("/", "").strip()
    if not _is_valid_cpf_cnpj(cpf_cnpj):
        raise HTTPException(status_code=422, detail="CPF ou CNPJ inválido.")

    coupon_code = (body.coupon_code or "").strip().upper()
    next_due_date: Optional[str] = None
    if coupon_code:
        coupon = await storage.get_coupon(coupon_code)
        expired = (
            not coupon
            or not coupon["active"]
            or datetime.now(timezone.utc)
            > datetime.strptime(coupon["expires_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        )
        if expired:
            raise HTTPException(status_code=400, detail="Cupom inválido ou expirado.")
        if await storage.count_coupon_redemptions(coupon_code) >= coupon["max_redemptions"]:
            raise HTTPException(status_code=400, detail="Cupom esgotado.")
        if await storage.cpf_has_redeemed_coupon(cpf_cnpj):
            raise HTTPException(status_code=400, detail="Este CPF/CNPJ já usou um cupom antes.")
        next_due_date = billing.add_one_month(date.today()).isoformat()

    try:
        customer_id = await billing.get_or_create_customer(
            user_id=user["id"],
            email=user["email"],
            name=user.get("name") or user["email"],
            cpf_cnpj=cpf_cnpj,
        )
        await storage.set_asaas_customer_id(user["id"], customer_id)

        success_url = f"{settings.app_base_url}/?upgraded=1"
        result = await billing.create_subscription(
            customer_id=customer_id,
            user_id=user["id"],
            billing_type=body.billing_type,
            success_url=success_url,
            next_due_date=next_due_date,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Erro ao criar cobrança: {exc}") from exc

    if coupon_code:
        await storage.set_user_pro(user["id"], True)
        await storage.set_pro_expires_at(user["id"], None)
        is_exclusive = coupon.get("grants_stop_limit") is not None
        if is_exclusive:
            await storage.set_exclusive_until(user["id"], f"{next_due_date} 23:59:59")
        await storage.record_coupon_redemption(coupon_code, user["id"], cpf_cnpj)
        await _send_welcome_email(user["email"], "exclusive" if is_exclusive else "pro")
        return {"ok": True, "coupon_applied": True}

    if not result.get("payment_url"):
        raise HTTPException(status_code=502, detail="Não foi possível obter o link de pagamento.")

    return {"payment_url": result["payment_url"]}


@router.get("/billing/account")
@limiter.limit("30/minute")
async def account(request: Request):
    user = await _require_auth(request)
    customer_id = user.get("asaas_customer_id")

    subscription = None
    payments = []

    if settings.asaas_api_key and customer_id:
        try:
            subscription = await billing.get_active_subscription(customer_id)
        except Exception:
            pass
        try:
            payments = await billing.list_payments(customer_id)
        except Exception:
            pass
        if subscription is not None:
            # Asaas advances the subscription's nextDueDate as soon as the next
            # invoice is generated, ahead of it being paid — use the earliest
            # unpaid invoice's due date instead, which reflects reality.
            period_end = billing.current_period_end(payments)
            if period_end:
                subscription["nextDueDate"] = period_end

    if subscription is None and settings.stripe_secret_key and user.get("stripe_customer_id"):
        try:
            subscription = await stripe_billing.get_active_subscription(user["stripe_customer_id"])
        except Exception:
            pass

    return {
        "user": {
            "id":               user["id"],
            "email":            user["email"],
            "name":             user.get("name"),
            "is_pro":           user.get("is_pro", False),
            "is_exclusive":     user.get("is_exclusive", False),
            "exclusive_until":  user.get("exclusive_until"),
        },
        "subscription": subscription,
        "payments":     payments,
    }


@router.delete("/billing/subscription")
@limiter.limit("5/minute")
async def cancel_subscription(request: Request):
    user = await _require_auth(request)

    # Stripe cancellation (EN deploy)
    stripe_customer_id = user.get("stripe_customer_id")
    if settings.stripe_secret_key and stripe_customer_id:
        try:
            sub = await stripe_billing.get_active_subscription(stripe_customer_id)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Error fetching subscription: {exc}") from exc

        if not sub:
            raise HTTPException(status_code=404, detail="No active subscription found.")

        try:
            expires_at = await stripe_billing.cancel_subscription(sub["id"])
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Error cancelling subscription: {exc}") from exc

        await storage.set_pro_expires_at(user["id"], expires_at)
        return {"ok": True, "pro_expires_at": expires_at[:10]}

    # Asaas cancellation (PT deploy)
    if not settings.asaas_api_key:
        raise HTTPException(status_code=501, detail="Pagamentos não configurados.")

    customer_id = user.get("asaas_customer_id")
    if not customer_id:
        raise HTTPException(status_code=404, detail="Nenhuma assinatura encontrada.")

    try:
        sub = await billing.get_active_subscription(customer_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Erro ao buscar assinatura: {exc}") from exc

    if not sub:
        raise HTTPException(status_code=404, detail="Nenhuma assinatura ativa encontrada.")

    # Fetch payments before cancelling — Asaas removes pending invoices as part
    # of the cancellation, so this is the last chance to read the current
    # (already-paid) cycle's end date.
    try:
        payments = await billing.list_payments(customer_id)
    except Exception:
        payments = []
    period_end = billing.current_period_end(payments)

    try:
        await billing.cancel_subscription(sub["id"])
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Erro ao cancelar assinatura: {exc}") from exc

    if period_end:
        expires_at = f"{period_end} 23:59:59"
    else:
        expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    await storage.set_pro_expires_at(user["id"], expires_at)

    return {"ok": True, "pro_expires_at": expires_at[:10]}


@router.post("/billing/stripe/checkout")
@limiter.limit("5/minute")
async def stripe_checkout(request: Request):
    if not settings.stripe_secret_key or not settings.stripe_price_id:
        raise HTTPException(status_code=501, detail="Stripe payments not configured.")

    user = await _require_auth(request)

    if user.get("is_pro"):
        raise HTTPException(status_code=400, detail="You are already on the Pro plan.")

    success_url = f"{settings.app_base_url}/?upgraded=1"
    cancel_url = f"{settings.app_base_url}/"

    try:
        result = await stripe_billing.create_checkout_session(
            user_id=user["id"],
            email=user["email"],
            success_url=success_url,
            cancel_url=cancel_url,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error creating checkout: {exc}") from exc

    if not result.get("payment_url"):
        raise HTTPException(status_code=502, detail="Could not obtain payment link.")

    return {"payment_url": result["payment_url"]}


@router.post("/billing/stripe/webhook")
async def stripe_webhook(request: Request):
    sig_header = request.headers.get("stripe-signature", "")
    payload = await request.body()

    event = stripe_billing.verify_webhook(payload, sig_header)
    if event is None:
        raise HTTPException(status_code=400, detail="Invalid Stripe webhook signature.")

    event_type = event.get("type", "")
    data_obj = event.get("data", {}).get("object", {})

    if event_type == "checkout.session.completed":
        user_id = (data_obj.get("metadata") or {}).get("user_id")
        stripe_customer = data_obj.get("customer")
        if user_id:
            await storage.set_user_pro(user_id, True)
            await storage.set_pro_expires_at(user_id, None)
            if stripe_customer:
                await storage.set_stripe_customer_id(user_id, stripe_customer)
            user = await storage.get_user_by_stripe_customer(stripe_customer) if stripe_customer else None
            if user:
                await _send_welcome_email(user["email"], "pro")

    elif event_type == "customer.subscription.deleted":
        stripe_customer = data_obj.get("customer")
        if stripe_customer:
            user = await storage.get_user_by_stripe_customer(stripe_customer)
            if user:
                expires_at = (
                    datetime.now(timezone.utc) + timedelta(days=30)
                ).strftime("%Y-%m-%d %H:%M:%S")
                await storage.set_pro_expires_at(user["id"], expires_at)

    return {"ok": True}


@router.post("/billing/webhook")
async def billing_webhook(request: Request):
    # Verify origin via header token — fail closed: an unconfigured token must never
    # be treated as "skip verification", or the endpoint becomes unauthenticated.
    if not settings.asaas_webhook_token:
        raise HTTPException(status_code=501, detail="Webhook do Asaas não configurado.")
    token = request.headers.get("asaas-access-token", "")
    if token != settings.asaas_webhook_token:
        raise HTTPException(status_code=403, detail="Webhook token inválido.")

    payload = await request.json()
    event = payload.get("event", "")
    # Payment events carry the payload under "payment"; subscription events under "subscription"
    entity = payload.get("subscription", {}) if event.startswith("SUBSCRIPTION_") else payload.get("payment", {})

    # Identify user: prefer externalReference, fall back to the Asaas customer id
    user_id: Optional[str] = entity.get("externalReference")
    if not user_id:
        customer_id = entity.get("customer")
        if customer_id:
            user = await storage.get_user_by_asaas_customer(customer_id)
            user_id = user["id"] if user else None

    if not user_id:
        # Unknown user — acknowledge without error so Asaas doesn't retry
        return {"ok": True}

    if event in ("PAYMENT_RECEIVED", "PAYMENT_CONFIRMED"):
        customer_id = entity.get("customer")
        was_pro = False
        user_for_email = None
        if customer_id:
            user_for_email = await storage.get_user_by_asaas_customer(customer_id)
            was_pro = bool(user_for_email and user_for_email.get("is_pro"))
        await storage.set_user_pro(user_id, True)
        await storage.set_pro_expires_at(user_id, None)  # renewed — no expiry
        if not was_pro and user_for_email:
            await _send_welcome_email(user_for_email["email"], "pro")
    elif event == "PAYMENT_OVERDUE":
        # grace period before downgrading — gives the user time to pay the PIX invoice
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=3)
        ).strftime("%Y-%m-%d %H:%M:%S")
        await storage.set_pro_expires_at(user_id, expires_at)
    elif event in ("PAYMENT_REFUNDED", "PAYMENT_CHARGEBACK_REQUESTED", "PAYMENT_CHARGEBACK_DISPUTE"):
        await storage.set_user_pro(user_id, False)
        await storage.set_pro_expires_at(user_id, None)
    elif event == "PAYMENT_RECEIVED_IN_CASH_UNDONE":
        # a manual "received in cash" confirmation was reversed — the payment is no
        # longer valid, so treat it the same as an overdue invoice (grace period).
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=3)
        ).strftime("%Y-%m-%d %H:%M:%S")
        await storage.set_pro_expires_at(user_id, expires_at)
    elif event in ("SUBSCRIPTION_DELETED", "SUBSCRIPTION_INACTIVATED"):
        # covers cancellation done outside our own /billing/subscription endpoint
        # (e.g. directly in the Asaas dashboard) — same grace period we already grant
        # when the user cancels through the app.
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=30)
        ).strftime("%Y-%m-%d %H:%M:%S")
        await storage.set_pro_expires_at(user_id, expires_at)

    return {"ok": True}
