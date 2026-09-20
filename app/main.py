import logging
import sys
from contextlib import asynccontextmanager

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(levelname)s:     %(name)s - %(message)s",
)
import hmac
from hashlib import md5
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler

from app import storage
from app.config import settings
from app.i18n import get_strings
from app.limiter import limiter
from app.routers import billing, routes


_STATIC = Path(__file__).parent / "static"
_css_hash = md5(_STATIC.joinpath("style.css").read_bytes()).hexdigest()[:8]
_js_hash  = md5(_STATIC.joinpath("app.js").read_bytes()).hexdigest()[:8]

templates = Jinja2Templates(directory=str(_STATIC))

_i18n = get_strings(settings.locale)
_BASE_URL = settings.app_base_url


@asynccontextmanager
async def lifespan(_: FastAPI):
    await storage.init_db()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://rotaotimizada.com.br", "https://findmyroute.com.br"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)

app.include_router(routes.router, prefix="/api/v1")
app.include_router(billing.router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap():
    base = _BASE_URL
    slugs = list(_SEO_PAGES.keys())
    urls = [f"  <url><loc>{base}/</loc><changefreq>monthly</changefreq><priority>1.0</priority></url>"]
    for slug in slugs:
        urls.append(f"  <url><loc>{base}/{slug}</loc><changefreq>monthly</changefreq><priority>0.8</priority></url>")
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    xml += "\n".join(urls)
    xml += "\n</urlset>"
    return Response(content=xml, media_type="application/xml")


@app.get("/robots.txt", include_in_schema=False)
async def robots():
    content = f"""User-agent: *
Allow: /

Disallow: /api/
Disallow: /r/
Disallow: /s/

Sitemap: {_BASE_URL}/sitemap.xml"""
    return Response(content=content, media_type="text/plain")


@app.get("/s/{code}")
async def expand_saved_route(code: str):
    return RedirectResponse(url=f"/?saved={code}", status_code=302)


@app.get("/r/{code}")
async def expand_route(code: str):
    state = await storage.get_route(code)
    if not state:
        return RedirectResponse(url="/?expired=1", status_code=302)
    parts = []
    if state.get("origin"):      parts.append(("origin", state["origin"]))
    if state.get("destination"): parts.append(("dest",   state["destination"]))
    for a in state.get("addresses", []):
        parts.append(("a", a))
    return RedirectResponse(url=f"/?{urlencode(parts)}", status_code=302)


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request,
        "css_version": _css_hash,
        "js_version": _js_hash,
        "google_maps_key": settings.google_maps_api_key or "",
        "ga_measurement_id": settings.ga_measurement_id or "",
        "base_url": _BASE_URL,
        "i18n": _i18n,
    })


@app.get("/marketing/preview/{slug}", include_in_schema=False)
async def marketing_preview(slug: str, locale: str = settings.locale):
    from app.marketing.campaigns import get_campaign
    from app.marketing.render import render_campaign_email

    campaign = get_campaign(slug, locale)
    if not campaign:
        raise HTTPException(status_code=404)
    html = render_campaign_email(campaign, "preview-user-id", locale)
    return Response(content=html, media_type="text/html")


async def _apply_marketing_unsubscribe(uid: str, sig: str) -> bool:
    from app.marketing.render import unsubscribe_signature

    valid = bool(uid) and bool(sig) and hmac.compare_digest(unsubscribe_signature(uid), sig)
    if valid:
        await storage.set_marketing_opt_out(uid)
    return valid


@app.get("/marketing/unsubscribe", include_in_schema=False)
async def marketing_unsubscribe(request: Request, uid: str, sig: str):
    valid = await _apply_marketing_unsubscribe(uid, sig)
    if valid:
        message_title = _i18n["marketing_unsubscribe_title"]
        message_body = _i18n["marketing_unsubscribe_body"]
    else:
        message_title = _i18n["marketing_unsubscribe_error"]
        message_body = ""
    return templates.TemplateResponse("marketing_unsubscribe.html", {
        "request": request,
        "title": message_title,
        "message_title": message_title,
        "message_body": message_body,
        "i18n": _i18n,
    })


@app.post("/marketing/unsubscribe", include_in_schema=False)
async def marketing_unsubscribe_one_click(uid: str, sig: str):
    """RFC 8058 one-click unsubscribe target: email clients POST here directly
    (via the List-Unsubscribe-Post header) without loading any page."""
    await _apply_marketing_unsubscribe(uid, sig)
    return Response(status_code=200)


@app.get("/conta")
async def conta(request: Request):
    return templates.TemplateResponse("conta.html", {
        "request": request,
        "css_version": _css_hash,
        "ga_measurement_id": settings.ga_measurement_id or "",
        "i18n": _i18n,
    })


