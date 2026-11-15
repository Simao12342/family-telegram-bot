import asyncio
import logging
import os
import random
from datetime import datetime
import gspread
from aiohttp import web
from google import genai
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logging.basicConfig(level=logging.INFO)

# --- НАЛАШТУВАННЯ ---
TOKEN = "9068759664:AAETWWERqdgOxLGq1h4DFj3H_3gDa90JU8s"
SPREADSHEET_ID = "1PFK_1jeFHyK5AxBxjoqMGdJieMCtQe7XksBWY1hnmsU"
GEMINI_API_KEY = "AQ.Ab8RN6JRgTBlbdv3CBB9CDi4QCH-lDsEB2Tb8Igbye8NWRYhIA"

# Ініціалізація ШІ
ai_client = genai.Client(api_key=GEMINI_API_KEY)

# Ініціалізація Google Таблиць
gc = gspread.service_account(filename="credentials.json")
sh = gc.open_by_key(SPREADSHEET_ID)

sheet_shopping = sh.worksheet("Покупки")
sheet_budget = sh.worksheet("Бюджет")
sheet_tasks = sh.worksheet("Завдання")
sheet_calendar = sh.worksheet("Календар")
sheet_album = sh.worksheet("Альбом")

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- СТАНИ FSM ---
class BotStates(StatesGroup):
    add_shopping = State()
    add_budget = State()
    add_task = State()
    add_event = State()
    add_photo = State()
    ask_ai = State()

# --- ГОЛОВНЕ МЕНЮ ---
def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Календар подій", callback_data="menu_calendar"),
         InlineKeyboardButton(text="🛒 Список покупок", callback_data="menu_shopping")],
        [InlineKeyboardButton(text="💰 Сімейний бюджет", callback_data="menu_budget"),
         InlineKeyboardButton(text="📋 Сімейні завдання", callback_data="menu_tasks")],
        [InlineKeyboardButton(text="📸 Фотоальбом", callback_data="menu_album"),
         InlineKeyboardButton(text="🎲 Розваги та ігри", callback_data="menu_games")],
        [InlineKeyboardButton(text="🧠 Розумний ШІ-помічник", callback_data="menu_ai")]
    ])

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🏡 **Family Bot — ваш сімейний помічник!**\n\n"
        "Оберіть потрібний розділ у меню нижче або просто напишіть мені запитання:",
        reply_markup=main_menu(),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "back_home")
