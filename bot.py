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

BOT_START_TIME = time.time()

# ---------- Animasi loading ----------

FRAME_SPINNER = ["⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷"]
FRAME_TEXTS = [
    "▓░░░░░░░░░ 10%  Menghubungi server...",
    "▓▓▓░░░░░░░ 30%  Mengambil data kurir...",
    "▓▓▓▓▓░░░░░ 50%  Memproses tracking...",
    "▓▓▓▓▓▓▓░░░ 70%  Menyusun riwayat...",
    "▓▓▓▓▓▓▓▓▓░ 90%  Menyelesaikan...",
    "▓▓▓▓▓▓▓▓▓▓ 100% Selesai.",
]


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


def get_active_api_key():
    return get_config("binderbyte_api_key", os.getenv("BINDERBYTE_API_KEY"))


def is_owner(update: Update) -> bool:
    if not OWNER_CHAT_ID:
        return False
    return str(update.effective_chat.id) == str(OWNER_CHAT_ID)


def main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("📦 Paket Saya", callback_data="status", style="primary"),
         InlineKeyboardButton("🚚 Kurir Didukung", callback_data="kurir", style="primary")],
        [InlineKeyboardButton("📖 Panduan Lengkap", callback_data="help", style="primary")],
    ]
    return InlineKeyboardMarkup(keyboard)


def hasil_keyboard(resi: str, courier: str):
    keyboard = [
        [InlineKeyboardButton("🔄 Refresh Status", callback_data=f"refresh:{courier}:{resi}", style="primary"),
         InlineKeyboardButton("📍 Lihat Peta", callback_data=f"map:{resi}", style="success")],
        [InlineKeyboardButton("🗑️ Berhenti Pantau", callback_data=f"untrack:{resi}", style="danger")],
    ]
    return InlineKeyboardMarkup(keyboard)


async def animasi_loading(message):
    msg = await message.reply_text(f"{FRAME_SPINNER[0]} {FRAME_TEXTS[0]}")

    async def animate():
        i, j = 0, 0
        try:
            while True:
                await asyncio.sleep(0.35)
                i = (i + 1) % len(FRAME_SPINNER)
                j = min(j + 1, len(FRAME_TEXTS) - 1)
                await msg.edit_text(f"{FRAME_SPINNER[i]} {FRAME_TEXTS[j]}")
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    task = asyncio.create_task(animate())
    return msg, task


async def selesai_animasi(msg, task, teks_akhir, reply_markup=None):
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await msg.edit_text(teks_akhir, parse_mode="Markdown", reply_markup=reply_markup)


def status_emoji(status_text: str) -> str:
    s = (status_text or "").upper()
    if "DELIVERED" in s or "DITERIMA" in s or "SUCCESS" in s:
        return "✅"
    if "TRANSIT" in s or "PROSES" in s or "PROCESS" in s or "PERJALANAN" in s:
        return "🚚"
    if "GAGAL" in s or "FAILED" in s or "RETUR" in s or "RETURN" in s:
        return "❌"
    if "OUT FOR DELIVERY" in s or "PENGIRIMAN" in s:
        return "🛵"
    return "📦"


def buat_kartu_hasil(courier_display: str, resi: str, label: str, hasil: dict, disimpan: bool) -> str:
    d = hasil["detail"]
    emoji = status_emoji(hasil["status"])
    teks_riwayat = format_history(hasil["history"])

    header = "*Paket berhasil dipantau*" if disimpan else "*Hasil pengecekan*"

    kartu = f"{emoji} {header}\n\n"
    kartu += f"*Status:* {hasil['status']}\n"
    kartu += f"*Kurir:* {d['courier_name']}"
    if d.get("service") and d["service"] != "-":
        kartu += f" · _{d['service']}_"
    kartu += f"\n*Resi:* `{resi}`\n"

    if label:
        kartu += f"*Label:* {label}\n"
    if d.get("weight") and d["weight"] != "-":
        kartu += f"*Berat:* {d['weight']} gram\n"

    kartu += (
        f"\n_{d['last_desc']}_\n"
        f"Update terakhir: `{d['last_date']}`\n\n"
        f"*Riwayat perjalanan*\n{teks_riwayat}"
    )
    return kartu


