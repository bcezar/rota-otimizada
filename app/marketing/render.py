from __future__ import annotations

import hashlib
import hmac

from app.config import settings
from app.i18n import get_strings

_BRAND_COLOR = "#1d4ed8"


def unsubscribe_signature(user_id: str) -> str:
    secret = settings.marketing_unsubscribe_secret or ""
    return hmac.new(secret.encode(), user_id.encode(), hashlib.sha256).hexdigest()[:32]


def unsubscribe_url(user_id: str) -> str:
    sig = unsubscribe_signature(user_id)
    return f"{settings.app_base_url}/marketing/unsubscribe?uid={user_id}&sig={sig}"


def _abs_url(path: str) -> str:
    return f"{settings.app_base_url}{path}" if path.startswith("/") else path


def _domain() -> str:
    return settings.app_base_url.removeprefix("https://").removeprefix("http://").rstrip("/")


def render_campaign_email(campaign: dict[str, str], user_id: str) -> str:
    """Renders the full HTML body for a marketing campaign: colored header, white
    content card, CTA and a footer with institutional links + unsubscribe.
    Built with tables/inline styles for email-client compatibility."""
    s = get_strings(settings.locale)
    cta_url = _abs_url(campaign["cta_url"])

    footer_links = [
        (s["marketing_email_footer_contact"], _abs_url(s["marketing_email_footer_contact_href"])),
        (s["marketing_email_footer_privacy"], _abs_url(s["marketing_email_footer_privacy_href"])),
        (s["marketing_email_footer_terms"], _abs_url(s["marketing_email_footer_terms_href"])),
        (s["marketing_email_footer_instagram"], s["marketing_email_footer_instagram_href"]),
    ]
    footer_links_html = " &nbsp;·&nbsp; ".join(
        f'<a href="{href}" style="color:#6b7280;text-decoration:underline">{label}</a>'
        for label, href in footer_links
    )

    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="background:#f3f4f6;padding:2rem 0">
      <tr>
        <td align="center">
          <table role="presentation" width="480" cellpadding="0" cellspacing="0"
                 style="max-width:480px;width:100%;background:#fff;border-radius:16px;overflow:hidden;font-family:system-ui,sans-serif">
            <tr>
              <td style="background:{_BRAND_COLOR};padding:1.75rem 2rem;text-align:center">
                <img src="{settings.app_base_url}/{s['logo']}" width="36"
                     style="border-radius:8px;vertical-align:middle" alt="{s['brand']}" />
                <span style="color:#fff;font-size:1.05rem;font-weight:700;vertical-align:middle;margin-left:.6rem">
                  {_domain()}
                </span>
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
                  <a href="{unsubscribe_url(user_id)}" style="color:#9ca3af;text-decoration:underline">
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
