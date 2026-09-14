import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)
from fastapi import APIRouter, Body, HTTPException, Query as QueryParam, Request
from fastapi.responses import RedirectResponse, Response

from app import storage
from app.config import settings
from app.i18n import get_strings
from app.limiter import limiter
from app.models import (
    ContactRequest, Coordinates, FeedbackRequest, LoginRequest, LoginResponse, MagicRequestBody,
    MagicRequestResponse, MapImageRequest, OriginInfo, PolylineRequest, RouteRequest, RouteResponse,
    RouteStop, SaveRouteRequest, UserResponse,
)
from app.services import directions, distance, geocoding, optimizer, static_maps

router = APIRouter()


def _user_response(user: dict) -> dict:
    return {
        "id":             user["id"],
        "email":          user["email"],
        "is_pro":         user.get("is_pro", False),
        "email_verified": user.get("email_verified", False),
        "name":           user.get("name"),
        "picture_url":    user.get("picture_url"),
        "is_exclusive":     user.get("is_exclusive", False),
        "exclusive_until":  user.get("exclusive_until"),
    }


async def _require_auth(request: Request) -> dict:
    s = get_strings(settings.locale)
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail=s["err_auth_required"])
    user = await storage.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail=s["err_session_invalid"])
    return user


# ── Auth: e-mail simples (mantido para retrocompatibilidade) ─────────────────

@router.post("/auth/login", response_model=LoginResponse)
@limiter.limit("10/minute")
async def login(request: Request, body: LoginRequest = Body(...)):
    user = await storage.find_or_create_user(body.email.lower().strip())
    token = await storage.create_session(user["id"])
    return {"token": token, "user": _user_response(user)}


@router.post("/auth/logout")
async def logout(request: Request):
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if token:
        await storage.delete_session(token)
    return {"ok": True}


@router.get("/auth/me", response_model=UserResponse)
async def me(request: Request):
    user = await _require_auth(request)
    return _user_response(user)


# ── Auth: Magic Link ──────────────────────────────────────────────────────────

async def _send_magic_email(to_email: str, magic_token: str) -> None:
    if not settings.resend_api_key:
        logger.warning("RESEND_API_KEY not set — magic link email skipped for %s", to_email)
        return
    s = get_strings(settings.locale)
    link = f"{settings.app_base_url}/api/v1/auth/verify?token={magic_token}"
    try:
        import resend
        resend.api_key = settings.resend_api_key
        resend.Emails.send({
        "from": f"{s['email_from_name']} <{settings.resend_from_email}>",
        "to": [to_email],
        "subject": s["email_subject"],
        "html": f"""
        <div style="font-family:system-ui,sans-serif;max-width:480px;margin:0 auto;padding:2rem">
          <img src="{settings.app_base_url}/{s['logo']}"
               width="40" style="border-radius:10px;margin-bottom:1.5rem" />
          <h2 style="color:#111;margin:0 0 .5rem">{s['email_heading']}</h2>
          <p style="color:#6b7280;margin:0 0 1.5rem">
            {s['email_body']}
          </p>
          <a href="{link}"
             style="display:inline-block;background:#1d4ed8;color:#fff;
                    padding:.8rem 1.5rem;border-radius:10px;text-decoration:none;
                    font-weight:700;font-size:1rem">
            {s['email_cta']}
          </a>
          <p style="color:#9ca3af;font-size:.8rem;margin-top:2rem">
            {s['email_footer']}
          </p>
        </div>
        """,
        })
        logger.info("magic link email sent to %s", to_email)
    except Exception as exc:
        logger.error("failed to send magic link email to %s: %s", to_email, exc)


