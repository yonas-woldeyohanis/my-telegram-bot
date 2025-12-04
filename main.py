import os
import logging
import asyncio
import re
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, 
    ContextTypes, 
    CommandHandler, 
    MessageHandler, 
    filters, 
    CallbackQueryHandler
)
import yt_dlp
from keep_alive import keep_alive

# --- CONFIGURATION ---
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

# Create downloads folder
if not os.path.exists('downloads'):
    os.makedirs('downloads')

# Setup Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- GLOBAL STATE ---
processing_users = set()

# --- HELPERS ---

def is_valid_url(text):
    regex = re.compile(
        r'^(?:http|ftp)s?://' 
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|' 
        r'localhost|' 
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})' 
        r'(?::\d+)?' 
        r'(?:/?|[/?]\S+)$', re.IGNORECASE)
    return re.match(regex, text) is not None

def cleanup_file(filename):
    try:
        if filename and os.path.exists(filename):
            os.remove(filename)
    except Exception as e:
        logging.error(f"Error deleting file {filename}: {e}")

# --- THE ANTI-BOT FIX ---
def get_common_opts():
    """
    Returns the options that trick YouTube into thinking we are a phone.
    """
    return {
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        # THIS IS THE KEY FIX: Pretend to be an Android device
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios']
            }
        },
        # Spoof User Agent to look like a browser
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    }

def get_video_options(url):
    ydl_opts = get_common_opts() # Load the anti-bot settings
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if 'formats' not in info:
                return []
            resolutions = set()
            for f in info['formats']:
                if f.get('vcodec') != 'none' and f.get('height'):
                    resolutions.add(f['height'])
            return sorted(list(resolutions), reverse=True)
    except Exception:
        return []

# --- DOWNLOAD LOGIC ---

async def download_and_send_audio(url, update, context):
    chat_id = update.effective_chat.id
    status_msg = await context.bot.send_message(chat_id, "⏳ Downloading Audio... (Converting to MP3)")
    
    output_template = f"downloads/{chat_id}_%(title)s.%(ext)s"
    
    # Merge common opts with specific audio opts
    ydl_opts = get_common_opts()
    ydl_opts.update({
        'format': 'bestaudio/best',
        'outtmpl': output_template,
        'postprocessors': [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3','preferredquality': '192'}],
    })

    final_filename = None
    try:
        loop = asyncio.get_event_loop()
        def run_download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return ydl.prepare_filename(info)

        temp_name = await loop.run_in_executor(None, run_download)
        final_filename = temp_name.rsplit('.', 1)[0] + ".mp3"

        await context.bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text="🚀 Uploading Audio...")
        
        with open(final_filename, 'rb') as f:
            await context.bot.send_audio(chat_id=chat_id, audio=f, caption="Here is your audio! 🎵")

        await context.bot.delete_message(chat_id=chat_id, message_id=status_msg.message_id)

    except Exception as e:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text=f"❌ Error: {str(e)}")
    finally:
        cleanup_file(final_filename)
        if chat_id in processing_users:
            processing_users.remove(chat_id)

async def download_and_send_video(url, quality, update, context):
    chat_id = update.effective_chat.id
    status_msg = await context.bot.send_message(chat_id, f"⏳ Downloading Video ({quality}p)...")

    output_template = f"downloads/{chat_id}_%(title)s.%(ext)s"
    
    if quality == 'best':
        format_str = 'bestvideo+bestaudio/best'
    else:
        format_str = f'bestvideo[height<={quality}]+bestaudio/best[height<={quality}]'

    # Merge common opts with specific video opts
    ydl_opts = get_common_opts()
    ydl_opts.update({
        'format': format_str,
        'outtmpl': output_template,
        'merge_output_format': 'mp4',
    })

    final_filename = None
    try:
        loop = asyncio.get_event_loop()
        def run_download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return ydl.prepare_filename(info)

        temp_name = await loop.run_in_executor(None, run_download)
        
        final_filename = temp_name
        if not final_filename.endswith(".mp4"):
             final_filename = temp_name.rsplit('.', 1)[0] + ".mp4"

        if os.path.exists(final_filename) and os.path.getsize(final_filename) > 52428800:
            await context.bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, 
                                                text="❌ File too big (>50MB). Telegram API limit reached.")
            return

        await context.bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text="🚀 Uploading Video...")
        
        with open(final_filename, 'rb') as f:
            await context.bot.send_video(chat_id=chat_id, video=f, supports_streaming=True)

        await context.bot.delete_message(chat_id=chat_id, message_id=status_msg.message_id)

    except Exception as e:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text=f"❌ Error: {str(e)}")
    finally:
        cleanup_file(final_filename)
        if chat_id in processing_users:
            processing_users.remove(chat_id)

