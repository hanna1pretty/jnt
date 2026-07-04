import os
import re
import logging
import asyncio
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from database import init_db, add_resi, remove_resi, get_all_resi, update_status
from binderbyte_api import cek_resi
from geocode import geocode_city

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

KURIR_VALID = ["jne", "pos", "jnt", "sicepat", "tiki", "anteraja", "wahana", "ninja", "lion"]
CEK_INTERVAL_DETIK = 3 * 3600   # 3 jam
MAX_PARALEL = 5                 # batas request bersamaan ke BinderByte

CITY_PATTERN = re.compile(r"\[([A-Z\s,]+)\]|di\s+([A-Za-z\s]+)$")

def extract_city(status_text: str):
    if not status_text:
        return None
    match = CITY_PATTERN.search(status_text)
    if match:
        return (match.group(1) or match.group(2) or "").strip()
    return None

# ---------- Command handlers ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Halo! Ini bot pelacak paket pribadi.\nKetik /help untuk daftar perintah."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    teks = (
        "📦 *Bot Cek Resi — Panduan*\n\n"
        "/track <kurir> <resi> [label] — mulai pantau paket\n"
        "   contoh: `/track jnt JP6961181926 Sepatu`\n\n"
        "/status — lihat semua paket yang dipantau\n"
        "/cek <kurir> <resi> — cek sekali tanpa disimpan\n"
        "/untrack <resi> — berhenti memantau\n"
        "/map <resi> — kirim titik lokasi checkpoint terakhir\n"
        "/kurir — daftar kode kurir yang didukung\n"
        "/help — tampilkan pesan ini\n\n"
        "⚠️ SPX (Shopee Express) belum didukung otomatis — cek manual di spx.co.id.\n"
        f"Bot otomatis cek ulang tiap {CEK_INTERVAL_DETIK // 3600} jam dan kirim notifikasi kalau status berubah."
    )
    await update.message.reply_text(teks, parse_mode="Markdown")

async def kurir_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Kurir yang didukung:\n" + ", ".join(KURIR_VALID))

async def track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Format: /track <kurir> <resi> [label]")
        return

    courier, resi = args[0].lower(), args[1]
    label = " ".join(args[2:]) if len(args) > 2 else None

    if courier == "spx":
        await update.message.reply_text(
            "⚠️ SPX belum didukung otomatis. Cek manual di https://spx.co.id"
        )
        return
    if courier not in KURIR_VALID:
        await update.message.reply_text(f"Kurir '{courier}' tidak dikenali. Ketik /kurir.")
        return

    await update.message.reply_text("🔍 Mengecek resi...")
    hasil = await cek_resi(courier, resi)

    if not hasil["success"]:
        await update.message.reply_text(f"❌ Gagal: {hasil['status']}")
        return

    if not add_resi(update.effective_chat.id, courier, resi, label):
        await update.message.reply_text("⚠️ Resi ini sudah dipantau.")
        return

    await update.message.reply_text(
        f"✅ Ditambahkan!\nKurir: {courier.upper()}\nResi: {resi}\n"
        f"Label: {label or '-'}\nStatus: {hasil['status']}"
    )

async def cek_sekali(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Format: /cek <kurir> <resi>")
        return
    courier, resi = args[0].lower(), args[1]
    await update.message.reply_text("🔍 Mengecek...")
    hasil = await cek_resi(courier, resi)
    await update.message.reply_text(
        f"📦 {hasil['status']}" if hasil["success"] else f"❌ {hasil['status']}"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = get_all_resi(update.effective_chat.id)
    if not rows:
        await update.message.reply_text("Belum ada paket dipantau. Pakai /track.")
        return
    teks = "📋 *Daftar paket:*\n\n"
    for _, _, courier, resi, label, last_status in rows:
        teks += f"• {label or resi} ({courier.upper()})\n  Resi: {resi}\n  Status: {last_status or 'Belum dicek'}\n\n"
    await update.message.reply_text(teks, parse_mode="Markdown")

async def untrack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Format: /untrack <resi>")
        return
    ok = remove_resi(update.effective_chat.id, args[0])
    await update.message.reply_text("🗑️ Dihapus." if ok else "Resi tidak ditemukan.")

async def map_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Format: /map <resi>")
        return
    resi = args[0]
    rows = get_all_resi(update.effective_chat.id)
    match = next((r for r in rows if r[3] == resi), None)
    if not match:
        await update.message.reply_text("Resi tidak ditemukan di daftar pantauan.")
        return
    _, _, courier, resi, label, last_status = match
    city = extract_city(last_status)
    if not city:
        await update.message.reply_text(f"Status: {last_status}\n(Tidak bisa deteksi nama kota untuk map)")
        return
    coords = await asyncio.to_thread(geocode_city, city)
    if not coords:
        await update.message.reply_text(f"Terdeteksi kota: {city}, tapi gagal geocode.")
        return
    lat, lon = coords
    await update.message.reply_location(latitude=lat, longitude=lon)
    await update.message.reply_text(f"📍 Perkiraan lokasi checkpoint terakhir: {city}\nStatus: {last_status}")

# ---------- Job berkala (paralel + dibatasi semaphore) ----------

async def cek_berkala(context: ContextTypes.DEFAULT_TYPE):
    rows = get_all_resi()
    semaphore = asyncio.Semaphore(MAX_PARALEL)

    async def proses_satu(row):
        row_id, chat_id, courier, resi, label, last_status = row
        async with semaphore:
            hasil = await cek_resi(courier, resi)
        if not hasil["success"]:
            return
        status_baru = hasil["status"]
        if status_baru != last_status:
            update_status(row_id, status_baru)
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"🔔 *Update paket!*\n{label or resi} ({courier.upper()})\nStatus baru: {status_baru}",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Gagal kirim notifikasi ke {chat_id}: {e}")

    await asyncio.gather(*(proses_satu(row) for row in rows))

# ---------- Main ----------

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN belum diisi di .env")

    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("kurir", kurir_list))
    app.add_handler(CommandHandler("track", track))
    app.add_handler(CommandHandler("cek", cek_sekali))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("untrack", untrack))
    app.add_handler(CommandHandler("map", map_location))

    app.job_queue.run_repeating(cek_berkala, interval=CEK_INTERVAL_DETIK, first=60)

    logger.info("Bot berjalan...")
    app.run_polling()

if __name__ == "__main__":
    main()