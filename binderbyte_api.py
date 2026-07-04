import os
import aiohttp
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("BINDERBYTE_API_KEY")
BASE_URL = "https://api.binderbyte.com/v1/track"

async def cek_resi(courier: str, resi: str):
    """
    Mengembalikan dict: {"success": bool, "status": str, "history": list, "raw": dict}
    """
    if not API_KEY:
        return {"success": False, "status": "BINDERBYTE_API_KEY belum diisi di .env", "history": [], "raw": {}}

    params = {"api_key": API_KEY, "courier": courier.lower(), "awb": resi}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                BASE_URL, params=params, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                data = await resp.json(content_type=None)
    except Exception as e:
        return {"success": False, "status": f"Error koneksi: {e}", "history": [], "raw": {}}

    if data.get("status") != 200:
        pesan = data.get("message", "Resi tidak ditemukan atau kurir tidak dikenali")
        return {"success": False, "status": pesan, "history": [], "raw": data}

    detail = data.get("data", {}) or {}
    summary = detail.get("summary", {}) or {}
    history = detail.get("history", []) or []
    status_terakhir = summary.get("status", "Tidak diketahui")

    return {"success": True, "status": status_terakhir, "history": history, "raw": data}