"""
Bot Telegram - Mpanisa Mpanatrika
Vendredi 17:50 accueil – 19:30
Dimanche 10:20 accueil – 12:00
Fuseau horaire : Indian/Antananarivo (UTC+3) — Madagascar
"""

import logging
import json
import os
import re
import threading
import urllib.parse
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, time
import pytz
from groq import Groq

from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ─── Configuration ────────────────────────────────────────────────────────────

TOKEN              = os.environ.get("BOT_TOKEN", "VOTRE_TOKEN_ICI")
GROUP_ID           = int(os.environ.get("GROUP_ID", "0"))
GROQ_KEY           = os.environ.get("GROQ_API_KEY", "")
ADMIN_ID           = int(os.environ.get("ADMIN_ID", "0"))
CALLMEBOT_USER     = os.environ.get("CALLMEBOT_USER", "@nfj_06")
TIMEZONE           = pytz.timezone("Indian/Antananarivo")

DATA_FILE     = "data.json"
MIN_COUNT     = 1
MAX_COUNT     = 30
SALLE_MAX     = 20

SCHEDULE = {
    4: (time(17, 45), time(19, 30)),  # Vendredi
    6: (time(10, 15), time(12,  0)),  # Dimanche
}

DAY_MG = {4: "Zoma", 6: "Alahady"}

CALLMEBOT_MESSAGE = "Attention ! Des membres signalent que le livestream ne fonctionne plus."

# ─── Mini serveur HTTP (pour Render) ──────────────────────────────────────────

class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()
    def do_POST(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args):
        pass

def run_http_server():
    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(("0.0.0.0", port), PingHandler)
    server.serve_forever()

threading.Thread(target=run_http_server, daemon=True).start()

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── Groq client ──────────────────────────────────────────────────────────────

groq_client = Groq(api_key=GROQ_KEY) if GROQ_KEY else None

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
            "count_message_id": None,
            "alert_message_id": None,
            "alert_reporters": {},
            "button_message_id": None,
        }
    return data["session"]

# ─── Utilitaires ──────────────────────────────────────────────────────────────

def parse_with_groq(text: str, old_sum: int) -> int | None:
    if not groq_client:
        return None
    try:
        prompt = (
            f"Ianao dia mpanampy amin'ny fisisana ny isan'ny olona. "
            f"Ny isa ankehitriny dia {old_sum}. "
            f"Ny hafatra alefan'ny mpampiasa : \"{text}\"\n\n"
            f"Io hafatra io dia mety ho amin'ny teny malagasy, frantsay, na isa fotsiny. "
            f"Ny teny hoe 'zay' dia teny fanamarihana fotsiny, tsy misy dikany amin'ny isa.\n\n"
            f"Fantaro ny isa vaovao araka izao fitsipika izao :\n\n"
            f"1) FAMPITOMBOANA — avereno {old_sum} + N :\n"
            f"   Famantarana : '+N', 'miampy N', 'nanampy N', 'fanampiny N', 'plus N', 'tonga N hafa', 'sy N', 'ary N'\n"
            f"   Ohatra : '+1 zay' → {old_sum + 1} | 'miampy 3' → {old_sum + 3}\n\n"
            f"2) FAMPIHENANA — avereno {old_sum} - N (tsy latsaka ny 0) :\n"
            f"   Famantarana : 'mihena N', 'miala N', 'lasa N' tsy voamariky ho total, '-N'\n"
            f"   Ohatra : 'mihena 2' → {max(0, old_sum - 2)} | 'miala 1' → {max(0, old_sum - 1)}\n\n"
            f"3) ISA VAOVAO MIVANTANA — avereno N mivantana :\n"
            f"   Famantarana : isa fotsiny, 'misy N', 'izahay N', 'isika N', 'sisa N', 'nandao N', 'lasa N' (= total ankehitriny N)\n"
            f"   Ohatra : 'misy 5' → 5 | 'lasa 3' → 3 | 'sisa 4 zay' → 4\n\n"
            f"   FITSIPIKA : 'lasa N' irery (tsy misy hafatra hafa) = ISA VAOVAO MIVANTANA, tsy fampihenana.\n\n"
            f"4) NULL — raha tsy misy isa na teny manondro isa ny hafatra.\n\n"
            f"FITSIPIKA MATOTRA :\n"
            f"  - Raha misy '+' alohan'ny isa → FAMPITOMBOANA FOANA\n"
            f"  - Raha misy '-' alohan'ny isa → FAMPIHENANA FOANA\n"
            f"  - Ny valiny dia tsy azo latsaka noho ny 0\n\n"
            f"Valiony ISA iray ihany na NULL, tsy misy inona hafa."
        )
        response = groq_client.chat.completions.create(
            model="llama3-8b-8192",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=10,
            temperature=0,
        )
        result = response.choices[0].message.content.strip()
        if result.upper() == "NULL":
            return None
        return max(0, int(result))
    except Exception as e:
        logger.warning(f"Groq error: {e}")
        return None

