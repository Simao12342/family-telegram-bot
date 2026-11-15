import asyncio
import logging
import os
import gspread
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# Увімкнемо логування
logging.basicConfig(level=logging.INFO)

# Токен твого бота (краще пізніше винести в змінні середовища GitHub/Render, але для старти можна прописати тут)
TOKEN = "9068759664:AAETWWERqdgOxLGq1h4DFj3H_3gDa90JU8s"
SPREADSHEET_ID = "1PFK_1jeFHyK5AxBxjoqMGdJieMCtQe7XksBWY1hnmsU"

# Авторизація в Google Таблицях через JSON файл (файл назвемо credentials.json)
gc = gspread.service_account(filename="credentials.json")
sh = gc.open_by_key(SPREADSHEET_ID)

# Листи таблиці
sheet_shopping = sh.worksheet("Покупки")
sheet_budget = sh.worksheet("Бюджет")
sheet_tasks = sh.worksheet("Завдання")

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Головне меню з кнопками
def main_menu():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Список покупок", callback_data="menu_shopping"),
         InlineKeyboardButton(text="💰 Бюджет", callback_data="menu_budget")],
        [InlineKeyboardButton(text="📋 Завдання", callback_data="menu_tasks")]
    ])
    return keyboard

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
            text += "Список порожній."
        else:
            for idx, row in enumerate(records, start=2):
                status = "✅" if row.get("Куплено") == "Так" else "❌"
                text += f"{status} {row.get('Товар')} ({row.get('Категорія')})\n"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Додати товар", callback_data="add_shopping")],
            [InlineKeyboardButton(text="« Головне меню", callback_data="back_home")]
        ])
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception as e:
        await callback.message.answer(f"Помилка читання таблиці: {e}")

@dp.callback_query(F.data == "back_home")
async def back_home(callback: types.CallbackQuery):
    await callback.message.edit_text("Головне меню:", reply_markup=main_menu())

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
