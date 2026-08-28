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
from datetime import datetime, time
import pytz
from groq import Groq

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
GROQ_KEY = os.environ.get("GROQ_API_KEY", "")
TIMEZONE = pytz.timezone("Indian/Antananarivo")

DATA_FILE = "data.json"
MIN_COUNT = 1
MAX_COUNT = 30

SCHEDULE = {
    4: (time(17, 45), time(19, 30)),  # Vendredi
    6: (time(10, 15), time(12,  0)),  # Dimanche
}

DAY_MG = {4: "Zoma", 6: "Alahady"}

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
            "welcome_message_id": None,
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

    # ── Fallback regex ────────────────────────────────────────────────────────
    text_clean = text.replace(",", ".").strip().lower()
    text_clean = re.sub(r"\bzay\b", "", text_clean).strip()

    plus_match = re.search(r"\+\s*(\d+)", text_clean)
    if plus_match:
        return old_sum + int(plus_match.group(1))

    minus_match = re.search(r"-\s*(\d+)", text_clean)
    if minus_match:
        return max(0, old_sum - int(minus_match.group(1)))

    match = re.search(r"(\d+)", text_clean)
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
        1:"Janoary", 2:"Febroary", 3:"Martsa",  4:"Aprily",
        5:"Mey",     6:"Jona",     7:"Jolay",    8:"Aogositra",
        9:"Septambra",10:"Oktobra",11:"Novambra",12:"Desambra",
    }
    return f"{now.day} {months_mg[now.month]} {now.year}"

def escape_md(text: str) -> str:
    for ch in ['_','*','[',']','(',')',  '~','`','>','#','+','-','=','|','{','}','.','!']:
        text = text.replace(ch, '\\' + ch)
    return text

# ─── Jobs ─────────────────────────────────────────────────────────────────────

async def job_start_session(context):
    bot: Bot = context.bot
    data = load_data()
    session = get_session(data)
    session["active"]             = True
    session["total"]              = 0
    session["participants"]       = {}
    session["count_message_id"]   = None
    session["welcome_message_id"] = None
    save_data(data)

    now      = datetime.now(TIMEZONE)
    date_str = format_date_mg(now)

    welcome_msg = await bot.send_message(
        chat_id=GROUP_ID,
        text=(
            f"🙏 *Salama daholo* 👋\n"
            f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
            f">Tongasoa amin'ny fivoriana androany — *{escape_md(date_str)}*\n"
            f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
            f"Ankasitrahana raha alefa mialoha ny isa 😁"
        ),
        parse_mode="MarkdownV2",
    )
    session["welcome_message_id"] = welcome_msg.message_id

    await bot.pin_chat_message(
        chat_id=GROUP_ID,
        message_id=welcome_msg.message_id,
        disable_notification=True,
    )
    try:
        await bot.delete_message(
            chat_id=GROUP_ID,
            message_id=welcome_msg.message_id + 1,
        )
    except Exception:
        pass

    save_data(data)
    logger.info("Session démarrée.")


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
    day_mg       = DAY_MG.get(now.weekday(), "")

    # Supprimer le message de comptage
    msg_id = session.get("count_message_id")
    if msg_id:
        try:
            await bot.delete_message(chat_id=GROUP_ID, message_id=msg_id)
        except Exception:
            pass
    session["count_message_id"] = None

    # Dépingler le message d'accueil
    welcome_id = session.get("welcome_message_id")
    if welcome_id:
        try:
            await bot.unpin_chat_message(chat_id=GROUP_ID, message_id=welcome_id)
        except Exception:
            pass
    session["welcome_message_id"] = None

    save_data(data)

    if not participants:
        text = (
            f"📅 *{escape_md(date_str)}*\n\n"
            f"📊 _Tsy nisy isa nandefa anio\\._\n\n"
            f">🙏 *Mankasitraka* \\!"
        )
    else:
        text = (
            f"🗓 *{escape_md(date_str)}*  \\|  *{escape_md(day_mg)}*\n\n"
            f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
            f"*Total  →  {total}*\n\n"
            f">🙏 *Mankasitraka* tamin'ny nanatrehana \\! ☺️"
        )
    await bot.send_message(chat_id=GROUP_ID, text=text, parse_mode="MarkdownV2")

    logger.info(f"Session terminée — {total} mpanatrika | {date_str}")
    context.application.stop_running()


# ─── Scheduler ────────────────────────────────────────────────────────────────

