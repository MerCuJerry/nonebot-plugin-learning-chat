import datetime
import inspect
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any

from nicegui import app, ui
from nonebot import get_adapter
from nonebot.adapters.milky import Adapter
from nonebot_plugin_orm import get_session
from sqlalchemy import delete, false, func, select, update

try:
    import jieba_fast as jieba  # type: ignore
except ImportError:
    import jieba

from .config import NICKNAME, config_manager
from .handler import LearningChat
from .models import ChatAnswer, ChatBlackList, ChatContext, ChatMessage

PER_PAGE = 10


# ---------------------------------------------------------------------------
# auth helpers
# ---------------------------------------------------------------------------
def is_authenticated() -> bool:
    """Return whether the current browser session has already logged in."""
    return bool(app.storage.user.get("authenticated"))


def logout() -> None:
    app.storage.user.clear()
    ui.navigate.to("/login")


# ---------------------------------------------------------------------------
# generic helpers
# ---------------------------------------------------------------------------
def confirm(title: str, message: str, on_confirm: Callable[[], Any]) -> None:
    """Show a confirmation dialog and run ``on_confirm`` after the user agrees."""
    with ui.dialog() as dialog, ui.card().classes("p-4"):
        ui.label(title).classes("text-lg font-bold")
        ui.label(message)
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("取消", on_click=dialog.close)

            async def do_confirm() -> None:
                dialog.close()
                result = on_confirm()
                if inspect.isawaitable(result):
                    await result

            ui.button("确定", on_click=do_confirm).props("color=positive")
    dialog.open()


def format_time(ts: int) -> str:
    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _int(value: float | None, default: int) -> int:
    """Coerce a numeric input value to ``int``, falling back to ``default``."""
    return default if value is None else int(value)


def _float(value: float | None, default: float) -> float:
    """Coerce a numeric input value to ``float``, falling back to ``default``."""
    return default if value is None else float(value)


def _int_list(values: list[str]) -> list[int]:
    """Coerce a list of string chips to ``int``, dropping non-numeric entries."""
    result: list[int] = []
    for value in values:
        try:
            result.append(int(value))
        except ValueError:
            continue
    return result


def _int_condition(column: Any, value: str | None) -> Any:
    """Exact-match condition for an integer column.

    Unparseable input yields a never-true condition so invalid searches
    return no rows instead of everything.
    """
    return column == int(value) if value and value.isdigit() else false()