async def _send_contact_notification_email(from_email: str, message: str) -> None:
    if not settings.resend_api_key or not settings.contact_notify_email:
        logger.warning("Resend or CONTACT_NOTIFY_EMAIL not set — contact notification email skipped")
        return
    try:
        import resend
        resend.api_key = settings.resend_api_key
        resend.Emails.send({
            "from": f"{settings.brand_name} <{settings.resend_from_email}>",
            "to": [settings.contact_notify_email],
            "reply_to": from_email,
            "subject": f"Nova mensagem de contato — {from_email}",
            "html": f"""
            <div style="font-family:system-ui,sans-serif;max-width:480px;margin:0 auto;padding:2rem">
              <h2 style="color:#111;margin:0 0 .5rem">Nova mensagem de contato</h2>
              <p style="color:#6b7280;margin:0 0 .5rem"><strong>De:</strong> {from_email}</p>
              <p style="color:#374151;white-space:pre-wrap">{message}</p>
            </div>
            """,
        })
        logger.info("contact notification email sent for %s", from_email)
    except Exception as exc:
        logger.error("failed to send contact notification email for %s: %s", from_email, exc)


@router.post("/auth/magic-request", response_model=MagicRequestResponse)
@limiter.limit("3/hour")
async def magic_request(request: Request, body: MagicRequestBody = Body(...)):
    email = body.email.lower().strip()
    user = await storage.find_or_create_user(email)
    magic_token = await storage.create_magic_token(user["id"])
    # fire-and-forget — não bloqueia o response se o e-mail demorar
    import asyncio
    asyncio.create_task(_send_magic_email(email, magic_token))
    return {"ok": True}


@router.get("/auth/verify")
async def verify_magic(request: Request, token: str = QueryParam(...)):
    user_id = await storage.consume_magic_token(token)
    if not user_id:
        return RedirectResponse(url="/?auth_error=expired", status_code=302)
    session_token = await storage.create_session(user_id)
    # mark email as verified
    await storage.mark_email_verified(user_id)
    return RedirectResponse(
        url=f"/?session={session_token}",
        status_code=302,
    )


# ── Auth: Google OAuth ────────────────────────────────────────────────────────

@router.get("/auth/google/init")
@limiter.limit("10/minute")
async def google_init(request: Request):
    if not settings.google_client_id:
        raise HTTPException(status_code=501, detail="Google OAuth não configurado.")
    state = await storage.create_oauth_state()
    params = {
        "client_id":     settings.google_client_id,
        "redirect_uri":  f"{settings.app_base_url}/api/v1/auth/google/callback",
        "response_type": "code",
        "scope":         "openid email profile",
        "state":         state,
        "access_type":   "online",
        "prompt":        "select_account",
    }
    from urllib.parse import urlencode
    redirect_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
    return {"redirect_url": redirect_url}


@router.get("/auth/google/callback")
async def google_callback(
    request: Request,
    code: Optional[str] = QueryParam(None),
    state: Optional[str] = QueryParam(None),
    error: Optional[str] = QueryParam(None),
):
    if error or not code or not state:
        return RedirectResponse(url="/?auth_error=google_failed", status_code=302)

    valid = await storage.consume_oauth_state(state)
    if not valid:
        return RedirectResponse(url="/?auth_error=google_failed", status_code=302)

    # Exchange code for tokens
    try:
        async with httpx.AsyncClient() as client:
            token_res = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code":          code,
                    "client_id":     settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "redirect_uri":  f"{settings.app_base_url}/api/v1/auth/google/callback",
                    "grant_type":    "authorization_code",
                },
                timeout=10,
            )
            token_res.raise_for_status()
            token_data = token_res.json()
    except Exception:
        return RedirectResponse(url="/?auth_error=google_failed", status_code=302)

    # Verify and decode id_token using Authlib
    try:
        from authlib.jose import jwt as jose_jwt
        from authlib.integrations.httpx_client import AsyncOAuth2Client

        # Fetch Google's public keys
        async with httpx.AsyncClient() as client:
            certs_res = await client.get("https://www.googleapis.com/oauth2/v3/certs")
            certs = certs_res.json()

        id_token = token_data["id_token"]
        claims = jose_jwt.decode(id_token, certs)
        claims.validate()

        email      = claims["email"]
        google_sub = claims["sub"]
        name       = claims.get("name")
        picture    = claims.get("picture")
    except Exception:
        return RedirectResponse(url="/?auth_error=google_failed", status_code=302)

    user = await storage.find_or_create_user_google(email, google_sub, name, picture)
    session_token = await storage.create_session(user["id"])
    return RedirectResponse(url=f"/?session={session_token}", status_code=302)


