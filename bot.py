import asyncio
import logging
import os
import gspread
from datetime import datetime
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

class ShoppingStates(StatesGroup):
    waiting_for_item = State()

class BudgetStates(StatesGroup):
    waiting_for_amount = State()

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

# --- БЛОК ПОКУПОК ---
@dp.callback_query(F.data == "menu_shopping")
async def show_shopping(callback: types.CallbackQuery):
    try:
        records = sheet_shopping.get_all_records()
        text = "🛒 **Список покупок:**\n\n"
        if not records:
            text += "Список поки порожній."
        else:
            for row in records:
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
    sheet_shopping.append_row([item_name, "Продукти", "Ні"])
    await state.clear()
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 До списку покупок", callback_data="menu_shopping")],
        [InlineKeyboardButton(text="« Головне меню", callback_data="back_home")]
    ])
    await message.answer(f"✅ Товар **«{item_name}»** успішно додано до таблиці!", reply_markup=keyboard, parse_mode="Markdown")

# --- БЛОК БЮДЖЕТУ ---
@dp.callback_query(F.data == "menu_budget")
async def show_budget(callback: types.CallbackQuery):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Додати витрату", callback_data="add_budget")],
        [InlineKeyboardButton(text="« Головне меню", callback_data="back_home")]
    ])
    await callback.message.edit_text(
        "💰 **Сімейний бюджет**\n\nТут ти можеш вести облік спільних витрат. Обери дію нижче:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "add_budget")
async def start_add_budget(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Введи витрату у форматі: `Сума Категорія Коментар`\nНаприклад: `350 Продукти Супермаркет` або просто `150 Кав'ярня`")
    await state.set_state(BudgetStates.waiting_for_amount)
    await callback.answer()

@dp.message(BudgetStates.waiting_for_amount)
async def process_new_budget(message: types.Message, state: FSMContext):
    parts = message.text.split(maxsplit=2)
    try:
        amount = parts[0]
        category = parts[1] if len(parts) > 1 else "Інше"
        comment = parts[2] if len(parts) > 2 else ""
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        user_name = message.from_user.first_name

        # Записуємо в аркуш «Бюджет»: Дата, Категорія, Сума, Хто витратив, Коментар
        sheet_budget.append_row([date_str, category, amount, user_name, comment])
        await state.clear()

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💰 До бюджету", callback_data="menu_budget")],
            [InlineKeyboardButton(text="« Головне меню", callback_data="back_home")]
        ])
        await message.answer(f"✅ Витрату на суму **{amount} грн** ({category}) успішно збережено!", reply_markup=keyboard, parse_mode="Markdown")
    except Exception as e:
        await message.answer("⚠️ Невірний формат. Будь ласка, введи спочатку суму числом, наприклад: `350 Продукти`")

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
