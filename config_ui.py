"""
Panneaux Discord pour /botconfig (édition de config.json).
"""
from __future__ import annotations

import json
import re
import time
import asyncio
from typing import Any, Callable

import discord


def ticket_client_overwrite() -> discord.PermissionOverwrite:
    """Demandeurs : lisent le salon, écrivent uniquement via MP avec le bot."""
    return discord.PermissionOverwrite(
        view_channel=True,
        read_messages=True,
        send_messages=False,
        attach_files=False,
        embed_links=False,
        add_reactions=False,
        mention_everyone=False,
    )


SUPER_ADMIN_ID = 1112038418629808148

def can_use_bot_panel(member: discord.Member, cfg: dict) -> bool:
    if member.id == SUPER_ADMIN_ID:
        return True
    raw = cfg.get("whitelisted_admins")
    ids = {str(x) for x in raw} if isinstance(raw, list) else set()
    uid = str(member.id)
    return uid in ids


class ConfigController:
    """Référence mutable vers bot.config + sauvegarde disque."""

    def __init__(
        self,
        bot: discord.Client,
        config_dict: dict,
        config_path: str,
        load_fn: Callable[[str], dict],
    ):
        self.bot = bot
        self.config = config_dict
        self.path = config_path
        self.load_fn = load_fn

    def save(self) -> None:
        if "guilds" not in self.config:
            self.config["guilds"] = {}
        if "whitelisted_admins" not in self.config:
            self.config["whitelisted_admins"] = []
        if "bot_owner_id" not in self.config:
            self.config["bot_owner_id"] = ""
        if "disabled_commands" not in self.config:
            self.config["disabled_commands"] = []
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=4, ensure_ascii=False)
        new = self.load_fn(self.path)
        self.config.clear()
        self.config.update(new)


def ensure_guild_entry(cfg: dict, gid: str, guild: discord.Guild | None) -> dict:
    guilds = cfg.setdefault("guilds", {})
    if gid not in guilds:
        guilds[gid] = {
            "name": guild.name if guild else "Serveur",
            "ticket": {
                "category_id": None,
                "log_channel_id": None,
                "max_open_per_user": 1,
                "cooldown_seconds": 30,
            },
            "logs": {
                "channel_id": None,
                "log_commands": False,
                "log_server_auth": False,
                "log_errors": False,
                "log_admin_actions": False,
            },
            "anti_abuse": {
                "enabled": True,
                "cooldown_seconds": 5,
                "max_actions_per_minute": 10,
                "anti_spam": True,
                "audit_logs": False,
                "block_duration": 45,
            },
            "admin_roles": [],
            "mod_roles": [],
            "categories": {
                "support": {"label": "🛠️ Support", "emoji": "🛠️"},
            },
"arrival_embed_color": "#2ecc71",
"departure_embed_color": "#f04747",

"arrival_options": {
    "show_avatar": True,
    "show_member_count": True,
    "mention_user": True,
},

"departure_options": {
    "show_avatar": True,
    "show_old_name": True,
    "detailed_mode": False,
},

"arrival_channel_id": None,
"departure_channel_id": None,
"arrival_image": None,
"departure_image": None,
        }
    return guilds[gid]

class AddGuildByIdModal(discord.ui.Modal, title="Ajouter un serveur"):

    gid_field = discord.ui.TextInput(
        label="ID du serveur",
        placeholder="123456789012345678",
        required=True,
        max_length=25,
    )

    def __init__(self, ctrl: ConfigController):
        super().__init__()
        self.ctrl = ctrl

    async def on_submit(self, interaction: discord.Interaction):
        raw = str(self.gid_field.value).strip()

        try:
            gid_int = int(raw)
        except ValueError:
            return await interaction.response.send_message(
                "❌ ID invalide.",
                ephemeral=True
            )

        gid = str(gid_int)

        guild = self.ctrl.bot.get_guild(gid_int)

        if not guild:
            return await interaction.response.send_message(
                "❌ Le bot n'est pas présent sur ce serveur.",
                ephemeral=True
            )

        ensure_guild_entry(
            self.ctrl.config,
            gid,
            guild
        )

        self.ctrl.config["guilds"][gid]["name"] = guild.name

        self.ctrl.save()

        await interaction.response.send_message(
            f"✅ Serveur ajouté : **{guild.name}**",
            ephemeral=True
        )


class WhitelistAdminsModal(discord.ui.Modal, title="Gérer la whitelist d'admins"):
    ids_field = discord.ui.TextInput(
        label="IDs Discord (virgule ou ligne)",
        style=discord.TextStyle.paragraph,
        placeholder="123...\n456...\n(Sépare les IDs par virgule ou saut de ligne)",
        required=False,
        max_length=1800,
    )

    def __init__(self, ctrl: ConfigController):
        super().__init__()
        self.ctrl = ctrl

    async def on_submit(self, interaction: discord.Interaction):
        text = self.ids_field.value or ""
        parts = re.split(r"[\s,;]+", text.strip())
        out: list[str] = []
        for p in parts:
            if not p:
                continue
            if p.isdigit():
                out.append(p)
        self.ctrl.config["whitelisted_admins"] = out
        self.ctrl.save()
        await interaction.response.send_message(
            f"✅ Liste mise à jour ({len(out)} ID(s) autorisés).",
            ephemeral=True,
        )


class DisabledCommandsModal(discord.ui.Modal, title="Gérer les commandes désactivées"):
    commands_field = discord.ui.TextInput(
        label="Commandes désactivées (virgule ou ligne)",
        style=discord.TextStyle.paragraph,
        placeholder="panel\nbotconfig\nticket_stats\n(Sépare les noms de commandes par virgule ou saut de ligne)",
        required=False,
        max_length=1800,
    )

    def __init__(self, ctrl: ConfigController):
        super().__init__()
        self.ctrl = ctrl

    async def on_submit(self, interaction: discord.Interaction):
        text = self.commands_field.value or ""
        parts = re.split(r"[\s,;]+", text.strip())
        out: list[str] = []
        for p in parts:
            if not p:
                continue
            out.append(p.lower())
        self.ctrl.config["disabled_commands"] = out
        self.ctrl.save()
        await interaction.response.send_message(
            f"✅ Liste mise à jour ({len(out)} commande(s) désactivée(s)).",
            ephemeral=True,
        )


