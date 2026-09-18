from __future__ import annotations

import asyncio
import re
from collections import OrderedDict

import httpx

import logging

from app.config import settings
from app import storage

logger = logging.getLogger(__name__)

_CACHE_MAXSIZE = 2000


class _LRUCache(OrderedDict):
    """Dict-like cache that evicts the least-recently-used entry past maxsize."""

    def __init__(self, maxsize: int) -> None:
        super().__init__()
        self.maxsize = maxsize

    def __getitem__(self, key):
        value = super().__getitem__(key)
        self.move_to_end(key)
        return value

    def __setitem__(self, key, value) -> None:
        super().__setitem__(key, value)
        self.move_to_end(key)
        if len(self) > self.maxsize:
            self.popitem(last=False)


_cache: _LRUCache = _LRUCache(maxsize=_CACHE_MAXSIZE)


def _cache_key(address: str) -> str:
    """Collapse whitespace/case differences so equivalent inputs share a cache entry."""
    return re.sub(r"\s+", " ", address.strip()).lower()


_STREET_PREFIXES = r"(?:Rua|R\.|Av\.|Avenida|Alameda|Al\.|Travessa|Tv\.|Estrada|Rod\.|Rodovia|Praça|Pça\.)"


def _normalize(address: str) -> str:
    """Normalize Brazilian address format for better Nominatim matching.

    - 'Medkal Pet, 123 Rua X, Cidade/SP' → 'Rua X, 123, Cidade, SP, Brasil'
    - 'Rua X, 123, Cidade/SP'            → 'Rua X, 123, Cidade, SP, Brasil'
    """
    # Strip leading business name: if a street prefix appears after a comma,
    # discard everything before it and move any inline number after the street name.
    # e.g. "Medkal Pet, 446 Rua Bolívia, Americana/SP"
    #   → "Rua Bolívia, 446, Americana/SP"
    business_match = re.search(
        rf",\s*(\d+)?\s*({_STREET_PREFIXES}\b.+)", address, re.IGNORECASE
    )
    if business_match:
        number = business_match.group(1) or ""
        rest = business_match.group(2).strip()
        # Re-attach the number right after the street prefix+name segment
        first_comma = rest.find(",")
        if number:
            if first_comma != -1:
                rest = rest[:first_comma] + f", {number}" + rest[first_comma:]
            else:
                rest = rest + f", {number}"
        address = rest

    # Replace city/state slash separator: Americana/SP → Americana, SP
    normalized = re.sub(r"([A-Za-zÀ-ú\s]+)/([A-Z]{2})\b", r"\1, \2", address)
    # Append country hint for Nominatim if locale is Brazil
    if settings.geocoding_country == "br":
        if "brasil" not in normalized.lower() and "brazil" not in normalized.lower():
            normalized = normalized.rstrip(", ") + ", Brasil"
    return normalized


def _parse_structured(address: str) -> dict[str, str] | None:
    """Try to extract street + city + state for structured Nominatim query.

    Expects format: 'Street info, City, SP, Brasil'
    Returns None if pattern doesn't match.
    """
    # Match: anything, City, 2-letter state, Brasil
    match = re.search(r"^(.+),\s*([^,]+),\s*([A-Z]{2}),\s*Brasil\s*$", address)
    if not match:
        return None
    return {
        "street": match.group(1).strip(),
        "city": match.group(2).strip(),
        "state": match.group(3).strip(),
        "country": "Brasil",
        "format": "json",
        "limit": "1",
        **({"countrycodes": settings.geocoding_country} if settings.geocoding_country else {}),
    }


async def _query_nominatim(
    params: dict, client: httpx.AsyncClient
) -> tuple[float, float] | None:
    headers = {"User-Agent": settings.nominatim_user_agent}
    try:
        response = await client.get(
            f"{settings.nominatim_base_url}/search",
            params=params,
            headers=headers,
            timeout=10.0,
        )
        response.raise_for_status()
        results = response.json()
    except (httpx.HTTPError, ValueError):
        return None

    if not results:
        return None

    return (float(results[0]["lat"]), float(results[0]["lon"]))


_BOUNDS_DELTA = 0.09  # ~10 km


async def _geocode_google(
    address: str, client: httpx.AsyncClient,
    lat: float | None = None, lng: float | None = None,
) -> tuple[float, float] | None:
    params: dict = {"address": address, "key": settings.google_maps_api_key}
    if lat is not None and lng is not None:
        params["bounds"] = f"{lat-_BOUNDS_DELTA},{lng-_BOUNDS_DELTA}|{lat+_BOUNDS_DELTA},{lng+_BOUNDS_DELTA}"
    try:
        response = await client.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params=params,
            timeout=10.0,
        )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError):
        return None

    if data.get("status") != "OK" or not data.get("results"):
        return None

    loc = data["results"][0]["geometry"]["location"]
    return (loc["lat"], loc["lng"])


