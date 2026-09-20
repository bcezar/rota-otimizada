from __future__ import annotations

# Each campaign is keyed by a slug (used in --campaign and in /marketing/preview/{slug})
# and provides per-locale subject/heading/body/cta content for the email.
CAMPAIGNS: dict[str, dict[str, dict[str, str]]] = {
    "novidades-mapa": {
        "pt-BR": {
            "subject": "Não sabe o endereço? Agora dá pra apontar no mapa 📍",
            "heading": "Selecione o local direto no mapa",
            "body_html": (
                "Chegou uma forma nova de definir origem, destino ou parada: toque no botão "
                "<strong>Mapa</strong>, arraste até o local certo e confirme — sem precisar "
                "digitar ou lembrar o endereço exato."
                "<br><br>"
                "Funciona em qualquer lugar do app onde você informa um endereço."
            ),
            "cta_label": "Testar no Rota Otimizada",
            "cta_url": "/",
        },
        "en-US": {
            "subject": "Don't know the address? Just point it on the map 📍",
            "heading": "Pick a location right on the map",
            "body_html": (
                "There's a new way to set an origin, destination or stop: tap the "
                "<strong>Map</strong> button, drag to the right spot and confirm — no need "
                "to type or remember the exact address."
                "<br><br>"
                "It works anywhere in the app where you'd normally type an address."
            ),
            "cta_label": "Try it on Find My Route",
            "cta_url": "/",
        },
    },
}


def get_campaign(slug: str, locale: str) -> dict[str, str] | None:
    campaign = CAMPAIGNS.get(slug)
    if not campaign:
        return None
    return campaign.get(locale) or campaign.get("pt-BR")