async def build_data_table(
    *,
    columns: list[dict],
    fetch: Callable[[dict, int], Awaitable[tuple[list[dict], int]]],
    search_fields: list[tuple[str, str, str]] | None = None,
    toolbar_buttons: list[tuple[str, str, Callable[[], Any]]] | None = None,
    row_buttons: list[tuple[str, str, Callable[[dict], Any]]] | None = None,
    row_key: str = "id",
) -> None:
    """Build a paginated, searchable table with single-row selection.

    ``fetch(filters, page)`` returns ``(rows, total)``; the filters dict is
    built from the search fields (field name -> input value). Row/toolbar
    button callbacks may be sync or async and receive the selected row (or no
    argument for toolbar buttons).
    """
    state = {"page": 1, "total_pages": 1, "table": None}
    search_inputs: dict[str, ui.input] = {}

    with ui.column().classes("w-full gap-2"):
        with ui.row().classes("w-full items-center gap-2 flex-wrap"):
            for key, label, placeholder in search_fields or []:
                search_inputs[key] = ui.input(label, placeholder=placeholder)

            def do_search() -> None:
                state["page"] = 1
                content.refresh()

            ui.button("搜索", icon="search", on_click=do_search)

            for label, icon, callback in toolbar_buttons or []:
                ui.button(label, icon=icon, on_click=callback).props("color=negative")

        @ui.refreshable
        async def content() -> None:
            filters = {k: inp.value for k, inp in search_inputs.items() if inp.value}
            rows, total = await fetch(filters, state["page"])
            state["total_pages"] = max((total + PER_PAGE - 1) // PER_PAGE, 1)
            if state["page"] > state["total_pages"]:
                state["page"] = max(state["total_pages"], 1)
                rows, total = await fetch(filters, state["page"])
            state["table"] = ui.table(
                columns=columns,
                rows=rows,
                row_key=row_key,
                selection="single",
            )
            count_label.text = f"共 {total} 条"
            page_label.text = f"{state['page']} / {state['total_pages']}"

        def goto(page: int) -> None:
            state["page"] = min(max(page, 1), state["total_pages"])
            content.refresh()

        async def require_row(callback: Callable[[dict], Any]) -> None:
            table = state["table"]
            if not table or not table.selected:
                ui.notify("请先选择一行", type="warning")
                return
            result = callback(table.selected[0])
            if inspect.isawaitable(result):
                await result

        with ui.row().classes("w-full items-center justify-between"):
            count_label = ui.label("")
            with ui.row().classes("items-center gap-1"):
                ui.button(
                    icon="chevron_left",
                    on_click=lambda: goto(state["page"] - 1),
                ).props("flat dense")
                page_label = ui.label("")
                ui.button(
                    icon="chevron_right",
                    on_click=lambda: goto(state["page"] + 1),
                ).props("flat dense")

        if row_buttons:
            with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                for label, icon, callback in row_buttons:
                    ui.button(
                        label,
                        icon=icon,
                        on_click=partial(require_row, callback),
                    ).props("outline dense")

        await content()


# ---------------------------------------------------------------------------
# pages
# ---------------------------------------------------------------------------
@ui.page("/")
def index() -> None:
    ui.navigate.to("/login")


@ui.page("/login")
def login_page() -> None:
    if is_authenticated():
        ui.navigate.to("/admin")
        return
    with (
        ui.column().classes("absolute-center w-96 max-w-full"),
        ui.card().classes("w-full p-6"),
    ):
        ui.label("Learning-Chat 后台管理").classes(
            "text-2xl font-bold text-center mb-2",
        )
        ui.label("Nonebot-Plugin-Learning-Chat 控制台").classes(
            "text-sm text-gray-500 text-center mb-4",
        )
        username = ui.input("用户名", placeholder="后台管理用户名，默认为 chat")
        password = ui.input(
            "密码", password=True, placeholder="后台管理密码，默认为 admin"
        )

        def do_login() -> None:
            cfg = config_manager.config
            if (
                username.value == cfg.web_username
                and password.value == cfg.web_password
            ):
                app.storage.user["authenticated"] = True
                ui.navigate.to("/admin")
            else:
                ui.notify("登录失败，请确认用户名和密码无误", type="negative")

        ui.button("登录", icon="login", on_click=do_login).props(
            "color=primary",
        ).classes("w-full")


def build_configs() -> None:
    """Global config form and per-group config form."""
    with ui.card().classes("w-full p-4"):
        ui.label("全局配置").classes("text-lg font-bold")
        with ui.column().classes("w-full gap-3"):
            total_enable = ui.switch(
                "群聊学习总开关",
                value=config_manager.config.total_enable,
            )
            enable_web = ui.switch(
                "后台管理总开关",
                value=config_manager.config.enable_web,
            )
            web_username = ui.input(
                "后台管理用户名",
                value=config_manager.config.web_username,
            )
            web_password = ui.input(
                "后台管理密码",
                value=config_manager.config.web_password,
                password=True,
            )
            web_secret_key = ui.input(
                "后台管理 token 密钥",
                value=config_manager.config.web_secret_key,
            )
            keywords_size = ui.number(
                "单句关键词数量",
                value=config_manager.config.KEYWORDS_SIZE,
                min=2,
                precision=0,
            )
            cross_group_threshold = ui.number(
                "跨群回复阈值",
                value=config_manager.config.cross_group_threshold,
                min=1,
                precision=0,
            )
            learn_max_count = ui.number(
                "最高学习次数",
                value=config_manager.config.learn_max_count,
                min=2,
                precision=0,
            )
            ban_words = ui.input_chips(
                "全局屏蔽词",
                value=config_manager.config.ban_words,
            )
            ban_users = ui.input_chips(
                "全局屏蔽用户",
                value=[str(u) for u in config_manager.config.ban_users],
            )
            dictionary = ui.input_chips(
                "自定义词典",
                value=config_manager.config.dictionary,
            )

        async def save_global() -> None:
            cfg = config_manager.config
            cfg.update(
                total_enable=total_enable.value,
                enable_web=enable_web.value,
                web_username=web_username.value,
                web_password=web_password.value,
                web_secret_key=web_secret_key.value,
                KEYWORDS_SIZE=_int(keywords_size.value, cfg.KEYWORDS_SIZE),
                cross_group_threshold=_int(
                    cross_group_threshold.value,
                    cfg.cross_group_threshold,
                ),
                learn_max_count=_int(learn_max_count.value, cfg.learn_max_count),
                ban_words=ban_words.value,
                ban_users=_int_list(ban_users.value),
                dictionary=dictionary.value,
            )
            config_manager.save()
            async with get_session() as session:
                await session.execute(
                    update(ChatContext)
                    .where(ChatContext.count > cfg.learn_max_count)
                    .values(count=cfg.learn_max_count),
                )
                await session.execute(
                    update(ChatAnswer)
                    .where(ChatAnswer.count > cfg.learn_max_count)
                    .values(count=cfg.learn_max_count),
                )
                await session.commit()
            jieba.load_userdict(cfg.dictionary)
            ui.notify("保存成功", type="positive")

        with ui.row().classes("gap-2"):
            ui.button("保存全局配置", icon="save", on_click=save_global).props(
                "color=positive",
            )

    with ui.card().classes("w-full p-4"):
        ui.label("分群配置").classes("text-lg font-bold")
        group_select = ui.select([], label="选择群")
        group_panel = ui.column().classes("w-full")

        async def load_groups() -> None:
            try:
                bots = get_adapter(Adapter).bots
                if not bots:
                    ui.notify("获取群列表失败，请确认已连接 GO-CQHTTP", type="warning")
                    return
                bot = next(iter(bots.values()))
                groups = await bot.get_group_list()
                group_select.options = [
                    (f"{g.group_name}({g.group_id})", g.group_id) for g in groups
                ]
                group_select.update()
            except Exception as e:
                ui.notify(f"获取群列表失败: {e}", type="warning")

        def on_group_change() -> None:
            group_id = group_select.value
            group_panel.clear()
            if group_id is not None:
                build_group_config(group_id, group_panel)

        group_select.on_value_change(on_group_change)
        ui.timer(0.1, load_groups, once=True)


def build_group_config(group_id: int, slot: ui.column) -> None:
    """Build the config form for a single group inside ``slot``."""
    gc = config_manager.get_group_config(group_id)
    with slot:
        with ui.column().classes("w-full gap-3"):
            enable = ui.switch("群聊学习开关", value=gc.enable)
            answer_threshold = ui.number(
                "回复阈值",
                value=gc.answer_threshold,
                min=2,
                precision=0,
            )
            weights = ui.input_chips(
                "回复阈值权重",
                value=[str(w) for w in gc.answer_threshold_weights],
            )
            repeat_threshold = ui.number(
                "复读阈值",
                value=gc.repeat_threshold,
                min=2,
                precision=0,
            )
            break_probability = ui.number(
                "打断复读概率",
                value=gc.break_probability * 100,
                min=0,
                max=100,
                precision=0,
                suffix="%",
            )
            ban_words = ui.input_chips("屏蔽词", value=gc.ban_words)
            ban_users = ui.input_chips(
                "屏蔽用户",
                value=[str(u) for u in gc.ban_users],
            )
            speak_enable = ui.switch("主动发言开关", value=gc.speak_enable)
            speak_threshold = ui.number(
                "主动发言阈值",
                value=gc.speak_threshold,
                min=0,
                precision=0,
            )
            speak_min_interval = ui.number(
                "主动发言最小间隔",
                value=gc.speak_min_interval,
                min=0,
                precision=0,
                suffix="秒",
            )
            speak_continuously_probability = ui.number(
                "连续主动发言概率",
                value=gc.speak_continuously_probability * 100,
                min=0,
                max=100,
                precision=0,
                suffix="%",
            )
            speak_continuously_max_len = ui.number(
                "最大连续主动发言句数",
                value=gc.speak_continuously_max_len,
                min=1,
                precision=0,
            )
            speak_poke_probability = ui.number(
                "主动发言附带戳一戳概率",
                value=gc.speak_poke_probability * 100,
                min=0,
                max=100,
                precision=0,
                suffix="%",
            )

        def collect() -> dict | None:
            try:
                weights_list = [int(w) for w in weights.value]
            except ValueError:
                ui.notify("回复阈值权重必须为数字", type="negative")
                return None
            if not weights_list:
                ui.notify("回复阈值权重不能为空，必须至少有一个数值", type="negative")
                return None
            return {
                "enable": enable.value,
                "answer_threshold": _int(answer_threshold.value, gc.answer_threshold),
                "answer_threshold_weights": weights_list,
                "repeat_threshold": _int(repeat_threshold.value, gc.repeat_threshold),
                "break_probability": _float(
                    break_probability.value,
                    gc.break_probability * 100,
                )
                / 100,
                "ban_words": ban_words.value,
                "ban_users": _int_list(ban_users.value),
                "speak_enable": speak_enable.value,
                "speak_threshold": _int(speak_threshold.value, gc.speak_threshold),
                "speak_min_interval": _int(
                    speak_min_interval.value,
                    gc.speak_min_interval,
                ),
                "speak_continuously_probability": _float(
                    speak_continuously_probability.value,
                    gc.speak_continuously_probability * 100,
                )
                / 100,
                "speak_continuously_max_len": _int(
                    speak_continuously_max_len.value,
                    gc.speak_continuously_max_len,
                ),
                "speak_poke_probability": _float(
                    speak_poke_probability.value,
                    gc.speak_poke_probability * 100,
                )
                / 100,
            }

        def save() -> None:
            data = collect()
            if data is None:
                return
            gc.update(**data)
            config_manager.save()
            ui.notify("保存成功", type="positive")

        async def save_all() -> None:
            data = collect()
            if data is None:
                return
            try:
                bots = get_adapter(Adapter).bots
                if not bots:
                    ui.notify("获取群列表失败，请确认已连接 GO-CQHTTP", type="warning")
                    return
                bot = next(iter(bots.values()))
                groups = await bot.get_group_list()
            except Exception as e:
                ui.notify(f"获取群列表失败: {e}", type="warning")
                return
            for group in groups:
                cfg = config_manager.get_group_config(int(group.group_id))
                cfg.update(**data)
                config_manager.config.group_config[int(group.group_id)] = cfg
            config_manager.save()
            ui.notify("已保存至所有群", type="positive")

        def reset() -> None:
            slot.clear()
            build_group_config(group_id, slot)

        with ui.row().classes("gap-2"):
            ui.button("保存", icon="save", on_click=save).props("color=positive")
            ui.button(
                "保存至所有群",
                on_click=lambda: confirm(
                    "保存至所有群",
                    "确认将当前配置保存至所有群？",
                    save_all,
                ),
            ).props("color=primary")
            ui.button("重置", icon="restart_alt", on_click=reset)


async def build_messages_page() -> None:
    """Message records table."""
    with ui.card().classes("w-full p-4"):
        ui.label("群聊消息").classes("text-lg font-bold")
        ui.label(
            f"此数据库记录了{NICKNAME}收到的聊天记录。"
            f"可以通过搜索{NICKNAME}的QQ号来查看它的回复记录。",
        ).classes("text-sm text-gray-500")

        async def fetch_messages(filters: dict, page: int) -> tuple[list[dict], int]:
            conditions = [
                _int_condition(ChatMessage.group_id, filters.get("group_id")),
                _int_condition(ChatMessage.user_id, filters.get("user_id")),
            ]
            if raw := filters.get("raw_message"):
                conditions.append(ChatMessage.raw_message.contains(raw))
            async with get_session() as session:
                total = (
                    await session.scalar(
                        select(func.count())
                        .select_from(ChatMessage)
                        .where(*conditions),
                    )
                ) or 0
                items = (
                    await session.execute(
                        select(
                            ChatMessage.id,
                            ChatMessage.message_id,
                            ChatMessage.group_id,
                            ChatMessage.user_id,
                            ChatMessage.raw_message,
                            ChatMessage.time,
                        )
                        .where(*conditions)
                        .order_by(ChatMessage.time.desc())
                        .offset((page - 1) * PER_PAGE)
                        .limit(PER_PAGE),
                    )
                ).all()
            rows = [
                {
                    "id": m.id,
                    "message_id": m.message_id,
                    "group_id": m.group_id,
                    "user_id": m.user_id,
                    "message": (m.raw_message or "")[:40],
                    "raw_message": m.raw_message,
                    "time": format_time(m.time),
                }
                for m in items
            ]
            return rows, total

        async def ban_selected(row: dict) -> None:
            try:
                async with get_session() as session:
                    data = await session.get(ChatMessage, row["id"])
                if data is None:
                    ui.notify("禁用失败: 记录不存在", type="negative")
                    return
                await LearningChat.add_ban(data)
                ui.notify("禁用成功", type="positive")
            except Exception as e:
                ui.notify(f"禁用失败: {e}", type="negative")

        async def delete_selected(row: dict) -> None:
            try:
                async with get_session() as session:
                    await session.execute(
                        delete(ChatMessage).where(ChatMessage.id == row["id"]),
                    )
                    await session.commit()
                ui.notify("删除成功", type="positive")
            except Exception as e:
                ui.notify(f"删除失败: {e}", type="negative")

        async def delete_all() -> None:
            try:
                async with get_session() as session:
                    await session.execute(delete(ChatMessage))
                    await session.commit()
                ui.notify("已删除所有聊天记录", type="positive")
            except Exception as e:
                ui.notify(f"删除失败: {e}", type="negative")

        def show_detail(row: dict) -> None:
            with ui.dialog() as dialog, ui.card().classes("p-4"):
                ui.label("消息全文").classes("text-lg font-bold")
                ui.label(row["raw_message"] or "").classes(
                    "whitespace-pre-wrap break-all",
                )
                with ui.row().classes("w-full justify-end"):
                    ui.button("关闭", on_click=dialog.close)
            dialog.open()

        await build_data_table(
            columns=[
                {"name": "id", "label": "ID", "field": "id", "align": "left"},
                {"name": "message_id", "label": "消息ID", "field": "message_id"},
                {"name": "group_id", "label": "群ID", "field": "group_id"},
                {"name": "user_id", "label": "用户ID", "field": "user_id"},
                {"name": "message", "label": "消息", "field": "message"},
                {"name": "time", "label": "时间", "field": "time"},
            ],
            fetch=fetch_messages,
            search_fields=[
                ("group_id", "群 ID", "搜索群 ID"),
                ("user_id", "用户 ID", "搜索用户 ID"),
                ("raw_message", "消息", "搜索消息"),
            ],
            toolbar_buttons=[
                (
                    "删除所有聊天记录",
                    "delete_sweep",
                    lambda: confirm(
                        "删除所有聊天记录",
                        "确定要删除所有聊天记录吗？",
                        delete_all,
                    ),
                ),
            ],
            row_buttons=[
                ("查看全文", "visibility", show_detail),
                (
                    "禁用",
                    "block",
                    lambda row: confirm(
                        "禁用聊天记录",
                        "禁用该聊天记录相关的学习内容和回复？",
                        lambda: ban_selected(row),
                    ),
                ),
                (
                    "删除",
                    "delete",
                    lambda row: confirm(
                        "删除聊天记录",
                        "删除该条聊天记录？",
                        lambda: delete_selected(row),
                    ),
                ),
            ],
        )


async def build_contexts_page() -> None:
    """Learned content table."""
    with ui.card().classes("w-full p-4"):
        ui.label("学习内容").classes("text-lg font-bold")
        ui.label(
            "此数据库记录了NICKNAME所学习的内容，可以查看每条内容已学习到的回复。",
        ).classes("text-sm text-gray-500")

        async def fetch_contexts(filters: dict, page: int) -> tuple[list[dict], int]:
            conditions = []
            if kw := filters.get("keywords"):
                conditions.append(ChatContext.keywords.contains(kw))
            async with get_session() as session:
                total = (
                    await session.scalar(
                        select(func.count())
                        .select_from(ChatContext)
                        .where(*conditions),
                    )
                ) or 0
                items = (
                    await session.execute(
                        select(
                            ChatContext.id,
                            ChatContext.keywords,
                            ChatContext.time,
                            ChatContext.count,
                        )
                        .where(*conditions)
                        .order_by(ChatContext.time.desc())
                        .offset((page - 1) * PER_PAGE)
                        .limit(PER_PAGE),
                    )
                ).all()
            rows = [
                {
                    "id": c.id,
                    "keywords": (c.keywords or "")[:40],
                    "full_keywords": c.keywords,
                    "time": format_time(c.time),
                    "count": c.count,
                }
                for c in items
            ]
            return rows, total

        async def show_answers(row: dict) -> None:
            async with get_session() as session:
                answers = (
                    await session.execute(
                        select(ChatAnswer.keywords, ChatAnswer.count)
                        .where(ChatAnswer.context_id == row["id"])
                        .order_by(ChatAnswer.count.desc()),
                    )
                ).all()
            with ui.dialog() as dialog, ui.card().classes("p-4 w-[640px] max-w-full"):
                ui.label("回复列表").classes("text-lg font-bold")
                ui.label(row["full_keywords"] or "").classes(
                    "text-sm text-gray-500 break-all",
                )
                if not answers:
                    ui.label("暂无回复").classes("text-gray-500")
                for answer in answers:
                    with ui.row().classes("w-full items-start gap-2 border-b py-1"):
                        ui.label(f"x{answer.count}").classes("w-12")
                        ui.label((answer.keywords or "")[:40]).classes(
                            "flex-1 break-all",
                        )
                with ui.row().classes("w-full justify-end"):
                    ui.button("关闭", on_click=dialog.close)
            dialog.open()

        async def ban_selected(row: dict) -> None:
            try:
                async with get_session() as session:
                    data = await session.get(ChatContext, row["id"])
                if data is None:
                    ui.notify("禁用失败: 记录不存在", type="negative")
                    return
                await LearningChat.add_ban(data)
                ui.notify("禁用成功", type="positive")
            except Exception as e:
                ui.notify(f"禁用失败: {e}", type="negative")

        async def delete_selected(row: dict) -> None:
            try:
                async with get_session() as session:
                    await session.execute(
                        delete(ChatAnswer).where(ChatAnswer.context_id == row["id"]),
                    )
                    await session.execute(
                        delete(ChatContext).where(ChatContext.id == row["id"]),
                    )
                    await session.commit()
                ui.notify("删除成功", type="positive")
            except Exception as e:
                ui.notify(f"删除失败: {e}", type="negative")

        async def delete_all() -> None:
            try:
                async with get_session() as session:
                    await session.execute(delete(ChatContext))
                    await session.commit()
                ui.notify("已删除所有学习内容", type="positive")
            except Exception as e:
                ui.notify(f"删除失败: {e}", type="negative")

        await build_data_table(
            columns=[
                {"name": "id", "label": "ID", "field": "id", "align": "left"},
                {"name": "keywords", "label": "内容/关键词", "field": "keywords"},
                {"name": "time", "label": "最后学习时间", "field": "time"},
                {"name": "count", "label": "已学次数", "field": "count"},
            ],
            fetch=fetch_contexts,
            search_fields=[("keywords", "内容/关键词", "搜索内容/关键词")],
            toolbar_buttons=[
                (
                    "删除所有学习内容",
                    "delete_sweep",
                    lambda: confirm(
                        "删除所有学习内容",
                        "确定要删除所有已学习的内容吗？",
                        delete_all,
                    ),
                ),
            ],
            row_buttons=[
                ("回复列表", "menu_book", show_answers),
                (
                    "禁用",
                    "block",
                    lambda row: confirm(
                        "禁用学习内容",
                        "禁用并删除该学习的内容及其所有回复？",
                        lambda: ban_selected(row),
                    ),
                ),
                (
                    "删除",
                    "delete",
                    lambda row: confirm(
                        "删除学习内容",
                        "仅删除该学习的内容及其所有回复，但不禁用？",
                        lambda: delete_selected(row),
                    ),
                ),
            ],
        )


async def build_answers_page() -> None:
    """Learned answers table."""
    with ui.card().classes("w-full p-4"):
        ui.label("内容回复").classes("text-lg font-bold")
        ui.label(
            "此数据库记录了NICKNAME已学习到的所有回复，推荐到「学习内容」页进行操作。",
        ).classes("text-sm text-gray-500")

        async def fetch_answers(filters: dict, page: int) -> tuple[list[dict], int]:
            conditions = [_int_condition(ChatAnswer.group_id, filters.get("group_id"))]
            if kw := filters.get("keywords"):
                conditions.append(ChatAnswer.keywords.contains(kw))
            async with get_session() as session:
                total = (
                    await session.scalar(
                        select(func.count()).select_from(ChatAnswer).where(*conditions),
                    )
                ) or 0
                items = (
                    await session.execute(
                        select(
                            ChatAnswer.id,
                            ChatAnswer.group_id,
                            ChatAnswer.keywords,
                            ChatAnswer.time,
                            ChatAnswer.count,
                            ChatAnswer.messages,
                        )
                        .where(*conditions)
                        .order_by(ChatAnswer.count.desc())
                        .offset((page - 1) * PER_PAGE)
                        .limit(PER_PAGE),
                    )
                ).all()
            rows = [
                {
                    "id": a.id,
                    "group_id": a.group_id,
                    "keywords": (a.keywords or "")[:40],
                    "full_keywords": a.keywords,
                    "time": format_time(a.time),
                    "count": a.count,
                    "messages": a.messages,
                }
                for a in items
            ]
            return rows, total

        def show_messages(row: dict) -> None:
            with ui.dialog() as dialog, ui.card().classes("p-4 w-[640px] max-w-full"):
                ui.label("完整消息").classes("text-lg font-bold")
                if not row["messages"]:
                    ui.label("暂无消息").classes("text-gray-500")
                for msg in row["messages"]:
                    ui.label(msg).classes("break-all border-b py-1")
                with ui.row().classes("w-full justify-end"):
                    ui.button("关闭", on_click=dialog.close)
            dialog.open()

        async def ban_selected(row: dict) -> None:
            try:
                async with get_session() as session:
                    data = await session.get(ChatAnswer, row["id"])
                if data is None:
                    ui.notify("禁用失败: 记录不存在", type="negative")
                    return
                await LearningChat.add_ban(data)
                ui.notify("禁用成功", type="positive")
            except Exception as e:
                ui.notify(f"禁用失败: {e}", type="negative")

        async def delete_selected(row: dict) -> None:
            try:
                async with get_session() as session:
                    await session.execute(
                        delete(ChatAnswer).where(ChatAnswer.id == row["id"]),
                    )
                    await session.commit()
                ui.notify("删除成功", type="positive")
            except Exception as e:
                ui.notify(f"删除失败: {e}", type="negative")

        async def delete_all() -> None:
            try:
                async with get_session() as session:
                    await session.execute(delete(ChatAnswer))
                    await session.commit()
                ui.notify("已删除所有已学习的回复", type="positive")
            except Exception as e:
                ui.notify(f"删除失败: {e}", type="negative")

        await build_data_table(
            columns=[
                {"name": "id", "label": "ID", "field": "id", "align": "left"},
                {"name": "group_id", "label": "群ID", "field": "group_id"},
                {"name": "keywords", "label": "内容/关键词", "field": "keywords"},
                {"name": "time", "label": "最后学习时间", "field": "time"},
                {"name": "count", "label": "次数", "field": "count"},
            ],
            fetch=fetch_answers,
            search_fields=[
                ("group_id", "群 ID", "搜索群 ID"),
                ("keywords", "内容/关键词", "搜索内容/关键词"),
            ],
            toolbar_buttons=[
                (
                    "删除所有已学习的回复",
                    "delete_sweep",
                    lambda: confirm(
                        "删除所有已学习的回复",
                        "确定要删除所有已学习的回复吗？",
                        delete_all,
                    ),
                ),
            ],
            row_buttons=[
                ("完整消息", "message", show_messages),
                (
                    "禁用",
                    "block",
                    lambda row: confirm(
                        "禁用回复",
                        "禁用并删除该已学回复？",
                        lambda: ban_selected(row),
                    ),
                ),
                (
                    "删除",
                    "delete",
                    lambda row: confirm(
                        "删除回复",
                        "仅删除该已学回复，不会禁用，所以依然能继续学？",
                        lambda: delete_selected(row),
                    ),
                ),
            ],
        )


async def build_blacklist_page() -> None:
    """Blacklist table."""
    with ui.card().classes("w-full p-4"):
        ui.label("禁用列表").classes("text-lg font-bold")
        ui.label(
            "此数据库记录了NICKNAME被禁用的内容/关键词。"
            "不能在此添加禁用，只能在群中回复「不可以」或在「配置」中添加屏蔽词来达到禁用效果。",
        ).classes("text-sm text-gray-500")

        async def fetch_blacklist(filters: dict, page: int) -> tuple[list[dict], int]:
            keywords = filters.pop("keywords", None)
            ban_keyword = filters.pop("bans", None)
            stmt = select(ChatBlackList).order_by(ChatBlackList.id.desc())
            if keywords:
                stmt = stmt.where(ChatBlackList.keywords.contains(keywords))
            async with get_session() as session:
                items = (await session.scalars(stmt)).all()
            rows = []
            for item in items:
                bans = (
                    "全局禁用"
                    if item.global_ban
                    else (str(item.ban_group_id[0]) if item.ban_group_id else "")
                )
                if ban_keyword and ban_keyword not in bans:
                    continue
                rows.append(
                    {
                        "id": item.id,
                        "keywords": (item.keywords or "")[:40],
                        "full_keywords": item.keywords,
                        "bans": bans,
                    },
                )
            total = len(rows)
            return rows[(page - 1) * PER_PAGE : page * PER_PAGE], total

        async def unban_selected(row: dict) -> None:
            try:
                async with get_session() as session:
                    await session.execute(
                        delete(ChatBlackList).where(ChatBlackList.id == row["id"]),
                    )
                    await session.commit()
                ui.notify("已取消禁用，但该内容/关键词需要重新学习", type="positive")
            except Exception as e:
                ui.notify(f"操作失败: {e}", type="negative")

        async def unban_all() -> None:
            try:
                async with get_session() as session:
                    await session.execute(delete(ChatBlackList))
                    await session.commit()
                ui.notify("已取消所有禁用", type="positive")
            except Exception as e:
                ui.notify(f"操作失败: {e}", type="negative")

        def show_detail(row: dict) -> None:
            with ui.dialog() as dialog, ui.card().classes("p-4"):
                ui.label("内容全文").classes("text-lg font-bold")
                ui.label(row["full_keywords"] or "").classes("break-all")
                with ui.row().classes("w-full justify-end"):
                    ui.button("关闭", on_click=dialog.close)
            dialog.open()

        await build_data_table(
            columns=[
                {"name": "keywords", "label": "内容/关键词", "field": "keywords"},
                {"name": "bans", "label": "已禁用的群", "field": "bans"},
            ],
            fetch=fetch_blacklist,
            search_fields=[
                ("keywords", "内容/关键词", "搜索内容/关键词"),
                ("bans", "已禁用的群", "搜索已禁用的群"),
            ],
            toolbar_buttons=[
                (
                    "取消所有禁用",
                    "check_circle",
                    lambda: confirm(
                        "取消所有禁用",
                        "确定要取消所有禁用吗？",
                        unban_all,
                    ),
                ),
            ],
            row_buttons=[
                ("查看全文", "visibility", show_detail),
                (
                    "取消禁用",
                    "check_circle",
                    lambda row: confirm(
                        "取消禁用",
                        "取消该被禁用的内容/关键词？取消后需要重新学习。",
                        lambda: unban_selected(row),
                    ),
                ),
            ],
        )


@ui.page("/admin")
async def admin_page() -> None:
    if not is_authenticated():
        ui.navigate.to("/login")
        return
    with ui.header().classes("items-center justify-between"):
        ui.label("Learning-Chat 后台管理").classes("text-lg font-bold")
        with ui.row().classes("items-center gap-2"):
            ui.link(
                "GitHub",
                "https://github.com/CMHopeSunshine/nonebot-plugin-learning-chat",
                new_tab=True,
            )
            ui.button("退出登录", icon="logout", on_click=logout).props("flat")
    with (
        ui.left_drawer(value=True, bordered=False).classes("bg-gray-50"),
        ui.tabs().props("vertical").classes("w-full") as tabs,
    ):
        tab_configs = ui.tab("configs", label="配置", icon="settings")
        tab_messages = ui.tab("messages", label="群聊消息", icon="chat")
        tab_contexts = ui.tab("contexts", label="学习内容", icon="article")
        tab_answers = ui.tab("answers", label="内容回复", icon="reply")
        tab_blacklist = ui.tab("blacklist", label="禁用列表", icon="block")
    with ui.tab_panels(tabs, value=tab_configs).classes("w-full"):
        with ui.tab_panel(tab_configs):
            build_configs()
        with ui.tab_panel(tab_messages):
            await build_messages_page()
        with ui.tab_panel(tab_contexts):
            await build_contexts_page()
        with ui.tab_panel(tab_answers):
            await build_answers_page()
        with ui.tab_panel(tab_blacklist):
            await build_blacklist_page()
