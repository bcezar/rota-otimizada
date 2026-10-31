from __future__ import annotations

import hashlib
import html
import logging
import secrets
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app import storage
from app.config import settings
from app.limiter import limiter
from app.services import billing, stripe_billing

logger = logging.getLogger(__name__)
router = APIRouter()


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (ValueError, TypeError):
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return None


def _period_quote(amount: float, currency: str, paid_at: datetime | None,
                  period_end: datetime | None, payment_id: str | None,
                  provider: str | None) -> dict:
    now = datetime.now(timezone.utc)
    if not paid_at or not period_end or period_end <= now or not payment_id or amount <= 0:
        return {"refund_amount": 0.0, "refund_type": "none", "period_end": period_end.date().isoformat() if period_end else None}
    total_seconds = max((period_end - paid_at).total_seconds(), 1)
    remaining = max((period_end - now).total_seconds(), 0)
    within_withdrawal_window = timedelta(0) <= now - paid_at <= timedelta(days=7)
    refund_type = "full" if within_withdrawal_window else "prorated"
    refund_amount = amount if within_withdrawal_window else round(amount * min(remaining / total_seconds, 1), 2)
    if refund_amount <= 0:
        refund_type = "none"
    return {
        "refund_amount": refund_amount,
        "refund_type": refund_type,
        "period_end": period_end.date().isoformat(),
        "provider": provider,
        "payment_id": payment_id,
        "currency": currency,
    }


async def _quote(user: dict) -> dict:
    provider = None
    subscription = None
    payment = None
    if settings.stripe_secret_key and user.get("stripe_customer_id"):
        provider = "stripe"
        try:
            subscription = await stripe_billing.get_active_subscription(user["stripe_customer_id"])
            payment = await stripe_billing.latest_paid_invoice(user["stripe_customer_id"])
        except Exception as exc:
            raise HTTPException(status_code=502, detail="Não foi possível consultar o plano e os pagamentos agora.") from exc
        if payment:
            paid_at = datetime.fromtimestamp(payment["paid_at"], tz=timezone.utc) if payment.get("paid_at") else None
            period_end = datetime.fromtimestamp(payment["period_end"], tz=timezone.utc) if payment.get("period_end") else None
            result = _period_quote(float(payment["amount"]), payment["currency"], paid_at, period_end,
                                   payment.get("payment_intent"), provider)
        else:
            result = _period_quote(0, "USD", None, None, None, provider)
    elif settings.asaas_api_key and user.get("asaas_customer_id"):
        provider = "asaas"
        try:
            subscription = await billing.get_active_subscription(user["asaas_customer_id"])
            payments = await billing.list_payments(user["asaas_customer_id"], limit=100)
        except Exception as exc:
            raise HTTPException(status_code=502, detail="Não foi possível consultar o plano e os pagamentos agora.") from exc
        paid_payments = [p for p in payments if p.get("status") in ("RECEIVED", "CONFIRMED") and p.get("value")]
        paid_payments.sort(key=lambda p: p.get("paymentDate") or p.get("clientPaymentDate") or p.get("dueDate") or "", reverse=True)
        payment = paid_payments[0] if paid_payments else None
        if payment:
            paid_at = _parse_date(payment.get("paymentDate") or payment.get("clientPaymentDate") or payment.get("dueDate"))
            next_invoice = billing.current_period_end(payments)
            period_end = _parse_date(next_invoice)
            if not period_end and paid_at:
                period_end = datetime.combine(billing.add_one_month(paid_at.date()), datetime.max.time(), tzinfo=timezone.utc)
            result = _period_quote(float(payment["value"]), "BRL", paid_at, period_end, payment.get("id"), provider)
        else:
            result = _period_quote(0, "BRL", None, None, None, provider)
    else:
        result = _period_quote(0, "BRL", None, None, None, provider)

    result.update({
        "is_pro": bool(user.get("is_pro")),
        "provider": provider,
        "deletion_enabled": settings.account_deletion_enabled,
        "currency": result.get("currency") or ("USD" if provider == "stripe" else "BRL"),
        "cancellation_required": bool(subscription),
    })
    return result


async def _require_auth(request: Request) -> dict:
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Autenticação necessária.")
    user = await storage.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Sessão inválida ou expirada.")
    return user


