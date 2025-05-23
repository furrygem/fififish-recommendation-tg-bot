from typing import Dict, Optional, Union, Tuple
from telethon import TelegramClient, events, Button
from telethon.tl.types import (
    User, BotCommand, BotCommandScopeDefault, InputPeerChannel,
    MessageMediaPhoto, MessageMediaDocument
)
from telethon.tl.functions.bots import SetBotCommandsRequest
from telethon.tl.custom import Message
import asyncio
import logging
from datetime import datetime, timedelta
from config import (
    API_ID, API_HASH, BOT_TOKEN, ADMIN_IDS, TARGET_CHANNEL, POST_COOLDOWN_MINUTES
)

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize the client
client = TelegramClient('bot_session', API_ID, API_HASH)

# Store pending posts, user entities, and cooldowns
pending_posts: Dict[int, dict] = {}
user_entities: Dict[int, User] = {}
user_cooldowns: Dict[int, datetime] = {}

# Allowed image MIME types
ALLOWED_MIME_TYPES = {
    'image/jpeg',
    'image/png',
    'image/gif',
    'image/webp'
}

def is_image_file(message: Message) -> bool:
    """Check if the message contains an image file."""
    if isinstance(message.media, MessageMediaPhoto):
        return True
    
    if isinstance(message.media, MessageMediaDocument):
        mime_type = message.media.document.mime_type
        return mime_type in ALLOWED_MIME_TYPES
    
    return False

async def setup_commands() -> None:
    """Set up bot commands for all users."""
    commands = [
        BotCommand('start', 'Start the bot and get instructions'),
        BotCommand('help', 'Show help message'),
        BotCommand('approve', 'Approve a post (admin only)'),
        BotCommand('reject', 'Reject a post (admin only)')
    ]

    await client(SetBotCommandsRequest(
        scope=BotCommandScopeDefault(),
        lang_code='en',
        commands=commands
    ))

async def get_user_entity(user_id: int) -> Optional[User]:
    """Get user entity by ID with caching."""
    try:
        if user_id in user_entities:
            return user_entities[user_id]

        entity = await client.get_entity(user_id)
        if entity:
            user_entities[user_id] = entity
            return entity
    except Exception as e:
        logger.error(f"Failed to get user entity {user_id}: {e}")
    return None

async def get_channel_entity() -> Optional[Union[InputPeerChannel, User]]:
    """Get the target channel entity."""
    try:
        if TARGET_CHANNEL.startswith('@'):
            return await client.get_entity(TARGET_CHANNEL)
        
        channel_id = int(TARGET_CHANNEL)
        try:
            return await client.get_entity(channel_id)
        except ValueError:
            async for message in client.iter_messages(channel_id, limit=1):
                return message.peer_id
    except Exception as e:
        logger.error(f"Failed to get channel entity: {e}")
    return None

async def send_to_channel(message: Message) -> bool:
    """Send a message to the target channel without forwarding attribution."""
    try:
        channel = await get_channel_entity()
        if not channel:
            return False

        caption = "#предложка"

        if message.media:
            await client.send_file(
                channel,
                message.media,
                caption=caption
            )
        else:
            await client.send_message(channel, caption)
        return True
    except Exception as e:
        logger.error(f"Failed to send message to channel: {e}")
        return False

async def notify_user(user_id: int, message: str) -> None:
    """Send a notification to a user."""
    try:
        user_entity = await get_user_entity(user_id)
        if user_entity:
            await client.send_message(user_entity, message)
    except Exception as e:
        logger.error(f"Failed to notify user {user_id}: {e}")

def can_user_post(user_id: int) -> Tuple[bool, Optional[timedelta]]:
    """Check if a user can post based on cooldown."""
    if user_id not in user_cooldowns:
        return True, None
    
    cooldown_end = user_cooldowns[user_id]
    now = datetime.now()
    
    if now >= cooldown_end:
        return True, None
    
    remaining = cooldown_end - now
    return False, remaining

def set_user_cooldown(user_id: int) -> None:
    """Set cooldown for a user."""
    user_cooldowns[user_id] = datetime.now() + timedelta(minutes=POST_COOLDOWN_MINUTES)

@client.on(events.NewMessage(pattern='/start'))
async def start_handler(event: events.NewMessage.Event) -> None:
    """Handle the /start command."""
    await event.respond(
        "👋 Welcome! Send me an image and I'll forward it to the admins for approval."
    )

