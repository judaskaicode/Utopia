import asyncio
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

import aiosqlite

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)


# =========================================================
# НАСТРОЙКИ
# =========================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

ADMIN_IDS = [
    int(x.strip())
    for x in os.environ.get("ADMIN_IDS", "").split(",")
    if x.strip()
]

DB_PATH = "loyalty.sqlite"


# =========================================================
# КАТЕГОРИИ
# =========================================================

CATEGORIES = {
    "regular": "🛍 Обычная покупка",
    "merch": "👕 Мерч Утопии",
}


# =========================================================
# ГЛАВНОЕ МЕНЮ
# =========================================================

MAIN_MENU = ReplyKeyboardMarkup(
    [
        ["🛍 Отправить покупку"],
        ["💳 Мой баланс", "🎁 Потратить баллы"],
        ["🔄 Сбросить"],
    ],
    resize_keyboard=True
)


# =========================================================
# RENDER HEALTH SERVER
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass


def start_health_server():

    port = int(
        os.environ.get("PORT", "10000")
    )

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    server.serve_forever()


# =========================================================
# БОНУСЫ
# =========================================================

def get_points(
    total_spent: float,
    purchase_amount: float,
    category: str
) -> float:

    if category == "merch":

        if total_spent <= 10000:
            return round(purchase_amount * 0.05, 2)

        elif total_spent <= 20000:
            return round(purchase_amount * 0.10, 2)

        else:
            return round(purchase_amount * 0.15, 2)

    else:

        if total_spent <= 10000:
            return round(purchase_amount * 0.03, 2)

        elif total_spent <= 20000:
            return round(purchase_amount * 0.07, 2)

        else:
            return round(purchase_amount * 0.10, 2)


def get_level_name(
    total_spent: float
) -> str:

    if total_spent <= 10000:
        return "🥉 Уровень 1"

    elif total_spent <= 20000:
        return "🥈 Уровень 2"

    else:
        return "🥇 Уровень 3"


def get_next_level_info(
    total_spent: float
) -> str:

    if total_spent <= 10000:

        remaining = 10001 - total_spent

        return (
            f"До уровня 2 осталось: "
            f"*{remaining:.2f}₽*"
        )

    elif total_spent <= 20000:

        remaining = 20001 - total_spent

        return (
            f"До уровня 3 осталось: "
            f"*{remaining:.2f}₽*"
        )

    else:

        return (
            "🏆 Максимальный уровень достигнут!"
        )


# =========================================================
# DATABASE
# =========================================================

async def init_db():

    async with aiosqlite.connect(DB_PATH) as db:

        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                name TEXT DEFAULT '',
                username TEXT DEFAULT '',
                points REAL DEFAULT 0,
                total_spent REAL DEFAULT 0
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS pending_purchases (
                pending_key TEXT PRIMARY KEY,
                user_id INTEGER,
                amount REAL,
                photo_id TEXT,
                name TEXT,
                username TEXT,
                category TEXT DEFAULT 'regular'
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS pending_redeems (
                user_id INTEGER PRIMARY KEY,
                points REAL,
                discount INTEGER,
                name TEXT,
                username TEXT,
                promo_code TEXT
            )
        """)

        await db.commit()


async def get_user(
    user_id: int
) -> dict:

    async with aiosqlite.connect(DB_PATH) as db:

        db.row_factory = aiosqlite.Row

        cursor = await db.execute(
            """
            SELECT *
            FROM users
            WHERE user_id = ?
            """,
            (user_id,)
        )

        row = await cursor.fetchone()

        if row:
            return dict(row)

        return {
            "user_id": user_id,
            "name": "",
            "username": "",
            "points": 0.0,
            "total_spent": 0.0,
        }


async def save_user(
    user: dict
):

    async with aiosqlite.connect(DB_PATH) as db:

        await db.execute(
            """
            INSERT INTO users (
                user_id,
                name,
                username,
                points,
                total_spent
            )
            VALUES (?, ?, ?, ?, ?)

            ON CONFLICT(user_id)
            DO UPDATE SET
                name = excluded.name,
                username = excluded.username,
                points = excluded.points,
                total_spent = excluded.total_spent
            """,
            (
                user["user_id"],
                user["name"],
                user["username"],
                user["points"],
                user["total_spent"],
            )
        )

        await db.commit()


async def save_pending(
    pending_key: str,
    data: dict
):

    async with aiosqlite.connect(DB_PATH) as db:

        await db.execute(
            """
            INSERT OR REPLACE INTO pending_purchases (
                pending_key,
                user_id,
                amount,
                photo_id,
                name,
                username,
                category
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pending_key,
                data["user_id"],
                data["amount"],
                data["photo_id"],
                data["name"],
                data["username"],
                data["category"],
            )
        )

        await db.commit()


