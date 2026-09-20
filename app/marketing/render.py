from __future__ import annotations

import hashlib
import hmac

from app.config import settings
from app.i18n import get_strings


def unsubscribe_signature(user_id: str) -> str:
    secret = settings.marketing_unsubscribe_secret or ""
    return hmac.new(secret.encode(), user_id.encode(), hashlib.sha256).hexdigest()[:32]


def unsubscribe_url(user_id: str) -> str:
    sig = unsubscribe_signature(user_id)
    return f"{settings.app_base_url}/marketing/unsubscribe?uid={user_id}&sig={sig}"


def render_campaign_email(campaign: dict[str, str], user_id: str) -> str:
    """Renders the full HTML body for a marketing campaign, including the unsubscribe footer."""
    s = get_strings(settings.locale)
    cta_url = campaign["cta_url"]
    if cta_url.startswith("/"):
        cta_url = f"{settings.app_base_url}{cta_url}"
    return f"""
    <div style="font-family:system-ui,sans-serif;max-width:480px;margin:0 auto;padding:2rem">
      <img src="{settings.app_base_url}/{s['logo']}"
           width="40" style="border-radius:10px;margin-bottom:1.5rem" />
      <h2 style="color:#111;margin:0 0 .5rem">{campaign['heading']}</h2>
      <p style="color:#6b7280;margin:0 0 1.5rem">
        {campaign['body_html']}
      </p>
      <a href="{cta_url}"
         style="display:inline-block;background:#1d4ed8;color:#fff;
                padding:.8rem 1.5rem;border-radius:10px;text-decoration:none;
                font-weight:700;font-size:1rem">
        {campaign['cta_label']}
      </a>
      <p style="color:#9ca3af;font-size:.8rem;margin-top:2rem">
        <a href="{unsubscribe_url(user_id)}" style="color:#9ca3af">
          {s['marketing_email_unsubscribe']}
        </a>
      </p>
    </div>
    """
