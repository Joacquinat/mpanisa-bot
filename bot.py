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
from datetime import datetime, time, timedelta
import pytz
from groq import Groq

from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ─── Configuration ────────────────────────────────────────────────────────────

TOKEN        = os.environ.get("BOT_TOKEN", "VOTRE_TOKEN_ICI")
GROUP_ID     = int(os.environ.get("GROUP_ID", "0"))
GROQ_KEY     = os.environ.get("GROQ_API_KEY", "")
MINIAPP_URL  = os.environ.get("MINIAPP_URL", "")
TIMEZONE     = pytz.timezone("Indian/Antananarivo")

DATA_FILE     = "data.json"
INTER_MINUTES = 45
MIN_COUNT     = 1
MAX_COUNT     = 30
SALLE_MAX     = 20

SCHEDULE = {
    4: (time(17, 50), time(19, 30)),  # Vendredi
    6: (time(10, 20), time(12,  0)),  # Dimanche
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

MONTHS_MG = ["janoary","febroary","martsa","aprily","mey","jona",
             "jolay","aogositra","septambra","oktobra","novambra","desambra"]
MONTHS_CAP = ["Janoary","Febroary","Martsa","Aprily","Mey","Jona",
              "Jolay","Aogositra","Septambra","Oktobra","Novambra","Desambra"]

def get_mwb_url() -> str:
    """Construit l'URL MWB jw.org de la semaine en cours."""
    now = datetime.now(TIMEZONE)
    dow = now.weekday()  # 0=Lundi
    monday = now - timedelta(days=dow)
    sunday = monday + timedelta(days=6)
    mon_d, mon_m, mon_y = monday.day, monday.month - 1, monday.year
    sun_d, sun_m, sun_y = sunday.day, sunday.month - 1, sunday.year
    bi = mon_m // 2
    bimestre = f"{MONTHS_MG[bi*2]}-{MONTHS_MG[bi*2+1]}-{mon_y}-mwb"
    week_slug = f"Fandaharana-ho-Anny-{mon_d}-{sun_d}-{MONTHS_CAP[sun_m]}-{sun_y}"
    return (f"https://www.jw.org/mg/zavatra-misy/fivoriana-vj-tari-dalana/"
            f"{bimestre}/Fivoriana-Momba-ny-Fiainantsika-Kristianina-sy-ny-Fanompoana-{week_slug}/")

def get_wt_url() -> str:
    """Construit l'URL Tour de Garde jw.org du mois en cours."""
    now = datetime.now(TIMEZONE)
    m = now.month - 1  # 0-based
    y = now.year
    pub_month = (m - 2) % 12
    pub_year  = y - 1 if m < 2 else y
    slug = f"gazety-fianarana-{MONTHS_MG[pub_month]}-{pub_year}"
    return f"https://www.jw.org/mg/zavatra-misy/gazety/{slug}/"

def miniapp_button():
    """Bouton inline : MWB le vendredi/samedi, Tilikambo le dimanche."""
    now = datetime.now(TIMEZONE)
    dow = now.weekday()  # 0=Lundi, 4=Vendredi, 5=Samedi, 6=Dimanche
    if dow >= 5:  # Samedi (5) ou Dimanche (6)
        url  = get_wt_url()
        text = "📖 Jereo ny Tilikambo Fiambenana"
    else:  # Lundi (0) à Vendredi (4)
        url  = get_mwb_url()
        text = "📋 Jereo ny fandaharam-pivoriana"
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(text=text, url=url)
    ]])

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
    return f"{day_mg} {now.day} {months_mg[now.month]} {now.year}"

def build_progress_bar(total: int) -> str:
    ratio  = total / SALLE_MAX
    filled = min(round(ratio * 10), 10)
    empty  = 10 - filled
    block  = "🟨" if ratio < 0.5 else ("🟧" if ratio < 0.75 else "🟥")
    bar    = block * filled + "⬜" * empty
    pct    = min(round(ratio * 100), 100)
    return f"{bar} *{total}/{SALLE_MAX}* _({pct}%)_"

def build_list(participants: dict) -> str:
    return "\n".join(
        f"  • *{v['name']}* : {v['sum']}"
        for v in participants.values()
    )

# ─── Jobs ─────────────────────────────────────────────────────────────────────

async def job_start_session(context):
    """Message d'accueil + bouton Mini App + ouverture du comptage."""
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
            "Ankasitrahana raha alefa mialoha ny isa 😁\n\n"
            "📋 _Tsindrio eto ambany mba ho hita ny fandaharam-pivoriana :_"
        ),
        parse_mode="Markdown",
        reply_markup=miniapp_button(),
    )
    logger.info("Session démarrée — message d'accueil + Mini App envoyé.")


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

    inter_total  = accueil_t.hour * 60 + accueil_t.minute + INTER_MINUTES
    inter_h, inter_m = divmod(inter_total, 60)
    if h == inter_h and m == inter_m:
        await job_intermediaire(context)

    if h == end_t.hour and m == end_t.minute:
        await job_end_session(context)


# ─── Commandes ────────────────────────────────────────────────────────────────

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Bot Mpanisa Mpanatrika*\n\n"
        "Andefaso ny isan'ny ao aminao\n\n"
        "*Baiko :*\n"
        "• /total — Jereo ny isa ankehitriny\n"
        "• /programme — Bukao ny fandaharam-pivoriana\n"
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

async def cmd_programme(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ouvre directement la Mini App."""
    await update.message.reply_text(
        "📋 *Fandaharam-pivoriana*\n\n"
        "_Tsindrio ny bokotra eto ambany mba bukao ny programme :_",
        parse_mode="Markdown",
        reply_markup=miniapp_button(),
    )

async def _check_admin(update: Update) -> bool:
    try:
        # En chat privé, on autorise toujours (le bot ne peut pas vérifier les admins)
        if update.effective_chat.type == "private":
            return True
        member = await update.effective_chat.get_member(update.effective_user.id)
        if member.status not in ("administrator", "creator"):
            await update.message.reply_text("🚫 Admin ihany no afaka manao izany.")
            return False
        return True
    except Exception as e:
        logger.warning(f"_check_admin error: {e}")
        # En cas d'erreur de vérification, on autorise quand même (fail-open)
        return True

async def cmd_debut(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_admin(update):
        return
    try:
        await job_start_session(context)
    except Exception as e:
        logger.error(f"cmd_debut error: {e}")
        await update.message.reply_text(f"❌ Nisy olana: {e}")

async def cmd_fin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_admin(update):
        return
    try:
        await job_end_session(context)
    except Exception as e:
        logger.error(f"cmd_fin error: {e}")
        await update.message.reply_text(f"❌ Nisy olana: {e}")

async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_admin(update):
        return
    try:
        data = load_data()
        data["session"] = {}
        save_data(data)
        await update.message.reply_text("✅ Voasasa ny session.")
    except Exception as e:
        logger.error(f"cmd_reset error: {e}")
        await update.message.reply_text(f"❌ Nisy olana: {e}")

# ─── Handler messages ─────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
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

    app.add_handler(CommandHandler("start",      cmd_help))
    app.add_handler(CommandHandler("help",       cmd_help))
    app.add_handler(CommandHandler("total",      cmd_total))
    app.add_handler(CommandHandler("programme",  cmd_programme))
    app.add_handler(CommandHandler("debut",      cmd_debut))
    app.add_handler(CommandHandler("fin",        cmd_fin))
    app.add_handler(CommandHandler("reset",      cmd_reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.job_queue.run_repeating(job_scheduler, interval=60, first=3)

    logger.info("Bot en ligne ✅")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
