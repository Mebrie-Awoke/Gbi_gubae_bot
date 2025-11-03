import json
import os
import asyncio
import logging
from ethiopian_date import EthiopianDateConverter
from datetime import datetime, timedelta
from functools import partial

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

from dotenv import load_dotenv
import pytz

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
TIMEZONE = os.getenv("TIMEZONE", "Africa/Addis_Ababa")
TZ = pytz.timezone(TIMEZONE)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if not BOT_TOKEN:
    logger.error("BOT_TOKEN not set in environment")
    raise SystemExit(1)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler(timezone=TZ)
users = {}       
reminders = []   
next_reminder_id = 1
MAIN_ADMIN_ID = 7575562460  
USERS_FILE = "users.json"

def convert_to_ethiopian(dt):
    """Convert datetime to Ethiopian date string"""
    try:
        ethiopian_date = EthiopianDateConverter.to_ethiopian(dt.year, dt.month, dt.day)
        eth_year, eth_month, eth_day = ethiopian_date
        return f"{eth_year}-{eth_month:02d}-{eth_day:02d} {dt.hour:02d}:{dt.minute:02d}"
    except Exception as e:
        logger.error(f"Failed to convert date to Ethiopian: {e}")
        return dt.strftime("%Y-%m-%d %H:%M")

def parse_datetime_local(s: str) -> datetime:
    dt = datetime.strptime(s, "%Y-%m-%d %H:%M")
    # ensure tz-aware
    if dt.tzinfo is None:
        return TZ.localize(dt)
    return dt

def load_users():
    global users
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                users = {int(k): v for k, v in data.items()}
        except Exception as e:
            print(f" Failed to load users.json: {e}")
            users = {}
    else:
        users = {}

def save_users():
    try:
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, default=str, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f" Failed to save users.json: {e}")

class RegisterStates(StatesGroup):
    waiting_batch = State()

class ReminderStates(StatesGroup):
    waiting_data = State()

class BroadcastStates(StatesGroup):
    waiting_batch = State()
    waiting_message = State()

def is_admin(chat_id: int) -> bool:
    return chat_id == MAIN_ADMIN_ID or users.get(chat_id, {}).get("is_admin", False)

def admin_main_keyboard() -> types.InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Add Reminder", callback_data="admin_add_reminder")
    kb.button(text="🕒 List Reminders", callback_data="admin_list_reminders")
    kb.button(text="👥 List Users", callback_data="admin_list_users")
    kb.button(text="📤 Message", callback_data="admin_broadcast")
    kb.button(text="👑 Add Admin", callback_data="admin_add_admin")
    kb.button(text="🚫 Remove Admin", callback_data="admin_remove_admin")
    kb.button(text=" Refresh", callback_data="admin_refresh")
    kb.adjust(2)
    return kb.as_markup()

def broadcast_batch_keyboard() -> types.InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🎯 All Users (0)", callback_data="broadcast_0")
    kb.button(text="1️⃣ Batch 1", callback_data="broadcast_1")
    kb.button(text="2️⃣ Batch 2", callback_data="broadcast_2")
    kb.button(text="3️⃣ Batch 3", callback_data="broadcast_3")
    kb.button(text="4️⃣ Batch 4", callback_data="broadcast_4")
    kb.button(text="5️⃣ Batch 5", callback_data="broadcast_5")
    kb.button(text="6️⃣ Batch 6", callback_data="broadcast_6")
    kb.button(text="🔙 Back to Main", callback_data="admin_back")
    kb.adjust(2)
    return kb.as_markup()


def reminder_actions_keyboard(reminder_id: int) -> types.InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=" Mark Done", callback_data=f"done_{reminder_id}")
    kb.button(text=" 10 Min", callback_data=f"snooze10_{reminder_id}")
    kb.button(text=" 1 Hour", callback_data=f"snooze60_{reminder_id}")
    kb.button(text=" View All", callback_data="admin_list_reminders")
    kb.adjust(2)
    return kb.as_markup()

