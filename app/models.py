from __future__ import annotations

from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field

Address = Annotated[str, Field(min_length=1, max_length=200)]


class RouteRequest(BaseModel):
    # 100 is the ceiling for the highest tier (Exclusive) — per-plan limits
    # (free/Pro/Exclusive) are enforced in the route handler, not here.
    addresses: list[Address] = Field(..., min_length=2, max_length=100)
    origin: Optional[Address] = None
    destination: Optional[Address] = None
    fixed_first: Optional[Address] = None
    fixed_last: Optional[Address] = None
    stop_ids: Optional[list[str]] = None
    preserve_order: bool = False


class Coordinates(BaseModel):
    lat: float
    lng: float


class OriginInfo(BaseModel):
    address: str
    coordinates: Coordinates


class RouteStop(BaseModel):
    order: int
    stop_id: Optional[str] = None
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


class AddressBookClient(BaseModel):
    name: str = Field(default="", max_length=200)
    email: str = Field(default="", max_length=200)
    phone: str = Field(default="", max_length=80)


class SavedRouteStop(BaseModel):
    id: str = Field(..., min_length=1, max_length=100)
    address: str = Field(..., min_length=1, max_length=500)
    description: str = Field(default="", max_length=2000)
    place_name: str = Field(default="", max_length=200)
    client: Optional[AddressBookClient] = None
    tags: list[str] = Field(default_factory=list, max_length=100)


class SaveRouteRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    result: RouteResponse
    inputs: RouteRequest
    stop_details: Optional[list[SavedRouteStop]] = None
    destination_mode: Optional[Literal["return_to_origin", "custom", "none"]] = None


class AddressBookEntry(BaseModel):
    id: str = Field(..., min_length=1, max_length=100)
    address: str = Field(..., min_length=1, max_length=500)
    place_name: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=5000)
    client: Optional[AddressBookClient] = None
    tags: list[str] = Field(default_factory=list, max_length=100)
    updated_at: str = Field(..., min_length=1, max_length=40)
    deleted_at: Optional[str] = Field(default=None, max_length=40)
    position: int = Field(default=0, ge=0, le=10000)


class AddressBookSyncRequest(BaseModel):
    addresses: list[AddressBookEntry] = Field(default_factory=list, max_length=1000)


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=200)


class UserResponse(BaseModel):
    id: str
    email: str
    is_pro: bool = False
    email_verified: bool = False
    name: Optional[str] = None
    picture_url: Optional[str] = None
    is_exclusive: bool = False
    exclusive_until: Optional[str] = None


class LoginResponse(BaseModel):
    token: str
    user: UserResponse


class MagicRequestBody(BaseModel):
    email: str = Field(..., min_length=3, max_length=200)
    client: Literal["web", "app"] = "web"


class MobileAuthExchangeRequest(BaseModel):
    code: str = Field(..., min_length=20, max_length=100)


class MagicRequestResponse(BaseModel):
    ok: bool


class FeedbackRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    comment: Optional[str] = Field(None, max_length=1000)


class ContactRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=200)
    message: str = Field(..., min_length=1, max_length=2000)


class UserPreferences(BaseModel):
    default_origin: Optional[Address] = None
    default_destination: Optional[Address] = None
    destination_mode: Literal["return_to_origin", "custom", "none"] = "none"
    navigation_app: Literal["ask_always", "google_maps", "waze", "apple_maps"] = "ask_always"
    distance_unit: Literal["km", "mi"] = "km"
    vehicle_type: Literal["car", "truck", "motorcycle", "bicycle"] = "car"


class UpdatePreferencesRequest(BaseModel):
    default_origin: Optional[Address] = None
    default_destination: Optional[Address] = None
    destination_mode: Optional[Literal["return_to_origin", "custom", "none"]] = None
    navigation_app: Optional[Literal["ask_always", "google_maps", "waze", "apple_maps"]] = None
    distance_unit: Optional[Literal["km", "mi"]] = None
    vehicle_type: Optional[Literal["car", "truck", "motorcycle", "bicycle"]] = None
