#!/usr/bin/env python3
"""
Discord draft bot (claim + conference first round, claim is extra).

Commands:
- /start_draft participants
- /claim <team_name>
- /choose_conference <name>
- /pick <team_name>
- /list_available
- /status
- /conference_rosters
- /conference_view <conference>
- /list_conferences
- /end_draft

Improvements:
- /list_conferences shows slot usage (max 2 per conference).
- When a user attempts to claim/pick an already-taken team, the bot responds with who has it and whether it was a claim or a normal pick (and pick number if known).
"""
import os
import json
import aiosqlite
import asyncio
import io
from datetime import datetime
from typing import Optional, List, Dict, Tuple

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))

DB_PATH = "draft.db"
TEAMS_JSON = "teams.json"

intents = discord.Intents.default()
intents.members = True
intents.guilds = True
bot = commands.Bot(command_prefix="!", intents=intents)
intents.message_content = False

# Load teams list: teams.json must be an array of team name strings
with open(TEAMS_JSON, "r", encoding="utf-8") as f:
    TEAMS = json.load(f)
if not isinstance(TEAMS, list):
    raise SystemExit("teams.json must be a JSON array of team name strings.")

def normalize_name(n: str) -> str:
    return " ".join(n.strip().split()).lower()

TEAM_SET = { normalize_name(t): t for t in TEAMS }

