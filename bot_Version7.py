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
# Enforce pick limit per user
    user_pick_count = await count_user_conference_picks(draft_id, conference_name, interaction.user.id)

    if user_pick_count >= TEAMS_PER_USER:
        await interaction.response.send_message(
            f"You have already drafted your limit of **{TEAMS_PER_USER} teams** "
            f"into **{conference_name}**.",
            ephemeral=True
        )
        return
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

TEAMS_PER_USER = 7   # or whatever number U is
async def count_user_conference_picks(draft_id, conference_name, user_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) AS count
        FROM conference_picks
        WHERE draft_id = ?
          AND conference_name = ?
          AND picked_by_user_id = ?
    """, (draft_id, conference_name, user_id))
    count = cur.fetchone()["count"]
    conn.close()
    return count

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
#------------------------------------------------------------
# /choose_conference — User selects a conference
#------------------------------------------------------------
@bot.tree.command(name="choose_conference", description="Choose a conference to draft from.")
@app_commands.describe(conference="The conference you want to draft from.")
async def choose_conference(interaction: discord.Interaction, conference: str):

    if not await ensure_guild(interaction):
        return

    draft = await get_active_draft()
    if not draft:
        await interaction.response.send_message(
            "There is no active draft.",
            ephemeral=True
        )
        return

    conferences = get_all_conferences()
    if conference not in conferences:
        await interaction.response.send_message(
            "Invalid conference. Use `/conference_view` to see available options.",
            ephemeral=True
        )
        return

    await interaction.response.send_message(
        f"You selected **{conference}**.\n"
        "Now use `/claim_team` to pick a team from that conference.",
        ephemeral=True
    )
#------------------------------------------------------------
# /conference_view — Show all conferences
#------------------------------------------------------------
@bot.tree.command(name="conference_view", description="View all conferences.")
async def conference_view(interaction: discord.Interaction):

    if not await ensure_guild(interaction):
        return

    conferences = get_all_conferences()
    formatted = "\n".join(f"- {c}" for c in conferences)

    await interaction.response.send_message(
        f"**Available Conferences:**\n{formatted}",
        ephemeral=True
    )
# ------------------------------------------------------------
# /claim_team — User claims a team from a conference
#------------------------------------------------------------
@bot.tree.command(name="claim_team", description="Claim a team from a chosen conference.")
@app_commands.describe(team_name="The team you want to claim.")
async def claim_team(interaction: discord.Interaction, team_name: str):

    if not await ensure_guild(interaction):
        return

    draft = await get_active_draft()
    if not draft:
        await interaction.response.send_message(
            "There is no active draft.",
            ephemeral=True
        )
        return

    draft_id = draft["id"]

    # Validate team
    team = find_team(team_name)
    if not team:
        await interaction.response.send_message(
            "Team not found. Check your spelling or use `/conference_view`.",
            ephemeral=True
        )
        return

    # Check if taken
    if await is_team_taken(draft_id, team["school"]):
        await interaction.response.send_message(
            f"**{team['school']}** is already taken.",
            ephemeral=True
        )
        return

    # Assign team
    await assign_team(
        draft_id,
        interaction.user.id,
        team["school"],
        team["conference"]
    )

    await interaction.response.send_message(
        f"You have successfully claimed **{team['school']}** ({team['conference']}).",
        ephemeral=False
    )
#------------------------------------------------------------
# /pick — User makes their draft pick
#------------------------------------------------------------
@bot.tree.command(name="pick", description="Make your draft pick.")
@app_commands.describe(team_name="The team you want to pick.")
async def pick(interaction: discord.Interaction, team_name: str):

    if not await ensure_guild(interaction):
        return

    draft = await get_active_draft()
    if not draft:
        await interaction.response.send_message(
            "There is no active draft.",
            ephemeral=True
        )
        return

    draft_id = draft["id"]
    current_index = draft["current_pick_index"]

    # Check if it's the user's turn
    expected_user_id = await get_participant_by_pick(draft_id, current_index)
    if expected_user_id != interaction.user.id:
        await interaction.response.send_message(
            "It is **not your turn** to pick.",
            ephemeral=True
        )
        return

    # Validate team
    team = find_team(team_name)
    if not team:
        await interaction.response.send_message(
            "Team not found.",
            ephemeral=True
        )
        return

    # Check if taken
    if await is_team_taken(draft_id, team["school"]):
        await interaction.response.send_message(
            f"**{team['school']}** is already taken.",
            ephemeral=True
        )
        return

    # Assign team
    await assign_team(
        draft_id,
        interaction.user.id,
        team["school"],
        team["conference"]
    )

    # Advance pick
    await advance_pick(draft_id)

    await interaction.response.send_message(
        f"**{interaction.user.display_name}** has selected **{team['school']}**!",
        ephemeral=False
    )
# ------------------------------------------------------------
# /assign_conference — User claims ownership of a conference
# ------------------------------------------------------------
@bot.tree.command(name="assign_conference", description="Claim ownership of a conference (max 2 owners).")
@app_commands.describe(conference_name="The conference you want to own.")
async def assign_conference(interaction: discord.Interaction, conference_name: str):

    if not await ensure_guild(interaction):
        return

    draft = await get_active_draft()
    if not draft:
        await interaction.response.send_message("There is no active draft.", ephemeral=True)
        return

    draft_id = draft["id"]

    # Check if user already owns a conference
    owners = await get_conference_owners(draft_id, conference_name)
    for owner in owners:
        if owner["user_id"] == interaction.user.id:
            await interaction.response.send_message(
                f"You already own **{conference_name}**.",
                ephemeral=True
            )
            return

    # Attempt to assign
    success = await assign_conference_owner(draft_id, conference_name, interaction.user.id)
    if not success:
        await interaction.response.send_message(
            f"**{conference_name}** already has 2 owners.",
            ephemeral=True
        )
        return

    await interaction.response.send_message(
        f"You are now an owner of **{conference_name}**!",
        ephemeral=False
    )


# ------------------------------------------------------------
# /draft_team_to_conference — Draft a team into your conference
# ------------------------------------------------------------
@bot.tree.command(name="draft_team_to_conference", description="Draft a team into the conference you own.")
@app_commands.describe(conference_name="Your conference", team_name="Team you want to draft")
async def draft_team_to_conference(interaction: discord.Interaction, conference_name: str, team_name: str):

    if not await ensure_guild(interaction):
        return

    draft = await get_active_draft()
    if not draft:
        await interaction.response.send_message("There is no active draft.", ephemeral=True)
        return

    draft_id = draft["id"]

    # Validate ownership
    owners = await get_conference_owners(draft_id, conference_name)
    owner_ids = [o["user_id"] for o in owners]

    if interaction.user.id not in owner_ids:
        await interaction.response.send_message(
            f"You do not own **{conference_name}**.",
            ephemeral=True
        )
        return

    # Validate team exists
    team = find_team(team_name)
    if not team:
        await interaction.response.send_message("Team not found.", ephemeral=True)
        return

    # Check if team already drafted
    if await is_team_in_any_conference(draft_id, team["school"]):
        await interaction.response.send_message(
            f"**{team['school']}** has already been drafted into a conference.",
            ephemeral=True
        )
        return

    # Add team to conference
    await add_team_to_conference(draft_id, conference_name, team["school"], interaction.user.id)

    await interaction.response.send_message(
        f"**{team['school']}** has been drafted into **{conference_name}**!",
        ephemeral=False
    )


# ------------------------------------------------------------
# /view_conference — Show owners + drafted teams + available teams
# ------------------------------------------------------------
@bot.tree.command(name="view_conference", description="View the current state of a custom conference.")
@app_commands.describe(conference_name="The conference to view.")
async def view_conference(interaction: discord.Interaction, conference_name: str):

    if not await ensure_guild(interaction):
        return

    draft = await get_active_draft()
    if not draft:
        await interaction.response.send_message("There is no active draft.", ephemeral=True)
        return

    draft_id = draft["id"]

    owners = await get_conference_owners(draft_id, conference_name)
    teams = await get_conference_teams(draft_id, conference_name)

    owner_lines = []
    for o in owners:
        member = await safe_fetch_member(interaction.guild, o["user_id"])
        name = member.display_name if member else f"User {o['user_id']}"
        owner_lines.append(f"- {name}")

    team_lines = [f"{t['pick_number']}. {t['team_name']}" for t in teams]

    if not owner_lines:
        owner_text = "No owners yet."
    else:
        owner_text = "\n".join(owner_lines)

    if not team_lines:
        team_text = "No teams drafted yet."
    else:
        team_text = "\n".join(team_lines)

    await interaction.response.send_message(
        f"**Conference: {conference_name}**\n\n"
        f"**Owners:**\n{owner_text}\n\n"
        f"**Drafted Teams:**\n{team_text}",
        ephemeral=False
    )


# ------------------------------------------------------------
# /view_all_custom_conferences — Show everything
# ------------------------------------------------------------
@bot.tree.command(name="view_all_custom_conferences", description="View all custom conferences and their drafted teams.")
async def view_all_custom_conferences(interaction: discord.Interaction):

    if not await ensure_guild(interaction):
        return

    draft = await get_active_draft()
    if not draft:
        await interaction.response.send_message("There is no active draft.", ephemeral=True)
        return

    draft_id = draft["id"]

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT conference_name
        FROM conference_assignments
        WHERE draft_id = ?
    """, (draft_id,))
    conferences = [row["conference_name"] for row in cur.fetchall()]
    conn.close()

    if not conferences:
        await interaction.response.send_message("No conferences have been claimed yet.", ephemeral=True)
        return

    output = []

    for conf in conferences:
        owners = await get_conference_owners(draft_id, conf)
        teams = await get_conference_teams(draft_id, conf)

        owner_names = []
        for o in owners:
            member = await safe_fetch_member(interaction.guild, o["user_id"])
            owner_names.append(member.display_name if member else f"User {o['user_id']}")

        team_list = ", ".join([t["team_name"] for t in teams]) if teams else "None"

        output.append(
            f"**{conf}**\n"
            f"Owners: {', '.join(owner_names)}\n"
            f"Teams: {team_list}\n"
        )

    await interaction.response.send_message("\n".join(output), ephemeral=False)


