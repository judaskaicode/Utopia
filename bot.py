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


# ============================================================
# НАСТРОЙКИ
# ============================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

ADMIN_IDS = [
    int(x.strip())
    for x in os.environ.get("ADMIN_IDS", "").split(",")
    if x.strip()
]

DB_PATH = "loyalty.sqlite"


# ============================================================
# КАТЕГОРИИ
# ============================================================

CATEGORIES = {
    "regular": "🛍 Обычная покупка",
    "merch": "👕 Мерч Утопии",
}


# ============================================================
# ГЛАВНОЕ МЕНЮ
# ============================================================

MAIN_MENU = ReplyKeyboardMarkup(
    [
        ["🛍 Отправить покупку"],
        ["💳 Мой баланс", "🎁 Потратить баллы"],
        ["ℹ️ Как это работает"],
        ["🔄 Сбросить"],
    ],
    resize_keyboard=True
)


# ============================================================
# СОСТОЯНИЯ ПОКУПКИ
# ============================================================

PURCHASE_CATEGORY, PURCHASE_PHOTO, PURCHASE_AMOUNT = range(3)


# ============================================================
# ВЕБ-СЕРВЕР ДЛЯ RENDER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"UTOPIA bot is running")

    def log_message(self, format, *args):
        pass


def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()


# ============================================================
# DATABASE
# ============================================================