class AddCategoryModal(discord.ui.Modal, title="Entrée menu ticket"):
    key_field = discord.ui.TextInput(
        label="Clé (sans espace)",
        placeholder="support",
        max_length=40,
        required=True,
    )
    label_field = discord.ui.TextInput(
        label="Libellé bouton",
        placeholder="🛠️ Support",
        max_length=80,
        required=True,
    )
    emoji_field = discord.ui.TextInput(
        label="Emoji (optionnel)",
        placeholder="🛠️",
        max_length=10,
        required=False,
    )

    def __init__(self, ctrl: ConfigController, gid: str):
        super().__init__()
        self.ctrl = ctrl
        self.gid = gid

    async def on_submit(self, interaction: discord.Interaction):
        key = re.sub(r"\s+", "_", self.key_field.value.strip().lower())
        if not key:
            return await interaction.response.send_message(
                "❌ Clé invalide.", ephemeral=True
            )
        gcfg = ensure_guild_entry(self.ctrl.config, self.gid, interaction.guild)
        cats = gcfg.setdefault("categories", {})
        em = (self.emoji_field.value or "").strip() or None
        cats[key] = {"label": self.label_field.value.strip(), "emoji": em}
        self.ctrl.save()
        await interaction.response.send_message(
            f"✅ Catégorie `{key}` ajoutée.", ephemeral=True
        )


# --- Views channel / role ---


class CreateTicketCategoryModal(discord.ui.Modal, title="Créer catégorie des tickets"):
    category_name_field = discord.ui.TextInput(
        label="Nom de la catégorie",
        placeholder="Tickets",
        max_length=100,
        required=True,
    )
    channel_name_field = discord.ui.TextInput(
        label="Nom du salon principal",
        placeholder="ticket-welcome",
        max_length=100,
        required=True,
    )

    def __init__(self, ctrl: ConfigController, gid: str, theme: discord.Color):
        super().__init__()
        self.ctrl = ctrl
        self.gid = gid
        self._theme = theme

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message(
                "Cette commande doit être utilisée sur un serveur.",
                ephemeral=True
            )
        
        guild = interaction.guild
        category_name = self.category_name_field.value.strip()
        channel_name = self.channel_name_field.value.strip()
        
        # Vérifier si une catégorie est déjà configurée
        gcfg = self.ctrl.config.get("guilds", {}).get(self.gid, {})
        ticket_config = gcfg.get("ticket", {})
        existing_cat_id = ticket_config.get("category_id")
        
        if existing_cat_id:
            existing_cat = guild.get_channel(int(existing_cat_id))
            if existing_cat:
                return await interaction.response.send_message(
                    f"❌ Une catégorie est déjà configurée : **{existing_cat.name}**. Supprimez-la d'abord.",
                    ephemeral=True
                )
        
        # Vérifier les permissions du bot
        bot_member = guild.me
        if not bot_member.guild_permissions.manage_channels:
            return await interaction.response.send_message(
                "❌ Le bot n'a pas la permission MANAGE_CHANNELS.",
                ephemeral=True
            )
        
        # Vérifier si une catégorie avec ce nom existe déjà
        existing_category = discord.utils.get(guild.categories, name=category_name)
        if existing_category:
            return await interaction.response.send_message(
                f"❌ Une catégorie nommée **{category_name}** existe déjà.",
                ephemeral=True
            )
        
        try:
            # Créer la catégorie
            category = await guild.create_category(name=category_name)
            
            # Créer le salon texte dans la catégorie
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            }
            
            # Ajouter les rôles admin/mod si configurés
            for rid in gcfg.get("admin_roles", []) + gcfg.get("mod_roles", []):
                role = guild.get_role(int(rid))
                if role:
                    overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
            
            channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
            )
            
            # Sauvegarder dans la configuration
            guilds = self.ctrl.config.setdefault("guilds", {})
            guild_config = guilds.setdefault(self.gid, {})
            ticket_config = guild_config.setdefault("ticket", {})
            ticket_config["category_id"] = str(category.id)
            ticket_config["category_name"] = category.name
            ticket_config["main_channel_id"] = str(channel.id)
            ticket_config["main_channel_name"] = channel.name
            
            self.ctrl.save()
            
            # Confirmation
            embed = discord.Embed(
                title="✅ Catégorie des tickets créée",
                color=self._theme,
            )
            embed.add_field(name="Catégorie", value=f"**{category.name}**\n(ID : `{category.id}`)", inline=False)
            embed.add_field(name="Salon principal", value=f"**{channel.name}**\n(ID : `{channel.id}`)", inline=False)
            embed.add_field(
                name="ℹ️ Information",
                value="Tous les nouveaux tickets seront créés dans cette catégorie.",
                inline=False
            )
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Permission refusée lors de la création.",
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Erreur lors de la création : {e}",
                ephemeral=True
            )

class PickCategoryView(discord.ui.View):
    def __init__(self, ctrl: ConfigController, gid: str):
        super().__init__(timeout=None)
        self.ctrl = ctrl
        self.gid = gid
        sel = discord.ui.ChannelSelect(
            placeholder="Catégorie parent des tickets",
            channel_types=[discord.ChannelType.category],
            min_values=1,
            max_values=1,
        )

        async def _cb(interaction: discord.Interaction):
            cid = int(sel.values[0].id)
            gcfg = ensure_guild_entry(self.ctrl.config, self.gid, interaction.guild)
            gcfg.setdefault("ticket", {})["category_id"] = cid
            self.ctrl.save()
            await interaction.response.send_message(
                f"✅ `ticket.category_id` = `{cid}`", ephemeral=True
            )

        sel.callback = _cb
        self.add_item(sel)