async def _geocode_nominatim(
    address: str, client: httpx.AsyncClient,
    lat: float | None = None, lng: float | None = None,
) -> tuple[float, float] | None:
    normalized = _normalize(address)

    base_params: dict = {"q": normalized, "format": "json", "limit": 1}
    if settings.geocoding_country:
        base_params["countrycodes"] = settings.geocoding_country
    if lat is not None and lng is not None:
        d = _BOUNDS_DELTA
        base_params["viewbox"] = f"{lng-d},{lat+d},{lng+d},{lat-d}"
        base_params["bounded"] = "1"

    coords = await _query_nominatim(base_params, client)

    if coords is None:
        await asyncio.sleep(1.0)
        structured = _parse_structured(normalized)
        if structured:
            if lat is not None and lng is not None:
                d = _BOUNDS_DELTA
                structured["viewbox"] = f"{lng-d},{lat+d},{lng+d},{lat-d}"
                structured["bounded"] = "1"
            coords = await _query_nominatim(structured, client)

    return coords


async def geocode(
    address: str, client: httpx.AsyncClient,
    lat: float | None = None, lng: float | None = None,
) -> tuple[float, float] | None:
    key = _cache_key(address)
    if key in _cache:
        return _cache[key]

    if settings.google_maps_api_key:
        coords = await _geocode_google(address, client, lat=lat, lng=lng)
    else:
        coords = await _geocode_nominatim(address, client, lat=lat, lng=lng)

    _cache[key] = coords
    if coords is not None:
        asyncio.create_task(storage.set_geocoding_cache(key, coords[0], coords[1]))
    return coords


async def autocomplete_address(query: str, lat: float | None = None, lng: float | None = None) -> list[str]:
    if not settings.google_maps_api_key:
        return []
    params: dict = {
        "input": query,
        "key": settings.google_maps_api_key,
        "language": settings.geocoding_language,
        **({"components": f"country:{settings.geocoding_country}"} if settings.geocoding_country else {}),
    }
    if lat is not None and lng is not None:
        params["location"] = f"{lat},{lng}"
        params["radius"] = "10000"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                "https://maps.googleapis.com/maps/api/place/autocomplete/json",
                params=params,
                timeout=5.0,
            )
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            return []
    if data.get("status") not in ("OK", "ZERO_RESULTS"):
        return []
    return [p["description"] for p in data.get("predictions", [])]


async def reverse_geocode(lat: float, lng: float) -> "str | None":
    async with httpx.AsyncClient() as client:
        if settings.google_maps_api_key:
            try:
                response = await client.get(
                    "https://maps.googleapis.com/maps/api/geocode/json",
                    params={"latlng": f"{lat},{lng}", "key": settings.google_maps_api_key, "language": settings.geocoding_language},
                    timeout=10.0,
                )
                data = response.json()
            except (httpx.HTTPError, ValueError):
                return None
            if data.get("status") == "OK":
                for result in data.get("results", []):
                    if "plus_code" not in result.get("types", []):
                        return result["formatted_address"]
                return None
        else:
            try:
                response = await client.get(
                    f"{settings.nominatim_base_url}/reverse",
                    params={"lat": lat, "lon": lng, "format": "json"},
                    headers={"User-Agent": settings.nominatim_user_agent},
                    timeout=10.0,
                )
                data = response.json()
            except (httpx.HTTPError, ValueError):
                return None
            if "display_name" in data:
                return data["display_name"]
    return None


async def geocode_all(
    addresses: list[str],
) -> tuple[dict[str, tuple[float, float]], list[str]]:
    """Returns (resolved: {address -> (lat, lng)}, failures: [address])."""
    resolved: dict[str, tuple[float, float]] = {}
    failures: list[str] = []

    # Tier 1: in-memory hits (instant)
    remaining = []
    for addr in addresses:
        key = _cache_key(addr)
        if key in _cache and _cache[key] is not None:
            resolved[addr] = _cache[key]
        elif key not in _cache:
            remaining.append(addr)
        # key in _cache with None value → known failure this session, skip

    memory_hits = len(addresses) - len(remaining)

    # Tier 2: Turso batch lookup for remaining
    turso_hit_count = 0
    if remaining:
        remaining_keys = {_cache_key(a): a for a in remaining}
        turso_hits = await storage.get_geocoding_cache_batch(list(remaining_keys))
        for key, coords in turso_hits.items():
            _cache[key] = coords
            resolved[remaining_keys[key]] = coords
        turso_hit_count = len(turso_hits)
        remaining = [a for a in remaining if _cache_key(a) not in turso_hits]

    logger.info(
        "geocode_all total=%d memory_hits=%d turso_hits=%d api_calls=%d",
        len(addresses), memory_hits, turso_hit_count, len(remaining),
    )

    if not remaining:
        return resolved, failures

    # Tier 3: API calls for true cache misses
    async with httpx.AsyncClient() as client:
        if settings.google_maps_api_key:
            # Google Maps: geocode all misses in parallel
            results = await asyncio.gather(*(geocode(a, client) for a in remaining))
            for address, result in zip(remaining, results):
                if result is None:
                    failures.append(address)
                else:
                    resolved[address] = result
        else:
            # Nominatim usage policy: max 1 request/second — must stay sequential
            for address in remaining:
                result = await geocode(address, client)
                if result is None:
                    failures.append(address)
                else:
                    resolved[address] = result
                await asyncio.sleep(1.0)

    return resolved, failures
