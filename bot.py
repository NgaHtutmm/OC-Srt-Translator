import os
import asyncio
import zipfile
import pyzipper
import shutil
from pathlib import Path
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY")

if not BOT_TOKEN or not OPENAI_KEY:
    raise RuntimeError("Please set TELEGRAM_BOT_TOKEN and OPENAI_API_KEY in environment variables (see .env.example)")

client = OpenAI(api_key=OPENAI_KEY)

# Temporary storage per-user (in-memory). For production, replace with persistent storage if needed.
USER_DATA = {}  # user_id -> {"path": path, "type": "zip"|"file", "original_name": name}

SUPPORTED_LANGS = {
    "my": "Burmese",
    "en": "English",
    "ja": "Japanese",
    "th": "Thai",
    "ko": "Korean",
    "zh": "Chinese"
}

SUPPORTED_SUB_EXT = {".srt", ".vtt", ".ass"}
SUPPORTED_STR_EXT = {".str"}
SUPPORTED_ZIP_EXT = {".zip"}

# ---------------- Prompt & translate helpers ----------------
async def call_chat_completion(prompt: str) -> str:
    # Single helper wrapper for OpenAI/Gemini chat completion.
    # Uses chat.completions.create because older SDK pattern used earlier examples.
    # If your SDK differs, adapt to the appropriate method.
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}]
    )
    # Extract text safely
    try:
        return response.choices[0].message["content"]
    except Exception:
        # Fallback to string representation
        return str(response)

async def translate_str_file(content: str, target_lang: str) -> str:
    prompt = f"""You are a professional translation engine.
Auto-detect the file language. Translate the RIGHT-HAND VALUES only in this `.str` file into {target_lang}.
Rules:
- Preserve key names (left side) exactly.
- Preserve format, spacing and newlines.
- Only translate the values.
Example:
hello_world=Hello World
-> hello_world=Translated Here
Now translate:
{content}
"""
    return await call_chat_completion(prompt)

async def translate_srt(content: str, target_lang: str) -> str:
    prompt = f"""You are a subtitle translation engine.
Translate ONLY the spoken/dialogue text into {target_lang}.
DO NOT change:
- Line numbers
- Timecodes
- Formatting / tags like <i>, <b>, {{\i1}}, etc.
Preserve line breaks and spacing exactly.
Translate faithfully.
Now translate:
{content}
"""
    return await call_chat_completion(prompt)

async def translate_srt_safe_adult(content: str, target_lang: str) -> str:
    prompt = f"""You are a subtitle translation assistant.
The subtitle may contain adult or explicit content. This is allowed AS LONG AS YOU DO NOT ADD, EXPAND, OR INTENSIFY SEXUAL CONTENT.
Translate ONLY the existing dialogue into {target_lang}.
DO NOT:
- Add new sexual details
- Change tone to be more sexual
Preserve structure, line numbers, timecodes and tags.
Now translate:
{content}
"""
    return await call_chat_completion(prompt)

# ---------------- File helpers ----------------
def extract_zip(zip_path: str, dest_folder: str):
    """Extracts zip (supports AES encrypted zips handled by pyzipper)"""
    os.makedirs(dest_folder, exist_ok=True)
    try:
        with pyzipper.AESZipFile(zip_path, 'r') as z:
            z.extractall(dest_folder)
    except Exception:
        # fallback to python zipfile for simple archives
        with zipfile.ZipFile(zip_path, 'r') as z:
            z.extractall(dest_folder)

