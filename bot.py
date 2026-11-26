import asyncio
import os
import sqlite3
from contextlib import closing
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from urllib.parse import urlencode
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
        for table, column, definition in [("tours","price","INTEGER NOT NULL DEFAULT 0"), ("stops","lat","REAL"), ("stops","lon","REAL")]:
            if column not in {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS purchases(charge_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, tour_id INTEGER NOT NULL, amount INTEGER NOT NULL);
        """)
        conn.commit()

def keyboard(buttons):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d)] for t,d in buttons])

async def catalog(message):
    tours = query("SELECT id,title,price FROM tours ORDER BY id")
    await message.answer("Выберите экскурсию:" if tours else "Экскурсии пока не добавлены.", reply_markup=keyboard([(f"{title} — {price} ⭐" if price else title,f"tour:{i}") for i,title,price in tours]) if tours else None)

@dp.message(CommandStart())
@dp.message(Command("tours"))
async def start(message: Message):
    query("INSERT OR IGNORE INTO users VALUES (?)",(message.from_user.id,),True)
    await catalog(message)

@dp.callback_query(F.data == "catalog")
async def back(call: CallbackQuery):
    await call.answer()
    await catalog(call.message)

async def show(call, tour_id, position=None):
    info = query("SELECT title,price FROM tours WHERE id=?",(tour_id,))
    if not info:
        await call.answer("Маршрут не найден.",show_alert=True)
        return
    name, price = info[0]
    if price and not os.getenv("SUPPORT_CONTACT"):
        await call.answer("Оплата временно недоступна.",show_alert=True)
        return
    if price and not query("SELECT 1 FROM purchases WHERE user_id=? AND tour_id=?",(call.from_user.id,tour_id)):
        await call.answer()
        await call.message.answer_invoice(title=name,description="Доступ к экскурсии",payload=f"tour:{tour_id}:{price}",currency="XTR",prices=[LabeledPrice(label=name,amount=price)],provider_token="")
        return
    stops = query("SELECT title,description,photo,lat,lon FROM stops WHERE tour_id=? ORDER BY id", (tour_id,))
    if not stops:
        await call.answer("Маршрут пуст.", show_alert=True)
        return
    if position is None:
        saved = query("SELECT position FROM progress WHERE user_id=? AND tour_id=?", (call.from_user.id,tour_id))
        position = saved[0][0] if saved else 0
    position = max(0,min(position,len(stops)-1))
    query("INSERT INTO progress VALUES (?,?,?) ON CONFLICT(user_id,tour_id) DO UPDATE SET position=excluded.position", (call.from_user.id,tour_id,position), True)
    title, description, photo, lat, lon = stops[position]
    buttons = []
    if position > 0:
        buttons.append(("Назад",f"stop:{tour_id}:{position-1}"))
    if position < len(stops)-1:
        buttons.append(("Далее",f"stop:{tour_id}:{position+1}"))
    else:
        buttons.append(("Начать заново",f"stop:{tour_id}:0"))
    buttons.append(("К экскурсиям","catalog"))
    markup = keyboard(buttons)
    if lat is not None and lon is not None:
        markup.inline_keyboard.insert(0,[InlineKeyboardButton(text="📍 Карта точки",url=f"https://www.google.com/maps/search/?api=1&query={lat},{lon}")])
    coords = [(s[3],s[4]) for s in stops if s[3] is not None and s[4] is not None]
    if len(coords) >= 2:
        points = [f"{a},{b}" for a,b in coords[:10]]
        url = "https://www.google.com/maps/dir/?" + urlencode({"api":1,"origin":points[0],"destination":points[-1],"waypoints":"|".join(points[1:-1]),"travelmode":"walking"})
        markup.inline_keyboard.insert(0,[InlineKeyboardButton(text="🗺 Маршрут (до 10 точек)",url=url)])
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
    await message.answer("Управление экскурсиями",reply_markup=keyboard([("Создать экскурсию","adm:new"),("Добавить остановку","adm:stop"),("Назначить цену","adm:price"),("Статистика","adm:stats"),("Список маршрутов","adm:list")]))

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
    if not title or len(title)>32:
        await message.answer("Укажите название (до 32 символов).")
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


class AdminInput(StatesGroup):
    value = State()

@dp.callback_query(F.data.startswith("adm:"))
async def admin_action(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMINS:
        await call.answer("Нет доступа",show_alert=True)
        return
    action = call.data.split(":")[1]
    await call.answer()
    if action == "stats":
        counts = [query(sql)[0][0] for sql in ("SELECT COUNT(*) FROM users","SELECT COUNT(*) FROM tours","SELECT COUNT(*) FROM progress","SELECT COUNT(*) FROM purchases","SELECT COALESCE(SUM(amount),0) FROM purchases")]
        await call.message.answer(f"Пользователи: {counts[0]}\nЭкскурсии: {counts[1]}\nНачатые маршруты: {counts[2]}\nПлатежи: {counts[3]}\nВыручка: {counts[4]} Stars")
        return
    if action == "list":
        await call.message.answer("\n".join(f"{i}: {t} ({p} Stars)" for i,t,p in query("SELECT id,title,price FROM tours")) or "Нет маршрутов.")
        return
    prompts = {"new":"Введите название экскурсии", "stop":"Введите ID | Название | Описание | Фото (или пусто) | Широта | Долгота", "price":"Введите ID экскурсии | Цена в Stars (0 — бесплатно)"}
    if action not in prompts:
        return
    await state.set_state(AdminInput.value)
    await state.update_data(action=action)
    await call.message.answer(prompts[action]+"\n/cancel — отмена")

@dp.message(Command("cancel"))
async def cancel(message: Message,state: FSMContext):
    await state.clear()
    await message.answer("Отменено.")

@dp.message(AdminInput.value, F.text)
async def admin_input(message: Message,state: FSMContext):
    if message.from_user.id not in ADMINS:
        await state.clear()
        return
    action = (await state.get_data()).get("action")
    parts = [x.strip() for x in message.text.split("|")]
    try:
        if action == "new":
            title = message.text.strip()
            if not 1 <= len(title) <= 32:
                raise ValueError()
            query("INSERT INTO tours(title) VALUES (?)",(title,),True)
        elif action == "price":
            i,p = map(int,parts)
            if not 0 <= p <= 10000 or not query("SELECT 1 FROM tours WHERE id=?",(i,)):
                raise ValueError()
            query("UPDATE tours SET price=? WHERE id=?",(p,i),True)
        elif action == "stop":
            i,title,description,photo,lat,lon = parts
            lat,lon = float(lat),float(lon)
            if not -90<=lat<=90 or not -180<=lon<=180 or not title or not description or len(title)+len(description)>3900:
                raise ValueError()
            query("INSERT INTO stops(tour_id,title,description,photo,lat,lon) VALUES (?,?,?,?,?,?)",(int(i),title,description,photo or None,lat,lon),True)
        else:
            raise ValueError()
    except (ValueError,sqlite3.IntegrityError):
        await message.answer("Неверные данные. Повторите ввод или /cancel.")
        return
    await state.clear()
    await message.answer("Сохранено.")

@dp.pre_checkout_query()
async def checkout(event: PreCheckoutQuery):
    try:
        prefix,i,price = event.invoice_payload.split(":")
        i,price = int(i),int(price)
        valid = prefix=="tour" and event.currency=="XTR" and price==event.total_amount and price>0 and bool(query("SELECT 1 FROM tours WHERE id=? AND price=?",(i,price))) and not query("SELECT 1 FROM purchases WHERE user_id=? AND tour_id=?",(event.from_user.id,i))
    except ValueError:
        valid = False
    await event.answer(ok=bool(valid),error_message=None if valid else "Счет недействителен. Откройте каталог заново.")

@dp.message(F.successful_payment)
async def payment(message: Message):
    p = message.successful_payment
    try:
        prefix,i,price = p.invoice_payload.split(":")
        i,price = int(i),int(price)
    except ValueError:
        return
    if prefix!="tour" or p.currency!="XTR" or p.total_amount!=price or price<=0:
        return
    query("INSERT OR IGNORE INTO purchases VALUES (?,?,?,?)",(p.telegram_payment_charge_id,message.from_user.id,i,price),True)
    await message.answer("Оплата получена. Экскурсия доступна.",reply_markup=keyboard([("Открыть экскурсию",f"tour:{i}")]))

@dp.message(Command("paysupport"))
async def support(message: Message):
    contact = os.getenv("SUPPORT_CONTACT")
    await message.answer(f"Поддержка по оплате: {contact}" if contact else "Обратитесь к владельцу бота. Укажите ID платежа.")


async def main():
    init()
    async with Bot(token=os.environ["BOT_TOKEN"]) as bot:
        await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
