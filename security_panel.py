import discord
from discord import app_commands

from config_ui import ensure_guild_entry

DEFAULT_COOLDOWN_SECONDS = 5
DEFAULT_MAX_ACTIONS_PER_MINUTE = 10
DEFAULT_BLOCK_DURATION = 45


def get_security_settings(config: dict, gid: str, guild: discord.Guild | None) -> dict:
    gcfg = ensure_guild_entry(config, gid, guild)
    return gcfg.setdefault(
        "anti_abuse",
        {
            "enabled": True,
            "cooldown_seconds": DEFAULT_COOLDOWN_SECONDS,
            "max_actions_per_minute": DEFAULT_MAX_ACTIONS_PER_MINUTE,
            "anti_spam": True,
            "audit_logs": False,
            "block_duration": DEFAULT_BLOCK_DURATION,
        },
    )


def build_security_embed(config: dict, gid: str, guild: discord.Guild) -> discord.Embed:
    settings = get_security_settings(config, gid, guild)
    logs = ensure_guild_entry(config, gid, guild).get("logs", {})
    channel = None
    if logs.get("channel_id") and guild:
        channel = guild.get_channel(int(logs["channel_id"])) or guild.get_channel(int(logs["channel_id"]))

    channel_text = channel.mention if channel else "Aucun salon de logs configuré"
    embed = discord.Embed(
        title="🛡️ Dashboard de sécurité",
        description=(
            "Gère toutes les protections anti-abuse du serveur depuis un seul panneau centralisé."
        ),
        color=discord.Color.blue(),
    )
    embed.add_field(
        name="Anti-abuse global",
        value="✅ Activé" if settings.get("enabled") else "❌ Désactivé",
        inline=True,
    )
    embed.add_field(
        name="Anti-spam interactions",
        value="✅ Activé" if settings.get("anti_spam") else "❌ Désactivé",
        inline=True,
    )
    embed.add_field(
        name="Audit logs anti-abuse",
        value="✅ Activé" if settings.get("audit_logs") else "❌ Désactivé",
        inline=True,
    )
    embed.add_field(
        name="Cooldown global",
        value=f"{settings.get('cooldown_seconds', DEFAULT_COOLDOWN_SECONDS)}s",
        inline=True,
    )
    limit_value = settings.get("max_actions_per_minute")
    limit_text = "OFF" if not limit_value else f"{limit_value}/m"
    embed.add_field(
        name="Limite",
        value=limit_text,
        inline=True,
    )
    embed.add_field(
        name="Salon de logs d'audit",
        value=channel_text,
        inline=False,
    )
    embed.set_footer(text=f"Serveur {guild.name} • ID {gid}")
    return embed


