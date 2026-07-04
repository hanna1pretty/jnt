import requests

def geocode_city(city_name: str):
    """Geocode nama kota ke (lat, lon) pakai Nominatim OSM (gratis, tanpa API key)."""
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": f"{city_name}, Indonesia", "format": "json", "limit": 1}
    headers = {"User-Agent": "resi-tracker-bot-personal (contact: youremail@example.com)"}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        data = resp.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception:
        pass
    return None