# --- DB init ---
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER,
            status TEXT NOT NULL,
            stage TEXT NOT NULL DEFAULT 'conference',
            created_at TEXT NOT NULL,
            current_pick_index INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            draft_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            pick_order INTEGER NOT NULL,
            claimed_team TEXT DEFAULT NULL,
            conference TEXT DEFAULT NULL,
            claimed INTEGER DEFAULT 0,
            conference_chosen INTEGER DEFAULT 0,
            FOREIGN KEY(draft_id) REFERENCES drafts(id)
        );
        CREATE TABLE IF NOT EXISTS participant_limits (
            draft_id INTEGER,
            user_id INTEGER,
            picks_allowed INTEGER,
            PRIMARY KEY (draft_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS picks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            draft_id INTEGER NOT NULL,
            pick_number INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            team_name TEXT NOT NULL,
            picked_at TEXT NOT NULL,
            FOREIGN KEY(draft_id) REFERENCES drafts(id)
        );
        CREATE TABLE IF NOT EXISTS assigned_teams (
            team_name TEXT PRIMARY KEY,
            draft_id INTEGER,
            user_id INTEGER
        );
        """)
        await db.commit()

@bot.event
async def on_ready():
    await init_db()
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands")
    except Exception as e:
        print("Failed to sync commands:", e)

# --- DB helpers ---
async def create_draft(guild_id: int, channel_id: int) -> int:
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO drafts (guild_id, channel_id, status, stage, created_at, current_pick_index) VALUES (?, ?, ?, ?, ?, ?)",
            (guild_id, channel_id, "active", "conference", now, 0)
        )
        await db.commit()
        return cur.lastrowid

async def add_participants(draft_id: int, user_ids: List[int], picks_allowed_for_teams: int = 7):
    async with aiosqlite.connect(DB_PATH) as db:
        for order, uid in enumerate(user_ids):
            await db.execute(
                "INSERT INTO participants (draft_id, user_id, pick_order) VALUES (?, ?, ?)",
                (draft_id, uid, order)
            )
            await db.execute(
                "INSERT OR REPLACE INTO participant_limits (draft_id, user_id, picks_allowed) VALUES (?, ?, ?)",
                (draft_id, uid, picks_allowed_for_teams)
            )
        await db.commit()

async def get_active_draft(guild_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, channel_id, status, stage, created_at, current_pick_index FROM drafts WHERE guild_id = ? AND status = 'active' ORDER BY id DESC LIMIT 1",
            (guild_id,)
        )
        row = await cur.fetchone()
        if not row:
            return None
        return {"id": row[0], "channel_id": row[1], "status": row[2], "stage": row[3], "created_at": row[4], "current_pick_index": row[5]}

async def get_latest_draft(guild_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, channel_id, status, stage, created_at, current_pick_index FROM drafts WHERE guild_id = ? ORDER BY id DESC LIMIT 1",
            (guild_id,)
        )
        row = await cur.fetchone()
        if not row:
            return None
        return {"id": row[0], "channel_id": row[1], "status": row[2], "stage": row[3], "created_at": row[4], "current_pick_index": row[5]}

async def get_current_or_latest_draft(guild_id: int) -> Optional[dict]:
    d = await get_active_draft(guild_id)
    if d:
        return d
    return await get_latest_draft(guild_id)

async def get_participant_by_pick(draft_id: int, pick_index: int) -> Optional[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT user_id FROM participants WHERE draft_id = ? AND pick_order = ?",
            (draft_id, pick_index)
        )
        row = await cur.fetchone()
        return row[0] if row else None

async def get_total_participants(draft_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM participants WHERE draft_id = ?", (draft_id,))
        row = await cur.fetchone()
        return row[0]

async def set_participant_claim(draft_id: int, user_id: int, team_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE participants SET claimed_team = ?, claimed = 1 WHERE draft_id = ? AND user_id = ?",
            (team_name, draft_id, user_id)
        )
        await db.execute(
            "INSERT INTO assigned_teams (team_name, draft_id, user_id) VALUES (?, ?, ?)",
            (team_name, draft_id, user_id)
        )
        await db.commit()

async def set_participant_conference(draft_id: int, user_id: int, conference: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE participants SET conference = ?, conference_chosen = 1 WHERE draft_id = ? AND user_id = ?", (conference, draft_id, user_id))
        await db.commit()

async def count_conference_users(draft_id: int, conference: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM participants WHERE draft_id = ? AND conference = ?", (draft_id, conference))
        row = await cur.fetchone()
        return row[0] if row else 0

async def increment_current_pick_index(draft_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE drafts SET current_pick_index = current_pick_index + 1 WHERE id = ?", (draft_id,))
        await db.commit()

async def reset_current_pick_index(draft_id: int, new_index: int = 0):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE drafts SET current_pick_index = ? WHERE id = ?", (new_index, draft_id))
        await db.commit()

async def set_draft_stage(draft_id: int, stage: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE drafts SET stage = ? WHERE id = ?", (stage, draft_id))
        await db.commit()

async def is_team_taken(team_name: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT 1 FROM assigned_teams WHERE team_name = ?", (team_name,))
        row = await cur.fetchone()
        return bool(row)

async def get_team_taken_info(team_name: str) -> Optional[Tuple[int, Optional[int], Optional[int]]]:
    """
    Return (user_id, draft_id, pick_number_or_None).
    pick_number_or_None is present if the team was taken via a picks entry; if claim only, pick_number is None.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, draft_id FROM assigned_teams WHERE team_name = ?", (team_name,))
        row = await cur.fetchone()
        if not row:
            return None
        user_id, draft_id = row[0], row[1]
        # try to find a pick_number for this team (if recorded in picks)
        cur2 = await db.execute("SELECT pick_number FROM picks WHERE draft_id = ? AND team_name = ? LIMIT 1", (draft_id, team_name))
        row2 = await cur2.fetchone()
        pick_number = row2[0] if row2 else None
        return (user_id, draft_id, pick_number)

async def record_team_pick(draft_id: int, user_id: int, global_pick_number: int, team_name: str):
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO picks (draft_id, pick_number, user_id, team_name, picked_at) VALUES (?, ?, ?, ?, ?)",
            (draft_id, global_pick_number, user_id, team_name, now)
        )
        await db.execute(
            "INSERT INTO assigned_teams (team_name, draft_id, user_id) VALUES (?, ?, ?)",
            (team_name, draft_id, user_id)
        )
        await db.execute(
            "UPDATE drafts SET current_pick_index = current_pick_index + 1 WHERE id = ?",
            (draft_id,)
        )
        await db.commit()

