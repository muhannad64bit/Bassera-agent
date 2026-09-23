"""
Shared platform registry for Bassera Agent.

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
    ("cli",            PlatformInfo(label="🖥️  CLI",            default_toolset="bassera-cli")),
    ("telegram",       PlatformInfo(label="📱 Telegram",        default_toolset="bassera-telegram")),
    ("discord",        PlatformInfo(label="💬 Discord",         default_toolset="bassera-discord")),
    ("slack",          PlatformInfo(label="💼 Slack",           default_toolset="bassera-slack")),
    ("whatsapp",       PlatformInfo(label="📱 WhatsApp",        default_toolset="bassera-whatsapp")),
    ("signal",         PlatformInfo(label="📡 Signal",          default_toolset="bassera-signal")),
    ("bluebubbles",    PlatformInfo(label="💙 BlueBubbles",     default_toolset="bassera-bluebubbles")),
    ("email",          PlatformInfo(label="📧 Email",           default_toolset="bassera-email")),
    ("homeassistant",  PlatformInfo(label="🏠 Home Assistant",  default_toolset="bassera-homeassistant")),
    ("mattermost",     PlatformInfo(label="💬 Mattermost",      default_toolset="bassera-mattermost")),
    ("matrix",         PlatformInfo(label="💬 Matrix",          default_toolset="bassera-matrix")),
    ("dingtalk",       PlatformInfo(label="💬 DingTalk",        default_toolset="bassera-dingtalk")),
    ("feishu",         PlatformInfo(label="🪽 Feishu",          default_toolset="bassera-feishu")),
    ("wecom",          PlatformInfo(label="💬 WeCom",           default_toolset="bassera-wecom")),
    ("wecom_callback", PlatformInfo(label="💬 WeCom Callback",  default_toolset="bassera-wecom-callback")),
    ("weixin",         PlatformInfo(label="💬 Weixin",          default_toolset="bassera-weixin")),
    ("qqbot",          PlatformInfo(label="💬 QQBot",           default_toolset="bassera-qqbot")),
    ("webhook",        PlatformInfo(label="🔗 Webhook",         default_toolset="bassera-webhook")),
    ("api_server",     PlatformInfo(label="🌐 API Server",      default_toolset="bassera-api-server")),
])


def platform_label(key: str, default: str = "") -> str:
    """Return the display label for a platform key, or *default*."""
    info = PLATFORMS.get(key)
    return info.label if info is not None else default
