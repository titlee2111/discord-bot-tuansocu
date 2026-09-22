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
UPDATE_CHANNEL_ID = int(os.getenv("UPDATE_CHANNEL_ID", "1548984360722501662"))
GITHUB_REPO = os.getenv("GITHUB_REPO", "titlee2111/war-of-genesis-helper")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
BOT_OWNER_ID = int(os.getenv("BOT_OWNER_ID", "756393451527995453"))

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
STATE_FILE = os.path.join(os.path.dirname(__file__), "github_state.json")

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
        "tại sao", "sao lại", "làm sao", "làm thế nào", "như thế nào",
        "sao k", "sao không", "sao ko", "sao chưa", "sao bị",
        "cho hỏi", "cho mình hỏi", "cho em hỏi", "ai biết", "có ai biết",
        "chỉ mình", "chỉ em", "hướng dẫn", "giúp mình", "giúp em", "cứu",
        "bị lỗi", "lỗi gì", "lỗi này", "k được", "không được", "ko đc", "k đc",
        "bị kẹt", "bị đơ", "văng game", "crash", "mất mạng", "dis mạng",
        "ghép đồ", "ghép ngọc", "livesync", "watchdog", "cmd", "script",
        "kho đầy", "cất kho", "xung đột", "không nhận"
    ]
    for kw in vi_keywords:
        if kw in text:
            return True

    # Dấu hiệu 3: Các từ khóa hỏi đáp / báo lỗi Tiếng Anh (English)
    en_keywords = [
        "why", "how to", "how do", "how can", "what is", "where is",
        "not working", "cant", "can't", "cannot", "issue", "error", "bug",
        "failing", "failed", "crash", "disconnect", "please help", "anyone know",
        "auto fuse", "live sync", "livesync", "watchdog", "cmd"
    ]
    for kw in en_keywords:
        if re.search(r'\b' + re.escape(kw) + r'\b', text):
            return True

    return False

# ==================== GITHUB AUTO-UPDATE MONITOR ====================

def load_github_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def clean_commit_message(raw_msg: str) -> str:
    """Lọc bỏ các dòng generic như 'Add files via upload', lấy nội dung chi tiết thực tế"""
    if not raw_msg:
        return "Cập nhật và tối ưu hóa hệ thống Web Helper."
    
    lines = [line.strip() for line in raw_msg.splitlines()]
    filtered = []
    for line in lines:
        if line.lower() in ["add files via upload", "upload files", "update"]:
            continue
        if line:
            filtered.append(line)
    
    if filtered:
        return "\n".join(filtered)
    return "Cập nhật và tối ưu hóa hệ thống Web Helper."

async def is_commit_already_posted(channel: discord.TextChannel, sha_short: str) -> bool:
    """Kiểm tra xem commit SHA này đã từng được bot thông báo trong kênh chưa"""
    try:
        async for msg in channel.history(limit=15):
            if msg.author == bot.user and msg.embeds:
                for emb in msg.embeds:
                    if sha_short in (emb.description or "") or sha_short in str(emb.to_dict()):
                        return True
    except Exception as e:
        logger.warning(f"Lỗi kiểm tra lịch sử kênh: {e}")
    return False

def save_github_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.error(f"Không thể lưu github_state.json: {e}")