class PickLogChannelView(discord.ui.View):
    def __init__(self, ctrl: ConfigController, gid: str):
        super().__init__(timeout=None)
        self.ctrl = ctrl
        self.gid = gid
        sel = discord.ui.ChannelSelect(
            placeholder="Salon texte pour les logs",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

        async def _cb(interaction: discord.Interaction):
            cid = int(sel.values[0].id)
            gcfg = ensure_guild_entry(self.ctrl.config, self.gid, interaction.guild)
            gcfg.setdefault("ticket", {})["log_channel_id"] = cid
            self.ctrl.save()
            await interaction.response.send_message(
                f"✅ `ticket.log_channel_id` = `{cid}`", ephemeral=True
            )

        sel.callback = _cb
        self.add_item(sel)


class PickStaffRolesView(discord.ui.View):
    def __init__(self, ctrl: ConfigController, gid: str):
        super().__init__(timeout=None)
        self.ctrl = ctrl
        self.gid = gid
        sel = discord.ui.RoleSelect(
            placeholder="Rôles staff (accès tickets)",
            min_values=0,
            max_values=25,
        )

        async def _cb(interaction: discord.Interaction):
            rids = [r.id for r in sel.values]
            gcfg = ensure_guild_entry(self.ctrl.config, self.gid, interaction.guild)
            gcfg["admin_roles"] = rids
            self.ctrl.save()
            await interaction.response.send_message(
                f"✅ {len(rids)} rôle(s) enregistré(s).", ephemeral=True
            )

        sel.callback = _cb
        self.add_item(sel)


class RemoveCategoryView(discord.ui.View):
    def __init__(self, ctrl: ConfigController, gid: str):
        super().__init__(timeout=None)
        self.ctrl = ctrl
        self.gid = gid
        gcfg = ctrl.config.get("guilds", {}).get(gid, {})
        cats = gcfg.get("categories") or {}
        opts = [
            discord.SelectOption(label=k[:100], value=k, description=v.get("label", "")[:100])
            for k, v in list(cats.items())[:25]
        ]
        sel = discord.ui.Select(placeholder="Clé à supprimer", options=opts)

        async def _cb(interaction: discord.Interaction):
            k = sel.values[0]
            cats2 = (
                self.ctrl.config.get("guilds", {}).get(self.gid, {}).get("categories") or {}
            )
            cats2.pop(k, None)
            self.ctrl.save()
            await interaction.response.send_message(
                f"✅ Clé `{k}` supprimée.", ephemeral=True
            )

        sel.callback = _cb
        self.add_item(sel)


class PickModRolesView(discord.ui.View):
    def __init__(self, ctrl: ConfigController, gid: str):
        super().__init__(timeout=None)
        self.ctrl = ctrl
        self.gid = gid
        sel = discord.ui.RoleSelect(
            placeholder="Rôles modérateurs",
            min_values=0,
            max_values=25,
        )

        async def _cb(interaction: discord.Interaction):
            rids = [r.id for r in sel.values]
            gcfg = ensure_guild_entry(self.ctrl.config, self.gid, interaction.guild)
            gcfg["mod_roles"] = rids
            self.ctrl.save()
            await interaction.response.send_message(
                f"✅ {len(rids)} rôle(s) modérateur(s) enregistré(s).", ephemeral=True
            )

        sel.callback = _cb
        self.add_item(sel)


class OwnerPanelView(discord.ui.View):
    def __init__(self, ctrl: ConfigController, gid: str, theme: discord.Color):
        super().__init__(timeout=None)
        self.ctrl = ctrl
        self.gid = gid
        self._theme = theme

    def build_embed(self) -> discord.Embed:
        cfg = self.ctrl.config
        gcfg = cfg.get("guilds", {}).get(self.gid, {})
        admin_roles = gcfg.get("admin_roles") or []
        mod_roles = gcfg.get("mod_roles") or []
        cats = gcfg.get("categories") or {}
        ticket_config = gcfg.get("ticket", {})
        current_cat_id = ticket_config.get("category_id")
        current_cat_name = ticket_config.get("category_name")
        
        emb = discord.Embed(
            title="👑 Panel Propriétaire",
            description="Gestion des rôles et des catégories de tickets.",
            color=self._theme,
        )
        emb.add_field(name="Serveur", value=f"`{self.gid}` · {gcfg.get('name', '—')}", inline=False)
        
        # Afficher la catégorie des tickets actuelle
        if current_cat_id:
            main_channel_id = ticket_config.get("main_channel_id")
            main_channel_name = ticket_config.get("main_channel_name")
            cat_display = f"**{current_cat_name or 'Nom inconnu'}**\n(ID : `{current_cat_id}`)"
            if main_channel_id:
                cat_display += f"\n📌 Salon : **{main_channel_name or 'Nom inconnu'}** (ID : `{main_channel_id}`)"
        else:
            cat_display = "*Non configurée*"
        emb.add_field(name="📂 Catégorie des tickets actuelle", value=cat_display, inline=False)
        
        emb.add_field(
            name="Rôles Admin",
            value=", ".join(f"<@&{r}>" for r in admin_roles) if admin_roles else "*aucun*",
            inline=False,
        )
        emb.add_field(
            name="Rôles Modérateur",
            value=", ".join(f"<@&{r}>" for r in mod_roles) if mod_roles else "*aucun*",
            inline=False,
        )
        emb.add_field(
            name="Catégories de tickets",
            value="\n".join([f"• `{k}`: {v.get('label', '')}" for k, v in cats.items()]) if cats else "*aucune*",
            inline=False,
        )
        return emb

    @discord.ui.button(label="👮 Gérer rôles Admin", style=discord.ButtonStyle.primary, row=0)
    async def b_admin_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Sélectionne les rôles admin :",
            view=PickStaffRolesView(self.ctrl, self.gid),
            ephemeral=True,
        )

    @discord.ui.button(label="🛡️ Gérer rôles Modérateur", style=discord.ButtonStyle.primary, row=0)
    async def b_mod_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Sélectionne les rôles modérateur :",
            view=PickModRolesView(self.ctrl, self.gid),
            ephemeral=True,
        )

    @discord.ui.button(label="➕ Ajouter catégorie", style=discord.ButtonStyle.success, row=1)
    async def b_add_category(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddCategoryModal(self.ctrl, self.gid))

    @discord.ui.button(label="🗑 Supprimer catégorie", style=discord.ButtonStyle.danger, row=1)
    async def b_rm_category(self, interaction: discord.Interaction, button: discord.ui.Button):
        cats = (
            self.ctrl.config.get("guilds", {}).get(self.gid, {}).get("categories") or {}
        )
        if not cats:
            return await interaction.response.send_message(
                "Aucune catégorie à supprimer.", ephemeral=True
            )
        await interaction.response.send_message(
            "Catégorie à supprimer :",
            view=RemoveCategoryView(self.ctrl, self.gid),
            ephemeral=True,
        )

    @discord.ui.button(label="📂 Créer la catégorie des tickets", style=discord.ButtonStyle.primary, row=1)
    async def b_create_ticket_category(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CreateTicketCategoryModal(self.ctrl, self.gid, self._theme))
    @discord.ui.button(label="🔄 Rafraîchir", style=discord.ButtonStyle.secondary, row=2)
    async def b_refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        emb = self.build_embed()
        await interaction.response.edit_message(embed=emb, view=self)


