import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Telegram API credentials
API_ID = os.getenv('API_ID')
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN')

# Admin user IDs (comma-separated in .env file)
ADMIN_IDS = [int(id.strip()) for id in os.getenv('ADMIN_IDS', '').split(',') if id.strip()]

# Channel ID where approved posts will be sent
TARGET_CHANNEL = os.getenv('TARGET_CHANNEL')

# Post timeout in hours (default: 24)
POST_TIMEOUT_HOURS = int(os.getenv('POST_TIMEOUT_HOURS', '24'))

# Cooldown between posts in minutes (default: 30)
POST_COOLDOWN_MINUTES = int(os.getenv('POST_COOLDOWN_MINUTES', '30'))