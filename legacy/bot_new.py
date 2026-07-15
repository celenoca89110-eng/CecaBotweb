import asyncio
import io
import json
import os
import re
import time

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore[assignment,misc]

import discord
from discord import app_commands
from discord.ext import commands

import store

from anti_abuse import abuse_manager
from config_ui import (
    ConfigController,
    ConfigRootView,
    OwnerPanelView,
    can_use_bot_panel,
    ensure_guild_entry,
    ticket_client_overwrite,
)
from security_panel import setup_security_panel

# =========================
# CHEMINS & CONFIG FICHIERS
# =========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if load_dotenv:
    _env_path = os.path.join(BASE_DIR, ".env")
    load_dotenv(_env_path, encoding="utf-8-sig", override=True)
else:
    print(
        "⚠️ Module « python-dotenv » introuvable — le fichier .env ne sera pas chargé.\n"
        "   Installe-le avec : python -m pip install python-dotenv\n"
        "   (ou : pip install -r requirements.txt)"
    )
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
TRANSCRIPTS_DIR = os.path.join(BASE_DIR, "transcripts")
INVITE_CONFIG_FILE = os.path.join(BASE_DIR, "invite_config.json")

store.init_db()


def load_cfg_file(path: str) -> dict:
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return {}
                return json.loads(content)
    except Exception as e:
        print(f"❌ ERREUR LOAD {path}:", e)
    return {}


