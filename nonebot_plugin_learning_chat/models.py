from nonebot import require

require("nonebot_plugin_orm")
from functools import cached_property

from nonebot_plugin_orm import Model
from sqlalchemy import JSON, ForeignKey
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship

try:
    import jieba_fast as jieba  # type: ignore
    import jieba_fast.analyse as jieba_analyse  # type: ignore
except ImportError:
    import jieba
    import jieba.analyse as jieba_analyse

from .config import config_manager

config = config_manager.config

jieba.setLogLevel(jieba.logging.INFO)
jieba.load_userdict(config.dictionary)  # 加载用户自定义的词典


class ChatMessage(Model):
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    """自增主键"""
    group_id: Mapped[int]
    """群id"""
    user_id: Mapped[int]
    """用户id"""
    message_id: Mapped[int]
    """消息id"""
    message: Mapped[str]
    """消息"""
    raw_message: Mapped[str]
    """原始消息"""
    plain_text: Mapped[str]
    """纯文本消息"""
    time: Mapped[int]
    """时间戳"""

    @cached_property
    def is_plain_text(self) -> bool:
        """是否纯文本"""
        return "[CQ:" not in self.message

    @cached_property
    def keyword_list(self) -> list[str]:
        """获取纯文本部分的关键词列表"""
        if not self.is_plain_text and not len(self.plain_text):
            return []
        return jieba_analyse.extract_tags(self.plain_text, topK=config.KEYWORDS_SIZE) # type: ignore

    @cached_property
    def keywords(self) -> str:
        """获取纯文本部分的关键词结果"""
        if not self.is_plain_text and not len(self.plain_text):
            return self.message
        return (
            self.message if len(self.keyword_list) < 2 else " ".join(self.keyword_list)
        )


class ChatContext(Model):
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    """自增主键"""
    keywords: Mapped[str]
    """关键词"""
    time: Mapped[int]
    """时间戳"""
    count: Mapped[int] = mapped_column(default=1)
    """次数"""
    answers: Mapped[list["ChatAnswer"]] = relationship(back_populates="context")
    """答案"""


class ChatAnswer(Model):
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    """自增主键"""
    keywords: Mapped[str]
    """关键词"""
    group_id: Mapped[int]
    """群id"""
    count: Mapped[int] = mapped_column(default=1)
    """次数"""
    time: Mapped[int]
    """时间戳"""
    messages: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(JSON),
        default=list,
    )
    """消息列表"""

    context_id: Mapped[int] = mapped_column(
        ForeignKey("nonebot_plugin_learning_chat_chatcontext.id"),
    )
    context: Mapped["ChatContext"] = relationship(back_populates="answers")

class ChatBlackList(Model):
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    """自增主键"""
    keywords: Mapped[str]
    """关键词"""
    global_ban: Mapped[bool] = mapped_column(default=False)
    """是否全局禁用"""
    ban_group_id: Mapped[list[int]] = mapped_column(
        MutableList.as_mutable(JSON), default=list,
    )
    """禁用的群id"""
