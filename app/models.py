from __future__ import annotations

from typing import Annotated, Optional

from pydantic import BaseModel, Field

Address = Annotated[str, Field(min_length=1, max_length=200)]


class RouteRequest(BaseModel):
    addresses: list[Address] = Field(..., min_length=2, max_length=50)
    origin: Optional[Address] = None
    destination: Optional[Address] = None
    fixed_first: Optional[Address] = None
    fixed_last: Optional[Address] = None


class Coordinates(BaseModel):
    lat: float
    lng: float


class OriginInfo(BaseModel):
    address: str
    coordinates: Coordinates


class RouteStop(BaseModel):
    order: int
    original_address: str
    coordinates: Coordinates
    leg_distance_km: Optional[float] = None
    leg_duration_min: Optional[float] = None


class RouteResponse(BaseModel):
    optimized_route: list[RouteStop]
    total_distance_km: float
    geocoding_failures: list[str]
    origin: Optional[OriginInfo] = None
    destination: Optional[OriginInfo] = None
    maps_url: Optional[str] = None
    total_duration_min: Optional[float] = None


class PolylineRequest(BaseModel):
    points: list[Coordinates] = Field(..., min_length=2)


class MapImageRequest(BaseModel):
    encoded_polyline: Optional[str] = None
    origin: Coordinates
    destination: Coordinates


class SaveRouteRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    result: RouteResponse
    inputs: RouteRequest


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=200)


class UserResponse(BaseModel):
    id: str
    email: str
    is_pro: bool = False
    email_verified: bool = False
    name: Optional[str] = None
    picture_url: Optional[str] = None


class LoginResponse(BaseModel):
    token: str
    user: UserResponse


class MagicRequestBody(BaseModel):
    email: str = Field(..., min_length=3, max_length=200)


class MagicRequestResponse(BaseModel):
    ok: bool


class FeedbackRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    comment: Optional[str] = Field(None, max_length=1000)
