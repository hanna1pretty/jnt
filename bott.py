import os
import re
import time
import platform
import logging
import asyncio
import psutil
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

from database import init_db, add_resi, remove_resi, get_all_resi, update_status, set_config, get_config
from binderbyte_api import cek_resi, format_history
from geocode import geocode_city, resolve_hub_code

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_CHAT_ID = os.getenv("OWNER_CHAT_ID")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

KURIR_VALID = ["jne", "pos", "tiki", "sicepat", "anteraja", "lion", "ninja", "sap", "ide", "jnt", "wahana", "spx"]
CEK_INTERVAL_DETIK = 3 * 3600
MAX_PARALEL = 5

CITY_PATTERN = re.compile(r"\[([A-Z\s,]+)\]|di\s+([A-Za-z\s]+)$")
FRAME_LOADING = ["🔍 Mengecek", "🔍 Mengecek.", "🔍 Mengecek..", "🔍 Mengecek..."]

BOT_START_TIME = time.time()


def extract_city(status_text: str):
    if not status_text:
        return None
    match = CITY_PATTERN.search(status_text)
    if match:
        return (match.group(1) or match.group(2) or "").strip()
    return None


def get_active_api_key():
    return get_config("binderbyte_api_key", os.getenv("BINDERBYTE_API_KEY"))


def is_owner(update: Update) -> bool:
    if not OWNER_CHAT_ID:
        return False
    return str(update.effective_chat.id) == str(OWNER_CHAT_ID)


def main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("📋 Paket Saya", callback_data="status"),
         InlineKeyboardButton("🚚 Kurir Didukung", callback_data="kurir")],
        [InlineKeyboardButton("🏓 Ping Bot", callback_data="ping"),
         InlineKeyboardButton("❓ Bantuan Lengkap", callback_data="help")],
    ]
    return InlineKeyboardMarkup(keyboard)


def format_uptime(seconds: float) -> str:
    seconds = int(seconds)
    hari, sisa = divmod(seconds, 86400)
    jam, sisa = divmod(sisa, 3600)
    menit, detik = divmod(sisa, 60)
    parts = []
    if hari: parts.append(f"{hari}h")
    if jam: parts.append(f"{jam}j")
    if menit: parts.append(f"{menit}m")
    parts.append(f"{detik}d")
    return " ".join(parts)


async def animasi_loading(message):
    msg = await message.reply_text(FRAME_LOADING[0])

    async def animate():
        i = 0
        try:
            while True:
                await asyncio.sleep(0.6)
                i = (i + 1) % len(FRAME_LOADING)
                await msg.edit_text(FRAME_LOADING[i])
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    task = asyncio.create_task(animate())
    return msg, task


async def selesai_animasi(msg, task, teks_akhir):
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await msg.edit_text(teks_akhir, parse_mode="Markdown")


def teks_help():
    return (
        "📦 *Bot Cek Resi — Panduan*\n\n"
        "/track `<kurir>` `<resi>` `[label]` — mulai pantau paket\n"
        "   contoh: `/track jnt JY1007603351 Sepatu`\n"
        "   contoh SPX: `/track spx SPXID048949914625 Baju`\n\n"
        "/status — lihat semua paket yang dipantau\n"
        "/cek `<kurir>` `<resi>` — cek sekali tanpa disimpan\n"
        "/untrack `<resi>` — berhenti memantau\n"
        "/map `<resi>` — kirim titik lokasi checkpoint terakhir\n"
        "/kurir — daftar kode kurir yang didukung\n"
        "/ping — cek respons bot & kondisi server\n"
        "/setapikey `<key>` — ganti API key BinderByte (khusus owner)\n"
        "/help — tampilkan pesan ini\n\n"
        "✅ SPX (Shopee Express) kini sudah didukung otomatis!\n"
        f"🔄 Bot otomatis cek ulang tiap {CEK_INTERVAL_DETIK // 3600} jam dan kirim notifikasi kalau status berubah."
    )


