from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@patch("app.routers.routes.geocoding.geocode_all")
def test_optimize_route_success(mock_geocode_all):
    mock_geocode_all.return_value = (
        {
            "Av. Paulista, 1000, São Paulo": (-23.5615, -46.6565),
            "Rua Augusta, 500, São Paulo": (-23.5558, -46.6614),
            "Praça da Sé, São Paulo": (-23.5504, -46.6339),
        },
        [],
    )

    response = client.post(
        "/api/v1/routes/optimize",
        json={"addresses": ["Av. Paulista, 1000, São Paulo", "Rua Augusta, 500, São Paulo", "Praça da Sé, São Paulo"]},
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["optimized_route"]) == 3
    assert data["geocoding_failures"] == []
    assert data["total_distance_km"] > 0
    assert data["origin"] is None


@patch("app.routers.routes.geocoding.geocode_all")
def test_optimize_route_with_origin(mock_geocode_all):
    mock_geocode_all.return_value = (
        {
            "Origem, São Paulo": (-23.5500, -46.6300),
            "Av. Paulista, 1000, São Paulo": (-23.5615, -46.6565),
            "Praça da Sé, São Paulo": (-23.5504, -46.6339),
        },
        [],
    )

    response = client.post(
        "/api/v1/routes/optimize",
        json={
            "origin": "Origem, São Paulo",
            "addresses": ["Av. Paulista, 1000, São Paulo", "Praça da Sé, São Paulo"],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["optimized_route"]) == 2
    assert data["origin"]["address"] == "Origem, São Paulo"
    assert "lat" in data["origin"]["coordinates"]
    assert data["total_distance_km"] > 0


@patch("app.routers.routes.geocoding.geocode_all")
def test_optimize_route_origin_geocoding_fails(mock_geocode_all):
    mock_geocode_all.return_value = (
        {"Av. Paulista, 1000, São Paulo": (-23.5615, -46.6565),
         "Praça da Sé, São Paulo": (-23.5504, -46.6339)},
        ["Origem Inválida XYZ"],
    )

    response = client.post(
        "/api/v1/routes/optimize",
        json={
            "origin": "Origem Inválida XYZ",
            "addresses": ["Av. Paulista, 1000, São Paulo", "Praça da Sé, São Paulo"],
        },
    )

    assert response.status_code == 422
    assert "origem" in response.json()["detail"].lower()


@patch("app.routers.routes.geocoding.geocode_all")
def test_optimize_route_with_failure(mock_geocode_all):
    mock_geocode_all.return_value = (
        {
            "Av. Paulista, 1000, São Paulo": (-23.5615, -46.6565),
            "Praça da Sé, São Paulo": (-23.5504, -46.6339),
        },
        ["Endereço Inválido XYZ"],
    )

    response = client.post(
        "/api/v1/routes/optimize",
        json={"addresses": ["Av. Paulista, 1000, São Paulo", "Endereço Inválido XYZ", "Praça da Sé, São Paulo"]},
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["optimized_route"]) == 2
    assert data["geocoding_failures"] == ["Endereço Inválido XYZ"]


@patch("app.routers.routes.geocoding.geocode_all")
def test_optimize_route_all_geocoding_fails(mock_geocode_all):
    mock_geocode_all.return_value = ({}, ["Addr A", "Addr B"])

    response = client.post(
        "/api/v1/routes/optimize",
        json={"addresses": ["Addr A", "Addr B"]},
    )

    assert response.status_code == 400


def test_optimize_route_too_few_addresses():
    response = client.post(
        "/api/v1/routes/optimize",
        json={"addresses": ["Apenas um endereço"]},
    )
    assert response.status_code == 422


@patch("app.routers.routes.geocoding.geocode_all")
def test_optimize_route_with_destination(mock_geocode_all):
    mock_geocode_all.return_value = (
        {
            "Origem, São Paulo": (-23.5500, -46.6300),
            "Av. Paulista, 1000, São Paulo": (-23.5615, -46.6565),
            "Praça da Sé, São Paulo": (-23.5504, -46.6339),
            "Destino, São Paulo": (-23.5600, -46.6400),
        },
        [],
    )

    response = client.post(
        "/api/v1/routes/optimize",
        json={
            "origin": "Origem, São Paulo",
            "destination": "Destino, São Paulo",
            "addresses": ["Av. Paulista, 1000, São Paulo", "Praça da Sé, São Paulo"],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["optimized_route"]) == 2
    assert data["origin"]["address"] == "Origem, São Paulo"
    assert data["destination"]["address"] == "Destino, São Paulo"
    assert data["total_distance_km"] > 0


@patch("app.routers.routes.geocoding.geocode_all")
def test_optimize_route_return_to_origin(mock_geocode_all):
    coords = (-23.5500, -46.6300)
    mock_geocode_all.return_value = (
        {
            "Depósito, São Paulo": coords,
            "Av. Paulista, 1000, São Paulo": (-23.5615, -46.6565),
            "Praça da Sé, São Paulo": (-23.5504, -46.6339),
        },
        [],
    )

    response = client.post(
        "/api/v1/routes/optimize",
        json={
            "origin": "Depósito, São Paulo",
            "destination": "Depósito, São Paulo",
            "addresses": ["Av. Paulista, 1000, São Paulo", "Praça da Sé, São Paulo"],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["optimized_route"]) == 2
    assert data["origin"]["address"] == "Depósito, São Paulo"
    assert data["destination"]["address"] == "Depósito, São Paulo"


@patch("app.routers.routes.geocoding.geocode_all")
def test_optimize_route_destination_geocoding_fails(mock_geocode_all):
    mock_geocode_all.return_value = (
        {"Av. Paulista, 1000, São Paulo": (-23.5615, -46.6565),
         "Praça da Sé, São Paulo": (-23.5504, -46.6339)},
        ["Destino Inválido XYZ"],
    )

    response = client.post(
        "/api/v1/routes/optimize",
        json={
            "destination": "Destino Inválido XYZ",
            "addresses": ["Av. Paulista, 1000, São Paulo", "Praça da Sé, São Paulo"],
        },
    )

    assert response.status_code == 422
    assert "destino" in response.json()["detail"].lower()