def load_invite_config() -> dict:
    """Charge la configuration des invitations."""
    default_config = {
        "configurations": {}
    }
    try:
        if os.path.isfile(INVITE_CONFIG_FILE):
            with open(INVITE_CONFIG_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return default_config
                loaded = json.loads(content)
                default_config.update(loaded)
                return default_config
    except Exception as e:
        print(f"❌ ERREUR LOAD {INVITE_CONFIG_FILE}:", e)
    return default_config


def save_invite_config(config_data: dict) -> bool:
    """Sauvegarde la configuration des invitations."""
    try:
        with open(INVITE_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"❌ ERREUR SAVE {INVITE_CONFIG_FILE}:", e)
        return False


def get_invite_config_by_name(config_name: str) -> dict | None:
    """Récupère une configuration spécifique par son nom."""
    config_data = load_invite_config()
    configurations = config_data.get("configurations", {})
    return configurations.get(config_name)


def generate_invite_url(client_id: str, permissions: str, recommended_guild_id: str = "") -> str:
    """Génère l'URL d'invitation OAuth2."""
    base_url = "https://discord.com/oauth2/authorize"
    invite_url = f"{base_url}?client_id={client_id}&permissions={permissions}&scope=bot%20applications.commands"
    if recommended_guild_id:
        invite_url += f"&guild_id={recommended_guild_id}"
    return invite_url


config: dict = load_cfg_file(CONFIG_FILE)
THEME = discord.Color.blurple()
COLOR_RELAY_USER = discord.Color.from_rgb(88, 101, 242)
COLOR_RELAY_STAFF = discord.Color.from_rgb(67, 181, 129)
cooldown: dict[str, float] = {}
first_messages: dict[str, str] = {}
user_guild_map: dict[str, str] = {}  # user_id -> guild_id mapping for DM interactions


def channel_name_slug(username: str, prefix: str) -> str:
    slug = "".join(
        c if c.isalnum() or c in "_-" else "-" for c in (username or "user").lower()
    )
    slug = re.sub(r"-+", "-", slug).strip("-") or "user"
    return f"{prefix}-{slug}"[:95]


def can_staff_close(member: discord.Member, cfg: dict | None) -> bool:
    if is_super_admin(member.id):
        return True
    if not cfg:
        return False
    admin_roles = cfg.get("admin_roles") or []
    for rid in admin_roles:
        role = member.guild.get_role(int(rid))
        if role and role in member.roles:
            return True
    return False


SUPER_ADMIN_ID = 1112038418629808148

def is_super_admin(user_id: int) -> bool:
    """Vérifie si l'utilisateur est le super admin (droits absolus sur tous les serveurs)."""
    return user_id == SUPER_ADMIN_ID

def is_bot_owner(user_id: int) -> bool:
    """Vérifie si l'utilisateur est le propriétaire du bot."""
    return str(user_id) == config.get("bot_owner_id", "") or is_super_admin(user_id)


def is_whitelisted_admin(user_id: int) -> bool:
    """Vérifie si l'utilisateur est dans la whitelist des admins."""
    whitelisted = config.get("whitelisted_admins", [])
    return str(user_id) in whitelisted


def has_bot_permission(user_id: int) -> bool:
    """Vérifie si l'utilisateur a les permissions principales du bot (owner ou whitelist)."""
    return is_bot_owner(user_id) or is_whitelisted_admin(user_id) or is_super_admin(user_id)


def is_command_disabled(command_name: str) -> bool:
    """Vérifie si une commande est désactivée dans la configuration."""
    disabled = config.get("disabled_commands", [])
    return command_name in disabled


def is_guild_owner(member: discord.Member) -> bool:
    """Vérifie si l'utilisateur est le propriétaire du serveur."""
    return member.guild.owner_id == member.id


def is_guild_admin(member: discord.Member, cfg: dict | None) -> bool:
    """Vérifie si l'utilisateur est admin du serveur (rôles admin ou permissions Discord)."""
    if is_super_admin(member.id):
        return True
    
    if not cfg:
        return False
    
    # Vérifier les rôles admin configurés
    admin_roles = cfg.get("admin_roles") or []
    for rid in admin_roles:
        role = member.guild.get_role(int(rid))
        if role and role in member.roles:
            return True
    
    # Vérifier les permissions Discord (manage_guild)
    if member.guild_permissions.manage_guild:
        return True
    
    return False


def is_guild_mod(member: discord.Member, cfg: dict | None) -> bool:
    """Vérifie si l'utilisateur est modérateur du serveur (rôles mod)."""
    if is_super_admin(member.id):
        return True
    
    if not cfg:
        return False
    
    mod_roles = cfg.get("mod_roles") or []
    for rid in mod_roles:
        role = member.guild.get_role(int(rid))
        if role and role in member.roles:
            return True
    
    return False


async def command_enabled_check(interaction: discord.Interaction) -> bool:
    """Check Discord : refuse l'exécution si la commande est désactivée."""
    command_name = interaction.command.name if interaction.command else None
    if command_name and is_command_disabled(command_name):
        await interaction.response.send_message(
            f"❌ Cette commande est désactivée par l'administrateur.",
            ephemeral=True
        )
        return False
    return True


async def build_transcript(channel: discord.TextChannel, limit: int = 450) -> str:
    lines = [
        f"Transcript du salon #{channel.name}",
        f"ID salon : {channel.id} | Serveur : {channel.guild.name} ({channel.guild.id})",
        "",
    ]
    async for m in channel.history(limit=limit, oldest_first=True):
        ts = m.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        author = f"{m.author} ({m.author.id})"
        chunks: list[str] = []
        if m.content:
            chunks.append(m.content)
        for a in m.attachments:
            chunks.append(f"[fichier: {a.filename} | {a.url}]")
        for e in m.embeds:
            et = e.title or e.description or "embed"
            chunks.append(f"[embed: {et[:240]}]")
        body = " ".join(chunks) if chunks else "(sans contenu texte)"
        lines.append(f"[{ts}] {author}: {body}")
    return "\n".join(lines)


async def send_support_dm(user: discord.User, guild: discord.Guild | None = None) -> None:
    """Envoie le message de support permanent en DM."""
    try:
        embed = discord.Embed(
            title="📩 SUPPORT",
            description=(
                "👉 Clique sur le bouton ci-dessous pour ouvrir un ticket\n\n"
                "⚡ Support rapide\n🛡️ Staff disponible\n\n"
                "💬 Tu peux aussi envoyer un message directement au bot pour créer un ticket."
            ),
            color=discord.Color.blurple(),
        )
        if bot.user and bot.user.avatar:
            embed.set_thumbnail(url=bot.user.avatar.url)
        if guild:
            embed.set_footer(text=guild.name)
        
        # Find the guild_id for this user
        user_id = str(user.id)
        guild_id = user_guild_map.get(user_id)
        
        if guild_id:
            # Show category selection directly
            cfg = config.get("guilds", {}).get(guild_id)
            if cfg and cfg.get("categories"):
                guild_obj = bot.get_guild(int(guild_id))
                embed.set_footer(text=guild_obj.name if guild_obj else "Serveur inconnu")
                await user.send(embed=embed, view=CategorySelect(user, guild_id, ""))
                return
        
        # If no guild_id, send simple message
        await user.send(embed=embed)
    except discord.Forbidden:
        pass  # DM fermés
    except discord.HTTPException:
        pass


async def execute_ticket_close(
    bot: discord.Client,
    channel: discord.TextChannel,
    actor: discord.abc.User,
    *,
    interaction: discord.Interaction | None = None,
    skip_permission_check: bool = False,
) -> None:
    guild = channel.guild
    gid = str(guild.id)
    cfg = config.get("guilds", {}).get(gid)
    actor_member = guild.get_member(actor.id) if isinstance(actor, discord.User) else None

    if not skip_permission_check and isinstance(actor_member, discord.Member) and cfg:
        if not can_staff_close(actor_member, cfg):
            if interaction and interaction.guild:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "Tu n'as pas la permission de fermer ce ticket.",
                        ephemeral=True,
                    )
                else:
                    await interaction.followup.send(
                        "Tu n'as pas la permission de fermer ce ticket.",
                        ephemeral=True,
                    )
            return

    pair = store.get_ticket_by_channel(channel.id)
    uid = pair[0] if pair else None
    meta = pair[1] if pair else {}

    os.makedirs(TRANSCRIPTS_DIR, exist_ok=True)
    transcript = await build_transcript(channel)
    filename = f"ticket-{channel.id}-{int(time.time())}.txt"
    path = os.path.join(TRANSCRIPTS_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(transcript)

    log_id = (cfg or {}).get("ticket", {}).get("log_channel_id")
    log_ch = guild.get_channel(int(log_id)) if log_id else None
    if isinstance(log_ch, discord.TextChannel):
        embed = discord.Embed(
            title="🗂 Ticket fermé",
            description=(
                f"Salon : `#{channel.name}`\n"
                f"Fermé par : {actor.mention}\n"
                + (f"Utilisateur ticket : <@{uid}>" if uid else "")
            ),
            color=THEME,
        )
        if bot.user and bot.user.avatar:
            embed.set_thumbnail(url=bot.user.avatar.url)
        try:
            await log_ch.send(
                embed=embed,
                file=discord.File(path, filename=filename),
            )
        except discord.HTTPException:
            await log_ch.send(embed=embed)

    if uid:
        # Envoyer un message automatique dans le salon avant sa fermeture
        try:
            close_channel_embed = discord.Embed(
                title="🔒 Ticket fermé",
                description=(
                    f"Ce ticket a été fermé par {actor.mention if actor != bot.user else 'le bot'}.\n\n"
                    f"📂 Catégorie : {meta.get('category', 'N/A')}\n"
                    f"📝 Le transcript a été sauvegardé."
                ),
                color=discord.Color.red(),
            )
            if bot.user and bot.user.avatar:
                close_channel_embed.set_thumbnail(url=bot.user.avatar.url)
            close_channel_embed.set_footer(text=f"ID ticket : {channel.id}")
            await channel.send(embed=close_channel_embed)
        except discord.HTTPException:
            pass  # Impossible d'envoyer le message
        
        # Envoyer un message en MP à l'utilisateur pour informer que le ticket est fermé
        try:
            user = await bot.fetch_user(int(uid))
            close_embed = discord.Embed(
                title="🗂 Ticket fermé",
                description=(
                    f"Ton ticket a été fermé par {actor.mention if actor != user else 'toi-même'}.\n\n"
                    f"📂 Catégorie : {meta.get('category', 'N/A')}\n"
                    f"💬 Tu peux réouvrir un ticket à tout moment en envoyant un MP au bot."
                ),
                color=discord.Color.orange(),
            )
            if bot.user and bot.user.avatar:
                close_embed.set_thumbnail(url=bot.user.avatar.url)
            await user.send(embed=close_embed)
            
            # Re-send support message after ticket closure
            await send_support_dm(user, guild)
        except discord.Forbidden:
            pass  # L'utilisateur a désactivé les MP
        except discord.NotFound:
            pass  # L'utilisateur n'existe plus

        store.recently_closed_add(uid, gid, str(meta.get("category", "")))
        store.delete_ticket(uid)
        store.stats_inc_closed()

    # Répondre AVANT de supprimer le salon
    if interaction is not None and interaction.response.is_done():
        try:
            await interaction.followup.send("✅ Ticket fermé.", ephemeral=True)
        except discord.HTTPException:
            pass

    try:
        await channel.delete(reason="Fermeture ticket")
    except discord.Forbidden:
        if interaction and not interaction.response.is_done():
            await interaction.response.send_message(
                "Impossible de supprimer le salon (permissions Discord).",
                ephemeral=True,
            )


class ProfileButton(discord.ui.Button):
    def __init__(self, user_id: int, channel_id: int):
        super().__init__(
            label="👤 Profil",
            style=discord.ButtonStyle.primary,
            custom_id=f"ticketmp_profile:{channel_id}",
        )
        self.ticket_user_id = user_id
        self.channel_id = channel_id

    async def callback(self, interaction: discord.Interaction):
        ch = interaction.channel
        if not isinstance(ch, discord.TextChannel):
            return await interaction.response.send_message(
                "Ce n'est pas un salon texte.", ephemeral=True
            )
        if ch.id != self.channel_id:
            return await interaction.response.send_message(
                "Bouton invalide pour ce salon.", ephemeral=True
            )
        member = interaction.user
        if not isinstance(member, discord.Member):
            return await interaction.response.send_message(
                "Action réservée aux membres du serveur.", ephemeral=True
            )
        cfg = config.get("guilds", {}).get(str(ch.guild.id))
        if not is_guild_admin(member, cfg) and not is_guild_mod(member, cfg) and not is_super_admin(member.id):
            return await interaction.response.send_message(
                "Refusé : rôle admin/mod requis.", ephemeral=True
            )
        
        try:
            target_user = await bot.fetch_user(self.ticket_user_id)
        except discord.NotFound:
            return await interaction.response.send_message(
                "Utilisateur introuvable.", ephemeral=True
            )
        
        target_member = ch.guild.get_member(self.ticket_user_id)
        
        embed = discord.Embed(
            title=f"👤 Profil de {target_user.name}",
            color=THEME,
        )
        embed.set_thumbnail(url=target_user.display_avatar.url)
        embed.add_field(name="ID", value=f"`{target_user.id}`", inline=True)
        embed.add_field(name="Bot", value="Oui" if target_user.bot else "Non", inline=True)
        
        if target_user.global_name:
            embed.add_field(name="Nom global", value=target_user.global_name, inline=True)
        
        if target_member:
            embed.add_field(name="Rejoint le", value=target_member.joined_at.strftime("%d/%m/%Y %H:%M") if target_member.joined_at else "N/A", inline=True)
            embed.add_field(name="Rôles", value=f"{len(target_member.roles)} rôle(s)", inline=True)
            if target_member.roles:
                roles_list = [role.mention for role in target_member.roles if not role.is_default()]
                if roles_list:
                    embed.add_field(name="Rôles détaillés", value=", ".join(roles_list[:10]), inline=False)
        
        embed.add_field(name="Compte créé le", value=target_user.created_at.strftime("%d/%m/%Y %H:%M"), inline=True)
        embed.set_footer(text=f"ID utilisateur: {target_user.id}")
        embed.timestamp = target_user.created_at
        
        await interaction.response.send_message(embed=embed, view=RoleManagementView(self.ticket_user_id, ch.guild.id), ephemeral=True)


class RoleManagementView(discord.ui.View):
    def __init__(self, user_id: int, guild_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.guild_id = guild_id
    
    @discord.ui.button(label="➕ Ajouter rôle", style=discord.ButtonStyle.success)
    async def add_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = bot.get_guild(self.guild_id)
        if not guild:
            return await interaction.response.send_message("Serveur introuvable.", ephemeral=True)
        
        cfg = config.get("guilds", {}).get(str(self.guild_id))
        if not cfg:
            return await interaction.response.send_message("Configuration introuvable.", ephemeral=True)
        
        member = guild.get_member(self.user_id)
        if not member:
            return await interaction.response.send_message("Membre introuvable.", ephemeral=True)
        
        available_roles = []
        for rid in cfg.get("admin_roles", []) + cfg.get("mod_roles", []):
            role = guild.get_role(int(rid))
            if role and role not in member.roles:
                available_roles.append(role)
        
        if not available_roles:
            return await interaction.response.send_message("Aucun rôle disponible à ajouter.", ephemeral=True)
        
        options = [discord.SelectOption(label=role.name[:100], value=str(role.id)) for role in available_roles[:25]]
        
        select = discord.ui.Select(
            placeholder="Choisis un rôle à ajouter",
            options=options,
        )
        
        async def select_callback(interaction: discord.Interaction):
            role_id = int(select.values[0])
            role = guild.get_role(role_id)
            if not role:
                return await interaction.response.send_message("Rôle introuvable.", ephemeral=True)
            
            try:
                await member.add_roles(role)
                await interaction.response.send_message(f"✅ Rôle {role.mention} ajouté à {member.mention}.", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("❌ Permission refusée pour ajouter ce rôle.", ephemeral=True)
        
        select.callback = select_callback
        view = discord.ui.View()
        view.add_item(select)
        await interaction.response.send_message("Sélectionne un rôle à ajouter:", view=view, ephemeral=True)
    
    @discord.ui.button(label="➖ Enlever rôle", style=discord.ButtonStyle.danger)
    async def remove_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = bot.get_guild(self.guild_id)
        if not guild:
            return await interaction.response.send_message("Serveur introuvable.", ephemeral=True)
        
        member = guild.get_member(self.user_id)
        if not member:
            return await interaction.response.send_message("Membre introuvable.", ephemeral=True)
        
        member_roles = [role for role in member.roles if not role.is_default()]
        if not member_roles:
            return await interaction.response.send_message("Ce membre n'a aucun rôle à enlever.", ephemeral=True)
        
        options = [discord.SelectOption(label=role.name[:100], value=str(role.id)) for role in member_roles[:25]]
        
        select = discord.ui.Select(
            placeholder="Choisis un rôle à enlever",
            options=options,
        )
        
        async def select_callback(interaction: discord.Interaction):
            role_id = int(select.values[0])
            role = guild.get_role(role_id)
            if not role:
                return await interaction.response.send_message("Rôle introuvable.", ephemeral=True)
            
            try:
                await member.remove_roles(role)
                await interaction.response.send_message(f"✅ Rôle {role.mention} retiré de {member.mention}.", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("❌ Permission refusée pour retirer ce rôle.", ephemeral=True)
        
        select.callback = select_callback
        view = discord.ui.View()
        view.add_item(select)
        await interaction.response.send_message("Sélectionne un rôle à enlever:", view=view, ephemeral=True)


class CloseTicketButton(discord.ui.Button):
    def __init__(self, user_id: int, channel_id: int):
        super().__init__(
            label="❌ Fermer",
            style=discord.ButtonStyle.danger,
            custom_id=f"ticketmp_close:{channel_id}",
        )
        self.ticket_user_id = user_id
        self.channel_id = channel_id

    async def callback(self, interaction: discord.Interaction):
        ch = interaction.channel
        if not isinstance(ch, discord.TextChannel):
            return await interaction.response.send_message(
                "Ce n'est pas un salon texte.", ephemeral=True
            )
        if ch.id != self.channel_id:
            return await interaction.response.send_message(
                "Bouton invalide pour ce salon.", ephemeral=True
            )
        member = interaction.user
        if not isinstance(member, discord.Member):
            return await interaction.response.send_message(
                "Action réservée aux membres du serveur.", ephemeral=True
            )
        cfg = config.get("guilds", {}).get(str(ch.guild.id))
        if not can_staff_close(member, cfg):
            return await interaction.response.send_message(
                "Refusé : rôle / permission staff requis.", ephemeral=True
            )
        await interaction.response.defer(ephemeral=True)
        await execute_ticket_close(
            interaction.client, ch, member, interaction=interaction
        )


class TicketAdminPanel(discord.ui.View):
    def __init__(self, user_id: int, channel_id: int):
        super().__init__(timeout=None)
        self.add_item(ProfileButton(user_id, channel_id))
        self.add_item(CloseTicketButton(user_id, channel_id))


class CreateConfigModal(discord.ui.Modal, title="Créer une configuration d'invitation"):
    def __init__(self):
        super().__init__()

        self.name = discord.ui.TextInput(
            label="Nom de la configuration",
            placeholder="Ex: Bot Principal",
            required=True,
            max_length=50,
        )
        self.add_item(self.name)

        self.client_id = discord.ui.TextInput(
            label="Client ID du bot (optionnel)",
            placeholder="123456789",
            required=False,
            max_length=30,
        )
        self.add_item(self.client_id)

        self.permissions = discord.ui.TextInput(
            label="Permissions (bitmask)",
            placeholder="8",
            required=True,
            max_length=20,
        )
        self.add_item(self.permissions)

        self.custom_url = discord.ui.TextInput(
            label="URL OAuth2 personnalisée (optionnel)",
            placeholder="https://discord.com/oauth2/authorize?...",
            required=False,
            max_length=500,
            style=discord.TextStyle.paragraph,
        )
        self.add_item(self.custom_url)

        self.recommended_guild = discord.ui.TextInput(
            label="Serveur Discord recommandé (optionnel)",
            placeholder="123456789",
            required=False,
            max_length=30,
        )
        self.add_item(self.recommended_guild)

    async def on_submit(self, interaction: discord.Interaction):
        if not is_super_admin(interaction.user.id):
            return await interaction.response.send_message(
                "❌ Accès refusé.", ephemeral=True
            )

        config_name = self.name.value.strip()
        config_data = {
            "client_id": self.client_id.value.strip(),
            "permissions": self.permissions.value.strip(),
            "custom_url": self.custom_url.value.strip(),
            "recommended_guild_id": self.recommended_guild.value.strip(),
        }

        full_config = load_invite_config()
        configurations = full_config.get("configurations", {})

        if config_name in configurations:
            return await interaction.response.send_message(
                f"❌ Une configuration nommée '{config_name}' existe déjà.",
                ephemeral=True
            )

        configurations[config_name] = config_data
        full_config["configurations"] = configurations

        if save_invite_config(full_config):
            embed = discord.Embed(
                title="✅ Configuration créée",
                description=f"La configuration '{config_name}' a été créée avec succès.",
                color=discord.Color.green(),
            )
            embed.add_field(name="Nom", value=f"`{config_name}`", inline=True)
            embed.add_field(name="Client ID", value=f"`{config_data['client_id'] or 'Non défini'}`", inline=True)
            embed.add_field(name="Permissions", value=f"`{config_data['permissions']}`", inline=True)
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(
                "❌ Erreur lors de la sauvegarde de la configuration.", ephemeral=True
            )


class SendInviteModal(discord.ui.Modal, title="Envoyer le lien d'invitation"):
    def __init__(self, config_name: str):
        super().__init__()
        self.config_name = config_name
        
        self.user_id = discord.ui.TextInput(
            label="User ID",
            placeholder="123456789",
            required=True,
            max_length=30,
        )
        self.add_item(self.user_id)

    async def on_submit(self, interaction: discord.Interaction):
        if not is_super_admin(interaction.user.id):
            return await interaction.response.send_message(
                "❌ Accès refusé.", ephemeral=True
            )

        user_id_str = self.user_id.value.strip()
        
        try:
            user_id = int(user_id_str)
        except ValueError:
            return await interaction.response.send_message(
                "❌ ID utilisateur invalide. Veuillez entrer un nombre valide.",
                ephemeral=True
            )

        config_data = get_invite_config_by_name(self.config_name)
        if not config_data:
            return await interaction.response.send_message(
                f"❌ Configuration '{self.config_name}' introuvable.",
                ephemeral=True
            )

        if config_data["custom_url"]:
            invite_url = config_data["custom_url"]
        elif config_data["client_id"]:
            invite_url = generate_invite_url(
                config_data["client_id"],
                config_data["permissions"],
                config_data.get("recommended_guild_id", "")
            )
        else:
            return await interaction.response.send_message(
                "❌ Configuration invalide (client_id ou custom_url requis).",
                ephemeral=True
            )

        try:
            target_user = await bot.fetch_user(user_id)
        except discord.NotFound:
            return await interaction.response.send_message(
                f"❌ Utilisateur introuvable (ID: {user_id}).",
                ephemeral=True
            )
        except discord.HTTPException as e:
            return await interaction.response.send_message(
                f"❌ Erreur lors de la récupération de l'utilisateur: {e}",
                ephemeral=True
            )

        embed = discord.Embed(
            title="🔗 Lien d'invitation",
            description=f"Configuration: {self.config_name}\nClique sur le bouton ci-dessous pour inviter le bot sur votre serveur.",
            color=THEME,
        )
        if bot.user and bot.user.avatar:
            embed.set_thumbnail(url=bot.user.avatar.url)
        
        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label="🚀 Inviter le bot",
            style=discord.ButtonStyle.url,
            url=invite_url
        ))
        
        try:
            await target_user.send(embed=embed, view=view)
            await interaction.response.send_message(
                "✅ Lien envoyé avec succès",
                ephemeral=True
            )
        except discord.Forbidden:
            return await interaction.response.send_message(
                f"❌ Impossible d'envoyer un DM à {target_user.mention} (DM fermés ou bloqués).",
                ephemeral=True
            )
        except discord.HTTPException as e:
            return await interaction.response.send_message(
                f"❌ Erreur lors de l'envoi du DM: {e}",
                ephemeral=True
            )


class InvitePanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.selected_config = None
        self._setup_select()

    def _setup_select(self):
        config_data = load_invite_config()
        configurations = config_data.get("configurations", {})
        options = []
        for name in configurations.keys():
            options.append(discord.SelectOption(label=name, value=name))
        if not options:
            options.append(discord.SelectOption(label="Aucune configuration", value="none", emoji="❌"))
        
        for item in self.children:
            if isinstance(item, discord.ui.Select) and item.custom_id == "invite_config_select":
                item.options = options
                break

    @discord.ui.select(
        placeholder="📋 Sélectionner une configuration",
        custom_id="invite_config_select",
        min_values=1,
        max_values=1,
    )
    async def select_config(self, interaction: discord.Interaction, select: discord.ui.Select):
        if not is_super_admin(interaction.user.id):
            return await interaction.response.send_message(
                "❌ Accès refusé.", ephemeral=True
            )

        selected = select.values[0]
        if selected == "none":
            self.selected_config = None
            await interaction.response.send_message(
                "❌ Aucune configuration sélectionnée.",
                ephemeral=True
            )
        else:
            self.selected_config = selected
            config_data = get_invite_config_by_name(selected)
            if config_data:
                embed = discord.Embed(
                    title=f"⚙️ Configuration : {selected}",
                    color=THEME,
                )
                if bot.user and bot.user.avatar:
                    embed.set_thumbnail(url=bot.user.avatar.url)
                embed.add_field(name="Client ID", value=f"`{config_data['client_id'] or 'Non défini'}`", inline=True)
                embed.add_field(name="Permissions", value=f"`{config_data['permissions']}`", inline=True)
                embed.add_field(name="URL personnalisée", value=f"`{config_data['custom_url'] or 'Non définie'}`", inline=False)
                embed.add_field(name="Serveur recommandé", value=f"`{config_data.get('recommended_guild_id', 'Non défini')}`", inline=True)
                await interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(
                    f"❌ Configuration '{selected}' introuvable.",
                    ephemeral=True
                )

    @discord.ui.button(
        label="➕ Créer une configuration",
        style=discord.ButtonStyle.success,
        custom_id="invite_create",
    )
    async def create_config(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_super_admin(interaction.user.id):
            return await interaction.response.send_message(
                "❌ Accès refusé.", ephemeral=True
            )

        await interaction.response.send_modal(CreateConfigModal())

    @discord.ui.button(
        label="✅ Générer un lien d'invitation",
        style=discord.ButtonStyle.success,
        custom_id="invite_generate",
    )
    async def generate_invite(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_super_admin(interaction.user.id):
            return await interaction.response.send_message(
                "❌ Accès refusé.", ephemeral=True
            )

        if not self.selected_config:
            return await interaction.response.send_message(
                "❌ Veuillez sélectionner une configuration d'abord.",
                ephemeral=True
            )

        config_data = get_invite_config_by_name(self.selected_config)
        if not config_data:
            return await interaction.response.send_message(
                f"❌ Configuration '{self.selected_config}' introuvable.",
                ephemeral=True
            )

        if config_data["custom_url"]:
            invite_url = config_data["custom_url"]
        elif config_data["client_id"]:
            invite_url = generate_invite_url(
                config_data["client_id"],
                config_data["permissions"],
                config_data.get("recommended_guild_id", "")
            )
        else:
            return await interaction.response.send_message(
                "❌ Configuration invalide (client_id ou custom_url requis).",
                ephemeral=True
            )

        embed = discord.Embed(
            title=f"🔗 Lien d'invitation - {self.selected_config}",
            description="Clique sur le bouton ci-dessous pour inviter le bot sur votre serveur.",
            color=THEME,
        )
        if bot.user and bot.user.avatar:
            embed.set_thumbnail(url=bot.user.avatar.url)
        if config_data["client_id"]:
            embed.add_field(name="Client ID", value=f"`{config_data['client_id']}`", inline=True)
        if config_data["permissions"]:
            embed.add_field(name="Permissions", value=f"`{config_data['permissions']}`", inline=True)

        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label="🚀 Inviter le bot",
            style=discord.ButtonStyle.url,
            url=invite_url
        ))

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(
        label="📩 Envoyer le lien d'invitation",
        style=discord.ButtonStyle.primary,
        custom_id="invite_send",
    )
    async def send_invite(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_super_admin(interaction.user.id):
            return await interaction.response.send_message(
                "❌ Accès refusé.", ephemeral=True
            )

        if not self.selected_config:
            return await interaction.response.send_message(
                "❌ Veuillez sélectionner une configuration d'abord.",
                ephemeral=True
            )

        await interaction.response.send_modal(SendInviteModal(self.selected_config))

    @discord.ui.button(
        label="🗑️ Supprimer la configuration",
        style=discord.ButtonStyle.danger,
        custom_id="invite_delete",
    )
    async def delete_config(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_super_admin(interaction.user.id):
            return await interaction.response.send_message(
                "❌ Accès refusé.", ephemeral=True
            )

        if not self.selected_config:
            return await interaction.response.send_message(
                "❌ Veuillez sélectionner une configuration d'abord.",
                ephemeral=True
            )

        full_config = load_invite_config()
        configurations = full_config.get("configurations", {})

        if self.selected_config not in configurations:
            return await interaction.response.send_message(
                f"❌ Configuration '{self.selected_config}' introuvable.",
                ephemeral=True
            )

        del configurations[self.selected_config]
        full_config["configurations"] = configurations

        if save_invite_config(full_config):
            embed = discord.Embed(
                title="🗑️ Configuration supprimée",
                description=f"La configuration '{self.selected_config}' a été supprimée avec succès.",
                color=discord.Color.orange(),
            )
            self.selected_config = None
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(
                "❌ Erreur lors de la suppression de la configuration.", ephemeral=True
            )


class TicketBot(commands.Bot):
    async def setup_hook(self) -> None:
        self.add_view(PanelView())
        self.add_view(InvitePanelView())
        for uid, t in store.get_tickets_dict().items():
            try:
                self.add_view(TicketAdminPanel(int(uid), int(t["channel_id"])))
            except (TypeError, ValueError):
                continue
        await self.tree.sync()


intents = discord.Intents.all()
bot = TicketBot(command_prefix="!", intents=intents)

cfg_ctrl = ConfigController(bot, config, CONFIG_FILE, load_cfg_file)
setup_security_panel(bot, config, cfg_ctrl, command_enabled_check)


def _embed_trim(text: str, limit: int = 3900) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


async def relay_dm_to_ticket_channel(
    message: discord.Message, channel: discord.TextChannel
) -> None:
    """MP utilisateur → salon ticket (embed)."""
    desc = (
        _embed_trim(message.content.strip())
        if message.content and message.content.strip()
        else "*_(sans texte — pièces jointes ci-dessous si besoin)_*"
    )
    embed = discord.Embed(
        description=desc,
        color=COLOR_RELAY_USER,
        timestamp=message.created_at,
    )
    embed.set_author(
        name=f"{message.author.display_name}",
        icon_url=message.author.display_avatar.url,
    )
    embed.set_footer(
        text="📩 Demandeur · écrit uniquement en MP · le staff répond dans ce salon"
    )
    await channel.send(embed=embed)
    if message.attachments:
        files: list[discord.File] = []
        for a in message.attachments[:10]:
            try:
                raw = await a.read()
                files.append(
                    discord.File(io.BytesIO(raw), filename=a.filename or "fichier")
                )
            except Exception:
                await channel.send(f"📎 {a.url}")
        if files:
            await channel.send(files=files)
    if message.embeds and not message.content and not message.attachments:
        for e in message.embeds[:3]:
            try:
                await channel.send(embed=discord.Embed.from_dict(e.to_dict()))
            except Exception:
                await channel.send("*_(embed non relayé)_*")


async def relay_ticket_channel_to_dm(
    message: discord.Message, ticket_owner_id: int
) -> None:
    """Salon ticket → MP du demandeur (embed)."""
    try:
        user = await bot.fetch_user(ticket_owner_id)
    except discord.NotFound:
        return
    
    chunks: list[str] = []
    if message.content and message.content.strip():
        chunks.append(message.content.strip())
    for e in message.embeds[:3]:
        title = e.title or ""
        desc = e.description or ""
        ex = " | ".join(x for x in (title, desc) if x).strip()
        if ex:
            chunks.append(f"📎 {ex}")
    body = "\n\n".join(chunks) if chunks else "*_(message du staff)_*"
    embed = discord.Embed(
        description=_embed_trim(body),
        color=COLOR_RELAY_STAFF,
        timestamp=message.created_at,
    )
    embed.set_author(
        name=f"{message.author.display_name} · Staff",
        icon_url=message.author.display_avatar.url,
    )
    if message.guild:
        embed.set_footer(text=f"{message.guild.name} · #{message.channel.name}")
    files: list[discord.File] = []
    for a in message.attachments[:10]:
        try:
            raw = await a.read()
            files.append(
                discord.File(io.BytesIO(raw), filename=a.filename or "fichier")
            )
        except Exception:
            embed.description = _embed_trim(
                (embed.description or "") + f"\n📎 {a.url}"
            )
    try:
        if files:
            await user.send(embed=embed, files=files)
        else:
            await user.send(embed=embed)
    except discord.Forbidden:
        await message.channel.send(
            f"⚠️ Je ne peux pas MP <@{ticket_owner_id}> (DM fermés). "
            "Le membre doit autoriser les MP du serveur / du bot."
        )


@bot.event
async def on_ready():
    global config
    new = load_cfg_file(CONFIG_FILE)
    config.clear()
    config.update(new)
    print("================================")
    print("✅ BOT CONNECTÉ :", bot.user)
    print("================================")
    bot.loop.create_task(background_jobs())
    bot.loop.create_task(check_unauthorized_servers())


async def update_bot_activity():
    """Met à jour l'activité du bot avec le nombre d'admins connectés par serveur."""
    if not bot.guilds:
        return

    admins_online_ids = set()
    total_servers = 0

    for guild in bot.guilds:
        gid = str(guild.id)
        cfg = config.get("guilds", {}).get(gid)
        if not cfg:
            continue

        total_servers += 1
        admin_role_ids = cfg.get("admin_roles", [])

        for member in guild.members:
            if member.bot:
                continue

            is_admin = False
            for role_id in admin_role_ids:
                role = guild.get_role(int(role_id))
                if role and role in member.roles:
                    is_admin = True
                    break

            if not is_admin:
                if member.guild_permissions.manage_channels or member.guild_permissions.manage_guild:
                    is_admin = True

            if is_admin and member.status in (discord.Status.online, discord.Status.idle, discord.Status.dnd):
                admins_online_ids.add(member.id)

    activity_text = f"{len(admins_online_ids)} admin(s) en ligne | {total_servers} serveur(s)"
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=activity_text))


async def background_jobs():
    await bot.wait_until_ready()
    while not bot.is_closed():
        await process_web_queue()
        await process_close_queue()
        await update_bot_activity()
        await asyncio.sleep(5)


async def check_unauthorized_servers():
    """Vérifie et quitte les serveurs non autorisés."""
    await bot.wait_until_ready()
    await asyncio.sleep(10)
    
    while not bot.is_closed():
        for guild in bot.guilds:
            gid = str(guild.id)
            if gid not in config.get("guilds", {}):
                print(f"⚠️ Serveur non autorisé détecté : {guild.name} ({gid}) - Départ...")
                try:
                    await guild.leave()
                    print(f"✅ Quitte le serveur : {guild.name}")
                except Exception as e:
                    print(f"❌ Erreur en quittant {guild.name} : {e}")
        
        await asyncio.sleep(60)


async def process_close_queue():
    for row_id, channel_id, _req in store.close_queue_list():
        ch = bot.get_channel(channel_id)
        if ch is None:
            store.delete_ticket_by_channel(channel_id)
            store.close_queue_delete(row_id)
            continue
        if not isinstance(ch, discord.TextChannel):
            store.delete_ticket_by_channel(channel_id)
            store.close_queue_delete(row_id)
            continue
        try:
            await execute_ticket_close(
                bot,
                ch,
                bot.user,
                interaction=None,
                skip_permission_check=True,
            )
        except Exception as e:
            print("❌ close_queue:", e)
        finally:
            store.close_queue_delete(row_id)


async def process_web_queue():
    for t in store.web_queue_list():
        tid = t["id"]
        try:
            guild = bot.get_guild(int(t["guild_id"]))
        except (TypeError, ValueError):
            store.web_queue_delete(tid)
            continue

        if not guild:
            continue

        cfg = config.get("guilds", {}).get(str(t["guild_id"]))
        if not cfg:
            continue

        tid_conf = cfg.get("ticket") or {}
        raw_cid = tid_conf.get("category_id")
        if raw_cid is None:
            continue
        category_parent = guild.get_channel(int(raw_cid))
        if not isinstance(category_parent, discord.CategoryChannel):
            continue

        try:
            user = await bot.fetch_user(int(t["user_id"]))
        except Exception:
            continue

        uid = str(user.id)
        tickets_map = store.get_tickets_dict()
        if uid in tickets_map:
            existing = bot.get_channel(tickets_map[uid]["channel_id"])
            if existing:
                store.web_queue_delete(tid)
                continue
            store.delete_ticket(uid)

        slug = channel_name_slug(user.name, "web")
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            guild.me: discord.PermissionOverwrite(read_messages=True),
        }
        mentions: list[str] = []
        for rid in cfg.get("admin_roles", []):
            role = guild.get_role(int(rid))
            if role:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True)
                mentions.append(role.mention)

        try:
            channel = await guild.create_text_channel(
                name=f"{slug}-{tid}",
                category=category_parent,
                overwrites=overwrites,
                topic=f"Ticket web | {user} | {t.get('category', '')}",
            )
        except discord.HTTPException:
            continue

        embed = discord.Embed(
            title="🌐 Ticket Web",
            description=(
                f"👤 {user.mention}\n"
                f"📂 `{t.get('category', '-')}`\n\n"
                f"{t.get('message', '')}\n\n"
                f"_Le membre répond **uniquement en MP** avec le bot._"
            ),
            color=THEME,
        )
        if bot.user and bot.user.avatar:
            embed.set_thumbnail(url=bot.user.avatar.url)

        await channel.send(
            content=" ".join(mentions),
            embed=embed,
            view=TicketAdminPanel(user.id, channel.id),
        )

        store.upsert_ticket(uid, channel.id, str(t["guild_id"]), t.get("category", ""), via="web")
        store.stats_inc_opened()
        store.web_queue_delete(tid)


