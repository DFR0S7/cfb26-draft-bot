import os
import json
import sqlite3
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
from http.server import BaseHTTPRequestHandler, HTTPServer
import socketserver

# -----------------------------
# Discord Intents
# -----------------------------
intents = discord.Intents.default()
intents.guilds = True
intents.members = True  # REQUIRED for get_member(), fetch_member(), etc.

bot = commands.Bot(command_prefix="!", intents=intents)

# -----------------------------
# Environment Variables
# -----------------------------
TOKEN = os.getenv("DISCORD_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))

if not TOKEN:
    print("DISCORD_TOKEN not set")
    raise SystemExit(1)

# -----------------------------
# Database Setup
# -----------------------------
DB_FILE = "draft.db"

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            status TEXT NOT NULL,
            current_pick_index INTEGER DEFAULT 0
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            draft_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            pick_order INTEGER NOT NULL,
            UNIQUE(draft_id, user_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS assigned_teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            draft_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            team_name TEXT NOT NULL,
            conference TEXT NOT NULL,
            UNIQUE(draft_id, team_name)
        )
    """)

    conn.commit()
    conn.close()

init_db()

# -----------------------------
# Load Teams JSON
# -----------------------------
TEAMS_JSON = "teams.json"

try:
    with open(TEAMS_JSON, "r", encoding="utf-8") as f:
        TEAMS = json.load(f)
except Exception as e:
    print(f"Error loading teams.json: {e}")
    TEAMS = []
# ============================================================
# SECTION 2 — Helper Functions
# ============================================================

# -----------------------------
# Utility: Normalize team names
# -----------------------------
def normalize_name(name: str) -> str:
    if not name:
        return ""
    return name.strip().lower().replace("&", "and").replace("  ", " ")

# -----------------------------
# Utility: Find team by name
# -----------------------------
def find_team(team_name: str):
    norm = normalize_name(team_name)
    for team in TEAMS:
        if normalize_name(team["school"]) == norm:
            return team
    return None

# -----------------------------
# Utility: Get all conferences
# -----------------------------
def get_all_conferences():
    return sorted({team["conference"] for team in TEAMS})

# -----------------------------
# DB: Create a new draft
# -----------------------------
async def create_draft():
    conn = get
# ============================================================
# SECTION 3 — Slash Commands
# ============================================================

@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands.")
    except Exception as e:
        print(f"Error syncing commands: {e}")
    print(f"Bot is ready. Logged in as {bot.user}.")


# ------------------------------------------------------------
# /start_draft — Admin only
# ------------------------------------------------------------
@bot.tree.command(name="start_draft", description="Start a new draft (Admin only).")
async def start_draft(interaction: discord.Interaction):

    # Safety checks
    if not await ensure_guild(interaction):
        return
    if not await ensure_member_intent(interaction):
        return

    # Admin check
    if interaction.user.id != ADMIN_USER_ID:
        await interaction.response.send_message(
            "You are not authorized to start a draft.",
            ephemeral=True
        )
        return

    # Create draft
    draft_id = await create_draft()

    await interaction.response.send_message(
        f"Draft **#{draft_id}** created and is now open!\n"
        "Participants may now join using `/join_draft`.",
        ephemeral=False
    )


# ------------------------------------------------------------
# /join_draft — Users
# ============================================================
# SECTION 4 — Health Server + Bot Runner
# ============================================================

# ------------------------------------------------------------
# Simple health check server for Render
# ------------------------------------------------------------
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")

def start_health_server():
    port = int(os.getenv("PORT", "10000"))
    server_address = ("0.0.0.0", port)

    try:
        httpd = HTTPServer(server_address, HealthHandler)
        print(f"Health server running on port {port}")
        httpd.serve_forever()
    except Exception as e:
        print(f"