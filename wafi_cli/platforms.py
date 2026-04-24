"""
Shared platform registry for Wafi Agent.

Single source of truth for platform metadata consumed by both
skills_config (label display) and tools_config (default toolset
resolution).  Import ``PLATFORMS`` from here instead of maintaining
duplicate dicts in each module.
"""

from collections import OrderedDict
from typing import NamedTuple


class PlatformInfo(NamedTuple):
    """Metadata for a single platform entry."""
    label: str
    default_toolset: str


# Ordered so that TUI menus are deterministic.
PLATFORMS: OrderedDict[str, PlatformInfo] = OrderedDict([
    ("cli",            PlatformInfo(label="🖥️  CLI",            default_toolset="wafi-cli")),
    ("telegram",       PlatformInfo(label="📱 Telegram",        default_toolset="wafi-telegram")),
    ("discord",        PlatformInfo(label="💬 Discord",         default_toolset="wafi-discord")),
    ("slack",          PlatformInfo(label="💼 Slack",           default_toolset="wafi-slack")),
    ("whatsapp",       PlatformInfo(label="📱 WhatsApp",        default_toolset="wafi-whatsapp")),
    ("signal",         PlatformInfo(label="📡 Signal",          default_toolset="wafi-signal")),
    ("bluebubbles",    PlatformInfo(label="💙 BlueBubbles",     default_toolset="wafi-bluebubbles")),
    ("email",          PlatformInfo(label="📧 Email",           default_toolset="wafi-email")),
    ("homeassistant",  PlatformInfo(label="🏠 Home Assistant",  default_toolset="wafi-homeassistant")),
    ("mattermost",     PlatformInfo(label="💬 Mattermost",      default_toolset="wafi-mattermost")),
    ("matrix",         PlatformInfo(label="💬 Matrix",          default_toolset="wafi-matrix")),
    ("dingtalk",       PlatformInfo(label="💬 DingTalk",        default_toolset="wafi-dingtalk")),
    ("feishu",         PlatformInfo(label="🪽 Feishu",          default_toolset="wafi-feishu")),
    ("wecom",          PlatformInfo(label="💬 WeCom",           default_toolset="wafi-wecom")),
    ("wecom_callback", PlatformInfo(label="💬 WeCom Callback",  default_toolset="wafi-wecom-callback")),
    ("weixin",         PlatformInfo(label="💬 Weixin",          default_toolset="wafi-weixin")),
    ("qqbot",          PlatformInfo(label="💬 QQBot",           default_toolset="wafi-qqbot")),
    ("webhook",        PlatformInfo(label="🔗 Webhook",         default_toolset="wafi-webhook")),
    ("api_server",     PlatformInfo(label="🌐 API Server",      default_toolset="wafi-api-server")),
])


def platform_label(key: str, default: str = "") -> str:
    """Return the display label for a platform key, or *default*."""
    info = PLATFORMS.get(key)
    return info.label if info is not None else default
