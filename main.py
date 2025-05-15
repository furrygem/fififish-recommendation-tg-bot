from typing import Dict, Optional, Union
from telethon import TelegramClient, events
from telethon.tl.types import (
    User, BotCommand, BotCommandScopeDefault, InputPeerChannel
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

        if message.media:
            await client.send_file(
                channel,
                message.media,
                caption=message.text if message.text else None
            )
        else:
            await client.send_message(channel, message.text)
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

def can_user_post(user_id: int) -> tuple[bool, Optional[timedelta]]:
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

    for admin_id in ADMIN_IDS:
        try:
            admin_entity = await get_user_entity(admin_id)
            if admin_entity:
                await client.send_message(
                    admin_entity,
                    f"New post pending approval (ID: {post_id})\n"
                    f"From user: {event.sender_id}"
                )
                await client.forward_messages(admin_entity, event.message)
        except Exception as e:
            logger.error(f"Failed to forward to admin {admin_id}: {e}")

@client.on(events.NewMessage(pattern=r'^/approve\s+\d+$'))
async def approve_handler(event: events.NewMessage.Event) -> None:
    """Handle the /approve command."""
    if event.sender_id not in ADMIN_IDS:
        await event.respond("❌ This command is only available to admins.")
        return

    try:
        post_id = int(event.text.split()[1].strip())
        if post_id not in pending_posts:
            await event.respond(f"❌ Invalid post ID! Available IDs: {list(pending_posts.keys())}")
            return

        post = pending_posts[post_id]
        if not await send_to_channel(post['message']):
            await event.respond("❌ Failed to send to target channel. Please check the channel ID.")
            return

        await notify_user(post['user_id'], "✅ Your post has been approved and published!")
        del pending_posts[post_id]
        await event.respond("✅ Post approved and published!")

    except (ValueError, IndexError) as e:
        logger.error(f"Error in approve handler: {e}")
        await event.respond("❌ Please use format: /approve <post_id>")

@client.on(events.NewMessage(pattern=r'^/reject\s+\d+$'))
async def reject_handler(event: events.NewMessage.Event) -> None:
    """Handle the /reject command."""
    if event.sender_id not in ADMIN_IDS:
        await event.respond("❌ This command is only available to admins.")
        return

    try:
        post_id = int(event.text.split()[1].strip())
        if post_id not in pending_posts:
            await event.respond(f"❌ Invalid post ID! Available IDs: {list(pending_posts.keys())}")
            return

        post = pending_posts[post_id]
        await notify_user(post['user_id'], "❌ Your post has been rejected.")
        del pending_posts[post_id]
        await event.respond("✅ Post rejected!")

    except (ValueError, IndexError) as e:
        logger.error(f"Error in reject handler: {e}")
        await event.respond("❌ Please use format: /reject <post_id>")

async def main() -> None:
    """Start the bot."""
    await client.start(bot_token=BOT_TOKEN)
    await setup_commands()
    logger.info("Bot started!")
    await client.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main()) 