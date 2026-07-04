import requests

# Mapping kode hub ke kota — diperluas dari pola umum singkatan kota Indonesia.
# ⚠️ INI PERKIRAAN, bukan daftar resmi dari ekspedisi manapun.
# Kalau nemu kode yang salah/tidak ada saat pakai bot, tambah/perbaiki manual di sini.
HUB_CODE_MAP = {
    "BDG": "Bandung",
    "SMI": "Sukabumi",
    "JBR": "Jember",
    "CKL": "Cikole",
    "JKT": "Jakarta",
    "SBY": "Surabaya",
    "SMG": "Semarang",
    "SLO": "Solo",
    "YGY": "Yogyakarta",
    "MLG": "Malang",
    "DPS": "Denpasar",
    "MDN": "Medan",
    "PLB": "Palembang",
    "PKU": "Pekanbaru",
    "BTH": "Batam",
    "BKS": "Bekasi",
    "TGR": "Tangerang",
    "BGR": "Bogor",
    "CRB": "Cirebon",
    "TGL": "Tegal",
    "PWT": "Purwokerto",
    "KDR": "Kediri",
    "JBR_GATEWAY": "Jember",
    "BDG_GATEWAY": "Bandung",
    "SMI_GATEWAY": "Sukabumi",
    "DC_CIKOLE": "Cikole",
}

def resolve_hub_code(desc: str):
    """Cari kode hub (format XXX_GATEWAY, XXX_DC, atau DC_XXX) di teks desc, mapping ke kota."""
    import re
    if not desc:
        return None
    # Coba cocokkan langsung ke key lengkap dulu (misal "BDG_GATEWAY")
    for kode, kota in HUB_CODE_MAP.items():
        if kode in desc:
            return kota
    # Fallback: ekstrak kode 2-4 huruf sebelum _GATEWAY / _DC, atau setelah DC_
    match = re.search(r"\b([A-Z]{2,5})_(GATEWAY|DC)\b|\bDC_([A-Z]{2,5})\b", desc)
    if match:
        kode = match.group(1) or match.group(3)
        return HUB_CODE_MAP.get(kode)
    return None

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