async def get_pending(
    pending_key: str
):

    async with aiosqlite.connect(DB_PATH) as db:

        db.row_factory = aiosqlite.Row

        cursor = await db.execute(
            """
            SELECT *
            FROM pending_purchases
            WHERE pending_key = ?
            """,
            (pending_key,)
        )

        row = await cursor.fetchone()

        return dict(row) if row else None


async def delete_pending(
    pending_key: str
):

    async with aiosqlite.connect(DB_PATH) as db:

        await db.execute(
            """
            DELETE FROM pending_purchases
            WHERE pending_key = ?
            """,
            (pending_key,)
        )

        await db.commit()


async def save_redeem(
    data: dict
):

    async with aiosqlite.connect(DB_PATH) as db:

        await db.execute(
            """
            INSERT OR REPLACE INTO pending_redeems (
                user_id,
                points,
                discount,
                name,
                username,
                promo_code
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                data["user_id"],
                data["points"],
                data["discount"],
                data["name"],
                data["username"],
                data["promo_code"],
            )
        )

        await db.commit()


async def get_redeem(
    user_id: int
):

    async with aiosqlite.connect(DB_PATH) as db:

        db.row_factory = aiosqlite.Row

        cursor = await db.execute(
            """
            SELECT *
            FROM pending_redeems
            WHERE user_id = ?
            """,
            (user_id,)
        )

        row = await cursor.fetchone()

        return dict(row) if row else None


async def delete_redeem(
    user_id: int
):

    async with aiosqlite.connect(DB_PATH) as db:

        await db.execute(
            """
            DELETE FROM pending_redeems
            WHERE user_id = ?
            """,
            (user_id,)
        )

        await db.commit()


async def get_all_users():

    async with aiosqlite.connect(DB_PATH) as db:

        db.row_factory = aiosqlite.Row

        cursor = await db.execute(
            """
            SELECT *
            FROM users
            ORDER BY total_spent DESC
            """
        )

        rows = await cursor.fetchall()

        return [
            dict(row)
            for row in rows
        ]


# =========================================================
# СОСТОЯНИЯ
# =========================================================

WAITING_CATEGORY = 0
WAITING_PHOTO = 1
WAITING_AMOUNT = 2


# =========================================================
# ПРОВЕРКА АДМИНА
# =========================================================

def is_admin(
    user_id: int
) -> bool:

    return user_id in ADMIN_IDS


# =========================================================
# /start
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    u = await get_user(user.id)

    u["name"] = user.full_name
    u["username"] = user.username or ""

    await save_user(u)

    text = (
        f"👋 Привет, {user.first_name}!\n\n"
        "🎁 *Программа лояльности*\n\n"

        "За каждую покупку ты получаешь баллы:\n\n"

        "*🛍 Обычная покупка:*\n"
        "🥉 Уровень 1 (до 10 000₽) — 3%\n"
        "🥈 Уровень 2 (10 001–20 000₽) — 7%\n"
        "🥇 Уровень 3 (от 20 001₽) — 10%\n\n"

        "*👕 Мерч Утопии:*\n"
        "🥉 Уровень 1 (до 10 000₽) — 5%\n"
        "🥈 Уровень 2 (10 001–20 000₽) — 10%\n"
        "🥇 Уровень 3 (от 20 001₽) — 15%\n\n"

        "💳 *100 баллов = 100₽ скидки*\n\n"

        "Выберите нужное действие ниже 👇"
    )

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=MAIN_MENU
    )


# =========================================================
# /balance
# =========================================================

async def balance(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    u = await get_user(user.id)

    text = (
        f"💳 *Ваш баланс*\n\n"
        f"Баллы: *{u['points']:.2f}* 🪙\n"
        f"Всего потрачено: "
        f"*{u['total_spent']:.2f}₽*\n"
        f"Уровень: "
        f"{get_level_name(u['total_spent'])}\n"
        f"{get_next_level_info(u['total_spent'])}\n\n"
        f"💡 {u['points']:.0f} баллов = "
        f"{u['points']:.0f}₽ скидки"
    )

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=MAIN_MENU
    )


# =========================================================
# НАЧАЛО ПОКУПКИ
# =========================================================

async def purchase_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    keyboard = [[
        InlineKeyboardButton(
            "🛍 Обычная покупка",
            callback_data="cat_regular"
        ),
        InlineKeyboardButton(
            "👕 Мерч Утопии",
            callback_data="cat_merch"
        ),
    ]]

    await update.message.reply_text(
        "Выберите категорию покупки:",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )

    return WAITING_CATEGORY


# =========================================================
# КАТЕГОРИЯ
# =========================================================

async def purchase_category(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    category = query.data.replace(
        "cat_",
        ""
    )

    context.user_data["category"] = category

    category_name = CATEGORIES[category]

    await query.edit_message_text(
        f"Категория: *{category_name}*\n\n"
        "📸 Теперь отправьте фото чека или покупки:",
        parse_mode="Markdown"
    )

    return WAITING_PHOTO


# =========================================================
# ФОТО
# =========================================================

async def purchase_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message.photo:

        await update.message.reply_text(
            "❌ Отправьте именно фотографию."
        )

        return WAITING_PHOTO

    context.user_data["photo_id"] = (
        update.message.photo[-1].file_id
    )

    await update.message.reply_text(
        "💰 Напишите сумму покупки в рублях "
        "(например: 1500):"
    )

    return WAITING_AMOUNT


# =========================================================
# СУММА
# =========================================================

async def purchase_amount(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = (
        update.message.text
        .strip()
        .replace(",", ".")
        .replace(" ", "")
    )

    try:

        amount = float(text)

        if amount <= 0:
            raise ValueError

    except ValueError:

        await update.message.reply_text(
            "❌ Введите корректную сумму, "
            "например: 1500"
        )

        return WAITING_AMOUNT

    user = update.effective_user

    u = await get_user(user.id)

    photo_id = context.user_data.get(
        "photo_id"
    )

    category = context.user_data.get(
        "category",
        "regular"
    )

    category_name = CATEGORIES[category]

    loop = asyncio.get_running_loop()

    pending_key = (
        f"pending_{user.id}_"
        f"{int(amount * 100)}_"
        f"{int(loop.time() * 1000)}"
    )

    await save_pending(
        pending_key,
        {
            "user_id": user.id,
            "amount": amount,
            "photo_id": photo_id,
            "name": user.full_name,
            "username": user.username or "",
            "category": category,
        }
    )

    points_will_earn = get_points(
        u["total_spent"] + amount,
        amount,
        category
    )

    keyboard = [[
        InlineKeyboardButton(
            "✅ Подтвердить",
            callback_data=(
                f"approve_{pending_key}"
            )
        ),
        InlineKeyboardButton(
            "❌ Отклонить",
            callback_data=(
                f"reject_{pending_key}"
            )
        ),
    ]]

    caption = (
        "🛒 *Новая покупка на проверку*\n\n"
        f"👤 Клиент: {user.full_name}"
        + (
            f" (@{user.username})"
            if user.username
            else ""
        )
        + f"\n🆔 ID: `{user.id}`\n"
        f"📦 Категория: *{category_name}*\n"
        f"💰 Сумма: *{amount:.2f}₽*\n"
        f"📊 Уровень: "
        f"{get_level_name(u['total_spent'])}\n"
        f"🪙 Будет начислено: "
        f"*{points_will_earn:.2f} баллов*"
    )

    for admin_id in ADMIN_IDS:

        await context.bot.send_photo(
            chat_id=admin_id,
            photo=photo_id,
            caption=caption,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                keyboard
            )
        )

    await update.message.reply_text(
        f"✅ Покупка на сумму "
        f"*{amount:.2f}₽* "
        "отправлена на проверку.\n"
        "Ожидайте подтверждения! 🕐",
        parse_mode="Markdown",
        reply_markup=MAIN_MENU
    )

    context.user_data.clear()

    return ConversationHandler.END


# =========================================================
# /cancel
# =========================================================

async def purchase_cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data.clear()

    await update.message.reply_text(
        "❌ Текущая операция сброшена.\n\n"
        "Можно начать заново.",
        reply_markup=MAIN_MENU
    )

    return ConversationHandler.END


# =========================================================
# ПОДТВЕРЖДЕНИЕ / ОТКЛОНЕНИЕ ПОКУПКИ
# =========================================================

async def admin_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    if not is_admin(
        query.from_user.id
    ):

        await query.answer(
            "⛔ Нет доступа.",
            show_alert=True
        )

        return

    await query.answer()

    data = query.data

    action, pending_key = data.split(
        "_",
        1
    )

    pending = await get_pending(
        pending_key
    )

    if not pending:

        await query.edit_message_caption(
            caption="⚠️ Заявка уже обработана."
        )

        return

    user_id = pending["user_id"]
    amount = pending["amount"]
    name = pending["name"]
    username = pending["username"]
    category = pending["category"]

    category_name = CATEGORIES[category]

    u = await get_user(user_id)

    if action == "approve":

        points_earned = get_points(
            u["total_spent"] + amount,
            amount,
            category
        )

        u["points"] = round(
            u["points"] + points_earned,
            2
        )

        u["total_spent"] = round(
            u["total_spent"] + amount,
            2
        )

        await save_user(u)

        await delete_pending(
            pending_key
        )

        username_link = ""

        if username:

            username_link = (
                f" [@{username}]"
                f"(https://t.me/{username})"
            )

        await query.edit_message_caption(
            caption=(
                f"✅ *Подтверждено!*\n\n"
                f"👤 {name}{username_link}\n"
                f"📦 {category_name} | "
                f"💰 {amount:.2f}₽\n"
                f"🪙 Начислено: "
                f"+{points_earned:.2f} баллов"
            ),
            parse_mode="Markdown"
        )

        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "🎉 Покупка подтверждена!\n\n"
                f"📦 Категория: "
                f"*{category_name}*\n"
                f"💰 Сумма: "
                f"*{amount:.2f}₽*\n"
                f"🪙 Начислено: "
                f"*+{points_earned:.2f} баллов*\n"
                f"💳 Ваш баланс: "
                f"*{u['points']:.2f} баллов*\n"
                f"📊 Уровень: "
                f"{get_level_name(u['total_spent'])}\n"
                f"{get_next_level_info(u['total_spent'])}"
            ),
            parse_mode="Markdown"
        )

    elif action == "reject":

        await delete_pending(
            pending_key
        )

        await query.edit_message_caption(
            caption=(
                f"❌ *Отклонено*\n\n"
                f"👤 {name}\n"
                f"📦 {category_name}\n"
                f"💰 {amount:.2f}₽"
            ),
            parse_mode="Markdown"
        )

        await context.bot.send_message(
            chat_id=user_id,
            text=(
                f"😔 Ваша покупка на сумму "
                f"*{amount:.2f}₽* была отклонена.\n\n"
                "Если вы считаете это ошибкой — "
                "свяжитесь с нами: "
                "https://vk.me/poputopia"
            ),
            parse_mode="Markdown"
        )


# =========================================================
# /redeem
# =========================================================

async def redeem(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    u = await get_user(user.id)

    if u["points"] < 1:

        await update.message.reply_text(
            "😔 У вас недостаточно баллов "
            "для обмена.",
            reply_markup=MAIN_MENU
        )

        return

    discount = int(u["points"])

    username = (
        user.username
        or user.full_name
    )

    promo_code = (
        f"@{username}_{discount}баллов"
    )

    await update.message.reply_text(
        f"🎁 *Ваш код на скидку*\n\n"
        f"🪙 Баллов: "
        f"*{u['points']:.2f}*\n"
        f"💰 Скидка: *{discount}₽*\n\n"
        f"Ваш код:\n`{promo_code}`\n\n"
        "Отправьте этот код в нашу группу ВК:\n"
        "https://vk.me/poputopia\n\n"
        "_После проверки скидка будет "
        "подтверждена._",
        parse_mode="Markdown",
        reply_markup=MAIN_MENU
    )

    await save_redeem(
        {
            "user_id": user.id,
            "points": u["points"],
            "discount": discount,
            "name": user.full_name,
            "username": user.username or "",
            "promo_code": promo_code,
        }
    )

    keyboard = [[
        InlineKeyboardButton(
            "✅ Скидка выдана",
            callback_data=(
                f"redeem_approve_{user.id}"
            )
        ),
        InlineKeyboardButton(
            "❌ Отклонить",
            callback_data=(
                f"redeem_reject_{user.id}"
            )
        ),
    ]]

    for admin_id in ADMIN_IDS:

        await context.bot.send_message(
            chat_id=admin_id,
            text=(
                "💳 *Запрос на скидку*\n\n"
                f"👤 {user.full_name}"
                + (
                    f" [@{user.username}]"
                    f"(https://t.me/"
                    f"{user.username})"
                    if user.username
                    else ""
                )
                + f"\n🆔 ID: `{user.id}`\n"
                f"🪙 Баллов: "
                f"*{u['points']:.2f}*\n"
                f"💰 Скидка: "
                f"*{discount}₽*\n"
                f"🔑 Код: `{promo_code}`"
            ),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                keyboard
            )
        )


# =========================================================
# REDEEM CALLBACK
# =========================================================

async def redeem_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    if not is_admin(
        query.from_user.id
    ):

        await query.answer(
            "⛔ Нет доступа.",
            show_alert=True
        )

        return

    await query.answer()

    parts = query.data.split("_")

    action = parts[1]

    user_id = int(parts[2])

    redeem_data = await get_redeem(
        user_id
    )

    if not redeem_data:

        await query.edit_message_text(
            "⚠️ Запрос уже обработан."
        )

        return

    name = redeem_data["name"]
    username = redeem_data["username"]
    discount = redeem_data["discount"]
    promo_code = redeem_data["promo_code"]

    username_link = ""

    if username:

        username_link = (
            f" [@{username}]"
            f"(https://t.me/{username})"
        )

    if action == "approve":

        u = await get_user(user_id)

        u["points"] = 0

        await save_user(u)

        await delete_redeem(
            user_id
        )

        await query.edit_message_text(
            (
                "✅ *Скидка выдана!*\n\n"
                f"👤 {name}{username_link}\n"
                f"💰 Скидка: *{discount}₽*\n"
                f"🔑 Код: `{promo_code}`\n"
                "🪙 Баланс обнулён"
            ),
            parse_mode="Markdown"
        )

        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "🎉 Ваша скидка подтверждена!\n\n"
                f"💰 Скидка: *{discount}₽*\n"
                "🪙 Баланс обнулён\n\n"
                "Спасибо за покупки! "
                "Продолжайте копить баллы 😊"
            ),
            parse_mode="Markdown"
        )

    elif action == "reject":

        await delete_redeem(
            user_id
        )

        await query.edit_message_text(
            (
                "❌ *Отклонено*\n\n"
                f"👤 {name}{username_link}\n"
                f"💰 Скидка: *{discount}₽*"
            ),
            parse_mode="Markdown"
        )

        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "😔 Ваш запрос на скидку "
                "был отклонён.\n\n"
                "Если вы считаете это ошибкой — "
                "свяжитесь с нами: "
                "https://vk.me/poputopia"
            ),
            parse_mode="Markdown"
        )


# =========================================================
# /clients
# =========================================================

async def admin_clients(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(
        update.effective_user.id
    ):

        return

    clients = await get_all_users()

    if not clients:

        await update.message.reply_text(
            "📋 Клиентов пока нет."
        )

        return

    text = "📋 *Список клиентов:*\n\n"

    for u in clients:

        username = ""

        if u.get("username"):

            username = (
                f" (@{u['username']})"
            )

        text += (
            f"👤 {u['name']}{username}\n"
            f"🆔 `{u['user_id']}` | "
            f"🪙 {u['points']:.2f} баллов | "
            f"💰 {u['total_spent']:.2f}₽ | "
            f"{get_level_name(u['total_spent'])}\n\n"
        )

    await update.message.reply_text(
        text,
        parse_mode="Markdown"
    )


# =========================================================
# /admin
# =========================================================

async def admin_help(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(
        update.effective_user.id
    ):

        return

    await update.message.reply_text(
        "🔧 *Команды администратора:*\n\n"
        "/clients — список всех клиентов\n"
        "/admin — эта справка\n\n"
        "Заявки на подтверждение приходят "
        "автоматически с кнопками ✅/❌",
        parse_mode="Markdown"
    )


# =========================================================
# КНОПКИ ГЛАВНОГО МЕНЮ
# =========================================================

async def menu_purchase(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    return await purchase_start(
        update,
        context
    )


async def menu_balance(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await balance(
        update,
        context
    )


async def menu_redeem(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await redeem(
        update,
        context
    )


async def menu_cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    return await purchase_cancel(
        update,
        context
    )


# =========================================================
# УСТАНОВКА КОМАНД TELEGRAM
# =========================================================

async def post_init(
    application: Application
):

    await application.bot.set_my_commands(
        [
            ("start", "Начать"),
            ("purchase", "Отправить покупку"),
            ("balance", "Мой баланс"),
            ("redeem", "Потратить баллы"),
            ("cancel", "Сбросить"),
        ]
    )


# =========================================================
# ЗАПУСК
# =========================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN не задан в Render."
        )

    if not ADMIN_IDS:

        raise RuntimeError(
            "ADMIN_IDS не задан в Render."
        )

    asyncio.run(
        init_db()
    )

    application = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # =====================================================
    # ПОКУПКА
    # =====================================================

    conversation_handler = ConversationHandler(

        entry_points=[
            CommandHandler(
                "purchase",
                purchase_start
            ),
            MessageHandler(
                filters.Regex(
                    r"^🛍 Отправить покупку$"
                ),
                purchase_start
            ),
        ],

        states={

            WAITING_CATEGORY: [
                CallbackQueryHandler(
                    purchase_category,
                    pattern=r"^cat_"
                )
            ],

            WAITING_PHOTO: [
                MessageHandler(
                    filters.PHOTO,
                    purchase_photo
                )
            ],

            WAITING_AMOUNT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    purchase_amount
                )
            ],
        },

        fallbacks=[
            CommandHandler(
                "cancel",
                purchase_cancel
            ),
            MessageHandler(
                filters.Regex(
                    r"^🔄 Сбросить$"
                ),
                purchase_cancel
            ),
        ],
    )

    # =====================================================
    # ОСНОВНЫЕ КОМАНДЫ
    # =====================================================

    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    application.add_handler(
        CommandHandler(
            "balance",
            balance
        )
    )

    application.add_handler(
        CommandHandler(
            "redeem",
            redeem
        )
    )

    # =====================================================
    # КНОПКИ МЕНЮ
    # =====================================================

    application.add_handler(
        MessageHandler(
            filters.Regex(
                r"^💳 Мой баланс$"
            ),
            menu_balance
        )
    )

    application.add_handler(
        MessageHandler(
            filters.Regex(
                r"^🎁 Потратить баллы$"
            ),
            menu_redeem
        )
    )

    # =====================================================
    # ОТМЕНА
    # =====================================================

    application.add_handler(
        CommandHandler(
            "cancel",
            purchase_cancel
        )
    )

    application.add_handler(
        MessageHandler(
            filters.Regex(
                r"^🔄 Сбросить$"
            ),
            menu_cancel
        )
    )

    # =====================================================
    # АДМИН
    # =====================================================

    application.add_handler(
        CommandHandler(
            "clients",
            admin_clients
        )
    )

    application.add_handler(
        CommandHandler(
            "admin",
            admin_help
        )
    )

    # =====================================================
    # CONVERSATION
    # =====================================================

    application.add_handler(
        conversation_handler
    )

    # =====================================================
    # CALLBACKS
    # =====================================================

    application.add_handler(
        CallbackQueryHandler(
            redeem_callback,
            pattern=r"^redeem_"
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            admin_callback,
            pattern=r"^(approve|reject)_"
        )
    )

    print("🤖 Бот запущен!")

    application.run_polling(
        drop_pending_updates=True
    )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    health_thread = threading.Thread(
        target=start_health_server,
        daemon=True
    )

    health_thread.start()

    main()
