"""
Bot Telegram - Mpanisa Mpivory
Vendredi 17:45–19:40 | Dimanche 10:00–12:05
Fuseau horaire : Indian/Antananarivo (UTC+3) — Madagascar
"""

import logging
import json
import os
import re
from datetime import datetime, time
import pytz

from telegram import Update, Bot
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ─── Configuration ────────────────────────────────────────────────────────────

TOKEN    = os.environ.get("BOT_TOKEN", "VOTRE_TOKEN_ICI")
GROUP_ID = int(os.environ.get("GROUP_ID", "0"))
TIMEZONE = pytz.timezone("Indian/Antananarivo")   # UTC+3 Madagascar

DATA_FILE      = "data.json"
INTER_MINUTES  = 45   # total intermédiaire après X min

# Planning : {weekday: (heure_début, heure_fin)}
# 4 = Vendredi | 6 = Dimanche
SCHEDULE = {
    4: (time(17, 45), time(19, 40)),
    6: (time(10,  0), time(12,  5)),
}

DAY_MG = {4: "Zoma", 6: "Alahady"}

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── Persistance JSON ─────────────────────────────────────────────────────────

def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_data(data: dict):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def session_key() -> str:
    now = datetime.now(TIMEZONE)
    return f"{now.strftime('%Y-%m-%d')}-{now.weekday()}"

def get_session(data: dict) -> dict:
    key = session_key()
    if "session" not in data or data["session"].get("key") != key:
        data["session"] = {
            "key": key,
            "active": False,
            "total": 0,
            "participants": {},
        }
    return data["session"]

# ─── Utilitaires ──────────────────────────────────────────────────────────────

def extract_number(text: str):
    text = text.replace(",", ".").strip()
    match = re.search(r"\b(\d+)\b", text)
    if match:
        n = int(match.group(1))
        return n if n > 0 else None
    return None

def format_date_mg(now: datetime) -> str:
    months_mg = {
        1:"Janoary",2:"Febroary",3:"Martsa",4:"Aprily",
        5:"Mey",6:"Jona",7:"Jolay",8:"Aogositra",
        9:"Septambra",10:"Oktobra",11:"Novambra",12:"Desambra",
    }
    day_mg = DAY_MG.get(now.weekday(), "")
    return f"{day_mg} {now.day} {months_mg[now.month]} {now.year}"

def build_list(participants: dict) -> str:
    return "\n".join(
        f"  • *{v['name']}* : {v['sum']} olona"
        for v in participants.values()
    )

# ─── Jobs ─────────────────────────────────────────────────────────────────────

async def job_start_session(context):
    bot: Bot = context.bot
    data = load_data()
    session = get_session(data)
    session["active"]       = True
    session["total"]        = 0
    session["participants"] = {}
    save_data(data)

    # Démarrer le salon vidéo
    try:
        await bot.create_video_chat(chat_id=GROUP_ID, title="Fivoriana 🙏")
        logger.info("Salon vidéo démarré.")
    except Exception as e:
        logger.warning(f"Salon vidéo : {e}")

    await bot.send_message(
        chat_id=GROUP_ID,
        text=(
            "🙏 *Salama daholo* 👋\n\n"
            "Ankasitrahana raha alefa mialoha ny isa 😁\n\n"
            "_Andefaso ny isan'ny olona ao aminao_\n"
            "_Ohatra : `6` na `izahay 6` na `izy mianaka 6`_"
        ),
        parse_mode="Markdown",
    )
    logger.info("Session démarrée.")


async def job_intermediaire(context):
    bot: Bot = context.bot
    data = load_data()
    session = get_session(data)
    if not session["active"]:
        return

    total        = session["total"]
    participants = session["participants"]

    if not participants:
        text = (
            "⏰ *Vokatra eo anelanelan'ny fivoriana* _(45 min)_\n\n"
            "📭 _Tsy nisy isa nandefa hatreto._"
        )
    else:
        text = (
            "⏰ *Vokatra eo anelanelan'ny fivoriana* _(45 min)_\n\n"
            f"{build_list(participants)}\n\n"
            f"👥 *Total ankehitriny : {total} olona*"
        )

    await bot.send_message(chat_id=GROUP_ID, text=text, parse_mode="Markdown")
    logger.info(f"Intermédiaire envoyé — {total} participants.")