@tasks.loop(seconds=120)
async def check_github_updates():
    """Kiểm tra commit mới trên GitHub repository và gửi thông báo tự động (mỗi 2 phút)"""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/commits?per_page=1"
    headers = {"User-Agent": "WoR-Helper-Bot"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=15) as resp:
                if resp.status == 403:
                    logger.warning("GitHub API bị giới hạn tần suất (403 Rate Limit). Sẽ kiểm tra lại ở chu kỳ tiếp theo.")
                    return
                elif resp.status != 200:
                    logger.warning(f"GitHub API trả về mã lỗi: {resp.status}")
                    return
                data = await resp.json()
                if not data or not isinstance(data, list):
                    return

                latest_commit = data[0]
                sha = latest_commit.get("sha", "")
                commit_msg = latest_commit.get("commit", {}).get("message", "Cập nhật mới")
                author = latest_commit.get("commit", {}).get("author", {}).get("name", "titlee2111")
                date_str = latest_commit.get("commit", {}).get("author", {}).get("date", "")
                commit_url = latest_commit.get("html_url", f"https://github.com/{GITHUB_REPO}")

                state = load_github_state()
                last_sha = state.get("last_sha")
                channel = bot.get_channel(UPDATE_CHANNEL_ID)

                # Kiểm tra xem commit này đã được thông báo trong kênh chưa
                already_posted = False
                if channel:
                    already_posted = await is_commit_already_posted(channel, sha[:7])

                should_announce = False
                if sha != last_sha and not already_posted:
                    should_announce = True

                if should_announce:
                    # Phát hiện commit mới cần đăng!
                    logger.info(f"Phát hiện bản cập nhật mới trên GitHub: {sha[:7]}")
                    state["last_sha"] = sha
                    state["last_date"] = date_str
                    save_github_state(state)

                    if channel:
                        cleaned_msg = clean_commit_message(commit_msg)
                        embed = discord.Embed(
                            title="🚀 THÔNG BÁO CẬP NHẬT WEB MỚI TRÊN GITHUB!",
                            description=(
                                f"Web **WoR Helper Tools** vừa được tác giả cập nhật phiên bản mới!\n\n"
                                f"**📝 Nội dung cập nhật:**\n{cleaned_msg}\n\n"
                                f"**🔗 Chi tiết commit:** [`{sha[:7]}`]({commit_url})\n"
                                f"**👤 Người cập nhật:** `{author}`\n"
                                f"**🌐 Trang Web:** https://titlee2111.github.io/war-of-genesis-helper/\n\n"
                                f"⚠️ **LƯU Ý QUAN TRỌNG CHO ANH EM:**\n"
                                f"Nếu bản cập nhật có liên quan đến tính năng bot/lò rèn/LiveSync, anh em hãy vào lại trang web để tải gói **`LiveSync_1Click.zip`** mới nhất và cài đặt lại vào game nhé!"
                            ),
                            color=discord.Color.green()
                        )
                        embed.set_footer(text=f"GitHub: {GITHUB_REPO} • Tự động cập nhật bởi Tuấn Sờ Cu")
                        await channel.send(content="@everyone", embed=embed)
                        logger.info(f"Đã gửi thông báo cập nhật commit {sha[:7]} kèm tag @everyone vào kênh {UPDATE_CHANNEL_ID}")
                    else:
                        logger.warning(f"Không tìm thấy kênh thông báo ID: {UPDATE_CHANNEL_ID}")
                else:
                    if sha != last_sha:
                        state["last_sha"] = sha
                        state["last_date"] = date_str
                        save_github_state(state)

    except Exception as e:
        logger.error(f"Lỗi khi kiểm tra GitHub updates: {e}")

@check_github_updates.before_loop
async def before_check_github():
    await bot.wait_until_ready()

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

    activity = discord.Activity(
        type=discord.ActivityType.listening,
        name=f"tag @{bot.user.name} hoặc hỏi đáp trong kênh"
    )
    await bot.change_presence(status=discord.Status.online, activity=activity)

    for guild in bot.guilds:
        try:
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            logger.info(f"Đã đồng bộ tức thì {len(synced)} lệnh Slash cho server '{guild.name}' (ID: {guild.id})")
        except Exception as e:
            logger.warning(f"Không thể đồng bộ lệnh cho server {guild.name}: {e}")

    # Bật tiến trình theo dõi cập nhật GitHub tự động
    if not check_github_updates.is_running():
        check_github_updates.start()
        logger.info(f"Đã khởi động tiến trình theo dõi GitHub ({GITHUB_REPO}) -> Kênh ID: {UPDATE_CHANNEL_ID}")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    content_clean = message.content.strip()
    content_lower = content_clean.lower()

    # KHÔNG BAO GIỜ TỰ ĐỘNG REP KHI CHỦ BOT / ADMIN NHẮN TIN TRONG SERVER
    if message.author.id in IGNORED_USER_IDS:
        # Nếu chủ bot chủ động gõ lệnh có tiền tố (!chat, !summary, !ping...) thì vẫn thực thi
        if content_clean.startswith(PREFIX):
            await bot.process_commands(message)
        return

    # Bỏ qua nếu tin nhắn bắt đầu bằng prefix lệnh
    if content_clean.startswith(PREFIX):
        await bot.process_commands(message)
        return

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
                embed.set_footer(text=f"Kênh: #{message.channel.name} • Tóm tắt bởi Tuấn Sờ Cu AI")
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
    is_mentioned = bot.user.mentioned_in(message) and not message.mention_everyone
    is_dm = isinstance(message.channel, discord.DMChannel)
    is_inquiry = is_user_inquiry(content_clean)

    if is_mentioned or is_dm or is_inquiry:
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
                    embed.set_footer(text=f"Kênh: #{message.channel.name} • Tóm tắt bởi Tuấn Sờ Cu AI")
                    await message.reply(embed=embed, mention_author=False)
                else:
                    await message.reply(count, mention_author=False)
            return

        if not clean_content:
            clean_content = "Chào bạn! Mình có thể giúp gì cho bạn?"

        async with message.channel.typing():
            author_name = message.author.display_name
            reply_text = await ai.get_response(message.channel.id, clean_content, author_name)
            chunks = split_message(reply_text)
            for i, chunk in enumerate(chunks):
                if i == 0:
                    await message.reply(chunk, mention_author=False)
                else:
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
            embed.set_footer(text=f"Kênh: #{ctx.channel.name} • Tóm tắt bởi Tuấn Sờ Cu AI")
            await ctx.reply(embed=embed)
        else:
            await ctx.reply(count)

