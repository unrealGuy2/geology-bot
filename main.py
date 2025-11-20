from keep_alive import keep_alive
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

# 2. Configure Gemini AI (Gemini 2.0 Flash)
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.0-flash')

# 3. Setup Long-Term Memory (Disk Storage)
KB_FOLDER = "knowledge_base"
os.makedirs(KB_FOLDER, exist_ok=True)

# In-Memory Cache
user_knowledge_base = {}

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- HELPER: RETRIEVE MEMORY ---
def get_user_context(user_id):
    """Checks RAM first, then Disk. Returns text or None."""
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
        "1. 📂 **Upload PDF** (Overwrites notes).\n"
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

    msg = await update.message.reply_text("⚙️ Processing & Saving to Brain...")
    
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
        await msg.edit_text(f"✅ **Saved.** I will remember this file forever.\nType `/quiz` to start.")
        
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
    prompt_context = ""

    if args and args[0].lower() == "random":
        mode = "random"
        if len(full_text) > 4000:
            start = random.randint(0, len(full_text) - 4000)
            prompt_context = full_text[start : start+4000]
        else:
            prompt_context = full_text
            
        prompt = (
            f"Act as a ruthless Professor. Read this random excerpt:\n---\n{prompt_context}\n---\n"
            f"TASK: Ask ONE specific question based ONLY on this excerpt.\n"
            f"CONSTRAINT: Max 2 sentences. Direct."
        )

    elif args:
        mode = "topic"
        topic = " ".join(args)
        prompt_context = full_text[:40000]
        prompt = (
            f"Act as a ruthless Professor. Ask ONE tough question about '{topic}' based on these notes.\n"
            f"NOTES: {prompt_context}\n"
            f"CONSTRAINT: Max 2 sentences."
        )

    else:
        mode = "exam"
        prompt_context = full_text[:40000]
        prompt = (
            f"Act as a strict Professor (Geology Dept). Scan these notes for likely Exam Questions.\n"
            f"NOTES: {prompt_context}\n\n"
            f"TASK: Generate ONE standard exam question.\n"
            f"PRIORITIZE: 'Differentiate', 'Discuss process', 'List factors'.\n"
            f"CONSTRAINT: Max 2 sentences. Difficult."
        )

    try:
        response = model.generate_content(prompt)
        icons = {"exam": "🔥", "random": "🎲", "topic": "🔍"}
        await update.message.reply_text(f"{icons.get(mode, '📝')} **Question:**\n{response.text}")
    except Exception as e:
        await update.message.reply_text(f"❌ AI Error: {str(e)}")

# --- THE NEW SMART CHAT HANDLER ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_input = update.message.text
    
    full_text = get_user_context(user_id)
    
    # If no PDF loaded, just chat normally or warn them
    context_text = full_text[:30000] if full_text else "NO PDF LOADED."
    
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    # This prompt tells Gemini to figure out what you want
    prompt = (
        f"You are a strict but intelligent Professor. The student just sent a message.\n"
        f"Context Notes (If any): {context_text[:500]}... [truncated]\n\n"
        f"Student Message: '{user_input}'\n\n"
        f"INSTRUCTIONS:\n"
        f"1. **IF IT'S AN ANSWER:** Grade it 0/10 based on the full notes. Correct errors ruthlessly.\n"
        f"2. **IF IT'S A QUESTION:** Answer it briefly using facts from the notes.\n"
        f"3. **IF IT'S CASUAL CHAT (e.g. 'Hi', 'Thanks', 'Bye'):** Reply briefly and professionally. Remind them to study.\n"
        f"4. **IF NO PDF IS LOADED:** Tell them to upload a file first."
    )

    try:
        response = model.generate_content(prompt)
        await update.message.reply_text(response.text)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

if __name__ == '__main__':
    # WINDOWS FIX
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("quiz", generate_quiz))
    app.add_handler(MessageHandler(filters.Document.PDF, handle_document))
    # Handles ALL text (Answers OR Chat)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    keep_alive()
    print("🔥 System is running... Press Ctrl+C to stop.")
    app.run_polling()