def confirm_cancel_keyboard() -> types.InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=" Confirm", callback_data="confirm_action")
    kb.button(text=" Cancel", callback_data="admin_back")
    kb.adjust(2)
    return kb.as_markup()

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    chat_id = message.chat.id
    if chat_id not in users:
        user_name = message.from_user.first_name or "User"
        await state.update_data(user_name=user_name, username=message.from_user.username)
        await message.answer(
            f" Welcome {user_name} to GBI Gubae \n\n"
            "የእርስዎን ባች ይምረጡ 1, 2, 3, 4, 5 ወይም 6. "
        )
        await state.set_state(RegisterStates.waiting_batch)
    else:
        user_data = users[chat_id]
        name = user_data.get('name', 'User')
        if user_data.get("is_admin"):
            await message.answer(
                f" Welcome back, Admin {name}! (Batch {user_data['batch']})",
                reply_markup=admin_main_keyboard()
            )
        else:
            await message.answer(f" Welcome back {name}! (Batch {user_data['batch']})")

@dp.message(StateFilter(RegisterStates.waiting_batch))
async def register_batch(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if text not in ("1", "2", "3", "4", "5", "6"):
        await message.answer("የእርስዎን ባች ይምረጡ 1, 2, 3, 4, 5, ወይም 6.")
        return

    batch = int(text)
    chat_id = message.chat.id
    is_user_admin = chat_id == MAIN_ADMIN_ID

    state_data = await state.get_data()
    user_name = state_data.get('user_name', message.from_user.first_name or 'User')
    username = state_data.get('username', message.from_user.username)

    users[chat_id] = {
        "batch": batch,
        "is_admin": is_user_admin,
        "name": user_name,
        "username": username,
        "registered_at": datetime.now(TZ)
    }

    response = f"እንኳን ደህና መጡ ወደ 6 ኪሎ ጉባዔ መልእክት ቦት። ማንኛውም የጉባኤ መለእክት እንዲደርስዎ እንደሚያስችልዎ ተስፋ አደርጋለሁ። \n You are registered in *Batch {batch}*."
    if is_user_admin:
        response += "\n You are registered as *Admin*."

    await message.answer(response, parse_mode="Markdown")

    if is_user_admin:
        await message.answer("⚙️ Admin Panel:", reply_markup=admin_main_keyboard())

    await state.clear()

async def send_reminder_job(reminder):
    title, msg, batch = reminder["title"], reminder["msg"], reminder["batch"]
    targets = [
        (cid, u) for cid, u in users.items()
        if (batch == 0 or u["batch"] == batch) and not u.get("is_admin", False)
    ]

    logger.info(f" Sending reminder '{title}' to {len(targets)} users")
    success = 0
    failed = 0
    for cid, udata in targets:
        try:
            uname = udata.get("name", str(cid))
            ethiopian_time = convert_to_ethiopian(reminder['send_at'])
            text = (
                f" *Personal Reminder for {uname}*\n\n"
                f"*{title}*\n\n{msg}\n\n"
                f" Scheduled: {ethiopian_time}\n"
                f" Batch: {udata['batch']}\n"
            )
            await bot.send_message(cid, text, parse_mode="Markdown", reply_markup=reminder_actions_keyboard(reminder["id"]))
            success += 1
            await asyncio.sleep(0.2)  
        except Exception as e:
            logger.exception("Failed to send reminder to %s: %s", cid, e)
            failed += 1
    try:
        ethiopian_now = convert_to_ethiopian(datetime.now(TZ))
        report = (
            f" Reminder Delivery Report\n\n"
            f" {title}\n"
            f" Sent: {success}\n"
            f" Failed: {failed}\n"
            f" Target: {'All batches' if batch == 0 else f'Batch {batch}'}\n"
            f" Sent at: {ethiopian_now}"
        )
        await bot.send_message(MAIN_ADMIN_ID, report, parse_mode="Markdown", reply_markup=admin_main_keyboard())
    except Exception:
        logger.exception("Failed to send delivery report to main admin")

@dp.callback_query(lambda c: c.data == "admin_add_reminder")
async def cb_add_reminder(callback: types.CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    if not is_admin(uid):
        await callback.answer("Unauthorized", show_alert=True)
        return
    await callback.message.answer(
        "Send reminder in this format:\n\n"
        "Title | Batch | YYYY-MM-DD HH:MM | Message\n\n"
        "Example:\n"
        "Weekly Meeting | 2 | 2025-10-13 09:00 | Be on time\n\n"
        "Use *batch 0* to send to *all users*.",
        parse_mode="Markdown"
    )
    await state.set_state(ReminderStates.waiting_data)
    await callback.answer()

@dp.callback_query(lambda c: c.data == "admin_list_users")
async def cb_list_users(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not is_admin(uid):
        await callback.answer("Unauthorized", show_alert=True)
        return
    if not users:
        await callback.message.answer("No users registered yet.")
    else:
        lines = []
        for cid, u in users.items():
            name = u.get("name") or u.get("username") or str(cid)
            eth_registered = convert_to_ethiopian(u.get('registered_at', datetime.now(TZ)))
            lines.append(f"{name} ({cid}) → Batch {u['batch']}, {'Admin' if u.get('is_admin') else 'User'}, Registered: {eth_registered}")
        await callback.message.answer("\n".join(lines))
    await callback.answer()

@dp.callback_query(lambda c: c.data == "admin_list_reminders")
async def cb_list_reminders(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not is_admin(uid):
        await callback.answer("Unauthorized", show_alert=True)
        return
    if not reminders:
        await callback.message.answer("ℹ No reminders scheduled.")
    else:
        lines = []
        for r in reminders:
            eth_time = convert_to_ethiopian(r['send_at'])
            lines.append(f"{r['id']} → {'ALL' if r['batch']==0 else f'Batch {r['batch']}'} | {r['title']} | {eth_time}")
        await callback.message.answer("\n".join(lines))
    await callback.answer()

@dp.callback_query(lambda c: c.data == "admin_broadcast")
async def cb_broadcast(callback: types.CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    if not is_admin(uid):
        await callback.answer("Unauthorized", show_alert=True)
        return
    await callback.message.answer("Select target batch:", reply_markup=broadcast_batch_keyboard())
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("broadcast_"))
async def cb_broadcast_select(callback: types.CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    if not is_admin(uid):
        await callback.answer("Unauthorized", show_alert=True)
        return
    data = callback.data  # broadcast_N
    try:
        batch = int(data.split("_", 1)[1])
    except Exception:
        await callback.answer("Invalid batch")
        return
    await state.update_data(broadcast_batch=batch)
    await state.set_state(BroadcastStates.waiting_message)
    await callback.message.answer(f" Enter the message to send to {'All' if batch==0 else f'Batch {batch}'}:")
    await callback.answer()

@dp.callback_query(lambda c: c.data == "admin_add_admin")
async def cb_add_admin(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not is_admin(uid):
        await callback.answer("Unauthorized", show_alert=True)
        return

    kb = InlineKeyboardBuilder()
    for other_id, u in users.items():
        if not u.get("is_admin"):
            display = u.get("name") or u.get("username") or str(other_id)
            kb.button(text=f"{display} (Batch {u['batch']})", callback_data=f"makeadmin_{other_id}")
    kb.adjust(1)
    markup = kb.as_markup()
    if not kb.buttons:
        await callback.message.answer(" No users available to promote.")
    else:
        await callback.message.answer("Select a user to make admin:", reply_markup=markup)
    await callback.answer()

@dp.callback_query(lambda c: c.data == "admin_remove_admin")
async def cb_remove_admin(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not is_admin(uid):
        await callback.answer("Unauthorized", show_alert=True)
        return

    kb = InlineKeyboardBuilder()
    for other_id, u in users.items():
        if u.get("is_admin") and other_id != MAIN_ADMIN_ID:
            display = u.get("name") or u.get("username") or str(other_id)
            kb.button(text=f"{display} (Batch {u['batch']})", callback_data=f"removeadmin_{other_id}")
    kb.adjust(1)
    markup = kb.as_markup()
    if not kb.buttons:
        await callback.message.answer(" No admins available to remove.")
    else:
        await callback.message.answer("Select an admin to remove:", reply_markup=markup)
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("makeadmin_"))
async def cb_make_admin(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not is_admin(uid):
        await callback.answer("Unauthorized", show_alert=True)
        return
    target = int(callback.data.split("_", 1)[1])
    if target in users:
        users[target]["is_admin"] = True
        display = users[target].get("name") or users[target].get("username") or str(target)
        await callback.message.edit_text(f" {display} is now an admin.", reply_markup=admin_main_keyboard())
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("removeadmin_"))
async def cb_remove_admin_click(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not is_admin(uid):
        await callback.answer("Unauthorized", show_alert=True)
        return
    target = int(callback.data.split("_", 1)[1])
    if target in users and target != MAIN_ADMIN_ID:
        users[target]["is_admin"] = False
        display = users[target].get("name") or users[target].get("username") or str(target)
        await callback.message.edit_text(f" Admin rights removed from {display}.", reply_markup=admin_main_keyboard())
    await callback.answer()

@dp.callback_query(lambda c: c.data == "admin_refresh" or c.data == "admin_back")
async def cb_refresh(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not is_admin(uid):
        await callback.answer("Unauthorized", show_alert=True)
        return
    await callback.message.edit_text("⚙️ Admin Panel:", reply_markup=admin_main_keyboard())
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("done_"))
async def cb_done(callback: types.CallbackQuery):
    rid = int(callback.data.split("_", 1)[1])
    await callback.answer("Marked as done!")
    logger.info("Reminder %s marked done by %s", rid, callback.from_user.id)

@dp.callback_query(lambda c: c.data.startswith("snooze10_") or c.data.startswith("snooze60_"))
async def cb_snooze(callback: types.CallbackQuery):
    data = callback.data
    if data.startswith("snooze10_"):
        rid = int(data.split("_", 1)[1])
        minutes = 10
    else:
        parts = data.split("_", 2)
        if len(parts) == 2:
            rid = int(parts[1])
        else:
            rid = int(parts[-1])
        minutes = 60

    reminder = next((r for r in reminders if r["id"] == rid), None)
    if not reminder:
        await callback.answer("Reminder not found", show_alert=True)
        return

    new_dt = datetime.now(TZ) + timedelta(minutes=minutes)
    reminder["send_at"] = new_dt
    scheduler.add_job(
        func=partial(asyncio.create_task, send_reminder_job(reminder)),
        trigger=DateTrigger(run_date=new_dt),
        id=f"reminder_{rid}_snoozed_{minutes}"
    )
    await callback.answer(f" Snoozed {minutes} minutes!")

@dp.message(StateFilter(BroadcastStates.waiting_message))
async def bc_waiting_message(message: types.Message, state: FSMContext):
    data = await state.get_data()
    batch = data.get("broadcast_batch", 0)
    text = message.text.strip()
    targets = [
        (cid, u) for cid, u in users.items() if batch == 0 or u["batch"] == batch
    ]
    if not targets:
        await message.answer(" No users found for that batch.")
    else:
        success = 0
        failed = 0
        for cid, udata in targets:
            try:
                uname = udata.get("name", str(cid))
                ethiopian_now = convert_to_ethiopian(datetime.now(TZ))
                bmsg = (
                    f"{text}\n\n"
                    f"የተላከበት ሰአት: {ethiopian_now}"
                )
                await bot.send_message(cid, bmsg, parse_mode="Markdown")
                success += 1
                await asyncio.sleep(0.15)
            except Exception:
                logger.exception("Failed broadcast to %s", cid)
                failed += 1

        batch_names = { 0: "All Users", 1: "Batch 1", 2: "Batch 2",3: "Batch 3", 4: "Batch 4",5: "Batch 5", 6: "Batch 6"}
        await message.answer(
            f" message completed!\n Success: {success}\n Failed: {failed}\n Target: {batch_names.get(batch)}",
            reply_markup=admin_main_keyboard()
        )
    await state.clear()

@dp.callback_query(lambda c: c.data.startswith("broadcast_"))
async def cb_broadcast_batch(callback: types.CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    if not is_admin(uid):
        await callback.answer("Unauthorized", show_alert=True)
        return
    try:
        batch = int(callback.data.split("_", 1)[1])
    except Exception:
        await callback.answer("Invalid")
        return
    await state.update_data(broadcast_batch=batch)
    await state.set_state(BroadcastStates.waiting_message)
    await callback.message.answer(f" Now send the message for {'All' if batch==0 else f'Batch {batch}'}:")
    await callback.answer()

@dp.message(StateFilter(ReminderStates.waiting_data))
async def handle_add_reminder_data(message: types.Message, state: FSMContext):
    text = message.text.strip()
    parts = [p.strip() for p in text.split("|", 3)]
    if len(parts) != 4:
        await message.answer(" Invalid format. Use: Title | Batch | YYYY-MM-DD HH:MM | Message")
        return

    title, batch_s, dt_s, msg = parts
    try:
        batch = int(batch_s)
        if batch not in (0, 1, 2, 3, 4, 5, 6):
            raise ValueError("Batch must be 0–6.")

        dt = parse_datetime_local(dt_s)
        if dt <= datetime.now(TZ):
            await message.answer(" Reminder time must be in the future.")
            return
    except Exception as e:
        await message.answer(f" Invalid input: {e}")
        return

    global next_reminder_id
    reminder = {
        "id": next_reminder_id,
        "title": title,
        "msg": msg,
        "batch": batch,
        "send_at": dt,
        "created_at": datetime.now(TZ),
        "created_by": message.chat.id
    }
    reminders.append(reminder)
    next_reminder_id += 1
    scheduler.add_job(
        func=partial(asyncio.create_task, send_reminder_job(reminder)),
        trigger=DateTrigger(run_date=dt),
        id=f"reminder_{reminder['id']}"
    )
    target_users = [
        u for u in users.values() if (batch == 0 or u["batch"] == batch) and not u.get("is_admin", False)
    ]
    
    ethiopian_time = convert_to_ethiopian(dt)
    preview_message = (
        f" *Reminder Scheduled Successfully!*\n\n"
        f" *Preview:*\n{title}\n\n{msg}\n\n"
        f"• ID: {reminder['id']}\n"
        f"• Target: {'All batches' if batch == 0 else f'Batch {batch}'}\n"
        f"• Recipients : {len(target_users)}\n"
        f"• Delivery time: {ethiopian_time}\n"
    )
    await message.answer(preview_message, parse_mode="Markdown", reply_markup=admin_main_keyboard())
    await state.clear()

async def on_startup():
    scheduler.start()
    users[MAIN_ADMIN_ID] = {
        "batch": 0,
        "is_admin": True,
        "name": "Main Admin",
        "username": None,
        "registered_at": datetime.now(TZ)
    }
    logger.info("🚀 Bot started (Admin ID = %s)", MAIN_ADMIN_ID)

async def on_shutdown():
    scheduler.shutdown(wait=False)
    await bot.session.close()
    logger.info(" Bot stopped.")

async def main():
    await on_startup()
    try:
        await dp.start_polling(bot, on_shutdown=on_shutdown)
    finally:
        await on_shutdown()

if __name__ == "__main__":
    asyncio.run(main())