@bot.command(name="checkupdate", aliases=["updatecheck"])
async def check_update_cmd(ctx: commands.Context):
    """Kiểm tra cập nhật GitHub thủ công: !checkupdate"""
    await ctx.reply("🔍 Đang kiểm tra cập nhật mới nhất từ GitHub...")
    await check_github_updates()
    await ctx.reply("✅ Đã kiểm tra xong trạng thái cập nhật trên GitHub!")

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

@bot.command(name="help", aliases=["trogiup", "huongdan"])
async def help_cmd(ctx: commands.Context):
    """Hiển thị menu trợ giúp: !help"""
    embed = discord.Embed(
        title="🤖 Menu Hướng Dẫn Bot Tuấn Sờ Cu",
        description="Chào mừng bạn đến với **Tuấn Sờ Cu#3019**! Dưới đây là các tính năng chính:",
        color=discord.Color.blue()
    )
    embed.add_field(
        name="💬 1. Tự Động Trả Lời & Đọc Ảnh",
        value=(
            "• **Tự động hỗ trợ trong mọi kênh:** Bất kỳ ai hỏi đáp, thắc mắc hoặc báo lỗi, bot sẽ tự động nhận diện và giải đáp ngay mà không cần tag!\n"
            "• **Đọc và phân tích hình ảnh (Vision AI):** Gửi ảnh chụp màn hình game, lỗi CMD, giao diện vào kênh chat, bot sẽ đọc chữ trên ảnh và hướng dẫn cách sửa chi tiết!"
        ),
        inline=False
    )
    embed.add_field(
        name="🚀 2. Tự Động Báo Cập Nhật Web GitHub",
        value=(
            f"• Bot tự động theo dõi GitHub repository `{GITHUB_REPO}` mỗi 60 giây.\n"
            f"• Khi có commit cập nhật mới, bot sẽ tự động đăng thông báo chi tiết vào kênh <#{UPDATE_CHANNEL_ID}>."
        ),
        inline=False
    )
    embed.add_field(
        name="📋 3. Tóm Tắt Tin Nhắn Trong Kênh",
        value=(
            f"• Gõ đơn giản: `summary` hoặc `tóm tắt`\n"
            f"• Gõ lệnh: `{PREFIX}summary [số_tin]` (Ví dụ: `{PREFIX}summary 30`)\n"
            f"• Dùng Slash command: `/summary [so_luong]`"
        ),
        inline=False
    )
    embed.set_footer(text="Tuấn Sờ Cu AI • Tự động hỗ trợ 24/7")
    await ctx.reply(embed=embed)

@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.CommandNotFound):
        return
    logger.error(f"Lỗi lệnh {ctx.command}: {error}")
    await ctx.reply(f"⚠️ **Đã xảy ra lỗi:** {error}")

# ==================== CÁC LỆNH SLASH (/) ====================

