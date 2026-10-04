from __future__ import annotations

from typing import Any

import httpx


_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


class WeatherError(RuntimeError):
    """The weather service did not return a usable forecast."""


async def forecast(location: str) -> dict[str, Any]:
    query = location.strip()[:120]
    if not query:
        raise WeatherError("location is required")

    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        geocode = await client.get(
            _GEOCODING_URL,
            params={"name": query, "count": 1, "language": "en", "format": "json"},
        )
        if geocode.status_code >= 400:
            raise WeatherError(f"weather location lookup failed ({geocode.status_code})")
        results = geocode.json().get("results") or []
        if not results:
            raise WeatherError(f"location not found: {query}")
        place = results[0]
        latitude = place.get("latitude")
        longitude = place.get("longitude")
        if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
            raise WeatherError("weather location lookup returned no coordinates")

        response = await client.get(
            _FORECAST_URL,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "forecast_days": 1,
                "timezone": "auto",
            },
        )
        if response.status_code >= 400:
            raise WeatherError(f"weather forecast failed ({response.status_code})")
        payload = response.json()

    current = payload.get("current")
    daily = payload.get("daily")
    if not isinstance(current, dict) or not isinstance(daily, dict):
        raise WeatherError("weather forecast returned incomplete data")
    return {
        "ok": True,
        "location": {
            "name": place.get("name") or query,
            "country": place.get("country"),
            "latitude": latitude,
            "longitude": longitude,
        },
        "timezone": payload.get("timezone"),
        "current": current,
        "today": {
            key: values[0]
            for key, values in daily.items()
            if isinstance(values, list) and values
        },
        "source": "Open-Meteo",
    }
