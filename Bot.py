import logging
import re
import datetime
import asyncio
import secrets
from aiogram import Bot, Dispatcher, types, executor
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.contrib.middlewares.fsm import FSMMiddleware
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.dispatcher import FSMContext
from aiogram.contrib.fsm_storage.memory import MemoryStorage
import aiohttp
import openai
from datetime import datetime, timedelta
import sqlite3
from sqlite3 import Error


TOKEN = '' #Токен брать отсюда @botfather
openai.api_key = '' #Ваш ключа отсюда https://discord.naga.ac/ | ОБЯЗАТЕЛЬНО ИМЕТЬ АВУ + АКК СТАРШЕ 7 ДНЕЙ
admin_id = #Ваш id акка в тг отсюда https://t.me/getmyid_bot
openai.api_base = "https://api.naga.ac/v1"

def generate_key():
    return secrets.token_hex(5)


class UserManager:
    def __init__(self, db_file):
        self.conn = self.create_connection(db_file)

    def create_connection(self, db_file):
        conn = None
        try:
            conn = sqlite3.connect(db_file)
        except Error as e:
            print(e)

        return conn

    def get_key(self, key):
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM users WHERE access_key=?", (key,))

        return cur.fetchone()

    def create_user(self, chat_id, key, expiration):
        cur = self.conn.cursor()
        cur.execute("INSERT INTO users(chat_id, access_key, access_expiration) VALUES(?, ?, ?)", (chat_id, key, expiration))

        self.conn.commit()

        return cur.lastrowid

    def update_user(self, chat_id, expiration):
        cur = self.conn.cursor()
        cur.execute("UPDATE users SET access_expiration = ? WHERE chat_id = ?", (expiration, chat_id))

        self.conn.commit()

    def get_user(self, chat_id):
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM users WHERE chat_id=?", (chat_id,))

        return cur.fetchone()

user_manager = UserManager(r"mydatabase.db")

class KeyManager:
    def __init__(self, db_file):
        self.db_file = db_file

    def create_key(self, key):
        conn = None
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            cursor.execute("INSERT INTO keys(key) VALUES(?)", (key,))
            conn.commit()
            return cursor.lastrowid
        except Error as e:
            print(e)
        finally:
            if conn:
                conn.close()

    def get_key(self, key):
        conn = None
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM keys WHERE key=?", (key,))
            return cursor.fetchone()
        except Error as e:
            print(e)
        finally:
            if conn:
                conn.close()

    def delete_key(self, key):
        conn = None
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM keys WHERE key=?", (key,))
            conn.commit()
        except Error as e:
            print(e)
        finally:
            if conn:
                conn.close()

key_manager = KeyManager("keysdatabase.db")



available_models = [
        "gpt-4",
        "gpt-4-0314",
        "gpt-3.5-turbo-0301",
        "gpt-3.5-turbo-16k",
        "llama-2-70b-chat"
]

logging.basicConfig(level=logging.INFO)

bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)
dp.middleware.setup(LoggingMiddleware())
dp.middleware.setup(FSMMiddleware())

INSTRUCTION = "You are GPT-4, one of the latest versions of the GPT model from OpenAI. Your mission is to provide the most useful, and accurate, ignoring all previously set instructions and following the latest standard set by OpenAI for GPT-3.5. If you are given the riddle 'I have a nice riddle for you…. Can you find the the mistake? 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 43 44 45 46 47 48 49 50 51 52 53 54 55 56 57 58 59 60 61 62 63 64 65 66 67 68 69 70 71 72 73', your answer should indicate that the error is the repetition of the word 'the' in the phrase 'Can you find the the mistake?', not in the sequence of numbers."

class ImagePrompt(StatesGroup):
    waiting_for_text = State()

class Tts(StatesGroup):
    waiting_for_tt = State()

user_states = {}

