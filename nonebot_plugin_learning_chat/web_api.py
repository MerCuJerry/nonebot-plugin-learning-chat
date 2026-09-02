"""Mount the NiceGUI-based admin UI onto the NoneBot FastAPI app.

This module replaces the previous FastAPI-only REST backend. All management
operations (login, config, CRUD) are now handled inside the NiceGUI pages in
``web_page``; this file only wires NiceGUI into the existing NoneBot FastAPI
application.
"""

from nicegui import ui
from nonebot import get_app, logger

from . import web_page  # noqa: F401  (module import registers all @ui.page routes)
from .config import config_manager

# ui.run_with() wraps the FastAPI lifespan, so it must run at module import time
# (i.e. during plugin loading) rather than inside a startup callback.
if config_manager.config.enable_web:
    ui.run_with(
        get_app(),
        mount_path="/learning_chat",
        storage_secret=config_manager.config.web_secret_key,
        title="Learning-Chat 后台管理",
        show_welcome_message=False,
    )
    logger.info("Learning-Chat web admin mounted at /learning_chat")
else:
    logger.info("Learning-Chat web admin is disabled (enable_web=false)")