async def back_home(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("🏡 **Головне меню Family Bot:**", reply_markup=main_menu(), parse_mode="Markdown")

# ==========================================
# 1. КАЛЕНДАР ПОДІЙ
# ==========================================
@dp.callback_query(F.data == "menu_calendar")
async def show_calendar(callback: types.CallbackQuery):
    records = sheet_calendar.get_all_records()
    text = "📅 **Сімейний календар подій:**\n\n"
    if not records:
        text += "Запланованих подій поки немає."
    else:
        for row in records:
            text += f"📌 **{row.get('Дата')}** — {row.get('Подія')} *(додав: {row.get('Хто додав')})*\n"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Додати подію / ДН", callback_data="add_event")],
        [InlineKeyboardButton(text="« Головне меню", callback_data="back_home")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "add_event")
async def start_add_event(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Введіть дату та назву події через тире.\nНаприклад: `23.11.2026 - День народження тата`")
    await state.set_state(BotStates.add_event)
    await callback.answer()

@dp.message(BotStates.add_event)
async def save_event(message: types.Message, state: FSMContext):
    parts = message.text.split("-", maxsplit=1)
    date_str = parts[0].strip()
    event_name = parts[1].strip() if len(parts) > 1 else message.text
    sheet_calendar.append_row([date_str, event_name, message.from_user.first_name])
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📅 До календаря", callback_data="menu_calendar")]])
    await message.answer(f"✅ Подію **{event_name}** ({date_str}) збережено!", reply_markup=kb, parse_mode="Markdown")

# ==========================================
# 2. СПИСОК ПОКУПОК (З ІНТЕРАКТИВНИМ ЗАКРИТТЯМ)
# ==========================================
@dp.callback_query(F.data == "menu_shopping")
async def show_shopping(callback: types.CallbackQuery):
    records = sheet_shopping.get_all_records()
    text = "🛒 **Сімейний список покупок:**\n*(Натисніть на товар знизу, щоб відмітити «Куплено»)*\n\n"
    buttons = []
    
    active_count = 0
    for idx, row in enumerate(records, start=2):
        is_bought = str(row.get("Куплено")).lower() == "так"
        if not is_bought:
            active_count += 1
            item = row.get("Товар")
            cat = row.get("Категорія")
            text += f"▫️ {item} *({cat})*\n"
            buttons.append([InlineKeyboardButton(text=f"✅ Куплено: {item}", callback_data=f"buy_{idx}")])
            
    if active_count == 0:
        text += "🎉 Усе куплено! Список порожній."

    buttons.append([InlineKeyboardButton(text="➕ Додати товар", callback_data="add_shopping")])
    buttons.append([InlineKeyboardButton(text="« Головне меню", callback_data="back_home")])
    
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("buy_"))
async def mark_bought(callback: types.CallbackQuery):
    row_idx = int(callback.data.split("_")[1])
    sheet_shopping.update_cell(row_idx, 3, "Так")
    await callback.answer("Відмічено як куплено! ✅")
    await show_shopping(callback)

@dp.callback_query(F.data == "add_shopping")
async def start_add_shopping(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Напишіть товар (можна з категорією в дужках).\nНаприклад: `Молоко` або `Порошок (Побутова хімія)`")
    await state.set_state(BotStates.add_shopping)
    await callback.answer()

@dp.message(BotStates.add_shopping)
async def save_shopping(message: types.Message, state: FSMContext):
    text = message.text
    if "(" in text and ")" in text:
        item = text.split("(")[0].strip()
        cat = text.split("(")[1].replace(")", "").strip()
    else:
        item = text.strip()
        cat = "Продукти"
    sheet_shopping.append_row([item, cat, "Ні"])
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🛒 До списку покупок", callback_data="menu_shopping")]])
    await message.answer(f"✅ Додано: **{item}** ({cat})", reply_markup=kb, parse_mode="Markdown")

# ==========================================
# 3. СІМЕЙНИЙ БЮДЖЕТ (ЗІ СТАТИСТИКОЮ)
# ==========================================
@dp.callback_query(F.data == "menu_budget")
async def show_budget(callback: types.CallbackQuery):
    records = sheet_budget.get_all_records()
    total = 0
    by_category = {}
    by_person = {}
    
    for row in records:
        try:
            amount = float(str(row.get("Сума", 0)).replace(",", "."))
            cat = row.get("Категорія", "Інше")
            person = row.get("Хто витратив", "Невідомо")
            total += amount
            by_category[cat] = by_category.get(cat, 0) + amount
            by_person[person] = by_person.get(person, 0) + amount
        except ValueError:
            continue

    text = f"💰 **Сімейний бюджет**\n\n📊 **Загальні витрати:** `{total:.0f} грн`\n\n**По категоріях:**\n"
    for cat, s in by_category.items():
        text += f"• {cat}: {s:.0f} грн\n"
    
    text += "\n**Хто скільки витратив:**\n"
    for p, s in by_person.items():
        text += f"👤 {p}: {s:.0f} грн\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Записати витрату", callback_data="add_budget")],
        [InlineKeyboardButton(text="« Головне меню", callback_data="back_home")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "add_budget")
