import os
import logging
import random
import asyncio
import sys
from dotenv import load_dotenv

# Telegram Libraries
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

# AI & PDF Libraries
import google.generativeai as genai
from pypdf import PdfReader

# 1. Load Environment Variables
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# 2. Configure Gemini AI
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.0-flash')

# 3. Setup Storage
KB_FOLDER = "knowledge_base"
os.makedirs(KB_FOLDER, exist_ok=True)

# In-Memory Cache
user_knowledge_base = {}  # Holds the PDF Text
user_sessions = {}        # Holds the Last Question Asked (Short-term memory)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- HELPER: RETRIEVE MEMORY ---
def get_user_context(user_id):
    if user_id in user_knowledge_base:
        return user_knowledge_base[user_id]
    
    file_path = os.path.join(KB_FOLDER, f"{user_id}.txt")
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                text = f.read()
                user_knowledge_base[user_id] = text
                return text
        except Exception as e:
            print(f"Error reading file: {e}")
    return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    has_memory = get_user_context(user_id) is not None
    status = "🧠 I remember your last PDF." if has_memory else "❌ Memory empty."

    await update.message.reply_text(
        f"🤖 **Study Architect Online**\n"
        f"Status: {status}\n\n"
        "**Commands:**\n"
        "1. 📂 **Upload PDF** (Notes OR Past Questions).\n"
        "2. 🔥 `/quiz` -> **Exam Mode**.\n"
        "3. 🎲 `/quiz random` -> **Random Mode**.\n"
        "4. 🔍 `/quiz [Topic]` -> **Topic Mode**."
    )

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    doc = update.message.document
    
    if doc.mime_type != 'application/pdf':
        await update.message.reply_text("⚠️ Strictly PDFs only.")
        return

    msg = await update.message.reply_text("⚙️ Ingesting... (I am learning your Lecturer's style)")
    
    try:
        file = await context.bot.get_file(doc.file_id)
        file_path = f"temp_{user_id}.pdf"
        await file.download_to_drive(file_path)
        
        reader = PdfReader(file_path)
        text = ""
        for page in reader.pages:
            text += page.extract_text() + "\n"
            
        user_knowledge_base[user_id] = text
        
        with open(os.path.join(KB_FOLDER, f"{user_id}.txt"), "w", encoding="utf-8") as f:
            f.write(text)

        os.remove(file_path)
        await msg.edit_text(f"✅ **Saved.** If this file contained Past Questions, I have now learned their pattern.\nType `/quiz` to test me.")
        
    except Exception as e:
        await msg.edit_text(f"❌ Failure: {str(e)}")

async def generate_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args
    
    full_text = get_user_context(user_id)
    if not full_text:
        await update.message.reply_text("⚠️ Memory empty. Upload a PDF first.")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    
    mode = "exam"
    prompt_context = full_text[:40000]

    # --- PROMPT ENGINEERING ---
    base_instruction = (
        f"Act as a strict Professor at UNILORIN (Geology Dept). "
        f"Scan the notes below. IF you see Past Questions (PQs) in the text, MIMIC that style.\n"
        f"NOTES:\n{prompt_context}\n\n"
    )

    if args and args[0].lower() == "random":
        mode = "random"
        if len(full_text) > 4000:
            start = random.randint(0, len(full_text) - 4000)
            snippet = full_text[start : start+4000]
        else:
            snippet = full_text
        prompt = f"{base_instruction} TASK: Ask ONE question based ONLY on this random snippet: {snippet}\nCONSTRAINT: Max 2 sentences."

    elif args:
        mode = "topic"
        topic = " ".join(args)
        prompt = f"{base_instruction} TASK: Ask ONE tough question about '{topic}'.\nCONSTRAINT: Max 2 sentences."

    else:
        mode = "exam"
        prompt = (
            f"{base_instruction}"
            f"TASK: Generate ONE standard exam question.\n"
            f"PRIORITIZE: 'Differentiate', 'Discuss process', 'List factors'.\n"
            f"CONSTRAINT: Max 2 sentences. Difficult."
        )

    try:
        response = model.generate_content(prompt)
        question_text = response.text
        
        # SAVE TO SESSION (Short-Term Memory)
        user_sessions[user_id] = question_text
        
        icons = {"exam": "🔥", "random": "🎲", "topic": "🔍"}
        await update.message.reply_text(f"{icons.get(mode, '📝')} **Question:**\n{question_text}")
    except Exception as e:
        await update.message.reply_text(f"❌ AI Error: {str(e)}")

# --- INTELLIGENT CHAT HANDLER ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_input = update.message.text
    
    full_text = get_user_context(user_id)
    last_question = user_sessions.get(user_id, "No active question.") # Retrieve memory
    
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    # This prompt now includes the LAST QUESTION
    prompt = (
        f"You are a Professor. \n"
        f"Context Notes: {full_text[:30000] if full_text else 'None'}\n"
        f"Last Question You Asked: '{last_question}'\n\n"
        f"Student Message: '{user_input}'\n\n"
        f"INSTRUCTIONS:\n"
        f"1. **IF ANSWERING:** Grade it 0/10 based on notes. Correct ruthlessly.\n"
        f"2. **IF GIVING UP (e.g. 'IDK', 'Answer it'):** Provide the correct answer to the Last Question.\n"
        f"3. **IF CHATTING:** Be professional.\n"
    )

    try:
        response = model.generate_content(prompt)
        await update.message.reply_text(response.text)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

if __name__ == '__main__':
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    # KEEP ALIVE FOR RENDER
    try:
        from keep_alive import keep_alive
        keep_alive()
    except ImportError:
        pass

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("quiz", generate_quiz))
    app.add_handler(MessageHandler(filters.Document.PDF, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    
    print("🔥 System is running... Press Ctrl+C to stop.")
    app.run_polling()