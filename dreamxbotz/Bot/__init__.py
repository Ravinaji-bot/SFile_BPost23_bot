import logging
import logging.config
from typing import Union, Optional, AsyncGenerator
from pyrogram import Client, types
from info import API_ID, API_HASH, BOT_TOKEN, SESSION, POST_BOT_TOKEN

logger = logging.getLogger(__name__)

class dreamcinezoneXBot(Client):

    def __init__(self):
        super().__init__(
            name=SESSION,
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            workers=60,
            plugins=dict(root= "plugins"),
            sleep_threshold=5,
        )
    async def iter_messages(self, chat_id: Union[int, str], limit: int, offset: int = 0,) -> Optional[AsyncGenerator["types.Message", None]]:
        """Iterate through a chat sequentially.
        This convenience method does the same as repeatedly calling :meth:`~pyrogram.Client.get_messages` in a loop, thus saving
        you from the hassle of setting up boilerplate code. It is useful for getting the whole chat messages with a
        single call.
        Parameters:
            chat_id (``int`` | ``str``):
                Unique identifier (int) or username (str) of the target chat.
                For your personal cloud (Saved Messages) you can simply use "me" or "self".
                For a contact that exists in your Telegram address book you can use his phone number (str).
                
            limit (``int``):
                Identifier of the last message to be returned.
                
            offset (``int``, *optional*):
                Identifier of the first message to be returned.
                Defaults to 0.
        Returns:
            ``Generator``: A generator yielding :obj:`~pyrogram.types.Message` objects.
        Example:
            .. code-block:: python
                for message in app.iter_messages("pyrogram", 1, 15000):
                    logger.info(message.text)
        """
        current = offset
        while True:
            new_diff = min(200, limit - current)
            if new_diff <= 0:
                return
            messages = await self.get_messages(chat_id, list(range(current, current+new_diff+1)))
            for message in messages:
                yield message
                current += 1
      
dreamxbotz = dreamcinezoneXBot()

class PostHelperBot(Client):
    """2nd bot: the 'Click here' links of the post cards open this bot. It runs only the /start + file
    delivery plugin (plugins/commands.py) and uses the same database as the main bot."""
    def __init__(self, token):
        super().__init__(
            name=f"{SESSION}_posthelper",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=token,
            workers=30,
            plugins=dict(root="plugins", include=["commands", "banned"]),
            sleep_threshold=5,
            in_memory=True,
        )

post_bot = PostHelperBot(POST_BOT_TOKEN) if POST_BOT_TOKEN else None

multi_clients = {}
work_loads = {}
