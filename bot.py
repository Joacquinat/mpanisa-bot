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

TOKEN      = os.environ.get("BOT_TOKEN", "VOTRE_TOKEN_ICI")
GROUP_ID   = int(os.environ.get("GROUP_ID", "0"))
GROQ_KEY   = os.environ.get("GROQ_API_KEY", "")
TIMEZONE   = pytz.timezone("Indian/Antananarivo")

DATA_FILE     = "data.json"
INTER_MINUTES = 45
MIN_COUNT     = 1
MAX_COUNT     = 30
SALLE_MAX     = 20  # Capacité max de la salle

# Planning : {weekday: (heure_accueil, heure_fin)}
# 4 = Vendredi | 6 = Dimanche
SCHEDULE = {
    4: (time(17, 50), time(19, 30)),
    6: (time(10, 20), time(12,  0)),
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
        }
    return data["session"]

# ─── Utilitaires ──────────────────────────────────────────────────────────────

def parse_with_groq(text: str, old_sum: int) -> int | None:
    """Utilise Groq pour comprendre le texte malgache et retourner le nouveau nombre."""
    if not groq_client:
        return None
    try:
        sub_result = max(0, old_sum - 1)  # exemple pour le prompt
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
    """Essaie d'abord Groq, sinon fallback regex robuste."""
    # Essai avec Groq
    result = parse_with_groq(text, old_sum)
    if result is not None:
        return result

    # Fallback regex si Groq indisponible
    # "zay" est un marqueur malgache neutre, on le supprime avant analyse
    text_clean = text.replace(",", ".").strip().lower()
    text_clean = re.sub(r"\bzay\b", "", text_clean).strip()

    # Détection FAMPITOMBOANA : +N (ex: "+1", "+ 2", "+1 zay")
    plus_match = re.search(r"(?<!\d)\+\s*(\d+)", text_clean)
    if plus_match:
        return old_sum + int(plus_match.group(1))

    # Détection FAMPIHENANA : -N (ex: "-2", "- 3")
    minus_match = re.search(r"(?<!\d)-\s*(\d+)", text_clean)
    if minus_match:
        return max(0, old_sum - int(minus_match.group(1)))

    # Extraction du nombre
    match = re.search(r"\b(\d+)\b", text_clean)
    if not match:
        return None
    n = int(match.group(1))
    if n <= 0:
        return None

    # Mots-clés FAMPITOMBOANA
    ADD_KEYWORDS = ["miampy", "nanampy", "fanampiny", "plus", "tonga", "sy", "ary"]
    if any(w in text_clean for w in ADD_KEYWORDS):
        return old_sum + n

    # Mots-clés FAMPIHENANA
    SUB_KEYWORDS = ["mihena", "miala"]
    if any(w in text_clean for w in SUB_KEYWORDS):
        return max(0, old_sum - n)

    # Par défaut : remplacement (lasa, misy, izahay, isa fotsiny, etc.)
    return n

def format_date_mg(now: datetime) -> str:
    months_mg = {
        1:"Janoary",2:"Febroary",3:"Martsa",4:"Aprily",
        5:"Mey",6:"Jona",7:"Jolay",8:"Aogositra",
        9:"Septambra",10:"Oktobra",11:"Novambra",12:"Desambra",
    }
    day_mg = DAY_MG.get(now.weekday(), "")
    return f"{day_mg} {now.day} {months_mg[now.month]} {now.year}"

def build_progress_bar(total: int) -> str:
    """Barre de progression emoji dynamique selon le taux de remplissage."""
    ratio = total / SALLE_MAX
    filled = min(round(ratio * 10), 10)
    empty  = 10 - filled

    if ratio < 0.5:
        block = "🟨"   # < 50% — jaune
    elif ratio < 0.75:
        block = "🟧"   # < 75% — orange
    else:
        block = "🟥"   # >= 75% — rouge

    bar = block * filled + "⬜" * empty
    pct = min(round(ratio * 100), 100)
    return f"{bar} *{total}/{SALLE_MAX}* _({pct}%)_"

def build_list(participants: dict) -> str:
    return "\n".join(
        f"  • *{v['name']}* : {v['sum']}"
        for v in participants.values()
    )

# ─── Jobs ─────────────────────────────────────────────────────────────────────

async def job_start_session(context):
    """Message d'accueil + ouverture du comptage."""
    bot: Bot = context.bot
    data = load_data()
    session = get_session(data)
    session["active"]       = True
    session["total"]        = 0
    session["participants"] = {}
    save_data(data)

    await bot.send_message(
        chat_id=GROUP_ID,
        text=(
            "🙏 *Salama daholo* 👋\n\n"
            "Ankasitrahana raha alefa mialoha ny isa 😁"
        ),
        parse_mode="Markdown",
    )
    logger.info("Session démarrée — message d'accueil envoyé.")