def make_zip(from_folder: str, out_path: str):
    with zipfile.ZipFile(out_path, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(from_folder):
            for f in files:
                absf = os.path.join(root, f)
                rel = os.path.relpath(absf, from_folder)
                z.write(absf, rel)

# ---------------- Telegram handlers ----------------
async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Called when user uploads any document (zip or single file)
    doc = update.message.document
    if not doc:
        await update.message.reply_text("❌ No document detected. Send a ZIP or supported file.")
        return

    ext = Path(doc.file_name).suffix.lower()
    user_id = update.effective_user.id
    file_id = doc.file_id
    unique_name = f"{file_id}_{doc.file_name}"
    save_path = os.path.join('data', unique_name)
    os.makedirs('data', exist_ok=True)

    file = await doc.get_file()
    await file.download_to_drive(save_path)

    # store info
    if ext in SUPPORTED_ZIP_EXT:
        USER_DATA[user_id] = {"path": save_path, "type": "zip", "name": doc.file_name}
    else:
        USER_DATA[user_id] = {"path": save_path, "type": "file", "name": doc.file_name}

    # Send language selection menu
    keyboard = [[InlineKeyboardButton(f"{v}", callback_data=f"lang_{k}")] for k,v in SUPPORTED_LANGS.items()]
    await update.message.reply_text("🌐 Choose a target language:", reply_markup=InlineKeyboardMarkup(keyboard))

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    user_id = query.from_user.id

    if data.startswith("lang_"):
        target = data.split("_")[1]
        # show mode menu
        keyboard_modes = [
            [InlineKeyboardButton("🌐 Normal Translation", callback_data=f"mode_normal_{target}")],
            [InlineKeyboardButton("🔞 Adult-Safe Subtitles", callback_data=f"mode_adult_{target}")],
        ]
        await query.edit_message_text(f"Choose translation mode for: {target}", reply_markup=InlineKeyboardMarkup(keyboard_modes))
        return

    if data.startswith("mode_"):
        # parse mode and lang
        parts = data.split("_")
        if len(parts) < 3:
            await query.edit_message_text("❌ Invalid selection.")
            return
        mode = parts[1]
        target = parts[2]
        info = USER_DATA.get(user_id)
        if not info:
            await query.edit_message_text("❌ No uploaded file found. Upload a ZIP or file first.")
            return

        await query.edit_message_text("⏳ Processing... This may take a while for large ZIPs.")

        # Prepare working directory
        working = Path('work') / f"{user_id}"
        if working.exists():
            shutil.rmtree(working)
        working.mkdir(parents=True, exist_ok=True)

        try:
            if info['type'] == 'zip':
                extract_dir = working / 'extracted'
                extract_zip(info['path'], str(extract_dir))
                # scan for supported files
                translated_count = 0
                for root, _, files in os.walk(extract_dir):
                    for fname in files:
                        fpath = os.path.join(root, fname)
                        ext = Path(fname).suffix.lower()
                        if ext in SUPPORTED_SUB_EXT:
                            with open(fpath, 'r', encoding='utf-8', errors='ignore') as fh:
                                txt = fh.read()
                            if mode == 'normal':
                                out = asyncio.get_event_loop().run_until_complete(translate_srt(txt, target))
                            else:
                                out = asyncio.get_event_loop().run_until_complete(translate_srt_safe_adult(txt, target))
                            with open(fpath, 'w', encoding='utf-8') as fh:
                                fh.write(out)
                            translated_count += 1
                        elif ext in SUPPORTED_STR_EXT:
                            with open(fpath, 'r', encoding='utf-8', errors='ignore') as fh:
                                txt = fh.read()
                            out = asyncio.get_event_loop().run_until_complete(translate_str_file(txt, target))
                            with open(fpath, 'w', encoding='utf-8') as fh:
                                fh.write(out)
                            translated_count += 1

                # repackage
                out_zip = working / f"translated_{user_id}.zip"
                make_zip(str(extract_dir), str(out_zip))
                await query.message.reply_document(document=open(out_zip, 'rb'))
                translated_msg = f"✅ Done. Translated files: {translated_count}"
                await query.message.reply_text(translated_msg)

            else:
                # single file
                fpath = info['path']
                ext = Path(fpath).suffix.lower()
                if ext in SUPPORTED_SUB_EXT:
                    with open(fpath, 'r', encoding='utf-8', errors='ignore') as fh:
                        txt = fh.read()
                    if mode == 'normal':
                        out = await translate_srt(txt, target)
                    else:
                        out = await translate_srt_safe_adult(txt, target)
                    out_name = f"translated_{info.get('name')}"
                    out_path = working / out_name
                    with open(out_path, 'w', encoding='utf-8') as fh:
                        fh.write(out)
                    await query.message.reply_document(document=open(out_path, 'rb'))
                    await query.message.reply_text("✅ Done.")
                elif ext in SUPPORTED_STR_EXT:
                    with open(fpath, 'r', encoding='utf-8', errors='ignore') as fh:
                        txt = fh.read()
                    out = await translate_str_file(txt, target)
                    out_name = f"translated_{info.get('name')}"
                    out_path = working / out_name
                    with open(out_path, 'w', encoding='utf-8') as fh:
                        fh.write(out)
                    await query.message.reply_document(document=open(out_path, 'rb'))
                    await query.message.reply_text("✅ Done.")
                else:
                    await query.message.reply_text("❌ Unsupported file type for single-file translation. Use a ZIP for batch.")
        except Exception as e:
            await query.message.reply_text(f"❌ Error while processing: {e}")
        finally:
            # cleanup user data and temp folders
            try:
                if 'path' in info and os.path.exists(info['path']):
                    os.remove(info['path'])
            except Exception:
                pass
            try:
                if working.exists():
                    shutil.rmtree(working)
            except Exception:
                pass
            USER_DATA.pop(user_id, None)

async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(CallbackQueryHandler(on_callback))
    print("Bot is starting...")
    await app.run_polling()

if __name__ == '__main__':
    asyncio.run(main())
