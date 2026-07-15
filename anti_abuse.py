import asyncio
import time
from collections import defaultdict, deque
from typing import DefaultDict, Deque, Dict, Optional, Tuple

import discord

from config_ui import ensure_guild_entry

DEFAULT_COOLDOWN_SECONDS = 5
DEFAULT_MAX_ACTIONS_PER_MINUTE = 10
DEFAULT_BLOCK_DURATION = 45
DEFAULT_ACTION_WINDOW_SECONDS = 60
DEFAULT_MODAL_TIMEOUT_SECONDS = 120
DEFAULT_INACTIVE_CLEANUP_SECONDS = 600


class DummyLock:
    def release(self) -> None:
        return None


class AntiAbuse:
    def __init__(self):
        self._last_action: Dict[Tuple[str, str], float] = {}
        self._cooldown_seconds: Dict[Tuple[str, str], float] = {}
        self._action_windows: DefaultDict[Tuple[str, str], Deque[float]] = defaultdict(deque)
        self._blocked_until: Dict[Tuple[str, str], float] = {}
        self._modal_open: Dict[Tuple[str, str], float] = {}
        self._modal_locks: DefaultDict[Tuple[str, str], asyncio.Lock] = defaultdict(asyncio.Lock)

    @staticmethod
    def _get_settings(config: dict, guild_id: str) -> dict:
        def _int_or_default(value, default):
            try:
                if value is None:
                    return default
                return int(value)
            except (TypeError, ValueError):
                return default

        gcfg = config.get("guilds", {}).get(guild_id, {})
        anti_abuse = gcfg.get("anti_abuse", {})
        max_actions = anti_abuse.get("max_actions_per_minute", DEFAULT_MAX_ACTIONS_PER_MINUTE)
        if max_actions is None:
            max_actions = 0
        else:
            max_actions = _int_or_default(max_actions, DEFAULT_MAX_ACTIONS_PER_MINUTE)
        return {
            "enabled": bool(anti_abuse.get("enabled", True)),
            "cooldown_seconds": _int_or_default(anti_abuse.get("cooldown_seconds", DEFAULT_COOLDOWN_SECONDS), DEFAULT_COOLDOWN_SECONDS),
            "max_actions_per_minute": max_actions,
            "anti_spam": bool(anti_abuse.get("anti_spam", True)),
            "audit_logs": bool(anti_abuse.get("audit_logs", False)),
            "block_duration": _int_or_default(anti_abuse.get("block_duration", DEFAULT_BLOCK_DURATION), DEFAULT_BLOCK_DURATION),
        }

    def _key(self, guild_id: str, user_id: str) -> Tuple[str, str]:
        return guild_id or "global", user_id

    def _get_limits(self, config: dict, guild_id: str) -> Tuple[int, int, int]:
        settings = self._get_settings(config, guild_id)
        return (
            settings["max_actions_per_minute"],
            settings["block_duration"],
            settings["cooldown_seconds"],
        )

    def _is_blocked(self, key: Tuple[str, str]) -> bool:
        return self._blocked_until.get(key, 0) > time.monotonic()

    def _cooldown_remaining(self, key: Tuple[str, str]) -> float:
        expires = self._last_action.get(key, 0) + self._cooldown_seconds.get(key, 0)
        return max(0.0, expires - time.monotonic())

    def _cleanup_window(self, timestamps: Deque[float]) -> None:
        cutoff = time.monotonic() - DEFAULT_ACTION_WINDOW_SECONDS
        while timestamps and timestamps[0] < cutoff:
            timestamps.popleft()

    def _refresh_modal_open(self, key: Tuple[str, str]) -> None:
        expiration = self._modal_open.get(key)
        if expiration and expiration < time.monotonic():
            self._modal_open.pop(key, None)

    async def _send_ephemeral(self, interaction: discord.Interaction, content: str) -> None:
        try:
            if interaction.response.is_done():
                await interaction.followup.send(content, ephemeral=True)
            else:
                await interaction.response.send_message(content, ephemeral=True)
        except Exception:
            pass

    async def _send_audit_log(
        self,
        bot: discord.Client,
        config: dict,
        interaction: discord.Interaction,
        guild_id: str,
        action_name: str,
        reason: str,
    ) -> None:
        guild = interaction.guild
        if not guild:
            return
        logs = ensure_guild_entry(config, guild_id, guild).get("logs", {})
        channel_id = logs.get("channel_id")
        if not channel_id:
            return

        channel = bot.get_channel(int(channel_id)) or guild.get_channel(int(channel_id))
        if not channel:
            return

        embed = discord.Embed(
            title="⚠️ Tentative refusée",
            color=discord.Color.orange(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Utilisateur", value=f"<@{interaction.user.id}> (`{interaction.user.id}`)", inline=False)
        embed.add_field(name="Action", value=action_name, inline=False)
        embed.add_field(name="Raison", value=reason, inline=False)
        embed.add_field(name="Serveur", value=f"{guild.name} (`{guild_id}`)", inline=False)

        try:
            await channel.send(embed=embed)
        except Exception:
            pass

    async def _deny(
        self,
        interaction: discord.Interaction,
        bot: discord.Client,
        config: dict,
        guild_id: str,
        action_name: str,
        reason: str,
    ) -> bool:
        message = {
            "cooldown": "⏳ Trop rapide, attends quelques secondes",
            "spam": "❌ Trop de tentatives, réessaye plus tard",
            "Déjà modal ouvert": "❌ Un modal est déjà ouvert pour toi",
            "double soumission": "❌ Modal déjà soumis, attends la réponse",
            "permission denied": "❌ Permission refusée",
        }.get(reason, f"❌ {reason}")
        await self._send_ephemeral(interaction, message)
        settings = self._get_settings(config, guild_id)
        if settings.get("audit_logs"):
            await self._send_audit_log(bot, config, interaction, guild_id, action_name, reason)
        return False

    def _record_action(self, key: Tuple[str, str], max_actions: int, block_duration: int) -> Optional[str]:
        if max_actions <= 0:
            return None
        timestamps = self._action_windows[key]
        self._cleanup_window(timestamps)
        if len(timestamps) >= max_actions:
            self._blocked_until[key] = time.monotonic() + block_duration
            timestamps.clear()
            return "spam"
        timestamps.append(time.monotonic())
        return None

    def _cleanup_inactive_keys(self) -> None:
        now = time.monotonic()
        cutoff = now - DEFAULT_INACTIVE_CLEANUP_SECONDS
        for key in list(self._last_action.keys()):
            last = self._last_action.get(key, 0)
            modal_expires = self._modal_open.get(key, 0)
            lock = self._modal_locks.get(key)
            if last < cutoff and modal_expires < now and (not lock or not lock.locked()):
                self._last_action.pop(key, None)
                self._cooldown_seconds.pop(key, None)
                self._blocked_until.pop(key, None)
                self._action_windows.pop(key, None)
                self._modal_open.pop(key, None)
                self._modal_locks.pop(key, None)

    def _set_cooldown(self, key: Tuple[str, str], cooldown_seconds: int) -> None:
        self._last_action[key] = time.monotonic()
        self._cooldown_seconds[key] = cooldown_seconds

    async def start_action(
        self,
        interaction: discord.Interaction,
        guild_id: str,
        action_name: str,
        config: dict,
        bot: discord.Client,
        *,
        modal_open: bool = False,
    ) -> bool:
        settings = self._get_settings(config, guild_id)
        if not settings.get("enabled"):
            return True

        self._cleanup_inactive_keys()
        key = self._key(guild_id, str(interaction.user.id))
        self._refresh_modal_open(key)
        max_actions, block_duration, _ = self._get_limits(config, guild_id)
        if max_actions > 0 and self._is_blocked(key):
            return await self._deny(interaction, bot, config, guild_id, action_name, "spam")

        if settings.get("anti_spam") and modal_open and key in self._modal_open:
            return await self._deny(interaction, bot, config, guild_id, action_name, "Déjà modal ouvert")

        self._set_cooldown(key, settings["cooldown_seconds"])

        reason = self._record_action(key, max_actions, block_duration)
        if reason:
            return await self._deny(interaction, bot, config, guild_id, action_name, reason)

        if settings.get("anti_spam") and modal_open:
            self._modal_open[key] = time.monotonic() + DEFAULT_MODAL_TIMEOUT_SECONDS
            asyncio.create_task(self._clear_modal_after_timeout(key))
        return True

    async def begin_modal_submit(
        self,
        interaction: discord.Interaction,
        guild_id: str,
        action_name: str,
        config: dict,
        bot: discord.Client,
    ) -> Optional[asyncio.Lock]:
        settings = self._get_settings(config, guild_id)
        if not settings.get("enabled"):
            return DummyLock()

        key = self._key(guild_id, str(interaction.user.id))
        self._refresh_modal_open(key)
        max_actions, block_duration, _ = self._get_limits(config, guild_id)
        if max_actions > 0 and self._is_blocked(key):
            await self._deny(interaction, bot, config, guild_id, action_name, "spam")
            return None

        lock = self._modal_locks[key]
        if settings.get("anti_spam") and lock.locked():
            await self._deny(interaction, bot, config, guild_id, action_name, "double soumission")
            return None

        cooldown_remaining = self._get_cooldown_remaining(key)
        if cooldown_remaining > 0:
            await self._deny(interaction, bot, config, guild_id, action_name, "cooldown")
            return None

        max_actions, block_duration, _ = self._get_limits(config, guild_id)
        reason = self._record_action(key, max_actions, block_duration)
        if reason:
            await self._deny(interaction, bot, config, guild_id, action_name, reason)
            return None

        self._set_cooldown(key, settings["cooldown_seconds"])
        if settings.get("anti_spam"):
            await lock.acquire()
            return lock
        return DummyLock()

    def _get_cooldown_remaining(self, key: Tuple[str, str]) -> float:
        expires = self._last_action.get(key, 0) + self._cooldown_seconds.get(key, 0)
        return max(0.0, expires - time.monotonic())

    def clear_modal_open(self, guild_id: str, user_id: str) -> None:
        key = self._key(guild_id, user_id)
        self._modal_open.pop(key, None)

    async def _clear_modal_after_timeout(self, key: Tuple[str, str]) -> None:
        await asyncio.sleep(DEFAULT_MODAL_TIMEOUT_SECONDS)
        self._modal_open.pop(key, None)


abuse_manager = AntiAbuse()