@router.get("/autocomplete")
@limiter.limit("120/minute")
async def autocomplete(
    request: Request,
    q: str = QueryParam(..., min_length=3),
    lat: Optional[float] = QueryParam(None),
    lng: Optional[float] = QueryParam(None),
):
    suggestions = await geocoding.autocomplete_address(q, lat=lat, lng=lng)
    return {"suggestions": suggestions}


@router.get("/geocode")
@limiter.limit("30/minute")
async def geocode_address(
    request: Request,
    q: str = QueryParam(..., min_length=3),
    lat: Optional[float] = QueryParam(None),
    lng: Optional[float] = QueryParam(None),
):
    async with httpx.AsyncClient() as client:
        coords = await geocoding.geocode(q, client, lat=lat, lng=lng)
    if coords is None:
        raise HTTPException(status_code=404, detail="Address not found.")
    result_lat, result_lng = coords
    return {"lat": result_lat, "lng": result_lng}


@router.post("/shorten")
@limiter.limit("20/minute")
async def shorten_route(request: Request, body: RouteRequest = Body(...)):
    state = {"addresses": body.addresses}
    if body.origin:      state["origin"]      = body.origin
    if body.destination: state["destination"] = body.destination
    code = await storage.save_route(state)
    return {"path": f"/r/{code}"}


@router.post("/routes/save")
@limiter.limit("20/minute")
async def save_route(request: Request, body: SaveRouteRequest = Body(...)):
    user = await _require_auth(request)
    code = await storage.save_result(
        body.name,
        body.result.model_dump(),
        body.inputs.model_dump(),
        user["id"],
    )
    return {"code": code, "path": f"/s/{code}"}


@router.get("/routes/my-routes")
@limiter.limit("30/minute")
async def my_routes(request: Request):
    user = await _require_auth(request)
    routes = await storage.list_results(user["id"])
    return {"routes": routes}


@router.get("/routes/saved/{code}")
async def get_saved_route(code: str):
    result = await storage.get_result(code)
    if result is None:
        raise HTTPException(status_code=404, detail="Rota não encontrada.")
    return result


@router.delete("/routes/saved/{code}")
@limiter.limit("20/minute")
async def delete_saved_route(request: Request, code: str):
    user = await _require_auth(request)
    await storage.delete_result(code, user["id"])
    return {"ok": True}


@router.post("/routes/polyline")
@limiter.limit("30/minute")
async def route_polyline(request: Request, body: PolylineRequest = Body(...)):
    pts = [(c.lat, c.lng) for c in body.points]
    path, encoded, error_status = await directions.get_route_polyline(pts)
    if path is None:
        raise HTTPException(status_code=503, detail=f"Could not retrieve road path: {error_status}")
    return {
        "path": [{"lat": lat, "lng": lng} for lat, lng in path],
        "encoded_polyline": encoded,
    }


@router.post("/routes/map-image")
@limiter.limit("20/minute")
async def route_map_image(request: Request, body: MapImageRequest = Body(...)):
    img = await static_maps.fetch_static_map(
        body.encoded_polyline,
        (body.origin.lat, body.origin.lng),
        (body.destination.lat, body.destination.lng),
    )
    if img is None:
        raise HTTPException(status_code=503, detail="Could not generate map image.")
    return Response(content=img, media_type="image/png")


@router.post("/feedback")
@limiter.limit("10/minute")
async def submit_feedback(request: Request, body: FeedbackRequest = Body(...)):
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    current_user = await storage.get_user_by_token(token) if token else None
    await storage.save_feedback(
        current_user["id"] if current_user else None,
        body.rating,
        body.comment,
    )
    return {"ok": True}


