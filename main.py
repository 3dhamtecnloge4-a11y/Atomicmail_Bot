import firebase_admin
from firebase_admin import credentials, firestore
import requests
import time
import asyncio
import random
import string
import os
import json
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

# ================= Firebase =================
firebase_json = os.getenv("HotmailDB")

if not firebase_json:
    raise ValueError("HotmailDB environment variable is not set")

cred_dict = json.loads(firebase_json)

cred = credentials.Certificate(cred_dict)
firebase_admin.initialize_app(cred)

db = firestore.client()

# ================= Config =================
TOKEN = os.getenv("TOKEN")
ADMIN_ID = 7879933809

SERVERS = ["https://lanie-underage-aprioristically.ngrok-free.dev"]
server_index = 0

# ================= Cache =================
user_cache = {}

# ================= Helpers =================
def is_user_activated(user_id):
    if user_id == ADMIN_ID:
        return True
    doc = db.collection("activated_users").document(str(user_id)).get()
    return doc.exists


def generate_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))


# ================= Email =================
def save_user(user_id, username):
    db.collection("Users").document(str(user_id)).set({
        "user_id": user_id,
        "username": username,
        "last_seen": firestore.SERVER_TIMESTAMP
    }, merge=True)


def get_new_email():
    docs = db.collection("Atomicmail").where("used", "==", False).limit(1).stream()

    for doc in docs:
        data = doc.to_dict()
        doc_id = doc.id

        db.collection("Atomicmail").document(doc_id).update({"used": True})

        return data["email"], data["password"]

    return None, None


# ================= SERVER TASK =================
async def send_task_safe(atomicmail, atomicpass, application):
    global server_index

    try:
        server_url = SERVERS[server_index]
        server_index = (server_index + 1) % len(SERVERS)

        data = {"htemail": atomicmail, "password": atomicpass}

        # POST
        try:
            response = requests.post(server_url + "/receive", json=data, timeout=10)
        except Exception:
            await application.bot.send_message(
                chat_id=ADMIN_ID,
                text="🚨 السيرفر غير متاح (POST failed)"
            )
            return "SERVER_DOWN"

        if response.status_code != 200:
            await application.bot.send_message(
                chat_id=ADMIN_ID,
                text="🚨 السيرفر غير متاح (bad status)"
            )
            return "SERVER_DOWN"

        try:
            task_info = response.json()
        except Exception:
            await application.bot.send_message(
                chat_id=ADMIN_ID,
                text="🚨 السيرفر غير متاح (invalid JSON)"
            )
            return "SERVER_DOWN"

        if "task_id" not in task_info:
            await application.bot.send_message(
                chat_id=ADMIN_ID,
                text="🚨 السيرفر رد بدون task_id"
            )
            return "SERVER_DOWN"

        task_id = task_info["task_id"]

        # polling
        while True:
            try:
                r = requests.get(server_url + f"/check/{task_id}", timeout=10)
            except Exception:
                await application.bot.send_message(
                    chat_id=ADMIN_ID,
                    text="🚨 السيرفر غير متاح أثناء المتابعة"
                )
                return "SERVER_DOWN"

            if r.status_code != 200:
                await application.bot.send_message(
                    chat_id=ADMIN_ID,
                    text="🚨 السيرفر check فشل"
                )
                return "SERVER_DOWN"

            try:
                res = r.json()
            except Exception:
                await application.bot.send_message(
                    chat_id=ADMIN_ID,
                    text="🚨 السيرفر check invalid JSON"
                )
                return "SERVER_DOWN"

            if res.get("success"):
                code = res.get("code")

                if code is None:
                    return "NO_CODE"

                return code

            time.sleep(1)

    except Exception:
        await application.bot.send_message(
            chat_id=ADMIN_ID,
            text="🚨 خطأ غير متوقع في السيرفر"
        )
        return "SERVER_DOWN"


# ================= UI =================
def main_menu():
    return ReplyKeyboardMarkup([
        ["📩 الحصول على ايميل"],
        ["🔑 الحصول على الكود"],
        ["📊 لوحة الادمن"]
    ], resize_keyboard=True)


def admin_welcome():
    return "👑 مرحباً أدمن\nاختر من القائمة:"


def user_welcome():
    return "👋 مرحباً بك\nاختر من القائمة:"


# ================= Worker =================
async def process_code_request(chat_id, application, atomicmail, atomicpass):

    result = await send_task_safe(atomicmail, atomicpass, application)

    if result == "SERVER_DOWN":
        await application.bot.send_message(
            chat_id=chat_id,
            text="❌ السيرفر غير متاح حالياً، حاول لاحقاً"
        )
        return

    if result == "NO_CODE":
        await application.bot.send_message(
            chat_id=chat_id,
            text="⚠️ لم يتم العثور على الكود، حاول مرة أخرى"
        )
        return

    await application.bot.send_message(
        chat_id=chat_id,
        text=f"✅ الكود الخاص بك هو:\n\n`{result}`",
        parse_mode="Markdown"
    )