async def job_intermediaire(context):
    """À +45 min : total intermédiaire."""
    bot: Bot = context.bot
    data = load_data()
    session = get_session(data)
    if not session["active"]:
        return

    total        = session["total"]
    participants = session["participants"]

    if not participants:
        text = (
            "⏰ *Isa eo anelanelan'ny fivoriana* _(45 min)_\n\n"
            "📭 _Tsy nisy isa nandefa hatreto._"
        )
    else:
        text = (
            "⏰ *Isa eo anelanelan'ny fivoriana* _(45 min)_\n\n"
            f"{build_list(participants)}\n\n"
            f"{build_progress_bar(total)}\n"
            f"👥 *Total amin'izao : {total}*"
        )

    await bot.send_message(chat_id=GROUP_ID, text=text, parse_mode="Markdown")
    logger.info(f"Intermédiaire — {total} mpanatrika.")


async def job_end_session(context):
    """Résultat final avec date + Mankasitraka."""
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
            f"━━━━━━━━━━━━━━━\n"
            f"📅 *{date_str}*\n"
            f"━━━━━━━━━━━━━━━\n\n"
            f"🎉 *Isa farany — Mpanatrika :*\n\n"
            f"{build_list(participants)}\n\n"
            f"{build_progress_bar(total)}\n"
            f"👥 *Total : {total}*\n\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🙏 *Mankasitraka* tamin'ny nanatrehana ! ❤️"
        )

    await bot.send_message(chat_id=GROUP_ID, text=text, parse_mode="Markdown")
    logger.info(f"Session terminée — {total} mpanatrika | {date_str}")


# ─── Scheduler 1×/minute ──────────────────────────────────────────────────────

async def job_scheduler(context):
    now   = datetime.now(TIMEZONE)
    sched = SCHEDULE.get(now.weekday())
    if not sched:
        return

    accueil_t, end_t = sched
    h, m = now.hour, now.minute

    # Message d'accueil + ouverture comptage
    if h == accueil_t.hour and m == accueil_t.minute:
        await job_start_session(context)

    # Total intermédiaire à +45 min depuis l'accueil
    inter_total  = accueil_t.hour * 60 + accueil_t.minute + INTER_MINUTES
    inter_h, inter_m = divmod(inter_total, 60)
    if h == inter_h and m == inter_m:
        await job_intermediaire(context)

    # Fin de session
    if h == end_t.hour and m == end_t.minute:
        await job_end_session(context)


# ─── Commandes ────────────────────────────────────────────────────────────────

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Bot Mpanisa Mpanatrika*\n\n"
        "Andefaso ny isan'ny ao aminao\n\n"
        "*Baiko :*\n"
        "• /total — Jereo ny isa ankehitriny\n"
        "• /debut — Admin : atombohy ny fivoriana\n"
        "• /fin — Admin : afarano ny fivoriana\n"
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
        f"📊 *Isa amin'izao : {total}*\n\n"
        f"{build_list(session['participants'])}\n\n"
        f"{build_progress_bar(total)}",
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

    # Ignorer les messages hors du groupe cible
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

    # Limite min/max
    if number < MIN_COUNT or number > MAX_COUNT:
        await message.reply_text(
            f"⚠️ Ny isa dia tsy maintsy eo anelanelan'ny *{MIN_COUNT}* sy *{MAX_COUNT}* olona.",
            parse_mode="Markdown",
        )
        return

    is_subtraction = already_sent and number < old_sum

    session["participants"][user_id] = {
        "name":  name,
        "sum":   number,
        "count": (session["participants"][user_id]["count"] + 1) if already_sent else 1,
    }
    session["total"] = sum(v["sum"] for v in session["participants"].values())
    save_data(data)

    total = session["total"]

    if is_subtraction:
        await message.reply_text(
            f"➖ *{name}* : mihena {old_sum} ➜ *{number}*\n\n"
            f"{build_progress_bar(total)}\n"
            f"👥 *Total amin'izao : {total}*",
            parse_mode="Markdown",
        )
    elif already_sent:
        await message.reply_text(
            f"🔄 *{name}* : novaina {old_sum} ➜ *{number}*\n\n"
            f"{build_progress_bar(total)}\n"
            f"👥 *Total amin'izao : {total}*",
            parse_mode="Markdown",
        )
    else:
        await message.reply_text(
            f"✅ *{name}* — *{number}*\n\n"
            f"{build_progress_bar(total)}\n"
            f"👥 *Total amin'izao : {total}*",
            parse_mode="Markdown",
        )

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    logger.info("Démarrage du bot Mpanisa Mpanatrika...")
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