@bot.tree.command(name="chat", description="Trò chuyện hoặc đặt câu hỏi với bot Tuấn Sờ Cu")
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
        res, count = await execute_summary(interaction.channel, so_luong)
        if res:
            embed = discord.Embed(
                title=f"📋 Bảng Tóm Tắt {count} Tin Nhắn Gần Nhất",
                description=res,
                color=discord.Color.gold()
            )
            embed.set_footer(text=f"Kênh: #{interaction.channel.name} • Tóm tắt bởi Tuấn Sờ Cu AI")
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send(count)
    except Exception as e:
        logger.error(f"Lỗi slash_summary: {e}")
        await interaction.followup.send(f"Không thể tóm tắt: {e}")

@bot.tree.command(name="checkupdate", description="Kiểm tra xem GitHub web đã có bản cập nhật mới chưa")
async def slash_checkupdate(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await check_github_updates()
    await interaction.followup.send("✅ Đã kiểm tra xong trạng thái cập nhật trên GitHub!")

@bot.tree.command(name="reload", description="Cập nhật lại kiến thức mới nhất từ file kien_thuc.txt")
async def slash_reload(interaction: discord.Interaction):
    result = ai.load_knowledge()
    await interaction.response.send_message(f"🔄 **Cập nhật:** {result}", ephemeral=True)

@bot.tree.command(name="reset", description="Xóa lịch sử nhớ của bot trong kênh này")
async def slash_reset(interaction: discord.Interaction):
    ai.reset_history(interaction.channel_id)
    await interaction.response.send_message("🧹 Đã làm mới ký ức cuộc trò chuyện trong kênh này!")

@bot.tree.command(name="ping", description="Kiểm tra độ trễ mạng của bot")
async def slash_ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    await interaction.response.send_message(f"🏓 Pong! Độ trễ: **{latency}ms**.")

@bot.tree.command(name="help", description="Xem hướng dẫn sử dụng bot Tuấn Sờ Cu")
async def slash_help(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🤖 Menu Hướng Dẫn Bot Tuấn Sờ Cu",
        description="Chào bạn! Dưới đây là các tính năng chính của bot:",
        color=discord.Color.blue()
    )
    embed.add_field(
        name="💬 1. Hỗ Trợ & Trả Lời Thành Viên",
        value=(
            "• **Tự động hỗ trợ trong mọi kênh:** Nhận diện câu hỏi/báo lỗi và giải đáp cho thành viên.\n"
            "• **Đọc ảnh (Vision AI):** Phân tích ảnh chụp màn hình game, lỗi CMD...\n"
            "• **Ra lệnh trả lời 1 người ở kênh bất kỳ:**\n"
            "  - Cách 1: Chuột phải vào tin nhắn ➔ **Apps** ➔ **Tuấn Trả Lời Tin Này**.\n"
            "  - Cách 2: Dùng lệnh `/tra_loi kenh:#kênh nguoi_hoi:@user cau_hoi:...`"
        ),
        inline=False
    )
    embed.add_field(
        name="🚀 2. Tiện Ích Khác",
        value=(
            "• Tự động đăng tin cập nhật GitHub vào kênh update-status.\n"
            "• `/summary <số lượng>` hoặc gõ `summary` để tóm tắt tin nhắn."
        ),
        inline=False
    )
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
            if e.status == 429:
                logger.warning(
                    f"⚠️ Discord báo lỗi 429 Too Many Requests: Cụm IP của Render đang bị Discord Cloudflare giới hạn tạm thời.\n"
                    f"Web server vẫn đang mở trên cổng PORT để giữ Render luôn ở trạng thái Live.\n"
                    f"Bot sẽ tự động thử kết nối lại sau {retry_delay} giây..."
                )
            else:
                logger.error(f"Lỗi HTTPException khi kết nối Discord: {e}. Thử lại sau {retry_delay}s...")
        except Exception as e:
            logger.error(f"Lỗi khi chạy bot: {e}. Sẽ thử lại sau {retry_delay} giây...")
        
        await asyncio.sleep(retry_delay)
        retry_delay = min(int(retry_delay * 1.5), 120)

async def main():
    await start_web_server()
    await run_bot_with_retry()

if __name__ == "__main__":
    logger.info("Đang khởi động bot Tuấn Sờ Cu...")
    asyncio.run(main())
