import os
from dotenv import load_dotenv

load_dotenv()

api_id_str = os.getenv('TELEGRAM_API_ID')
TELEGRAM_API_ID = int(api_id_str) if api_id_str else None
TELEGRAM_API_HASH = os.getenv('TELEGRAM_API_HASH')
TELEGRAM_PHONE_NUMBER = os.getenv('TELEGRAM_PHONE_NUMBER', '')

CHANNELS = [
    -1001335391393,
    -1002304081962,
    -1001077610923,
]

BOT_ID = 8208462989
