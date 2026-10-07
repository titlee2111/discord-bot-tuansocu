import os
import sys
import asyncio
import logging
import re
import time
import json
import aiohttp
import discord
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv
from ai_service import AIService

# Thiết lập logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("TuanSoCuBot")

# Load biến môi trường
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = os.getenv("COMMAND_PREFIX", "!")
SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT")

BOT_OWNER_ID = int(os.getenv("BOT_OWNER_ID", "756393451527995453"))

# Server The Dark Knight — bot sẽ vào vai Tuấn Sờ Cu người thật
DARK_KNIGHT_GUILD_ID = int(os.getenv("DARK_KNIGHT_GUILD_ID", "1296159555679686676"))
# Xác suất bot tự động rep mỗi tin nhắn trong DK server (0.0 - 1.0), mặc định 70%
DK_REPLY_CHANCE = float(os.getenv("DK_REPLY_CHANCE", "0.7"))

# Danh sách ID chủ bot / admin (Bot sẽ KHÔNG BAO GIỜ tự động rep khi họ nhắn tin trong server)
IGNORED_USER_IDS = {BOT_OWNER_ID, 756393451527995453}
extra_admins = os.getenv("ADMIN_IDS", "")
for adm in extra_admins.split(","):
    if adm.strip().isdigit():
        IGNORED_USER_IDS.add(int(adm.strip()))

if not TOKEN:
    logger.error("Lỗi: Không tìm thấy DISCORD_TOKEN trong file .env!")
    sys.exit(1)

# Cấu hình intents đầy đủ
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)
ai = AIService(base_system_prompt=SYSTEM_PROMPT, knowledge_file="kien_thuc.txt")

# Bộ đệm thời gian chống spam khi tự động trả lời trong kênh
channel_last_reply = {}
# Bộ đệm cooldown riêng cho Dark Knight server
dk_last_reply = {}

def split_message(content: str, max_length: int = 1900):
    """Chia nhỏ tin nhắn nếu dài hơn giới hạn của Discord"""
    if len(content) <= max_length:
        return [content]
    
    chunks = []
    lines = content.split("\n")
    current_chunk = ""
    
    for line in lines:
        if len(current_chunk) + len(line) + 1 <= max_length:
            current_chunk += (line + "\n")
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            while len(line) > max_length:
                chunks.append(line[:max_length])
                line = line[max_length:]
            current_chunk = line + "\n"
            
    if current_chunk.strip():
        chunks.append(current_chunk.strip())
    return chunks

async def fetch_recent_context(channel, before_message=None, limit: int = 8) -> list:
    """Lấy danh sách các tin nhắn gần nhất trong kênh để nạp ngữ cảnh cho Tuấn Sờ Cu chém gió khớp chủ đề"""
    context = []
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        return context
    try:
        kwargs = {"limit": limit}
        if before_message:
            kwargs["before"] = before_message
        async for msg in channel.history(**kwargs):
            text = msg.clean_content.strip()
            if text and not text.startswith(PREFIX):
                sender = "Tuấn Sờ Cu" if msg.author == bot.user else msg.author.display_name
                context.append(f"{sender}: {text}")
        context.reverse()
    except Exception as e:
        logger.debug(f"Không thể đọc ngữ cảnh kênh {channel.id}: {e}")
    return context

