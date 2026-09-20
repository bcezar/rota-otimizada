"""One-off marketing email sender.

Usage:
    python -m app.marketing.send --campaign novidades-mapa --dry-run
    python -m app.marketing.send --campaign novidades-mapa --to you@example.com
    python -m app.marketing.send --campaign novidades-mapa --limit 1
    python -m app.marketing.send --campaign novidades-mapa
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from app import storage
from app.config import settings
from app.i18n import get_strings
from app.marketing.campaigns import get_campaign
from app.marketing.render import render_campaign_email, unsubscribe_url, from_email_for

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# users.signup_source is "findmyroute" / "rotaotimizada" / NULL (accounts created
# before that column existed). Fall back to the running script's own locale for NULL.
_SOURCE_TO_LOCALE = {"findmyroute": "en-US", "rotaotimizada": "pt-BR"}


def _locale_for(recipient: dict) -> str:
    return _SOURCE_TO_LOCALE.get(recipient.get("signup_source"), settings.locale)


async def _send_one(recipient: dict, campaign_slug: str) -> bool:
    import resend
    locale = _locale_for(recipient)
    campaign = get_campaign(campaign_slug, locale)
    s = get_strings(locale)
    resend.api_key = settings.resend_api_key
    try:
        resend.Emails.send({
            "from": f"{s['email_from_name']} <{from_email_for(locale)}>",
            "to": [recipient["email"]],
            "subject": campaign["subject"],
            "html": render_campaign_email(campaign, recipient["id"], locale, campaign_slug),
            "headers": {
                "List-Unsubscribe": f"<{unsubscribe_url(recipient['id'], locale)}>",
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            },
        })
        logger.info("sent to %s (locale=%s)", recipient["email"], locale)
        return True
    except Exception as exc:
        logger.error("failed to send to %s: %s", recipient["email"], exc)
        return False


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", required=True, help="Campaign slug (see app/marketing/campaigns.py)")
    parser.add_argument("--dry-run", action="store_true", help="List recipients without sending")
    parser.add_argument("--limit", type=int, default=None, help="Only send to the first N recipients")
    parser.add_argument("--to", default=None, help="Send only to this email address (test send, bypasses the recipient list)")
    parser.add_argument("--locale", default=None, choices=["pt-BR", "en-US"],
                         help="Locale to use for --to (defaults to this environment's own locale)")
    args = parser.parse_args()

    if not get_campaign(args.campaign, "pt-BR") and not get_campaign(args.campaign, "en-US"):
        parser.error(f"unknown campaign: {args.campaign}")

    if not settings.resend_api_key:
        parser.error("RESEND_API_KEY not set — cannot send marketing emails")
    if not settings.marketing_unsubscribe_secret:
        parser.error("MARKETING_UNSUBSCRIBE_SECRET not set — cannot generate unsubscribe links")

    await storage.init_db()
    if args.to:
        source = {"en-US": "findmyroute", "pt-BR": "rotaotimizada"}.get(args.locale)
        recipients = [{"id": "test-send", "email": args.to, "name": None, "signup_source": source}]
    else:
        recipients = await storage.list_marketing_recipients()
        if args.limit is not None:
            recipients = recipients[: args.limit]

    logger.info("campaign=%s recipients=%d dry_run=%s", args.campaign, len(recipients), args.dry_run)
    if args.dry_run:
        for r in recipients:
            logger.info("would send to %s (locale=%s)", r["email"], _locale_for(r))
        return

    sent = 0
    for r in recipients:
        if await _send_one(r, args.campaign):
            sent += 1
        await asyncio.sleep(0.3)
    logger.info("done: %d/%d sent", sent, len(recipients))


if __name__ == "__main__":
    asyncio.run(main())