def teks_help():
    return (
        "*Panduan Resi Tracker Bot*\n\n"
        "`/track <kurir> <resi> [label]`\n"
        "_Mulai pantau paket otomatis_\n"
        "Contoh: `/track jnt JY1007603351 Sepatu`\n"
        "Contoh SPX: `/track spx SPXID048949914625 Baju`\n\n"
        "`/status` — lihat semua paket yang dipantau\n"
        "`/cek <kurir> <resi>` — cek sekali tanpa disimpan\n"
        "`/untrack <resi>` — berhenti memantau\n"
        "`/map <resi>` — lokasi checkpoint terakhir\n"
        "`/kurir` — daftar kode kurir yang didukung\n"
        "`/ping` — cek respons bot & kondisi server\n"
        "`/setapikey <key>` — ganti API key _(khusus owner)_\n\n"
        "SPX (Shopee Express) sudah didukung otomatis.\n"
        f"Bot auto-cek tiap {CEK_INTERVAL_DETIK // 3600} jam dan kirim notifikasi bila status berubah."
    )


# ---------- Command handlers ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📦 *Resi Tracker Bot*\n\n"
        "Pantau semua paketmu tanpa perlu buka aplikasi marketplace.\n"
        "Pilih menu di bawah atau ketik /help untuk panduan lengkap.",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(teks_help(), parse_mode="Markdown", reply_markup=main_menu_keyboard())

async def kurir_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    teks = "*Kurir yang didukung*\n\n" + ", ".join(f"`{k.upper()}`" for k in KURIR_VALID)
    await update.message.reply_text(teks, parse_mode="Markdown")

async def setapikey(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await update.message.reply_text("Perintah ini hanya untuk pemilik bot.")
        return
    args = context.args
    if not args:
        await update.message.reply_text("Format: `/setapikey <api_key_baru>`", parse_mode="Markdown")
        return
    new_key = args[0]
    set_config("binderbyte_api_key", new_key)
    try:
        await update.message.delete()
    except Exception:
        pass
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="API key BinderByte berhasil diperbarui dan langsung aktif tanpa restart."
    )

async def track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Format: `/track <kurir> <resi> [label]`", parse_mode="Markdown")
        return

    courier, resi = args[0].lower(), args[1]
    label = " ".join(args[2:]) if len(args) > 2 else None

    if courier not in KURIR_VALID:
        await update.message.reply_text(f"Kurir '{courier}' tidak dikenali. Ketik /kurir.")
        return

    msg, task = await animasi_loading(update.message)
    hasil = await cek_resi(courier, resi, api_key=get_active_api_key())

    if not hasil["success"]:
        await selesai_animasi(msg, task, f"Gagal: {hasil['status']}")
        return

    if not add_resi(update.effective_chat.id, courier, resi, label):
        await selesai_animasi(msg, task, "Resi ini sudah dipantau sebelumnya.")
        return

    kartu = buat_kartu_hasil(courier.upper(), resi, label, hasil, disimpan=True)
    await selesai_animasi(msg, task, kartu, reply_markup=hasil_keyboard(resi, courier))