def is_user_inquiry(content: str) -> bool:
    """Nhận diện xem tin nhắn có phải là một thắc mắc / câu hỏi cần giải đáp hay không"""
    if not content or len(content.strip()) < 4:
        return False
    
    text = content.strip().lower()

    # Dấu hiệu 1: Chứa dấu chấm hỏi
    if "?" in text or "？" in text:
        return True

    # Dấu hiệu 2: Các từ khóa hỏi đáp / báo lỗi Tiếng Việt
    vi_keywords = [
        # Hỏi
        "tại sao", "sao lại", "làm sao", "làm thế nào", "như thế nào",
        "sao k", "sao không", "sao ko", "sao chưa", "sao bị", "sao vậy",
        "cho hỏi", "cho mình hỏi", "cho em hỏi", "ai biết", "có ai biết",
        "chỉ mình", "chỉ em", "hướng dẫn", "giúp mình", "giúp em", "cứu",
        "cách nào", "có cách", "biết cách", "thử cách",
        # Báo lỗi / kết nối
        "bị lỗi", "lỗi gì", "lỗi này", "lỗi r", "lỗi rồi",
        "k được", "không được", "ko đc", "k đc", "không đc",
        "k lên", "không lên", "ko lên", "k chạy", "không chạy", "ko chạy",
        "k kết nối", "không kết nối", "ko kết nối",
        "k nhận", "không nhận", "ko nhận",
        "k hoạt động", "không hoạt động",
        "bị kẹt", "bị đơ", "văng game", "crash", "mất mạng", "dis mạng",
        "bị dis", "disconnect", "disconnected",
        # Hoạt động chung / thắc mắc
        "ải", "kèo", "solo", "leo rank",
        "cài đặt", "cài lại", "reinstall", "setup",
        # Kết quả xấu
        "failed", "fail", "error", "không thấy", "mất rồi", "biến mất",
        "không hiện", "k hiện", "ko hiện",
    ]
    for kw in vi_keywords:
        if kw in text:
            return True

    # Dấu hiệu 3: Các từ khóa hỏi đáp / báo lỗi Tiếng Anh (English)
    en_keywords = [
        "why", "how to", "how do", "how can", "what is", "where is",
        "not working", "doesnt work", "doesn't work", "wont work", "won't work",
        "cant", "can't", "cannot", "issue", "error", "bug",
        "failing", "failed", "crash", "disconnect", "disconnected",
        "please help", "anyone know", "help me", "need help",
        "not connecting", "not loading", "not showing",
    ]
    for kw in en_keywords:
        if re.search(r'\b' + re.escape(kw) + r'\b', text):
            return True

    # Dấu hiệu 4: Tin nhắn ngắn kiểu "lỗi r", "k lên", "sao vậy" (4-25 ký tự, không phải chào hỏi)
    greeting_words = {"hi", "hello", "chào", "hey", "ok", "oke", "okay", "thanks", "cảm ơn", "camon", "haha", "lol"}
    if len(text) <= 25:
        for gw in greeting_words:
            if text.strip() == gw or text.strip().startswith(gw + " "):
                return False
        # Tin nhắn ngắn chứa từ tiêu cực / hành động → có thể là báo lỗi
        short_signals = ["lỗi", "lag", "out", "k vô", "vô k", "sao", "hả", "hả", "heh", "bị"]
        for sig in short_signals:
            if sig in text:
                return True

    return False

# ==================== SUMMARY HELPER ====================

async def execute_summary(channel: discord.TextChannel, limit: int = 30):
    """Hàm lõi thực hiện đọc và tóm tắt tin nhắn trong kênh"""
    if not isinstance(channel, discord.TextChannel):
        return None, "⚠️ Lệnh tóm tắt chỉ hoạt động trong kênh chat văn bản của server!"

    perms = channel.permissions_for(channel.guild.me)
    if not perms.read_message_history:
        return None, (
            "❌ **Bot thiếu quyền đọc lịch sử tin nhắn!**\n"
            "Để bot tóm tắt được, Admin hoặc bạn hãy vào **Cài đặt kênh > Quyền** và cấp quyền **'Read Message History' (Xem lịch sử tin nhắn)** cho bot **Tuấn Sờ Cu** nhé!"
        )

    if limit < 5:
        limit = 5
    elif limit > 100:
        limit = 100

    messages_text = []
    try:
        async for msg in channel.history(limit=limit + 10):
            if msg.author == bot.user:
                continue
            clean = msg.clean_content.strip()
            if clean.startswith(PREFIX + "summary") or clean.startswith(PREFIX + "tomtat") or clean.lower() == "summary":
                continue
            if clean:
                messages_text.append(f"{msg.author.display_name}: {clean}")
            if len(messages_text) >= limit:
                break
    except Exception as e:
        logger.error(f"Lỗi khi đọc lịch sử kênh: {e}")
        return None, f"❌ Không thể đọc lịch sử tin nhắn trong kênh: {e}"

    if len(messages_text) < 2:
        return None, "⚠️ Kênh này chưa có đủ tin nhắn gần đây để tóm tắt (cần ít nhất 2 tin nhắn của mọi người)!"

    messages_text.reverse()
    raw_chat = "\n".join(messages_text)

    try:
        summary_result = await ai.summarize_chat(raw_chat, len(messages_text))
        return summary_result, len(messages_text)
    except Exception as e:
        logger.error(f"Lỗi AI tóm tắt: {e}")
        return None, f"❌ Lỗi khi gửi dữ liệu cho AI tóm tắt: {e}"

# ==================== DISCORD EVENTS ====================