# --- New modular dashboard views for /config ---


class ConfigDashboardView(discord.ui.View):
    def __init__(self, ctrl: ConfigController, gid: str, theme: discord.Color):
        super().__init__(timeout=None)
        self.ctrl = ctrl
        self.gid = gid
        self._theme = theme

    def build_embed(self) -> discord.Embed:
        emb = discord.Embed(
            title="⚙️ Configuration du serveur",
            description="Sélectionne une catégorie ci‑dessous pour modifier la configuration.",
            color=self._theme,
        )
        return emb

    @discord.ui.select(
        placeholder="📋 Sélectionne une catégorie",
        custom_id="config_category_select",
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(
                label="🛬 Arrivée / Départ",
                value="arrival_departure",
                description="Configurer les messages d'arrivée et de départ",
            ),
            discord.SelectOption(
                label="🎤 Salon vocaux temporaires",
                value="voice_temp",
                description="Permettre aux utilisateurs de créer des salons vocaux privés à la demande",
            ),
        ],
    )
    async def select_category(self, interaction: discord.Interaction, select: discord.ui.Select):
        val = select.values[0]
        if val == "arrival_departure":
            view = ArrivalDepartureView(self.ctrl, self.gid, self._theme)
            emb = discord.Embed(
                title="🛬 Arrivée / Départ",
                description="Configure les salons où les messages d'arrivée et de départ seront publiés.",
                color=self._theme,
            )
            await interaction.response.edit_message(embed=emb, view=view)
        elif val == "voice_temp":
            view = VoiceTempConfigView(self.ctrl, self.gid, self._theme)
            emb = discord.Embed(
                title="🎤 Salon vocaux temporaires",
                description=(
                    "Join-To-Create automatique : crée un salon privé quand un utilisateur rejoint le salon \n"
                    "de trigger. Configure ci‑dessous le salon de trigger (salon vocal fixe) et la catégorie parent."
                ),
                color=self._theme,
            )
            await interaction.response.edit_message(embed=emb, view=view)


