from __future__ import annotations

import hashlib
import hmac
from urllib.parse import urlencode

from app.config import settings
from app.i18n import get_strings

_BRAND_COLOR = "#1d4ed8"

# Both PT and EN deploys share the same Turso database, but each runs from its own
# .env (its own APP_BASE_URL/RESEND_FROM_EMAIL). A marketing send always runs from
# a single environment, yet recipients span both locales (see users.signup_source),
# so we need the base URL/from-email for a locale that may not match the running
# deploy's own. These are public brand domains/addresses, not secrets, and only
# change if a domain is retired — a single edit here beats keeping 4 env vars
# in sync across both Railway services.
_LOCALE_BASE_URLS = {
    "pt-BR": "https://rotaotimizada.com.br",
    "en-US": "https://findmyroute.com.br",
}
_LOCALE_FROM_EMAILS = {
    "pt-BR": "noreply@rotaotimizada.com.br",
    "en-US": "noreply@findmyroute.com.br",
}


def _base_url_for(locale: str) -> str:
    return _LOCALE_BASE_URLS.get(locale, settings.app_base_url)


def from_email_for(locale: str) -> str:
    return _LOCALE_FROM_EMAILS.get(locale, settings.resend_from_email)


def unsubscribe_signature(user_id: str) -> str:
    secret = settings.marketing_unsubscribe_secret or ""
    return hmac.new(secret.encode(), user_id.encode(), hashlib.sha256).hexdigest()[:32]


def unsubscribe_url(user_id: str, locale: str) -> str:
    sig = unsubscribe_signature(user_id)
    return f"{_base_url_for(locale)}/marketing/unsubscribe?uid={user_id}&sig={sig}"


def _abs_url(path: str, base_url: str) -> str:
    return f"{base_url}{path}" if path.startswith("/") else path


def _domain(base_url: str) -> str:
    return base_url.removeprefix("https://").removeprefix("http://").rstrip("/")


def _with_utm(url: str, campaign_slug: str) -> str:
    """Tags the CTA link so GA4 attributes the visit to this campaign (session-level
    source/medium/campaign dimensions) — app.js also fires an explicit event for it."""
    utm = urlencode({"utm_source": "email", "utm_medium": "marketing", "utm_campaign": campaign_slug})
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{utm}"


def render_campaign_email(campaign: dict[str, str], user_id: str, locale: str, campaign_slug: str) -> str:
    """Renders the full HTML body for a marketing campaign: colored header, white
    content card, CTA and a footer with institutional links + unsubscribe.
    Built with tables/inline styles for email-client compatibility.
    `locale` picks the recipient's own language/domain, independent of the
    environment the sending script happens to run in."""
    s = get_strings(locale)
    base_url = _base_url_for(locale)
    cta_url = _with_utm(_abs_url(campaign["cta_url"], base_url), campaign_slug)

    footer_links = [
        (s["marketing_email_footer_contact"], _abs_url(s["marketing_email_footer_contact_href"], base_url)),
        (s["marketing_email_footer_privacy"], _abs_url(s["marketing_email_footer_privacy_href"], base_url)),
        (s["marketing_email_footer_terms"], _abs_url(s["marketing_email_footer_terms_href"], base_url)),
        (s["marketing_email_footer_instagram"], s["marketing_email_footer_instagram_href"]),
    ]
    footer_links_html = " &nbsp;·&nbsp; ".join(
        f'<a href="{href}" style="color:#6b7280;text-decoration:underline">{label}</a>'
        for label, href in footer_links
    )

    return f"""
    <style>
      .header-domain-link, .header-domain-link:visited, .header-domain-link:hover {{
        color: #ffffff !important;
      }}
    </style>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="background:#f3f4f6;padding:2rem 0">
      <tr>
        <td align="center">
          <table role="presentation" width="480" cellpadding="0" cellspacing="0"
                 style="max-width:480px;width:100%;background:#fff;border-radius:16px;overflow:hidden;font-family:system-ui,sans-serif">
            <tr>
              <td style="background:{_BRAND_COLOR};padding:1.75rem 2rem;text-align:center">
                <img src="{base_url}/{s['logo']}" width="36"
                     style="border-radius:8px;vertical-align:middle" alt="{s['brand']}" />
                <a href="{base_url}/" class="header-domain-link"
                   style="color:#ffffff !important;font-size:1.05rem;font-weight:700;
                          vertical-align:middle;margin-left:.6rem;text-decoration:none">
                  {_domain(base_url)}
                </a>
              </td>
            </tr>
            <tr>
              <td style="padding:2rem">
                <span style="display:inline-block;background:#eff6ff;color:{_BRAND_COLOR};
                             font-size:.72rem;font-weight:700;letter-spacing:.03em;
                             padding:.3rem .7rem;border-radius:99px;margin-bottom:1rem">
                  {s['marketing_email_badge']}
                </span>
                <h2 style="color:#111;margin:0 0 .75rem;font-size:1.3rem">{campaign['heading']}</h2>
                <p style="color:#6b7280;margin:0 0 1.5rem;font-size:.95rem;line-height:1.6">
                  {campaign['body_html']}
                </p>
                <a href="{cta_url}"
                   style="display:inline-block;background:{_BRAND_COLOR};color:#fff;
                          padding:.8rem 1.5rem;border-radius:10px;text-decoration:none;
                          font-weight:700;font-size:1rem">
                  {campaign['cta_label']}
                </a>
              </td>
            </tr>
            <tr>
              <td style="background:#f9fafb;padding:1.5rem 2rem;border-top:1px solid #e5e7eb">
                <p style="color:#9ca3af;font-size:.78rem;margin:0 0 .75rem">
                  {s['marketing_email_footer_note']}
                </p>
                <p style="font-size:.78rem;margin:0 0 .75rem">
                  {footer_links_html}
                </p>
                <p style="font-size:.78rem;margin:0">
                  <a href="{unsubscribe_url(user_id, locale)}" style="color:#9ca3af;text-decoration:underline">
                    {s['marketing_email_unsubscribe']}
                  </a>
                </p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
    """
