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
    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT INTO drafts (status, current_pick_index) VALUES (?, ?)", ("open", 0))
    conn.commit()
    draft_id = cur.lastrowid
    conn.close()
    return draft_id

# -----------------------------
# DB: Get active draft
# -----------------------------
async def get_active_draft():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM drafts WHERE status = 'open' ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    conn.close()
    return row

# -----------------------------
# DB: Add participant
# -----------------------------
async def add_participant(draft_id, user_id, pick_order):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT OR IGNORE INTO participants (draft_id, user_id, pick_order)
        VALUES (?, ?, ?)
    """, (draft_id, user_id, pick_order))
    conn.commit()
    conn.close()

# -----------------------------
# DB: Get participants ordered
# -----------------------------
async def get_participants(draft_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT user_id, pick_order
        FROM participants
        WHERE draft_id = ?
        ORDER BY pick_order ASC
    """, (draft_id,))
    rows = cur.fetchall()
    conn.close()
    return rows

# -----------------------------
# DB: Get participant by pick #
# -----------------------------
async def get_participant_by_pick(draft_id, pick_index):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT user_id
        FROM participants
        WHERE draft_id = ? AND pick_order = ?
    """, (draft_id, pick_index))
    row = cur.fetchone()
    conn.close()
    return row["user_id"] if row else None

# -----------------------------
# DB: Assign team to user
# -----------------------------
async def assign_team(draft_id, user_id, team_name, conference):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO assigned_teams (draft_id, user_id, team_name, conference)
        VALUES (?, ?, ?, ?)
    """, (draft_id, user_id, team_name, conference))
    conn.commit()
    conn.close()

# -----------------------------
# DB: Check if team already taken
# -----------------------------
async def is_team_taken(draft_id, team_name):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT 1 FROM assigned_teams
        WHERE draft_id = ? AND team_name = ?
    """, (draft_id, team_name))
    row = cur.fetchone()
    conn.close()
    return row is not None

# -----------------------------
# DB: Advance pick index
# -----------------------------
async def advance_pick(draft_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE drafts
        SET current_pick_index = current_pick_index + 1
        WHERE id = ?
    """, (draft_id,))
    conn.commit()
    conn.close()

# -----------------------------
# Safety: Ensure command is in a guild
# -----------------------------
async def ensure_guild(interaction):
    if interaction.guild is None:
        await interaction.response.send_message(
            "This command must be used inside a server channel.",
            ephemeral=True
        )
        return False
    return True

# -----------------------------
# Safety: Ensure bot has member intent
# -----------------------------
async def ensure_member_intent(interaction):
    if not interaction.client.intents.members:
        await interaction.response.send_message(
            "Bot is missing the **Server Members Intent**. "
            "Enable it in the Discord Developer Portal.",
            ephemeral=True
        )
        return False
    return True

# -----------------------------
# Safety: Fetch member safely
# -----------------------------
async def safe_fetch_member(guild, user_id):
    member = guild.get_member(user_id)
    if member:
        return member
    try:
        return await guild.fetch_member(user_id)
    except Exception:
        return None
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
#------------------------------------------------------------
@bot.tree.command(name="join_draft", description="Join the currently open draft.")
async def join_draft(interaction: discord.Interaction):

    if not await ensure_guild(interaction):
        return

    draft = await get_active_draft()
    if not draft:
        await interaction.response.send_message(
            "There is no active draft to join.",
            ephemeral=True
        )
        return

    draft_id = draft["id"]

    # Determine next pick order
    participants = await get_participants(draft_id)
    next_pick = len(participants)

    await add_participant(draft_id, interaction.user.id, next_pick)

    await interaction.response.send_message(
        f"You have joined Draft #{draft_id} with pick order **{next_pick + 1}**.",
        ephemeral=True
    ) 
#============================================================
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
        print(f"Health server failed to start: {e}")
# ------------------------------------------------------------
# Background keep-alive thread
# ------------------------------------------------------------
import threading

def start_background_tasks():
    thread = threading.Thread(target=start_health_server, daemon=True)
    thread.start()


# ------------------------------------------------------------
# Start bot
# ------------------------------------------------------------
if __name__ == "__main__":
    start_background_tasks()
    bot.run(TOKEN)
