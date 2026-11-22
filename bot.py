# -*- coding: utf-8 -*-
# pip install "python-telegram-bot>=21.9"
import json, os, re, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import (Update, InlineKeyboardButton as B, InlineKeyboardMarkup as M,
                      InputFile)
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                          MessageHandler, ChatMemberHandler, ContextTypes, filters)

# ================== تنظیمات ==================
TOKEN = os.environ.get("TOKEN", "PUT_BOT_TOKEN")
MAIN_GROUP = int(os.environ.get("MAIN_GROUP", "-1001111111111"))      # گروه اصلی اعضا
ADMIN_GROUP = int(os.environ.get("ADMIN_GROUP", "-1002222222222"))    # گروه فیش‌ها برای ادمین‌ها
BACKUP_GROUP = int(os.environ.get("BACKUP_GROUP", "-1003333333333"))  # گروه بکاپ
ADMINS = {int(x) for x in re.findall(r"\d+", os.environ.get("ADMINS", "111111111"))}
DB_FILE = "data.json"
# ============================================

DB = {"global_debt": 0, "groups": {}, "cards": [], "users": {}, "receipts": {},
      "rr": 0, "seq": 0}
APP = None


def load():
    global DB
    if os.path.exists(DB_FILE):
        DB = json.load(open(DB_FILE, encoding="utf-8"))