# ---------- Command handlers ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Selamat datang di Resi Tracker Bot!*\n\n"
        "Pantau semua paketmu tanpa perlu buka aplikasi marketplace.\n"
        "Pilih menu di bawah, atau ketik /help untuk daftar lengkap perintah.",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(teks_help(), parse_mode="Markdown", reply_markup=main_menu_keyboard())

async def kurir_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚚 Kurir yang didukung:\n" + ", ".join(k.upper() for k in KURIR_VALID))

async def setapikey(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await update.message.reply_text("⛔ Perintah ini hanya untuk pemilik bot.")
        return
    args = context.args
    if not args:
        await update.message.reply_text("Format: /setapikey <api_key_baru>")
        return
    new_key = args[0]
    set_config("binderbyte_api_key", new_key)
    try:
        await update.message.delete()
    except Exception:
        pass
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="✅ API key BinderByte berhasil diperbarui dan langsung aktif (tanpa restart)."
    )

async def track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Format: /track <kurir> <resi> [label]")
        return

    courier, resi = args[0].lower(), args[1]
    label = " ".join(args[2:]) if len(args) > 2 else None

    if courier not in KURIR_VALID:
        await update.message.reply_text(f"Kurir '{courier}' tidak dikenali. Ketik /kurir.")
        return

    msg, task = await animasi_loading(update.message)
    hasil = await cek_resi(courier, resi, api_key=get_active_api_key())

    if not hasil["success"]:
        await selesai_animasi(msg, task, f"❌ Gagal: {hasil['status']}")
        return

    if not add_resi(update.effective_chat.id, courier, resi, label):
        await selesai_animasi(msg, task, "⚠️ Resi ini sudah dipantau.")
        return

    d = hasil["detail"]
    teks_riwayat = format_history(hasil["history"])
    teks = (
        f"✅ *Berhasil ditambahkan!*\n\n"
        f"📦 Kurir: {d['courier_name']} ({d['service'] or '-'})\n"
        f"Resi: `{resi}`\n"
        f"Label: {label or '-'}\n"
        f"Berat: {d['weight']} gram\n\n"
        f"📍 *Status saat ini:* {hasil['status']}\n"
        f"_{d['last_desc']}_\n"
        f"Update terakhir: {d['last_date']}\n\n"
        f"🧾 *Riwayat perjalanan:*\n{teks_riwayat}"
    )
    await selesai_animasi(msg, task, teks)