_SEO_PAGES_PT: dict[str, dict[str, str]] = {
    "como-funciona": {
        "title": "Como Funciona o Rota Otimizada — Otimização de Rota com TSP",
        "description": "Entenda como o Rota Otimizada calcula a ordem ideal de visitas usando o algoritmo TSP. Economize tempo e combustível nas suas entregas.",
        "template": "como-funciona.html",
    },
    "importar-enderecos-csv": {
        "title": "Importar Endereços via CSV — Rota Otimizada",
        "description": "Aprenda a importar uma lista de endereços por arquivo CSV ou Excel no Rota Otimizada. Roteirize dezenas de paradas em segundos.",
        "template": "importar-enderecos-csv.html",
    },
    "otimizar-rota-entregas": {
        "title": "Otimizar Rota de Entregas Gratuitamente — Rota Otimizada",
        "description": "Calcule a melhor ordem de entregas com múltiplos endereços. Reduza km rodados, economize combustível e faça mais entregas por dia.",
        "template": "otimizar-rota-entregas.html",
    },
    "roteirizador-gratuito": {
        "title": "Roteirizador Gratuito com Múltiplos Endereços — Rota Otimizada",
        "description": "O melhor roteirizador do Brasil. Grátis até 5 paradas, até 50 no plano Pro. Sem cadastro, sem complicação. Abra direto no Maps.",
        "template": "roteirizador-gratuito.html",
    },
    "google-maps-multiplos-enderecos": {
        "title": "Google Maps com Múltiplos Endereços Otimizados — Rota Otimizada",
        "description": "Supere o limite do Google Maps e otimize a ordem de visitas. O Rota Otimizada calcula a rota ideal e abre automaticamente no Google Maps.",
        "template": "google-maps-multiplos-enderecos.html",
    },
    "waze-multiplos-destinos": {
        "title": "Waze com Múltiplos Destinos Otimizados — Rota Otimizada",
        "description": "Use o Waze com vários destinos em ordem otimizada. O Rota Otimizada organiza suas paradas e abre cada uma diretamente no Waze.",
        "template": "waze-multiplos-destinos.html",
    },
    "perguntas-frequentes": {
        "title": "Perguntas Frequentes — Rota Otimizada",
        "description": "Tire suas dúvidas sobre o Rota Otimizada: como otimizar rotas, importar endereços, usar com Google Maps e Waze, e muito mais.",
        "template": "perguntas-frequentes.html",
    },
    "termos-de-uso": {
        "title": "Termos de Uso — Rota Otimizada",
        "description": "Termos de uso do Rota Otimizada: planos, cobrança, cancelamento e responsabilidades.",
        "template": "termos-de-uso.html",
    },
    "privacidade": {
        "title": "Política de Privacidade — Rota Otimizada",
        "description": "Como o Rota Otimizada coleta, usa e protege seus dados pessoais.",
        "template": "privacidade.html",
    },
}

_SEO_PAGES_EN: dict[str, dict[str, str]] = {
    "how-it-works": {
        "title": "How Find My Route Optimizes Your Route — TSP Route Optimization",
        "description": "Learn how Find My Route calculates the ideal visit order using the TSP algorithm. Save time and fuel on your deliveries.",
        "template": "how-it-works.html",
    },
    "import-addresses-csv": {
        "title": "Import Addresses via CSV — Find My Route",
        "description": "Learn how to import a list of addresses from a CSV or Excel file into Find My Route. Plan dozens of stops in seconds.",
        "template": "import-addresses-csv.html",
    },
    "optimize-delivery-route": {
        "title": "Optimize Your Delivery Route for Free — Find My Route",
        "description": "Calculate the best order for deliveries with multiple addresses. Reduce mileage, save fuel, and complete more deliveries per day.",
        "template": "optimize-delivery-route.html",
    },
    "free-route-planner": {
        "title": "Free Route Planner with Multiple Addresses — Find My Route",
        "description": "The best free route planner. Free up to 5 stops, up to 50 on the Pro plan. No sign-up, no hassle. Open directly in Maps.",
        "template": "free-route-planner.html",
    },
    "google-maps-multiple-addresses": {
        "title": "Google Maps with Multiple Optimized Addresses — Find My Route",
        "description": "Overcome the Google Maps limit and optimize your visit order. Find My Route calculates the ideal route and opens it automatically in Google Maps.",
        "template": "google-maps-multiple-addresses.html",
    },
    "waze-multiple-destinations": {
        "title": "Waze with Multiple Optimized Destinations — Find My Route",
        "description": "Use Waze with multiple destinations in optimized order. Find My Route organizes your stops and opens each one directly in Waze.",
        "template": "waze-multiple-destinations.html",
    },
    "faq": {
        "title": "Frequently Asked Questions — Find My Route",
        "description": "Get answers about Find My Route: how to optimize routes, import addresses, use with Google Maps and Waze, and much more.",
        "template": "faq.html",
    },
    "terms-of-service": {
        "title": "Terms of Service — Find My Route",
        "description": "Find My Route's terms of service: plans, billing, cancellation, and responsibilities.",
        "template": "terms-of-service.html",
    },
    "privacy-policy": {
        "title": "Privacy Policy — Find My Route",
        "description": "How Find My Route collects, uses, and protects your personal data.",
        "template": "privacy-policy.html",
    },
}

_SEO_PAGES = _SEO_PAGES_EN if settings.locale == "en-US" else _SEO_PAGES_PT

@app.get("/{slug}")
async def seo_page(request: Request, slug: str):
    # Serve known static files that need to bypass the SEO handler
    if "." in slug:
        static_file = _STATIC / slug
        if static_file.exists() and static_file.is_file():
            return FileResponse(str(static_file))
        raise HTTPException(status_code=404)
    page = _SEO_PAGES.get(slug)
    if not page:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(page["template"], {
        "request": request,
        "css_version": _css_hash,
        "title": page["title"],
        "description": page["description"],
        "canonical": f"{_BASE_URL}/{slug}",
        "base_url": _BASE_URL,
        "ga_measurement_id": settings.ga_measurement_id or "",
        "i18n": _i18n,
    })


# Serve static assets (CSS, images, etc.) — must come after all routes
app.mount("/", StaticFiles(directory="app/static", html=True), name="static")
