from __future__ import annotations

# Each campaign is keyed by a slug (used in --campaign and in /marketing/preview/{slug})
# and provides per-locale subject/heading/body/cta content for the email.
CAMPAIGNS: dict[str, dict[str, dict[str, str]]] = {
    "novidades-mapa": {
        "pt-BR": {
            "subject": "Novidade: escolha o local direto no mapa 📍",
            "heading": "Selecione locais arrastando o mapa",
            "body_html": (
                "Agora você pode definir origem, destino ou paradas sem digitar o endereço: "
                "toque no botão <strong>Mapa</strong>, arraste até o local exato e confirme. "
                "Ideal para quando você sabe onde fica, mas não sabe o endereço de cor."
            ),
            "cta_label": "Testar agora",
            "cta_url": "/",
        },
        "en-US": {
            "subject": "New: pick a location right on the map 📍",
            "heading": "Select locations by dragging the map",
            "body_html": (
                "You can now set an origin, destination or stop without typing the address: "
                "tap the <strong>Map</strong> button, drag to the exact spot and confirm. "
                "Perfect for when you know the place but not the address."
            ),
            "cta_label": "Try it now",
            "cta_url": "/",
        },
    },
}


def get_campaign(slug: str, locale: str) -> dict[str, str] | None:
    campaign = CAMPAIGNS.get(slug)
    if not campaign:
        return None
    return campaign.get(locale) or campaign.get("pt-BR")