# ================= Start =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user.id, user.username)

    if user.id == ADMIN_ID:
        await update.message.reply_text(admin_welcome(), reply_markup=main_menu())
        return

    if not is_user_activated(user.id):
        await update.message.reply_text("🔐 ادخل كود التفعيل:")
        return

    await update.message.reply_text(user_welcome(), reply_markup=main_menu())


# ================= Handle =================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id

    save_user(user_id, update.effective_user.username)

    # ================= Activation =================
    if not is_user_activated(user_id):

        if user_id == ADMIN_ID:
            return

        codes_ref = db.collection("activation_codes")\
            .where("code", "==", text)\
            .where("used", "==", False)\
            .stream()

        for doc in codes_ref:
            doc_id = doc.id

            db.collection("activation_codes").document(doc_id).update({
                "used": True,
                "used_by": user_id
            })

            db.collection("activated_users").document(str(user_id)).set({
                "user_id": user_id,
                "activated_at": firestore.SERVER_TIMESTAMP
            })

            await update.message.reply_text("✅ تم التفعيل بنجاح")
            return

        await update.message.reply_text("❌ كود غير صحيح")
        return

    # ================= Email =================
    if text == "📩 الحصول على ايميل":

        if user_id in user_cache:
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ نعم", callback_data="confirm_new_email"),
                    InlineKeyboardButton("❌ لا", callback_data="keep_old_email")
                ]
            ])

            await update.message.reply_text(
                "⚠️ لديك ايميل بالفعل\nهل تريد الحصول على ايميل جديد؟",
                reply_markup=keyboard
            )
            return

        email, password = get_new_email()

        if not email:
            await context.application.bot.send_message(
                chat_id=ADMIN_ID,
                text="⚠️ المخزون فاضي - لا يوجد ايميلات"
            )

            await update.message.reply_text("❌ لا يوجد ايميلات حالياً")
            return

        user_cache[user_id] = {"email": email, "password": password}

        await update.message.reply_text(
            f"📧 ايميلك:\n\n`{email}`",
            parse_mode="Markdown",
            reply_markup=main_menu()
        )

    # ================= Code =================
    elif text == "🔑 الحصول على الكود":

        if user_id not in user_cache:
            await update.message.reply_text("❌ لازم تجيب ايميل الأول")
            return

        await update.message.reply_text("⏳ جاري استخراج الكود...")

        asyncio.create_task(
            process_code_request(
                update.effective_chat.id,
                context.application,
                user_cache[user_id]["email"],
                user_cache[user_id]["password"]
            )
        )

    # ================= Admin =================
    elif text == "📊 لوحة الادمن":

        if user_id != ADMIN_ID:
            await update.message.reply_text("❌ غير مصرح")
            return

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("👥 Users", callback_data="users")],
            [InlineKeyboardButton("📧 Emails", callback_data="emails")],
            [InlineKeyboardButton("🔐 Activation", callback_data="activation")],
            [InlineKeyboardButton("🔑 Codes", callback_data="codes")],
        ])

        await update.message.reply_text(admin_welcome(), reply_markup=keyboard)


# ================= Callback =================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    if query.data == "confirm_new_email":

        email, password = get_new_email()

        if not email:
            await query.edit_message_text("❌ لا يوجد ايميلات")
            return

        user_cache[user_id] = {"email": email, "password": password}

        await query.edit_message_text(f"📧 تم إعطاؤك ايميل جديد:\n\n`{email}`", parse_mode="Markdown")
        return

    elif query.data == "keep_old_email":
        email = user_cache[user_id]["email"]
        await query.edit_message_text(f"📧 تم الاحتفاظ بالايميل الحالي:\n\n`{email}`", parse_mode="Markdown")
        return

    if user_id != ADMIN_ID:
        await query.edit_message_text("❌ غير مصرح")
        return

    if query.data == "users":
        users_count = len(list(db.collection("Users").stream()))
        activated_count = len(list(db.collection("activated_users").stream()))
        await query.edit_message_text(f"👥 Users: {users_count}\nActivated: {activated_count}")

    elif query.data == "emails":
        total = len(list(db.collection("Atomicmail").stream()))
        used = len(list(db.collection("Atomicmail").where("used", "==", True).stream()))
        remaining = total - used

        await query.edit_message_text(f"📧 Total: {total}\nUsed: {used}\nRemaining: {remaining}")

    elif query.data == "activation":
        total_codes = len(list(db.collection("activation_codes").stream()))
        used_codes = len(list(db.collection("activation_codes").where("used", "==", True).stream()))

        await query.edit_message_text(f"🔐 Total Codes: {total_codes}\nUsed: {used_codes}")

    elif query.data == "codes":
        code = generate_code()

        db.collection("activation_codes").add({
            "code": code,
            "used": False,
            "created_at": firestore.SERVER_TIMESTAMP,
            "used_by": None
        })

        await query.edit_message_text(f"✅ Code:\n\n`{code}`", parse_mode="Markdown")


# ================= Run =================
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.add_handler(CallbackQueryHandler(button_handler))

app.run_polling()
