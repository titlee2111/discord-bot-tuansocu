import os
import aiohttp
import logging
import base64
from collections import defaultdict

logger = logging.getLogger(__name__)

class AIService:
    def __init__(self, base_system_prompt: str = None, knowledge_file: str = "kien_thuc.txt"):
        self.base_prompt = base_system_prompt or (
            "Bạn tên là Tuấn Sờ Cu, một bot Discord thông minh, am hiểu công nghệ/game, vui tính và nhiệt tình của server WoR Helper Tools. "
            "Bạn hỗ trợ song ngữ (Bilingual: Vietnamese & English):\n"
            "- Khi người dùng hỏi bằng tiếng Việt, trả lời bằng tiếng Việt tự nhiên, dí dỏm, hữu ích và chuẩn xác.\n"
            "- Khi người dùng hỏi bằng tiếng Anh (hoặc ngôn ngữ khác), hãy trả lời hoàn toàn bằng tiếng Anh chuẩn, chuyên nghiệp, nhiệt tình và rõ ràng.\n"
            "Trả lời ngắn gọn, súc tích, định dạng gạch đầu dòng rõ ràng để vừa vặn khung chat Discord."
        )
        self.knowledge_file = knowledge_file
        self.knowledge_text = ""
        self.load_knowledge()

        # NVIDIA API Key cho Vision Model
        self.nvidia_api_key = os.getenv("NVIDIA_API_KEY")

        # Lưu lịch sử chat: channel_id -> list of message dicts
        self.conversations = defaultdict(list)
        self.max_history = 8

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
                "\n\n[KHO KIẾN THỨC BẮT BUỘC ĐỂ HỖ TRỢ VÀ GIẢI ĐÁP NGƯỜI DÙNG / KNOWLEDGE BASE]:\n"
                f"{self.knowledge_text}\n"
                "Hãy sử dụng kho kiến thức chi tiết ở trên để giải đáp mọi thắc mắc của người dùng (về lỗi tính năng, nguyên nhân, cách khắc phục, giải thích lệnh CMD/Batch script, bằng cả Tiếng Việt hoặc English tương ứng với ngôn ngữ của người hỏi)."
            )
        return prompt

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
        current_user_msg = {"role": "user", "content": f"{user_name}: {user_message}"}
        messages.append(current_user_msg)

        payload = {
            "messages": messages,
            "model": "openai",
            "jsonMode": False
        }

        url = "https://text.pollinations.ai/"

        try:
            timeout = aiohttp.ClientTimeout(total=45)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status == 200:
                        reply = await resp.text()
                        reply = reply.strip()
                        if reply:
                            history.append({"role": "user", "content": f"{user_name}: {user_message}"})
                            history.append({"role": "assistant", "content": reply})
                            if len(history) > self.max_history * 2:
                                self.conversations[channel_id] = history[-self.max_history * 2:]
                            return reply
                    else:
                        error_text = await resp.text()
                        logger.error(f"AI API error {resp.status}: {error_text}")
                        return f"Ui da, máy chủ AI đang báo lỗi (Mã: {resp.status}). Bác thử lại xíu nha!"
        except Exception as e:
            logger.error(f"Error calling AI API: {e}")
            return "Ui lag quá, mình chưa kịp load câu trả lời. Bác thử hỏi lại xem sao nha!"

        return "Tuấn Sờ Cu đang ngơ ngác, chưa nghĩ ra câu trả lời. Hỏi lại phát nữa nào!"

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
        """Tóm tắt đoạn chat trong kênh Discord"""
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

        payload = {
            "messages": messages,
            "model": "openai",
            "jsonMode": False
        }

        url = "https://text.pollinations.ai/"

        try:
            timeout = aiohttp.ClientTimeout(total=50)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status == 200:
                        reply = await resp.text()
                        return reply.strip()
                    else:
                        return f"Lỗi khi tóm tắt (Mã lỗi API: {resp.status}). Bạn thử lại sau nhé!"
        except Exception as e:
            logger.error(f"Error summarizing: {e}")
            return "Không thể tóm tắt do lỗi kết nối mạng tới AI."
