import json
import os
from pathlib import Path
from functools import wraps

import requests

from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    session,
    redirect
)

from config import get_user_role


# =========================
# APP
# =========================

app = Flask(__name__)

app.secret_key = os.getenv(
    "SECRET_KEY",
    "change-this-secret"
)

app.config["JSON_SORT_KEYS"] = False
app.config["TEMPLATES_AUTO_RELOAD"] = True


# =========================
# CONFIG
# =========================

DISCORD_CLIENT_ID = os.getenv(
    "DISCORD_CLIENT_ID"
)

DISCORD_CLIENT_SECRET = os.getenv(
    "DISCORD_CLIENT_SECRET"
)

DISCORD_REDIRECT = os.getenv(
    "DISCORD_REDIRECT",
    "https://cecabot-web.onrender.com/callback"
)


API_TOKEN = os.getenv(
    "API_TOKEN",
    "change-api-token"
)


# =========================
# FILES
# =========================

BASE_DIR = Path(__file__).resolve().parent


TICKETS_JSON_FILE = BASE_DIR.parent / "tickets.json"

PANELS_JSON_FILE = BASE_DIR.parent / "panels.json"

CONFIG_JSON_FILE = BASE_DIR.parent / "config.json"


# =========================
# HELPERS
# =========================

def load_json(path):

    try:

        if not path.exists():
            return {}

        data = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

        return data if isinstance(data, dict) else {}

    except Exception as e:

        print(
            "Erreur JSON:",
            e
        )

        return {}



def save_json(path, data):

    try:

        path.write_text(
            json.dumps(
                data,
                indent=4,
                ensure_ascii=False
            ),
            encoding="utf-8"
        )

        return True

    except Exception as e:

        print(
            "Erreur sauvegarde JSON:",
            e
        )

        return False



def api_required(func):

    @wraps(func)
    def wrapper(*args, **kwargs):

        auth = request.headers.get(
            "Authorization"
        )

        if not auth:
            return jsonify({
                "error": "Missing token"
            }),401


        if auth != f"Bearer {API_TOKEN}":

            return jsonify({
                "error": "Invalid token"
            }),403


        return func(*args, **kwargs)


    return wrapper



def ticket_counts(tickets):

    opened = 0
    closed = 0


    for ticket in tickets.values():

        if isinstance(ticket, dict):

            if ticket.get("closed"):

                closed += 1

            else:

                opened += 1


    return opened, closed



def count_panels(panels):

    total = 0


    for value in panels.values():

        if isinstance(value, list):

            total += len(value)


        elif isinstance(value, dict):

            total += 1


    return total



# =========================
# DISCORD LOGIN
# =========================

@app.route("/login")
def login():

    params = {

        "client_id": DISCORD_CLIENT_ID,

        "response_type": "code",

        "redirect_uri": DISCORD_REDIRECT,

        "scope": "identify guilds"

    }


    url = (
        "https://discord.com/oauth2/authorize?"
        +
        "&".join(
            f"{k}={requests.utils.quote(str(v))}"
            for k,v in params.items()
        )
    )


    return redirect(url)



# =========================
# DISCORD CALLBACK
# =========================

@app.route("/callback")
def callback():

    code = request.args.get(
        "code"
    )


    if not code:

        return "Code Discord absent",400



    try:

        token_response = requests.post(

            "https://discord.com/api/oauth2/token",

            data={

                "client_id": DISCORD_CLIENT_ID,

                "client_secret": DISCORD_CLIENT_SECRET,

                "grant_type":
                "authorization_code",

                "code": code,

                "redirect_uri":
                DISCORD_REDIRECT

            },

            timeout=10

        )


        token = token_response.json()


    except Exception as e:

        print(e)

        return "Erreur Discord",500



    if "access_token" not in token:

        print(token)

        return "Erreur OAuth Discord",400



    headers = {

        "Authorization":
        f"Bearer {token['access_token']}"

    }



    try:

        user = requests.get(

            "https://discord.com/api/users/@me",

            headers=headers,

            timeout=10

        ).json()



        guilds = requests.get(

            "https://discord.com/api/users/@me/guilds",

            headers=headers,

            timeout=10

        ).json()


    except Exception as e:

        print(e)

        return "Erreur récupération Discord",500



    discord_id = int(
        user["id"]
    )


    session.permanent = True


    session["user"] = {

        "id": discord_id,

        "username":
        user.get(
            "global_name",
            user["username"]
        ),

        "avatar":
        user.get("avatar"),

        "role":
        get_user_role(
            discord_id
        )

    }


    # uniquement les serveurs gérables

    session["guilds"] = [

        g for g in guilds

        if int(
            g.get(
                "permissions",
                0
            )
        ) & 0x20

    ]



    print(
        "Connexion:",
        user["username"]
    )


    print(
        "Serveurs:",
        len(session["guilds"])
    )


    return redirect("/servers")



# =========================
# SERVERS
# =========================

@app.route("/servers")
def servers():

    if not session.get("user"):

        return redirect("/login")


    return render_template(

        "servers.html",

        user=session["user"],

        guilds=session.get(
            "guilds",
            []
        )

    )



# =========================
# DASHBOARD
# =========================

@app.route("/")
def dashboard():

    if not session.get("user"):

        return redirect("/login")


    tickets = load_json(
        TICKETS_JSON_FILE
    )

    panels = load_json(
        PANELS_JSON_FILE
    )


    stats = {}

    stats["open"], stats["closed"] = ticket_counts(
        tickets
    )

    stats["panels"] = count_panels(
        panels
    )


    return render_template(

        "dashboard.html",

        user=session["user"],

        stats=stats,

        tickets=tickets,

        panels=panels,

        config=load_json(
            CONFIG_JSON_FILE
        )

    )



# =========================
# API
# =========================

@app.route("/api/health")
def health():

    return jsonify({

        "status":"online",

        "service":"TicketMP"

    })



@app.route("/api/guilds")
@api_required
def api_guilds():

    return jsonify(
        session.get(
            "guilds",
            []
        )
    )



@app.route("/api/guilds/<guild_id>")
@api_required
def api_guild(guild_id):

    config = load_json(
        CONFIG_JSON_FILE
    )


    return jsonify({

        "guild_id": guild_id,

        "settings":
        config.get(
            guild_id,
            {}

        )

    })



@app.route(
    "/api/guilds/<guild_id>/settings",
    methods=["POST"]
)
@api_required
def api_settings(guild_id):

    data = request.json


    if not isinstance(data,dict):

        return jsonify({
            "error":"Invalid data"
        }),400



    config = load_json(
        CONFIG_JSON_FILE
    )


    config[guild_id] = data


    save_json(
        CONFIG_JSON_FILE,
        config
    )


    return jsonify({

        "success":True,

        "guild_id":guild_id

    })



# =========================
# PAGES
# =========================

@app.route("/tickets")
def tickets_page():

    return render_template(

        "tickets.html",

        tickets=load_json(
            TICKETS_JSON_FILE
        )

    )



@app.route("/panels")
def panels_page():

    return render_template(

        "panels.html",

        panels=load_json(
            PANELS_JSON_FILE
        )

    )



@app.route("/logout")
def logout():

    session.clear()

    return redirect("/login")



# =========================
# ERRORS
# =========================

@app.errorhandler(404)
def not_found(e):

    return jsonify({

        "error":
        "Page introuvable"

    }),404



# =========================
# START
# =========================

if __name__ == "__main__":

    app.run(

        host="0.0.0.0",

        port=int(
            os.getenv(
                "PORT",
                3000
            )
        )

    )