def extract_number(text: str, old_sum: int = 0) -> int | None:
    result = parse_with_groq(text, old_sum)
    if result is not None:
        return result

    text_clean = text.replace(",", ".").strip().lower()
    text_clean = re.sub(r"\bzay\b", "", text_clean).strip()

    plus_match = re.search(r"(?<!\d)\+\s*(\d+)", text_clean)
    if plus_match:
        return old_sum + int(plus_match.group(1))

    minus_match = re.search(r"(?<!\d)-\s*(\d+)", text_clean)
    if minus_match:
        return max(0, old_sum - int(minus_match.group(1)))

    match = re.search(r"\b(\d+)\b", text_clean)
    if not match:
        return None
    n = int(match.group(1))
    if n <= 0:
        return None

    ADD_KEYWORDS = ["miampy", "nanampy", "fanampiny", "plus", "tonga", "sy", "ary"]
    if any(w in text_clean for w in ADD_KEYWORDS):
        return old_sum + n

    SUB_KEYWORDS = ["mihena", "miala"]
    if any(w in text_clean for w in SUB_KEYWORDS):
        return max(0, old_sum - n)

    return n

def format_date_mg(now: datetime) -> str:
    months_mg = {
        1:"Janoary",2:"Febroary",3:"Martsa",4:"Aprily",
        5:"Mey",6:"Jona",7:"Jolay",8:"Aogositra",
        9:"Septambra",10:"Oktobra",11:"Novambra",12:"Desambra",
    }
    day_mg = DAY_MG.get(now.weekday(), "")
    return f"{now.day} {months_mg[now.month]} {now.year}  |  {day_mg}"

def escape_md(text: str) -> str:
    """Échappe les caractères spéciaux MarkdownV2."""
    for ch in ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']:
        text = text.replace(ch, '\\' + ch)
    return text

def build_list(participants: dict) -> str:
    return "\n".join(
        f"▸ *{escape_md(v['name'])}*    {v['sum']}"
        for v in participants.values()
    )

def build_alert_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📡 Tapaka ny fivoriana", callback_data="live_coupe")]
    ])

def build_alert_text(reporters: dict) -> str:
    names = ", ".join(f"*{escape_md(v['name'])}*" for v in reporters.values())
    return (
        f"🔴 Tapaka ny Livestream\\! \\({names}\\)\\. "
        f"Miandrasa kely azafady\\. _\\(L'admin a été notifié par appel\\)_"
    )

def call_callmebot():
    """Déclenche un appel vocal via CallMeBot."""
    try:
        text_encoded = urllib.parse.quote(CALLMEBOT_MESSAGE)
        url = f"https://api.callmebot.com/start.php?user={CALLMEBOT_USER}&text={text_encoded}&lang=fr-FR-Standard-A&rpt=2"
        urllib.request.urlopen(url, timeout=10)
        logger.info("Appel CallMeBot déclenché avec succès.")
    except Exception as e:
        logger.warning(f"CallMeBot error: {e}")

# ─── Jobs ─────────────────────────────────────────────────────────────────────

