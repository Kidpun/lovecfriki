import asyncio
import re
from telethon import TelegramClient, events
from telethon.tl.types import Message, MessageEntityUrl, MessageEntityTextUrl
import logging
from collections import defaultdict
from config import CHANNELS, BOT_ID, TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE_NUMBER

logging.getLogger('telethon').setLevel(logging.WARNING)

logging.basicConfig(
    format='%(message)s',
    level=logging.INFO,
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

channel_check_counts = defaultdict(int)
channel_names = {}
channel_access = {}

REF_LINK_PATTERN = re.compile(
    r'(?:https?://)?t\.me/FreakRedanBot\?start=code(?:_[A-Z0-9]+)?',
    re.IGNORECASE
)

INACTIVE_CHECK_PATTERN = re.compile(r'❌\s*Чек\s+уже\s+неактивен', re.IGNORECASE)

SESSION_NAME = 'telegram_bot_session'

processed_checks = set()
inactive_checks = set()
check_attempts = defaultdict(int)
MAX_ATTEMPTS = 2
last_activated_check = None
pending_retries = {}

check_lock = asyncio.Lock()


def extract_links_from_message(message: Message):
    links = []
    text = message.text or message.raw_text or ""
    
    if not text:
        return links
    
    for match in REF_LINK_PATTERN.finditer(text):
        full_match = match.group(0)
        if not full_match.startswith('http'):
            full_match = 'https://' + full_match
        links.append(full_match)
    
    if message.entities:
        for entity in message.entities:
            if isinstance(entity, MessageEntityUrl):
                url_start = entity.offset
                url_end = entity.offset + entity.length
                if url_end <= len(text):
                    url_text = text[url_start:url_end]
                    if REF_LINK_PATTERN.search(url_text):
                        if not url_text.startswith('http'):
                            url_text = 'https://' + url_text
                        links.append(url_text)
            elif isinstance(entity, MessageEntityTextUrl):
                if entity.url and REF_LINK_PATTERN.search(entity.url):
                    normalized_url = entity.url
                    if not normalized_url.startswith('http'):
                        normalized_url = 'https://' + normalized_url
                    links.append(normalized_url)
    
    return list(set(links))


def find_ref_links_in_buttons(message: Message):
    links = []
    
    if message.reply_markup and hasattr(message.reply_markup, 'rows'):
        for row in message.reply_markup.rows:
            if hasattr(row, 'buttons'):
                for button in row.buttons:
                    if hasattr(button, 'url') and button.url:
                        url = button.url
                        if REF_LINK_PATTERN.search(url):
                            if not url.startswith('http'):
                                url = 'https://' + url
                            links.append({
                                'url': url,
                                'button': button
                            })
    
    return links


async def retry_activation(client: TelegramClient, check_code: str, link: str):
    global last_activated_check
    
    async with check_lock:
        if check_code in inactive_checks:
            return
        last_activated_check = check_code
    
    await asyncio.sleep(0.05)
    
    logger.info(f"🔄 Повторная попытка: {check_code}")
    
    check_code_new, start_param = extract_check_code(link)
    if check_code_new and start_param:
        try:
            bot_username = "FreakRedanBot"
            await client.send_message(bot_username, f"/start {start_param}")
        except Exception as e:
            logger.error(f"❌ Ошибка при повторной попытке: {e}")
            async with check_lock:
                if last_activated_check == check_code:
                    last_activated_check = None


def extract_check_code(link: str) -> tuple:
    match = re.search(r'start=code_([A-Z0-9]+)', link, re.IGNORECASE)
    if match:
        check_code = match.group(1)
        start_param = f"code_{check_code}"
        return check_code, start_param
    
    match = re.search(r'start=([^&\s]+)', link)
    if match:
        start_param = match.group(1)
        if start_param.startswith('code_'):
            check_code = start_param.replace('code_', '', 1)
            return check_code, start_param
        elif '_' in start_param:
            check_code = start_param.split('_', 1)[1]
            return check_code, start_param
        else:
            return start_param, start_param
    
    return None, None


async def process_ref_link(client: TelegramClient, link: str):
    global last_activated_check, pending_retries
    
    try:
        check_code, start_param = extract_check_code(link)
        
        if not check_code or not start_param:
            return False
        
        bot_username = "FreakRedanBot"
        
        async with check_lock:
            if check_code in inactive_checks:
                return False
            
            if check_code in processed_checks:
                attempts = check_attempts.get(check_code, 0)
                if attempts >= MAX_ATTEMPTS:
                    return False
            else:
                processed_checks.add(check_code)
                check_attempts[check_code] = 1
            
            last_activated_check = check_code
            
            attempts = check_attempts.get(check_code, 1)
            if attempts < MAX_ATTEMPTS:
                pending_retries[check_code] = link
        
        logger.info(f"⚡ АКТИВИРУЮ: {check_code}")
        await client.send_message(bot_username, f"/start {start_param}")
        
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        try:
            async with check_lock:
                check_code, _ = extract_check_code(link)
                if check_code:
                    pending_retries.pop(check_code, None)
                    if last_activated_check == check_code:
                        last_activated_check = None
        except:
            pass
        return False


async def handle_new_message(event: events.NewMessage.Event):
    message = event.message
    
    try:
        channel_entity = await event.client.get_entity(message.peer_id)
        channel_id = channel_entity.id
        channel_title = getattr(channel_entity, 'title', f'Канал {channel_id}')
        
        if channel_id not in channel_names:
            channel_names[channel_id] = channel_title
        channel_access[channel_id] = True
        channel_check_counts[channel_id] += 1
    except:
        try:
            if hasattr(message.peer_id, 'channel_id'):
                channel_id = message.peer_id.channel_id
            elif hasattr(message.peer_id, 'chat_id'):
                channel_id = message.peer_id.chat_id
            else:
                channel_id = None
            
            if channel_id:
                if channel_id not in channel_names:
                    channel_names[channel_id] = f'Канал {channel_id}'
                channel_access[channel_id] = True
                channel_check_counts[channel_id] += 1
        except:
            pass
    
    button_links = find_ref_links_in_buttons(message)
    
    if button_links:
        link = button_links[0]['url']
        await process_ref_link(event.client, link)
        return
    
    text_links = extract_links_from_message(message)
    if text_links:
        await process_ref_link(event.client, text_links[0])


def display_status():
    status_lines = []
    for channel_id in CHANNELS:
        name = channel_names.get(channel_id, f'Канал {abs(channel_id)}')
        has_access = channel_access.get(channel_id, False)
        access_status = "ДОСТУП ЕСТЬ" if has_access else "ДОСТУП НЕТ"
        status_lines.append(f"{name.upper()} - {access_status}")
    
    status_text = ' | '.join(status_lines)
    print(status_text)


def setup_api_credentials():
    print("\n" + "="*60)
    print("НАСТРОЙКА TELEGRAM API CREDENTIALS")
    print("="*60)
    print("\n1. Перейдите на https://my.telegram.org/apps")
    print("2. Войдите в свой аккаунт Telegram")
    print("3. Создайте новое приложение или используйте существующее")
    print("4. Скопируйте api_id и api_hash из раздела 'App configuration'")
    print("\n" + "-"*60 + "\n")
    
    api_id = input("Введите ваш API ID (api_id): ").strip()
    if not api_id:
        print("❌ API ID не может быть пустым!")
        return None, None
    
    try:
        api_id = int(api_id)
    except ValueError:
        print("❌ API ID должен быть числом!")
        return None, None
    
    api_hash = input("Введите ваш API Hash (api_hash): ").strip()
    if not api_hash:
        print("❌ API Hash не может быть пустым!")
        return None, None
    
    print("\n✅ API credentials получены!")
    return api_id, api_hash


async def main():
    api_id = TELEGRAM_API_ID
    api_hash = TELEGRAM_API_HASH
    
    if not api_id or not api_hash:
        print("\n⚠️ API credentials не найдены в конфигурации или .env файле")
        api_id, api_hash = setup_api_credentials()
        
        if not api_id or not api_hash:
            print("❌ Не удалось получить API credentials. Завершение работы.")
            return
    
    print("\n🚀 Запуск...")
    
    client = TelegramClient(SESSION_NAME, api_id, api_hash)
    
    try:
        phone_handler = TELEGRAM_PHONE_NUMBER if TELEGRAM_PHONE_NUMBER else None
        if not phone_handler:
            phone_handler = lambda: input('Введите номер телефона: ')
        
        await client.start(phone=phone_handler)
        
        print("\n✅ Подключение установлено!")
        
        me = await client.get_me()
        print(f"👤 Вы вошли как: {me.first_name}\n")
        
        transition_channel = "freaksredana"
        
        channels_need_subscription = []
        for channel_id in CHANNELS:
            try:
                channel_entity = await client.get_entity(channel_id)
                channel_title = getattr(channel_entity, 'title', None)
                if channel_title:
                    channel_names[channel_id] = channel_title
                else:
                    username = getattr(channel_entity, 'username', None)
                    if username:
                        channel_names[channel_id] = f"@{username}"
                    else:
                        channel_names[channel_id] = f'Канал {abs(channel_id)}'
                channel_access[channel_id] = True
            except Exception as e:
                channels_need_subscription.append(channel_id)
                channel_names[channel_id] = f'Канал {abs(channel_id)}'
                channel_access[channel_id] = False
        
        if channels_need_subscription:
            try:
                transition_entity = await client.get_entity(transition_channel)
                from telethon.tl.functions.channels import JoinChannelRequest
                await client(JoinChannelRequest(transition_entity))
                await asyncio.sleep(2)
                
                for channel_id in channels_need_subscription[:]:
                    try:
                        channel_entity = await client.get_entity(channel_id)
                        channel_title = getattr(channel_entity, 'title', None)
                        if channel_title:
                            channel_names[channel_id] = channel_title
                        else:
                            username = getattr(channel_entity, 'username', None)
                            if username:
                                channel_names[channel_id] = f"@{username}"
                        channel_access[channel_id] = True
                        channels_need_subscription.remove(channel_id)
                    except:
                        channel_access[channel_id] = False
            except Exception as e:
                pass
        
        print("\nждем чеки...")
        display_status()
        print()
        
        @client.on(events.NewMessage(chats=CHANNELS))
        async def channel_handler(event):
            await handle_new_message(event)
            display_status()
        
        @client.on(events.NewMessage(from_users=BOT_ID))
        async def bot_response_handler(event):
            global last_activated_check, pending_retries
            msg_text = event.message.text or event.message.raw_text or ""
            
            if INACTIVE_CHECK_PATTERN.search(msg_text):
                async with check_lock:
                    if last_activated_check:
                        check_code_to_mark = last_activated_check
                        inactive_checks.add(check_code_to_mark)
                        pending_retries.pop(check_code_to_mark, None)
                        logger.warning(f"❌ ЧЕК НЕАКТИВЕН: {check_code_to_mark}")
                        logger.info("🔍 Ищу новый чек...")
                        last_activated_check = None
            else:
                if last_activated_check:
                    current_check = last_activated_check
                    
                    should_retry = False
                    retry_link = None
                    
                    async with check_lock:
                        attempts = check_attempts.get(current_check, 1)
                        if attempts < MAX_ATTEMPTS and current_check in pending_retries:
                            should_retry = True
                            retry_link = pending_retries.pop(current_check)
                            check_attempts[current_check] += 1
                    
                    if should_retry and retry_link:
                        asyncio.create_task(retry_activation(client, current_check, retry_link))
                    else:
                        logger.info(f"✅ Ответ бота (чек {current_check})")
                        async with check_lock:
                            if last_activated_check == current_check:
                                last_activated_check = None
                            pending_retries.pop(current_check, None)
        
        await client.run_until_disconnected()
        
    except KeyboardInterrupt:
        print("\n\n⚠️ Остановка...")
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if client.is_connected():
            await client.disconnect()
            print("👋 Клиент отключен")


if __name__ == "__main__":
    asyncio.run(main())