def _send_deletion_email(email: str, token: str, quote: dict) -> None:
    if not settings.resend_api_key:
        raise RuntimeError("Envio de e-mail não está configurado.")
    from urllib.parse import urlencode
    link = f"{settings.app_base_url}/api/v1/account/deletion/confirm?{urlencode({'token': token})}"
    import resend
    resend.api_key = settings.resend_api_key
    amount = f"R$ {quote['refund_amount']:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.') if quote['currency'] == 'BRL' else f"{quote['currency']} {quote['refund_amount']:.2f}"
    refund_description = {
        'full': 'integral quando aplicável',
        'prorated': 'proporcional ao período pago restante',
        'none': 'não há reembolso estimado',
    }.get(quote['refund_type'], 'não há reembolso estimado')
    resend.Emails.send({
        "from": f"{settings.brand_name} <{settings.resend_from_email}>",
        "to": [email],
        "subject": "Confirme a exclusão da sua conta",
        "html": f"""
        <div style="font-family:system-ui,sans-serif;max-width:520px;margin:0 auto;padding:2rem;color:#111">
          <h2>Confirme a exclusão da sua conta</h2>
          <p>Recebemos uma solicitação para excluir sua conta do Rota Otimizada.</p>
          <p>Reembolso estimado: <strong>{html.escape(amount)}</strong> ({html.escape(refund_description)}).
          A renovação será cancelada e, quando houver reembolso, o acesso Pro terminará após o processamento.</p>
          <p>O valor final poderá variar conforme o processamento do provedor de pagamento.</p>
          <a href="{html.escape(link)}" style="display:inline-block;background:#dc2626;color:white;border-radius:8px;padding:12px 18px;font-weight:700;text-decoration:none">Revisar e confirmar exclusão</a>
          <p>Se você não fez esta solicitação, ignore este e-mail. O link expira em 30 minutos.</p>
        </div>
        """,
    })


@router.get("/account/deletion/preview")
@limiter.limit("10/minute")
async def deletion_preview(request: Request):
    user = await _require_auth(request)
    return await _quote(user)


@router.post("/account/deletion/request")
@limiter.limit("3/hour")
async def deletion_request(request: Request):
    user = await _require_auth(request)
    if not settings.account_deletion_enabled:
        raise HTTPException(status_code=503, detail="A exclusão de conta está desativada neste ambiente.")
    pending = await storage.get_pending_account_deletion_for_user(user['id'])
    if pending:
        raise HTTPException(status_code=409, detail="Já existe uma solicitação de exclusão em andamento. Confira seu e-mail ou fale com o suporte.")
    quote = await _quote(user)
    raw_token = secrets.token_urlsafe(36)
    now = datetime.now(timezone.utc)
    deletion = {
        "id": secrets.token_urlsafe(18),
        "user_id": user["id"],
        "token_hash": hashlib.sha256(raw_token.encode()).hexdigest(),
        "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "expires_at": (now + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S"),
        "refund_amount": quote["refund_amount"],
        "currency": quote["currency"],
        "refund_type": quote["refund_type"],
        "period_end": quote.get("period_end"),
        "provider": quote.get("provider"),
        "payment_id": quote.get("payment_id"),
    }
    await storage.create_account_deletion_request(deletion)
    try:
        await __import__("asyncio").to_thread(_send_deletion_email, user["email"], raw_token, quote)
    except Exception as exc:
        logger.exception("Could not send account deletion confirmation email")
        await storage.update_account_deletion_request(deletion["id"], "failed")
        raise HTTPException(status_code=502, detail="Não foi possível enviar o e-mail de confirmação. Tente novamente.") from exc
    return {"ok": True}