async def start_add_budget(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Введіть витрату: `Сума Категорія Коментар`\nНаприклад: `450 Автомобіль Пальне` або `220 Продукти`")
    await state.set_state(BotStates.add_budget)
    await callback.answer()

@dp.message(BotStates.add_budget)
async def save_budget(message: types.Message, state: FSMContext):
    parts = message.text.split(maxsplit=2)
    try:
        amount = float(parts[0].replace(",", "."))
        cat = parts[1] if len(parts) > 1 else "Інше"
        comment = parts[2] if len(parts) > 2 else ""
        date_str = datetime.now().strftime("%d.%m.%Y")
        sheet_budget.append_row([date_str, cat, amount, message.from_user.first_name, comment])
        await state.clear()
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💰 До бюджету", callback_data="menu_budget")]])
        await message.answer(f"✅ Витрату **{amount:.0f} грн** ({cat}) записано!", reply_markup=kb, parse_mode="Markdown")
    except ValueError:
        await message.answer("⚠️ Почніть повідомлення з суми числом, наприклад: `350 Продукти`")

# ==========================================
# 4. СІМЕЙНІ ЗАВДАННЯ (З ЗАКРИТТЯМ)
# ==========================================
@dp.callback_query(F.data == "menu_tasks")
async def show_tasks(callback: types.CallbackQuery):
    records = sheet_tasks.get_all_records()
    text = "📋 **Активні сімейні завдання:**\n\n"
    buttons = []
    active = 0
    for idx, row in enumerate(records, start=2):
        if str(row.get("Статус")).lower() != "виконано":
            active += 1
            task = row.get("Завдання")
            person = row.get("Виконавець")
            text += f"⏳ **{task}** — *{person}*\n"
            buttons.append([InlineKeyboardButton(text=f"✔️ Виконано: {task}", callback_data=f"done_{idx}")])
            
    if active == 0:
        text += "✨ Всі домашні завдання виконані!"

    buttons.append([InlineKeyboardButton(text="➕ Призначити завдання", callback_data="add_task")])
    buttons.append([InlineKeyboardButton(text="« Головне меню", callback_data="back_home")])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("done_"))
async def mark_task_done(callback: types.CallbackQuery):
    row_idx = int(callback.data.split("_")[1])
    sheet_tasks.update_cell(row_idx, 3, "Виконано")
    await callback.answer("Завдання закрито! 🎉")
    await show_tasks(callback)

