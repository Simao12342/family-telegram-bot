import asyncio
import logging
import os
import gspread
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logging.basicConfig(level=logging.INFO)

TOKEN = "9068759664:AAETWWERqdgOxLGq1h4DFj3H_3gDa90JU8s"
SPREADSHEET_ID = "1PFK_1jeFHyK5AxBxjoqMGdJieMCtQe7XksBWY1hnmsU"

gc = gspread.service_account(filename="credentials.json")
sh = gc.open_by_key(SPREADSHEET_ID)

sheet_shopping = sh.worksheet("Покупки")
sheet_budget = sh.worksheet("Бюджет")
sheet_tasks = sh.worksheet("Завдання")

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Стани для FSM (очікування введення назви товару)
class ShoppingStates(StatesGroup):
    waiting_for_item = State()

def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Список покупок", callback_data="menu_shopping"),
         InlineKeyboardButton(text="💰 Бюджет", callback_data="menu_budget")],
        [InlineKeyboardButton(text="📋 Завдання", callback_data="menu_tasks")]
    ])

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Привіт! Я ваш розумний сімейний помічник 🤖\nОбери розділ нижче:",
        reply_markup=main_menu()
    )

@dp.callback_query(F.data == "menu_shopping")
async def show_shopping(callback: types.CallbackQuery):
    try:
        records = sheet_shopping.get_all_records()
        text = "🛒 **Список покупок:**\n\n"
        if not records:
            text += "Список поки порожній."
        else:
            for idx, row in enumerate(records, start=2):
                status = "✅" if str(row.get("Куплено")).lower() == "так" else "❌"
                text += f"{status} {row.get('Товар')} — *{row.get('Категорія')}*\n"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Додати товар", callback_data="add_shopping")],
            [InlineKeyboardButton(text="« Головне меню", callback_data="back_home")]
        ])
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception as e:
        await callback.message.answer(f"Помилка читання таблиці: {e}")

@dp.callback_query(F.data == "add_shopping")
async def start_add_shopping(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Введи назву товару, який хочеш додати до списку покупок:")
    await state.set_state(ShoppingStates.waiting_for_item)
    await callback.answer()

@dp.message(ShoppingStates.waiting_for_item)
async def process_new_item(message: types.Message, state: FSMContext):
    item_name = message.text
    # Додаємо в таблицю: Товар, Категорія (за замовчуванням Продукти), Куплено (Ні)
    sheet_shopping.append_row([item_name, "Продукти", "Ні"])
    await state.clear()
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 До списку покупок", callback_data="menu_shopping")],
        [InlineKeyboardButton(text="« Головне меню", callback_data="back_home")]
    ])
    await message.answer(f"✅ Товар **«{item_name}»** успішно додано до таблиці!", reply_markup=keyboard, parse_mode="Markdown")

@dp.callback_query(F.data == "back_home")
async def back_home(callback: types.CallbackQuery):
    await callback.message.edit_text("Головне меню:", reply_markup=main_menu())

# Міні-сервер для Render
async def health_check(request):
    return web.Response(text="Bot is running!")

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
