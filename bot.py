import asyncio
import os
import sqlite3
from contextlib import closing
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from dotenv import load_dotenv

load_dotenv()
DB = os.getenv("DB_PATH", "tours.db")
ADMINS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}
dp = Dispatcher()

def query(sql, args=(), write=False):
    with closing(sqlite3.connect(DB)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        cursor = conn.execute(sql, args)
        if write:
            conn.commit()
            return cursor.lastrowid
        return cursor.fetchall()

def init():
    with closing(sqlite3.connect(DB)) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS tours(id INTEGER PRIMARY KEY, title TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS stops(id INTEGER PRIMARY KEY, tour_id INTEGER NOT NULL REFERENCES tours(id), title TEXT NOT NULL, description TEXT NOT NULL, photo TEXT);
        CREATE TABLE IF NOT EXISTS progress(user_id INTEGER, tour_id INTEGER, position INTEGER NOT NULL, PRIMARY KEY(user_id,tour_id));
        """)
        conn.commit()

def keyboard(buttons):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d)] for t,d in buttons])

async def catalog(message):
    tours = query("SELECT id,title FROM tours ORDER BY id")
    await message.answer("Выберите экскурсию:" if tours else "Экскурсии пока не добавлены.", reply_markup=keyboard([(title,f"tour:{i}") for i,title in tours]) if tours else None)

@dp.message(CommandStart())
@dp.message(Command("tours"))
async def start(message: Message):
    await catalog(message)

@dp.callback_query(F.data == "catalog")
async def back(call: CallbackQuery):
    await call.answer()
    await catalog(call.message)

async def show(call, tour_id, position=None):
    stops = query("SELECT title,description,photo FROM stops WHERE tour_id=? ORDER BY id", (tour_id,))
    if not stops:
        await call.answer("Маршрут пуст.", show_alert=True)
        return
    if position is None:
        saved = query("SELECT position FROM progress WHERE user_id=? AND tour_id=?", (call.from_user.id,tour_id))
        position = saved[0][0] if saved else 0
    position = max(0,min(position,len(stops)-1))
    query("INSERT INTO progress VALUES (?,?,?) ON CONFLICT(user_id,tour_id) DO UPDATE SET position=excluded.position", (call.from_user.id,tour_id,position), True)
    title, description, photo = stops[position]
    buttons = []
    if position > 0:
        buttons.append(("Назад",f"stop:{tour_id}:{position-1}"))
    if position < len(stops)-1:
        buttons.append(("Далее",f"stop:{tour_id}:{position+1}"))
    else:
        buttons.append(("Начать заново",f"stop:{tour_id}:0"))
    buttons.append(("К экскурсиям","catalog"))
    markup = keyboard(buttons)
    body = f"{position+1}/{len(stops)} — {title}\n\n{description}"
    await call.answer()
    if photo and len(body) <= 1024:
        try:
            await call.message.answer_photo(photo,caption=body,reply_markup=markup)
            return
        except TelegramBadRequest:
            pass
    await call.message.answer(body,reply_markup=markup)

@dp.callback_query(F.data.startswith("tour:"))
async def tour(call: CallbackQuery):
    await show(call,int(call.data.split(":")[1]))

@dp.callback_query(F.data.startswith("stop:"))
async def stop(call: CallbackQuery):
    _, tour_id, position = call.data.split(":")
    await show(call,int(tour_id),int(position))

@dp.message(Command("admin"))
async def admin(message: Message):
    if message.from_user.id not in ADMINS:
        return
    await message.answer("/addtour Название\n/addstop ID | Название | Описание | URL фото (необязательно)\n/tourids")

@dp.message(Command("tourids"))
async def ids(message: Message):
    if message.from_user.id not in ADMINS:
        return
    await message.answer("\n".join(f"{i}: {t}" for i,t in query("SELECT id,title FROM tours")) or "Нет маршрутов.")

@dp.message(Command("addtour"))
async def addtour(message: Message):
    if message.from_user.id not in ADMINS:
        return
    title = (message.text or "").partition(" ")[2].strip()
    if not title or len(title)>100:
        await message.answer("Укажите название (до 100 символов).")
        return
    i = query("INSERT INTO tours(title) VALUES (?)",(title,),True)
    await message.answer(f"Маршрут добавлен. ID: {i}")

@dp.message(Command("addstop"))
async def addstop(message: Message):
    if message.from_user.id not in ADMINS:
        return
    parts = [p.strip() for p in (message.text or "").partition(" ")[2].split("|")]
    if len(parts) not in (3,4) or not parts[0].isdigit() or not parts[1] or not parts[2] or len(parts[1])+len(parts[2])>3900:
        await message.answer("Формат: /addstop ID | Название | Описание | URL фото")
        return
    if not query("SELECT id FROM tours WHERE id=?", (int(parts[0]),)):
        await message.answer("Маршрут не найден.")
        return
    query("INSERT INTO stops(tour_id,title,description,photo) VALUES (?,?,?,?)",(int(parts[0]),parts[1],parts[2],parts[3] if len(parts)==4 else None),True)
    await message.answer("Точка добавлена.")

async def main():
    init()
    async with Bot(token=os.environ["BOT_TOKEN"]) as bot:
        await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
