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
    BotCommand,
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
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip()
]

DB_PATH = "loyalty.sqlite"


# ============================================================
# CATEGORIES
# ============================================================

CATEGORIES = {
    "regular": "🛍 Обычная покупка",
    "merch": "👕 Мерч Утопии",
}


# ============================================================
# MAIN MENU
# ============================================================

MAIN_MENU = ReplyKeyboardMarkup(
    [
        ["🛍 Отправить покупку"],
        ["💳 Мой баланс", "🎁 Потратить баллы"],
        ["ℹ️ Как это работает"],
        ["🔄 Сбросить"],
    ],
    resize_keyboard=True,
)


# ============================================================
# STATES
# ============================================================

CATEGORY, RECEIPT, AMOUNT = range(3)


# ============================================================
# RENDER HEALTH CHECK
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"UTOPIA BOT OK")

    def log_message(self, format, *args):
        return


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
                total_spent INTEGER DEFAULT 0
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                category TEXT,
                amount INTEGER,
                receipt_file_id TEXT,
                status TEXT DEFAULT 'pending'
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS redemptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                points INTEGER,
                status TEXT DEFAULT 'pending'
            )
        """)

        await db.commit()


async def ensure_user(update: Update):
    user = update.effective_user

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR IGNORE INTO users
            (user_id, username, first_name, points, total_spent)
            VALUES (?, ?, ?, 0, 0)
        """, (
            user.id,
            user.username,
            user.first_name,
        ))

        await db.execute("""
            UPDATE users
            SET username = ?, first_name = ?
            WHERE user_id = ?
        """, (
            user.username,
            user.first_name,
            user.id,
        ))

        await db.commit()


