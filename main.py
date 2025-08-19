import os
import requests
import json
import schedule
import time
import logging
import threading
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()
# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Configuration - Use environment variables for security
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
DAILY_TIME = os.getenv('DAILY_TIME', '13:38')  # Default time is 13:38
DEBUG_MODE = os.getenv('DEBUG_MODE', 'false').lower() == 'true'  # Debug mode for testing
MODEL = os.getenv('MODEL')

# Validate required environment variables
if not all([BOT_TOKEN, CHAT_ID, OPENROUTER_API_KEY]):
    logger.error("Missing required environment variables. Please set:")
    logger.error("- TELEGRAM_BOT_TOKEN")
    logger.error("- TELEGRAM_CHAT_ID") 
    logger.error("- OPENROUTER_API_KEY")
    logger.error("- DAILY_TIME (optional, defaults to 13:38)")
    logger.error("- DEBUG_MODE (optional, set to 'true' for testing)")
    exit(1)

try:
    CHAT_ID = int(CHAT_ID)
except ValueError:
    logger.error("TELEGRAM_CHAT_ID must be a valid integer")
    exit(1)

API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Bot state
user_sessions = {}  # Support multiple users
last_update_id = None
used_words = set()  # เก็บคำที่ใช้ไปแล้ว
word_history = []   # เก็บประวัติคำที่ใช้พร้อมวันที่