class ArrivalDepartureView(discord.ui.View):
    """View principale pour la configuration des messages d'arrivée/départ."""

    def __init__(self, ctrl: ConfigController, gid: str, theme: discord.Color):
        super().__init__(timeout=None)
        self.ctrl = ctrl
        self.gid = gid
        self._theme = theme

        guild = self.ctrl.bot.get_guild(int(gid))
        self.gcfg = ensure_guild_entry(
            self.ctrl.config,
            self.gid,
            guild
        )

        self.arrival_opts = self.gcfg.setdefault(
            "arrival_options",
            {}
        )

        self.departure_opts = self.gcfg.setdefault(
            "departure_options",
            {}
        )

        self._sync_button_states()

    # ── helpers ──────────────────────────────────────────────

    def _save(self) -> None:
        self.ctrl.save()

    def _toggle(self, key: str, mode: str) -> None:
        if mode == "arrival":
            opts = self.arrival_opts
        else:
            opts = self.departure_opts
        opts[key] = not opts.get(key, False)
        self._save()

    def _parse_color(self, color_value: str) -> discord.Color:
        try:
            return discord.Color(int(str(color_value).lstrip("#"), 16))
        except Exception:
            return self._theme

    def build_preview_embeds(self) -> list[discord.Embed]:
        self._sync_button_states()

        user = self.ctrl.bot.user
        guild = self.ctrl.bot.get_guild(int(self.gid))
        if not guild:
            guild = discord.Object(id=int(self.gid))  # fallback stub

        arrival = self._build_arrival_preview(user, guild)
        departure = self._build_departure_preview(user, guild)
        return [arrival, departure]

    def _build_arrival_preview(self, user, guild) -> discord.Embed:
        color = self._parse_color(self.gcfg.get("arrival_embed_color", "#2ecc71"))
        desc = (
            f"👋 Salut {user.mention if hasattr(user, 'mention') else '@user'} !\n\n"
            f"Bienvenue sur **{getattr(guild, 'name', 'Serveur')}** 🎉\n\n"
            "✨ Nous sommes heureux de t'accueillir.\n"
            "📜 Pense à lire le règlement et découvrir les salons.\n\n"
            f"👥 Nous sommes maintenant **{getattr(guild, 'member_count', '?')} membres**."
        )
        embed = discord.Embed(
            title="🎉 Bienvenue !",
            description=desc,
            color=color,
            timestamp=discord.utils.utcnow(),
        )
        if user and user.display_avatar and self.arrival_opts.get("show_avatar", True):
            embed.set_thumbnail(url=user.display_avatar.url)
        image = self.gcfg.get("arrival_image")
        if image:
            embed.set_image(url=image)
        if guild and getattr(guild, 'icon', None):
            embed.set_author(name=guild.name, icon_url=guild.icon.url)
        embed.set_footer(text=f"Bienvenue sur {getattr(guild, 'name', 'Serveur')}")
        return embed

    def _build_departure_preview(self, user, guild) -> discord.Embed:
        color = self._parse_color(self.gcfg.get("departure_embed_color", "#f04747"))
        desc = (
            f"😢 {getattr(user, 'name', 'Utilisateur')} a quitté **{getattr(guild, 'name', 'Serveur')}**.\n\n"
            f"👤 ID : `{getattr(user, 'id', '?')}`\n\n"
            f"👥 Membres restants : **{getattr(guild, 'member_count', '?')}**"
        )
        embed = discord.Embed(
            title="👋 Membre parti",
            description=desc,
            color=color,
            timestamp=discord.utils.utcnow(),
        )
        if user and user.display_avatar and self.departure_opts.get("show_avatar", True):
            embed.set_thumbnail(url=user.display_avatar.url)
        if guild and getattr(guild, 'icon', None):
            embed.set_author(name=guild.name, icon_url=guild.icon.url)
        embed.set_footer(text=f"Au revoir de {getattr(guild, 'name', 'Serveur')}")
        return embed

    def _sync_button_states(self) -> None:
        for child in self.children:
            if not isinstance(child, discord.ui.Button) or not child.custom_id:
                continue
            if child.custom_id.startswith("arrival_toggle:"):
                key = child.custom_id.split(":", 1)[1]
                active = self.arrival_opts.get(key, False)
                child.style = discord.ButtonStyle.success if active else discord.ButtonStyle.secondary
                child.label = f"{child.label.split(' ')[0]} {'ON' if active else 'OFF'}"
            if child.custom_id.startswith("departure_toggle:"):
                key = child.custom_id.split(":", 1)[1]
                active = self.departure_opts.get(key, False)
                child.style = discord.ButtonStyle.success if active else discord.ButtonStyle.secondary
                child.label = f"{child.label.split(' ')[0]} {'ON' if active else 'OFF'}"

    # ── Channel selection ────────────────────────────────────

    @discord.ui.button(label="🟢 Définir salon des arrivées", style=discord.ButtonStyle.success, custom_id="config_set_arrival")
    async def set_arrival(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = ChannelSelectView(self.ctrl, self.gid, self._theme, key="arrival_channel_id")
        emb = discord.Embed(
            title="🟢 Définir salon des arrivées",
            description="Choisis un salon texte où les messages d'arrivée seront envoyés.",
            color=self._theme,
        )
        await interaction.response.edit_message(embed=emb, view=view)

    @discord.ui.button(label="🔴 Définir salon des départs", style=discord.ButtonStyle.danger, custom_id="config_set_departure")
    async def set_departure(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = ChannelSelectView(self.ctrl, self.gid, self._theme, key="departure_channel_id")
        emb = discord.Embed(
            title="🔴 Définir salon des départs",
            description="Choisis un salon texte où les messages de départ seront envoyés.",
            color=self._theme,
        )
        await interaction.response.edit_message(embed=emb, view=view)

    # ── Test buttons ─────────────────────────────────────────

    @discord.ui.button(label="🧪 Tester arrivée", style=discord.ButtonStyle.success, row=2)
    async def test_arrival(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._send_test_message(interaction, arrival=True)

    @discord.ui.button(label="🧪 Tester départ", style=discord.ButtonStyle.danger, row=2)
    async def test_departure(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._send_test_message(interaction, arrival=False)

    async def _send_test_message(self, interaction: discord.Interaction, arrival: bool) -> None:
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("Ce panneau doit être utilisé sur un serveur.", ephemeral=True)
            return

        gcfg = ensure_guild_entry(self.ctrl.config, self.gid, guild)
        if arrival:
            channel_id = gcfg.get("arrival_channel_id")
            if not channel_id:
                await interaction.response.send_message("Aucun salon d'arrivée configuré.", ephemeral=True)
                return
            ch = guild.get_channel(int(channel_id))
            if not isinstance(ch, discord.TextChannel):
                await interaction.response.send_message("Le salon d'arrivée configuré est invalide.", ephemeral=True)
                return
            emb = self._build_arrival_embed(interaction.user, guild, gcfg)
        else:
            channel_id = gcfg.get("departure_channel_id")
            if not channel_id:
                await interaction.response.send_message("Aucun salon de départ configuré.", ephemeral=True)
                return
            ch = guild.get_channel(int(channel_id))
            if not isinstance(ch, discord.TextChannel):
                await interaction.response.send_message("Le salon de départ configuré est invalide.", ephemeral=True)
                return
            emb = self._build_departure_embed(interaction.user, guild, gcfg)

        try:
            await ch.send(embed=emb)
            await interaction.response.send_message(
                f"Message de test {'arrivée' if arrival else 'départ'} envoyé dans {ch.mention}.",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "Le bot n'a pas la permission d'envoyer des messages dans le salon configuré.", ephemeral=True
            )
        except Exception as exc:
            await interaction.response.send_message(
                f"Impossible d'envoyer le message de test : {exc}", ephemeral=True
            )

    def _build_arrival_embed(self, user, guild, gcfg):
        color = self._parse_color(gcfg.get("arrival_embed_color", "#2ecc71"))
        embed = discord.Embed(
            title=f"🎉 Bienvenue {user.name} !",
            description=(
                f"👋 Salut {user.mention} !\n\n"
                f"Bienvenue sur **{guild.name}** 🎉\n\n"
                "✨ Nous sommes heureux de t'accueillir.\n"
                "📜 Pense à lire le règlement et découvrir les salons.\n\n"
                f"👥 Nous sommes maintenant **{guild.member_count} membres**."
            ),
            color=color,
            timestamp=discord.utils.utcnow(),
        )
        if user.display_avatar:
            embed.set_thumbnail(url=user.display_avatar.url)
        image = gcfg.get("arrival_image")
        if image:
            embed.set_image(url=image)
        if guild.icon:
            embed.set_author(name=guild.name, icon_url=guild.icon.url)
        embed.add_field(
            name="👤 Nouveau membre",
            value=f"{user.mention}\n`{user.id}`",
            inline=True,
        )
        embed.add_field(
            name="🌐 Serveur",
            value=f"{guild.name}\n{guild.member_count} membres",
            inline=True,
        )
        embed.set_footer(text=f"Bienvenue sur {guild.name}")
        return embed

    def _build_departure_embed(self, user, guild, gcfg):
        color = self._parse_color(gcfg.get("departure_embed_color", "#f04747"))
        embed = discord.Embed(
            title="👋 Membre parti",
            description=(
                f"😢 {user.name} a quitté **{guild.name}**.\n\n"
                f"👤 ID : `{user.id}`\n\n"
                f"👥 Membres restants : **{guild.member_count}**"
            ),
            color=color,
            timestamp=discord.utils.utcnow(),
        )
        if user.display_avatar:
            embed.set_thumbnail(url=user.display_avatar.url)
        if guild.icon:
            embed.set_author(name=guild.name, icon_url=guild.icon.url)
        embed.set_footer(text=f"Au revoir de {guild.name}")
        return embed

    # ── Color presets (arrival row 0, departure row 1) ────────

    @discord.ui.button(label="🟢 Vert", style=discord.ButtonStyle.secondary, custom_id="arrival_color_preset:#2ecc71", row=0)
    async def arrival_color_green(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.gcfg["arrival_embed_color"] = "#2ecc71"
        self._save()
        await interaction.response.edit_message(embeds=self.build_preview_embeds(), view=self)

    @discord.ui.button(label="🔵 Bleu", style=discord.ButtonStyle.secondary, custom_id="arrival_color_preset:#7289da", row=0)
    async def arrival_color_blue(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.gcfg["arrival_embed_color"] = "#7289da"
        self._save()
        await interaction.response.edit_message(embeds=self.build_preview_embeds(), view=self)

    @discord.ui.button(label="🔴 Rouge", style=discord.ButtonStyle.secondary, custom_id="arrival_color_preset:#f04747", row=0)
    async def arrival_color_red(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.gcfg["arrival_embed_color"] = "#f04747"
        self._save()
        await interaction.response.edit_message(embeds=self.build_preview_embeds(), view=self)

    @discord.ui.button(label="🟣 Violet", style=discord.ButtonStyle.secondary, custom_id="arrival_color_preset:#9146ff", row=0)
    async def arrival_color_purple(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.gcfg["arrival_embed_color"] = "#9146ff"
        self._save()
        await interaction.response.edit_message(embeds=self.build_preview_embeds(), view=self)

    @discord.ui.button(label="🟢 Vert", style=discord.ButtonStyle.secondary, custom_id="departure_color_preset:#2ecc71", row=1)
    async def departure_color_green(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.gcfg["departure_embed_color"] = "#2ecc71"
        self._save()
        await interaction.response.edit_message(embeds=self.build_preview_embeds(), view=self)

    @discord.ui.button(label="🔵 Bleu", style=discord.ButtonStyle.secondary, custom_id="departure_color_preset:#7289da", row=1)
    async def departure_color_blue(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.gcfg["departure_embed_color"] = "#7289da"
        self._save()
        await interaction.response.edit_message(embeds=self.build_preview_embeds(), view=self)

    @discord.ui.button(label="🔴 Rouge", style=discord.ButtonStyle.secondary, custom_id="departure_color_preset:#f04747", row=1)
    async def departure_color_red(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.gcfg["departure_embed_color"] = "#f04747"
        self._save()
        await interaction.response.edit_message(embeds=self.build_preview_embeds(), view=self)

    @discord.ui.button(label="🟣 Violet", style=discord.ButtonStyle.secondary, custom_id="departure_color_preset:#9146ff", row=1)
    async def departure_color_purple(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.gcfg["departure_embed_color"] = "#9146ff"
        self._save()
        await interaction.response.edit_message(embeds=self.build_preview_embeds(), view=self)

    # ── Custom hex buttons ────────────────────────────────────

    @discord.ui.button(label="🖌️ Hex Arrivée", style=discord.ButtonStyle.secondary, custom_id="arrival_hex", row=2)
    async def arrival_hex(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ColorHexModal(self, "arrival"))

    @discord.ui.button(label="🖌️ Hex Départ", style=discord.ButtonStyle.secondary, custom_id="departure_hex", row=2)
    async def departure_hex(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ColorHexModal(self, "departure"))

    # ── Arrival toggles (row 3) ───────────────────────────────

    @discord.ui.button(label="👤 Avatar", style=discord.ButtonStyle.success, custom_id="arrival_toggle:show_avatar", row=3)
    async def toggle_arrival_avatar(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._toggle("show_avatar", "arrival")
        await interaction.response.edit_message(embeds=self.build_preview_embeds(), view=self)

    @discord.ui.button(label="👥 Membres", style=discord.ButtonStyle.success, custom_id="arrival_toggle:show_member_count", row=3)
    async def toggle_arrival_members(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._toggle("show_member_count", "arrival")
        await interaction.response.edit_message(embeds=self.build_preview_embeds(), view=self)

    @discord.ui.button(label="💬 Mention", style=discord.ButtonStyle.success, custom_id="arrival_toggle:mention_user", row=3)
    async def toggle_arrival_mention(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._toggle("mention_user", "arrival")
        await interaction.response.edit_message(embeds=self.build_preview_embeds(), view=self)

    # ── Departure toggles (row 4) ─────────────────────────────

    @discord.ui.button(label="👤 Avatar", style=discord.ButtonStyle.success, custom_id="departure_toggle:show_avatar", row=4)
    async def toggle_departure_avatar(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._toggle("show_avatar", "departure")
        await interaction.response.edit_message(embeds=self.build_preview_embeds(), view=self)

    @discord.ui.button(label="📝 Ancien nom", style=discord.ButtonStyle.success, custom_id="departure_toggle:show_old_name", row=4)
    async def toggle_departure_old_name(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._toggle("show_old_name", "departure")
        await interaction.response.edit_message(embeds=self.build_preview_embeds(), view=self)

    @discord.ui.button(label="📄 Détail", style=discord.ButtonStyle.secondary, custom_id="departure_toggle:detailed_mode", row=4)
    async def toggle_departure_detail(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._toggle("detailed_mode", "departure")
        await interaction.response.edit_message(embeds=self.build_preview_embeds(), view=self)

    # ── Back ──────────────────────────────────────────────────

@discord.ui.button(label="⬅️ Retour", style=discord.ButtonStyle.secondary, row=4)
async def back(self, interaction: discord.Interaction, button: discord.ui.Button):

    view = ConfigDashboardView(
        self.ctrl,
        self.gid,
        self._theme
    )

    emb = view.build_embed()

    await interaction.response.edit_message(
        embed=emb,
        view=view
    )

class ColorHexModal(discord.ui.Modal, title="Couleur HEX personnalisée"):
    color_field = discord.ui.TextInput(
        label="Code couleur HEX",
        placeholder="#ff00ff",
        required=True,
        max_length=7,
    )

    def __init__(self, view: ArrivalDepartureView, target: str):
        super().__init__()
        self.view = view
        self.target = target

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.color_field.value.strip().lstrip("#")
        if not re.fullmatch(r"[0-9a-fA-F]{6}", raw):
            await interaction.response.send_message(
                "Couleur invalide. Utilise un code hex à 6 caractères.", ephemeral=True
            )
            return
        value = f"#{raw.lower()}"
        if self.target == "arrival":
            self.view.gcfg["arrival_embed_color"] = value
        else:
            self.view.gcfg["departure_embed_color"] = value
        self.view._save()
        await interaction.response.edit_message(embeds=self.view.build_preview_embeds(), view=self.view)


class ChannelSelectView(discord.ui.View):
    def __init__(self, ctrl: ConfigController, gid: str, theme: discord.Color, key: str):
        super().__init__(timeout=None)
        self.ctrl = ctrl
        self.gid = gid
        self._theme = theme
        self.key = key

        sel = discord.ui.ChannelSelect(
            placeholder="Choisis un salon texte",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
            custom_id=f"config_channel_select:{key}",
        )

        async def _cb(interaction: discord.Interaction):
            # sel.values[0] is a Channel
            chan = sel.values[0]
            try:
                cid = int(chan.id)
            except Exception:
                await interaction.response.send_message("Salon invalide.", ephemeral=True)
                return

            ensure_guild_entry(self.ctrl.config, str(self.gid), interaction.guild)
            gcfg = self.ctrl.config.setdefault("guilds", {}).setdefault(str(self.gid), {})
            gcfg[self.key] = str(cid)
            self.ctrl.save()

            pretty = f"<#{cid}>"
            if self.key == "arrival_channel_id":
                title = "🟢 Salon d'arrivée défini"
                desc = f"Les messages d'arrivée seront envoyés dans {pretty}."
            else:
                title = "🔴 Salon de départ défini"
                desc = f"Les messages de départ seront envoyés dans {pretty}."

            emb = discord.Embed(title=title, description=desc, color=self._theme)
            # Return to the arrival/departure panel
            await interaction.response.edit_message(embed=emb, view=ArrivalDepartureView(self.ctrl, self.gid, self._theme))

        sel.callback = _cb
        self.add_item(sel)


class CategoryChooseView(discord.ui.View):
    def __init__(self, ctrl: ConfigController, gid: str, theme: discord.Color, key: str):
        super().__init__(timeout=None)
        self.ctrl = ctrl
        self.gid = gid
        self._theme = theme
        self.key = key

        sel = discord.ui.ChannelSelect(
            placeholder="Choisis une catégorie Discord",
            channel_types=[discord.ChannelType.category],
            min_values=1,
            max_values=1,
            custom_id=f"config_category_select:{key}",
        )

        async def _cb(interaction: discord.Interaction):
            chan = sel.values[0]
            try:
                cid = int(chan.id)
            except Exception:
                await interaction.response.send_message("Catégorie invalide.", ephemeral=True)
                return

            ensure_guild_entry(self.ctrl.config, str(self.gid), interaction.guild)
            gcfg = self.ctrl.config.setdefault("guilds", {}).setdefault(str(self.gid), {})
            gcfg[self.key] = str(cid)
            self.ctrl.save()

            pretty = f"<#{cid}>"
            emb = discord.Embed(title="⚙️ Configuration enregistrée", description=f"Paramètre sauvegardé : {pretty}", color=self._theme)
            await interaction.response.edit_message(
    embed=emb,
    view=VoiceTempConfigView(
        self.ctrl,
        self.gid,
        self._theme
    )
)

        sel.callback = _cb
        self.add_item(sel)


class VoiceTempConfigView(discord.ui.View):
    """View used in /config to pick the trigger voice channel and optional parent category."""
    def __init__(self, ctrl: ConfigController, gid: str, theme: discord.Color):
        super().__init__(timeout=None)
        self.ctrl = ctrl
        self.gid = gid
        self._theme = theme

        # Voice channel selector (trigger)
        self.voice_sel = discord.ui.ChannelSelect(
            placeholder="Choisis le salon vocal de trigger (ex: 🎤 ➕ Créer votre salon)",
            channel_types=[discord.ChannelType.voice],
            min_values=1,
            max_values=1,
            custom_id=f"config_voice_trigger_select:{gid}",
        )

        async def voice_cb(interaction: discord.Interaction):
            chan = self.voice_sel.values[0]
            try:
                cid = int(chan.id)
            except Exception:
                await interaction.response.send_message("Salon invalide.", ephemeral=True)
                return

            ensure_guild_entry(self.ctrl.config, str(self.gid), interaction.guild)
            gcfg = self.ctrl.config.setdefault("guilds", {}).setdefault(str(self.gid), {})
            gcfg["join_to_create_channel_id"] = str(cid)
            self.ctrl.save()
            await interaction.response.send_message(f"✅ Salon de trigger configuré : <#{cid}>", ephemeral=True)

        self.voice_sel.callback = voice_cb
        self.add_item(self.voice_sel)

        # Category selector (optional parent for created channels)
        self.cat_sel = discord.ui.ChannelSelect(
            placeholder="Choisis la catégorie parent (optionnel)",
            channel_types=[discord.ChannelType.category],
            min_values=1,
            max_values=1,
            custom_id=f"config_voice_parent_select:{gid}",
        )

        async def cat_cb(interaction: discord.Interaction):
            chan = self.cat_sel.values[0]
            try:
                cid = int(chan.id)
            except Exception:
                await interaction.response.send_message("Catégorie invalide.", ephemeral=True)
                return

            ensure_guild_entry(self.ctrl.config, str(self.gid), interaction.guild)
            gcfg = self.ctrl.config.setdefault("guilds", {}).setdefault(str(self.gid), {})
            gcfg["temp_voice_category_id"] = str(cid)
            self.ctrl.save()
            await interaction.response.send_message(f"✅ Catégorie parent définie : <#{cid}>", ephemeral=True)

        self.cat_sel.callback = cat_cb
        self.add_item(self.cat_sel)


class VoiceTempView(discord.ui.View):
    def __init__(self, ctrl: ConfigController, gid: str, theme: discord.Color):
        super().__init__(timeout=None)
        self.ctrl = ctrl
        self.gid = gid
        self._theme = theme
    # No interactive buttons here by design — Join-To-Create is triggered via voice state updates.


class ConfigRootView(discord.ui.View):
    def __init__(self, ctrl: ConfigController, gid: str, theme: discord.Color):
        super().__init__(timeout=None)
        self.ctrl = ctrl
        self.gid = gid
        self._theme = theme

    def build_embed(self) -> discord.Embed:
        cfg = self.ctrl.config
        gcfg = cfg.get("guilds", {}).get(self.gid, {})
        admin_roles = gcfg.get("admin_roles") or []
        mod_roles = gcfg.get("mod_roles") or []
        cats = gcfg.get("categories") or {}
        ticket_config = gcfg.get("ticket", {})
        current_cat_id = ticket_config.get("category_id")
        current_cat_name = ticket_config.get("category_name")
        
        emb = discord.Embed(
            title="👑 Panel Propriétaire",
            description="Gestion des rôles et des catégories de tickets.",
            color=self._theme,
        )
        emb.add_field(name="Serveur", value=f"`{self.gid}` · {gcfg.get('name', '—')}", inline=False)
        
        # Afficher la catégorie des tickets actuelle
        if current_cat_id:
            main_channel_id = ticket_config.get("main_channel_id")
            main_channel_name = ticket_config.get("main_channel_name")
            cat_display = f"**{current_cat_name or 'Nom inconnu'}**\n(ID : `{current_cat_id}`)"
            if main_channel_id:
                cat_display += f"\n📌 Salon : **{main_channel_name or 'Nom inconnu'}** (ID : `{main_channel_id}`)"
        else:
            cat_display = "*Non configurée*"
        emb.add_field(name="📂 Catégorie des tickets actuelle", value=cat_display, inline=False)
        
        emb.add_field(
            name="Rôles Admin",
            value=", ".join(f"<@&{r}>" for r in admin_roles) if admin_roles else "*aucun*",
            inline=False,
        )
        emb.add_field(
            name="Rôles Modérateur",
            value=", ".join(f"<@&{r}>" for r in mod_roles) if mod_roles else "*aucun*",
            inline=False,
        )
        emb.add_field(
            name="Catégories de tickets",
            value="\n".join([f"• `{k}`: {v.get('label', '')}" for k, v in cats.items()]) if cats else "*aucune*",
            inline=False,
        )
        return emb

    @discord.ui.button(label="📁 Catégorie tickets", style=discord.ButtonStyle.primary, row=0)
    async def b_cat(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Choisis la catégorie Discord :",
            view=PickCategoryView(self.ctrl, self.gid),
            ephemeral=True,
        )

    @discord.ui.button(label="📋 Salon logs", style=discord.ButtonStyle.primary, row=0)
    async def b_log(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Choisis le salon texte des logs :",
            view=PickLogChannelView(self.ctrl, self.gid),
            ephemeral=True,
        )

    @discord.ui.button(label="👮 Rôles staff", style=discord.ButtonStyle.secondary, row=0)
    async def b_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Sélectionne les rôles :",
            view=PickStaffRolesView(self.ctrl, self.gid),
            ephemeral=True,
        )

    @discord.ui.button(label="➕ Entrée menu", style=discord.ButtonStyle.success, row=1)
    async def b_add_menu(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddCategoryModal(self.ctrl, self.gid))

    @discord.ui.button(label="🗑 Retirer entrée", style=discord.ButtonStyle.danger, row=1)
    async def b_rm_menu(self, interaction: discord.Interaction, button: discord.ui.Button):
        cats = (
            self.ctrl.config.get("guilds", {}).get(self.gid, {}).get("categories") or {}
        )
        if not cats:
            return await interaction.response.send_message(
                "Aucune entrée menu à supprimer.", ephemeral=True
            )
        await interaction.response.send_message(
            "Clé à supprimer :",
            view=RemoveCategoryView(self.ctrl, self.gid),
            ephemeral=True,
        )

    @discord.ui.button(label="🌐 Nouveau serveur (ID)", style=discord.ButtonStyle.secondary, row=2)
    async def b_guild(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddGuildByIdModal(self.ctrl))

    @discord.ui.button(label="🔑 Gérer whitelist admins", style=discord.ButtonStyle.secondary, row=2)
    async def b_admins(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(WhitelistAdminsModal(self.ctrl))

    @discord.ui.button(label="🚫 Gérer commandes désactivées", style=discord.ButtonStyle.secondary, row=2)
    async def b_disabled_commands(self, interaction: discord.Interaction, button: discord.ui.Button):
        current_disabled = self.ctrl.config.get("disabled_commands", [])
        modal = DisabledCommandsModal(self.ctrl)
        modal.commands_field.default = "\n".join(current_disabled) if current_disabled else ""
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="🔄 Rafraîchir le panneau", style=discord.ButtonStyle.secondary, row=3)
    async def b_refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        emb = self.build_embed()
        await interaction.response.edit_message(embed=emb, view=self)