async def job_start_session(context):
    """Message d'accueil seul + message bouton séparé épinglé."""
    bot: Bot = context.bot
    data = load_data()
    session = get_session(data)
    session["active"]            = True
    session["total"]             = 0
    session["participants"]      = {}
    session["count_message_id"]  = None
    session["alert_message_id"]  = None
    session["alert_reporters"]   = {}
    session["button_message_id"] = None
    save_data(data)

    # 1. Message d'accueil seul
    await bot.send_message(
        chat_id=GROUP_ID,
        text=(
            "🙏 *Salama daholo* 👋\n\n"
            "Ankasitrahana raha alefa mialoha ny isa 😁"
        ),
        parse_mode="Markdown",
    )

    # 2. Message séparé avec uniquement le bouton
    button_msg = await bot.send_message(
        chat_id=GROUP_ID,
        text="_Raha sanatria tapaka ny fivoriana, tsindrio eto ambany\\._",
        parse_mode="MarkdownV2",
        reply_markup=build_alert_keyboard(),
    )
    session["button_message_id"] = button_msg.message_id

    # 3. Épingler le message bouton sans notifier les membres
    await bot.pin_chat_message(
        chat_id=GROUP_ID,
        message_id=button_msg.message_id,
        disable_notification=True,
    )

    # 4. Supprimer la notification d'épinglage dans le chat
    try:
        await bot.delete_message(
            chat_id=GROUP_ID,
            message_id=button_msg.message_id + 1,
        )
    except Exception:
        pass

    save_data(data)
    logger.info("Session démarrée — message d'accueil + bouton épinglé.")


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

    # Supprimer le message de comptage
    msg_id = session.get("count_message_id")
    if msg_id:
        try:
            await bot.delete_message(chat_id=GROUP_ID, message_id=msg_id)
        except Exception:
            pass
    session["count_message_id"] = None

    # Supprimer le message de signalement
    alert_id = session.get("alert_message_id")
    if alert_id:
        try:
            await bot.delete_message(chat_id=GROUP_ID, message_id=alert_id)
        except Exception:
            pass
    session["alert_message_id"] = None
    session["alert_reporters"]  = {}

    # Dépingler et supprimer le message bouton
    button_id = session.get("button_message_id")
    if button_id:
        try:
            await bot.unpin_chat_message(chat_id=GROUP_ID, message_id=button_id)
        except Exception:
            pass
        try:
            await bot.delete_message(chat_id=GROUP_ID, message_id=button_id)
        except Exception:
            pass
    session["button_message_id"] = None

    save_data(data)

    if not participants:
        text = (
            f"📅 *{date_str}*\n\n"
            "📊 _Tsy nisy isa nandefa anio._\n\n"
            "🙏 *Mankasitraka* !"
        )
    else:
        text = (
            f"🗓 *{date_str}*\n\n"
            f"{build_list(participants)}\n\n"
            f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
            f"*Total  →  {total}*\n\n"
            f"🙏 *Mankasitraka* tamin'ny nanatrehana ! ☺️"
        )

    await bot.send_message(chat_id=GROUP_ID, text=text, parse_mode="Markdown")
    logger.info(f"Session terminée — {total} mpanatrika | {date_str}")


# ─── Scheduler ────────────────────────────────────────────────────────────────

async def job_scheduler(context):
    now   = datetime.now(TIMEZONE)
    sched = SCHEDULE.get(now.weekday())
    if not sched:
        return

    accueil_t, end_t = sched
    h, m = now.hour, now.minute

    if h == accueil_t.hour and m == accueil_t.minute:
        await job_start_session(context)

    if h == end_t.hour and m == end_t.minute:
        await job_end_session(context)


# ─── Commandes ────────────────────────────────────────────────────────────────

async def _send(context, text, parse_mode="Markdown"):
    await context.bot.send_message(chat_id=GROUP_ID, text=text, parse_mode=parse_mode)


async def _delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.bot.delete_message(
            chat_id=update.effective_chat.id,
            message_id=update.effective_message.message_id,
        )
    except Exception as e:
        logger.warning(f"_delete_cmd error: {e}")


async def _check_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(GROUP_ID, update.effective_user.id)
        if member.status not in ("administrator", "creator"):
            return False
        return True
    except Exception as e:
        logger.warning(f"_check_admin error: {e}")
        return False


