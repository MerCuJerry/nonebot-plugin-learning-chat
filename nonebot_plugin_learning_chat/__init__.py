from nonebot import require

require("nonebot_plugin_orm")
import random
import time
from asyncio import sleep

from nonebot import get_adapter, logger, on_message, require
from nonebot.adapters.milky import (
    Adapter,
    Message,
)
from nonebot.adapters.milky.event import GroupMessageEvent
from nonebot.adapters.milky.exception import ActionFailed
from nonebot.adapters.milky.permission import GROUP
from nonebot.matcher import Matcher
from nonebot.params import Arg
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule
from nonebot.typing import T_State
from nonebot_plugin_orm import get_session

from . import web_api as web_api
from . import web_page as web_page
from .config import NICKNAME, config_manager
from .handler import LearningChat
from .models import ChatMessage

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

__plugin_meta__ = PluginMetadata(
    name="群聊学习",
    description="学习群友们的发言、复读以及主动发言",
    usage="详见README",
    type="application",
    homepage="https://github.com/CMHopeSunshine/nonebot-plugin-learning-chat",
    supported_adapters={"~milky"},
    extra={"author": "惜月"},
)


async def ChatRule(event: GroupMessageEvent, state: T_State) -> bool:
    if answers := await LearningChat(event).answer():
        state["answers"] = answers
        return True
    return False

async def NotMe(event: GroupMessageEvent) -> bool:
    return event.data.sender_id != event.self_id

learning_chat = on_message(
    priority=99,
    block=False,
    rule=Rule(NotMe) & Rule(ChatRule),
    permission=GROUP,
    state={
        "pm_name": "群聊学习",
        "pm_description": "(被动技能)bot会学习群友们的发言",
        "pm_usage": "群聊学习",
        "pm_priority": 1,
    },
)


@learning_chat.handle()
async def _(matcher: Matcher, event: GroupMessageEvent, answers=Arg("answers")):
    for answer in answers:
        try:
            logger.info(
                "群聊学习", f'{NICKNAME}将向群<m>{event.data.peer_id}</m>回复<m>"{answer}"</m>'
            )
            msg = await matcher.send(Message(answer))
            async with get_session(expire_on_commit=False) as session:
                session.add(
                    ChatMessage(
                        group_id=event.data.peer_id,
                        user_id=event.data.sender_id,
                        message_id=msg.message_seq,
                        message=answer,
                        raw_message=answer,
                        time=int(time.time()),
                        plain_text=Message(answer).extract_plain_text(),
                    )
                )
                await session.commit()
            await sleep(random.random() + 0.5)
        except ActionFailed:
            logger.info(
                "群聊学习",
                f'{NICKNAME}向群<m>{event.data.peer_id}</m>的回复<m>"{answer}"</m>发送<r>失败，可能处于风控中</r>',
            )
    await matcher.finish()


@scheduler.scheduled_job("interval", minutes=3, misfire_grace_time=5)
async def speak_up():
    if not config_manager.config.total_enable:
        return
    try:
        bots = get_adapter(Adapter).bots
        if len(bots) == 0:
            return
        bot = next(iter(bots.values()))
    except ValueError:
        return
    if not (speak := await LearningChat.speak(int(bot.self_id))):
        return
    group_id, messages = speak
    for msg in messages:
        try:
            logger.info("群聊学习", f'{NICKNAME}向群<m>{group_id}</m>主动发言<m>"{msg}"</m>')
            send_result = await bot.send_group_msg(
                group_id=group_id, message=Message(msg)
            )
            async with get_session(expire_on_commit=False) as session:
                session.add(
                    ChatMessage(
                        group_id=group_id,
                        user_id=int(bot.self_id),
                        message_id=send_result.message_seq,
                        message=msg,
                        raw_message=msg,
                        time=int(time.time()),
                        plain_text=Message(msg).extract_plain_text(),
                    )
                )
                await session.commit()
            await sleep(random.randint(2, 4))
        except ActionFailed:
            logger.info(
                "群聊学习",
                f'{NICKNAME}向群<m>{group_id}</m>主动发言<m>"{msg}"</m><r>发送失败，可能处于风控中</r>',
            )
