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
    data = body.get("data", {})
    itpay_id = data.get("id", "")
    logger.info(f"ITPAY webhook: {event_type}, id={itpay_id}")

    if event_type not in ("payment.pay", "payment.completed"):
        return web.json_response({"status": 0})

    bot = request.app["bot"]
    db = request.app["db"]
    panel = request.app["panel"]

    # Ищем платёж сначала по itpay_id в БД
    payment = await db.get_pending_payment_by_itpay_id(itpay_id)

    # Fallback: если не нашли по itpay_id — пробуем по client_payment_id из data
    if not payment:
        client_payment_id = data.get("client_payment_id", "")
        if client_payment_id:
            payment = await db.get_pending_payment(client_payment_id)

    # Второй fallback: берём user_id и plan_id из metadata (на случай рассинхрона БД)
    if not payment:
        metadata = data.get("metadata") or {}
        user_id_meta = metadata.get("user_id")
        plan_id_meta = metadata.get("plan_id")
        client_payment_id = data.get("client_payment_id", "")
        if user_id_meta and plan_id_meta and client_payment_id:
            logger.warning(f"ITPAY webhook: платёж {itpay_id} не найден в БД, восстанавливаем из metadata")
            payment = {
                "payment_id": client_payment_id,
                "user_id": int(user_id_meta),
                "plan_id": plan_id_meta,
                "amount": float(data.get("amount", 0)),
                "status": "pending",
                "msg_id": None,
            }
        else:
            logger.error(f"ITPAY webhook: платёж {itpay_id} не найден нигде")
            return web.json_response({"status": 0})

    if payment.get("status") != "pending":
        return web.json_response({"status": 0})

    payment_id = payment["payment_id"]
    user_id = payment["user_id"]
    plan_id = payment["plan_id"]
    amount = payment["amount"]
    plan = get_by_id(plan_id)

    if not plan:
        logger.error(f"ITPAY webhook: план {plan_id} не найден")
        return web.json_response({"status": 0})

    user_data = await db.get_user(user_id)
    ref_by = user_data.get("ref_by") if user_data else None
    ref_rewarded = user_data.get("ref_rewarded") if user_data else None

    bonus_days = 0
    if ref_by and not ref_rewarded:
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

        if ref_by and not ref_rewarded and bonus_days > 0:
            from utils.helpers import notify_user
            await notify_user(user_id, f"🎁 Вам начислено <b>+{bonus_days} дней</b> бесплатно по реферальной программе!")

        msg_id = payment.get("msg_id")
        notify_text = (
            "✅ <b>Платёж подтверждён!</b>\n\n"
            f"📦 Тариф: <b>{plan.get('name', plan_id)}</b>\n"
            f"🔗 URL: <code>{vpn_url}</code>\n\n"
            "Спасибо за покупку! 🎉"
        )
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
        ])
        try:
            if msg_id:
                await bot.edit_message_text(
                    notify_text,
                    chat_id=user_id,
                    message_id=msg_id,
                    parse_mode="HTML",
                    reply_markup=kb,
                )
            else:
                await bot.send_message(user_id, notify_text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.warning(f"Не удалось уведомить {user_id}: {e}")

        await notify_admins(
            f"✅ <b>Авто-оплата ITPAY</b>\n"
            f"👤 <code>{user_id}</code>\n"
            f"📦 {plan.get('name', plan_id)}\n"
            f"💰 {amount} ₽"
        )
    else:
        await db.update_payment_status(payment_id, "rejected")
        try:
            await bot.send_message(
                user_id,
                "❌ Оплата получена, но произошла ошибка активации. Обратитесь в поддержку.",
                parse_mode="HTML",
            )
        except Exception:
            pass
        logger.error(f"ITPAY webhook: не удалось создать VPN для {user_id}, план {plan_id}")

    return web.json_response({"status": 0})


async def start_webhook_server(bot, db, panel) -> None:
    app = web.Application()
    app["bot"] = bot
    app["db"] = db
    app["panel"] = panel
    app.router.add_post("/itpay/webhook", itpay_webhook_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    logger.info("ITPAY webhook: 0.0.0.0:8080/itpay/webhook")