async def cmd_debut(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_admin(update, context):
        return
    try:
        await job_start_session(context)
        await _delete_cmd(update, context)
    except Exception as e:
        logger.error(f"cmd_debut error: {e}")
        await _send(context, f"❌ Nisy olana: {e}")
        await _delete_cmd(update, context)

async def cmd_fin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_admin(update, context):
        return
    try:
        await job_end_session(context)
        await _delete_cmd(update, context)
    except Exception as e:
        logger.error(f"cmd_fin error: {e}")
        await _send(context, f"❌ Nisy olana: {e}")
        await _delete_cmd(update, context)

async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_admin(update, context):
        return
    try:
        data = load_data()
        data["session"] = {}
        save_data(data)
        await _send(context, "✅ Voasasa ny session.")
        await _delete_cmd(update, context)
    except Exception as e:
        logger.error(f"cmd_reset error: {e}")
        await _send(context, f"❌ Nisy olana: {e}")
        await _delete_cmd(update, context)

async def cmd_ok(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin : /ok — réinitialise le signalement live coupé."""
    if not await _check_admin(update, context):
        return
    try:
        data    = load_data()
        session = get_session(data)

        alert_id = session.get("alert_message_id")
        if alert_id:
            try:
                await context.bot.delete_message(chat_id=GROUP_ID, message_id=alert_id)
            except Exception:
                pass

        session["alert_message_id"] = None
        session["alert_reporters"]  = {}
        save_data(data)

        await _delete_cmd(update, context)
        logger.info("Signalement live réinitialisé par admin.")
    except Exception as e:
        logger.error(f"cmd_ok error: {e}")
        await _send(context, f"❌ Nisy olana: {e}")
        await _delete_cmd(update, context)


async def cmd_modifier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_admin(update, context):
        return
    try:
        args = context.args
        if len(args) < 2:
            await _send(context, "❌ Fampiasana : /modifier nom 5")
            await _delete_cmd(update, context)
            return

        new_number = int(args[-1])
        search = " ".join(args[:-1]).lstrip("@").lower()

        data    = load_data()
        session = get_session(data)

        if not session["active"]:
            await _send(context, "❌ Tsy misy session mavitrika.")
            await _delete_cmd(update, context)
            return

        participants = session["participants"]
        found_id = None

        if search.isdigit():
            idx = int(search) - 1
            keys = list(participants.keys())
            if 0 <= idx < len(keys):
                found_id = keys[idx]
        else:
            for uid, v in participants.items():
                if search in v["name"].lower():
                    found_id = uid
                    break

        if not found_id:
            await _send(context, f"❌ Tsy hita : *{search}*")
            await _delete_cmd(update, context)
            return

        old_number = session["participants"][found_id]["sum"]
        session["participants"][found_id]["sum"] = new_number
        session["total"] = sum(v["sum"] for v in session["participants"].values())
        save_data(data)

        await _send(context,
            f"✅ *{session['participants'][found_id]['name']}* : {old_number} ➜ *{new_number}*",
        )
        await _delete_cmd(update, context)
    except ValueError:
        await _send(context, "❌ Ny isa dia tsy mety.")
        await _delete_cmd(update, context)
    except Exception as e:
        await _send(context, f"❌ Nisy olana: {e}")
        await _delete_cmd(update, context)


async def cmd_supprimer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_admin(update, context):
        return
    try:
        if not context.args:
            await _send(context, "❌ Fampiasana : /supprimer nom")
            await _delete_cmd(update, context)
            return

        search = " ".join(context.args).lstrip("@").lower()

        data    = load_data()
        session = get_session(data)

        if not session["active"]:
            await _send(context, "❌ Tsy misy session mavitrika.")
            await _delete_cmd(update, context)
            return

        participants = session["participants"]
        found_id = None

        if search.isdigit():
            idx = int(search) - 1
            keys = list(participants.keys())
            if 0 <= idx < len(keys):
                found_id = keys[idx]
        else:
            for uid, v in participants.items():
                if search in v["name"].lower():
                    found_id = uid
                    break

        if not found_id:
            await _send(context, f"❌ Tsy hita : *{search}*")
            await _delete_cmd(update, context)
            return

        name = session["participants"][found_id]["name"]
        del session["participants"][found_id]
        session["total"] = sum(v["sum"] for v in session["participants"].values())
        save_data(data)

        await _send(context, f"🗑️ *{name}* nesorina.")
        await _delete_cmd(update, context)
    except Exception as e:
        await _send(context, f"❌ Nisy olana: {e}")
        await _delete_cmd(update, context)


# ─── Callback bouton live coupé ───────────────────────────────────────────────

async def callback_live_coupe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user    = query.from_user
    user_id = str(user.id)
    name    = user.full_name or user.username or f"User{user.id}"

    data    = load_data()
    session = get_session(data)

    if not session["active"]:
        return

    reporters = session.get("alert_reporters", {})
    session["alert_reporters"] = reporters

    # Ignorer si déjà signalé par ce membre
    if user_id in reporters:
        return

    reporters[user_id] = {"name": name}
    now      = datetime.now(TIMEZONE)
    count    = len(reporters)
    time_str = now.strftime("%Hh%M")

    # Supprimer l'ancien message de signalement
    alert_id = session.get("alert_message_id")
    if alert_id:
        try:
            await context.bot.delete_message(chat_id=GROUP_ID, message_id=alert_id)
        except Exception:
            pass

    # Envoyer nouveau message de signalement dans le groupe
    sent = await context.bot.send_message(
        chat_id=GROUP_ID,
        text=build_alert_text(reporters),
        parse_mode="MarkdownV2",
    )
    session["alert_message_id"] = sent.message_id
    save_data(data)

    # Envoyer message privé à l'admin
    if ADMIN_ID:
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    f"⚠️ *Tapaka ny Livestream\\!*\n"
                    f"Signalé par *{count}* membre{'s' if count > 1 else ''} — {time_str}"
                ),
                parse_mode="MarkdownV2",
            )
        except Exception as e:
            logger.warning(f"Erreur envoi message privé admin: {e}")

    # Appel CallMeBot uniquement pour le premier signalement
    if count == 1:
        threading.Thread(target=call_callmebot, daemon=True).start()

    logger.info(f"Live coupé signalé par {name} ({count} signalement(s)) — {time_str}")


# ─── Handler messages ─────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message or update.edited_message
    if not message or not message.text:
        return

    if message.chat.id != GROUP_ID:
        return

    data    = load_data()
    session = get_session(data)
    if not session["active"]:
        return

    user    = message.from_user
    user_id = str(user.id)
    name    = user.full_name or user.username or f"User{user.id}"

    already_sent = user_id in session["participants"]
    old_sum      = session["participants"][user_id]["sum"] if already_sent else 0

    number = extract_number(message.text, old_sum)
    if number is None:
        return

    if number < MIN_COUNT or number > MAX_COUNT:
        await context.bot.send_message(
            chat_id=GROUP_ID,
            text=f"⚠️ Ny isa dia tsy maintsy eo anelanelan'ny *{MIN_COUNT}* sy *{MAX_COUNT}* olona.",
            parse_mode="Markdown",
        )
        return

    session["participants"][user_id] = {
        "name":  name,
        "sum":   number,
    }
    session["total"] = sum(v["sum"] for v in session["participants"].values())
    save_data(data)

    total        = session["total"]
    participants = session["participants"]
    text = (
        f"📊 *Isa amin'izao : {total}*\n\n"
        f"{build_list(participants)}"
    )

    bot = context.bot
    msg_id = session.get("count_message_id")
    if msg_id:
        try:
            await bot.delete_message(chat_id=GROUP_ID, message_id=msg_id)
        except Exception:
            pass
    sent = await bot.send_message(chat_id=GROUP_ID, text=text, parse_mode="Markdown")
    session["count_message_id"] = sent.message_id
    save_data(data)


async def handle_edited_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await handle_message(update, context)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    logger.info("Démarrage du bot Mpanisa Mpanatrika...")
    app = ApplicationBuilder().token(TOKEN).build()

    group_filter = filters.Chat(GROUP_ID)

    app.add_handler(CommandHandler("debut",      cmd_debut,     filters=group_filter))
    app.add_handler(CommandHandler("fin",        cmd_fin,       filters=group_filter))
    app.add_handler(CommandHandler("reset",      cmd_reset,     filters=group_filter))
    app.add_handler(CommandHandler("ok",         cmd_ok,        filters=group_filter))
    app.add_handler(CommandHandler("modifier",   cmd_modifier,  filters=group_filter))
    app.add_handler(CommandHandler("supprimer",  cmd_supprimer, filters=group_filter))

    app.add_handler(CallbackQueryHandler(callback_live_coupe, pattern="^live_coupe$"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & group_filter, handle_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & group_filter & filters.UpdateType.EDITED_MESSAGE, handle_edited_message))

    app.job_queue.run_repeating(job_scheduler, interval=60, first=3)

    logger.info("Bot en ligne ✅")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