@dp.callback_query(F.data == "add_task")
async def start_add_task(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Формат: `Завдання | Виконавець`\nНаприклад: `Помити автомобіль | Сергій`")
    await state.set_state(BotStates.add_task)
    await callback.answer()

@dp.message(BotStates.add_task)
async def save_task(message: types.Message, state: FSMContext):
    parts = message.text.split("|")
    task = parts[0].strip()
    person = parts[1].strip() if len(parts) > 1 else "Сім'я"
    sheet_tasks.append_row([task, person, "В процесі"])
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📋 До завдань", callback_data="menu_tasks")]])
    await message.answer(f"✅ Завдання **«{task}»** призначено для **{person}**!", reply_markup=kb, parse_mode="Markdown")

# ==========================================
# 5. СІМЕЙНИЙ ФОТОАЛЬБОМ
# ==========================================
@dp.callback_query(F.data == "menu_album")
async def show_album(callback: types.CallbackQuery):
    records = sheet_album.get_all_records()
    text = f"📸 **Сімейний фотоальбом**\nЗбережено спогадів: **{len(records)}**\n\n"
    if records:
        last = records[-1]
        text += f"Останнє фото: *{last.get('Опис')}* ({last.get('Дата')})"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Зберегти фото в альбом", callback_data="add_photo")],
        [InlineKeyboardButton(text="🖼 Показати випадкове фото", callback_data="random_photo")],
        [InlineKeyboardButton(text="« Головне меню", callback_data="back_home")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "add_photo")
async def start_add_photo(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Надішліть фотографію та додайте підпис до неї (наприклад: *Прогулянка в парку*):")
    await state.set_state(BotStates.add_photo)
    await callback.answer()

@dp.message(BotStates.add_photo, F.photo)
async def save_photo(message: types.Message, state: FSMContext):
    file_id = message.photo[-1].file_id
    caption = message.caption if message.caption else "Сімейне фото"
    date_str = datetime.now().strftime("%d.%m.%Y")
    sheet_album.append_row([date_str, "Сім'я", caption, file_id])
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📸 До альбому", callback_data="menu_album")]])
    await message.answer("✅ Фото збережено у сімейний архів!", reply_markup=kb)

@dp.callback_query(F.data == "random_photo")
async def send_random_photo(callback: types.CallbackQuery):
    records = sheet_album.get_all_records()
    if not records:
        await callback.answer("Альбом поки порожній!", show_alert=True)
        return
    photo_row = random.choice(records)
    await callback.message.answer_photo(
        photo=photo_row.get("File_ID"),
        caption=f"📸 **{photo_row.get('Опис')}**\n📅 Дата: {photo_row.get('Дата')}",
        parse_mode="Markdown"
    )
    await callback.answer()

# ==========================================
# 6. РОЗВАГИ ТА ІГРИ
# ==========================================
@dp.callback_query(F.data == "menu_games")
async def show_games(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎬 Випадковий фільм на вечір", callback_data="game_movie")],
        [InlineKeyboardButton(text="🍕 Що приготувати / замовити?", callback_data="game_food")],
        [InlineKeyboardButton(text="😂 Сімейний жарт", callback_data="game_joke")],
        [InlineKeyboardButton(text="« Головне меню", callback_data="back_home")]
    ])
    await callback.message.edit_text("🎲 **Сімейні розваги та вибір без суперечок:**", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("game_"))
async def play_game(callback: types.CallbackQuery):
    action = callback.data.split("_")[1]
    prompts = {
        "movie": "Порадь один чудовий сімейний фільм або мультфільм для перегляду ввечері всією родиною з дітьми. Напиши назву, рік та 2 речення опису українською мовою.",
        "food": "Запропонуй 3 швидкі та смачні ідеї для сімейної вечері українською мовою.",
        "joke": "Розкажи один добрий, веселий сімейний жарт українською мовою."
    }
    await callback.answer("Думаю... 🎲")
    response = ai_client.models.generate_content(model="gemini-2.5-flash", contents=prompts[action])
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎲 Ще варіант", callback_data=f"game_{action}"),
                                                 InlineKeyboardButton(text="« Назад", callback_data="menu_games")]])
    await callback.message.edit_text(response.text, reply_markup=kb)

# ==========================================
# 7. РОЗУМНИЙ ШІ-ПОМІЧНИК
# ==========================================
@dp.callback_query(F.data == "menu_ai")
async def start_ai(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "🧠 **Розумний сімейний помічник на зв'язку!**\n\n"
        "Запитайте що завгодно звичайною мовою, наприклад:\n"
        "• *Склади меню на вихідні для сім'ї з 4 людей*\n"
        "• *Що купити на вечерю з курки та овочів?*\n"
        "• *Придумай привітання з днем народження для бабусі*"
    )
    await state.set_state(BotStates.ask_ai)
    await callback.answer()

@dp.message(BotStates.ask_ai)
async def answer_ai(message: types.Message, state: FSMContext):
    wait_msg = await message.answer("⏳ Думаю над відповіддю...")
    prompt = (
        "Ти — дружній, турботливий розумний сімейний помічник для української родини (тато, мама, двоє дітей). "
        "Відповідай українською мовою, структуровано, тепло і по суті.\n\n"
        f"Запит користувача: {message.text}"
    )
    try:
        response = ai_client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="« Головне меню", callback_data="back_home")]])
        await wait_msg.edit_text(response.text, reply_markup=kb)
    except Exception as e:
        await wait_msg.edit_text(f"Помилка ШІ: {e}")

# --- ВЕБ-СЕРВЕР ДЛЯ RENDER ---
async def health_check(request):
    return web.Response(text="Family Bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

async def main():
    await start_web_server()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
