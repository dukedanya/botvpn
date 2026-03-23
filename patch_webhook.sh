#!/bin/bash
set -e
cd ~/kakoi

cat > services/webhook.py << 'PEOF_SERVICES_WEBHOOK_PY'
import json
import logging

from aiohttp import web

from config import Config
from services.itpay import ItpayAPI
from services.subscriptions import create_subscription, reward_referrer_days, reward_referrer_percent
from tariffs import get_by_id
from utils.helpers import notify_admins

logger = logging.getLogger(__name__)


async def itpay_webhook_handler(request: web.Request) -> web.Response:
    raw_body = await request.read()
    signature = request.headers.get("itpay-signature", "")

    if Config.ITPAY_WEBHOOK_SECRET and signature:
        if not ItpayAPI.verify_webhook_signature(Config.ITPAY_WEBHOOK_SECRET, raw_body, signature):
            logger.warning("ITPAY webhook: неверная подпись")
            return web.Response(status=403, text="invalid signature")

    try:
        body = json.loads(raw_body)
    except Exception:
        return web.Response(status=400, text="bad json")

    event_type = body.get("type", "")
    data       = body.get("data", {})
    itpay_id   = data.get("id", "")
    logger.info(f"ITPAY webhook: {event_type}, id={itpay_id}")

    if event_type not in ("payment.pay", "payment.completed"):
        return web.json_response({"status": 0})

    bot = request.app["bot"]
    db  = request.app["db"]
    panel = request.app["panel"]

    payment = await db.get_pending_payment_by_itpay_id(itpay_id)
    if not payment or payment.get("status") != "pending":
        return web.json_response({"status": 0})

    payment_id = payment["payment_id"]
    user_id    = payment["user_id"]
    plan_id    = payment["plan_id"]
    amount     = payment["amount"]
    plan       = get_by_id(plan_id)

    if not plan:
        return web.json_response({"status": 0})

    user_data    = await db.get_user(user_id)
    ref_by       = user_data.get("ref_by") if user_data else None
    ref_rewarded = user_data.get("ref_rewarded") if user_data else None

    bonus_days = 0
    if ref_by and not ref_rewarded:
        referrer = await db.get_user(ref_by)
        if referrer and referrer.get("ref_system_type") == 1:
            bonus_days = Config.REF_BONUS_DAYS

    vpn_url = await create_subscription(user_id, plan, db=db, panel=panel, extra_days=bonus_days)

    if vpn_url:
        await db.set_has_subscription(user_id)
        if ref_by and not ref_rewarded:
            referrer = await db.get_user(ref_by)
            if referrer:
                if referrer.get("ref_system_type") == 1:
                    await reward_referrer_days(ref_by, Config.REF_BONUS_DAYS, db=db, panel=panel)
                else:
                    await reward_referrer_percent(user_id, amount, db=db)
            await db.mark_ref_rewarded(user_id)
        await db.update_payment_status(payment_id, "accepted")

        msg_id = payment.get("msg_id")
        try:
            if msg_id:
                await bot.edit_message_text(
                    "✅ <b>Платёж подтверждён!</b>\n\nСпасибо за покупку! 🎉\nПодписка активирована.",
                    chat_id=user_id, message_id=msg_id, parse_mode="HTML")
            else:
                await bot.send_message(user_id, "✅ <b>Платёж подтверждён!</b>\n\nПодписка активирована.", parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Не удалось уведомить {user_id}: {e}")

        await notify_admins(f"✅ <b>Авто-подтверждение ITPAY</b>\n\n"
            f"👤 <code>{user_id}</code>\n📦 {plan.get('name', plan_id)}\n💰 {amount} ₽")
    else:
        await db.update_payment_status(payment_id, "rejected")
        try:
            await bot.send_message(user_id, "❌ Ошибка активации. Обратитесь в поддержку.", parse_mode="HTML")
        except Exception:
            pass

    return web.json_response({"status": 0})


async def start_webhook_server(bot, db, panel) -> None:
    app = web.Application()
    app["bot"] = bot
    app["db"]  = db
    app["panel"] = panel
    app.router.add_post("/itpay/webhook", itpay_webhook_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    logger.info("ITPAY webhook: 0.0.0.0:8080/itpay/webhook")

PEOF_SERVICES_WEBHOOK_PY
echo "✓ services/webhook.py"

cat > main.py << 'PEOF_MAIN_PY'
import asyncio
import logging
import os
from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.callback_answer import CallbackAnswerMiddleware

from config import Config
from db import Database
from services.panel import PanelAPI
from services.itpay import ItpayAPI
from services.webhook import start_webhook_server
from tariffs.loader import load_tariffs
from middlewares.ban import ban_middleware
from handlers import start, profile, buy, payment_admin, referral, admin
from utils.helpers import set_bot, set_db, set_panel, set_itpay

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR  = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            os.path.join(LOG_DIR, f"{datetime.now().strftime('%Y-%m-%d')}.log"),
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger(__name__)

db    = Database(Config.DATA_FILE)
panel = PanelAPI()
itpay = ItpayAPI()


async def check_expired_subscriptions() -> None:
    from services.subscriptions import is_active_subscription
    while True:
        try:
            await asyncio.sleep(60)
        except Exception as e:
            logger.error(f"check_expired_subscriptions: {e}")
            await asyncio.sleep(60)


async def cleanup_old_payments() -> None:
    while True:
        try:
            deleted = await db.cleanup_old_pending_payments(days=30)
            if deleted:
                logger.info(f"Удалено старых платежей: {deleted}")
            await asyncio.sleep(259200)
        except Exception as e:
            logger.error(f"cleanup_old_payments: {e}")
            await asyncio.sleep(3600)


async def main() -> None:
    load_tariffs()
    await db.connect()

    bot = Bot(
        token=Config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    dp.message.middleware(ban_middleware)
    dp.callback_query.middleware(ban_middleware)
    dp.callback_query.middleware(CallbackAnswerMiddleware())

    dp.include_router(start.router)
    dp.include_router(profile.router)
    dp.include_router(buy.router)
    dp.include_router(payment_admin.router)
    dp.include_router(referral.router)
    dp.include_router(admin.router)

    # Глобальные зависимости для хендлеров
    dp["db"]    = db
    dp["panel"] = panel
    dp["itpay"] = itpay
    dp["bot"]   = bot

    asyncio.create_task(check_expired_subscriptions())
    asyncio.create_task(cleanup_old_payments())
    await start_webhook_server(bot, db, panel)

    me = await bot.get_me()
    set_bot(bot, me.username)
    set_db(db)
    set_panel(panel)
    set_itpay(itpay)
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Бот запущен")

    try:
        await dp.start_polling(bot)
    finally:
        await db.close()
        await panel.close()
        await itpay.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

PEOF_MAIN_PY
echo "✓ main.py"

python main.py