async def init_db():

    async with aiosqlite.connect(DB_PATH) as db:

        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                points INTEGER DEFAULT 0,
                total_spent REAL DEFAULT 0
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS pending_purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL,
                category TEXT,
                photo_id TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS pending_redeems (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                points INTEGER,
                code TEXT
            )
        """)

        await db.commit()


async def get_user(user_id, username=None, first_name=None):

    async with aiosqlite.connect(DB_PATH) as db:

        cursor = await db.execute(
            "SELECT * FROM users WHERE user_id = ?",
            (user_id,)
        )

        row = await cursor.fetchone()

        if row is None:

            await db.execute(
                """
                INSERT INTO users
                (user_id, username, first_name, points, total_spent)
                VALUES (?, ?, ?, 0, 0)
                """,
                (
                    user_id,
                    username,
                    first_name
                )
            )

            await db.commit()

            cursor = await db.execute(
                "SELECT * FROM users WHERE user_id = ?",
                (user_id,)
            )

            row = await cursor.fetchone()

        return {
            "user_id": row[0],
            "username": row[1],
            "first_name": row[2],
            "points": row[3],
            "total_spent": row[4],
        }


# ============================================================
# БОНУСЫ
# ============================================================

def get_points(total_spent, amount, category):

    if category == "regular":

        if total_spent <= 10000:
            rate = 0.03

        elif total_spent <= 20000:
            rate = 0.07

        else:
            rate = 0.10

    elif category == "merch":

        if total_spent <= 10000:
            rate = 0.05

        elif total_spent <= 20000:
            rate = 0.10

        else:
            rate = 0.15

    else:
        rate = 0

    return int(amount * rate)


def get_level(total_spent):

    if total_spent <= 10000:
        return "🥉 Уровень 1"

    elif total_spent <= 20000:
        return "🥈 Уровень 2"

    return "🥇 Уровень 3"


def get_next_level_text(total_spent):

    if total_spent <= 10000:

        remaining = 10000 - total_spent

        return f"До 🥈 Уровня 2: ещё {remaining:.0f} ₽"

    elif total_spent <= 20000:

        remaining = 20000 - total_spent

        return f"До 🥇 Уровня 3: ещё {remaining:.0f} ₽"

    return "🏆 Максимальный уровень"


# ============================================================
# START
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    await get_user(
        user.id,
        user.username,
        user.first_name
    )

    text = (
        "🌙 <b>UTOPIA</b>\n"
        "<i>Программа лояльности</i>\n\n"

        "Добро пожаловать в клуб Утопии.\n"
        "Здесь хранятся твои покупки, баллы и скидки.\n\n"

        "💳 <b>100 баллов = 100 ₽ скидки</b>\n\n"

        "Выберите нужное действие ниже 👇"
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=MAIN_MENU
    )


# ============================================================
# КАК ЭТО РАБОТАЕТ
# ============================================================

async def how_it_works(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = (
        "🌙 <b>Как работает программа</b>\n\n"

        "За каждую подтверждённую покупку ты получаешь бонусные баллы.\n\n"

        "🥉 <b>Уровень 1</b>\n"
        "До 10 000 ₽\n"
        "• Обычная покупка — 3%\n"
        "• Мерч Утопии — 5%\n\n"

        "🥈 <b>Уровень 2</b>\n"
        "От 10 001 до 20 000 ₽\n"
        "• Обычная покупка — 7%\n"
        "• Мерч Утопии — 10%\n\n"

        "🥇 <b>Уровень 3</b>\n"
        "Более 20 000 ₽\n"
        "• Обычная покупка — 10%\n"
        "• Мерч Утопии — 15%\n\n"

        "💳 <b>100 баллов = 100 ₽ скидки.</b>\n\n"

        "На данный момент баллами можно оплатить до 100% стоимости покупки. "
        "В будущем это правило будет изменено.\n\n"

        "Если возникли вопросы по работе бота, пишите админу "
        "<b>@judaskai</b>\n"
        "<i>Админ отвечает исключительно на вопросы, связанные с ботом. "
        "Любые другие вопросы игнорируются 😽</i>\n\n"

        "P.S. Подтверждение покупок и, соответственно, начисление баллов "
        "происходит вручную, а не автоматически. Поэтому в некоторых "
        "случаях это может занять время, но не более 12 часов.\n\n"

        "Чтобы получить баллы за покупку, нажми "
        "«🛍 Отправить покупку» и отправь чек."
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=MAIN_MENU
    )


# ============================================================
# BALANCE
# ============================================================

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    u = await get_user(
        user.id,
        user.username,
        user.first_name
    )

    level = get_level(u["total_spent"])
    next_level = get_next_level_text(u["total_spent"])

    text = (
        "🌙 <b>Мой баланс</b>\n\n"
        f"💳 Баллы: <b>{u['points']}</b>\n"
        f"💰 Всего покупок: <b>{u['total_spent']:.0f} ₽</b>\n"
        f"🏆 {level}\n\n"
        f"{next_level}\n\n"
        "💡 100 баллов = 100 ₽ скидки"
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=MAIN_MENU
    )


# ============================================================
# PURCHASE START
# ============================================================

async def purchase_start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    keyboard = [
        [
            InlineKeyboardButton(
                CATEGORIES["regular"],
                callback_data="category_regular"
            )
        ],
        [
            InlineKeyboardButton(
                CATEGORIES["merch"],
                callback_data="category_merch"
            )
        ],
    ]

    await update.message.reply_text(
        "🌙 <b>Отправить покупку</b>\n\n"
        "Выбери категорию покупки:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    return PURCHASE_CATEGORY


# ============================================================
# CATEGORY
# ============================================================

async def purchase_category(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    category = query.data.replace(
        "category_",
        ""
    )

    context.user_data["purchase_category"] = category

    await query.edit_message_text(
        f"Выбрано: <b>{CATEGORIES[category]}</b>\n\n"
        "Теперь отправь фотографию чека.",
        parse_mode="HTML"
    )

    return PURCHASE_PHOTO


# ============================================================
# PHOTO
# ============================================================

async def purchase_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message.photo:

        await update.message.reply_text(
            "📷 Пожалуйста, отправь именно фотографию чека."
        )

        return PURCHASE_PHOTO

    photo = update.message.photo[-1]

    context.user_data["purchase_photo"] = photo.file_id

    await update.message.reply_text(
        "💰 Теперь напиши сумму покупки в рублях.\n\n"
        "Например: <b>2500</b>",
        parse_mode="HTML"
    )

    return PURCHASE_AMOUNT


# ============================================================
# AMOUNT
# ============================================================

async def purchase_amount(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = update.message.text.strip().replace(",", ".")

    try:

        amount = float(text)

        if amount <= 0:
            raise ValueError

    except ValueError:

        await update.message.reply_text(
            "❌ Не удалось распознать сумму.\n"
            "Напиши её числом, например: <b>2500</b>",
            parse_mode="HTML"
        )

        return PURCHASE_AMOUNT

    user = update.effective_user

    category = context.user_data.get(
        "purchase_category"
    )

    photo_id = context.user_data.get(
        "purchase_photo"
    )

    async with aiosqlite.connect(DB_PATH) as db:

        cursor = await db.execute(
            """
            INSERT INTO pending_purchases
            (user_id, amount, category, photo_id)
            VALUES (?, ?, ?, ?)
            """,
            (
                user.id,
                amount,
                category,
                photo_id
            )
        )

        purchase_id = cursor.lastrowid

        await db.commit()

    await update.message.reply_text(
        "🌙 Покупка отправлена на проверку.\n\n"
        "Мы сообщим тебе результат после подтверждения.",
        reply_markup=MAIN_MENU
    )

    admin_keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Подтвердить",
                    callback_data=f"approve_purchase_{purchase_id}"
                ),
                InlineKeyboardButton(
                    "❌ Отклонить",
                    callback_data=f"reject_purchase_{purchase_id}"
                ),
            ]
        ]
    )

    username = (
        f"@{user.username}"
        if user.username
        else user.first_name or "Без имени"
    )

    admin_text = (
        "🛍 <b>Новая покупка</b>\n\n"
        f"👤 Пользователь: {username}\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"💰 Сумма: <b>{amount:.0f} ₽</b>\n"
        f"📦 Категория: {CATEGORIES[category]}"
    )

    for admin_id in ADMIN_IDS:

        try:

            await context.bot.send_photo(
                chat_id=admin_id,
                photo=photo_id,
                caption=admin_text,
                parse_mode="HTML",
                reply_markup=admin_keyboard
            )

        except Exception as e:

            print(
                "Admin notification error:",
                e
            )

    context.user_data.clear()

    return ConversationHandler.END


# ============================================================
# PURCHASE CANCEL
# ============================================================

async def purchase_cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data.clear()

    await update.message.reply_text(
        "🔄 <b>Сброс выполнен.</b>\n\n"
        "Можно начать заново.",
        parse_mode="HTML",
        reply_markup=MAIN_MENU
    )

    return ConversationHandler.END


# ============================================================
# APPROVE / REJECT PURCHASE
# ============================================================

async def purchase_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    data = query.data

    if data.startswith("approve_purchase_"):

        purchase_id = int(
            data.replace(
                "approve_purchase_",
                ""
            )
        )

        async with aiosqlite.connect(DB_PATH) as db:

            cursor = await db.execute(
                """
                SELECT user_id, amount, category, photo_id
                FROM pending_purchases
                WHERE id = ?
                """,
                (purchase_id,)
            )

            purchase = await cursor.fetchone()

            if not purchase:

                await query.edit_message_caption(
                    caption="⚠️ Покупка уже обработана."
                )

                return

            user_id, amount, category, photo_id = purchase

            cursor = await db.execute(
                """
                SELECT points, total_spent
                FROM users
                WHERE user_id = ?
                """,
                (user_id,)
            )

            user = await cursor.fetchone()

            if not user:
                return

            current_points, total_spent = user

            new_total = total_spent + amount

            points = get_points(
                new_total,
                amount,
                category
            )

            await db.execute(
                """
                UPDATE users
                SET points = points + ?,
                    total_spent = ?
                WHERE user_id = ?
                """,
                (
                    points,
                    new_total,
                    user_id
                )
            )

            await db.execute(
                "DELETE FROM pending_purchases WHERE id = ?",
                (purchase_id,)
            )

            await db.commit()

        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "✅ <b>Покупка подтверждена!</b>\n\n"
                f"💰 Сумма: {amount:.0f} ₽\n"
                f"✨ Начислено: <b>{points} баллов</b>\n\n"
                "Баллы уже доступны на твоём балансе."
            ),
            parse_mode="HTML",
            reply_markup=MAIN_MENU
        )

        await query.edit_message_caption(
            caption=(
                "✅ <b>Покупка подтверждена</b>\n\n"
                f"💰 Сумма: {amount:.0f} ₽\n"
                f"✨ Начислено: {points} баллов"
            ),
            parse_mode="HTML"
        )

    elif data.startswith("reject_purchase_"):

        purchase_id = int(
            data.replace(
                "reject_purchase_",
                ""
            )
        )

        async with aiosqlite.connect(DB_PATH) as db:

            cursor = await db.execute(
                """
                SELECT user_id, amount
                FROM pending_purchases
                WHERE id = ?
                """,
                (purchase_id,)
            )

            purchase = await cursor.fetchone()

            if not purchase:

                await query.edit_message_caption(
                    caption="⚠️ Покупка уже обработана."
                )

                return

            user_id, amount = purchase

            await db.execute(
                "DELETE FROM pending_purchases WHERE id = ?",
                (purchase_id,)
            )

            await db.commit()

        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "❌ <b>Покупка отклонена.</b>\n\n"
                "Если ты считаешь, что произошла ошибка, "
                "свяжись с нами:\n"
                "https://vk.me/poputopia"
            ),
            parse_mode="HTML",
            reply_markup=MAIN_MENU
        )

        await query.edit_message_caption(
            caption="❌ <b>Покупка отклонена</b>",
            parse_mode="HTML"
        )


# ============================================================
# REDEEM
# ============================================================

async def redeem(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    u = await get_user(
        user.id,
        user.username,
        user.first_name
    )

    if u["points"] <= 0:

        await update.message.reply_text(
            "🎁 <b>Потратить баллы</b>\n\n"
            "У тебя пока нет доступных баллов.",
            parse_mode="HTML",
            reply_markup=MAIN_MENU
        )

        return

    code = (
        f"@{user.username}"
        if user.username
        else f"id{user.id}"
    )

    code = f"{code}_{u['points']}баллов"

    async with aiosqlite.connect(DB_PATH) as db:

        cursor = await db.execute(
            """
            INSERT INTO pending_redeems
            (user_id, points, code)
            VALUES (?, ?, ?)
            """,
            (
                user.id,
                u["points"],
                code
            )
        )

        redeem_id = cursor.lastrowid

        await db.commit()

    await update.message.reply_text(
        "🎁 <b>Запрос на списание создан.</b>\n\n"
        f"Твои баллы: <b>{u['points']}</b>\n"
        f"Скидка: <b>{u['points']} ₽</b>\n\n"
        "Дождись подтверждения администратора.",
        parse_mode="HTML",
        reply_markup=MAIN_MENU
    )

    admin_keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Подтвердить",
                    callback_data=f"approve_redeem_{redeem_id}"
                ),
                InlineKeyboardButton(
                    "❌ Отклонить",
                    callback_data=f"reject_redeem_{redeem_id}"
                ),
            ]
        ]
    )

    username = (
        f"@{user.username}"
        if user.username
        else user.first_name or "Без имени"
    )

    admin_text = (
        "🎁 <b>Запрос на списание баллов</b>\n\n"
        f"👤 Пользователь: {username}\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"💳 Баллы: <b>{u['points']}</b>\n"
        f"💰 Скидка: <b>{u['points']} ₽</b>\n\n"
        f"Код: <code>{code}</code>"
    )

    for admin_id in ADMIN_IDS:

        try:

            await context.bot.send_message(
                chat_id=admin_id,
                text=admin_text,
                parse_mode="HTML",
                reply_markup=admin_keyboard
            )

        except Exception as e:

            print(
                "Admin redeem notification error:",
                e
            )


# ============================================================
# APPROVE / REJECT REDEEM
# ============================================================

async def redeem_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    data = query.data

    if data.startswith("approve_redeem_"):

        redeem_id = int(
            data.replace(
                "approve_redeem_",
                ""
            )
        )

        async with aiosqlite.connect(DB_PATH) as db:

            cursor = await db.execute(
                """
                SELECT user_id, points, code
                FROM pending_redeems
                WHERE id = ?
                """,
                (redeem_id,)
            )

            redeem_data = await cursor.fetchone()

            if not redeem_data:

                await query.edit_message_text(
                    "⚠️ Запрос уже обработан."
                )

                return

            user_id, points, code = redeem_data

            await db.execute(
                """
                UPDATE users
                SET points = 0
                WHERE user_id = ?
                """,
                (user_id,)
            )

            await db.execute(
                "DELETE FROM pending_redeems WHERE id = ?",
                (redeem_id,)
            )

            await db.commit()

        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "✅ <b>Списание подтверждено!</b>\n\n"
                f"💳 Списано баллов: <b>{points}</b>\n"
                f"💰 Скидка: <b>{points} ₽</b>\n\n"
                "Спасибо, что выбираешь Утопию 🌙"
            ),
            parse_mode="HTML",
            reply_markup=MAIN_MENU
        )

        await query.edit_message_text(
            "✅ <b>Списание подтверждено</b>\n\n"
            f"Списано: {points} баллов",
            parse_mode="HTML"
        )

    elif data.startswith("reject_redeem_"):

        redeem_id = int(
            data.replace(
                "reject_redeem_",
                ""
            )
        )

        async with aiosqlite.connect(DB_PATH) as db:

            cursor = await db.execute(
                """
                SELECT user_id, points
                FROM pending_redeems
                WHERE id = ?
                """,
                (redeem_id,)
            )

            redeem_data = await cursor.fetchone()

            if not redeem_data:

                await query.edit_message_text(
                    "⚠️ Запрос уже обработан."
                )

                return

            user_id, points = redeem_data

            await db.execute(
                "DELETE FROM pending_redeems WHERE id = ?",
                (redeem_id,)
            )

            await db.commit()

        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "❌ <b>Запрос на списание отклонён.</b>\n\n"
                f"Твои {points} баллов остались на балансе."
            ),
            parse_mode="HTML",
            reply_markup=MAIN_MENU
        )

        await query.edit_message_text(
            "❌ <b>Списание отклонено</b>",
            parse_mode="HTML"
        )


# ============================================================
# ADMIN CLIENTS
# ============================================================

async def clients(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id not in ADMIN_IDS:
        return

    async with aiosqlite.connect(DB_PATH) as db:

        cursor = await db.execute(
            """
            SELECT user_id, username, first_name, points, total_spent
            FROM users
            ORDER BY total_spent DESC
            """
        )

        users = await cursor.fetchall()

    if not users:

        await update.message.reply_text(
            "Пока нет клиентов."
        )

        return

    lines = [
        "👥 <b>Клиенты</b>\n"
    ]

    for i, user in enumerate(users, 1):

        user_id, username, first_name, points, total_spent = user

        name = (
            f"@{username}"
            if username
            else first_name or "Без имени"
        )

        lines.append(
            f"{i}. {name}\n"
            f"   💰 {total_spent:.0f} ₽ | "
            f"💳 {points} баллов\n"
            f"   ID: <code>{user_id}</code>"
        )

    await update.message.reply_text(
        "\n\n".join(lines),
        parse_mode="HTML"
    )


# ============================================================
# ADMIN HELP
# ============================================================

async def admin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id not in ADMIN_IDS:
        return

    text = (
        "🌙 <b>UTOPIA — Админ</b>\n\n"
        "/clients — список клиентов\n"
        "/admin — эта справка"
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML"
    )


# ============================================================
# POST INIT
# ============================================================

async def post_init(application: Application):

    await application.bot.set_my_commands(
        [
            ("start", "Начать"),
            ("purchase", "Отправить покупку"),
            ("balance", "Мой баланс"),
            ("redeem", "Потратить баллы"),
            ("cancel", "Сбросить"),
        ]
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN is not set"
        )

    asyncio.run(init_db())

    health_thread = threading.Thread(
        target=run_health_server,
        daemon=True
    )

    health_thread.start()

    purchase_conversation = ConversationHandler(

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

            PURCHASE_CATEGORY: [
                CallbackQueryHandler(
                    purchase_category,
                    pattern=r"^category_"
                )
            ],

            PURCHASE_PHOTO: [
                MessageHandler(
                    filters.PHOTO,
                    purchase_photo
                )
            ],

            PURCHASE_AMOUNT: [
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

        allow_reentry=True,
    )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # ========================================================
    # КОМАНДЫ
    # ========================================================

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

    application.add_handler(
        CommandHandler(
            "cancel",
            purchase_cancel
        )
    )

    application.add_handler(
        CommandHandler(
            "clients",
            clients
        )
    )

    application.add_handler(
        CommandHandler(
            "admin",
            admin
        )
    )

    # ========================================================
    # ПОКУПКА
    # ========================================================

    application.add_handler(
        purchase_conversation
    )

    # ========================================================
    # КНОПКИ ГЛАВНОГО МЕНЮ
    # ========================================================

    application.add_handler(
        MessageHandler(
            filters.Regex(
                r"^💳 Мой баланс$"
            ),
            balance
        )
    )

    application.add_handler(
        MessageHandler(
            filters.Regex(
                r"^🎁 Потратить баллы$"
            ),
            redeem
        )
    )

    application.add_handler(
        MessageHandler(
            filters.Regex(
                r"^ℹ️ Как это работает$"
            ),
            how_it_works
        )
    )

    application.add_handler(
        MessageHandler(
            filters.Regex(
                r"^🔄 Сбросить$"
            ),
            purchase_cancel
        )
    )

    # ========================================================
    # АДМИНСКИЕ КНОПКИ
    # ========================================================

    application.add_handler(
        CallbackQueryHandler(
            purchase_callback,
            pattern=r"^(approve|reject)_purchase_"
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            redeem_callback,
            pattern=r"^(approve|reject)_redeem_"
        )
    )

    print("🌙 UTOPIA bot started")

    application.run_polling()


# ============================================================
# ЗАПУСК
# ============================================================

if __name__ == "__main__":
    main()