@bot.event
async def on_ready():
    logger.info("=== Bot Tuấn Sờ Cu đã Online thành công! ===")
    logger.info(f"Tên bot: {bot.user} (ID: {bot.user.id})")
    
    # Tự động nhận diện chủ sở hữu bot
    try:
        app = await bot.application_info()
        if app.owner:
            IGNORED_USER_IDS.add(app.owner.id)
            logger.info(f"Đã nhận diện chủ bot: {app.owner.name} (ID: {app.owner.id}) - Bot sẽ KHÔNG BAO GIỜ tự động rep tin nhắn của chủ bot.")
    except Exception as e:
        logger.warning(f"Không thể đọc thông tin chủ bot: {e}")

    activity = discord.CustomActivity(name="Chém gió cùng anh em 😎")
    await bot.change_presence(status=discord.Status.online, activity=activity)

    for guild in bot.guilds:
        try:
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            logger.info(f"Đã đồng bộ tức thì {len(synced)} lệnh Slash cho server '{guild.name}' (ID: {guild.id})")
        except Exception as e:
            logger.warning(f"Không thể đồng bộ lệnh cho server {guild.name}: {e}")

    # Tự động dọn dẹp các tin nhắn cũ của Tuấn trong kênh 1420142364454027307 khi bot có quyền
    asyncio.create_task(auto_cleanup_target_channel())

