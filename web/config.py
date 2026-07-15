# ===============================
# TicketMP Dashboard Permissions
# ===============================


# 👑 Compte fondateur du site
# Ce compte aura tous les droits
FOUNDER_DISCORD_ID = 1112038418629808148



# 🛡️ Liste des administrateurs
# Ajoute ici les IDs Discord autorisés
ADMIN_DISCORD_IDS = [

    # Exemple :
    # 123456789012345678,
    # 987654321098765432

]



# ===============================
# Gestion des rôles
# ===============================

def get_user_role(discord_id: int):

    """
    Retourne le rôle d'un utilisateur connecté
    """

    # Protection fondateur
    if discord_id == FOUNDER_DISCORD_ID:
        return "FOUNDER"


    # Vérification admins
    if discord_id in ADMIN_DISCORD_IDS:
        return "ADMIN"


    # Utilisateur normal
    return "USER"