async def cek_sekali(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Format: /cek <kurir> <resi>")
        return
    courier, resi = args[0].lower(), args[1]

    if courier not in KURIR_VALID:
        await update.message.reply_text(f"Kurir '{courier}' tidak dikenali. Ketik /kurir.")
        return

    msg, task = await animasi_loading(update.message)
    hasil = await cek_resi(courier, resi, api_key=get_active_api_key())

    if not hasil["success"]:
        await selesai_animasi(msg, task, f"❌ {hasil['status']}")
        return

    d = hasil["detail"]
    teks_riwayat = format_history(hasil["history"])
    teks = (
        f"📦 Kurir: {d['courier_name']} ({d['service'] or '-'})\n"
        f"Resi: `{resi}`\n\n"
        f"📍 *Status:* {hasil['status']}\n"
        f"_{d['last_desc']}_\n"
        f"Update terakhir: {d['last_date']}\n\n"
        f"🧾 *Riwayat:*\n{teks_riwayat}"
    )
    await selesai_animasi(msg, task, teks)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = get_all_resi(update.effective_chat.id)
    if not rows:
        await update.message.reply_text("Belum ada paket dipantau. Pakai /track.", reply_markup=main_menu_keyboard())
        return
    teks = "📋 *Daftar paket:*\n\n"
    for _, _, courier, resi, label, last_status in rows:
        teks += f"• {label or resi} ({courier.upper()})\n  Resi: `{resi}`\n  Status: {last_status or 'Belum dicek'}\n\n"
    await update.message.reply_text(teks, parse_mode="Markdown", reply_markup=main_menu_keyboard())

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

    city = resolve_hub_code(last_status) or extract_city(last_status)
    if not city:
        await update.message.reply_text(
            f"Status: {last_status}\n(Kode hub belum ada di mapping — tambahkan manual di geocode.py)"
        )
        return
    coords = await asyncio.to_thread(geocode_city, city)
    if not coords:
        await update.message.reply_text(f"Terdeteksi kota: {city}, tapi gagal geocode.")
        return
    lat, lon = coords
    await update.message.reply_location(latitude=lat, longitude=lon)
    await update.message.reply_text(f"📍 Perkiraan lokasi checkpoint terakhir: {city}\nStatus: {last_status}")

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t0 = time.perf_counter()
    msg = await update.message.reply_text("🏓 Menghitung ping...")
    latency_ms = (time.perf_counter() - t0) * 1000

    cpu = psutil.cpu_percent(interval=0.5)
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    uptime = format_uptime(time.time() - BOT_START_TIME)

    teks = (
        f"🏓 *Pong!*\n\n"
        f"⚡ Latensi bot: `{latency_ms:.0f} ms`\n"
        f"⏱️ Uptime bot: `{uptime}`\n\n"
        f"🖥️ *Kondisi Server*\n"
        f"CPU: `{cpu:.1f}%`\n"
        f"RAM: `{ram.percent:.1f}%` ({ram.used // (1024**2)}MB / {ram.total // (1024**2)}MB)\n"
        f"Disk: `{disk.percent:.1f}%` ({disk.used // (1024**3)}GB / {disk.total // (1024**3)}GB)\n"
        f"OS: `{platform.system()} {platform.release()}`"
    )
    await msg.edit_text(teks, parse_mode="Markdown")


# ---------- Inline button callback ----------

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "status":
        rows = get_all_resi(update.effective_chat.id)
        if not rows:
            await query.message.reply_text("Belum ada paket dipantau. Pakai /track.")
            return
        teks = "📋 *Daftar paket:*\n\n"
        for _, _, courier, resi, label, last_status in rows:
            teks += f"• {label or resi} ({courier.upper()})\n  Resi: `{resi}`\n  Status: {last_status or 'Belum dicek'}\n\n"
        await query.message.reply_text(teks, parse_mode="Markdown")

    elif query.data == "kurir":
        await query.message.reply_text("🚚 Kurir yang didukung:\n" + ", ".join(k.upper() for k in KURIR_VALID))

    elif query.data == "help":
        await query.message.reply_text(teks_help(), parse_mode="Markdown")

    elif query.data == "ping":
        cpu = psutil.cpu_percent(interval=0.5)
        ram = psutil.virtual_memory()
        uptime = format_uptime(time.time() - BOT_START_TIME)
        teks = (
            f"🏓 *Pong!*\n\n"
            f"⏱️ Uptime: `{uptime}`\n"
            f"CPU: `{cpu:.1f}%` | RAM: `{ram.percent:.1f}%`"
        )
        await query.message.reply_text(teks, parse_mode="Markdown")


# ---------- Job berkala (paralel + dibatasi semaphore) ----------

async def cek_berkala(context: ContextTypes.DEFAULT_TYPE):
    rows = get_all_resi()
    semaphore = asyncio.Semaphore(MAX_PARALEL)
    api_key = get_active_api_key()

    async def proses_satu(row):
        row_id, chat_id, courier, resi, label, last_status = row
        async with semaphore:
            hasil = await cek_resi(courier, resi, api_key=api_key)
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
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("setapikey", setapikey))
    app.add_handler(CallbackQueryHandler(button_callback))

    app.job_queue.run_repeating(cek_berkala, interval=CEK_INTERVAL_DETIK, first=60)

    logger.info("Bot berjalan...")
    app.run_polling()

if __name__ == "__main__":
    main()