async def auto_cleanup_target_channel():
    """Tự động dọn dẹp tin nhắn bot đã gửi ở kênh được chỉ định khi bot có quyền truy cập"""
    await bot.wait_until_ready()
    target_ch_id = 1420142364454027307
    ch = bot.get_channel(target_ch_id)
    if not ch:
        try:
            ch = await bot.fetch_channel(target_ch_id)
        except Exception:
            return
    if ch:
        deleted = 0
        try:
            async for msg in ch.history(limit=100):
                if msg.author == bot.user:
                    try:
                        await msg.delete()
                        deleted += 1
                        await asyncio.sleep(0.4)
                    except Exception:
                        pass
            if deleted > 0:
                logger.info(f"Đã tự động xóa {deleted} tin nhắn của Tuấn Sờ Cu tại kênh {target_ch_id}")
        except Exception as e:
            logger.debug(f"Không thể xóa tin nhắn kênh {target_ch_id}: {e}")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    content_clean = message.content.strip()
    content_lower = content_clean.lower()

    # KHÔNG BAO GIỜ TỰ ĐỘNG REP KHI CHỦ BOT / ADMIN NHẮN TIN TRONG BẤT KỲ SERVER NÀO
    if message.author.id in IGNORED_USER_IDS:
        if content_clean.startswith(PREFIX):
            await bot.process_commands(message)
        return

    # Bỏ qua nếu tin nhắn bắt đầu bằng prefix lệnh
    if content_clean.startswith(PREFIX):
        await bot.process_commands(message)
        return

    # =====================================================================
    # ⚔️  SERVER THE DARK KNIGHT — BOT VÀO VAI TUẤN SỜ CU NGƯỜI THẬT
    # =====================================================================
    is_dk_server = (
        message.guild is not None and
        (
            message.guild.id == DARK_KNIGHT_GUILD_ID or
            "dark knight" in message.guild.name.lower()
        ) and
        isinstance(message.channel, discord.TextChannel)
    )

    if is_dk_server and content_clean:
        import random
        is_mentioned_dk = bot.user.mentioned_in(message) and not message.mention_everyone
        now = time.time()
        ch_id = message.channel.id

        should_reply_dk = False
        is_calling_name = any(k in content_lower for k in ["tuấn", "tuan", "tuấn sờ cu", "chú tuấn", "anh tuấn", "thằng tuấn"])
        last_t = dk_last_reply.get(ch_id, 0)
        time_passed = now - last_t

        # 1. Nếu tag bot hoặc gọi tên Tuấn -> 100% trả lời (cooldown ngắn 2.5s)
        if is_mentioned_dk or is_calling_name:
            if time_passed >= 2.5 or is_mentioned_dk:
                should_reply_dk = True
        # 2. Nếu là câu hỏi hoặc thắc mắc -> 85% cơ hội trả lời (cooldown 3.5s)
        elif ("?" in content_clean or "？" in content_clean or any(w in content_lower for w in ["sao", "gì", "đâu", "ai", "hả", "thế", "chưa", "kèo", "game"])) and time_passed >= 3.5:
            if random.random() < 0.85:
                should_reply_dk = True
        # 3. Các cuộc trò chuyện bình thường khác -> 70% cơ hội nhảy vào hóng hớt (cooldown 4s)
        elif random.random() < DK_REPLY_CHANCE and time_passed >= 4.0:
            should_reply_dk = True

        if should_reply_dk:
            dk_last_reply[ch_id] = now
            clean_dk = content_clean
            for m in message.mentions:
                clean_dk = clean_dk.replace(f"<@{m.id}>", "").replace(f"<@!{m.id}>", "")
            clean_dk = clean_dk.strip()
            if not clean_dk:
                clean_dk = "alo"

            async with message.channel.typing():
                try:
                    # Thu thập 8 tin nhắn gần nhất trong kênh để nạp ngữ cảnh thảo luận
                    recent_ctx = await fetch_recent_context(message.channel, before_message=message, limit=8)
                    reply_text = await ai.get_dark_knight_response(
                        channel_id=ch_id,
                        user_message=clean_dk,
                        user_name=message.author.display_name,
                        recent_context=recent_ctx
                    )
                    chunks = split_message(reply_text)
                    for i, chunk in enumerate(chunks):
                        if i == 0:
                            await message.reply(chunk, mention_author=False)
                        else:
                            await message.channel.send(chunk)
                except Exception as e:
                    logger.error(f"[DarkKnight] Lỗi reply: {e}")
        return

    # =====================================================================

    # 1. Hỗ trợ người dùng gõ chỉ mỗi chữ "summary" hoặc "tóm tắt"
    if content_lower in ["summary", "tóm tắt", "tom tat"]:
        async with message.channel.typing():
            res, count = await execute_summary(message.channel, 30)
            if res:
                embed = discord.Embed(
                    title=f"📋 Bảng Tóm Tắt {count} Tin Nhắn Gần Nhất",
                    description=res,
                    color=discord.Color.gold()
                )
                embed.set_footer(text=f"Kênh: #{message.channel.name} • Tóm tắt bởi Tuấn Sờ Cu 😎")
                await message.reply(embed=embed, mention_author=False)
            else:
                await message.reply(count, mention_author=False)
        return

    # 2. XỬ LÝ ĐỌC VÀ PHÂN TÍCH HÌNH ẢNH (VISION AI)
    image_attachment = None
    for att in message.attachments:
        if att.content_type and att.content_type.startswith("image/"):
            image_attachment = att
            break
        elif any(att.filename.lower().endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".webp"]):
            image_attachment = att
            break

    if image_attachment:
        async with message.channel.typing():
            try:
                img_bytes = await image_attachment.read()
                content_type = image_attachment.content_type or "image/png"
                reply_text = await ai.analyze_image(
                    image_bytes=img_bytes,
                    content_type=content_type,
                    user_prompt=content_clean,
                    user_name=message.author.display_name
                )
                chunks = split_message(reply_text)
                for i, chunk in enumerate(chunks):
                    if i == 0:
                        await message.reply(chunk, mention_author=False)
                    else:
                        await message.channel.send(chunk)
            except Exception as e:
                logger.error(f"Lỗi xử lý ảnh từ Discord: {e}")
                await message.reply(f"Ui da, có lỗi khi đọc bức ảnh này rồi bác ơi: {e}")
        return

    # 3. KIỂM TRA ĐIỀU KIỆN TRẢ LỜI TIN NHẮN VĂN BẢN
    is_dm = (message.guild is None) or isinstance(message.channel, discord.DMChannel)
    is_mentioned = bot.user.mentioned_in(message) and not message.mention_everyone
    is_inquiry = is_user_inquiry(content_clean)

    if is_dm or is_mentioned or is_inquiry:
        now = time.time()
        ch_id = message.channel.id
        if not is_mentioned and not is_dm:
            if ch_id in channel_last_reply and (now - channel_last_reply[ch_id]) < 3.0:
                return
        channel_last_reply[ch_id] = now

        clean_content = content_clean
        for mention in message.mentions:
            clean_content = clean_content.replace(f"<@{mention.id}>", "").replace(f"<@!{mention.id}>", "")
        clean_content = clean_content.strip()

        # Nếu tag nhờ tóm tắt
        clean_lower = clean_content.lower()
        if "tóm tắt" in clean_lower or "summary" in clean_lower or "tom tat" in clean_lower:
            match = re.search(r'\d+', clean_content)
            limit = int(match.group()) if match else 30
            async with message.channel.typing():
                res, count = await execute_summary(message.channel, limit)
                if res:
                    embed = discord.Embed(
                        title=f"📋 Bảng Tóm Tắt {count} Tin Nhắn Gần Nhất",
                        description=res,
                        color=discord.Color.gold()
                    )
                    ch_name = "DM" if is_dm else getattr(message.channel, "name", "Chat")
                    embed.set_footer(text=f"Kênh: #{ch_name} • Tóm tắt bởi Tuấn Sờ Cu 😎")
                    try:
                        await message.reply(embed=embed, mention_author=False)
                    except Exception:
                        await message.channel.send(embed=embed)
                else:
                    try:
                        await message.reply(count, mention_author=False)
                    except Exception:
                        await message.channel.send(count)
            return

        if not clean_content:
            clean_content = "Chào bạn! Mình có thể giúp gì cho bạn?"

        async with message.channel.typing():
            author_name = message.author.display_name
            recent_ctx = await fetch_recent_context(message.channel, before_message=message, limit=8)
            reply_text = await ai.get_response(message.channel.id, clean_content, author_name, recent_context=recent_ctx)
            chunks = split_message(reply_text)
            for i, chunk in enumerate(chunks):
                try:
                    if is_dm:
                        await message.channel.send(chunk)
                    elif i == 0:
                        await message.reply(chunk, mention_author=False)
                    else:
                        await message.channel.send(chunk)
                except Exception as e:
                    logger.warning(f"Lỗi khi gửi reply ({e}), thử send trực tiếp...")
                    await message.channel.send(chunk)
        return

    await bot.process_commands(message)