@client.on(events.NewMessage(pattern='/help'))
async def help_handler(event: events.NewMessage.Event) -> None:
    """Handle the /help command."""
    is_admin = event.sender_id in ADMIN_IDS
    help_text = (
        "🤖 Bot Commands:\n\n"
        "/start - Start the bot\n"
        "/help - Show this help message\n"
    )
    
    if is_admin:
        help_text += (
            "/approve <post_id> - Approve a post\n"
            "/reject <post_id> - Reject a post"
        )
    else:
        help_text += "\nSimply send an image to submit it for approval!"
    
    await event.respond(help_text)

@client.on(events.NewMessage(func=lambda e: e.media is not None))
async def media_handler(event: events.NewMessage.Event) -> None:
    """Handle incoming media messages."""
    if not isinstance(event.sender, User):
        return

    # Verify that the file is an image
    if not is_image_file(event.message):
        await event.respond(
            "❌ Please send only image files (JPEG, PNG, GIF, or WebP)."
        )
        return

    # Check cooldown
    can_post, remaining = can_user_post(event.sender_id)
    if not can_post:
        minutes = int(remaining.total_seconds() / 60)
        await event.respond(
            f"⏳ Please wait {minutes} minutes before submitting another post."
        )
        return

    user_entities[event.sender_id] = event.sender
    post_id = len(pending_posts)
    pending_posts[post_id] = {
        'user_id': event.sender_id,
        'message': event.message,
        'timestamp': datetime.now()
    }

    # Set cooldown
    set_user_cooldown(event.sender_id)

    await event.respond(
        "✅ Your post has been received and is pending approval by an admin."
    )

    # Create approve/reject buttons
    buttons = [
        [
            Button.inline("✅ Approve", f"approve_{post_id}"),
            Button.inline("❌ Reject", f"reject_{post_id}")
        ]
    ]

    for admin_id in ADMIN_IDS:
        try:
            admin_entity = await get_user_entity(admin_id)
            if admin_entity:
                await client.send_message(
                    admin_entity,
                    f"New post pending approval (ID: {post_id})\n"
                    f"From user: {event.sender_id}",
                    buttons=buttons
                )
                await client.forward_messages(admin_entity, event.message)
        except Exception as e:
            logger.error(f"Failed to forward to admin {admin_id}: {e}")

@client.on(events.CallbackQuery(pattern=r"^approve_(\d+)$"))
async def approve_callback(event: events.CallbackQuery.Event) -> None:
    """Handle approve button callback."""
    if event.sender_id not in ADMIN_IDS:
        await event.answer("❌ This action is only available to admins.", alert=True)
        return

    try:
        post_id = int(event.pattern_match.group(1))
        if post_id not in pending_posts:
            await event.answer("❌ Invalid post ID!", alert=True)
            return

        post = pending_posts[post_id]
        if not await send_to_channel(post['message']):
            await event.answer("❌ Failed to send to target channel!", alert=True)
            return

        await notify_user(post['user_id'], "✅ Your post has been approved and published!")
        del pending_posts[post_id]
        
        # Update the message to show it was approved
        await event.edit(
            f"✅ Post {post_id} has been approved and published!",
            buttons=None
        )
        await event.answer("Post approved!")

    except Exception as e:
        logger.error(f"Error in approve callback: {e}")
        await event.answer("❌ An error occurred!", alert=True)

@client.on(events.CallbackQuery(pattern=r"^reject_(\d+)$"))
async def reject_callback(event: events.CallbackQuery.Event) -> None:
    """Handle reject button callback."""
    if event.sender_id not in ADMIN_IDS:
        await event.answer("❌ This action is only available to admins.", alert=True)
        return

    try:
        post_id = int(event.pattern_match.group(1))
        if post_id not in pending_posts:
            await event.answer("❌ Invalid post ID!", alert=True)
            return

        post = pending_posts[post_id]
        await notify_user(post['user_id'], "❌ Your post has been rejected.")
        del pending_posts[post_id]
        
        # Update the message to show it was rejected
        await event.edit(
            f"❌ Post {post_id} has been rejected.",
            buttons=None
        )
        await event.answer("Post rejected!")

    except Exception as e:
        logger.error(f"Error in reject callback: {e}")
        await event.answer("❌ An error occurred!", alert=True)

async def main() -> None:
    """Start the bot."""
    await client.start(bot_token=BOT_TOKEN)
    await setup_commands()
    logger.info("Bot started!")
    await client.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main()) 