async def cek_sekali(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Format: `/cek <kurir> <resi>`", parse_mode="Markdown")
        return
    courier, resi = args[0].lower(), args[1]

    if courier not in KURIR_VALID:
        await update.message.reply_text(f"Kurir '{courier}' tidak dikenali. Ketik /kurir.")
        return

    msg, task = await animasi_loading(update.message)
    hasil = await cek_resi(courier, resi, api_key=get_active_api_key())

    if not hasil["success"]:
        await selesai_animasi(msg, task, hasil["status"])
        return

    kartu = buat_kartu_hasil(courier.upper(), resi, None, hasil, disimpan=False)
    await selesai_animasi(msg, task, kartu)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = get_all_resi(update.effective_chat.id)
    if not rows:
        await update.message.reply_text(
            "Belum ada paket dipantau.\nPakai `/track <kurir> <resi>` untuk mulai.",
            parse_mode="Markdown", reply_markup=main_menu_keyboard()
        )
        return

    teks = "*Daftar paket dipantau*\n\n"
    for _, _, courier, resi, label, last_status in rows:
        emoji = status_emoji(last_status)
        teks += (
            f"{emoji} *{label or resi}*\n"
            f"{courier.upper()} · `{resi}`\n"
            f"_{last_status or 'Belum dicek'}_\n\n"
        )
    await update.message.reply_text(teks, parse_mode="Markdown", reply_markup=main_menu_keyboard())

async def untrack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Format: `/untrack <resi>`", parse_mode="Markdown")
        return
    ok = remove_resi(update.effective_chat.id, args[0])
    await update.message.reply_text("Dihapus dari pantauan." if ok else "Resi tidak ditemukan.")

async def kirim_map(update_or_query, resi: str, chat_id: int):
    rows = get_all_resi(chat_id)
    match = next((r for r in rows if r[3] == resi), None)
    if not match:
        await update_or_query.reply_text("Resi tidak ditemukan di daftar pantauan.")
        return
    _, _, courier, resi, label, last_status = match

    city = resolve_hub_code(last_status)
    if not city:
        await update_or_query.reply_text(
            f"_{last_status}_\n\nKode lokasi belum dikenali sistem, belum bisa ditampilkan di peta.",
            parse_mode="Markdown"
        )
        return
    coords = await asyncio.to_thread(geocode_city, city)
    if not coords:
        await update_or_query.reply_text(f"Terdeteksi kota: {city}, tapi gagal geocode.")
        return
    lat, lon = coords
    await update_or_query.reply_location(latitude=lat, longitude=lon)
    await update_or_query.reply_text(f"*Perkiraan lokasi:* {city}\n_{last_status}_", parse_mode="Markdown")

async def map_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Format: `/map <resi>`", parse_mode="Markdown")
        return
    await kirim_map(update.message, args[0], update.effective_chat.id)

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t0 = time.perf_counter()
    msg = await update.message.reply_text("Mengukur latensi...")
    latency_ms = (time.perf_counter() - t0) * 1000

    cpu = psutil.cpu_percent(interval=0.5)
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    uptime = format_uptime(time.time() - BOT_START_TIME)

    teks = (
        "🏓 *Pong!*\n\n"
        f"Latensi bot: `{latency_ms:.0f} ms`\n"
        f"Uptime: `{uptime}`\n\n"
        "*Kondisi server*\n"
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
    data = query.data

    if data == "status":
        rows = get_all_resi(update.effective_chat.id)
        if not rows:
            await query.message.reply_text("Belum ada paket dipantau. Pakai /track.")
            return
        teks = "*Daftar paket dipantau*\n\n"
        for _, _, courier, resi, label, last_status in rows:
            emoji = status_emoji(last_status)
            teks += f"{emoji} *{label or resi}*\n{courier.upper()} · `{resi}`\n_{last_status or 'Belum dicek'}_\n\n"
        await query.message.reply_text(teks, parse_mode="Markdown")

    elif data == "kurir":
        teks = "*Kurir yang didukung*\n\n" + ", ".join(f"`{k.upper()}`" for k in KURIR_VALID)
        await query.message.reply_text(teks, parse_mode="Markdown")

    elif data == "help":
        await query.message.reply_text(teks_help(), parse_mode="Markdown")

    elif data.startswith("refresh:"):
        _, courier, resi = data.split(":", 2)
        msg_placeholder, task = await animasi_loading(query.message)
        hasil = await cek_resi(courier, resi, api_key=get_active_api_key())
        if not hasil["success"]:
            await selesai_animasi(msg_placeholder, task, f"Gagal: {hasil['status']}")
            return
        rows = get_all_resi(update.effective_chat.id)
        match = next((r for r in rows if r[3] == resi), None)
        label = match[4] if match else None
        if match:
            update_status(match[0], hasil["status"])
        kartu = buat_kartu_hasil(courier.upper(), resi, label, hasil, disimpan=True)
        await selesai_animasi(msg_placeholder, task, kartu, reply_markup=hasil_keyboard(resi, courier))

    elif data.startswith("map:"):
        resi = data.split(":", 1)[1]
        await kirim_map(query.message, resi, update.effective_chat.id)

    elif data.startswith("untrack:"):
        resi = data.split(":", 1)[1]
        ok = remove_resi(update.effective_chat.id, resi)
        await query.message.reply_text("Dihapus dari pantauan." if ok else "Resi tidak ditemukan.")


# ---------- Job berkala ----------

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
            emoji = status_emoji(status_baru)
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"🔔 *Update paket*\n"
                        f"{emoji} {label or resi} ({courier.upper()})\n"
                        f"Status baru: *{status_baru}*"
                    ),
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