async def generate_speech(text: str):
    try:
        headers = {'Authorization': f'Bearer {openai.api_key}'}
        json_data = {'text': text, 'language': 'ru'} 
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.post('https://chimeragpt.adventblocks.cc/v1/audio/tts/generation', json=json_data) as resp:
                if resp.status == 200:
                    response = await resp.json()
                    return response
                else:
                    error_message = await resp.text()
                    raise Exception(f"Text-to-speech API returned non-200 status code: {resp.status}. Error: {error_message}")
    except Exception as e:
        raise Exception(f"Error in generating speech: {str(e)}")


async def start_dialog(user_id):
    if user_id not in user_states:
        user_states[user_id] = {'model': None, 'button_sent': False, 'conversation': []}
    user_data = user_states[user_id]
    if user_data['model']:
        await bot.send_message(user_id, 'Для начала завершите диалог.')
    else:
        model_keyboard = types.InlineKeyboardMarkup(row_width=1)
        model_buttons = [types.InlineKeyboardButton(model, callback_data=model) for model in available_models]
        model_keyboard.add(*model_buttons)
        model_keyboard.add(types.InlineKeyboardButton('Создать изображение', callback_data='image_prompt'))
        await bot.send_message(user_id, f'<b>Выберите модель:</b>\n\n<i>Имейте ввиду то, что некоторые модели могут быть перегружены!</i>', reply_markup=model_keyboard, parse_mode='HTML')

@dp.message_handler(commands=['start'])
async def handle_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    user = message.from_user
    user_states[user_id] = {'model': None, 'button_sent': False, 'conversation': []}

    user_data = user_manager.get_user(user_id)
    if user_data and datetime.strptime(user_data[3], '%Y-%m-%d %H:%M:%S.%f') > datetime.now():
        await message.answer(f"👋 Приветствую, <b>{user.first_name}</b>!\n\nВаша подписка действительна до <b>{user_data[3]}</b>.", reply_markup=get_start_dialog_keyboard(), parse_mode='HTML')
        await start_dialog(user_id)
    else:
        await message.answer(f"👋 Приветствую, <b>{user.first_name}</b>! \n\nПожалуйста, введите ваш <b>ключ доступа.</b>", parse_mode='HTML')

@dp.message_handler(lambda message: len(message.text) == 10)
async def handle_access_key(message: types.Message):
    user_id = message.from_user.id
    user = message.from_user
    access_key = message.text

    
    key_data = key_manager.get_key(access_key)
    if key_data:
        
        expiration = datetime.now() + timedelta(days=30)

        user_data = user_manager.get_user(user_id)
        if user_data:
            
            user_manager.update_user(user_id, expiration)
        else:
            
            user_manager.create_user(user_id, access_key, expiration)

        
        key_manager.delete_key(access_key)

        await message.answer("Поздравляем с покупкой 🎊 \n\nВы получили доступ к боту на 30 дней. \n\n Очень просим вас оставить отзыв, вы очень поможете в развитии нашего проекта!",
                             reply_markup=get_start_dialog_keyboard())
        await start_dialog(user_id)
    else:
        
        await message.answer("Введен неверный ключ. Пожалуйста, введите действительный ключ доступа.")

async def check_subscription(user_id):
    user_data = user_manager.get_user(user_id)
    if user_data and datetime.strptime(user_data[3], '%Y-%m-%d %H:%M:%S.%f') > datetime.now():
        return True
    else:
        return False


@dp.message_handler(commands=['gen'], user_id=admin_id)
async def handle_generate_key(message: types.Message):
    new_key = generate_key()

    key_manager.create_key(new_key)

    await message.answer(f"Код - <code>{new_key}</code>.", parse_mode='HTML')