@router.get("/account/deletion/status")
@limiter.limit("30/minute")
async def deletion_status(request: Request):
    user = await _require_auth(request)
    deletion = await storage.get_pending_account_deletion_for_user(user["id"])
    if not deletion:
        return {"status": "none"}

    status = deletion.get("status")
    if status == "requested":
        return {"status": "awaiting_confirmation"}

    result_status = "refund_processing"
    refresh_failed = False
    payment_id = deletion.get("payment_id")
    provider = deletion.get("provider")
    target_amount = float(deletion.get("refund_amount") or 0)

    if payment_id and provider == "asaas":
        try:
            refunds = await billing.list_payment_refunds(payment_id)
            matching = [
                item for item in refunds
                if item.get("description") == "Reembolso por exclusão de conta"
            ]
            if any(item.get("status") == "AWAITING_CRITICAL_ACTION_AUTHORIZATION" for item in matching):
                result_status = "awaiting_authorization"
            else:
                completed_amount = sum(
                    float(item.get("value") or 0)
                    for item in matching
                    if item.get("status") == "DONE"
                )
                if target_amount > 0 and completed_amount + 0.005 >= target_amount:
                    result_status = "refund_completed"
        except Exception:
            logger.exception("Could not refresh Asaas refund status for deletion request %s", deletion.get("id"))
            refresh_failed = True
    elif payment_id and provider == "stripe":
        try:
            refunds = await stripe_billing.list_refunds(payment_id)
            matching = [
                item for item in refunds
                if (item.get("metadata") or {}).get("reason") == "account_deletion"
            ]
            if any(item.get("status") == "pending" for item in matching):
                result_status = "refund_processing"
            else:
                completed_amount = sum(
                    int(item.get("amount") or 0)
                    for item in matching
                    if item.get("status") == "succeeded"
                )
                if target_amount > 0 and completed_amount >= round(target_amount * 100):
                    result_status = "refund_completed"
        except Exception:
            logger.exception("Could not refresh Stripe refund status for deletion request %s", deletion.get("id"))
            refresh_failed = True

    return {
        "status": result_status,
        "refund_amount": target_amount,
        "currency": deletion.get("currency") or "BRL",
        "refresh_failed": refresh_failed,
    }