# ==================== CÁC LỆNH PREFIX ====================

@bot.command(name="chat", aliases=["ask", "hoi"])
async def chat_cmd(ctx: commands.Context, *, prompt: str = None):
    """Trò chuyện: !chat <câu hỏi>"""
    if not prompt:
        await ctx.reply(f"Bác hãy nhập câu hỏi sau lệnh, ví dụ: `{PREFIX}chat Bạn biết gì về server này?`")
        return

    async with ctx.typing():
        author_name = ctx.author.display_name
        reply_text = await ai.get_response(ctx.channel.id, prompt, author_name)
        chunks = split_message(reply_text)
        for i, chunk in enumerate(chunks):
            if i == 0:
                await ctx.reply(chunk, mention_author=False)
            else:
                await ctx.channel.send(chunk)

@bot.command(name="summary", aliases=["tomtat", "recap"])
async def summary_cmd(ctx: commands.Context, limit: int = 30):
    """Tóm tắt tin nhắn gần đây trong kênh: !summary [số_lượng_tin_nhắn]"""
    async with ctx.typing():
        res, count = await execute_summary(ctx.channel, limit)
        if res:
            embed = discord.Embed(
                title=f"📋 Bảng Tóm Tắt {count} Tin Nhắn Gần Nhất",
                description=res,
                color=discord.Color.gold()
            )
            embed.set_footer(text=f"Kênh: #{ctx.channel.name} • Tóm tắt bởi Tuấn Sờ Cu 😎")
            await ctx.reply(embed=embed)
        else:
            await ctx.reply(count)

@bot.command(name="reload", aliases=["napkienthuc", "load"])
async def reload_cmd(ctx: commands.Context):
    """Nạp lại kiến thức mới nhất từ file kien_thuc.txt"""
    result = ai.load_knowledge()
    await ctx.reply(f"🔄 **Kết quả cập nhật:** {result}")

@bot.command(name="reset", aliases=["clear", "xoachat"])
async def reset_cmd(ctx: commands.Context):
    """Xóa lịch sử trò chuyện trong kênh: !reset"""
    cleared = ai.reset_history(ctx.channel.id)
    if cleared:
        await ctx.reply("🧹 Đã làm mới ký ức trong kênh này rồi nha!")
    else:
        await ctx.reply("Kênh này chưa có ký ức trò chuyện nào để xóa!")

@bot.command(name="ping")
async def ping_cmd(ctx: commands.Context):
    """Kiểm tra độ trễ mạng: !ping"""
    latency = round(bot.latency * 1000)
    await ctx.reply(f"🏓 Pong! Độ trễ hiện tại: **{latency}ms**.")

@bot.command(name="clearbot", aliases=["xoabot", "donbot"])
async def clear_bot_cmd(ctx: commands.Context, limit: int = 50):
    """Xóa tất cả tin nhắn Tuấn Sờ Cu đã gửi trong kênh này: !clearbot [số_tin]"""
    deleted_count = 0
    async with ctx.typing():
        try:
            async for msg in ctx.channel.history(limit=min(limit, 200)):
                if msg.author == bot.user:
                    try:
                        await msg.delete()
                        deleted_count += 1
                        await asyncio.sleep(0.4)
                    except Exception:
                        pass
            notice = await ctx.send(f"🧹 Đã xóa sạch {deleted_count} tin nhắn của Tuấn trong kênh này!")
            await asyncio.sleep(4)
            try:
                await notice.delete()
            except Exception:
                pass
        except Exception as e:
            await ctx.send(f"❌ Lỗi khi dọn dẹp: {e}")

@bot.command(name="help", aliases=["trogiup", "huongdan"])
async def help_cmd(ctx: commands.Context):
    """Hiển thị menu trợ giúp: !help"""
    embed = discord.Embed(
        title="😎 Tuấn Sờ Cu — Đẹp Trai Số 1 Server",
        description="Tuấn Sờ Cu bằng xương bằng thịt, luôn sẵn sàng chém gió, tấu hài và đồng hành cùng anh em!",
        color=discord.Color.blue()
    )
    embed.add_field(
        name="💬 1. Chém Gió Tự Động & Đọc Ảnh",
        value=(
            "• **Nói chuyện tự nhiên:** Cứ tag Tuấn hoặc réo tên 'Tuấn ơi' là Tuấn sẽ nhảy vào chém gió ngay!\n"
            "• **Đọc ảnh:** Gửi bất kỳ bức ảnh nào vào kênh, Tuấn cũng đọc được và bình luận cùng anh em."
        ),
        inline=False
    )
    embed.add_field(
        name="📋 2. Tóm Tắt Tin Nhắn Trong Kênh",
        value=(
            f"• Gõ đơn giản: `summary` hoặc `tóm tắt`\n"
            f"• Gõ lệnh: `{PREFIX}summary [số_tin]` (Ví dụ: `{PREFIX}summary 30`)\n"
            f"• Dùng Slash command: `/summary [so_luong]`"
        ),
        inline=False
    )
    embed.set_footer(text="Tuấn Sờ Cu • Người thật đẹp trai nhất vũ trụ • Boss: Memories 😎")
    await ctx.reply(embed=embed)