async def job_startup_check(context):
    now   = datetime.now(TIMEZONE)
    sched = SCHEDULE.get(now.weekday())
    if not sched:
        logger.info("Démarrage hors jour de session.")
        return

    accueil_t, end_t = sched
    now_time = now.time().replace(second=0, microsecond=0)

    if accueil_t <= now_time < end_t:
        data = load_data()
        session = get_session(data)
        if not session["active"]:
            logger.info("Lancement automatique de la session.")
            await job_start_session(context)
        else:
            logger.info("Session déjà active au démarrage.")
    else:
        logger.info(f"Hors plage horaire ({now_time}).")


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
        return member.status in ("administrator", "creator")
    except Exception as e:
        logger.warning(f"_check_admin error: {e}")
        return False


async def cmd_debut(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_admin(update, context):
        return
    try:
        await job_start_session(context)
    except Exception as e:
        await _send(context, f"❌ Nisy olana: {e}")
    finally:
        await _delete_cmd(update, context)

async def cmd_fin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_admin(update, context):
        return
    try:
        await job_end_session(context)
    except Exception as e:
        await _send(context, f"❌ Nisy olana: {e}")
    finally:
        await _delete_cmd(update, context)

async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_admin(update, context):
        return
    try:
        data = load_data()
        data["session"] = {}
        save_data(data)
        await _send(context, "✅ Voasasa ny session.")
    except Exception as e:
        await _send(context, f"❌ Nisy olana: {e}")
    finally:
        await _delete_cmd(update, context)

async def cmd_total(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_admin(update, context):
        return
    try:
        if not context.args or not context.args[0].isdigit():
            await _send(context, "❌ Fampiasana : /total 25")
            await _delete_cmd(update, context)
            return

        new_total = int(context.args[0])
        data    = load_data()
        session = get_session(data)

        if not session["active"]:
            await _send(context, "❌ Tsy misy session mavitrika.")
            await _delete_cmd(update, context)
            return

        session["total"] = new_total
        save_data(data)

        bot    = context.bot
        msg_id = session.get("count_message_id")
        if msg_id:
            try:
                await bot.delete_message(chat_id=GROUP_ID, message_id=msg_id)
            except Exception:
                pass
        sent = await bot.send_message(
            chat_id=GROUP_ID,
            text=f">📊 *Isa amin'izao : {new_total}*",
            parse_mode="MarkdownV2",
        )
        session["count_message_id"] = sent.message_id
        save_data(data)
    except Exception as e:
        await _send(context, f"❌ Nisy olana: {e}")
    finally:
        await _delete_cmd(update, context)


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

    old_sum = session["participants"].get(user_id, {}).get("sum", 0)
    number  = extract_number(message.text, old_sum)
    if number is None:
        return

    if number < MIN_COUNT or number > MAX_COUNT:
        await context.bot.send_message(
            chat_id=GROUP_ID,
            text=f"⚠️ Ny isa dia tsy maintsy eo anelanelan'ny *{MIN_COUNT}* sy *{MAX_COUNT}* olona.",
            parse_mode="Markdown",
        )
        return

    session["participants"][user_id] = {"name": name, "sum": number}
    session["total"] = sum(v["sum"] for v in session["participants"].values())
    save_data(data)

    bot    = context.bot
    msg_id = session.get("count_message_id")
    if msg_id:
        try:
            await bot.delete_message(chat_id=GROUP_ID, message_id=msg_id)
        except Exception:
            pass
    sent = await bot.send_message(
        chat_id=GROUP_ID,
        text=f">📊 *Isa amin'izao : {session['total']}*",
        parse_mode="MarkdownV2",
    )
    session["count_message_id"] = sent.message_id
    save_data(data)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    logger.info("Démarrage du bot Mpanisa Mpanatrika...")
    app = ApplicationBuilder().token(TOKEN).build()

    group_filter = filters.Chat(GROUP_ID)

    app.add_handler(CommandHandler("debut", cmd_debut, filters=group_filter))
    app.add_handler(CommandHandler("fin",   cmd_fin,   filters=group_filter))
    app.add_handler(CommandHandler("reset", cmd_reset, filters=group_filter))
    app.add_handler(CommandHandler("total", cmd_total, filters=group_filter))

    app.add_handler(MessageHandler(
        (filters.TEXT & ~filters.COMMAND & group_filter) |
        (filters.UpdateType.EDITED_MESSAGE & group_filter),
        handle_message,
    ))

    app.job_queue.run_once(job_startup_check, when=5)
    app.job_queue.run_repeating(job_scheduler, interval=60, first=65)

    logger.info("Bot en ligne ✅")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