async def download_and_send_image(url, update, context):
    chat_id = update.effective_chat.id
    status_msg = await context.bot.send_message(chat_id, "⏳ Downloading Image...")
    
    output_template = f"downloads/{chat_id}_image" 
    
    # Merge common opts
    ydl_opts = get_common_opts()
    ydl_opts.update({
        'format': 'best',
        'outtmpl': output_template,
        'writethumbnail': True,
    })

    found_file = None
    try:
        loop = asyncio.get_event_loop()
        def run_download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return ydl.prepare_filename(info)

        downloaded_path = await loop.run_in_executor(None, run_download)
        
        base_name = f"downloads/{chat_id}_image"
        for ext in ['.jpg', '.jpeg', '.png', '.webp']:
            if os.path.exists(downloaded_path):
                found_file = downloaded_path
                break
            if os.path.exists(base_name + ext):
                found_file = base_name + ext
                break
        
        if found_file:
            await context.bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text="🚀 Uploading Image...")
            with open(found_file, 'rb') as f:
                await context.bot.send_photo(chat_id=chat_id, photo=f)
            await context.bot.delete_message(chat_id=chat_id, message_id=status_msg.message_id)
        else:
            await context.bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text="❌ Could not fetch image.")

    except Exception as e:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text=f"❌ Error: {str(e)}")
    finally:
        cleanup_file(found_file)
        if chat_id in processing_users:
            processing_users.remove(chat_id)

# --- BOT HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Hello! Send me a link to download.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text

    if chat_id in processing_users:
        await update.message.reply_text("⚠️ Processing previous link. Please wait!")
        return

    if not is_valid_url(text):
        await update.message.reply_text("⚠️ Please send a valid http:// or https:// link.")
        return

    context.user_data['current_url'] = text
    
    keyboard = [
        [InlineKeyboardButton("🎵 Audio (MP3)", callback_data='type_audio')],
        [InlineKeyboardButton("🎬 Video (Quality)", callback_data='type_video_selection')],
        [InlineKeyboardButton("📸 Image", callback_data='type_image')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Link detected! Choose format:", reply_markup=reply_markup)

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = update.effective_chat.id
    await query.answer()
    
    if chat_id in processing_users:
        await query.edit_message_text("⚠️ Processing active. Please wait.")
        return

    choice = query.data
    url = context.user_data.get('current_url')

    if choice == 'type_audio':
        processing_users.add(chat_id)
        await query.delete_message()
        await download_and_send_audio(url, update, context)

    elif choice == 'type_image':
        processing_users.add(chat_id)
        await query.delete_message()
        await download_and_send_image(url, update, context)
        
    elif choice == 'type_video_selection':
        await query.edit_message_text(text="🔍 Scanning available qualities...")
        resolutions = get_video_options(url)
        
        if not resolutions:
            keyboard = [[InlineKeyboardButton("🎬 Download Best", callback_data='download_video_best')]]
        else:
            keyboard = []
            for res in resolutions[:4]:
                keyboard.append([InlineKeyboardButton(f"🎬 {res}p", callback_data=f'download_video_{res}')])
            
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text="Select Video Quality:", reply_markup=reply_markup)
    
    elif choice.startswith('download_video_'):
        processing_users.add(chat_id)
        quality = choice.split('_')[-1]
        await query.delete_message()
        await download_and_send_video(url, quality, update, context)

# --- MAIN ---
if __name__ == '__main__':
    keep_alive() # Start web server
    
    if not TOKEN:
        print("Error: BOT_TOKEN not found.")
        exit(1)

    application = ApplicationBuilder().token(TOKEN).build()
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(button_click))

    print("Bot is running...")
    application.run_polling()