class SecurityPanelView(discord.ui.View):
    def __init__(self, config: dict, cfg_ctrl, guild: discord.Guild, gid: str):
        super().__init__(timeout=None)
        self.config = config
        self.cfg_ctrl = cfg_ctrl
        self.guild = guild
        self.gid = gid

    def _reload_config(self) -> None:
        if hasattr(self.cfg_ctrl, "load_fn") and hasattr(self.cfg_ctrl, "path"):
            current = self.cfg_ctrl.load_fn(self.cfg_ctrl.path)
            self.config.clear()
            self.config.update(current)

    def _get_current_settings(self) -> dict:
        self._reload_config()
        return get_security_settings(self.config, self.gid, self.guild)

    def _set_button_states(self, settings: dict) -> None:
        for item in self.children:
            if item.custom_id == "security_toggle_global":
                item.style = discord.ButtonStyle.success if settings.get("enabled") else discord.ButtonStyle.secondary
            elif item.custom_id == "security_toggle_antispam":
                item.style = discord.ButtonStyle.success if settings.get("anti_spam") else discord.ButtonStyle.secondary
            elif item.custom_id == "security_toggle_audit_logs":
                item.style = discord.ButtonStyle.success if settings.get("audit_logs") else discord.ButtonStyle.secondary
            elif item.custom_id == "security_limit_off":
                item.style = discord.ButtonStyle.success if settings.get("max_actions_per_minute") == 0 else discord.ButtonStyle.secondary
            elif item.custom_id == "security_limit_10":
                item.style = discord.ButtonStyle.success if settings.get("max_actions_per_minute") == 10 else discord.ButtonStyle.secondary
            elif item.custom_id == "security_limit_20":
                item.style = discord.ButtonStyle.success if settings.get("max_actions_per_minute") == 20 else discord.ButtonStyle.secondary
            elif item.custom_id == "security_limit_30":
                item.style = discord.ButtonStyle.success if settings.get("max_actions_per_minute") == 30 else discord.ButtonStyle.secondary

    async def ensure_guild_owner(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            await interaction.response.send_message(
                "Cette commande doit être utilisée depuis un serveur.", ephemeral=True
            )
            return False
        if interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message("Accès refusé", ephemeral=True)
            return False
        return True

    async def update_message(self, interaction: discord.Interaction) -> None:
        self._reload_config()
        settings = get_security_settings(self.config, self.gid, self.guild)
        self._set_button_states(settings)
        await interaction.response.edit_message(
            embed=build_security_embed(self.config, self.gid, self.guild),
            view=self,
        )

    @discord.ui.button(
        label="Anti-abuse global",
        style=discord.ButtonStyle.secondary,
        custom_id="security_toggle_global",
        row=1,
    )
    async def toggle_global(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.ensure_guild_owner(interaction):
            return
        settings = self._get_current_settings()
        settings["enabled"] = not settings.get("enabled", True)
        self.cfg_ctrl.save()
        self._reload_config()
        await self.update_message(interaction)

    @discord.ui.button(
        label="Anti-spam",
        style=discord.ButtonStyle.secondary,
        custom_id="security_toggle_antispam",
        row=1,
    )
    async def toggle_antispam(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.ensure_guild_owner(interaction):
            return
        settings = self._get_current_settings()
        settings["anti_spam"] = not settings.get("anti_spam", True)
        self.cfg_ctrl.save()
        self._reload_config()
        await self.update_message(interaction)

    @discord.ui.button(
        label="Audit logs",
        style=discord.ButtonStyle.secondary,
        custom_id="security_toggle_audit_logs",
        row=1,
    )
    async def toggle_audit_logs(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.ensure_guild_owner(interaction):
            return
        settings = self._get_current_settings()
        settings["audit_logs"] = not settings.get("audit_logs", False)
        self.cfg_ctrl.save()
        self._reload_config()
        await self.update_message(interaction)

    @discord.ui.button(
        label="Cooldown 5s",
        style=discord.ButtonStyle.primary,
        custom_id="security_cooldown_5",
        row=2,
    )
    async def cooldown_5(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.ensure_guild_owner(interaction):
            return
        settings = self._get_current_settings()
        settings["cooldown_seconds"] = 5
        self.cfg_ctrl.save()
        self._reload_config()
        await self.update_message(interaction)

    @discord.ui.button(
        label="Cooldown 10s",
        style=discord.ButtonStyle.primary,
        custom_id="security_cooldown_10",
        row=2,
    )
    async def cooldown_10(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.ensure_guild_owner(interaction):
            return
        settings = self._get_current_settings()
        settings["cooldown_seconds"] = 10
        self.cfg_ctrl.save()
        self._reload_config()
        await self.update_message(interaction)

    @discord.ui.button(
        label="Cooldown 15s",
        style=discord.ButtonStyle.primary,
        custom_id="security_cooldown_15",
        row=2,
    )
    async def cooldown_15(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.ensure_guild_owner(interaction):
            return
        settings = self._get_current_settings()
        settings["cooldown_seconds"] = 15
        self.cfg_ctrl.save()
        self._reload_config()
        await self.update_message(interaction)

    @discord.ui.button(
        label="Limite OFF",
        style=discord.ButtonStyle.danger,
        custom_id="security_limit_off",
        row=3,
    )
    async def limit_off(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.ensure_guild_owner(interaction):
            return
        settings = self._get_current_settings()
        settings["max_actions_per_minute"] = 0
        self.cfg_ctrl.save()
        self._reload_config()
        await self.update_message(interaction)

    @discord.ui.button(
        label="Limite 10/m",
        style=discord.ButtonStyle.success,
        custom_id="security_limit_10",
        row=3,
    )
    async def limit_10(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.ensure_guild_owner(interaction):
            return
        settings = self._get_current_settings()
        settings["max_actions_per_minute"] = 10
        self.cfg_ctrl.save()
        self._reload_config()
        await self.update_message(interaction)

    @discord.ui.button(
        label="Limite 20/m",
        style=discord.ButtonStyle.success,
        custom_id="security_limit_20",
        row=3,
    )
    async def limit_20(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.ensure_guild_owner(interaction):
            return
        settings = self._get_current_settings()
        settings["max_actions_per_minute"] = 20
        self.cfg_ctrl.save()
        self._reload_config()
        await self.update_message(interaction)

    @discord.ui.button(
        label="Limite 30/m",
        style=discord.ButtonStyle.success,
        custom_id="security_limit_30",
        row=3,
    )
    async def limit_30(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.ensure_guild_owner(interaction):
            return
        settings = self._get_current_settings()
        settings["max_actions_per_minute"] = 30
        self.cfg_ctrl.save()
        self._reload_config()
        await self.update_message(interaction)


def setup_security_panel(
    bot: discord.Client,
    config: dict,
    cfg_ctrl,
    command_enabled_check,
) -> None:
    @bot.tree.command(name="security", description="Ouvre le dashboard de sécurité du serveur")
    @app_commands.check(command_enabled_check)
    async def security_cmd(interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message(
                "Cette commande doit être utilisée depuis un serveur.", ephemeral=True
            )
        if interaction.user.id != interaction.guild.owner_id:
            return await interaction.response.send_message("Accès refusé", ephemeral=True)

        gid = str(interaction.guild.id)
        get_security_settings(config, gid, interaction.guild)
        view = SecurityPanelView(config, cfg_ctrl, interaction.guild, gid)
        await interaction.response.send_message(
            embed=build_security_embed(config, gid, interaction.guild),
            view=view,
            ephemeral=True,
        )

    return security_cmd