def save():
    json.dump(DB, open(DB_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if APP:
        APP.create_task(send_backup())


async def send_backup(chat=None):
    with open(DB_FILE, "rb") as f:
        msg = await APP.bot.send_document(chat or BACKUP_GROUP, InputFile(f, "backup.json"),
                                          caption="💾 بکاپ")
    if not chat:  # آخرین بکاپ را پین می‌کنیم تا بعد از ری‌استارت رندر بازیابی شود
        try:
            await APP.bot.pin_chat_message(BACKUP_GROUP, msg.message_id, disable_notification=True)
        except Exception as e:
            print("pin error", e)


def nid():
    DB["seq"] += 1
    return str(DB["seq"])


def user(uid, tg=None):
    u = DB["users"].setdefault(str(uid), {"name": "", "username": "", "paid": False,
                                          "in_group": False})
    if tg:
        u["name"] = tg.full_name
        u["username"] = tg.username or ""
    return u


def debt_of(uid):
    for g in DB["groups"].values():
        if uid in g["members"] and g["debt"] is not None:
            return g["debt"]
    return DB["global_debt"]


def groups_of(uid):
    return [g["name"] for g in DB["groups"].values() if uid in g["members"]]


def uline(uid):
    u = DB["users"].get(str(uid), {})
    return (f"👤 {u.get('name','?')} | @{u.get('username') or '-'} | <code>{uid}</code>\n"
            f"💰 بدهی: {debt_of(int(uid))} | 🏷 گروه: {'، '.join(groups_of(int(uid))) or '-'}")


async def reply_long(msg, text):
    for i in range(0, len(text), 3800):
        await msg.reply_text(text[i:i + 3800], parse_mode="HTML")


def kb(rows):
    return M([[B(t, callback_data=d) for t, d in r] for r in rows])


ADMIN_MENU = kb([
    [("💰 تعیین مبلغ", "amt")],
    [("🧾 فیش‌های تایید/رد نشده", "pend")],
    [("💳 مدیریت شماره کارت", "cards")],
    [("📊 وضعیت", "stat")],
    [("⚡ اعمال قدرت", "power")],
    [("🆓 معاف کردن", "exempt")],
    [("💾 دریافت بکاپ", "bk_get"), ("📥 وارد کردن بکاپ", "bk_put")],
])
BACK = [("⬅️ منو", "menu")]


async def pick_card():
    act = [c for c in DB["cards"] if c["active"]]
    if not act:
        return None
    c = act[DB["rr"] % len(act)]
    DB["rr"] += 1
    save()
    return c


async def refresh_member(ctx, uid):
    try:
        cm = await ctx.bot.get_chat_member(MAIN_GROUP, uid)
        user(uid)["in_group"] = cm.status in ("member", "administrator", "creator", "restricted")
    except Exception as e:
        print("member check error", uid, e)


async def resolve(tokens, ctx):
    ids, bad = [], []
    for t in tokens:
        t = t.strip()
        if re.fullmatch(r"-?\d+", t):
            ids.append(int(t)); continue
        name = t.lstrip("@").lower()
        found = next((int(k) for k, v in DB["users"].items()
                      if v["username"].lower() == name), None)
        if not found:
            try:
                found = (await ctx.bot.get_chat("@" + name)).id
            except Exception:
                pass
        (ids.append(found) if found else bad.append(t))
    return ids, bad


# ---------------- /start ----------------
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    uid = update.effective_user.id
    user(uid, update.effective_user)
    await refresh_member(ctx, uid)
    save()
    if uid in ADMINS:
        await update.message.reply_text("پنل ادمین:", reply_markup=ADMIN_MENU)
    else:
        await update.message.reply_text("سلام 👋", reply_markup=kb([[("💳 پرداخت صورت حساب", "pay")]]))


# ---------------- track group members ----------------
async def track_msg(update: Update, ctx):
    u = update.effective_user
    if update.effective_chat.id == MAIN_GROUP and u and not u.is_bot:
        x = user(u.id, u)
        if not x["in_group"]:
            x["in_group"] = True
            save()


async def track_member(update: Update, ctx):
    cm = update.chat_member
    if cm.chat.id != MAIN_GROUP:
        return
    u = user(cm.new_chat_member.user.id, cm.new_chat_member.user)
    u["in_group"] = cm.new_chat_member.status in ("member", "administrator", "creator", "restricted")
    save()


# ---------------- callbacks ----------------
async def cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    d = q.data
    uid = q.from_user.id
    await q.answer()
    ctx.user_data.pop("st", None)
    E = q.edit_message_text

    # ---- user side ----
    if d == "pay":
        user(uid, q.from_user)
        x = user(uid)
        if x["paid"]:
            return await E("✅ پرداخت شما قبلا تایید شده است.")
        await E(f"صورت حساب شما: <b>{debt_of(uid)}</b>\nتایید می‌کنید؟", parse_mode="HTML",
                reply_markup=kb([[("✅ تایید", "payok"), ("❌ انصراف", "cancel")]]))
        return
    if d == "cancel":
        return await E("لغو شد.")
    if d == "payok":
        c = await pick_card()
        if not c:
            return await E("فعلا شماره کارتی ثبت نشده، بعدا تلاش کنید.")
        ctx.user_data["rcpt"] = {"card": c["id"], "debt": debt_of(uid)}
        ctx.user_data["st"] = "receipt"
        return await E(f"💳 شماره کارت: <code>{c['number']}</code>\n👤 به نام: {c['name']}\n"
                       f"💰 مبلغ: {debt_of(uid)}\n\nپس از واریز، تصویر فیش را بفرستید.",
                       parse_mode="HTML")

    # ---- approve / reject (admin group or private) ----
    if d.startswith(("ok:", "no:")):
        if uid not in ADMINS:
            return await q.answer("دسترسی ندارید", show_alert=True)
        act, rid = d.split(":")
        r = DB["receipts"].get(rid)
        if not r or r["status"] != "pending":
            return await q.answer("قبلا بررسی شده", show_alert=True)
        r["status"] = "approved" if act == "ok" else "rejected"
        if act == "ok":
            user(r["uid"])["paid"] = True
            await refresh_member(ctx, r["uid"])
        save()
        mark = "✅ تایید شد" if act == "ok" else "❌ رد شد"
        cap = (q.message.caption or q.message.text or "") + f"\n\n{mark} توسط {q.from_user.full_name}"
        try:
            await (q.edit_message_caption(cap, parse_mode="HTML") if q.message.caption
                   else q.edit_message_text(cap, parse_mode="HTML"))
        except Exception:
            pass
        await ctx.bot.send_message(r["uid"], "✅ فیش شما تایید شد." if act == "ok"
                                   else "❌ فیش شما رد شد. دوباره تلاش کنید.")
        return

    if uid not in ADMINS:
        return

    # ---- admin ----
    if d == "menu":
        return await E("پنل ادمین:", reply_markup=ADMIN_MENU)

    # amount
    if d == "amt":
        rows = [[("💵 مبلغ همگانی", "amt_all")]]
        for gid, g in DB["groups"].items():
            rows.append([(f"👥 {g['name']}", f"g:{gid}")])
        rows.append([("➕ افزودن گروه", "g_add"), ("🗑 حذف گروه", "g_delm")])
        rows.append(BACK)
        return await E("تعیین مبلغ:", reply_markup=kb(rows))
    if d == "amt_all":
        ctx.user_data["st"] = "amt_all"
        return await E(f"مبلغ فعلی: {DB['global_debt']}\nمبلغ جدید همگانی را بفرستید:")
    if d == "g_add":
        ctx.user_data["st"] = "g_add"
        return await E("نام گروه را بفرستید (مثلا: بدهکاران ۲ ماهه):")
    if d == "g_delm":
        rows = [[(f"🗑 {g['name']}", f"g_del:{gid}")] for gid, g in DB["groups"].items()]
        return await E("کدام گروه حذف شود؟", reply_markup=kb(rows + [[("⬅️", "amt")]]))
    if d.startswith("g_del:"):
        DB["groups"].pop(d[6:], None); save()
        return await E("حذف شد.", reply_markup=kb([[("⬅️", "amt")]]))
    if d.startswith("g:"):
        gid = d[2:]
        g = DB["groups"][gid]
        return await E(f"گروه «{g['name']}» | اعضا: {len(g['members'])} | بدهی: {g['debt']}",
                       reply_markup=kb([
                           [("➕ افزودن فرد", f"ga:{gid}"), ("➖ حذف فرد", f"gr:{gid}")],
                           [("🧹 حذف همگی", f"gc:{gid}"), ("📋 لیست گروه", f"gl:{gid}")],
                           [("💰 تعیین/تغییر بدهی گروه", f"gd:{gid}")],
                           [("⬅️", "amt")]]))
    for p, st in (("ga:", "g_addm"), ("gr:", "g_remm"), ("gd:", "g_debt")):
        if d.startswith(p):
            ctx.user_data["st"] = (st, d[3:])
            hint = {"g_addm": "آیدی عددی/یوزرنیم افراد را بفرستید (چند نفر با فاصله، کاما یا خط جدید):",
                    "g_remm": "آیدی/یوزرنیم افرادی که باید حذف شوند:",
                    "g_debt": "مبلغ بدهی این گروه:"}[st]
            return await E(hint)
    if d.startswith("gc:"):
        DB["groups"][d[3:]]["members"] = []; save()
        return await E("همه اعضا حذف شدند.", reply_markup=kb([[("⬅️", f"g:{d[3:]}")]]))
    if d.startswith("gl:"):
        g = DB["groups"][d[3:]]
        txt = "\n\n".join(uline(m) for m in g["members"]) or "خالی"
        await reply_long(q.message, txt)
        return

    # receipts
    if d == "pend":
        pend = [k for k, r in DB["receipts"].items() if r["status"] == "pending"]
        if not pend:
            return await E("فیش در انتظاری نیست.", reply_markup=kb([BACK]))
        await E(f"{len(pend)} فیش در انتظار:")
        for k in pend:
            await send_receipt(uid, k, ctx)
        return

    # cards
    if d == "cards":
        return await E("مدیریت کارت‌ها:", reply_markup=kb([
            [("📋 شماره کارت‌های موجود", "c_list")], [("➕ افزودن", "c_add")],
            [("➖ حذف کارت خاص", "c_delm")], [("🧹 حذف همه", "c_clr")],
            [("🔁 تعیین کارت‌های فعال", "c_act")], BACK]))
    if d == "c_list":
        t = "\n".join(f"{'🟢' if c['active'] else '⚪️'} {c['number']} - {c['name']}"
                      for c in DB["cards"]) or "خالی"
        return await E(t, reply_markup=kb([[("⬅️", "cards")]]))
    if d == "c_add":
        ctx.user_data["st"] = "c_add"
        return await E("شماره کارت را بفرستید:")
    if d == "c_delm":
        rows = [[(f"🗑 {c['number']} {c['name']}", f"c_del:{c['id']}")] for c in DB["cards"]]
        return await E("کدام حذف شود؟", reply_markup=kb(rows + [[("⬅️", "cards")]]))
    if d.startswith("c_del:"):
        DB["cards"] = [c for c in DB["cards"] if c["id"] != d[6:]]; save()
        return await E("حذف شد.", reply_markup=kb([[("⬅️", "cards")]]))
    if d == "c_clr":
        DB["cards"] = []; save()
        return await E("همه حذف شدند.", reply_markup=kb([[("⬅️", "cards")]]))
    if d == "c_act" or d.startswith("c_tg:"):
        if d.startswith("c_tg:"):
            for c in DB["cards"]:
                if c["id"] == d[5:]:
                    c["active"] = not c["active"]
            save()
        rows = [[(f"{'🟢' if c['active'] else '⚪️'} {c['number']} {c['name']}", f"c_tg:{c['id']}")]
                for c in DB["cards"]]
        return await E("روی کارت بزنید تا فعال/غیرفعال شود (بین فعال‌ها چرخشی پخش می‌شود):",
                       reply_markup=kb(rows + [[("⬅️", "cards")]]))

    # status
    if d == "stat":
        return await E("وضعیت:", reply_markup=kb([[("✅ پرداخت کرده‌ها", "s_paid"),
                                                   ("⛔️ بدهکارها", "s_debt")], BACK]))
    if d in ("s_paid", "s_debt"):
        want = d == "s_paid"
        ids = [k for k, v in DB["users"].items()
               if int(k) not in ADMINS and v["paid"] == want and (want or v["in_group"])]
        await reply_long(q.message, f"تعداد: {len(ids)}\n\n" + "\n\n".join(uline(i) for i in ids))
        return

    # power
    if d == "power":
        return await E("اعمال قدرت:", reply_markup=kb([
            [("🏷 تگ پرداخت‌کرده‌ها", "p_tagp")], [("🏷 تگ بدهکاران", "p_tagd")],
            [("🚫 ریموو بدهکاران", "p_kick")], [("🧽 حذف تگ همه", "p_untag")], BACK]))
    if d in ("p_tagp", "p_tagd"):
        ctx.user_data["st"] = d
        return await E("متن تگ را بفرستید:")
    if d == "p_kick":
        return await E("مطمئنی همه بدهکاران از گروه حذف شوند؟",
                       reply_markup=kb([[("✅ بله", "p_kick_ok"), ("❌ خیر", "power")]]))
    if d == "p_kick_ok":
        n = 0
        for k in debtors():
            try:
                await ctx.bot.ban_chat_member(MAIN_GROUP, int(k))
                await ctx.bot.unban_chat_member(MAIN_GROUP, int(k), only_if_banned=True)
                DB["users"][k]["in_group"] = False; n += 1
            except Exception:
                pass
        save()
        return await E(f"{n} نفر ریموو شدند.", reply_markup=kb([[("⬅️", "power")]]))
    if d == "p_untag":
        n = 0
        for k, v in DB["users"].items():
            if v["in_group"]:
                n += await set_tag(ctx, int(k), "")
        return await E(f"تگ {n} نفر پاک شد.", reply_markup=kb([[("⬅️", "power")]]))

    if d == "exempt":
        ctx.user_data["st"] = "exempt"
        return await E("آیدی عددی یا یوزرنیم را بفرستید:")

    if d == "bk_get":
        await send_backup(uid)
        return
    if d == "bk_put":
        ctx.user_data["st"] = "restore"
        return await E("فایل بکاپ (json) را بفرستید:")


def debtors():
    return [k for k, v in DB["users"].items()
            if int(k) not in ADMINS and v["in_group"] and not v["paid"]]


async def set_tag(ctx, uid, tag):
    # Bot API: setChatMemberTag (نیاز به ادمین بودن ربات با دسترسی مدیریت تگ)
    try:
        await ctx.bot.do_api_request("setChatMemberTag", api_kwargs={
            "chat_id": MAIN_GROUP, "user_id": uid, "tag": tag})
        return 1
    except Exception as e:
        print("tag error", uid, e)
        return 0


async def send_receipt(chat, rid, ctx):
    r = DB["receipts"][rid]
    u = DB["users"].get(str(r["uid"]), {})
    c = next((c for c in DB["cards"] if c["id"] == r["card"]), {"number": "-", "name": "-"})
    cap = (f"🧾 فیش #{rid}\n👤 {u.get('name')} | @{u.get('username') or '-'} | <code>{r['uid']}</code>\n"
           f"💰 بدهی: {r['debt']}\n🏷 گروه: {'، '.join(groups_of(r['uid'])) or '-'}\n"
           f"💳 {c['number']} به نام {c['name']}")
    markup = kb([[("✅ تایید", f"ok:{rid}"), ("❌ رد", f"no:{rid}")]])
    if r["type"] == "photo":
        await ctx.bot.send_photo(chat, r["file"], caption=cap, parse_mode="HTML", reply_markup=markup)
    elif r["type"] == "doc":
        await ctx.bot.send_document(chat, r["file"], caption=cap, parse_mode="HTML", reply_markup=markup)
    else:
        await ctx.bot.send_message(chat, cap + "\n\n" + r["file"], parse_mode="HTML", reply_markup=markup)


# ---------------- private messages ----------------
async def private(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global DB
    m = update.message
    uid = m.from_user.id
    st = ctx.user_data.get("st")
    if not st:
        return
    text = m.text or ""

    # user: receipt
    if st == "receipt":
        if m.photo:
            t, f = "photo", m.photo[-1].file_id
        elif m.document:
            t, f = "doc", m.document.file_id
        else:
            return await m.reply_text("لطفا تصویر فیش را بفرستید.")
        info = ctx.user_data.pop("rcpt")
        ctx.user_data.pop("st")
        rid = nid()
        DB["receipts"][rid] = {"uid": uid, "debt": info["debt"], "card": info["card"],
                               "status": "pending", "type": t, "file": f}
        save()
        await send_receipt(ADMIN_GROUP, rid, ctx)
        return await m.reply_text("فیش شما ارسال شد؛ منتظر تایید ادمین باشید ⏳")

    if uid not in ADMINS:
        return
    ctx.user_data.pop("st")
    back = kb([BACK])

    if st == "restore":
        if not m.document:
            return await m.reply_text("فایل json بفرستید.")
        try:
            f = await (await m.document.get_file()).download_as_bytearray()
            DB = json.loads(f.decode("utf-8"))
            save()
            return await m.reply_text("✅ بکاپ بازیابی شد.", reply_markup=back)
        except Exception as e:
            return await m.reply_text(f"خطا: {e}")

    if st == "amt_all":
        DB["global_debt"] = int(re.sub(r"\D", "", text) or 0); save()
        return await m.reply_text("✅ ثبت شد.", reply_markup=back)
    if st == "g_add":
        DB["groups"][nid()] = {"name": text.strip(), "debt": None, "members": []}; save()
        return await m.reply_text("✅ گروه ساخته شد.", reply_markup=back)
    if st == "c_add":
        ctx.user_data["st"] = "c_name"
        ctx.user_data["cn"] = text.strip()
        return await m.reply_text("نام صاحب کارت را بفرستید:")
    if st == "c_name":
        DB["cards"].append({"id": nid(), "number": ctx.user_data.pop("cn"),
                            "name": text.strip(), "active": True}); save()
        return await m.reply_text("✅ کارت اضافه شد.", reply_markup=back)
    if st in ("p_tagp", "p_tagd"):
        want = st == "p_tagp"
        n = 0
        for k, v in DB["users"].items():
            if int(k) not in ADMINS and v["paid"] == want and (want or v["in_group"]):
                n += await set_tag(ctx, int(k), text.strip())
        return await m.reply_text(f"تگ برای {n} نفر ثبت شد.", reply_markup=back)
    if st == "exempt":
        ids, bad = await resolve(re.split(r"[\s,،]+", text.strip()), ctx)
        for i in ids:
            user(i)["paid"] = True
        save()
        return await m.reply_text(f"✅ معاف شدند: {len(ids)}\nنامعتبر: {bad or '-'}", reply_markup=back)
    if isinstance(st, tuple):
        kind, gid = st
        g = DB["groups"].get(gid)
        if not g:
            return
        if kind == "g_debt":
            g["debt"] = int(re.sub(r"\D", "", text) or 0); save()
            return await m.reply_text("✅ بدهی گروه ثبت شد.", reply_markup=back)
        ids, bad = await resolve([t for t in re.split(r"[\s,،]+", text.strip()) if t], ctx)
        if kind == "g_addm":
            for i in ids:
                user(i)
                if i not in g["members"]:
                    g["members"].append(i)
        else:
            g["members"] = [x for x in g["members"] if x not in ids]
        save()
        return await m.reply_text(f"✅ انجام شد: {len(ids)}\nشناسایی نشد: {bad or '-'}", reply_markup=back)


class Health(BaseHTTPRequestHandler):
    def _ok(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()

    def do_GET(self):
        self._ok()
        self.wfile.write(b"OK")

    def do_HEAD(self):
        self._ok()

    def log_message(self, *a):
        pass


def run_health():
    port = int(os.environ.get("PORT", "10000"))
    HTTPServer(("0.0.0.0", port), Health).serve_forever()


async def post_init(app):
    # دیسک رندر موقتی است؛ اگر دیتا نبود، آخرین بکاپ پین‌شده را برمی‌گردانیم
    if os.path.exists(DB_FILE):
        return
    try:
        pm = (await app.bot.get_chat(BACKUP_GROUP)).pinned_message
        if pm and pm.document:
            f = await app.bot.get_file(pm.document.file_id)
            await f.download_to_drive(DB_FILE)
            load()
            print("restored from pinned backup")
    except Exception as e:
        print("auto-restore failed", e)


def main():
    global APP
    load()
    threading.Thread(target=run_health, daemon=True).start()
    APP = Application.builder().token(TOKEN).post_init(post_init).build()
    APP.add_handler(CommandHandler("start", start))
    APP.add_handler(CallbackQueryHandler(cb))
    APP.add_handler(ChatMemberHandler(track_member, ChatMemberHandler.CHAT_MEMBER))
    APP.add_handler(MessageHandler(filters.ChatType.GROUPS, track_msg), group=1)
    APP.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, private))
    APP.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