@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.CommandNotFound):
        return
    logger.error(f"Lỗi lệnh {ctx.command}: {error}")
    await ctx.reply(f"⚠️ **Đã xảy ra lỗi:** {error}")

# ==================== CÁC LỆNH SLASH (/) ====================

@bot.tree.command(name="chat", description="Trò chuyện hoặc đặt câu hỏi với Tuấn Sờ Cu")
@app_commands.describe(cau_hoi="Nội dung bạn muốn hỏi hoặc trò chuyện")
async def slash_chat(interaction: discord.Interaction, cau_hoi: str):
    await interaction.response.defer(thinking=True)
    try:
        author_name = interaction.user.display_name
        reply_text = await ai.get_response(interaction.channel_id, cau_hoi, author_name)
        chunks = split_message(reply_text)
        await interaction.followup.send(chunks[0])
        for chunk in chunks[1:]:
            await interaction.channel.send(chunk)
    except Exception as e:
        logger.error(f"Lỗi slash_chat: {e}")
        await interaction.followup.send("Ui có lỗi nhỏ khi xử lý câu hỏi này rồi bác ơi!")

@bot.tree.command(name="summary", description="Đọc và tóm tắt các tin nhắn gần đây trong kênh này")
@app_commands.describe(so_luong="Số lượng tin nhắn gần nhất muốn tóm tắt (Mặc định: 30, tối đa: 100)")
async def slash_summary(interaction: discord.Interaction, so_luong: int = 30):
    await interaction.response.defer(thinking=True)
    try:
        # Thông báo trạng thái đang xử lý
        await interaction.followup.send(
            f"⏳ Đang đọc **{so_luong}** tin nhắn và tóm tắt... (chờ xíu nha)",
            ephemeral=True
        )
        res, count = await execute_summary(interaction.channel, so_luong)
        if res:
            # Cắt nếu quá 4096 ký tự (giới hạn Discord embed description)
            description = res if len(res) <= 4000 else res[:3997] + "..."
            embed = discord.Embed(
                title=f"📋 Bảng Tóm Tắt {count} Tin Nhắn Gần Nhất",
                description=description,
                color=discord.Color.gold()
            )
            embed.set_footer(text=f"Kênh: #{interaction.channel.name} • Tóm tắt bởi Tuấn Sờ Cu 😎")
            await interaction.channel.send(embed=embed)
        else:
            await interaction.channel.send(count)  # count chứa thông báo lỗi khi res=None
    except Exception as e:
        logger.error(f"Lỗi slash_summary: {e}")
        try:
            await interaction.followup.send(f"❌ Không thể tóm tắt: {e}", ephemeral=True)
        except Exception:
            await interaction.channel.send(f"❌ Lỗi khi tóm tắt kênh này: {e}")

@bot.tree.command(name="reload", description="Cập nhật lại kiến thức mới nhất từ file kien_thuc.txt")
async def slash_reload(interaction: discord.Interaction):
    result = ai.load_knowledge()
    await interaction.response.send_message(f"🔄 **Cập nhật:** {result}", ephemeral=True)

@bot.tree.command(name="reset", description="Xóa lịch sử nhớ trong kênh này")
async def slash_reset(interaction: discord.Interaction):
    ai.reset_history(interaction.channel_id)
    await interaction.response.send_message("🧹 Đã làm mới ký ức cuộc trò chuyện trong kênh này!")

@bot.tree.command(name="ping", description="Kiểm tra độ trễ mạng")
async def slash_ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    await interaction.response.send_message(f"🏓 Pong! Độ trễ: **{latency}ms**.")

