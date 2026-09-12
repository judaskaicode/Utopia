import asyncio
import os
import re
from typing import Optional
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

DB_PATH = "loyalty.sqlite"

TOKEN = os.getenv("BOT_TOKEN")

ADMIN_IDS = [
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip()
]

BONUS_RATE = float(os.getenv("BONUS_RATE", "0.05"))

router = Router()


# =========================
# RENDER HEALTH SERVER
# =========================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass


def start_health_server():
    port = int(os.getenv("PORT", "10000"))

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    server.serve_forever()


# =========================
# DATABASE
# =========================

async def db_init():

    async with aiosqlite.connect(DB_PATH) as db:

        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                created_at TEXT DEFAULT (datetime('now')),
                points INTEGER DEFAULT 0
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount_cents INTEGER NOT NULL,
                photo_file_id TEXT NOT NULL,
                comment TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now')),
                decided_at TEXT,
                decided_by INTEGER,
                points_awarded INTEGER DEFAULT 0
            )
        """)

        await db.commit()


async def ensure_user(user_id: int):

    async with aiosqlite.connect(DB_PATH) as db:

        await db.execute(
            "INSERT OR IGNORE INTO users(user_id) VALUES(?)",
            (user_id,)
        )

        await db.commit()


async def create_submission(
    user_id: int,
    amount_cents: int,
    photo_file_id: str,
    comment: Optional[str]
) -> int:

    async with aiosqlite.connect(DB_PATH) as db:

        cur = await db.execute(
            """
            INSERT INTO submissions
            (user_id, amount_cents, photo_file_id, comment)
            VALUES (?, ?, ?, ?)
            """,
            (
                user_id,
                amount_cents,
                photo_file_id,
                comment
            )
        )

        await db.commit()

        return cur.lastrowid


async def get_submission(submission_id: int):

    async with aiosqlite.connect(DB_PATH) as db:

        cur = await db.execute(
            """
            SELECT
                id,
                user_id,
                amount_cents,
                photo_file_id,
                comment,
                status,
                points_awarded
            FROM submissions
            WHERE id=?
            """,
            (submission_id,)
        )

        return await cur.fetchone()


async def set_submission_status(
    submission_id: int,
    status: str,
    decided_by: int,
    points_awarded: int = 0
):

    async with aiosqlite.connect(DB_PATH) as db:

        await db.execute(
            """
            UPDATE submissions
            SET
                status=?,
                decided_at=datetime('now'),
                decided_by=?,
                points_awarded=?
            WHERE id=?
            """,
            (
                status,
                decided_by,
                points_awarded,
                submission_id
            )
        )

        await db.commit()


async def add_points(user_id: int, points: int):

    async with aiosqlite.connect(DB_PATH) as db:

        await db.execute(
            "INSERT OR IGNORE INTO users(user_id) VALUES(?)",
            (user_id,)
        )

        await db.execute(
            """
            UPDATE users
            SET points = points + ?
            WHERE user_id=?
            """,
            (
                points,
                user_id
            )
        )

        await db.commit()


async def get_balance(user_id: int) -> int:

    async with aiosqlite.connect(DB_PATH) as db:

        cur = await db.execute(
            "SELECT points FROM users WHERE user_id=?",
            (user_id,)
        )

        row = await cur.fetchone()

        return int(row[0]) if row else 0


# =========================
# HELPERS
# =========================

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def parse_amount_to_cents(text: str) -> Optional[int]:

    text = (
        text
        .strip()
        .replace(" ", "")
        .replace(",", ".")
    )

    if not re.fullmatch(r"\d+(\.\d{1,2})?", text):
        return None

    if "." in text:

        rub, kop = text.split(".", 1)

        kop = (kop + "0")[:2]

    else:

        rub = text
        kop = "00"

    return int(rub) * 100 + int(kop)


def cents_to_money(cents: int) -> str:

    return f"{cents // 100}.{cents % 100:02d}"


def admin_keyboard(submission_id: int):

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Начислить (авто)",
                    callback_data=f"appr_auto:{submission_id}"
                ),
                InlineKeyboardButton(
                    text="✅ Начислить (вручную)",
                    callback_data=f"appr_manual:{submission_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"reject:{submission_id}"
                )
            ]
        ]
    )


# =========================
# STATES
# =========================

class SubmitFlow(StatesGroup):

    waiting_amount = State()
    waiting_photo = State()
    waiting_comment = State()


class AdminManual(StatesGroup):

    waiting_points = State()


# =========================
# USER
# =========================

@router.message(Command("start"))
async def start(message: Message):

    await ensure_user(message.from_user.id)

    await message.answer(
        "🛍 Программа лояльности Utopia\n\n"
        "Команды:\n"
        "/submit — отправить чек\n"
        "/balance — мой баланс\n"
        "/help — помощь"
    )


@router.message(Command("help"))
async def help_cmd(message: Message):

    await message.answer(
        "Как получить бонусы:\n\n"
        "1. Отправьте чек командой /submit\n"
        "2. Введите сумму покупки\n"
        "3. Прикрепите фотографию чека\n"
        "4. Администратор проверит покупку\n"
        "5. После проверки бонусы будут начислены"
    )


@router.message(Command("balance"))
async def balance(message: Message):

    await ensure_user(message.from_user.id)

    points = await get_balance(message.from_user.id)

    await message.answer(
        f"💰 Ваш баланс: {points} бонусов"
    )


@router.message(Command("submit"))
async def submit(
    message: Message,
    state: FSMContext
):

    await ensure_user(message.from_user.id)

    await state.set_state(
        SubmitFlow.waiting_amount
    )

    await state.update_data(
        amount_cents=None,
        photo_file_id=None,
        comment=None
    )

    await message.answer(
        "💰 Введите сумму покупки.\n\n"
        "Например: 1999 или 1999,50"
    )


@router.message(SubmitFlow.waiting_amount)
async def got_amount(
    message: Message,
    state: FSMContext
):

    cents = parse_amount_to_cents(
        message.text or ""
    )

    if cents is None or cents <= 0:

        await message.answer(
            "❌ Не удалось распознать сумму.\n\n"
            "Например: 1500 или 1500,50"
        )

        return

    await state.update_data(
        amount_cents=cents
    )

    await state.set_state(
        SubmitFlow.waiting_photo
    )

    await message.answer(
        "📸 Теперь отправьте фотографию чека."
    )


@router.message(SubmitFlow.waiting_photo)
async def got_photo(
    message: Message,
    state: FSMContext
):

    if not message.photo:

        await message.answer(
            "❌ Нужно отправить именно фотографию чека."
        )

        return

    photo = message.photo[-1]

    await state.update_data(
        photo_file_id=photo.file_id
    )

    await state.set_state(
        SubmitFlow.waiting_comment
    )

    await message.answer(
        "✏️ Напишите комментарий к покупке.\n\n"
        "Если комментарий не нужен — отправьте символ -"
    )


@router.message(SubmitFlow.waiting_comment)
async def got_comment(
    message: Message,
    state: FSMContext,
    bot: Bot
):

    data = await state.get_data()

    amount_cents = data["amount_cents"]
    photo_file_id = data["photo_file_id"]

    comment = (
        message.text or ""
    ).strip()

    if comment == "-":
        comment = None

    submission_id = await create_submission(
        user_id=message.from_user.id,
        amount_cents=amount_cents,
        photo_file_id=photo_file_id,
        comment=comment
    )

    caption = (
        f"🧾 Новая заявка #{submission_id}\n\n"
        f"👤 Telegram ID: {message.from_user.id}\n"
        f"💰 Сумма: {cents_to_money(amount_cents)} ₽\n"
        f"💬 Комментарий: {comment or '—'}"
    )

    for admin_id in ADMIN_IDS:

        await bot.send_photo(
            chat_id=admin_id,
            photo=photo_file_id,
            caption=caption,
            reply_markup=admin_keyboard(
                submission_id
            )
        )

    await state.clear()

    await message.answer(
        f"✅ Заявка #{submission_id} отправлена "
        "на проверку.\n\n"
        "После проверки вам придёт уведомление."
    )


# =========================
# ADMIN
# =========================

@router.callback_query(
    F.data.startswith(
        ("appr_auto:", "appr_manual:", "reject:")
    )
)
async def admin_actions(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot
):

    if not is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "Нет доступа.",
            show_alert=True
        )

        return

    action, sid = callback.data.split(":", 1)

    submission_id = int(sid)

    row = await get_submission(
        submission_id
    )

    if not row:

        await callback.answer(
            "Заявка не найдена.",
            show_alert=True
        )

        return

    (
        _id,
        user_id,
        amount_cents,
        photo_file_id,
        comment,
        status,
        points_awarded
    ) = row

    if status != "pending":

        await callback.answer(
            f"Заявка уже обработана: {status}",
            show_alert=True
        )

        return

    if action == "reject":

        await set_submission_status(
            submission_id,
            "rejected",
            callback.from_user.id
        )

        await callback.message.edit_caption(
            caption=callback.message.caption
            + "\n\n❌ Отклонено."
        )

        await bot.send_message(
            user_id,
            f"❌ Заявка #{submission_id} отклонена."
        )

        await callback.answer(
            "Заявка отклонена."
        )

        return

    if action == "appr_auto":

        points = int(
            round(
                (amount_cents / 100)
                * BONUS_RATE
            )
        )

        if points < 1:
            points = 1

        await add_points(
            user_id,
            points
        )

        await set_submission_status(
            submission_id,
            "approved",
            callback.from_user.id,
            points
        )

        await callback.message.edit_caption(
            caption=callback.message.caption
            + f"\n\n✅ Начислено: {points} бонусов."
        )

        balance = await get_balance(
            user_id
        )

        await bot.send_message(
            user_id,
            f"🎉 Заявка #{submission_id} подтверждена!\n\n"
            f"Начислено: {points} бонусов\n"
            f"💰 Ваш баланс: {balance} бонусов"
        )

        await callback.answer(
            "Бонусы начислены."
        )

        return

    if action == "appr_manual":

        await state.set_state(
            AdminManual.waiting_points
        )

        await state.update_data(
            manual_submission_id=submission_id
        )

        await callback.answer()

        await bot.send_message(
            callback.from_user.id,
            f"Введите количество бонусов "
            f"для заявки #{submission_id}:"
        )


@router.message(
    AdminManual.waiting_points
)
async def admin_manual_points(
    message: Message,
    state: FSMContext,
    bot: Bot
):

    if not is_admin(
        message.from_user.id
    ):

        await state.clear()

        return

    data = await state.get_data()

    submission_id = int(
        data.get(
            "manual_submission_id",
            0
        )
    )

    row = await get_submission(
        submission_id
    )

    if not row:

        await message.answer(
            "Заявка не найдена."
        )

        await state.clear()

        return

    (
        _id,
        user_id,
        amount_cents,
        photo_file_id,
        comment,
        status,
        points_awarded
    ) = row

    if status != "pending":

        await message.answer(
            "Заявка уже обработана."
        )

        await state.clear()

        return

    try:

        points = int(
            (message.text or "").strip()
        )

        if points <= 0:
            raise ValueError

    except ValueError:

        await message.answer(
            "Введите целое число больше 0."
        )

        return

    await add_points(
        user_id,
        points
    )

    await set_submission_status(
        submission_id,
        "approved",
        message.from_user.id,
        points
    )

    balance = await get_balance(
        user_id
    )

    await message.answer(
        f"✅ Начислено {points} бонусов."
    )

    await bot.send_message(
        user_id,
        f"🎉 Заявка #{submission_id} подтверждена!\n\n"
        f"Начислено: {points} бонусов\n"
        f"💰 Ваш баланс: {balance} бонусов"
    )

    await state.clear()


# =========================
# START
# =========================

async def main():

    if not TOKEN:
        raise RuntimeError(
            "BOT_TOKEN не задан."
        )

    if not ADMIN_IDS:
        raise RuntimeError(
            "ADMIN_IDS не задан."
        )

    await db_init()

    bot = Bot(
        token=TOKEN
    )

    dp = Dispatcher(
        storage=MemoryStorage()
    )

    dp.include_router(
        router
    )

    await dp.start_polling(
        bot
    )


if __name__ == "__main__":

    health_thread = threading.Thread(
        target=start_health_server,
        daemon=True
    )

    health_thread.start()

    asyncio.run(main())