async def get_user(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT user_id, username, first_name, points, total_spent
            FROM users
            WHERE user_id = ?
        """, (user_id,))

        return await cursor.fetchone()


# ============================================================
# BONUS LOGIC
# ============================================================

def get_level(total_spent):

    if total_spent <= 10000:
        return "🥉 Уровень 1"

    if total_spent <= 20000:
        return "🥈 Уровень 2"

    return "🥇 Уровень 3"


def get_points(total_spent, amount, category):

    if category == "regular":

        if total_spent <= 10000:
            rate = 0.03
        elif total_spent <= 20000:
            rate = 0.07
        else:
            rate = 0.10

    else:

        if total_spent <= 10000:
            rate = 0.05
        elif total_spent <= 20000:
            rate = 0.10
        else:
            rate = 0.15

    return int(amount * rate)


# ============================================================
# START
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await ensure_user(update)

    text = """
🌙 <b>UTOPIA</b>

Добро пожаловать в клуб Утопии.

Здесь хранятся твои покупки,
бонусные баллы и скидки.

━━━━━━━━━━━━━━━━━━

💳 <b>100 баллов = 100 ₽ скидки</b>

━━━━━━━━━━━━━━━━━━

Выбери действие ниже ↓
"""

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=MAIN_MENU,
    )


# ============================================================
# HOW IT WORKS
# ============================================================

async def how_it_works(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = """
🌙 <b>КАК РАБОТАЕТ ПРОГРАММА</b>

За каждую подтверждённую покупку
ты получаешь бонусные баллы.

━━━━━━━━━━━━━━━━━━

🥉 <b>УРОВЕНЬ 1</b>

До 10 000 ₽

• Обычная покупка — <b>3%</b>
• Мерч Утопии — <b>5%</b>

━━━━━━━━━━━━━━━━━━

🥈 <b>УРОВЕНЬ 2</b>

От 10 001 до 20 000 ₽

• Обычная покупка — <b>7%</b>
• Мерч Утопии — <b>10%</b>

━━━━━━━━━━━━━━━━━━

🥇 <b>УРОВЕНЬ 3</b>

Более 20 000 ₽

• Обычная покупка — <b>10%</b>
• Мерч Утопии — <b>15%</b>

━━━━━━━━━━━━━━━━━━

💳 <b>100 баллов = 100 ₽ скидки.</b>

На данный момент баллами можно оплатить
до 100% стоимости покупки.

В будущем это правило будет изменено.

━━━━━━━━━━━━━━━━━━

Если возникли вопросы по работе бота,
пишите админу <b>@judaskai</b>

<i>Админ отвечает исключительно на вопросы,
связанные с ботом.
Любые другие вопросы игнорируются 😽</i>

━━━━━━━━━━━━━━━━━━

<i>P.S. Подтверждение покупок и, соответственно,
начисление баллов происходит вручную,
а не автоматически.

Поэтому в некоторых случаях это может занять
время, но не более 12 часов.</i>

━━━━━━━━━━━━━━━━━━

Чтобы получить баллы за покупку,
нажми «🛍 Отправить покупку»
и отправь чек.
"""

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=MAIN_MENU,
    )


# ============================================================
# BALANCE
# ============================================================

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await ensure_user(update)

    user = await get_user(update.effective_user.id)

    if not user:
        return

    _, username, first_name, points, total_spent = user

    level = get_level(total_spent)

    if total_spent <= 10000:
        remaining = 10001 - total_spent
        next_level = "до Уровня 2"
    elif total_spent <= 20000:
        remaining = 20001 - total_spent
        next_level = "до Уровня 3"
    else:
        remaining = 0
        next_level = None

    if remaining > 0:
        progress_text = (
            f"До следующего уровня\n"
            f"<b>{remaining:,} ₽</b>".replace(",", " ")
        )
    else:
        progress_text = "✨ <b>Максимальный уровень достигнут</b>"

    text = f"""
🌙 <b>UTOPIA</b>

💳 <b>ТВОЙ БАЛАНС</b>

━━━━━━━━━━━━━━━━━━

✨ <b>{points:,} баллов</b>
   = {points:,} ₽ скидки

🏆 {level}

💰 Покупок на
<b>{total_spent:,} ₽</b>

━━━━━━━━━━━━━━━━━━

{progress_text}

━━━━━━━━━━━━━━━━━━

Выбирай действие ниже ↓
""".replace(",", " ")

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=MAIN_MENU,
    )


# ============================================================
# PURCHASE
# ============================================================

async def purchase_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await ensure_user(update)

    keyboard = [
        [
            InlineKeyboardButton(
                "🛍 Обычная покупка",
                callback_data="category_regular",
            )
        ],
        [
            InlineKeyboardButton(
                "👕 Мерч Утопии",
                callback_data="category_merch",
            )
        ],
    ]

    text = """
🌙 <b>НОВАЯ ПОКУПКА</b>

━━━━━━━━━━━━━━━━━━

Выбери тип покупки:

🛍 <b>Обычная покупка</b>
Покупки в магазине

👕 <b>Мерч Утопии</b>
Фирменный мерч

━━━━━━━━━━━━━━━━━━
"""

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    return CATEGORY


async def category_selected(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()

    category = query.data.replace("category_", "")

    context.user_data["category"] = category

    text = """
🌙 <b>ЧЕК</b>

━━━━━━━━━━━━━━━━━━

Теперь отправь фотографию чека
одним сообщением.

<i>Фото должно быть читаемым,
чтобы сумма и дата были видны.</i>
"""

    await query.edit_message_text(
        text,
        parse_mode="HTML",
    )

    return RECEIPT


async def receipt_received(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message.photo:

        await update.message.reply_text(
            """
⚠️ <b>Нужна фотография чека</b>

Отправь именно фото чека одним сообщением.
""",
            parse_mode="HTML",
        )

        return RECEIPT

    photo = update.message.photo[-1]

    context.user_data["receipt_file_id"] = photo.file_id

    await update.message.reply_text(
        """
🌙 <b>СУММА ПОКУПКИ</b>

━━━━━━━━━━━━━━━━━━

Напиши сумму покупки в рублях.

Например:

<b>2500</b>
""",
        parse_mode="HTML",
    )

    return AMOUNT


async def amount_received(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    try:
        amount = int(
            update.message.text
            .replace(" ", "")
            .replace("₽", "")
        )

        if amount <= 0:
            raise ValueError

    except ValueError:

        await update.message.reply_text(
            """
⚠️ <b>Не удалось определить сумму</b>

Напиши сумму только числом.

Например: <b>2500</b>
""",
            parse_mode="HTML",
        )

        return AMOUNT

    category = context.user_data.get("category")
    receipt_file_id = context.user_data.get("receipt_file_id")

    await ensure_user(update)

    async with aiosqlite.connect(DB_PATH) as db:

        cursor = await db.execute("""
            INSERT INTO purchases
            (user_id, category, amount, receipt_file_id, status)
            VALUES (?, ?, ?, ?, 'pending')
        """, (
            update.effective_user.id,
            category,
            amount,
            receipt_file_id,
        ))

        purchase_id = cursor.lastrowid

        await db.commit()

    # ADMIN NOTIFICATION

    admin_text = f"""
🌙 <b>НОВАЯ ПОКУПКА</b>

━━━━━━━━━━━━━━━━━━

👤 <b>{update.effective_user.first_name}</b>
ID: <code>{update.effective_user.id}</code>

🛒 {CATEGORIES.get(category, category)}

💰 <b>{amount:,} ₽</b>

━━━━━━━━━━━━━━━━━━

Заявка № <b>{purchase_id}</b>
""".replace(",", " ")

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Подтвердить",
                callback_data=f"approve_purchase_{purchase_id}",
            ),
            InlineKeyboardButton(
                "❌ Отклонить",
                callback_data=f"reject_purchase_{purchase_id}",
            ),
        ]
    ])

    for admin_id in ADMIN_IDS:

        try:

            await context.bot.send_photo(
                chat_id=admin_id,
                photo=receipt_file_id,
                caption=admin_text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )

        except Exception:
            pass

    await update.message.reply_text(
        """
🌙 <b>ПОКУПКА ОТПРАВЛЕНА</b>

━━━━━━━━━━━━━━━━━━

Чек передан на проверку.

После подтверждения тебе автоматически
начислятся бонусные баллы.

⏳ Проверка может занять некоторое время,
но не более 12 часов.

━━━━━━━━━━━━━━━━━━

Спасибо, что выбираешь <b>UTOPIA</b>.
""",
        parse_mode="HTML",
        reply_markup=MAIN_MENU,
    )

    context.user_data.clear()

    return ConversationHandler.END


# ============================================================
# CANCEL
# ============================================================

async def cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data.clear()

    await update.message.reply_text(
        """
🌙 <b>ДЕЙСТВИЕ ОТМЕНЕНО</b>

Возвращаемся в главное меню.
""",
        parse_mode="HTML",
        reply_markup=MAIN_MENU,
    )

    return ConversationHandler.END


# ============================================================
# PURCHASE APPROVE / REJECT
# ============================================================

async def approve_purchase(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()

    purchase_id = int(
        query.data.replace("approve_purchase_", "")
    )

    async with aiosqlite.connect(DB_PATH) as db:

        cursor = await db.execute("""
            SELECT user_id, category, amount, status
            FROM purchases
            WHERE id = ?
        """, (purchase_id,))

        purchase = await cursor.fetchone()

        if not purchase:
            await query.edit_message_caption(
                caption="⚠️ Заявка не найдена."
            )
            return

        user_id, category, amount, status = purchase

        if status != "pending":

            await query.answer(
                "Эта заявка уже обработана.",
                show_alert=True,
            )

            return

        cursor = await db.execute("""
            SELECT total_spent, points
            FROM users
            WHERE user_id = ?
        """, (user_id,))

        user = await cursor.fetchone()

        if not user:
            return

        total_spent, current_points = user

        new_total = total_spent + amount

        earned_points = get_points(
            new_total,
            amount,
            category,
        )

        new_points = current_points + earned_points

        await db.execute("""
            UPDATE users
            SET total_spent = ?, points = ?
            WHERE user_id = ?
        """, (
            new_total,
            new_points,
            user_id,
        ))

        await db.execute("""
            UPDATE purchases
            SET status = 'approved'
            WHERE id = ?
        """, (purchase_id,))

        await db.commit()

    # USER NOTIFICATION

    await context.bot.send_message(
        chat_id=user_id,
        text=f"""
🌙 <b>UTOPIA</b>

✅ <b>ПОКУПКА ПОДТВЕРЖДЕНА</b>

━━━━━━━━━━━━━━━━━━

💰 Сумма
<b>{amount:,} ₽</b>

✨ Начислено
<b>{earned_points} баллов</b>

━━━━━━━━━━━━━━━━━━

Теперь на твоём балансе:

💳 <b>{new_points} баллов</b>

🏆 {get_level(new_total)}

━━━━━━━━━━━━━━━━━━

Спасибо, что выбираешь Утопию.
""".replace(",", " "),
        parse_mode="HTML",
        reply_markup=MAIN_MENU,
    )

    await query.edit_message_caption(
        caption=f"""
🌙 <b>ПОКУПКА ПОДТВЕРЖДЕНА</b>

━━━━━━━━━━━━━━━━━━

💰 {amount:,} ₽
✨ +{earned_points} баллов

Заявка № {purchase_id}

━━━━━━━━━━━━━━━━━━
""".replace(",", " "),
        parse_mode="HTML",
    )


async def reject_purchase(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()

    purchase_id = int(
        query.data.replace("reject_purchase_", "")
    )

    async with aiosqlite.connect(DB_PATH) as db:

        cursor = await db.execute("""
            SELECT user_id, amount, status
            FROM purchases
            WHERE id = ?
        """, (purchase_id,))

        purchase = await cursor.fetchone()

        if not purchase:
            return

        user_id, amount, status = purchase

        if status != "pending":

            await query.answer(
                "Эта заявка уже обработана.",
                show_alert=True,
            )

            return

        await db.execute("""
            UPDATE purchases
            SET status = 'rejected'
            WHERE id = ?
        """, (purchase_id,))

        await db.commit()

    await context.bot.send_message(
        chat_id=user_id,
        text="""
🌙 <b>ПОКУПКА НЕ ПОДТВЕРЖДЕНА</b>

━━━━━━━━━━━━━━━━━━

К сожалению, этот чек не удалось
подтвердить.

Если ты считаешь, что произошла ошибка,
напиши админу:

<b>@judaskai</b>

━━━━━━━━━━━━━━━━━━

Также можно обратиться через VK:

https://vk.me/poputopia
""",
        parse_mode="HTML",
        reply_markup=MAIN_MENU,
    )

    await query.edit_message_caption(
        caption=f"""
🌙 <b>ПОКУПКА ОТКЛОНЕНА</b>

━━━━━━━━━━━━━━━━━━

Сумма: {amount:,} ₽
Заявка № {purchase_id}

━━━━━━━━━━━━━━━━━━
""".replace(",", " "),
        parse_mode="HTML",
    )


# ============================================================
# REDEEM
# ============================================================

async def redeem_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await ensure_user(update)

    user = await get_user(update.effective_user.id)

    if not user:
        return

    _, _, _, points, _ = user

    if points <= 0:

        await update.message.reply_text(
            """
🌙 <b>ТВОЙ БАЛАНС</b>

━━━━━━━━━━━━━━━━━━

✨ Сейчас у тебя <b>0 баллов</b>.

Сначала накопи бонусы,
совершая покупки в Утопии.

━━━━━━━━━━━━━━━━━━
""",
            parse_mode="HTML",
            reply_markup=MAIN_MENU,
        )

        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🎁 Потратить все баллы",
                callback_data="redeem_confirm",
            )
        ],
        [
            InlineKeyboardButton(
                "✕ Отмена",
                callback_data="redeem_cancel",
            )
        ],
    ])

    await update.message.reply_text(
        f"""
🌙 <b>ПОТРАТИТЬ БАЛЛЫ</b>

━━━━━━━━━━━━━━━━━━

💳 Доступно:

<b>{points} баллов</b>
= <b>{points} ₽ скидки</b>

━━━━━━━━━━━━━━━━━━

Сейчас можно использовать
до 100% стоимости покупки.

Хочешь потратить все баллы?

""",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def redeem_confirm(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    user = await get_user(user_id)

    if not user:
        return

    points = user[3]

    if points <= 0:

        await query.edit_message_text(
            "⚠️ На балансе нет доступных баллов."
        )

        return

    async with aiosqlite.connect(DB_PATH) as db:

        cursor = await db.execute("""
            INSERT INTO redemptions
            (user_id, points, status)
            VALUES (?, ?, 'pending')
        """, (
            user_id,
            points,
        ))

        redemption_id = cursor.lastrowid

        await db.commit()

    admin_text = f"""
🌙 <b>НОВОЕ СПИСАНИЕ</b>

━━━━━━━━━━━━━━━━━━

👤 <b>{query.from_user.first_name}</b>
ID: <code>{user_id}</code>

🎁 Баллы:
<b>{points}</b>

💰 Скидка:
<b>{points} ₽</b>

━━━━━━━━━━━━━━━━━━

Заявка № <b>{redemption_id}</b>
"""

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Подтвердить",
                callback_data=f"approve_redeem_{redemption_id}",
            ),
            InlineKeyboardButton(
                "❌ Отклонить",
                callback_data=f"reject_redeem_{redemption_id}",
            ),
        ]
    ])

    for admin_id in ADMIN_IDS:

        try:

            await context.bot.send_message(
                chat_id=admin_id,
                text=admin_text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )

        except Exception:
            pass

    await query.edit_message_text(
        f"""
🌙 <b>ЗАПРОС ОТПРАВЛЕН</b>

━━━━━━━━━━━━━━━━━━

🎁 Запрошено:

<b>{points} баллов</b>
= <b>{points} ₽ скидки</b>

━━━━━━━━━━━━━━━━━━

Администратор должен подтвердить
списание вручную.

После подтверждения баллы
будут списаны с баланса.

⏳ Проверка может занять время,
но не более 12 часов.
""",
        parse_mode="HTML",
    )


async def redeem_cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        """
🌙 <b>СПИСАНИЕ ОТМЕНЕНО</b>

Баллы остались на твоём балансе.
""",
        parse_mode="HTML",
    )


async def approve_redeem(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()

    redemption_id = int(
        query.data.replace("approve_redeem_", "")
    )

    async with aiosqlite.connect(DB_PATH) as db:

        cursor = await db.execute("""
            SELECT user_id, points, status
            FROM redemptions
            WHERE id = ?
        """, (redemption_id,))

        redemption = await cursor.fetchone()

        if not redemption:
            return

        user_id, points, status = redemption

        if status != "pending":
            return

        cursor = await db.execute("""
            SELECT points
            FROM users
            WHERE user_id = ?
        """, (user_id,))

        user = await cursor.fetchone()

        if not user:
            return

        current_points = user[0]

        if current_points < points:

            await query.edit_message_text(
                "⚠️ У пользователя недостаточно баллов."
            )

            return

        await db.execute("""
            UPDATE users
            SET points = points - ?
            WHERE user_id = ?
        """, (
            points,
            user_id,
        ))

        await db.execute("""
            UPDATE redemptions
            SET status = 'approved'
            WHERE id = ?
        """, (redemption_id,))

        await db.commit()

    await context.bot.send_message(
        chat_id=user_id,
        text=f"""
🌙 <b>СПИСАНИЕ ПОДТВЕРЖДЕНО</b>

━━━━━━━━━━━━━━━━━━

🎁 Списано:

<b>{points} баллов</b>
= <b>{points} ₽ скидки</b>

━━━━━━━━━━━━━━━━━━

Баллы можно использовать
при следующей покупке.

Спасибо, что выбираешь Утопию.
""",
        parse_mode="HTML",
        reply_markup=MAIN_MENU,
    )

    await query.edit_message_text(
        f"""
🌙 <b>СПИСАНИЕ ПОДТВЕРЖДЕНО</b>

━━━━━━━━━━━━━━━━━━

🎁 {points} баллов
💰 {points} ₽

Заявка № {redemption_id}

━━━━━━━━━━━━━━━━━━
"""
    )


async def reject_redeem(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()

    redemption_id = int(
        query.data.replace("reject_redeem_", "")
    )

    async with aiosqlite.connect(DB_PATH) as db:

        cursor = await db.execute("""
            SELECT user_id, points, status
            FROM redemptions
            WHERE id = ?
        """, (redemption_id,))

        redemption = await cursor.fetchone()

        if not redemption:
            return

        user_id, points, status = redemption

        if status != "pending":
            return

        await db.execute("""
            UPDATE redemptions
            SET status = 'rejected'
            WHERE id = ?
        """, (redemption_id,))

        await db.commit()

    await context.bot.send_message(
        chat_id=user_id,
        text=f"""
🌙 <b>СПИСАНИЕ НЕ ПОДТВЕРЖДЕНО</b>

━━━━━━━━━━━━━━━━━━

Запрос на списание
<b>{points} баллов</b> был отклонён.

Баллы остаются на твоём балансе.

Если ты считаешь, что произошла ошибка,
напиши админу:

<b>@judaskai</b>
""",
        parse_mode="HTML",
        reply_markup=MAIN_MENU,
    )

    await query.edit_message_text(
        f"""
🌙 <b>СПИСАНИЕ ОТКЛОНЕНО</b>

━━━━━━━━━━━━━━━━━━

{points} баллов возвращены пользователю.

Заявка № {redemption_id}

━━━━━━━━━━━━━━━━━━
"""
    )


# ============================================================
# ADMIN — CLIENTS
# ============================================================

async def clients(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id not in ADMIN_IDS:
        return

    async with aiosqlite.connect(DB_PATH) as db:

        cursor = await db.execute("""
            SELECT first_name, username, points, total_spent
            FROM users
            ORDER BY total_spent DESC
        """)

        users = await cursor.fetchall()

    if not users:

        await update.message.reply_text(
            "Пока нет клиентов."
        )

        return

    lines = [
        "🌙 <b>КЛИЕНТЫ UTOPIA</b>",
        "",
        "━━━━━━━━━━━━━━━━━━",
        "",
    ]

    for index, user in enumerate(users, 1):

        first_name, username, points, total_spent = user

        name = first_name or "Без имени"

        if username:
            name += f" @{username}"

        lines.append(
            f"<b>{index}.</b> {name}\n"
            f"💰 {total_spent:,} ₽ · ✨ {points} баллов"
            .replace(",", " ")
        )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
    )


# ============================================================
# ADMIN HELP
# ============================================================

async def admin_help(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id not in ADMIN_IDS:
        return

    await update.message.reply_text(
        """
🌙 <b>UTOPIA ADMIN</b>

━━━━━━━━━━━━━━━━━━

<b>Команды:</b>

/clients
Список клиентов

/admin
Эта справка

━━━━━━━━━━━━━━━━━━

Все покупки и списания
приходят автоматически.

Администратор подтверждает
их кнопками под заявкой.
""",
        parse_mode="HTML",
    )


# ============================================================
# POST INIT
# ============================================================

async def post_init(application: Application):

    await application.bot.set_my_commands([
        BotCommand("start", "Начать"),
        BotCommand("purchase", "Отправить покупку"),
        BotCommand("balance", "Мой баланс"),
        BotCommand("redeem", "Потратить баллы"),
        BotCommand("cancel", "Сбросить"),
    ])


# ============================================================
# MAIN
# ============================================================

def main():

    threading.Thread(
        target=run_health_server,
        daemon=True,
    ).start()

    asyncio.run(init_db())

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # --------------------------------------------------------
    # BASIC COMMANDS
    # --------------------------------------------------------

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("balance", balance)
    )

    application.add_handler(
        CommandHandler("purchase", purchase_start)
    )

    application.add_handler(
        CommandHandler("redeem", redeem_start)
    )

    application.add_handler(
        CommandHandler("cancel", cancel)
    )

    application.add_handler(
        CommandHandler("clients", clients)
    )

    application.add_handler(
        CommandHandler("admin", admin_help)
    )

    # --------------------------------------------------------
    # PURCHASE CONVERSATION
    # --------------------------------------------------------

    purchase_conversation = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex("^🛍 Отправить покупку$"),
                purchase_start,
            ),
            CommandHandler("purchase", purchase_start),
        ],

        states={

            CATEGORY: [
                CallbackQueryHandler(
                    category_selected,
                    pattern="^category_",
                )
            ],

            RECEIPT: [
                MessageHandler(
                    filters.PHOTO,
                    receipt_received,
                )
            ],

            AMOUNT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    amount_received,
                )
            ],
        },

        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(
                filters.Regex("^🔄 Сбросить$"),
                cancel,
            ),
        ],

        allow_reentry=True,
    )

    application.add_handler(
        purchase_conversation
    )

    # --------------------------------------------------------
    # PURCHASE ADMIN CALLBACKS
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            approve_purchase,
            pattern="^approve_purchase_",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            reject_purchase,
            pattern="^reject_purchase_",
        )
    )

    # --------------------------------------------------------
    # REDEEM
    # --------------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.Regex("^🎁 Потратить баллы$"),
            redeem_start,
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            redeem_confirm,
            pattern="^redeem_confirm$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            redeem_cancel,
            pattern="^redeem_cancel$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            approve_redeem,
            pattern="^approve_redeem_",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            reject_redeem,
            pattern="^reject_redeem_",
        )
    )

    # --------------------------------------------------------
    # OTHER BUTTONS
    # --------------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.Regex("^💳 Мой баланс$"),
            balance,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.Regex("^ℹ️ Как это работает$"),
            how_it_works,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.Regex("^🔄 Сбросить$"),
            cancel,
        )
    )

    # --------------------------------------------------------
    # START BOT
    # --------------------------------------------------------

    print("UTOPIA loyalty bot started")

    application.run_polling()


if __name__ == "__main__":
    main()
