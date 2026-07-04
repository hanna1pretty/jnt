import os
import aiohttp
from dotenv import load_dotenv

load_dotenv()
DEFAULT_API_KEY = os.getenv("BINDERBYTE_API_KEY")
BASE_URL = "https://api.binderbyte.com/v1/track"

async def cek_resi(courier: str, resi: str, api_key: str = None):
    key = api_key or DEFAULT_API_KEY
    if not key:
        return {"success": False, "status": "API key belum diatur", "history": [], "raw": {}, "detail": {}}

    params = {"api_key": key, "courier": courier.lower(), "awb": resi}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                BASE_URL, params=params, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                data = await resp.json(content_type=None)
    except Exception as e:
        return {"success": False, "status": f"Error koneksi: {e}", "history": [], "raw": {}, "detail": {}}

    if data.get("status") != 200:
        pesan = data.get("message", "Resi tidak ditemukan atau kurir tidak dikenali")
        return {"success": False, "status": pesan, "history": [], "raw": data, "detail": {}}

    detail = data.get("data", {}) or {}
    summary = detail.get("summary", {}) or {}
    history = detail.get("history", []) or []

    return {
        "success": True,
        "status": summary.get("status", "Tidak diketahui"),
        "history": history,
        "raw": data,
        "detail": {
            "courier_name": summary.get("courier", courier.upper()),
            "service": summary.get("service", "-"),
            "last_desc": summary.get("desc", "-"),
            "last_date": summary.get("date", "-"),
            "weight": summary.get("weight", "-"),
        }
    }


def format_history(history: list, max_items: int = 5) -> str:
    if not history:
        return "_Belum ada riwayat._"
    lines = []
    for item in history[:max_items]:
        tanggal = item.get("date", "-")
        desc = item.get("desc", "-")
        lines.append(f"🕐 {tanggal}\n   {desc}")
    sisa = len(history) - max_items
    teks = "\n\n".join(lines)
    if sisa > 0:
        teks += f"\n\n_...dan {sisa} riwayat lainnya_"
    return teks
