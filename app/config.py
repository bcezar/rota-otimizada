from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    app_name: str = "rota-otimizada"
    nominatim_user_agent: str = "rota-otimizada/1.0"
    locale: str = "pt-BR"
    brand_name: str = "Rota Otimizada"
    geocoding_country: str = "br"
    geocoding_language: str = "pt-BR"
    nominatim_base_url: str = "https://nominatim.openstreetmap.org"
    tsp_timeout_seconds: int = 5
    max_addresses: int = 50
    google_maps_api_key: Optional[str] = None
    osrm_base_url: Optional[str] = None
    turso_database_url: Optional[str] = None
    turso_auth_token: Optional[str] = None
    ga_measurement_id: Optional[str] = None
    resend_api_key: Optional[str] = None
    resend_from_email: str = "noreply@rotaotimizada.com.br"
    app_base_url: str = "https://rotaotimizada.com.br"
    google_client_id: Optional[str] = None
    google_client_secret: Optional[str] = None
    asaas_api_key: Optional[str] = None
    asaas_webhook_token: Optional[str] = None
    asaas_base_url: str = "https://sandbox.asaas.com/api/v3"
    pro_price: float = 29.90
    stripe_secret_key: Optional[str] = None
    stripe_webhook_secret: Optional[str] = None
    stripe_price_id: Optional[str] = None
    contact_notify_email: Optional[str] = None


settings = Settings()
