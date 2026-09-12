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

    asyncio.run(init_db())

    application = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    conversation_handler = ConversationHandler(

        entry_points=[
            CommandHandler(
                "purchase",
                purchase_start
            )
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
            )
        ],
    )

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

    application.add_handler(
        conversation_handler
    )

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