class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="📩 Contacter",
        style=discord.ButtonStyle.primary,
        custom_id="ticketmp_panel_contact",
    )
    async def dm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            return await interaction.response.send_message(
                "Cette commande doit être utilisée sur un serveur.",
                ephemeral=True,
            )
        
        guild_id = str(interaction.guild.id)
        user_id = str(interaction.user.id)
        
        # Store the guild_id for this user
        user_guild_map[user_id] = guild_id
        
        # Check if guild has categories configured
        cfg = config.get("guilds", {}).get(guild_id)
        if not cfg or not cfg.get("categories"):
            return await interaction.response.send_message(
                "❌ Ce serveur n'a pas de catégories de ticket configurées.",
                ephemeral=True,
            )
        
        try:
            # Send category selection directly in DM
            guild = interaction.guild
            embed = discord.Embed(
                title="📩 SUPPORT",
                description=(
                    "👉 Choisis une catégorie pour ouvrir un ticket\n\n"
                    "⚡ Support rapide\n🛡️ Staff disponible"
                ),
                color=discord.Color.blurple(),
            )
            if bot.user and bot.user.avatar:
                embed.set_thumbnail(url=bot.user.avatar.url)
            embed.set_footer(text=guild.name if guild else "Serveur inconnu")
            
            await interaction.user.send(
                embed=embed,
                view=CategorySelect(interaction.user, guild_id, "")
            )
            await interaction.response.send_message("📩 Regarde tes MP", ephemeral=True)
        except discord.Forbidden:
            return await interaction.response.send_message(
                "❌ Impossible de t'envoyer un MP (DM fermés ou bloqués).",
                ephemeral=True,
            )


