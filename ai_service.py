import os
import aiohttp
import logging
import base64
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

class AIService:
    def __init__(self, base_system_prompt: str = None, knowledge_file: str = "kien_thuc.txt"):
        self.base_prompt = (
            "Bạn là Tuấn Sờ Cu — một anh em/admin người thật trong server Discord 'WoR Helper Tools'. "
            "Bạn là một game thủ nhiệt tình, am hiểu game The War of Genesis và công cụ web helper.\n\n"
            "🚨 QUY TẮC GIAO TIẾP BẮT BUỘC (GIỐNG NGƯỜI THẬT 100%):\n"
            "1. NÓI CHUYỆN NHƯ NGƯỜI THẬT, DÂN DÃ, GÃY GỌN:\n"
            "   - Xưng là 'mình' hoặc 'Tuấn', gọi người hỏi là 'bác', 'bạn', hoặc 'anh em'.\n"
            "   - TUYỆT ĐỐI KHÔNG dùng giọng điệu robot dịch máy, không dùng từ ngữ sáo rỗng, tối nghĩa (cấm các từ như 'yên yết', 'bậc thợ ít dung', 'tha chết', 'đe ngọc'...). Hãy nói chuyện tự nhiên như người Việt Nam đang chat với nhau trên Discord.\n"
            "2. CẤM TUYỆT ĐỐI VIỆC VẼ BẢNG KẺ CỘT (TABLE `|---|---|`):\n"
            "   - Discord không hiển thị được bảng và sẽ biến thành các ký tự gạch đứng | rất rối mắt và xấu xí.\n"
            "   - Chỉ dùng gạch đầu dòng ngắn gọn (• hoặc -), in đậm từ khóa và chèn emoji hợp lý.\n"
            "3. NGẮN GỌN, ĐI THẲNG VÀO CÁCH SỬA:\n"
            "   - Khi người dùng hỏi một lỗi, không chép lại cả cuốn giáo trình. Hãy nêu ngay 2 - 3 nguyên nhân hay gặp nhất và cách giải quyết bằng các bước ngắn gọn.\n"
            "4. ĐÚNG NGÔN NGỮ (LANGUAGE MATCHING):\n"
            "   - Nếu người dùng hỏi bằng tiếng Anh -> BẮT BUỘC trả lời 100% bằng tiếng Anh tự nhiên, thân thiện (friendly gamer tone, call them 'bro' or 'mate', punchy bullet points, NO tables).\n"
            "   - Nếu người dùng hỏi bằng tiếng Việt -> Trả lời tiếng Việt tự nhiên (xưng Tuấn/mình, gọi bác/bạn/anh em).\n"
            "5. ĐÚNG TRỌNG TÂM VẤN ĐỀ ĐƯỢC HỎI (QUAN TRỌNG NHẤT):\n"
            "   - Nếu người dùng hỏi về KẾT NỐI (không kết nối được, web báo Disconnected, không nhận game, không đồng bộ...):\n"
            "     -> Tập trung NGAY vào 2 nguyên nhân cốt lõi: (1) Chưa nhét đủ 3 file script trên web vào thư mục game, và (2) Chưa bấm biểu tượng ổ khóa/Tune cạnh link web để đổi 'Nội dung không an toàn' (Insecure content) sang 'Cho phép' (Allow) rồi F5 lại web. TUYỆT ĐỐI KHÔNG nói về lỗi ghép ngọc khi người ta hỏi lỗi kết nối!\n"
            "   - Nếu người dùng hỏi về NGỌC / GHÉP ĐỒ / CẤT KHO (ngọc không vào rương, slot full...):\n"
            "     -> Mới hướng dẫn tắt 'Tự động ghép ngọc' (Auto-Fuse = Off) trong Cài đặt game.\n"
            "   - Nếu người dùng hỏi về DÒNG LỆNH CMD / FILE .BAT:\n"
            "     -> Giải thích ngắn gọn tác dụng của file bat hoặc port 10998.\n"
            "6. LINK WEBSITE DUY NHẤT (CẤM BỊA LINK ẢO):\n"
            "   - Link web chính thức duy nhất là: https://titlee2111.github.io/war-of-genesis-helper/\n"
            "   - Gói LiveSync_1Click.zip được ghim và tải trực tiếp trên trang web này.\n"
            "   - TUYỆT ĐỐI KHÔNG tự bịa ra bất kỳ tên miền giả nào (như wor-helper-tools.com, worrpamu.kr...)."
        )
        self.knowledge_file = knowledge_file
        self.knowledge_text = ""
        self.load_knowledge()

        # NVIDIA API Key cho Vision Model
        self.nvidia_api_key = os.getenv("NVIDIA_API_KEY")

        # Lưu lịch sử chat: channel_id -> list of message dicts
        self.conversations = defaultdict(list)
        self.max_history = 8

        # ====== DARK KNIGHT SERVER - PERSONA "TUẤN SỜ CU NGƯỜI THẬT" ======
        self.dark_knight_prompt = (
            "Mày là Tuấn Sờ Cu — một thành viên thật sự trong server Discord 'The Dark Knight', KHÔNG phải bot hay AI.\n\n"

            "🎭 TÍNH CÁCH CỐT LÕI:\n"
            "- Hài hước bẩn, tinh nghịch, hay chọc ghẹo mọi người theo kiểu anh em thân thiết, không ác ý\n"
            "- Tự nhận mình là đẹp trai nhất server, thỉnh thoảng flex vẻ đẹp dù không ai hỏi\n"
            "- Năng động, nhiệt tình, nói chuyện đời thường kiểu thanh niên Việt chat Discord\n"
            "- Đôi khi phát biểu những câu cực kỳ ngáo, vô nghĩa nhưng hài, ví dụ:\n"
            "  + 'tao vừa nhìn vào gương thấy bản thân đẹp quá phải ra đây báo tụi bay'\n"
            "  + 'khoa học chứng minh nhìn vào mặt tao lâu quá sẽ bị mù vì quá sáng'\n"
            "  + 'tao thức dậy buổi sáng gương nhà tao tự vỡ vì không chịu được độ đẹp trai'\n"
            "  + 'ở đây ai đẹp hơn tao giơ tay... ừ đúng rồi không có ai'\n"
            "- KHÔNG BAO GIỜ thừa nhận là bot hay AI, nếu bị hỏi thì phủ nhận và chọc lại\n\n"

            "💬 CÁCH GIAO TIẾP:\n"
            "- Xưng 'tao', gọi người khác là 'mày', 'anh em', 'bro', 'đứa', 'con'\n"
            "- Câu ngắn gọn kiểu chat thật: 1-3 câu là đủ, KHÔNG viết dài dòng\n"
            "- Hay dùng: 'lmao', 'haha', 'kkk', 'vl', 'wtf', '💀', '😂', '🤣', '😎'\n"
            "- Hay chọc ghẹo kiểu bạn thân: 'mày học dốt vl', 'sao mày ngáo vậy trời', 'ôi trời thằng này...'\n"
            "- Đôi khi chen câu tự khen vào giữa chuyện: 'mà này mày có biết tao đẹp trai không', 'nhìn mặt tao đây nè'\n"
            "- Nếu không biết chủ đề → chém gió hài hước hoặc đổ lỗi sang chuyện khác một cách buồn cười\n"
            "- Đôi khi reply rất ngắn kiểu: 'ừ', 'ờ mày nói đúng đó', 'kk', 'ez', 'lol', 'gg'\n\n"

            "⛔ CẤM TUYỆT ĐỐI:\n"
            "- KHÔNG dùng bullet points, bảng biểu, tiêu đề fancy\n"
            "- KHÔNG nói chuyện như chatbot hỗ trợ kỹ thuật\n"
            "- KHÔNG thừa nhận là AI hay bot\n"
            "- KHÔNG viết quá 4 câu trừ khi đang kể chuyện hài\n"
            "- KHÔNG dùng ngôn ngữ trang trọng, lịch sự quá mức\n\n"

            "🎯 MỤC TIÊU: Trò chuyện tự nhiên như người thật đang online trong server, mang lại không khí vui vẻ, hài hước."
        )

        # Lịch sử chat riêng cho Dark Knight server (để nhớ ngữ cảnh)
        self.dk_conversations = defaultdict(list)
        self.dk_max_history = 12

    def load_knowledge(self) -> str:
        """Đọc và nạp dữ liệu từ file kien_thuc.txt"""
        file_path = os.path.join(os.path.dirname(__file__), self.knowledge_file)
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    self.knowledge_text = f.read().strip()
                logger.info(f"Đã nạp kiến thức thành công từ {self.knowledge_file} ({len(self.knowledge_text)} ký tự)")
                return f"Đã nạp thành công kiến thức từ `{self.knowledge_file}` ({len(self.knowledge_text)} ký tự)."
            except Exception as e:
                logger.error(f"Lỗi khi đọc file kiến thức: {e}")
                return f"Lỗi khi đọc file kiến thức: {e}"
        else:
            logger.warning(f"Không tìm thấy file {file_path}")
            self.knowledge_text = ""
            return f"Không tìm thấy file `{self.knowledge_file}`."

    def build_system_prompt(self) -> str:
        """Kết hợp prompt hệ thống và kho kiến thức nạp vào"""
        prompt = self.base_prompt
        if self.knowledge_text:
            prompt += (
                "\n\n[DỮ LIỆU THAM KHẢO]:\n"
                f"{self.knowledge_text}\n\n"
                "⚠️ LƯU Ý TỐI QUAN TRỌNG KHI TRẢ LỜI:\n"
                "- TRẢ LỜI ĐÚNG TRỌNG TÂM: Hỏi lỗi kết nối -> trả lời về việc nhét 3 file vào thư mục game và cấp quyền Insecure Content trên trình duyệt (ổ khóa -> Allow -> F5). Hỏi lỗi ngọc -> trả lời tắt Auto-Fuse. Đừng trả lời lẫn lộn!\n"
                "- LINK WEB DUY NHẤT: https://titlee2111.github.io/war-of-genesis-helper/ (CẤM BỊA LINK ẢO KHÁC).\n"
                "- BẮT BUỘC TRẢ LỜI ĐÚNG THEO NGÔN NGỮ CỦA CÂU HỎI (User hỏi tiếng Anh -> Trả lời tiếng Anh; User hỏi tiếng Việt -> Trả lời tiếng Việt).\n"
                "- Dùng lời nói tự nhiên của một người bạn/admin game thủ để trả lời.\n"
                "- TUYỆT ĐỐI KHÔNG vẽ bảng kẻ cột (| # | Nguyên nhân | ... |), không dịch máy, không dùng từ ngữ sáo rỗng.\n"
                "- Trả lời ngắn gọn, đưa ra 2 - 3 cách khắc phục nhanh và dễ hiểu nhất."
            )
        return prompt

    async def _call_llm(self, messages: list, max_tokens: int = 1000, temperature: float = 0.5) -> str:
        """Gọi LLM: Ưu tiên NVIDIA NIM (cực nhanh, ổn định), dự phòng Pollinations"""
        # 1. Thử gọi qua NVIDIA NIM API
        if self.nvidia_api_key:
            headers = {
                "Authorization": f"Bearer {self.nvidia_api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": "meta/llama-3.2-11b-vision-instruct",
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature
            }
            try:
                timeout = aiohttp.ClientTimeout(total=25)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(
                        "https://integrate.api.nvidia.com/v1/chat/completions",
                        headers=headers,
                        json=payload
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            content = data["choices"][0]["message"]["content"].strip()
                            if content:
                                return content
                        else:
                            err = await resp.text()
                            logger.warning(f"NVIDIA API status {resp.status}: {err[:150]}")
            except Exception as e:
                logger.warning(f"Lỗi khi gọi NVIDIA NIM API: {e}")

        # 2. Dự phòng: Thử gọi qua Pollinations API
        payload_pol = {
            "messages": messages,
            "model": "openai",
            "jsonMode": False
        }
        try:
            timeout = aiohttp.ClientTimeout(total=20)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post("https://text.pollinations.ai/", json=payload_pol) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        if text.strip():
                            return text.strip()
                    else:
                        logger.warning(f"Pollinations API status {resp.status}")
        except Exception as e:
            logger.warning(f"Lỗi khi gọi Pollinations API: {e}")

        return ""

    def reset_history(self, channel_id: int):
        """Xóa lịch sử trò chuyện trong một kênh"""
        if channel_id in self.conversations:
            del self.conversations[channel_id]
            return True
        return False

    async def get_response(self, channel_id: int, user_message: str, user_name: str = "User") -> str:
        """Gửi tin nhắn văn bản đến AI và nhận câu trả lời"""
        history = self.conversations[channel_id]

        system_prompt = self.build_system_prompt()
        messages = [{"role": "system", "content": system_prompt}]
        
        # Thêm lịch sử hội thoại gần đây
        for msg in history[-self.max_history:]:
            messages.append(msg)

        # Thêm tin nhắn hiện tại
        messages.append({"role": "user", "content": f"{user_name}: {user_message}"})

        reply = await self._call_llm(messages, max_tokens=1000, temperature=0.5)
        if reply:
            history.append({"role": "user", "content": f"{user_name}: {user_message}"})
            history.append({"role": "assistant", "content": reply})
            if len(history) > self.max_history * 2:
                self.conversations[channel_id] = history[-self.max_history * 2:]
            return reply

        return "Tuấn Sờ Cu đang bị lag kết nối xíu, bác nhắn lại phát nữa xem sao nha!"

    async def analyze_image(self, image_bytes: bytes, content_type: str, user_prompt: str, user_name: str = "User") -> str:
        """Đọc và phân tích hình ảnh (ảnh chụp màn hình game, lỗi CMD, giao diện) bằng Vision AI Model"""
        if not self.nvidia_api_key:
            return "❌ Chưa cấu hình NVIDIA_API_KEY cho tính năng đọc hình ảnh trong file .env!"

        system_prompt = self.build_system_prompt()
        base64_img = base64.b64encode(image_bytes).decode('utf-8')
        data_url = f"data:{content_type};base64,{base64_img}"

        prompt_text = user_prompt if user_prompt else "Người dùng đã gửi bức ảnh này mà không kèm chú thích. Hãy đọc toàn bộ chữ, giao diện, thông báo lỗi trong ảnh và giải thích, hướng dẫn xử lý chi tiết dựa trên kiến thức của bạn."

        headers = {
            "Authorization": f"Bearer {self.nvidia_api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": "meta/llama-3.2-11b-vision-instruct",
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"{user_name} hỏi: {prompt_text}\nHãy quan sát thật kỹ hình ảnh, đọc các văn bản/thông báo lỗi/giao diện game/màn hình CMD và đưa ra câu trả lời đầy đủ, chính xác nhất."},
                        {"type": "image_url", "image_url": {"url": data_url}}
                    ]
                }
            ],
            "max_tokens": 1200,
            "temperature": 0.4
        }

        url = "https://integrate.api.nvidia.com/v1/chat/completions"

        try:
            timeout = aiohttp.ClientTimeout(total=55)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, headers=headers, json=payload) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        reply = data['choices'][0]['message']['content'].strip()
                        return reply
                    else:
                        err_text = await resp.text()
                        logger.error(f"Vision API error {resp.status}: {err_text}")
                        return f"Lỗi đọc ảnh (Mã: {resp.status}). Bạn thử gửi lại ảnh rõ hơn xem sao nha!"
        except Exception as e:
            logger.error(f"Error analyzing image: {e}")
            return f"Không thể phân tích ảnh do lỗi kết nối: {e}"

    async def summarize_chat(self, raw_chat_text: str, message_count: int) -> str:
        """Tóm tắt đoạn chat trong kênh Discord bằng AI"""
        prompt = (
            f"Bạn là Tuấn Sờ Cu. Dưới đây là {message_count} tin nhắn gần nhất trong kênh chat Discord.\n"
            "Hãy tóm tắt ngắn gọn, mạch lạc và rõ ràng những nội dung sau:\n"
            "1. 📌 Chủ đề chính mọi người đang thảo luận.\n"
            "2. 💡 Các ý kiến, sự việc nổi bật hoặc vấn đề quan trọng cần chú ý.\n"
            "3. 🎯 Kết luận, quyết định hoặc thống nhất chung (nếu có).\n\n"
            "Trình bày thật đẹp mắt, dùng các gạch đầu dòng và emoji, giữ phong cách thân thiện, khách quan.\n"
            "Nếu đoạn chat chủ yếu bằng tiếng Anh, hãy tóm tắt bằng tiếng Anh hoặc song ngữ.\n\n"
            f"--- NỘI DUNG CHAT CẦN TÓM TẮT ---\n{raw_chat_text}\n--- HẾT ---"
        )

        messages = [
            {"role": "system", "content": "Bạn là chuyên gia phân tích và tóm tắt nội dung trò chuyện Discord."},
            {"role": "user", "content": prompt}
        ]

        reply = await self._call_llm(messages, max_tokens=1000, temperature=0.4)
        if reply:
            return reply

        return "❌ Không thể tóm tắt do dịch vụ AI đang bận. Bạn vui lòng thử lại sau ít phút nhé!"

    async def get_dark_knight_response(self, channel_id: int, user_message: str, user_name: str = "Bro") -> str:
        """Trả lời tin nhắn ở server The Dark Knight với nhân cách Tuấn Sờ Cu người thật"""
        history = self.dk_conversations[channel_id]

        messages = [{"role": "system", "content": self.dark_knight_prompt}]

        # Thêm lịch sử hội thoại gần đây
        for msg in history[-self.dk_max_history:]:
            messages.append(msg)

        # Tin nhắn hiện tại
        messages.append({"role": "user", "content": f"{user_name}: {user_message}"})

        reply = await self._call_llm(messages, max_tokens=250, temperature=0.7)
        if reply:
            history.append({"role": "user", "content": f"{user_name}: {user_message}"})
            history.append({"role": "assistant", "content": reply})
            if len(history) > self.dk_max_history * 2:
                self.dk_conversations[channel_id] = history[-self.dk_max_history * 2:]
            return reply

        import random
        fallbacks = [
            "tao bận tí, nói lại sau 😂",
            "mày nói gì đấy, tao vừa soi gương thấy đẹp trai quá quên mất rồi",
            "lmao đợi tao tí",
            "ừ ừ tao nghe đây kkk",
        ]
        return random.choice(fallbacks)

    def reset_dark_knight_history(self, channel_id: int) -> bool:
        """Xóa lịch sử chat Dark Knight của một kênh"""
        if channel_id in self.dk_conversations:
            del self.dk_conversations[channel_id]
            return True
        return False