async def get_user_team_picks_count(draft_id: int, user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM picks WHERE draft_id = ? AND user_id = ?", (draft_id, user_id))
        row = await cur.fetchone()
        return row[0] if row else 0

async def get_user_picks_allowed(draft_id: int, user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT picks_allowed FROM participant_limits WHERE draft_id = ? AND user_id = ?", (draft_id, user_id))
        row = await cur.fetchone()
        return row[0] if row else 0

async def list_available_teams() -> List[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT team_name FROM assigned_teams")
        rows = await cur.fetchall()
        taken = {r[0] for r in rows}
    return [t for n,t in TEAM_SET.items() if t not in taken]

# --- Conference roster helpers ---
async def build_conference_mapping_for_draft(draft_id: int) -> Dict[str, Dict[int, List[str]]]:
    """
    Returns mapping:
      conference_name -> { user_id -> [team1, team2, ...] }
    Includes claimed_team (participants.claimed_team) and picks (picks.team_name).
    Users without conference set will be grouped under '(unassigned)' key.
    """
    mapping: Dict[str, Dict[int, List[str]]] = {}

    async with aiosqlite.connect(DB_PATH) as db:
        # Fetch participants and their claimed_team and conference
        cur = await db.execute("SELECT user_id, claimed_team, conference FROM participants WHERE draft_id = ?", (draft_id,))
        participants = await cur.fetchall()
        # Initialize mapping entries
        for user_id, claimed_team, conference in participants:
            conf_key = conference if conference else "(unassigned)"
            if conf_key not in mapping:
                mapping[conf_key] = {}
            mapping[conf_key].setdefault(user_id, [])
            if claimed_team:
                mapping[conf_key][user_id].append(claimed_team)
        # Fetch picks made in this draft
        cur2 = await db.execute("SELECT user_id, team_name FROM picks WHERE draft_id = ? ORDER BY pick_number ASC", (draft_id,))
        picks = await cur2.fetchall()
        for user_id, team_name in picks:
            # find this user's conference (could be null -> unassigned)
            cur3 = await db.execute("SELECT conference FROM participants WHERE draft_id = ? AND user_id = ?", (draft_id, user_id))
            row = await cur3.fetchone()
            conf = row[0] if row else None
            conf_key = conf if conf else "(unassigned)"
            if conf_key not in mapping:
                mapping[conf_key] = {}
            mapping[conf_key].setdefault(user_id, [])
            mapping[conf_key][user_id].append(team_name)
    return mapping

def format_conference_mapping(mapping: Dict[str, Dict[int, List[str]]], guild: discord.Guild) -> str:
    lines: List[str] = []
    for conf in sorted(mapping.keys()):
        lines.append(f"=== {conf} ===")
        users = mapping[conf]
        if not users:
            lines.append("  (no users)")
            continue
        for uid, teams in users.items():
            member_display = f"<@{uid}>"
            if teams:
                lines.append(f"- {member_display}: {', '.join(teams)}")
            else:
                lines.append(f"- {member_display}: (no teams)")
        lines.append("")  # blank line after each conference
    return "\n".join(lines)

# --- New helper: list conferences (slot counts) ---
async def get_conference_slots(draft_id: int) -> Dict[str, List[int]]:
    """
    Returns mapping conference_name -> list of user_ids (length <= 2)
    Includes '(unassigned)' for participants without conference set.
    """
    mapping: Dict[str, List[int]] = {}
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, conference FROM participants WHERE draft_id = ? ORDER BY pick_order ASC", (draft_id,))
        rows = await cur.fetchall()
        for user_id, conference in rows:
            key = conference if conference else "(unassigned)"
            mapping.setdefault(key, []).append(user_id)
    return mapping

# --- Commands ---
@bot.tree.command(name="start_draft", description="Create and start a new draft. Provide participants in pick order (space-separated mentions or IDs).")
@app_commands.describe(participants="Mention participants in pick order (space separated). Example: @User1 @User2")
async def start_draft(interaction: discord.Interaction, participants: str):
    if ADMIN_USER_ID and interaction.user.id != ADMIN_USER_ID and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You don't have permission to start a draft.", ephemeral=True)
        return

    mention_ids = []
    for token in participants.split():
        if token.startswith("<@") and token.endswith(">"):
            token = token.strip("<@!>")
            try:
                mention_ids.append(int(token))
            except:
                pass
        else:
            try:
                mention_ids.append(int(token))
            except:
                pass

    if len(mention_ids) < 2:
        await interaction.response.send_message("Provide at least two participants.", ephemeral=True)
        return

    draft_id = await create_draft(interaction.guild_id, interaction.channel_id)
    await add_participants(draft_id, mention_ids, picks_allowed_for_teams=7)
    first_user_id = await get_participant_by_pick(draft_id, 0)
    first_member = interaction.guild.get_member(first_user_id)
    await interaction.response.send_message(
        f"Draft started (ID {draft_id}). Stage: claim+conference selection. It's {first_member.mention}'s claim turn. Use /claim <team_name> to declare your preassigned team (this is an extra pick), then /choose_conference <conference_name> to choose your conference (max 2 users per conference). After all users have claimed and chosen, the draft will move to the team-picking stage.",
        ephemeral=False
    )

@bot.tree.command(name="claim", description="Claim (declare) your preassigned team during the initial round. This is an extra pick and does not count against your team picks.")
@app_commands.describe(team_name="Exact team name (case-insensitive). Use /list_available to see options.")
async def claim(interaction: discord.Interaction, team_name: str):
    draft = await get_active_draft(interaction.guild_id)
    if not draft:
        await interaction.response.send_message("No active draft.", ephemeral=True)
        return
    if draft["stage"] != "conference":
        await interaction.response.send_message("Claiming is only allowed during the initial claim/conference selection round.", ephemeral=True)
        return

    draft_id = draft["id"]
    current_index = draft["current_pick_index"]
    expected_user_id = await get_participant_by_pick(draft_id, current_index)
    if expected_user_id is None:
        await interaction.response.send_message("Draft order exhausted or misconfigured.", ephemeral=True)
        return
    if interaction.user.id != expected_user_id:
        expected_member = interaction.guild.get_member(expected_user_id)
        await interaction.response.send_message(f"It is not your claim turn. It is {expected_member.mention}'s turn.", ephemeral=True)
        return

    norm = normalize_name(team_name)
    if norm not in TEAM_SET:
        await interaction.response.send_message("Unknown team name. Use /list_available to see valid team names.", ephemeral=True)
        return
    canonical = TEAM_SET[norm]

    if await is_team_taken(canonical):
        info = await get_team_taken_info(canonical)
        if info:
            taken_by_user, taken_draft_id, pick_num = info
            if pick_num:
                await interaction.response.send_message(f"That team was already picked by <@{taken_by_user}> (global pick #{pick_num}) in draft #{taken_draft_id}.", ephemeral=True)
            else:
                await interaction.response.send_message(f"That team was already claimed by <@{taken_by_user}> (initial claim) in draft #{taken_draft_id}.", ephemeral=True)
            return
        else:
            await interaction.response.send_message("That team is already taken.", ephemeral=True)
            return

    # Record claimed team (extra pick): set claimed_team and add to assigned_teams
    await set_participant_claim(draft_id, interaction.user.id, canonical)

    # After claiming, instruct user to choose conference (still same user's turn)
    await interaction.response.send_message(
        f"{interaction.user.mention} claimed '{canonical}' (extra pick). Now use /choose_conference <conference_name> to pick your conference (max 2 users per conference).",
        ephemeral=False
    )

@bot.tree.command(name="choose_conference", description="Choose your conference during the initial round. Must follow /claim.")
@app_commands.describe(conference="Conference name (freeform). Max 2 users per conference.")
async def choose_conference(interaction: discord.Interaction, conference: str):
    draft = await get_active_draft(interaction.guild_id)
    if not draft:
        await interaction.response.send_message("No active draft.", ephemeral=True)
        return
    if draft["stage"] != "conference":
        await interaction.response.send_message("Conference selection is only allowed during the initial claim/conference selection round.", ephemeral=True)
        return

    draft_id = draft["id"]
    current_index = draft["current_pick_index"]
    expected_user_id = await get_participant_by_pick(draft_id, current_index)
    if expected_user_id is None:
        await interaction.response.send_message("Draft order exhausted or misconfigured.", ephemeral=True)
        return
    if interaction.user.id != expected_user_id:
        expected_member = interaction.guild.get_member(expected_user_id)
        await interaction.response.send_message(f"It is not your conference turn. It is {expected_member.mention}'s turn.", ephemeral=True)
        return

    # Ensure user has claimed already
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT claimed FROM participants WHERE draft_id = ? AND user_id = ?", (draft_id, interaction.user.id))
        row = await cur.fetchone()
        claimed_flag = row[0] if row else 0
    if not claimed_flag:
        await interaction.response.send_message("You must use /claim <team_name> first to declare your preassigned team before choosing a conference.", ephemeral=True)
        return

    conf_norm = " ".join(conference.strip().split())
    # enforce 2-user-per-conference
    cnt = await count_conference_users(draft_id, conf_norm)
    if cnt >= 2:
        await interaction.response.send_message(f"The conference '{conf_norm}' already has 2 users. Pick a different conference.", ephemeral=True)
        return

    await set_participant_conference(draft_id, interaction.user.id, conf_norm)

    # advance to next participant
    await increment_current_pick_index(draft_id)

    # Check if all participants have both claimed and chosen conference
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM participants WHERE draft_id = ? AND claimed = 1 AND conference_chosen = 1", (draft_id,))
        done_count = (await cur.fetchone())[0]
        cur2 = await db.execute("SELECT COUNT(*) FROM participants WHERE draft_id = ?", (draft_id,))
        total = (await cur2.fetchone())[0]

    if done_count >= total:
        # Move to teams stage and reset pick index to 0
        await set_draft_stage(draft_id, "teams")
        await reset_current_pick_index(draft_id, 0)
        next_user_id = await get_participant_by_pick(draft_id, 0)
        next_member = interaction.guild.get_member(next_user_id) if next_user_id else None
        await interaction.response.send_message(
            f"{interaction.user.mention} chose conference '{conf_norm}'. All users finished claim/conference stage. Moving to team picks. It's {next_member.mention}'s turn to pick a team. Use /pick <team_name>.",
            ephemeral=False
        )
    else:
        next_index = (current_index + 1) % total
        next_user_id = await get_participant_by_pick(draft_id, next_index)
        next_member = interaction.guild.get_member(next_user_id)
        await interaction.response.send_message(
            f"{interaction.user.mention} chose conference '{conf_norm}'. Next: {next_member.mention}'s claim turn.",
            ephemeral=False
        )

@bot.tree.command(name="pick", description="Pick a team during the team-picking stage. Use exact team name (case-insensitive).")
@app_commands.describe(team_name="Exact team name (case-insensitive). Use /list_available to see options.")
async def slash_pick(interaction: discord.Interaction, team_name: str):
    draft = await get_active_draft(interaction.guild_id)
    if not draft:
        await interaction.response.send_message("There is no active draft.", ephemeral=True)
        return

    draft_id = draft["id"]
    if draft["stage"] != "teams":
        await interaction.response.send_message("Team picks are not allowed during the claim/conference round.", ephemeral=True)
        return

    current_index = draft["current_pick_index"]
    expected_user_id = await get_participant_by_pick(draft_id, current_index)
    if expected_user_id is None:
        await interaction.response.send_message("Draft order exhausted or misconfigured.", ephemeral=True)
        return
    if interaction.user.id != expected_user_id:
        expected_member = interaction.guild.get_member(expected_user_id)
        await interaction.response.send_message(f"It is not your turn. It is {expected_member.mention}'s turn.", ephemeral=True)
        return

    normalized = normalize_name(team_name)
    if normalized not in TEAM_SET:
        await interaction.response.send_message("Unknown team name. Use /list_available to see valid team names.", ephemeral=True)
        return
    canonical = TEAM_SET[normalized]

    if await is_team_taken(canonical):
        info = await get_team_taken_info(canonical)
        if info:
            taken_by_user, taken_draft_id, pick_num = info
            if pick_num:
                await interaction.response.send_message(f"That team was already picked by <@{taken_by_user}> (global pick #{pick_num}) in draft #{taken_draft_id}.", ephemeral=True)
            else:
                await interaction.response.send_message(f"That team was already claimed by <@{taken_by_user}> (initial claim) in draft #{taken_draft_id}.", ephemeral=True)
            return
        else:
            await interaction.response.send_message("That team is already taken.", ephemeral=True)
            return

    # Ensure user has chosen a conference
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT conference FROM participants WHERE draft_id = ? AND user_id = ?", (draft_id, interaction.user.id))
        row = await cur.fetchone()
        user_conf = row[0] if row else None
    if not user_conf:
        await interaction.response.send_message("You have not selected a conference. This should not happen if initial stage completed. Contact admin.", ephemeral=True)
        return

    # enforce per-user team pick limit (claims did not count)
    allowed = await get_user_picks_allowed(draft_id, interaction.user.id)
    made = await get_user_team_picks_count(draft_id, interaction.user.id)
    if made >= allowed:
        await interaction.response.send_message(f"You have already made {made} team picks and reached your limit ({allowed}).", ephemeral=True)
        return

    # global pick number is current_pick_index + 1
    global_pick_num = draft["current_pick_index"] + 1
    await record_team_pick(draft_id, interaction.user.id, global_pick_num, canonical)

    # After recording, compute next user
    total = await get_total_participants(draft_id)
    next_index = (current_index + 1) % total
    next_user_id = await get_participant_by_pick(draft_id, next_index)
    next_member = interaction.guild.get_member(next_user_id) if next_user_id else None

    await interaction.response.send_message(
        f"{interaction.user.mention} picked {canonical} (global pick #{global_pick_num}). Next: {next_member.mention if next_member else 'n/a'}",
        ephemeral=False
    )

@bot.tree.command(name="list_available", description="List available teams (not yet assigned).")
async def slash_list_available(interaction: discord.Interaction):
    draft = await get_active_draft(interaction.guild_id)
    if not draft:
        await interaction.response.send_message("No active draft.", ephemeral=True)
        return
    avail = await list_available_teams()
    if not avail:
        await interaction.response.send_message("No teams left.", ephemeral=True)
        return
    lines = [f"{t}" for t in avail]
    out = "\n".join(lines)
    await interaction.response.send_message(f"Available teams ({len(avail)}):\n{out}", ephemeral=True)

@bot.tree.command(name="status", description="Show draft status and recent picks (up to 50).")
async def slash_status(interaction: discord.Interaction):
    draft = await get_active_draft(interaction.guild_id)
    if not draft:
        await interaction.response.send_message("No active draft.", ephemeral=True)
        return
    draft_id = draft["id"]
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT pick_number, user_id, team_name, picked_at FROM picks WHERE draft_id = ? ORDER BY pick_number ASC LIMIT 50", (draft_id,))
        rows = await cur.fetchall()
        cur2 = await db.execute("SELECT user_id, claimed_team, conference FROM participants WHERE draft_id = ? ORDER BY pick_order ASC", (draft_id,))
        participants = await cur2.fetchall()

    lines = []
    lines.append(f"Draft stage: {draft['stage']}; current_pick_index: {draft['current_pick_index']}")
    lines.append("Participants (in order):")
    for p in participants:
        lines.append(f"- <@{p[0]}> — claimed_team: {p[1] or '(none)'}; conference: {p[2] or '(not chosen)'}")
    lines.append("\nRecent picks:")
    if rows:
        for r in rows:
            lines.append(f"Pick #{r[0]} — <@{r[1]}> — {r[2]} ({r[3]})")
    else:
        lines.append("No team picks yet.")
    await interaction.response.send_message("\n".join(lines), ephemeral=False)

@bot.tree.command(name="conference_rosters", description="Show teams assigned to each conference for the active or most-recent draft.")
async def conference_rosters(interaction: discord.Interaction):
    draft = await get_current_or_latest_draft(interaction.guild_id)
    if not draft:
        await interaction.response.send_message("No draft data available.", ephemeral=True)
        return
    draft_id = draft["id"]
    mapping = await build_conference_mapping_for_draft(draft_id)
    out = format_conference_mapping(mapping, interaction.guild)

    # If too long for a message, send as a file
    if len(out) > 1900:
        fp = io.StringIO(out)
        fp.seek(0)
        file = discord.File(fp, filename=f"conference_rosters_draft_{draft_id}.txt")
        await interaction.response.send_message(f"Conference rosters for draft {draft_id} (file):", file=file, ephemeral=False)
    else:
        await interaction.response.send_message(f"Conference rosters for draft {draft_id}:\n{out}", ephemeral=False)

@bot.tree.command(name="conference_view", description="Show teams assigned to a single conference for the active or most-recent draft.")
@app_commands.describe(conference="Conference name (exact match as chosen by users).")
async def conference_view(interaction: discord.Interaction, conference: str):
    draft = await get_current_or_latest_draft(interaction.guild_id)
    if not draft:
        await interaction.response.send_message("No draft data available.", ephemeral=True)
        return
    draft_id = draft["id"]
    mapping = await build_conference_mapping_for_draft(draft_id)
    conf_key = conference if conference in mapping else None
    # Also try normalized matching (case-insensitive)
    if conf_key is None:
        lower_map = {k.lower(): k for k in mapping.keys()}
        if conference.strip().lower() in lower_map:
            conf_key = lower_map[conference.strip().lower()]

    if not conf_key or conf_key not in mapping:
        await interaction.response.send_message(f"No entries found for conference '{conference}' in draft {draft_id}.", ephemeral=True)
        return

    users = mapping[conf_key]
    lines = [f"=== {conf_key} ==="]
    for uid, teams in users.items():
        lines.append(f"- <@{uid}>: {', '.join(teams) if teams else '(no teams)'}")
    out = "\n".join(lines)
    if len(out) > 1900:
        fp = io.StringIO(out)
        fp.seek(0)
        file = discord.File(fp, filename=f"conference_{conf_key}_draft_{draft_id}.txt")
        await interaction.response.send_message(f"Conference '{conf_key}' for draft {draft_id} (file):", file=file, ephemeral=False)
    else:
        await interaction.response.send_message(f"Conference '{conf_key}' for draft {draft_id}:\n{out}", ephemeral=False)

@bot.tree.command(name="list_conferences", description="Show current conferences and slot usage (max 2 users per conference) for the active or most-recent draft.")
async def list_conferences(interaction: discord.Interaction):
    draft = await get_current_or_latest_draft(interaction.guild_id)
    if not draft:
        await interaction.response.send_message("No draft data available.", ephemeral=True)
        return
    draft_id = draft["id"]
    mapping = await get_conference_slots(draft_id)

    lines: List[str] = []
    lines.append(f"Conferences (draft #{draft_id}) — each conference has max 2 slots.")
    for conf in sorted(mapping.keys(), key=lambda s: (s == "(unassigned)", s.lower())):
        users = mapping[conf]
        lines.append(f"- {conf}: {len(users)}/2 slots used")
        if users:
            lines.append("  " + ", ".join(f"<@{uid}>" for uid in users))
    out = "\n".join(lines)
    # If too long, send as file
    if len(out) > 1900:
        fp = io.StringIO(out)
        fp.seek(0)
        file = discord.File(fp, filename=f"conferences_draft_{draft_id}.txt")
        await interaction.response.send_message(f"Conference slots for draft {draft_id} (file):", file=file, ephemeral=False)
    else:
        await interaction.response.send_message(out, ephemeral=False)

@bot.tree.command(name="end_draft", description="End the current draft")
async def slash_end_draft(interaction: discord.Interaction):
    if ADMIN_USER_ID and interaction.user.id != ADMIN_USER_ID and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You don't have permission to end a draft.", ephemeral=True)
        return
    draft = await get_active_draft(interaction.guild_id)
    if not draft:
        await interaction.response.send_message("No active draft.", ephemeral=True)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE drafts SET status = ? WHERE id = ?", ("finished", draft["id"]))
        await db.commit()
    await interaction.response.send_message("Draft ended.", ephemeral=False)

if __name__ == "__main__":
    if not TOKEN:
        print("DISCORD_TOKEN not set in environment (.env)")
        raise SystemExit(1)
    bot.run(TOKEN)