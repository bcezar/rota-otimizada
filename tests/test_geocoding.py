from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import geocoding


@pytest.fixture(autouse=True)
def clear_cache():
    geocoding._cache.clear()
    yield
    geocoding._cache.clear()


# Force Nominatim path in unit tests (no Google Maps key)
@pytest.fixture(autouse=True)
def use_nominatim(clear_cache):
    with patch("app.services.geocoding.settings") as mock_settings:
        mock_settings.google_maps_api_key = None
        mock_settings.nominatim_user_agent = "test/1.0"
        mock_settings.nominatim_base_url = "https://nominatim.openstreetmap.org"
        yield mock_settings


@pytest.mark.asyncio
async def test_geocode_success(use_nominatim):
    mock_response = MagicMock()
    mock_response.json.return_value = [{"lat": "-23.5615", "lon": "-46.6565"}]
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response

    result = await geocoding.geocode("Av. Paulista, 1000", mock_client)
    assert result == pytest.approx((-23.5615, -46.6565))


@pytest.mark.asyncio
async def test_geocode_not_found_returns_none(use_nominatim):
    mock_response = MagicMock()
    mock_response.json.return_value = []
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response

    result = await geocoding.geocode("Endereço Inexistente XYZ 99999", mock_client)
    assert result is None


@pytest.mark.asyncio
async def test_geocode_uses_cache(use_nominatim):
    mock_response = MagicMock()
    mock_response.json.return_value = [{"lat": "-23.5615", "lon": "-46.6565"}]
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response

    await geocoding.geocode("Av. Paulista, 1000", mock_client)
    await geocoding.geocode("Av. Paulista, 1000", mock_client)

    assert mock_client.get.call_count == 1


@pytest.mark.asyncio
async def test_geocode_cache_ignores_case_and_whitespace(use_nominatim):
    mock_response = MagicMock()
    mock_response.json.return_value = [{"lat": "-23.5615", "lon": "-46.6565"}]
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response

    await geocoding.geocode("Av. Paulista, 1000", mock_client)
    await geocoding.geocode("  av. paulista,   1000  ", mock_client)
    await geocoding.geocode("AV. PAULISTA, 1000", mock_client)

    assert mock_client.get.call_count == 1


@pytest.mark.asyncio
async def test_geocode_all_separates_failures():
    with patch("app.services.geocoding.geocode") as mock_geocode, \
         patch("asyncio.sleep", new_callable=AsyncMock), \
         patch("httpx.AsyncClient") as mock_client_cls, \
         patch("app.storage.get_geocoding_cache_batch", new_callable=AsyncMock, return_value={}):

        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_geocode.side_effect = [
            (-23.5615, -46.6565),
            None,
            (-23.5504, -46.6339),
        ]

        addresses = ["Addr A", "Bad Address", "Addr C"]
        resolved, failures = await geocoding.geocode_all(addresses)

        assert "Addr A" in resolved
        assert "Addr C" in resolved
        assert failures == ["Bad Address"]