def _confirmation_page(title: str, message: str) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html lang="pt-br"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)}</title><body style="font-family:system-ui,sans-serif;background:#f8fafc;color:#0f172a;max-width:560px;margin:10vh auto;padding:24px"><h1>{html.escape(title)}</h1><p>{html.escape(message)}</p></body></html>""")


@router.get("/account/deletion/confirm")
async def deletion_confirmation_page(token: str):
    if not settings.account_deletion_enabled:
        return _confirmation_page("Exclusão indisponível", "A exclusão de conta está desativada neste ambiente.")
    digest = hashlib.sha256(token.encode()).hexdigest()
    deletion = await storage.get_account_deletion_request(digest)
    if not deletion:
        return _confirmation_page("Link expirado", "Solicite um novo link de confirmação no app ou no site.")
    safe_token = html.escape(token)
    amount = float(deletion.get('refund_amount') or 0)
    currency = deletion.get('currency') or 'BRL'
    display_amount = f"R$ {amount:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.') if currency == 'BRL' else f"{currency} {amount:.2f}"
    amount_line = f"Reembolso estimado: {display_amount}." if amount > 0 else "Não há reembolso estimado para esta conta."
    return HTMLResponse(f"""<!doctype html><html lang="pt-br"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Confirmar exclusão</title><body style="font-family:system-ui,sans-serif;background:#f8fafc;color:#0f172a;max-width:560px;margin:10vh auto;padding:24px"><h1>Excluir sua conta?</h1><p>Esta ação removerá seus dados da conta. {html.escape(amount_line)} A renovação será cancelada. Quando houver reembolso, o acesso Pro termina após o processamento.</p><form method="post" action="/api/v1/account/deletion/confirm"><input type="hidden" name="token" value="{safe_token}"><button style="background:#dc2626;color:white;border:0;border-radius:8px;padding:12px 18px;font-weight:700">Confirmar exclusão</button></form></body></html>""")


async def _cancel_subscription(user: dict, provider: str | None) -> None:
    if provider == "stripe" and settings.stripe_secret_key and user.get("stripe_customer_id"):
        sub = await stripe_billing.get_active_subscription(user["stripe_customer_id"])
        if sub:
            await stripe_billing.cancel_subscription_immediately(sub["id"])
    elif provider == "asaas" and settings.asaas_api_key and user.get("asaas_customer_id"):
        sub = await billing.get_active_subscription(user["asaas_customer_id"])
        if sub:
            await billing.cancel_subscription(sub["id"])


async def _refund_and_delete(deletion: dict, user: dict) -> str:
    await _cancel_subscription(user, deletion.get("provider"))
    amount = float(deletion.get("refund_amount") or 0)
    payment_id = deletion.get("payment_id")
    if amount <= 0 or not payment_id:
        await storage.finish_account_deletion(deletion["id"], user["id"])
        return "completed"

    await storage.update_account_deletion_request(deletion["id"], "refund_processing")
    if deletion.get("provider") == "stripe":
        result = await stripe_billing.refund_payment(payment_id, amount if deletion.get("refund_type") != "full" else None)
        if result.get("status") == "succeeded":
            await storage.finish_account_deletion(deletion["id"], user["id"])
            return "completed"
        await storage.update_account_deletion_request(deletion["id"], "awaiting_refund")
        return "awaiting_refund"

    existing = [item for item in await billing.list_payment_refunds(payment_id)
                if item.get("description") == "Reembolso por exclusão de conta"]
    target = round(amount, 2)
    confirmed = sum(float(item.get("value") or 0) for item in existing if item.get("status") == "DONE")
    in_progress = sum(float(item.get("value") or 0) for item in existing if item.get("status") not in ("CANCELLED", "REFUSED", "DENIED"))
    if confirmed >= target:
        await storage.finish_account_deletion(deletion["id"], user["id"])
        return "completed"
    if in_progress >= target:
        await storage.update_account_deletion_request(deletion["id"], "awaiting_refund")
        return "awaiting_refund"
    amount_to_refund = None if deletion.get("refund_type") == "full" and in_progress == 0 else round(target - in_progress, 2)
    result = await billing.refund_payment(payment_id, amount_to_refund)
    if result.get("status") == "DONE":
        await storage.finish_account_deletion(deletion["id"], user["id"])
        return "completed"
    await storage.update_account_deletion_request(deletion["id"], "awaiting_refund")
    return "awaiting_refund"


@router.post("/account/deletion/confirm")
async def confirm_account_deletion(request: Request):
    if not settings.account_deletion_enabled:
        return _confirmation_page("Exclusão indisponível", "A exclusão de conta está desativada neste ambiente.")
    body = await request.body()
    values = parse_qs(body.decode("utf-8"))
    raw_token = values.get("token", [""])[0]
    digest = hashlib.sha256(raw_token.encode()).hexdigest()
    deletion = await storage.get_account_deletion_request(digest)
    if not deletion or not deletion.get("user_id"):
        return _confirmation_page("Link expirado", "Solicite um novo link de confirmação no app ou no site.")
    if deletion.get("status") == "awaiting_refund":
        return _confirmation_page("Reembolso em processamento", "A solicitação foi aceita pelo meio de pagamento. A exclusão será concluída após a confirmação do estorno.")
    user = await storage.get_user_by_id(deletion["user_id"])
    if not user:
        return _confirmation_page("Conta não encontrada", "Não foi possível localizar a conta associada à solicitação.")
    try:
        status = await _refund_and_delete(deletion, user)
    except Exception as exc:
        logger.exception("Account deletion failed for request %s", deletion["id"])
        await storage.update_account_deletion_request(deletion["id"], "failed")
        return _confirmation_page("Não foi possível concluir", "Sua conta continua ativa. A solicitação não foi concluída; tente novamente pelo app ou fale com o suporte.")
    if status == "awaiting_refund":
        return _confirmation_page("Reembolso em processamento", "A solicitação foi aceita pelo meio de pagamento. A exclusão será concluída após a confirmação do estorno.")
    return _confirmation_page("Conta excluída", "A renovação foi cancelada e o reembolso solicitado. Os dados da conta foram removidos.")


async def complete_deletion_for_payment(payment_id: str) -> None:
    deletion = await storage.get_pending_account_deletion_by_payment(payment_id)
    if not deletion or not deletion.get("user_id"):
        return
    if deletion.get('provider') == 'asaas':
        refunds = await billing.list_payment_refunds(payment_id)
        total_done = sum(float(item.get('value') or 0) for item in refunds if item.get('status') == 'DONE')
        if total_done + 0.005 < float(deletion.get('refund_amount') or 0):
            return
    elif deletion.get('provider') == 'stripe':
        refunds = await stripe_billing.list_refunds(payment_id)
        total_done = sum(
            int(item.get('amount') or 0) for item in refunds
            if item.get('status') == 'succeeded'
            and (item.get('metadata') or {}).get('reason') == 'account_deletion'
        )
        if total_done < round(float(deletion.get('refund_amount') or 0) * 100):
            return
    await storage.finish_account_deletion(deletion["id"], deletion["user_id"])
