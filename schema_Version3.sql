-- Updated DB schema supporting claim + conference initial round
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
    conference_chosen INTEGER DEFAULT 0
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
    picked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assigned_teams (
    team_name TEXT PRIMARY KEY,
    draft_id INTEGER,
    user_id INTEGER
);