@router.post("/contact")
@limiter.limit("5/minute")
async def submit_contact(request: Request, body: ContactRequest = Body(...)):
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    current_user = await storage.get_user_by_token(token) if token else None
    await storage.save_contact_message(
        current_user["id"] if current_user else None,
        body.email,
        body.message,
    )
    await _send_contact_notification_email(body.email, body.message)
    return {"ok": True}


@router.get("/reverse")
@limiter.limit("30/minute")
async def reverse_geocode(request: Request, lat: float = QueryParam(...), lng: float = QueryParam(...)):
    address = await geocoding.reverse_geocode(lat, lng)
    if address is None:
        raise HTTPException(status_code=404, detail="Could not reverse geocode coordinates.")
    return {"address": address}


_FREE_STOP_LIMIT      = 5
_PRO_STOP_LIMIT       = 50
_EXCLUSIVE_STOP_LIMIT = 100


@router.post("/routes/optimize", response_model=RouteResponse)
@limiter.limit("20/minute")
async def optimize_route(request: Request, body: RouteRequest = Body(...)):
    # Enforce per-plan stop limit (auth is optional — anon users have no token)
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    current_user = await storage.get_user_by_token(token) if token else None
    is_pro = bool(current_user.get("is_pro", False)) if current_user else False
    is_exclusive = bool(current_user.get("is_exclusive", False)) if current_user else False
    stop_limit = (
        _EXCLUSIVE_STOP_LIMIT if is_exclusive else _PRO_STOP_LIMIT if is_pro else _FREE_STOP_LIMIT
    )
    if len(body.addresses) > stop_limit:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "STOP_LIMIT_EXCEEDED",
                "limit": stop_limit,
                "is_pro": is_pro,
            },
        )

    # Collect all addresses that need geocoding (no duplicates)
    to_geocode = list(dict.fromkeys(
        ([body.origin] if body.origin else [])
        + body.addresses
        + ([body.destination] if body.destination else [])
    ))
    resolved, failures = await geocoding.geocode_all(to_geocode)

    if body.origin and body.origin not in resolved:
        raise HTTPException(status_code=422, detail="Origin address could not be geocoded.")

    if body.destination and body.destination not in resolved:
        raise HTTPException(status_code=422, detail="Destination address could not be geocoded.")

    resolved_addresses = [a for a in body.addresses if a in resolved]

    if len(resolved_addresses) < 2:
        raise HTTPException(
            status_code=400,
            detail="At least 2 addresses must be successfully geocoded to optimize a route.",
        )

    fixed_first = body.fixed_first
    fixed_last  = body.fixed_last

    if fixed_first and fixed_first not in resolved:
        raise HTTPException(status_code=422, detail="O endereço 'visitar primeiro' não pôde ser geocodificado.")
    if fixed_last and fixed_last not in resolved:
        raise HTTPException(status_code=422, detail="O endereço 'visitar por último' não pôde ser geocodificado.")

    # Reorder resolved_addresses: fixed_first leads, fixed_last trails
    _middle = [a for a in resolved_addresses if a != fixed_first and a != fixed_last]
    resolved_addresses = (
        ([fixed_first] if fixed_first else []) +
        _middle +
        ([fixed_last]  if fixed_last  else [])
    )
    coords = [resolved[a] for a in resolved_addresses]

    # Build full coordinate list for the distance matrix
    # [origin?] + [addresses] + [destination?]
    all_coords = (
        ([resolved[body.origin]]      if body.origin      else []) +
        coords +
        ([resolved[body.destination]] if body.destination else [])
    )
    n = len(all_coords)
    origin_offset = 1 if body.origin else 0

    matrix, dur_matrix = await distance.build_distance_matrix(all_coords)

    # When fixed_first + origin both exist: origin→fixed_first is a fixed prefix leg;
    # exclude origin from the optimizer so OR-Tools doesn't freely reorder it.
    # Symmetrically for fixed_last + destination.
    has_fixed_prefix = bool(body.origin and fixed_first)
    has_fixed_suffix = bool(body.destination and fixed_last)

    if has_fixed_prefix and has_fixed_suffix:
        opt_indices = list(range(origin_offset, n - 1))
    elif has_fixed_prefix:
        opt_indices = list(range(origin_offset, n))
    elif has_fixed_suffix:
        opt_indices = list(range(0, n - 1))
    else:
        opt_indices = list(range(n))

    opt_matrix = [[matrix[i][j] for j in opt_indices] for i in opt_indices]
    opt_end    = len(opt_indices) - 1 if (body.destination or fixed_last) else None

    opt_order = optimizer.optimize_route(opt_matrix, start_index=0, end_index=opt_end)

    # Reassemble full_order in all_coords indices, prepending/appending fixed legs
    full_order = (
        ([0] if has_fixed_prefix else []) +
        [opt_indices[i] for i in opt_order] +
        ([n - 1] if has_fixed_suffix else [])
    )

    # Slice the fixed endpoints out of the order to build the middle stops
    first = 1 if body.origin else 0
    last = len(full_order) - 1 if body.destination else len(full_order)
    middle_order = full_order[first:last]

    # Map all_coords indices back to resolved_addresses indices
    order = [i - origin_offset for i in middle_order]

    total_km = sum(matrix[full_order[i]][full_order[i + 1]] for i in range(len(full_order) - 1))
    total_dur = (
        sum(dur_matrix[full_order[i]][full_order[i + 1]] for i in range(len(full_order) - 1))
        if dur_matrix else None
    )

    optimized_route = [
        RouteStop(
            order=i + 1,
            original_address=resolved_addresses[idx],
            coordinates=Coordinates(lat=coords[idx][0], lng=coords[idx][1]),
            leg_distance_km=round(matrix[full_order[first + i]][full_order[first + i + 1]], 2)
                if (first + i + 1) < len(full_order) else None,
            leg_duration_min=round(dur_matrix[full_order[first + i]][full_order[first + i + 1]], 1)
                if dur_matrix and (first + i + 1) < len(full_order) else None,
        )
        for i, idx in enumerate(order)
    ]

    def _make_endpoint_info(address: str) -> OriginInfo:
        lat, lng = resolved[address]
        return OriginInfo(address=address, coordinates=Coordinates(lat=lat, lng=lng))

    origin_info = _make_endpoint_info(body.origin) if body.origin else None
    destination_info = _make_endpoint_info(body.destination) if body.destination else None

    return RouteResponse(
        optimized_route=optimized_route,
        total_distance_km=round(total_km, 2),
        geocoding_failures=failures,
        origin=origin_info,
        destination=destination_info,
        maps_url=_build_maps_url(origin_info, destination_info, optimized_route),
        total_duration_min=round(total_dur, 1) if total_dur is not None else None,
    )


_MAPS_MAX_STOPS = 23  # Google Maps URL limit for intermediate waypoints


def _build_maps_url(
    origin: Optional[OriginInfo],
    destination: Optional[OriginInfo],
    route_stops: list,
) -> Optional[str]:
    if not route_stops:
        return None

    def coord(c: Coordinates) -> str:
        return f"{c.lat},{c.lng}"

    # Path-based format (/dir/LAT,LNG/LAT,LNG/...) shows each stop as an
    # individual field in Google Maps — the ?api=1&waypoints= format collapses
    # them into "N paradas" on mobile.
    points = []
    if origin:
        points.append(coord(origin.coordinates))
    for stop in route_stops[:_MAPS_MAX_STOPS]:
        points.append(coord(stop.coordinates))
    if destination:
        points.append(coord(destination.coordinates))

    return "https://www.google.com/maps/dir/" + "/".join(points) + "/"
