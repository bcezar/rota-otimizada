from __future__ import annotations

import httpx

from app.config import settings


def _decode_polyline(encoded: str) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    index = lat = lng = 0
    while index < len(encoded):
        for is_lng in (False, True):
            result = shift = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1f) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = -(result >> 1) if result & 1 else result >> 1
            if is_lng:
                lng += delta
            else:
                lat += delta
        points.append((lat * 1e-5, lng * 1e-5))
    return points


def _encode_polyline(points: list[tuple[float, float]]) -> str:
    encoded: list[str] = []
    previous_lat = previous_lng = 0

    for latitude, longitude in points:
        lat = round(latitude * 1e5)
        lng = round(longitude * 1e5)
        for delta in (lat - previous_lat, lng - previous_lng):
            value = ~(delta << 1) if delta < 0 else delta << 1
            while value >= 0x20:
                encoded.append(chr((0x20 | (value & 0x1f)) + 63))
                value >>= 5
            encoded.append(chr(value + 63))
        previous_lat, previous_lng = lat, lng

    return "".join(encoded)


async def get_route_polyline(
    points: list[tuple[float, float]],
) -> tuple[list[tuple[float, float]] | None, str | None, str | None]:
    """Returns (decoded_path, encoded_polyline, error_status). error_status is None on success."""
    if not settings.google_maps_api_key:
        return None, None, "NO_API_KEY"
    if len(points) < 2:
        return None, None, "INSUFFICIENT_POINTS"

    origin = f"{points[0][0]},{points[0][1]}"
    destination = f"{points[-1][0]},{points[-1][1]}"
    waypoints = "|".join(f"{p[0]},{p[1]}" for p in points[1:-1][:25])

    params: dict = {
        "origin": origin,
        "destination": destination,
        "key": settings.google_maps_api_key,
    }
    if waypoints:
        params["waypoints"] = waypoints

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                "https://maps.googleapis.com/maps/api/directions/json",
                params=params,
                timeout=10.0,
            )
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            return None, None, "HTTP_ERROR"

    status = data.get("status", "UNKNOWN")
    if status != "OK" or not data.get("routes"):
        return None, None, status

    route = data["routes"][0]
    path: list[tuple[float, float]] = []
    for leg in route.get("legs", []):
        for step in leg.get("steps", []):
            step_encoded = step.get("polyline", {}).get("points", "")
            if not step_encoded:
                continue
            step_path = _decode_polyline(step_encoded)
            if path and step_path and path[-1] == step_path[0]:
                path.extend(step_path[1:])
            else:
                path.extend(step_path)

    if not path:
        overview = route.get("overview_polyline", {}).get("points", "")
        if overview:
            path = _decode_polyline(overview)

    if not path:
        return None, None, "EMPTY_POLYLINE"

    return path, _encode_polyline(path), None