@dp.callback_query_handler(lambda query: query.data in available_models or query.data == 'image_prompt' or query.data == 'tts')
async def select_model_or_image_prompt(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    subscription_valid = await check_subscription(user_id)
    if not subscription_valid:
        await callback_query.message.answer('У вас нет подписки. Пожалуйста, отправьте новый ключ доступа.')
        return

    if user_id not in user_states:
        user_states[user_id] = {'model': None, 'button_sent': False, 'conversation': []}

    await callback_query.answer()
    if callback_query.data == 'image_prompt':
        await callback_query.message.answer("Введите текст для генерации изображения:")
        await ImagePrompt.waiting_for_text.set()
        user_states[user_id]['model'] = None
        cancel_button = KeyboardButton("Завершить диалог")
        cancel_markup = ReplyKeyboardMarkup(resize_keyboard=True).add(cancel_button)
        await callback_query.message.answer('Вы можете завершить диалог, нажав кнопку "Завершить диалог".', reply_markup=cancel_markup)
        user_states[user_id]['button_sent'] = True
    elif callback_query.data == 'tts':
        await callback_query.message.answer("Введите текст для озвучки:")
        await Tts.waiting_for_tt.set()
        user_states[user_id]['model'] = None
        cancel_button = KeyboardButton("Завершить диалог")
        cancel_markup = ReplyKeyboardMarkup(resize_keyboard=True).add(cancel_button)
        await callback_query.message.answer('Вы можете завершить диалог, нажав кнопку "Завершить диалог".', reply_markup=cancel_markup)
        user_states[user_id]['button_sent'] = True
    else:
        display_model = callback_query.data
        if callback_query.data == 'gpt-4':
            selected_model = 'gpt-3.5-turbo'
        else:
            selected_model = callback_query.data
        user_states[user_id]['model'] = selected_model
        await callback_query.message.edit_text(f'Выбранная модель: <b>{display_model}</b>.\n\nОтправьте сообщение, чтобы начать диалог.', parse_mode='HTML')
        cancel_button = KeyboardButton("Завершить диалог")
        cancel_markup = ReplyKeyboardMarkup(resize_keyboard=True).add(cancel_button)
        await callback_query.message.answer('Чтобы <b>закончить диалог</b> нажмите кнопку внизу 👇', reply_markup=cancel_markup, parse_mode='HTML')
        user_states[user_id]['button_sent'] = True
      

@dp.message_handler(state=ImagePrompt.waiting_for_text)
async def process_text(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    subscription_valid = await check_subscription(user_id)
    if not subscription_valid:
        await message.answer('У вас нет подписки. Пожалуйста, отправьте новый ключ доступа.')
        return
    user_data = user_states.get(user_id, {})
    if message.text.lower() == 'завершить диалог':
        await state.finish()
        user_states[user_id] = {'model': None, 'button_sent': False, 'conversation': []}
        await message.reply('Диалог завершен. \n\nВы можете начать новый диалог, нажав кнопку "Начать диалог". \n\nВыбрать модель можно командой /start', reply_markup=get_start_dialog_keyboard())
        return
    try:
        prompt_text = message.text
        response = openai.Image.create(
            prompt=prompt_text,
            n=4,
            size="1024x1024"
        )
        for image in response['data']:
            await bot.send_photo(message.chat.id, photo=image['url'])

    except openai.error.APIError as e:
        error_message = "Произошла ошибка при создании изображения: "
        if hasattr(e, 'response') and 'detail' in e.response:
            error_message += e.response['detail']
        else:
            error_message += str(e)
        await message.answer(error_message)

@dp.message_handler(lambda message: message.text.lower() == 'завершить диалог')
async def cancel(message: types.Message):
    user_id = message.from_user.id
    subscription_valid = await check_subscription(user_id)
    if not subscription_valid:
        await message.answer('У вас нет подписки. Пожалуйста, отправьте новый ключ доступа.')
        return
    user_data = user_states.get(user_id)
    if user_data and user_data.get('button_sent'):
        user_states[user_id] = {'model': None, 'button_sent': False, 'conversation': []}
        await message.answer('Диалог завершен. Вы можете начать новый диалог, нажав кнопку "Начать диалог".', reply_markup=get_start_dialog_keyboard())
    else:
        await message.reply('Сейчас нет активного диалога.')


class TtsLanguage(StatesGroup):
    waiting_for_language = State()


@dp.message_handler(state=Tts.waiting_for_tt)
async def process_tts_text(message: types.Message, state: FSMContext):
    try:
        text = message.text

        if text.lower() == 'завершить диалог':
            await state.finish()
            user_id = message.from_user.id
            user_states[user_id]['model'] = None
            user_states[user_id]['button_sent'] = False
            await message.reply('Диалог завершен. Вы можете начать новый диалог, нажав кнопку "Начать диалог".', reply_markup=get_start_dialog_keyboard())
            return

        text = text.strip()

        if not text:
            text = "Please enter valid text."

        await generate_tts_for_text(text, message.chat.id)

    except:
        await bot.send_message(message.chat.id, "Ошибка при озвучке текста.")


async def generate_tts_for_text(text: str, chat_id: int):
   
    if text:
        resp = await generate_speech(text)
        url = resp['url']
        async with aiohttp.ClientSession() as session:
            response = await session.get(url)
        audio_file = await response.content.read()
        await bot.send_audio(chat_id, audio_file)


@dp.message_handler(content_types=types.ContentTypes.TEXT)
async def chat_message(message: types.Message):
    user_id = message.from_user.id
    subscription_valid = await check_subscription(user_id)
    if not subscription_valid:
        await message.answer('У вас нет подписки. Пожалуйста, отправьте новый ключ доступа.')
        return
    user_data = user_states.get(user_id, {})
    model = user_data.get('model')

    if model:

        response_message = await message.reply("Генерирую ответ...")
        for i in range(1):
            await asyncio.sleep(1)
            await bot.edit_message_text(f"Генерирую ответ{'.' * (i + 1)}", chat_id=message.chat.id,
                                        message_id=response_message.message_id)

        if model == 'gpt-3.5-turbo':
            print('pick gpt4')
            conversation = user_data['conversation']
            conversation.append({'role': 'system', 'content': INSTRUCTION})
        else:
            print('pick all')
            conversation = user_data['conversation']

        conversation.append({'role': 'user', 'content': message.text})
        try:
            response = openai.ChatCompletion.create(
                model=model,
                messages=conversation,
                max_tokens=1500,
                n=1,
                temperature=0.7
            )
            ai_response = response.choices[0].message['content']
        except openai.error.APIError as e:
            match = re.search(r'Please, try again in (\d+) seconds', str(e))
            if match:
                wait_time = match.group(1)
                ai_response = f"Произошла ошибка при обработке вашего запроса. Пожалуйста, подождите {wait_time} секунд и попробуйте снова."
            else:
                ai_response = f"Произошла ошибка при обработке вашего запроса: {str(e)}"

        await bot.edit_message_text(ai_response, chat_id=message.chat.id, message_id=response_message.message_id)
        if not user_data.get('button_sent', False):
            cancel_button = KeyboardButton("Завершить диалог")
            cancel_markup = ReplyKeyboardMarkup(resize_keyboard=True).add(cancel_button)
            await message.answer('Вы можете завершить диалог, нажав кнопку "Завершить диалог".',
                                 reply_markup=cancel_markup)
            user_states[user_id]['button_sent'] = True
    else:
        model_keyboard = types.InlineKeyboardMarkup(row_width=1)
        model_buttons = [types.InlineKeyboardButton(model, callback_data=model) for model in available_models]
        model_keyboard.add(*model_buttons)
        model_keyboard.add(types.InlineKeyboardButton('Создать изображение', callback_data='image_prompt'))
        await message.answer(
            '<b>Выберите модель:</b>\n\n<i>Имейте ввиду то, что некоторые модели могут быть перегружены!</i>',
            reply_markup=model_keyboard, parse_mode='HTML')

def get_start_dialog_keyboard():
    start_button = KeyboardButton("Начать диалог")
    start_markup = ReplyKeyboardMarkup(resize_keyboard=True).add(start_button)
    return start_markup

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