async def job_end_session(context):
    bot: Bot = context.bot
    data = load_data()
    session = get_session(data)
    if not session["active"]:
        return

    session["active"] = False
    total        = session["total"]
    participants = session["participants"]
    now          = datetime.now(TIMEZONE)
    date_str     = format_date_mg(now)
    save_data(data)

    if not participants:
        text = (
            f"📅 *{date_str}*\n\n"
            "📊 _Tsy nisy isa nandefa anio._\n\n"
            "🙏 *Mankasitraka* !"
        )
    else:
        text = (
            f"📅 *{date_str}*\n\n"
            "🎉 *Vokatra farany — Isan'ny mpivory :*\n\n"
            f"{build_list(participants)}\n\n"
            f"👥 *Totalin'ny mpivory : {total} olona*\n\n"
            "🙏 *Mankasitraka* tamin'ny nanatrehana ! ❤️"
        )

    await bot.send_message(chat_id=GROUP_ID, text=text, parse_mode="Markdown")
    logger.info(f"Session terminée — {total} olona | {date_str}")


# ─── Scheduler 1×/minute ──────────────────────────────────────────────────────

async def job_scheduler(context):
    now   = datetime.now(TIMEZONE)
    sched = SCHEDULE.get(now.weekday())
    if not sched:
        return

    start_t, end_t = sched
    h, m = now.hour, now.minute

    if h == start_t.hour and m == start_t.minute:
        await job_start_session(context)

    inter_total   = start_t.hour * 60 + start_t.minute + INTER_MINUTES
    inter_h, inter_m = divmod(inter_total, 60)
    if h == inter_h and m == inter_m:
        await job_intermediaire(context)

    if h == end_t.hour and m == end_t.minute:
        await job_end_session(context)


# ─── Commandes ────────────────────────────────────────────────────────────────

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Bot Mpanisa Mpivory*\n\n"
        "Andefaso ny isan'ny olona ao aminao :\n"
        "`6` na `izahay 6` na `izy mianaka 6`\n\n"
        "*Baiko :*\n"
        "• /total — Total ankehitriny\n"
        "• /debut — Admin : atombohy ny session\n"
        "• /fin — Admin : afarano ny session\n"
        "• /reset — Admin : manomboka indray",
        parse_mode="Markdown",
    )

async def cmd_total(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data    = load_data()
    session = get_session(data)
    if not session["participants"]:
        await update.message.reply_text("📭 _Tsy nisy isa nandefa hatreto._", parse_mode="Markdown")
        return
    total = session["total"]
    await update.message.reply_text(
        f"📊 *Total ankehitriny : {total} olona*\n\n{build_list(session['participants'])}",
        parse_mode="Markdown",
    )

async def _check_admin(update: Update) -> bool:
    member = await update.effective_chat.get_member(update.effective_user.id)
    if member.status not in ("administrator", "creator"):
        await update.message.reply_text("🚫 Admin ihany no afaka manao izany.")
        return False
    return True

async def cmd_debut(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_admin(update): return
    await job_start_session(context)

async def cmd_fin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_admin(update): return
    await job_end_session(context)

async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_admin(update): return
    data = load_data()
    data["session"] = {}
    save_data(data)
    await update.message.reply_text("✅ Voasasa ny session.")

# ─── Handler messages ─────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text:
        return

    data    = load_data()
    session = get_session(data)
    if not session["active"]:
        return

    number = extract_number(message.text)
    if number is None:
        return

    user    = message.from_user
    user_id = str(user.id)
    name    = user.full_name or user.username or f"User{user.id}"

    already_sent = user_id in session["participants"]
    old_sum      = session["participants"][user_id]["sum"] if already_sent else 0

    session["participants"][user_id] = {
        "name":  name,
        "sum":   number,
        "count": (session["participants"][user_id]["count"] + 1) if already_sent else 1,
    }
    session["total"] = sum(v["sum"] for v in session["participants"].values())
    save_data(data)

    total = session["total"]

    if already_sent:
        await message.reply_text(
            f"🔄 *{name}* : novaina {old_sum} ➜ *{number} olona*\n"
            f"👥 *Total : {total} olona*",
            parse_mode="Markdown",
        )
    else:
        await message.reply_text(
            f"✅ *{name}* : *{number} olona*\n"
            f"👥 *Total ankehitriny : {total} olona*",
            parse_mode="Markdown",
        )

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    logger.info("Démarrage du bot Mpanisa Mpivory...")
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_help))
    app.add_handler(CommandHandler("help",  cmd_help))
    app.add_handler(CommandHandler("total", cmd_total))
    app.add_handler(CommandHandler("debut", cmd_debut))
    app.add_handler(CommandHandler("fin",   cmd_fin))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.job_queue.run_repeating(job_scheduler, interval=60, first=3)

    logger.info("Bot en ligne ✅")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
