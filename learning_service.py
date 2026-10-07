import os
import json
import time
import logging
import asyncio
from datetime import datetime

logger = logging.getLogger("LearningService")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
CHAT_LOGS_FILE = os.path.join(DATA_DIR, "chat_logs.jsonl")
LEARNED_PATTERNS_FILE = os.path.join(DATA_DIR, "learned_patterns.json")
TRAINING_PAIRS_FILE = os.path.join(DATA_DIR, "training_pairs.jsonl")

os.makedirs(DATA_DIR, exist_ok=True)

class LearningService:
    def __init__(self, ai_service=None):
        self.ai_service = ai_service
        self.last_messages_by_channel = {}  # channel_id -> {author, content, timestamp}
        self.new_messages_counter = 0
        self.is_distilling = False
        self.learned_data = self._load_learned_patterns()

    def _load_learned_patterns(self) -> dict:
        """Đọc kho dữ liệu phong cách đã học từ file JSON"""
        default_data = {
            "slang_and_terms": [],
            "sample_responses": [],
            "hot_topics": [],
            "total_messages_learned": 0,
            "last_updated": ""
        }
        if os.path.exists(LEARNED_PATTERNS_FILE):
            try:
                with open(LEARNED_PATTERNS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return {**default_data, **data}
            except Exception as e:
                logger.error(f"Lỗi đọc {LEARNED_PATTERNS_FILE}: {e}")
        return default_data

    def _save_learned_patterns(self):
        """Lưu lại kho dữ liệu phong cách"""
        try:
            with open(LEARNED_PATTERNS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.learned_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Lỗi lưu {LEARNED_PATTERNS_FILE}: {e}")

    def record_message(self, message) -> bool:
        """
        Thu thập và phân loại tin nhắn của người dùng trong server để làm dữ liệu training.
        Tự động tạo cặp câu hỏi - đáp (training pair) giữa các thành viên.
        """
        if message.author.bot:
            return False

        content = message.clean_content.strip()
        if not content or len(content) < 3:
            return False

        # Lọc bỏ lệnh bot hoặc link URL
        if content.startswith(("!", "/", "?", ".")) or "http://" in content or "https://" in content:
            return False

        # Lọc bỏ tin nhắn chứa key bí mật hoặc token
        if any(w in content.lower() for w in ["token", "nvapi-", "discord_token", "api_key", "password"]):
            return False

        now = time.time()
        ch_id = message.channel.id
        author_name = message.author.display_name
        timestamp_str = datetime.now().isoformat()

        # 1. Ghi tin nhắn thô vào chat_logs.jsonl
        entry = {
            "channel_id": ch_id,
            "channel_name": getattr(message.channel, "name", "unknown"),
            "author": author_name,
            "content": content,
            "created_at": timestamp_str
        }
        try:
            with open(CHAT_LOGS_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"Lỗi ghi log chat: {e}")

        # 2. Tạo cặp tương tác (Pair) giữa 2 người nhắn kế tiếp trong kênh
        last_msg = self.last_messages_by_channel.get(ch_id)
        if last_msg:
            time_diff = now - last_msg.get("time", 0)
            # Nếu 2 tin nhắn cách nhau dưới 70 giây và từ 2 người khác nhau -> cặp tương tác tự nhiên
            if time_diff < 70 and last_msg.get("author") != author_name:
                pair_entry = {
                    "instruction": f"{last_msg['author']}: {last_msg['content']}",
                    "response": f"{author_name}: {content}",
                    "channel": getattr(message.channel, "name", ""),
                    "timestamp": timestamp_str
                }
                try:
                    with open(TRAINING_PAIRS_FILE, "a", encoding="utf-8") as f:
                        f.write(json.dumps(pair_entry, ensure_ascii=False) + "\n")
                except Exception as e:
                    logger.debug(f"Lỗi ghi pair: {e}")

        # Cập nhật tin nhắn gần nhất của kênh
        self.last_messages_by_channel[ch_id] = {
            "author": author_name,
            "content": content,
            "time": now
        }

        self.new_messages_counter += 1

        # Cứ mỗi 35 tin nhắn mới -> tự động chạy AI tổng hợp phong cách mới
        if self.new_messages_counter >= 35 and not self.is_distilling:
            asyncio.create_task(self.distill_knowledge())

        return True

    async def distill_knowledge(self, sample_limit: int = 40) -> dict:
        """
        Dùng AI để tổng hợp và rút trích từ lóng, phong cách nói chuyện và chủ đề hot từ chat logs.
        Tự động nạp kiến thức mới vào bộ nhớ phong cách của Tuấn Sờ Cu.
        """
        if not self.ai_service or self.is_distilling:
            return self.learned_data

        if not os.path.exists(CHAT_LOGS_FILE):
            return self.learned_data

        self.is_distilling = True
        logger.info("Đang chạy AI Distillation để học phong cách trò chuyện mới từ server...")

        recent_lines = []
        try:
            with open(CHAT_LOGS_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
                recent_lines = lines[-sample_limit:]
        except Exception as e:
            logger.error(f"Lỗi đọc chat_logs: {e}")
            self.is_distilling = False
            return self.learned_data

        if len(recent_lines) < 10:
            self.is_distilling = False
            return self.learned_data

        chat_snippets = []
        for line in recent_lines:
            try:
                item = json.loads(line)
                chat_snippets.append(f"{item['author']}: {item['content']}")
            except Exception:
                pass

        corpus_text = "\n".join(chat_snippets)

        prompt = (
            "Dưới đây là các tin nhắn trò chuyện thực tế giữa các anh em game thủ trong server Discord:\n"
            f"--- BẮT ĐẦU ĐOẠN CHAT ---\n{corpus_text}\n--- KẾT THÚC ĐOẠN CHAT ---\n\n"
            "Hãy quan sát kỹ văn phong, ngôn từ và chắt lọc ra phong cách giao tiếp của server:\n"
            "1. Danh sách từ lóng, teencode, từ ngữ viết tắt hoặc thuật ngữ game (Once Human, v.v.) mà anh em hay dùng (tối đa 12 từ).\n"
            "2. 4 - 6 câu đối đáp tiêu biểu, chân thực, ngắn gọn và hài hước nhất của các thành viên.\n"
            "3. 2 - 3 chủ đề nóng mà mọi người đang hào hứng thảo luận.\n\n"
            "Chỉ trả về DUY NHẤT một khối JSON hợp lệ theo cấu trúc sau, không kèm bất kỳ lời giải thích nào khác:\n"
            "{\n"
            '  "slang_and_terms": ["từ 1", "từ 2", ...],\n'
            '  "sample_responses": ["câu 1", "câu 2", ...],\n'
            '  "hot_topics": ["chủ đề 1", "chủ đề 2"]\n'
            "}"
        )

        messages = [
            {"role": "system", "content": "Bạn là chuyên gia phân tích ngôn ngữ tự nhiên và hành vi cộng đồng Discord. Trả về đúng định dạng JSON thuần túy."},
            {"role": "user", "content": prompt}
        ]

        try:
            ai_reply = await self.ai_service._call_llm(messages, max_tokens=600, temperature=0.3)
            if ai_reply:
                # Trích xuất JSON từ câu trả lời của AI
                clean_json_str = ai_reply.strip()
                if "```json" in clean_json_str:
                    clean_json_str = clean_json_str.split("```json")[1].split("```")[0].strip()
                elif "```" in clean_json_str:
                    clean_json_str = clean_json_str.split("```")[1].split("```")[0].strip()

                parsed = json.loads(clean_json_str)
                new_slangs = parsed.get("slang_and_terms", [])
                new_samples = parsed.get("sample_responses", [])
                new_topics = parsed.get("hot_topics", [])

                # Hợp nhất và lọc trùng lặp
                current_slangs = set(self.learned_data.get("slang_and_terms", []))
                for s in new_slangs:
                    if isinstance(s, str) and 1 < len(s) < 30:
                        current_slangs.add(s)

                current_samples = self.learned_data.get("sample_responses", [])
                for s in new_samples:
                    if isinstance(s, str) and 2 < len(s) < 80 and s not in current_samples:
                        current_samples.append(s)

                # Giữ tối đa 30 từ lóng và 15 câu mẫu hay nhất
                self.learned_data["slang_and_terms"] = list(current_slangs)[-30:]
                self.learned_data["sample_responses"] = current_samples[-15:]
                self.learned_data["hot_topics"] = new_topics[:5]
                self.learned_data["total_messages_learned"] += len(recent_lines)
                self.learned_data["last_updated"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

                self._save_learned_patterns()
                self.new_messages_counter = 0
                logger.info(f"✅ Đã chắt lọc thành công: {len(self.learned_data['slang_and_terms'])} từ lóng, {len(self.learned_data['sample_responses'])} mẫu câu!")
        except Exception as e:
            logger.warning(f"Lỗi khi AI distill kiến thức: {e}")
        finally:
            self.is_distilling = False

        return self.learned_data

    def get_learned_prompt_injection(self) -> str:
        """
        Sinh ra đoạn prompt phong cách được học từ chính server
        để nhúng trực tiếp vào System Prompt của Tuấn Sờ Cu.
        """
        slangs = self.learned_data.get("slang_and_terms", [])
        samples = self.learned_data.get("sample_responses", [])
        topics = self.learned_data.get("hot_topics", [])

        if not slangs and not samples:
            return ""

        parts = ["\n📚 PHONG CÁCH VÀ TỪ NGỮ ĐÃ HỌC TỪ ANH EM TRONG SERVER:"]
        if slangs:
            parts.append(f"- Từ lóng và thuật ngữ anh em hay nói: {', '.join(slangs[:20])}")
        if topics:
            parts.append(f"- Các chủ đề hot server đang quan tâm: {', '.join(topics)}")
        if samples:
            parts.append("- Một số mẫu câu đối đáp chuẩn bài của anh em:")
            for s in samples[:5]:
                parts.append(f"  + {s}")
        parts.append("-> Hãy khéo léo hòa trộn các từ lóng và kiểu nói chuyện này vào lời thoại để nói chuyện tự nhiên và thân thuộc như người trong nhà!")
        return "\n".join(parts) + "\n"

    def get_stats(self) -> dict:
        """Trả về thống kê dữ liệu đã thu thập và học tập"""
        total_raw = 0
        total_pairs = 0
        if os.path.exists(CHAT_LOGS_FILE):
            try:
                with open(CHAT_LOGS_FILE, "r", encoding="utf-8") as f:
                    total_raw = sum(1 for _ in f)
            except Exception:
                pass
        if os.path.exists(TRAINING_PAIRS_FILE):
            try:
                with open(TRAINING_PAIRS_FILE, "r", encoding="utf-8") as f:
                    total_pairs = sum(1 for _ in f)
            except Exception:
                pass

        return {
            "total_raw_messages": total_raw,
            "total_training_pairs": total_pairs,
            "slang_count": len(self.learned_data.get("slang_and_terms", [])),
            "sample_count": len(self.learned_data.get("sample_responses", [])),
            "hot_topics": self.learned_data.get("hot_topics", []),
            "last_updated": self.learned_data.get("last_updated", "Chưa cập nhật")
        }