# ------------------------------------------------------------
# /available_teams — Show all teams not yet drafted
# ------------------------------------------------------------
@bot.tree.command(name="available_teams", description="View all teams not yet drafted into any conference.")
async def available_teams(interaction: discord.Interaction):

    if not await ensure_guild(interaction):
        return

    draft = await get_active_draft()
    if not draft:
        await interaction.response.send_message("There is no active draft.", ephemeral=True)
        return

    draft_id = draft["id"]

    taken = set()

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT team_name
        FROM conference_picks
        WHERE draft_id = ?
    """, (draft_id,))
    for row in cur.fetchall():
        taken.add(row["team_name"])
    conn.close()

    available = [team["school"] for team in TEAMS if team["school"] not in taken]

    formatted = "\n".join(f"- {t}" for t in available)

    await interaction.response.send_message(
        f"**Available Teams ({len(available)}):**\n{formatted}",
        ephemeral=False
    )

# ------------------------------------------------------------
# /my_progress — Show user's assigned team, conference, and draft progress
# ------------------------------------------------------------
@bot.tree.command(
    name="my_progress",
    description="View your assigned team, your conference, and your draft progress."
)
async def my_progress(interaction: discord.Interaction):

    if not await ensure_guild(interaction):
        return

    draft = await get_active_draft()
    if not draft:
        await interaction.response.send_message(
            "There is no active draft.",
            ephemeral=True
        )
        return

    draft_id = draft["id"]
    user_id = interaction.user.id

    # ------------------------------------------------------------
    # 1. Assigned Team
    # ------------------------------------------------------------
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT team_name
        FROM assigned_teams
        WHERE draft_id = ? AND user_id = ?
    """, (draft_id, user_id))
    row = cur.fetchone()
    conn.close()

    assigned_team = row["team_name"] if row else "None"

    # ------------------------------------------------------------
    # 2. Conference Ownership
    # ------------------------------------------------------------
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT conference_name
        FROM conference_assignments
        WHERE draft_id = ? AND user_id = ?
    """, (draft_id, user_id))
    rows = cur.fetchall()
    conn.close()

    if not rows:
        conference_name = None
    else:
        conference_name = rows[0]["conference_name"]

    # ------------------------------------------------------------
    # 3. Draft Progress (teams drafted into user's conference)
    # ------------------------------------------------------------
    if conference_name:
        user_pick_count = await count_user_conference_picks(draft_id, conference_name, user_id)
        teams = await get_conference_teams(draft_id, conference_name)
        drafted_by_user = [t["team_name"] for t in teams if t["picked_by_user_id"] == user_id]
    else:
        user_pick_count = 0
        drafted_by_user = []

    picks_remaining = TEAMS_PER_USER - user_pick_count

    # ------------------------------------------------------------
    # Build Output
    # ------------------------------------------------------------
    assigned_text = f"**Assigned Team:** {assigned_team}"

    if conference_name:
        conference_text = f"**Your Conference:** {conference_name}"
    else:
        conference_text = "**Your Conference:** Not yet assigned"

    drafted_text = (
        "\n".join(f"- {team}" for team in drafted_by_user)
        if drafted_by_user else "None"
    )

    await interaction.response.send_message(
        f"**Your Draft Progress**\n\n"
        f"{assigned_text}\n"
        f"{conference_text}\n\n"
        f"**Teams Drafted ({user_pick_count}/{TEAMS_PER_USER}):**\n"
        f"{drafted_text}\n\n"
        f"**Picks Remaining:** {picks_remaining}",
ephemeral=False
    )
# ------------------------------------------------------------
# /export_conferences — Export full custom conference results
# ------------------------------------------------------------
@bot.tree.command(
    name="export_conferences",
    description="Export the full list of custom conferences, owners, and drafted teams."
)
async def export_conferences(interaction: discord.Interaction):

    if not await ensure_guild(interaction):
        return

    draft = await get_active_draft()
    if not draft:
        await interaction.response.send_message(
            "There is no active draft.",
            ephemeral=True
        )
        return

    draft_id = draft["id"]

    # Get all conferences that have owners
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT conference_name
        FROM conference_assignments
        WHERE draft_id = ?
        ORDER BY conference_name ASC
    """, (draft_id,))
    conferences = [row["conference_name"] for row in cur.fetchall()]
    conn.close()

    if not conferences:
        await interaction.response.send_message(
            "No conferences have been created yet.",
            ephemeral=True
        )
        return

    output_lines = []
    output_lines.append(f"**Custom Conference Draft Results (Draft #{draft_id})**\n")

    for conf in conferences:
        owners = await get_conference_owners(draft_id, conf)
        teams = await get_conference_teams(draft_id, conf)

        # Owners
        owner_names = []
        for o in owners:
            member = await safe_fetch_member(interaction.guild, o["user_id"])
            owner_names.append(member.display_name if member else f"User {o['user_id']}")

        # Teams
        if teams:
            team_list = "\n".join([f"  {t['pick_number']}. {t['team_name']}" for t in teams])
        else:
            team_list = "  None"

        output_lines.append(
            f"**{conf}**\n"
            f"Owners: {', '.join(owner_names)}\n"
            f"Teams:\n{team_list}\n"
        )

    final_output = "\n".join(output_lines)

    await interaction.response.send_message(final_output, ephemeral=False)
#============================================================
# SECTION 4 — Health Server + Bot Runner
#============================================================

#------------------------------------------------------------
# Simple health check server for Render
#------------------------------------------------------------
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