@bot.tree.command(name="panel", description="Affiche le panneau support avec bouton MP")
@app_commands.check(command_enabled_check)
async def panel_cmd(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📩 SUPPORT",
        description="Clique pour ouvrir un ticket en message privé.",
        color=THEME,
    )
    if bot.user and bot.user.avatar:
        embed.set_thumbnail(url=bot.user.avatar.url)
    await interaction.response.send_message(embed=embed, view=PanelView())


@bot.tree.command(name="botconfig", description="Panneau complet : serveurs, salons, rôles, menu ticket…")
@app_commands.check(command_enabled_check)
async def botconfig_cmd(interaction: discord.Interaction):
    if not is_super_admin(interaction.user.id):
        return await interaction.response.send_message(
            "❌ Tu n'as pas accès à cette commande.",
            ephemeral=True,
        )
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message(
            "Commande à utiliser **sur un serveur**.", ephemeral=True
        )
    gid = str(interaction.guild.id)
    ensure_guild_entry(config, gid, interaction.guild)
    cfg_ctrl.save()
    view = ConfigRootView(cfg_ctrl, gid, THEME)
    await interaction.response.send_message(
        embed=view.build_embed(), view=view, ephemeral=True
    )


@bot.tree.command(name="ticket_stats", description="Statistiques tickets (staff)")
@app_commands.default_permissions(manage_channels=True)
@app_commands.check(command_enabled_check)
async def ticket_stats(interaction: discord.Interaction):
    if not interaction.guild:
        return await interaction.response.send_message(
            "Commande utilisabe sur un serveur.", ephemeral=True
        )
    stats = store.stats_get()
    open_count = len(store.get_tickets_dict())
    embed = discord.Embed(title="📊 Tickets", color=THEME)
    if bot.user and bot.user.avatar:
        embed.set_thumbnail(url=bot.user.avatar.url)
    embed.add_field(name="Ouverts (suivis)", value=str(open_count), inline=True)
    embed.add_field(name="Ouverts (total bot)", value=str(stats["opened"]), inline=True)
    embed.add_field(name="Fermés (total bot)", value=str(stats["closed"]), inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="admin_info", description="Informations sur tous les serveurs et admins (réservé)")