@bot.tree.command(name="clearbot", description="Xóa tất cả tin nhắn mà Tuấn Sờ Cu đã gửi gần đây trong kênh này")
@app_commands.describe(so_luong="Số lượng tin nhắn cần quét để xóa (Mặc định: 50, tối đa: 200)")
async def slash_clearbot(interaction: discord.Interaction, so_luong: int = 50):
    await interaction.response.defer(ephemeral=True)
    deleted_count = 0
    try:
        async for msg in interaction.channel.history(limit=min(so_luong, 200)):
            if msg.author == bot.user:
                try:
                    await msg.delete()
                    deleted_count += 1
                    await asyncio.sleep(0.4)
                except Exception:
                    pass
        await interaction.followup.send(f"🧹 Đã xóa sạch **{deleted_count}** tin nhắn của Tuấn Sờ Cu trong kênh này!", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Lỗi: {e}", ephemeral=True)

@bot.tree.command(name="help", description="Xem hướng dẫn sử dụng Tuấn Sờ Cu")
async def slash_help(interaction: discord.Interaction):
    embed = discord.Embed(
        title="😎 Menu Hướng Dẫn Tuấn Sờ Cu",
        description="Chào anh em! Dưới đây là các tính năng của Tuấn Sờ Cu:",
        color=discord.Color.blue()
    )
    embed.add_field(
        name="💬 1. Chém Gió & Tương Tác",
        value=(
            "• **Nói chuyện tự nhiên:** Cứ tag Tuấn hoặc réo tên 'Tuấn ơi' là Tuấn sẽ nhảy vào chém gió ngay!\n"
            "• **Đọc ảnh:** Gửi bất kỳ bức ảnh nào vào kênh, Tuấn cũng đọc được và bình luận cùng anh em.\n"
            "• **Ra lệnh trả lời 1 người ở kênh bất kỳ:**\n"
            "  - Cách 1: Chuột phải vào tin nhắn ➔ **Apps** ➔ **Tuấn Trả Lời Tin Này**.\n"
            "  - Cách 2: Dùng lệnh `/tra_loi kenh:#kênh nguoi_hoi:@user cau_hoi:...`"
        ),
        inline=False
    )
    embed.add_field(
        name="📋 2. Tiện Ích Khác",
        value=(
            "• `/summary [số lượng]` hoặc gõ `summary` để tóm tắt tin nhắn kênh.\n"
            "• `/ping` kiểm tra độ trễ mạng.\n"
            "• `/reset` làm mới ký ức cuộc trò chuyện trong kênh."
        ),
        inline=False
    )
    embed.set_footer(text="Tuấn Sờ Cu • Người thật đẹp trai nhất vũ trụ • Boss: Memories 😎")
    await interaction.response.send_message(embed=embed)

# ==================== LỆNH ĐIỀU BOT TRẢ LỜI CHO 1 NGƯỜI Ở KÊNH CỤ THỂ ====================

@bot.tree.context_menu(name="Tuấn Trả Lời Tin Này")
async def context_reply_message(interaction: discord.Interaction, message: discord.Message):
    """Chuột phải vào tin nhắn của bất kỳ ai trong bất kỳ kênh nào để bot trả lời tin nhắn đó"""
    await interaction.response.defer(ephemeral=True)
    try:
        author_name = message.author.display_name
        target_channel = message.channel

        # 1. Kiểm tra nếu tin nhắn có đính kèm ảnh (ảnh lỗi, màn hình game...)
        image_attachment = None
        for att in message.attachments:
            if att.content_type and att.content_type.startswith("image/"):
                image_attachment = att
                break
            elif any(att.filename.lower().endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".webp"]):
                image_attachment = att
                break

        if image_attachment:
            img_bytes = await image_attachment.read()
            content_type = image_attachment.content_type or "image/png"
            reply_text = await ai.analyze_image(
                image_bytes=img_bytes,
                content_type=content_type,
                user_prompt=message.content,
                user_name=author_name
            )
        else:
            prompt = message.content.strip()
            if not prompt:
                await interaction.followup.send("⚠️ Tin nhắn này không có nội dung văn bản hoặc hình ảnh để trả lời.", ephemeral=True)
                return
            reply_text = await ai.get_response(target_channel.id, prompt, author_name)

        chunks = split_message(reply_text)
        for i, chunk in enumerate(chunks):
            if i == 0:
                await message.reply(chunk, mention_author=True)
            else:
                await target_channel.send(chunk)

        await interaction.followup.send(
            f"✅ Đã ra lệnh thành công! Bot đã trả lời tin nhắn của **{author_name}** tại kênh {target_channel.mention}!",
            ephemeral=True
        )
    except Exception as e:
        logger.error(f"Lỗi context_reply_message: {e}")
        await interaction.followup.send(f"❌ Có lỗi khi ra lệnh cho bot trả lời: {e}", ephemeral=True)