class VocabularyBot:
    def __init__(self):
        self.session = requests.Session()
        # Set reasonable timeouts
        self.session.timeout = (10, 30)  # (connect, read) timeout
        
        # โหลดประวัติคำที่ใช้แล้ว
        self.load_word_history()
        
    def send_message(self, chat_id, text, parse_mode='Markdown'):
        """Send message to Telegram chat with error handling"""
        # Validate that text is not empty
        if not text or not text.strip():
            logger.error("Cannot send empty message")
            return False
            
        url = f"{API_URL}/sendMessage"
        payload = {
            'chat_id': chat_id,
            'text': text,
            'parse_mode': parse_mode
        }
        
        try:
            response = self.session.post(url, data=payload)
            response.raise_for_status()
            logger.info(f"Message sent successfully to chat {chat_id}")
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to send message: {e}")
            return False

    def get_updates(self, offset=None):
        """Get updates from Telegram with error handling"""
        url = f"{API_URL}/getUpdates"
        params = {'timeout': 100, 'offset': offset}
        
        try:
            response = self.session.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to get updates: {e}")
            return None

    def extract_words_from_response(self, response_text):
        """แยกคำศัพท์จาก response ของ AI"""
        import re
        
        # หาคำภาษาอังกฤษที่อยู่ในรูปแบบ bold หรือมี pattern ต่างๆ
        patterns = [
            r'\*\*([A-Za-z]+)\*\*',  # **word**
            r'\*([A-Za-z]+)\*',      # *word*
            r'(\d+\.?\s*)([A-Z][a-z]+)',  # 1. Word หรือ 1 Word
            r'^([A-Z][a-z]+)',       # คำที่ขึ้นต้นด้วยพิมพ์ใหญ่ต้นบรรทัด
        ]
        
        words = set()
        for pattern in patterns:
            matches = re.findall(pattern, response_text, re.MULTILINE)
            for match in matches:
                # ถ้า match เป็น tuple (จาก group), เอาส่วนที่เป็นคำ
                word = match[-1] if isinstance(match, tuple) else match
                word = word.strip().lower()
                # เฉพาะคำที่มีความยาวเหมาะสม
                if 3 <= len(word) <= 15 and word.isalpha():
                    words.add(word)
        
        return words

    def save_word_history(self):
        """บันทึกประวัติคำลงไฟล์"""
        try:
            with open('word_history.json', 'w', encoding='utf-8') as f:
                history_data = {
                    'used_words': list(used_words),
                    'word_history': word_history
                }
                json.dump(history_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save word history: {e}")

    def load_word_history(self):
        """โหลดประวัติคำจากไฟล์"""
        global used_words, word_history
        try:
            if os.path.exists('word_history.json'):
                with open('word_history.json', 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    used_words = set(data.get('used_words', []))
                    word_history = data.get('word_history', [])
                    logger.info(f"📚 Loaded {len(used_words)} previously used words")
        except Exception as e:
            logger.error(f"Failed to load word history: {e}")
            used_words = set()
            word_history = []

    def get_vocabulary_from_openrouter(self, avoid_repetition=True, max_retries=3):
        """Get vocabulary words from OpenRouter API with repetition avoidance"""
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        }
        
        # สร้างรายการคำที่ใช้แล้ว (เฉพาะคำล่าสุด 50 คำ เพื่อไม่ให้ prompt ยาวเกินไป)
        recent_used_words = list(used_words)[-50:] if used_words else []
        avoid_words_text = ""
        
        if avoid_repetition and recent_used_words:
            avoid_words_text = f"\n\nIMPORTANT: Please avoid using these previously used words: {', '.join(recent_used_words)}"
        
        # เพิ่มคำแนะนำให้หลีกเลี่ยงคำซ้ำ
        system_prompt = """You are a helpful English vocabulary teacher. Provide exactly 5 English vocabulary words with clear, simple Thai explanations. 
        
        Format each word clearly with:
        1. The English word in bold
        2. Pronunciation guide in brackets  
        3. Thai meaning and example
        
        Choose intermediate-level words that are useful in daily life. Make sure each word is different and unique."""
        
        user_prompt = f"Give me 5 intermediate-level English vocabulary words with their meanings explained clearly in Thai. Please format them nicely with numbers.{avoid_words_text}"
        
        for attempt in range(max_retries):
            data = {
                "model": f"{MODEL}",
                "messages": [
                    {
                        "role": "system",
                        "content": system_prompt
                    },
                    {
                        "role": "user",
                        "content": user_prompt
                    }
                ],
                "max_tokens": 800,
                "temperature": 0.8 + (attempt * 0.1)  # เพิ่ม randomness ในครั้งต่อไป
            }
            
            try:
                response = self.session.post(
                    url, 
                    headers=headers, 
                    data=json.dumps(data),
                    timeout=(10, 30)
                )
                response.raise_for_status()
                
                res_json = response.json()
                
                # Validate response structure
                if 'choices' not in res_json or not res_json['choices']:
                    logger.error(f"Invalid OpenRouter response structure: {res_json}")
                    continue
                    
                if 'message' not in res_json['choices'][0] or 'content' not in res_json['choices'][0]['message']:
                    logger.error(f"Missing content in OpenRouter response: {res_json}")
                    continue
                    
                content = res_json['choices'][0]['message']['content']
                
                # Validate content is not empty
                if not content or not content.strip():
                    logger.error("OpenRouter returned empty content")
                    continue
                
                if avoid_repetition:
                    # แยกคำออกมาจาก response
                    new_words = self.extract_words_from_response(content)
                    
                    # ตรวจสอบว่ามีคำซ้ำไหม
                    repeated_words = new_words.intersection(used_words)
                    
                    if repeated_words and attempt < max_retries - 1:
                        logger.warning(f"🔄 Attempt {attempt + 1}: Found repeated words {repeated_words}, retrying...")
                        continue
                    
                    # บันทึกคำใหม่
                    for word in new_words:
                        used_words.add(word)
                        word_history.append({
                            'word': word,
                            'date': datetime.now().isoformat(),
                            'attempt': attempt + 1
                        })
                    
                    # บันทึกลงไฟล์
                    self.save_word_history()
                    
                    logger.info(f"✅ Generated {len(new_words)} new vocabulary words (Total used: {len(used_words)})")
                    if repeated_words:
                        logger.info(f"⚠️  Some repeated words were included: {repeated_words}")
                
                return content.strip()
                
            except requests.exceptions.RequestException as e:
                logger.error(f"Network error calling OpenRouter (attempt {attempt + 1}): {e}")
                if attempt == max_retries - 1:
                    return None
                time.sleep(2)  # รอ 2 วินาทีก่อนลองใหม่
            except (KeyError, IndexError, json.JSONDecodeError) as e:
                logger.error(f"Error parsing OpenRouter response (attempt {attempt + 1}): {e}")
                if attempt == max_retries - 1:
                    return None
        
        return None

    def handle_user_message(self, chat_id, text):
        global used_words, word_history

        """Handle incoming user messages"""
        user_id = str(chat_id)
        
        # Initialize user session if doesn't exist
        if user_id not in user_sessions:
            user_sessions[user_id] = {
                'ready': False,
                'reminder_sent': False,
                'last_interaction': datetime.now(),
                'session_active': False
            }
        
        session = user_sessions[user_id]
        text_lower = text.strip().lower()
        
        logger.info(f"Processing message from {chat_id}: '{text_lower}'")
        
        if not session['ready']:
            if text_lower in ['พร้อม', 'ready', 'yes']:
                logger.info(f"✅ User {chat_id} is ready for vocabulary")
                session['ready'] = True
                session['session_active'] = True
                session['last_interaction'] = datetime.now()
                
                self.send_message(chat_id, "เยี่ยมมาก! 🎉 เดี๋ยวผมจะส่งคำศัพท์ให้คุณครับ...")
                
                # Get and send vocabulary
                words = self.get_vocabulary_from_openrouter()
                if words and words.strip():
                    formatted_message = f"📚 *คำศัพท์วันนี้*\n\n{words}\n\n💡 *ทำการบ้าน:* ลองเขียนประโยคด้วยคำเหล่านี้ดูนะครับ!\n\n🤖 พิมพ์ 'help' เพื่อดูคำสั่งเพิ่มเติม"
                    self.send_message(chat_id, formatted_message)
                else:
                    self.send_message(chat_id, "ขออภัยครับ ตอนนี้ไม่สามารถดึงคำศัพท์ได้ กรุณาลองใหม่อีกครั้ง")
            else:
                if not session['reminder_sent']:
                    self.send_message(
                        chat_id, 
                        "ผมยังรอคำตอบ 'พร้อม' อยู่นะครับ 😊\n\nเมื่อคุณพร้อมฝึกคำศัพท์แล้ว ให้พิมพ์ 'พร้อม' มาได้เลย"
                    )
                    session['reminder_sent'] = True
        else:
            # User is ready, handle additional interactions
            session['last_interaction'] = datetime.now()
            
            if text_lower in ['help', 'ช่วย', 'คำสั่ง']:
                logger.info(f"📋 Sending help to user {chat_id}")
                help_text = """🤖 *คำสั่งที่ใช้ได้:*

• `พร้อม` - เริ่มเรียนคำศัพท์
• `help` - แสดงคำสั่งนี้  
• `reset` - เริ่มใหม่
• `new` - ขอคำศัพท์ใหม่
• `stats` - ดูสถิติคำที่เรียนแล้ว
• `clear` - ลบประวัติคำทั้งหมด

📝 *วิธีใช้:* รอข้อความเตือน แล้วตอบ 'พร้อม' เพื่อรับคำศัพท์ประจำวัน"""
                self.send_message(chat_id, help_text)
                
            elif text_lower in ['reset', 'รีเซ็ต']:
                logger.info(f"🔄 Resetting session for user {chat_id}")
                session['ready'] = False
                session['reminder_sent'] = False
                session['session_active'] = False
                self.send_message(chat_id, "รีเซ็ตแล้ว! พิมพ์ 'พร้อม' เมื่อต้องการเริ่มใหม่")
                
            elif text_lower in ['new', 'ใหม่', 'คำใหม่']:
                logger.info(f"🆕 Sending new vocabulary to user {chat_id}")
                self.send_message(chat_id, "กำลังหาคำศัพท์ใหม่ให้... ⏳")
                words = self.get_vocabulary_from_openrouter()
                if words and words.strip():
                    formatted_message = f"📚 *คำศัพท์ใหม่*\n\n{words}\n\n💡 ลองฝึกใช้คำเหล่านี้ดูนะครับ!"
                    self.send_message(chat_id, formatted_message)
                else:
                    self.send_message(chat_id, "ขออภัยครับ ตอนนี้ไม่สามารถดึงคำศัพท์ได้")
                    
            elif text_lower in ['stats', 'สถิติ', 'ข้อมูล']:
                logger.info(f"📊 Sending statistics to user {chat_id}")
                total_words = len(used_words)
                recent_words = word_history[-5:] if word_history else []
                
                stats_text = f"📊 *สถิติการเรียนรู้*\n\n"
                stats_text += f"🔢 จำนวนคำทั้งหมด: {total_words} คำ\n\n"
                
                if recent_words:
                    stats_text += "🕐 *คำล่าสุด 5 คำ:*\n"
                    for i, item in enumerate(recent_words[::-1], 1):
                        date_str = datetime.fromisoformat(item['date']).strftime('%d/%m %H:%M')
                        stats_text += f"{i}. {item['word']} ({date_str})\n"
                else:
                    stats_text += "ยังไม่มีประวัติการเรียน\n"
                    
                self.send_message(chat_id, stats_text)
                
            elif text_lower in ['clear', 'ล้าง', 'ลบประวัติ']:
                logger.info(f"🗑️ Clearing word history for user {chat_id}")
                used_words.clear()
                word_history.clear()
                self.save_word_history()
                self.send_message(chat_id, "🗑️ ลบประวัติคำศัพท์ทั้งหมดแล้ว! ตอนนี้สามารถได้คำซ้ำได้อีกครั้ง")
                    
            else:
                # Echo back or provide encouragement
                self.send_message(chat_id, f"ได้รับข้อความแล้ว: '{text}' 👍\n\nพิมพ์ 'help' เพื่อดูคำสั่งที่ใช้ได้ครับ")

    def daily_vocabulary_job(self):
        """Daily scheduled job to send vocabulary prompt"""
        logger.info("🚀 Starting daily vocabulary job")
        
        # Reset all user sessions for new day  
        for session in user_sessions.values():
            if not session.get('session_active', False):  # Only reset if not in active session
                session['ready'] = False
                session['reminder_sent'] = False
        
        # Send initial prompt
        success = self.send_message(
            CHAT_ID, 
            "🌅 *สวัสดีครับ!*\n\nวันนี้คุณพร้อมฝึกคำศัพท์ภาษาอังกฤษหรือยัง? \n\n✨ ถ้าพร้อมแล้ว กรุณาพิมพ์ '*พร้อม*' ครับ"
        )
        
        if not success:
            logger.error("❌ Failed to send daily vocabulary prompt")
        else:
            logger.info("📤 Daily vocabulary prompt sent successfully")

    def start_continuous_listener(self):
        """Start continuous message listener (runs in background)"""
        global last_update_id
        
        logger.info("🎧 Starting continuous message listener...")
        
        while True:
            try:
                updates = self.get_updates(last_update_id)
                if updates and updates.get('result'):
                    for item in updates['result']:
                        last_update_id = item['update_id'] + 1
                        message = item.get('message')
                        
                        if not message:
                            continue
                            
                        text = message.get('text', '')
                        chat_id = message['chat']['id']
                        
                        logger.info(f"📨 Received message from {chat_id}: {text}")
                        
                        # Process the message
                        self.handle_user_message(chat_id, text)
                
                time.sleep(2)  # Poll every 2 seconds
                
            except KeyboardInterrupt:
                logger.info("Listener stopped by user")
                break
            except Exception as e:
                logger.error(f"Error in listener: {e}")
                time.sleep(5)

def main():
    """Main function to run the bot"""
    logger.info("Starting Vocabulary Bot...")
    
    bot = VocabularyBot()
    
    # Test bot immediately
    # logger.info("🧪 Testing bot functionality...")
    # try:
    #     bot.send_message(CHAT_ID, "🤖 Bot เริ่มทำงานแล้ว! พิมพ์ 'help' เพื่อดูคำสั่ง")
    #     logger.info("✅ Bot communication test successful")
    # except Exception as e:
    #     logger.error(f"❌ Bot test failed: {e}")
    #     return
    
    # Start continuous message listener in background
    import threading
    listener_thread = threading.Thread(target=bot.start_continuous_listener, daemon=True)
    listener_thread.start()
    logger.info("🎧 Message listener started in background")
    
    # For testing - run every 2 minutes
    # logger.info("🧪 TESTING MODE: Running every 2 minutes")
    # schedule.every(2).minutes.do(bot.daily_vocabulary_job)
    schedule.every().day.at(DAILY_TIME).do(bot.daily_vocabulary_job)
    
    logger.info("Bot started successfully, waiting for scheduled time...")
    # logger.info("Next run in 2 minutes...")
    
    # Show next scheduled run time
    jobs = schedule.get_jobs()
    if jobs:
        next_run = jobs[0].next_run
        logger.info(f"⏰ Next scheduled run: {next_run}")
    
    try:
        check_count = 0
        while True:
            # Show periodic status
            check_count += 1
            if check_count % 6 == 0:  # Every 3 minutes (6 * 30 seconds)
                remaining_jobs = schedule.get_jobs()
                if remaining_jobs:
                    next_run = remaining_jobs[0].next_run
                    now = datetime.now()
                    time_until = next_run - now
                    logger.info(f"⏳ Time until next run: {time_until}")
                
            schedule.run_pending()
            time.sleep(30)  # Check every 30 seconds
            
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")

if __name__ == '__main__':
    main()