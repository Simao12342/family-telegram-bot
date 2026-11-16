import asyncio
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo

import gspread
from google import genai
from google.genai import types
from telegram import InlineKeyboardButton as B
from telegram import InlineKeyboardMarkup as M
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("family-bot")

# ---------- Налаштування (усе беремо зі змінних середовища Render) ----------
BOT_TOKEN = os.environ["BOT_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
ALLOWED = {int(x) for x in os.environ.get("ALLOWED_IDS", "").split(",") if x.strip()}
TZ = ZoneInfo("Europe/Kyiv")


def now():
    return datetime.now(TZ)


def allowed(update: Update) -> bool:
    return not ALLOWED or update.effective_user.id in ALLOWED


# ---------- Google Sheets ----------
def _creds():
    raw = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"].strip()
    if raw.startswith("{"):
        return json.loads(raw)
    with open(raw, encoding="utf-8") as f:  # якщо це шлях до Secret File
        return json.load(f)


book = gspread.service_account_from_dict(_creds()).open_by_key(SPREADSHEET_ID)
HEAD = ["id", "дата", "автор", "текст", "сума"]


def _sheet(name):
    try:
        return book.worksheet(name)
    except gspread.WorksheetNotFound:
        ws = book.add_worksheet(name, 1000, len(HEAD))
        ws.append_row(HEAD)
        return ws


def _add(sec, author, text, extra=""):
    rid = "i" + uuid.uuid4().hex[:6]
    _sheet(sec).append_row(
        [rid, now().strftime("%d.%m.%Y %H:%M"), author, text, extra],
        value_input_option="RAW",
    )
    return rid


def _rows(sec):
    return _sheet(sec).get_all_records()


def _del(sec, rid):
    ws = _sheet(sec)
    cell = ws.find(rid, in_column=1)
    if cell:
        ws.delete_rows(cell.row)


# ---------- Розділи ----------
SECTIONS = {
    "events": ("📅 Календар подій", "Напишіть подію, напр.: 17.07 18:00 День народження тата"),
    "shifts": ("🕒 Графік змін", "Напишіть зміну, напр.: 20.11 Сергій день"),
    "shop": ("🛒 Покупки", "Напишіть, що купити (можна кілька рядків)."),
    "budget": ("💰 Сімейний бюджет", "Витрата: «хліб 45». Дохід із плюсом: «+ зарплата 20000»."),
    "tasks": ("📋 Завдання", "Напишіть нове завдання."),
}
CHECK = {"shop", "tasks"}  # списки з кнопкою ✅
TITLES = {
    **{k: v[0] for k, v in SECTIONS.items()},
    "photos": "📸 Фотоальбом",
    "remind": "🔔 Нагадування",
    "ai": "🤖 AI-помічник",
}
ORDER = ["events", "shifts", "shop", "budget", "photos", "tasks", "remind", "ai"]
MENU_TEXT = "🏡 FAMILY BOT\nВітаю у сімейному помічнику! Оберіть розділ:"


def menu_kb():
    btns = [B(TITLES[k], callback_data=f"m:{k}") for k in ORDER]
    return M([btns[i : i + 2] for i in range(0, len(btns), 2)])


BACK = [B("⬅️ Меню", callback_data="menu")]


def build_view(key):
    """Текст і кнопки розділу (синхронно, викликати через to_thread)."""
    title = TITLES[key]
    rows = _rows(key)
    kb = []
    if key in CHECK:
        head = f"Відкритих: {len(rows)}. Натисніть, щоб відмітити виконаним." if rows else "Поки порожньо."
        text = f"{title}\n\n{head}\n\n{SECTIONS[key][1]}"
        kb = [[B("✅ " + str(r["текст"])[:40], callback_data=f"d:{key}:{r['id']}")] for r in rows[:30]]
    elif key == "remind":
        text = f"{title}\n\n" + ("Заплановано:" if rows else "Немає активних нагадувань.")
        text += (
            "\n\nНапишіть, коли нагадати:\n"
            "• 30 хв винести сміття\n• 18:30 забрати дітей\n• 20.11 09:00 лікар"
        )
        for r in rows[:30]:
            due = str(r["сума"]).split("|")[0]
            try:
                due = datetime.fromisoformat(due).strftime("%d.%m %H:%M")
            except ValueError:
                pass
            kb.append([B(f"🗑 {due} {str(r['текст'])[:30]}", callback_data=f"d:{key}:{r['id']}")])
    elif key == "budget":
        month = now().strftime("%m.%Y")
        inc = exp = 0.0
        for r in rows:
            if str(r["дата"])[3:10] == month:
                s = float(r["сума"] or 0)
                inc += max(s, 0)
                exp += min(s, 0)
        last = "\n".join(
            f"{str(r['дата'])[:5]} {r['автор']}: {float(r['сума'] or 0):+.0f} {r['текст']}"
            for r in rows[-12:]
        )
        text = (
            f"{title}\n\nЦей місяць: доходи +{inc:.0f}, витрати {exp:.0f}, баланс {inc + exp:+.0f}\n\n"
            f"{last or 'Записів ще немає.'}\n\n{SECTIONS[key][1]}"
        )
    else:
        last = "\n".join(f"• {r['текст']}  ({r['автор']})" for r in rows[-15:])
        text = f"{title}\n\n{last or 'Записів ще немає.'}\n\n{SECTIONS[key][1]}"
    kb.append(BACK)
    return text, M(kb)


# ---------- Нагадування ----------
def parse_when(t):
    n = now()
    m = re.match(r"^(\d+)\s*(хв|год)\w*\s+(.+)$", t, re.I | re.S)
    if m:
        mins = int(m[1]) * (1 if m[2].lower() == "хв" else 60)
        return n + timedelta(minutes=mins), m[3]
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\s+(\d{1,2}):(\d{2})\s+(.+)$", t, re.S)
    if m:
        due = n.replace(month=int(m[2]), day=int(m[1]), hour=int(m[3]), minute=int(m[4]), second=0, microsecond=0)
        if due < n:
            due = due.replace(year=n.year + 1)
        return due, m[5]
    m = re.match(r"^(\d{1,2}):(\d{2})\s+(.+)$", t, re.S)
    if m:
        due = n.replace(hour=int(m[1]), minute=int(m[2]), second=0, microsecond=0)
        if due <= n:
            due += timedelta(days=1)
        return due, m[3]
    return None


async def fire(ctx: ContextTypes.DEFAULT_TYPE):
    d = ctx.job.data
    await ctx.bot.send_message(d["chat"], f"🔔 Нагадування: {d['text']}")
    await asyncio.to_thread(_del, "remind", d["rid"])


def schedule(jq, rid, chat_id, due, text):
    delay = max((due - now()).total_seconds(), 3)
    jq.run_once(fire, delay, data={"rid": rid, "chat": chat_id, "text": text}, name=rid)


async def post_init(app: Application):
    try:
        for r in await asyncio.to_thread(_rows, "remind"):
            due_s, chat = str(r["сума"]).split("|")
            schedule(app.job_queue, r["id"], int(chat), datetime.fromisoformat(due_s), r["текст"])
    except Exception:
        log.exception("Не вдалося відновити нагадування")


# ---------- AI ----------
ai = genai.Client(api_key=GEMINI_API_KEY)
SYSTEM = (
    "Ти — теплий і лаконічний сімейний помічник у Telegram. Відповідай українською, "
    "коротко і по справі, без Markdown-розмітки. "
)


def ask_ai(question, user):
    shop = [r["текст"] for r in _rows("shop")]
    tasks = [r["текст"] for r in _rows("tasks")]
    ctx = (
        f"Зараз {now():%d.%m.%Y %H:%M}. З тобою говорить {user}. "
        f"Список покупок: {shop}. Відкриті завдання: {tasks}."
    )
    r = ai.models.generate_content(
        model=MODEL,
        contents=question,
        config=types.GenerateContentConfig(system_instruction=SYSTEM + ctx),
    )
    return (r.text or "Не вдалося отримати відповідь.")[:4000]


# ---------- Хендлери ----------
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    ctx.user_data["mode"] = None
    await update.message.reply_text(MENU_TEXT, reply_markup=menu_kb())


async def cmd_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Ваш Telegram ID: {update.effective_user.id}")


async def send_view(update: Update, key: str):
    text, kb = await asyncio.to_thread(build_view, key)
    await update.effective_chat.send_message(text, reply_markup=kb)


async def open_section(update: Update, ctx: ContextTypes.DEFAULT_TYPE, key: str):
    ctx.user_data["mode"] = key
    if key == "ai":
        await update.effective_chat.send_message(
            "🤖 AI-помічник\n\nПишіть будь-яке питання: рецепт, план дня, ідея подарунка. "
            "Я бачу ваші списки покупок і завдань.",
            reply_markup=M([BACK]),
        )
    elif key == "photos":
        rows = await asyncio.to_thread(_rows, "photos")
        await update.effective_chat.send_message(
            f"📸 Фотоальбом\n\nУ альбомі: {len(rows)}. Надішліть фото (з підписом), щоб додати.",
            reply_markup=M([BACK]),
        )
        for r in rows[-5:]:
            await update.effective_chat.send_photo(
                str(r["сума"]), caption=f"{r['дата']} · {r['автор']} {r['текст']}"
            )
    else:
        await send_view(update, key)


async def on_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not allowed(update):
        return
    data = q.data
    if data == "menu":
        ctx.user_data["mode"] = None
        await q.message.edit_text(MENU_TEXT, reply_markup=menu_kb())
        return
    kind, _, rest = data.partition(":")
    if kind == "m":
        await open_section(update, ctx, rest)
    elif kind == "d":
        key, rid = rest.split(":")
        for job in ctx.job_queue.get_jobs_by_name(rid):
            job.schedule_removal()
        await asyncio.to_thread(_del, key, rid)
        text, kb = await asyncio.to_thread(build_view, key)
        await q.message.edit_text(text, reply_markup=kb)


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    mode = ctx.user_data.get("mode")
    text = update.message.text.strip()
    user = update.effective_user.first_name or "Хтось"

    if mode in CHECK:
        lines = [l.strip(" -•") for l in text.splitlines() if l.strip()]
        for l in lines:
            await asyncio.to_thread(_add, mode, user, l)
        await send_view(update, mode)
    elif mode in ("events", "shifts"):
        await asyncio.to_thread(_add, mode, user, text)
        await send_view(update, mode)
    elif mode == "budget":
        m = re.search(r"(\d+(?:[.,]\d+)?)", text)
        if not m:
            await update.message.reply_text("Не бачу суми. Напишіть, напр.: хліб 45")
            return
        amount = float(m[1].replace(",", "."))
        if not text.startswith("+"):
            amount = -amount
        desc = (text[: m.start()] + text[m.end() :]).strip(" +-") or "без опису"
        await asyncio.to_thread(_add, "budget", user, desc, amount)
        await send_view(update, "budget")
    elif mode == "remind":
        try:
            parsed = parse_when(text)
        except ValueError:
            parsed = None
        if not parsed:
            await update.message.reply_text(
                "Не зрозумів час. Приклади: «30 хв чайник», «18:30 дзвінок», «20.11 09:00 лікар»."
            )
            return
        due, what = parsed
        chat_id = update.effective_chat.id
        rid = await asyncio.to_thread(_add, "remind", user, what, f"{due.isoformat()}|{chat_id}")
        schedule(ctx.job_queue, rid, chat_id, due, what)
        await send_view(update, "remind")
    elif mode == "ai":
        await update.effective_chat.send_action("typing")
        try:
            answer = await asyncio.to_thread(ask_ai, text, user)
        except Exception:
            log.exception("Gemini error")
            answer = "Не вдалося звернутися до AI. Перевірте GEMINI_API_KEY та назву моделі."
        await update.message.reply_text(answer, reply_markup=M([BACK]))
    else:
        await update.message.reply_text("Оберіть розділ:", reply_markup=menu_kb())


async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    user = update.effective_user.first_name or "Хтось"
    file_id = update.message.photo[-1].file_id
    await asyncio.to_thread(_add, "photos", user, update.message.caption or "", file_id)
    await update.message.reply_text("📸 Збережено в сімейний альбом.", reply_markup=M([BACK]))


async def on_error(update, ctx: ContextTypes.DEFAULT_TYPE):
    log.error("Помилка", exc_info=ctx.error)


# ---------- Health-check для Render (Web Service) ----------
class _H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


def _serve():
    HTTPServer(("0.0.0.0", int(os.environ.get("PORT", "10000"))), _H).serve_forever()


def main():
    threading.Thread(target=_serve, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler(["start", "menu"], cmd_start))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CallbackQueryHandler(on_btn))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
    app.run_polling()


if __name__ == "__main__":
    main()