@bot.tree.command(name="tra_loi", description="Ra lệnh cho bot trả lời thắc mắc của một người ở một kênh chat cụ thể")
@app_commands.describe(
    kenh="Kênh chat muốn bot gửi câu trả lời (ví dụ: #vietnam-chat, #english-chat)",
    nguoi_hoi="Thành viên bạn muốn bot giải đáp cho họ (tag @user)",
    cau_hoi="Nội dung câu hỏi cần giải đáp (hoặc dán link tin nhắn của họ)"
)
async def slash_answer(
    interaction: discord.Interaction,
    kenh: discord.TextChannel,
    nguoi_hoi: discord.Member,
    cau_hoi: str
):
    await interaction.response.defer(ephemeral=True)
    try:
        target_message = None
        prompt_text = cau_hoi.strip()

        # Kiểm tra nếu người dùng dán link tin nhắn Discord
        link_pattern = r'discord(?:app)?\.com/channels/\d+/(\d+)/(\d+)'
        match = re.search(link_pattern, prompt_text)
        if match:
            src_ch_id, src_msg_id = int(match.group(1)), int(match.group(2))
            source_ch = bot.get_channel(src_ch_id)
            if source_ch:
                try:
                    target_message = await source_ch.fetch_message(src_msg_id)
                    prompt_text = target_message.content
                except Exception:
                    pass

        author_name = nguoi_hoi.display_name
        reply_text = await ai.get_response(kenh.id, prompt_text, author_name)
        chunks = split_message(reply_text)

        # Gửi vào kênh được chỉ định
        if target_message and target_message.channel.id == kenh.id:
            for i, chunk in enumerate(chunks):
                if i == 0:
                    await target_message.reply(f"{nguoi_hoi.mention}\n{chunk}", mention_author=True)
                else:
                    await kenh.send(chunk)
        else:
            for i, chunk in enumerate(chunks):
                if i == 0:
                    await kenh.send(f"{nguoi_hoi.mention}\n{chunk}")
                else:
                    await kenh.send(chunk)

        await interaction.followup.send(
            f"✅ Đã ra lệnh thành công! Bot đã trả lời **{author_name}** tại kênh {kenh.mention}.",
            ephemeral=True
        )
    except Exception as e:
        logger.error(f"Lỗi slash_answer: {e}")
        await interaction.followup.send(f"❌ Có lỗi khi ra lệnh cho bot: {e}", ephemeral=True)

async def start_web_server():
    port = int(os.environ.get("PORT", 8080))
    try:
        from aiohttp import web
        app = web.Application()
        async def handle_ping(request):
            return web.Response(text="🤖 Bot Tuấn Sờ Cu is running online 24/7 on Render!")
        app.router.add_get("/", handle_ping)
        app.router.add_get("/ping", handle_ping)
        app.router.add_get("/health", handle_ping)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        logger.info(f"Đã bật Web Health Check Server trên cổng {port} (Hỗ trợ Render.com 24/7)")
        return runner
    except Exception as e:
        logger.warning(f"Không thể bật web health server: {e}")
        return None

async def run_bot_with_retry():
    retry_delay = 15
    while True:
        try:
            logger.info("Đang đăng nhập vào Discord...")
            await bot.start(TOKEN)
        except discord.errors.HTTPException as e:
            try:
                await bot.close()
            except Exception:
                pass
            if e.status == 429:
                # Đọc thời gian Discord yêu cầu chờ (nếu có trong header)
                retry_after = getattr(e, "retry_after", None)
                if not retry_after and hasattr(e, "response") and hasattr(e.response, "headers"):
                    retry_after = e.response.headers.get("Retry-After")
                
                try:
                    wait_time = float(retry_after) if retry_after else 90
                except (ValueError, TypeError):
                    wait_time = 90
                
                # Nghỉ ít nhất 60s để Cloudflare Discord kịp nhả lệnh chặn IP
                wait_time = max(wait_time, 60)
                logger.warning(
                    f"⚠️ Discord báo lỗi 429 Too Many Requests: Cụm IP của Render đang bị Discord Cloudflare giới hạn tạm thời.\n"
                    f"Web server vẫn đang mở trên cổng PORT để giữ Render luôn ở trạng thái Live.\n"
                    f"Bot sẽ tự động chờ {int(wait_time)} giây trước khi thử kết nối lại..."
                )
                await asyncio.sleep(wait_time)
                continue
            else:
                logger.error(f"Lỗi HTTPException khi kết nối Discord: {e}. Thử lại sau {retry_delay}s...")
        except Exception as e:
            try:
                await bot.close()
            except Exception:
                pass
            logger.error(f"Lỗi khi chạy bot: {e}. Sẽ thử lại sau {retry_delay} giây...")
        
        await asyncio.sleep(retry_delay)
        retry_delay = min(int(retry_delay * 1.5), 120)

async def main():
    await start_web_server()
    await run_bot_with_retry()

if __name__ == "__main__":
    logger.info("Đang khởi động bot Tuấn Sờ Cu...")
    asyncio.run(main())