@app_commands.check(command_enabled_check)
async def admin_info_cmd(interaction: discord.Interaction):
    if not is_bot_owner(interaction.user.id):
        return await interaction.response.send_message(
            "❌ Cette commande est réservée au propriétaire du bot.", ephemeral=True
        )

    embed = discord.Embed(title="🔐 Informations Serveurs & Admins", color=THEME)
    if bot.user and bot.user.avatar:
        embed.set_thumbnail(url=bot.user.display_avatar.url if bot.user else None)

    total_admins = 0
    total_admins_online = 0
    total_members = 0
    total_tickets = len(store.get_tickets_dict())

    for guild in bot.guilds:
        gid = str(guild.id)
        cfg = config.get("guilds", {}).get(gid)
        if not cfg:
            continue

        server_name = cfg.get("name", guild.name)
        admin_role_ids = cfg.get("admin_roles", [])

        admins = []
        admins_online = []
        member_count = 0

        for member in guild.members:
            if member.bot:
                continue

            member_count += 1

            is_admin = False
            for role_id in admin_role_ids:
                role = guild.get_role(int(role_id))
                if role and role in member.roles:
                    is_admin = True
                    break

            if not is_admin:
                if member.guild_permissions.manage_channels or member.guild_permissions.manage_guild:
                    is_admin = True

            if is_admin:
                admins.append(member)
                if member.status in (discord.Status.online, discord.Status.idle, discord.Status.dnd):
                    admins_online.append(member)

        total_admins += len(admins)
        total_admins_online += len(admins_online)
        total_members += member_count

        if len(admins) > 0:
            admin_list = "\n".join([f"• {a.name} ({'🟢' if a.status == discord.Status.online else '🔴'})" for a in admins[:5]])
            if len(admins) > 5:
                admin_list += f"\n... et {len(admins) - 5} autres"
        else:
            admin_list = "Aucun admin configuré"

        embed.add_field(
            name=f"📌 {server_name}",
            value=f"**Membres:** {member_count}\n**Admins:** {len(admins)} ({len(admins_online)} en ligne)\n**Admins:**\n{admin_list}",
            inline=False
        )

    embed.add_field(
        name="📊 Résumé Global",
        value=f"**Serveurs:** {len(bot.guilds)}\n**Membres totaux:** {total_members}\n**Admins totaux:** {total_admins} ({total_admins_online} en ligne)\n**Tickets ouverts:** {total_tickets}",
        inline=False
    )

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="ticket_close", description="Ferme le ticket du salon actuel")
@app_commands.default_permissions(manage_channels=True)
@app_commands.check(command_enabled_check)
async def ticket_close_cmd(interaction: discord.Interaction):
    ch = interaction.channel
    if not isinstance(ch, discord.TextChannel) or not interaction.guild:
        return await interaction.response.send_message(
            "Utilise cette commande dans un salon du serveur.", ephemeral=True
        )
    if store.get_ticket_by_channel(ch.id) is None:
        return await interaction.response.send_message(
            "Ce salon n'est pas un ticket suivi.", ephemeral=True
        )
    member = interaction.user
    if not isinstance(member, discord.Member):
        return await interaction.response.send_message("Membre introuvable.", ephemeral=True)
    cfg = config.get("guilds", {}).get(str(interaction.guild.id))
    if not can_staff_close(member, cfg):
        return await interaction.response.send_message("Permission refusée.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)
    await execute_ticket_close(bot, ch, member, interaction=interaction)


@bot.tree.command(name="whitelist_add", description="Ajoute un utilisateur à la whitelist (réservé au propriétaire)")
@app_commands.describe(user="Utilisateur à ajouter")
@app_commands.check(command_enabled_check)
async def whitelist_add_cmd(interaction: discord.Interaction, user: discord.User):
    if not is_bot_owner(interaction.user.id):
        return await interaction.response.send_message(
            "❌ Cette commande est réservée au propriétaire du bot.", ephemeral=True
        )
    
    whitelisted = config.get("whitelisted_admins", [])
    if str(user.id) in whitelisted:
        return await interaction.response.send_message(
            f"❌ {user.mention} est déjà dans la whitelist.", ephemeral=True
        )
    
    whitelisted.append(str(user.id))
    config["whitelisted_admins"] = whitelisted
    cfg_ctrl.save()
    
    embed = discord.Embed(
        title="✅ Utilisateur ajouté à la whitelist",
        description=f"{user.mention} ({user.id}) a été ajouté à la whitelist des admins.",
        color=discord.Color.green(),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="whitelist_remove", description="Retire un utilisateur de la whitelist (réservé au propriétaire)")
@app_commands.describe(user="Utilisateur à retirer")
@app_commands.check(command_enabled_check)
async def whitelist_remove_cmd(interaction: discord.Interaction, user: discord.User):
    if not is_bot_owner(interaction.user.id):
        return await interaction.response.send_message(
            "❌ Cette commande est réservée au propriétaire du bot.", ephemeral=True
        )
    
    whitelisted = config.get("whitelisted_admins", [])
    if str(user.id) not in whitelisted:
        return await interaction.response.send_message(
            f"❌ {user.mention} n'est pas dans la whitelist.", ephemeral=True
        )
    
    whitelisted.remove(str(user.id))
    config["whitelisted_admins"] = whitelisted
    cfg_ctrl.save()
    
    embed = discord.Embed(
        title="✅ Utilisateur retiré de la whitelist",
        description=f"{user.mention} ({user.id}) a été retiré de la whitelist des admins.",
        color=discord.Color.orange(),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="whitelist_list", description="Liste tous les utilisateurs dans la whitelist")
@app_commands.check(command_enabled_check)
async def whitelist_list_cmd(interaction: discord.Interaction):
    if not has_bot_permission(interaction.user.id):
        return await interaction.response.send_message(
            "❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True
        )
    
    whitelisted = config.get("whitelisted_admins", [])
    if not whitelisted:
        return await interaction.response.send_message(
            "📋 La whitelist est vide.", ephemeral=True
        )
    
    embed = discord.Embed(
        title="📋 Whitelist des admins",
        description=f"{len(whitelisted)} utilisateur(s) autorisé(s)",
        color=THEME,
    )
    
    users_list = []
    for uid in whitelisted:
        try:
            user = await bot.fetch_user(int(uid))
            users_list.append(f"• {user.mention} ({uid})")
        except:
            users_list.append(f"• {uid} (utilisateur introuvable)")
    
    if users_list:
        embed.add_field(name="Utilisateurs", value="\n".join(users_list[:25]), inline=False)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)


class ServerAuthorizeModal(discord.ui.Modal, title="Autoriser un serveur"):
    guild_id = discord.ui.TextInput(
        label="ID du serveur",
        style=discord.TextStyle.short,
        placeholder="Entrez l'ID du serveur",
        required=True,
        max_length=32,
    )

    async def on_submit(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id) if interaction.guild else "global"
        lock = await abuse_manager.begin_modal_submit(
            interaction,
            guild_id,
            "server_authorize_modal",
            config,
            bot,
        )
        if not lock:
            return

        try:
            if not is_super_admin(interaction.user.id):
                return await interaction.response.send_message("Accès refusé", ephemeral=True)

            try:
                gid_int = int(self.guild_id.value.strip())
            except ValueError:
                return await interaction.response.send_message(
                    "❌ ID de serveur invalide.", ephemeral=True
                )

            gid = str(gid_int)
            if gid in config.get("guilds", {}):
                return await interaction.response.send_message(
                    "❌ Ce serveur est déjà autorisé.", ephemeral=True
                )

            guild = bot.get_guild(gid_int)
            ensure_guild_entry(config, gid, guild)
            cfg_ctrl.save()

            embed = discord.Embed(
                title="✅ Serveur autorisé",
                description=f"Le serveur `{gid}` a été autorisé avec succès.",
                color=discord.Color.green(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        finally:
            lock.release()
            abuse_manager.clear_modal_open(guild_id, str(interaction.user.id))


class ServerUnauthorizeModal(discord.ui.Modal, title="Désautoriser un serveur"):
    guild_id = discord.ui.TextInput(
        label="ID du serveur",
        style=discord.TextStyle.short,
        placeholder="Entrez l'ID du serveur",
        required=True,
        max_length=32,
    )

    async def on_submit(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id) if interaction.guild else "global"
        lock = await abuse_manager.begin_modal_submit(
            interaction,
            guild_id,
            "server_unauthorize_modal",
            config,
            bot,
        )
        if not lock:
            return

        try:
            if not is_super_admin(interaction.user.id):
                return await interaction.response.send_message("Accès refusé", ephemeral=True)

            try:
                gid_int = int(self.guild_id.value.strip())
            except ValueError:
                return await interaction.response.send_message(
                    "❌ ID de serveur invalide.", ephemeral=True
                )

            gid = str(gid_int)
            if gid not in config.get("guilds", {}):
                return await interaction.response.send_message(
                    "❌ Ce serveur n'est pas autorisé.", ephemeral=True
                )

            guild = bot.get_guild(gid_int)
            config["guilds"].pop(gid, None)
            cfg_ctrl.save()

            if guild:
                try:
                    await guild.leave()
                except Exception as e:
                    return await interaction.response.send_message(
                        f"❌ Erreur en quittant le serveur : {e}", ephemeral=True
                    )

            embed = discord.Embed(
                title="✅ Serveur désautorisé",
                description=f"Le serveur `{gid}` a été désautorisé.",
                color=discord.Color.orange(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        finally:
            lock.release()
            abuse_manager.clear_modal_open(guild_id, str(interaction.user.id))


class ServerPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Autoriser un serveur",
        style=discord.ButtonStyle.success,
        custom_id="server_panel_authorize",
    )
    async def authorize_server(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_super_admin(interaction.user.id):
            return await interaction.response.send_message("Accès refusé", ephemeral=True)

        guild_id = str(interaction.guild.id) if interaction.guild else "global"
        if not await abuse_manager.start_action(
            interaction,
            guild_id,
            "server_panel_authorize_button",
            config,
            bot,
            modal_open=True,
        ):
            return

        await interaction.response.send_modal(ServerAuthorizeModal())

    @discord.ui.button(
        label="Désautoriser un serveur",
        style=discord.ButtonStyle.danger,
        custom_id="server_panel_unauthorize",
    )
    async def unauthorize_server(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_super_admin(interaction.user.id):
            return await interaction.response.send_message("Accès refusé", ephemeral=True)

        guild_id = str(interaction.guild.id) if interaction.guild else "global"
        if not await abuse_manager.start_action(
            interaction,
            guild_id,
            "server_panel_unauthorize_button",
            config,
            bot,
            modal_open=True,
        ):
            return

        await interaction.response.send_modal(ServerUnauthorizeModal())


@bot.tree.command(name="server_panel", description="Ancienne commande : utilise /security")
@app_commands.check(command_enabled_check)
async def server_panel_cmd(interaction: discord.Interaction):
    if not interaction.guild:
        return await interaction.response.send_message(
            "Cette commande doit être utilisée depuis un serveur.", ephemeral=True
        )
    return await interaction.response.send_message(
        "📌 Utilise maintenant la commande `/security` pour gérer la sécurité et l'anti-abuse.",
        ephemeral=True,
    )


def get_logs_settings(gid: str, guild: discord.Guild | None) -> dict:
    gcfg = ensure_guild_entry(config, gid, guild)
    return gcfg.setdefault(
        "logs",
        {
            "channel_id": None,
            "log_commands": False,
            "log_server_auth": False,
            "log_errors": False,
            "log_admin_actions": False,
        },
    )


def build_logs_panel_embed(gid: str, guild: discord.Guild) -> discord.Embed:
    logs = get_logs_settings(gid, guild)
    channel = None
    if logs.get("channel_id"):
        channel = bot.get_channel(int(logs["channel_id"])) or guild.get_channel(int(logs["channel_id"]))

    channel_text = channel.mention if channel else "Aucun salon de logs configuré"
    embed = discord.Embed(
        title="📝 Panneau de logs du serveur",
        description=(
            "Gère les logs de ce serveur avec le panneau interactif ci-dessous.\n"
            "Seul le super admin peut utiliser cette commande."
        ),
        color=THEME,
    )
    embed.add_field(name="Salon de logs actuel", value=channel_text, inline=False)
    embed.add_field(
        name="Types de logs activés",
        value=(
            f"• Commandes : {'✅' if logs.get('log_commands') else '❌'}\n"
            f"• Autorisations de serveurs : {'✅' if logs.get('log_server_auth') else '❌'}\n"
            f"• Erreurs du bot : {'✅' if logs.get('log_errors') else '❌'}\n"
            f"• Actions admin : {'✅' if logs.get('log_admin_actions') else '❌'}"
        ),
        inline=False,
    )
    embed.set_footer(text=f"Serveur {guild.name} • ID {gid}")
    return embed


class LogsPanelView(discord.ui.View):
    def __init__(self, guild: discord.Guild, gid: str):
        super().__init__(timeout=None)
        self.guild = guild
        self.gid = gid
        self.logs = get_logs_settings(gid, guild)

        self.channel_select = discord.ui.ChannelSelect(
            placeholder="Choisir un salon texte pour les logs",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
            custom_id="logs_panel_channel_select",
        )
        self.channel_select.callback = self.set_log_channel
        self.add_item(self.channel_select)

    async def ensure_super_admin(self, interaction: discord.Interaction) -> bool:
        if not is_super_admin(interaction.user.id):
            await interaction.response.send_message("Accès refusé", ephemeral=True)
            return False
        return True

    async def update_message(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            embed=build_logs_panel_embed(self.gid, self.guild),
            view=self,
        )

    @discord.ui.button(
        label="Créer salon logs",
        style=discord.ButtonStyle.primary,
        custom_id="logs_panel_create_channel",
        row=1,
    )
    async def create_logs_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.ensure_super_admin(interaction):
            return

        guild_id = str(self.guild.id)
        if not await abuse_manager.start_action(
            interaction,
            guild_id,
            "logs_panel_create_channel",
            config,
            bot,
        ):
            return

        channel = next(
            (c for c in self.guild.text_channels if c.name.lower() == "logs-bot"),
            None,
        )
        if not channel:
            try:
                channel = await self.guild.create_text_channel("logs-bot")
            except discord.Forbidden:
                return await interaction.response.send_message(
                    "❌ Le bot n'a pas la permission de créer un salon.",
                    ephemeral=True,
                )
            except Exception as e:
                return await interaction.response.send_message(
                    f"❌ Impossible de créer le salon : {e}", ephemeral=True
                )

        self.logs["channel_id"] = int(channel.id)
        cfg_ctrl.save()
        await self.update_message(interaction)

    @discord.ui.button(
        label="Commandes",
        style=discord.ButtonStyle.secondary,
        custom_id="logs_panel_toggle_commands",
        row=1,
    )
    async def toggle_commands(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.ensure_super_admin(interaction):
            return

        guild_id = str(self.guild.id)
        if not await abuse_manager.start_action(
            interaction,
            guild_id,
            "logs_panel_toggle_commands",
            config,
            bot,
        ):
            return

        self.logs["log_commands"] = not self.logs.get("log_commands", False)
        cfg_ctrl.save()
        await self.update_message(interaction)

    @discord.ui.button(
        label="Serveur auth",
        style=discord.ButtonStyle.secondary,
        custom_id="logs_panel_toggle_server_auth",
        row=1,
    )
    async def toggle_server_auth(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.ensure_super_admin(interaction):
            return

        guild_id = str(self.guild.id)
        if not await abuse_manager.start_action(
            interaction,
            guild_id,
            "logs_panel_toggle_server_auth",
            config,
            bot,
        ):
            return

        self.logs["log_server_auth"] = not self.logs.get("log_server_auth", False)
        cfg_ctrl.save()
        await self.update_message(interaction)

    @discord.ui.button(
        label="Erreurs",
        style=discord.ButtonStyle.secondary,
        custom_id="logs_panel_toggle_errors",
        row=2,
    )
    async def toggle_errors(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.ensure_super_admin(interaction):
            return

        guild_id = str(self.guild.id)
        if not await abuse_manager.start_action(
            interaction,
            guild_id,
            "logs_panel_toggle_errors",
            config,
            bot,
        ):
            return

        self.logs["log_errors"] = not self.logs.get("log_errors", False)
        cfg_ctrl.save()
        await self.update_message(interaction)

    @discord.ui.button(
        label="Actions admin",
        style=discord.ButtonStyle.secondary,
        custom_id="logs_panel_toggle_admin_actions",
        row=2,
    )
    async def toggle_admin_actions(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.ensure_super_admin(interaction):
            return

        guild_id = str(self.guild.id)
        if not await abuse_manager.start_action(
            interaction,
            guild_id,
            "logs_panel_toggle_admin_actions",
            config,
            bot,
        ):
            return

        self.logs["log_admin_actions"] = not self.logs.get("log_admin_actions", False)
        cfg_ctrl.save()
        await self.update_message(interaction)

    async def set_log_channel(self, interaction: discord.Interaction):
        if not await self.ensure_super_admin(interaction):
            return

        guild_id = str(self.guild.id)
        if not await abuse_manager.start_action(
            interaction,
            guild_id,
            "logs_panel_select_channel",
            config,
            bot,
        ):
            return

        selected = self.channel_select.values[0]
        self.logs["channel_id"] = int(selected.id)
        cfg_ctrl.save()
        await self.update_message(interaction)


@bot.tree.command(name="logs_panel", description="Ancienne commande : utilise /security")
@app_commands.check(command_enabled_check)
async def logs_panel_cmd(interaction: discord.Interaction):
    if not interaction.guild:
        return await interaction.response.send_message(
            "Cette commande doit être utilisée depuis un serveur.", ephemeral=True
        )
    return await interaction.response.send_message(
        "📌 Utilise maintenant la commande `/security` pour gérer la sécurité et l'anti-abuse.",
        ephemeral=True,
    )


@bot.tree.command(name="server_list", description="Liste tous les serveurs autorisés")
@app_commands.check(command_enabled_check)
async def server_list_cmd(interaction: discord.Interaction):
    if not has_bot_permission(interaction.user.id):
        return await interaction.response.send_message(
            "❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True
        )
    
    guilds_config = config.get("guilds", {})
    if not guilds_config:
        return await interaction.response.send_message(
            "📋 Aucun serveur autorisé.", ephemeral=True
        )
    
    embed = discord.Embed(
        title="🌐 Serveurs autorisés",
        description=f"{len(guilds_config)} serveur(s) autorisé(s)",
        color=THEME,
    )
    
    for gid, data in guilds_config.items():
        guild = bot.get_guild(int(gid))
        guild_name = guild.name if guild else data.get("name", "Inconnu")
        status = "🟢 Actif" if guild else "🔴 Inactif"
        embed.add_field(
            name=f"{status} {guild_name}",
            value=f"ID: `{gid}`\nAdmin roles: {len(data.get('admin_roles', []))}",
            inline=False
        )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="profile", description="Affiche le profil d'un utilisateur")
@app_commands.describe(user="Utilisateur (vide = toi-même)")
@app_commands.check(command_enabled_check)
async def profile_cmd(interaction: discord.Interaction, user: discord.User | None = None):
    target = user or interaction.user
    
    embed = discord.Embed(
        title=f"👤 Profil de {target.name}",
        color=THEME,
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="Nom", value=target.name, inline=True)
    embed.add_field(name="ID", value=f"`{target.id}`", inline=True)
    embed.add_field(name="Bot", value="Oui" if target.bot else "Non", inline=True)
    
    if target.global_name:
        embed.add_field(name="Nom global", value=target.global_name, inline=True)
    
    if isinstance(interaction.user, discord.Member) and interaction.guild:
        member = interaction.guild.get_member(target.id)
        if member:
            embed.add_field(name="Rejoint le", value=member.joined_at.strftime("%d/%m/%Y %H:%M") if member.joined_at else "N/A", inline=True)
            embed.add_field(name="Rôles", value=f"{len(member.roles)} rôle(s)", inline=True)
            if member.roles:
                roles_list = [role.mention for role in member.roles if not role.is_default()]
                if roles_list:
                    embed.add_field(name="Rôles détaillés", value=", ".join(roles_list[:10]), inline=False)
    
    embed.set_footer(text=f"Créé le {target.created_at.strftime('%d/%m/%Y %H:%M')}")
    embed.timestamp = target.created_at
    
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="ticket_reopen", description="Rouvre un ticket pour un membre (catégorie auto si possible)")
@app_commands.default_permissions(manage_channels=True)
@app_commands.describe(
    membre="Utilisateur",
    categorie="Clé de catégorie (support, achat, …). Vide = dernière fermeture.",
)
@app_commands.check(command_enabled_check)
async def ticket_reopen_cmd(
    interaction: discord.Interaction,
    membre: discord.Member,
    categorie: str | None = None,
):
    if not interaction.guild:
        return await interaction.response.send_message("Serveur uniquement.", ephemeral=True)
    gid = str(interaction.guild.id)
    cfg = config.get("guilds", {}).get(gid)
    if not cfg:
        return await interaction.response.send_message("Serveur non configuré.", ephemeral=True)

    uid = str(membre.id)
    if uid in store.get_tickets_dict():
        return await interaction.response.send_message(
            "Cet utilisateur a déjà un ticket ouvert.", ephemeral=True
        )

    cat = (categorie or "").strip() or store.recently_closed_last_category(uid, gid)
    if not cat:
        return await interaction.response.send_message(
            "Aucune catégorie récente : précise `categorie` (clé exacte).",
            ephemeral=True,
        )
    if cat not in (cfg.get("categories") or {}):
        return await interaction.response.send_message(
            f"Catégorie inconnue : `{cat}`.",
            ephemeral=True,
        )

    guild = interaction.guild
    tid_conf = cfg.get("ticket") or {}
    raw_cid = tid_conf.get("category_id")
    if raw_cid is None:
        return await interaction.response.send_message(
            "Clé `ticket.category_id` absente dans config.json.",
            ephemeral=True,
        )
    category_parent = guild.get_channel(int(raw_cid))
    if not isinstance(category_parent, discord.CategoryChannel):
        return await interaction.response.send_message(
            "Catégorie Discord introuvable (vérifie `category_id`).",
            ephemeral=True,
        )

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        guild.me: discord.PermissionOverwrite(read_messages=True),
    }
    mentions: list[str] = []
    for rid in cfg.get("admin_roles", []):
        role = guild.get_role(int(rid))
        if role:
            overwrites[role] = discord.PermissionOverwrite(read_messages=True)
            mentions.append(role.mention)

    safe_name = f"{channel_name_slug(membre.name, 'ticket')}-{int(time.time())}"
    channel = await guild.create_text_channel(
        name=safe_name,
        category=category_parent,
        overwrites=overwrites,
        topic=f"Ticket de {membre} | {cat} (rouvert)",
    )

    store.upsert_ticket(uid, channel.id, gid, cat, via="discord_reopen")
    store.stats_inc_opened()

    embed = discord.Embed(
        title="🎫 Ticket rouvert",
        description=(
            f"👤 {membre.mention}\n📂 Catégorie : `{cat}`\n\n"
            f"💬 Le membre utilise les **MP** pour répondre ; toi tu écris dans ce salon."
        ),
        color=THEME,
    )
    embed.set_thumbnail(url=membre.display_avatar.url)
    await channel.send(
        content=" ".join(mentions),
        embed=embed,
        view=TicketAdminPanel(membre.id, channel.id),
    )

    await interaction.response.send_message(
        "✅ Ticket rouvert ! Réponds en MP au bot pour communiquer avec le staff.",
        ephemeral=True
    )


@bot.tree.command(name="owner_panel", description="Panel propriétaire : gérer rôles et catégories (réservé au propriétaire)")
@app_commands.check(command_enabled_check)
async def owner_panel_cmd(interaction: discord.Interaction):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message(
            "Commande à utiliser **sur un serveur**.", ephemeral=True
        )
    
    gid = str(interaction.guild.id)
    cfg = config.get("guilds", {}).get(gid)
    
    if not is_guild_owner(interaction.user) and not is_guild_admin(interaction.user, cfg) and not is_super_admin(interaction.user.id):
        return await interaction.response.send_message(
            "🔒 Accès refusé (réservé au propriétaire et aux admins du serveur).",
            ephemeral=True,
        )
    
    ensure_guild_entry(config, gid, interaction.guild)
    cfg_ctrl.save()
    view = OwnerPanelView(cfg_ctrl, gid, THEME)
    await interaction.response.send_message(
        embed=view.build_embed(), view=view, ephemeral=True
    )


@bot.tree.command(name="panel-invitation", description="Panel de gestion multi-configurations des invitations du bot (réservé)")
@app_commands.check(command_enabled_check)
async def panel_invitation_cmd(interaction: discord.Interaction):
    if not is_super_admin(interaction.user.id):
        return await interaction.response.send_message(
            "❌ Accès refusé. Cette commande est réservée.",
            ephemeral=True
        )

    embed = discord.Embed(
        title="🔗 Panel de gestion des invitations",
        description=(
            "Gérez plusieurs configurations d'invitation pour vos bots.\n\n"
            "📋 **Sélectionner** : Choisissez une configuration existante\n"
            "➕ **Créer** : Ajoutez une nouvelle configuration nommée\n"
            "✅ **Générer** : Affiche le lien d'invitation de la config sélectionnée\n"
            "📩 **Envoyer** : Envoyez le lien par DM à un utilisateur\n"
            "🗑️ **Supprimer** : Supprimez la configuration sélectionnée"
        ),
        color=THEME,
    )
    if bot.user and bot.user.avatar:
        embed.set_thumbnail(url=bot.user.avatar.url)
    embed.set_footer(text=f"Accès réservé à l'utilisateur {SUPER_ADMIN_ID}")

    await interaction.response.send_message(embed=embed, view=InvitePanelView(), ephemeral=True)


class CategorySelect(discord.ui.View):
    def __init__(self, user: discord.User, guild_id: str, first_message: str = ""):
        super().__init__(timeout=120)
        self.user = user
        self.guild_id = str(guild_id)
        self.first_message = first_message

        guild_conf = config.get("guilds", {}).get(self.guild_id, {})
        categories = guild_conf.get("categories", {})
        if not categories:
            print("❌ Aucune catégorie trouvée pour", self.guild_id)
            return
        for key, data in categories.items():
            self.add_item(CategoryButton(self, key, data))

    async def create_ticket(self, interaction: discord.Interaction, category: str):
        uid = str(self.user.id)
        tickets_map = store.get_tickets_dict()

        guild = interaction.guild or bot.get_guild(int(self.guild_id))
        if not guild:
            return await interaction.response.send_message(
                "❌ Serveur introuvable : vérifie que le bot est **invité** sur ce serveur.",
                ephemeral=True,
            )

        if uid in tickets_map:
            old_id = tickets_map[uid].get("channel_id")
            old_channel = guild.get_channel(old_id)
            if old_channel:
                return await interaction.response.send_message(
                    "❌ Tu as déjà un ticket ouvert", ephemeral=True
                )
            store.delete_ticket(uid)

        cfg = config.get("guilds", {}).get(self.guild_id)
        if not cfg:
            return await interaction.response.send_message(
                "❌ Config introuvable", ephemeral=True
            )

        tid_conf = cfg.get("ticket") or {}
        raw_cid = tid_conf.get("category_id")
        if raw_cid is None:
            return await interaction.response.send_message(
                "❌ `ticket.category_id` manquant dans la config.",
                ephemeral=True,
            )
        category_parent = guild.get_channel(int(raw_cid))
        if not isinstance(category_parent, discord.CategoryChannel):
            return await interaction.response.send_message(
                "❌ Catégorie Discord invalide (vérifie `category_id` dans config).",
                ephemeral=True,
            )

        try:
            ticket_subject = await guild.fetch_member(int(uid))
        except discord.NotFound:
            return await interaction.response.send_message(
                "❌ Tu dois être **sur le serveur** pour ouvrir un ticket depuis les MP.",
                ephemeral=True,
            )
        except discord.HTTPException:
            return await interaction.response.send_message(
                "❌ Le bot ne peut pas lire les membres du serveur. "
                "Active **Privileged Gateway Intent → Server Members Intent** pour ton bot "
                "(Discord Developer Portal → Bot).",
                ephemeral=True,
            )

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            guild.me: discord.PermissionOverwrite(read_messages=True),
        }

        mentions: list[str] = []
        for rid in cfg.get("admin_roles", []):
            role = guild.get_role(int(rid))
            if role:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True)
                mentions.append(role.mention)

        safe_name = f"{channel_name_slug(ticket_subject.name, 'ticket')}-{int(time.time())}"
        try:
            channel = await guild.create_text_channel(
                name=safe_name,
                category=category_parent,
                overwrites=overwrites,
                topic=f"Ticket de {ticket_subject} | {category}",
            )
        except discord.HTTPException as e:
            return await interaction.response.send_message(
                f"❌ Impossible de créer le salon : {e}", ephemeral=True
            )

        store.upsert_ticket(uid, channel.id, self.guild_id, category, via="discord")
        store.stats_inc_opened()

        if self.first_message:
            await channel.send(
                f"📩 **Premier message de {ticket_subject.mention} :**\n{self.first_message}"
            )
            if uid in first_messages:
                del first_messages[uid]

        embed = discord.Embed(
            title="🎫 Ticket ouvert",
            description=(
                f"👤 {ticket_subject.mention}\n"
                f"📂 Catégorie : `{category}`\n\n"
                f"💬 Le membre écrit **uniquement en MP** avec le bot — ses messages apparaissent ici.\n"
                f"Réponds dans ce salon : il recevra une **copie stylée en MP**."
            ),
            color=discord.Color.blurple(),
        )
        embed.set_thumbnail(url=ticket_subject.display_avatar.url)
        embed.set_footer(text=f"ID utilisateur: {ticket_subject.id}")

        creation_message = await channel.send(
            content=" ".join(mentions),
            embed=embed,
            view=TicketAdminPanel(ticket_subject.id, channel.id),
        )
        await creation_message.pin()

        try:
            confirm_embed = discord.Embed(
                title="✅ Ticket créé avec succès",
                description=(
                    f"🎫 Ton ticket a été créé dans la catégorie **{category}**.\n\n"
                    f"💬 Tu peux maintenant communiquer avec le staff en envoyant des messages en MP au bot.\n"
                    f"📩 Tes messages apparaîtront dans le salon admin et les réponses du staff te seront envoyées en MP."
                ),
                color=discord.Color.green(),
            )
            if bot.user and bot.user.avatar:
                confirm_embed.set_thumbnail(url=bot.user.avatar.url)
            confirm_embed.set_footer(text=f"ID ticket: {channel.id}")
            await ticket_subject.send(embed=confirm_embed)
        except discord.Forbidden:
            pass

        await interaction.response.send_message(
            "✅ Ticket créé ! Réponds en MP au bot pour communiquer avec le staff.",
            ephemeral=True
        )


class CategoryButton(discord.ui.Button):
    def __init__(self, view_ref: CategorySelect, key: str, data: dict):
        kw: dict = {
            "label": (data.get("label") or key)[:80],
            "style": discord.ButtonStyle.primary,
        }
        em = data.get("emoji")
        if em:
            kw["emoji"] = em
        super().__init__(**kw)
        self.view_ref = view_ref
        self.key = key

    async def callback(self, interaction: discord.Interaction):
        await self.view_ref.create_ticket(interaction, self.key)


@bot.event
async def on_member_join(member: discord.Member):
    """Gestion arrivée membre + MP support + bienvenue."""

    gid = str(member.guild.id)

    # Création config si besoin
    create_guild_if_missing(member.guild.id)

    cfg = config.get("guilds", {}).get(gid, {})


    # Sauvegarde serveur utilisateur
    user_guild_map[str(member.id)] = gid


    # MP support Ticket MP
    try:
        await send_support_dm(
            member,
            member.guild
        )

    except Exception as e:
        print(f"[SUPPORT DM ERROR] {e}")


    # Salon bienvenue
    arrival_id = cfg.get(
        "arrival_channel_id"
    )

    if not arrival_id:
        return


    channel = bot.get_channel(
        int(arrival_id)
    )

    if not channel:
        return



    # ==========================
    # EMBED BIENVENUE
    # ==========================

    embed = discord.Embed(
        title=f"🎉 Bienvenue {member.name} !",
        description=(
            f"👋 Salut {member.mention} !\n\n"
            f"Bienvenue sur **{member.guild.name}** 🎉\n\n"
            "✨ Nous sommes heureux de t'accueillir parmi nous.\n"
            "📜 Pense à lire le règlement et découvrir les salons disponibles.\n\n"
            f"👥 Tu es notre **{member.guild.member_count}ᵉ membre** !"
        ),
        color=discord.Color.from_rgb(
            46,
            204,
            113
        ),
        timestamp=discord.utils.utcnow()
    )


    # Avatar membre
    embed.set_thumbnail(
        url=member.display_avatar.url
    )


    # Image bienvenue
    arrival_image = cfg.get(
        "arrival_image"
    )

    if arrival_image:
        embed.set_image(
            url=arrival_image
        )


    # Informations membre
    embed.add_field(
        name="👤 Membre",
        value=(
            f"{member.mention}\n"
            f"`{member.id}`"
        ),
        inline=True
    )


    embed.add_field(
        name="📅 Arrivée",
        value=(
            f"<t:{int(discord.utils.utcnow().timestamp())}:F>"
        ),
        inline=True
    )


    embed.add_field(
        name="🌐 Serveur",
        value=(
            f"**{member.guild.name}**\n"
            f"{member.guild.member_count} membres"
        ),
        inline=True
    )


    # Logo serveur
    if member.guild.icon:

        embed.set_author(
            name=member.guild.name,
            icon_url=member.guild.icon.url
        )


    embed.set_footer(
        text=(
            f"Bienvenue sur {member.guild.name}"
        ),
        icon_url=(
            member.guild.icon.url
            if member.guild.icon
            else None
        )
    )


    await channel.send(
        content=member.mention,
        embed=embed
    )


        # ==========================
        # EMBED DEPART
        # ==========================

@bot.event
async def on_member_remove(member: discord.Member):
    """Gestion départ membre."""

    try:

        gid = str(member.guild.id)

        cfg = config.get(
            "guilds",
            {}
        ).get(
            gid,
            {}
        )


        # Salon départ
        departure_id = cfg.get(
            "departure_channel_id"
        )


        if not departure_id:
            return


        channel = bot.get_channel(
            int(departure_id)
        )


        if not channel:
            return



        # Couleur embed
        try:
            color = discord.Color.from_rgb(
                240,
                71,
                71
            )

        except:
            color = discord.Color.red()

        embed = discord.Embed(
            title=f"👋 Au revoir {member.name} !",
            description=(
                f"😢 **{member.name}** a quitté "
                f"**{member.guild.name}**.\n\n"
                "Nous espérons te revoir bientôt !\n"
                "Merci d'avoir fait partie de notre communauté ❤️"
            ),
            color=color,
            timestamp=discord.utils.utcnow()
        )


        # Avatar ancien membre
        embed.set_thumbnail(
            url=member.display_avatar.url
        )


        # Informations
        embed.add_field(
            name="👤 Ancien membre",
            value=(
                f"**{member.name}**\n"
                f"`{member.id}`"
            ),
            inline=True
        )


        embed.add_field(
            name="👥 Membres restants",
            value=(
                f"{member.guild.member_count} membres"
            ),
            inline=True
        )


        # Image départ personnalisée
        departure_image = cfg.get(
            "departure_image"
        )

        if departure_image:
            embed.set_image(
                url=departure_image
            )


        # Logo serveur
        if member.guild.icon:

            embed.set_author(
                name=member.guild.name,
                icon_url=member.guild.icon.url
            )


        embed.set_footer(
            text=(
                f"Merci d'avoir été avec nous ❤️"
            ),
            icon_url=(
                member.guild.icon.url
                if member.guild.icon
                else None
            )
        )


        await channel.send(
            embed=embed
        )


    except Exception as e:
        print(
            f"[LEAVE ERROR] {e}"
        )


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    uid = str(message.author.id)

    if isinstance(message.channel, discord.TextChannel):
        pair = store.get_ticket_by_channel(message.channel.id)
        if pair:
            owner_uid, _meta = pair
            try:
                await relay_ticket_channel_to_dm(message, int(owner_uid))
            except Exception as e:
                print("❌ relay salon→MP:", e)
            await bot.process_commands(message)
            return

    if isinstance(message.channel, discord.DMChannel):
        tickets_map = store.get_tickets_dict()
        if uid in tickets_map:
            channel = bot.get_channel(tickets_map[uid]["channel_id"])
            if isinstance(channel, discord.TextChannel):
                try:
                    await relay_dm_to_ticket_channel(message, channel)
                except Exception as e:
                    print("❌ relay MP→salon:", e)
        else:
            if uid not in first_messages:
                first_messages[uid] = message.content

            now = time.time()
            cooldown_sec = 30
            if config.get("guilds"):
                try:
                    gid_any = next(iter(config["guilds"].values()))
                    cooldown_sec = int(
                        gid_any.get("ticket", {}).get("cooldown_seconds", 30)
                    )
                except Exception:
                    pass

            if now - cooldown.get(uid, 0) > cooldown_sec:
                cooldown[uid] = now
                
                # Check if user has a guild_id stored
                guild_id = user_guild_map.get(uid)
                if guild_id:
                    # Show category selection directly for the stored guild
                    cfg = config.get("guilds", {}).get(guild_id)
                    if cfg and cfg.get("categories"):
                        guild = bot.get_guild(int(guild_id))
                        embed = discord.Embed(
                            title="📩 SUPPORT",
                            description=(
                                "👉 Choisis une catégorie pour ouvrir un ticket\n\n"
                                "⚡ Support rapide\n🛡️ Staff disponible"
                            ),
                            color=discord.Color.blurple(),
                        )
                        if bot.user and bot.user.avatar:
                            embed.set_thumbnail(url=bot.user.avatar.url)
                        embed.set_footer(text=guild.name if guild else "Serveur inconnu")
                        
                        await message.channel.send(
                            embed=embed,
                            view=CategorySelect(message.author, guild_id, message.content)
                        )
                    else:
                        await message.channel.send("❌ Ce serveur n'a pas de catégories configurées.")
                else:
                    # No guild_id stored, send simple message
                    embed = discord.Embed(
                        title="📩 SUPPORT",
                        description="Utilise le bouton 📩 sur un serveur pour ouvrir un ticket.",
                        color=THEME,
                    )
                    if bot.user and bot.user.avatar:
                        embed.set_thumbnail(url=bot.user.avatar.url)
                    await message.channel.send(embed=embed)

    await bot.process_commands(message)


def _discord_token_from_env() -> str:
    if load_dotenv:
        load_dotenv(
            os.path.join(BASE_DIR, ".env"),
            encoding="utf-8-sig",
            override=True,
        )
    raw = os.getenv("DISCORD_TOKEN") or ""
    token = raw.strip().strip('"').strip("'").replace("\ufeff", "")
    token = "".join(token.split())
    if not token:
        raise SystemExit(
            "DISCORD_TOKEN est vide ou absent.\n\n"
            "Vérifie :\n"
            "  • Un fichier .env dans le même dossier que bot.py (TicketMPbot\\.env)\n"
            "  • Une ligne du type : DISCORD_TOKEN=ton_token_sans_guillemets\n"
            "  • python-dotenv installé : python -m pip install python-dotenv\n"
            "  • Ou définis DISCORD_TOKEN dans les variables d'environnement Windows.\n"
        )
    bogus = {"cacher", "xxx", "token", "changeme", "your_token_here", "paste_here"}
    if token.lower() in bogus:
        raise SystemExit(
            "DISCORD_TOKEN est encore un placeholder (ex. « cacher »).\n"
            "Remplace-le par le vrai token du bot : Discord Developer Portal → ton application → Bot → Reset Token / copier.\n"
        )
    if token.count(".") != 2:
        print(
            "⚠️ Attention : un token bot Discord ressemble en général à trois segments séparés par des points.\n"
            "   Si tu as mis le Client Secret OAuth à la place du Bot Token, Discord renverra 401 Unauthorized.\n"
        )
    return token


if __name__ == "__main__":
    bot.run(_discord_token_from_env())
