-- Hebbot substrate schema
-- See docs/foundations.md Appendix A for the design rationale.

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- Schema metadata for migrations
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', '2');
INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('created_at', datetime('now'));

-- ---------------------------------------------------------------------------
-- Skills: the skill store. Each row is one skill, with a typed execution body.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS skills (
    name           TEXT PRIMARY KEY,
    params_schema  TEXT,                            -- JSON Schema for parameters
    expectations   TEXT,                            -- JSON: per-phase expectation bounds
    body_type      TEXT NOT NULL CHECK (body_type IN (
                       'classical', 'script', 'composition',
                       'trajectory', 'learned_policy', 'vlm_grounded'
                   )),
    body           TEXT NOT NULL,                   -- JSON: type-specific payload
    version        INTEGER NOT NULL DEFAULT 1,
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL,
    statistics     TEXT                             -- JSON: success rate, surprise freq, etc.
);

CREATE INDEX IF NOT EXISTS idx_skills_body_type ON skills(body_type);

-- ---------------------------------------------------------------------------
-- Episodes: one row per skill invocation. parent_id nests compositions.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS episodes (
    id              TEXT PRIMARY KEY,               -- UUID
    skill_name      TEXT NOT NULL,
    parent_id       TEXT,                           -- for composition; nullable
    started_at      REAL NOT NULL,
    ended_at        REAL,
    success         INTEGER,                        -- 0/1, NULL while running
    params          TEXT,                           -- JSON
    deliberate_ctx  TEXT,                           -- JSON: goal, reasoning, retrieved_ids
    outcome         TEXT,                           -- JSON: duration, notes, structured result
    body_type_used  TEXT,                           -- which body executed
    FOREIGN KEY (parent_id) REFERENCES episodes(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_episodes_skill ON episodes(skill_name);
CREATE INDEX IF NOT EXISTS idx_episodes_parent ON episodes(parent_id);
CREATE INDEX IF NOT EXISTS idx_episodes_started ON episodes(started_at);

-- ---------------------------------------------------------------------------
-- Frames: camera frames captured during an episode. Multiple cameras per episode.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS frames (
    id          TEXT PRIMARY KEY,
    episode_id  TEXT NOT NULL,
    camera      TEXT NOT NULL,                      -- "wrist", "scene", "zed_2i", etc.
    t           REAL NOT NULL,                      -- seconds since episode start
    file_path   TEXT NOT NULL,                      -- relative to data root
    width       INTEGER,
    height      INTEGER,
    FOREIGN KEY (episode_id) REFERENCES episodes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_frames_episode_cam ON frames(episode_id, camera);
CREATE INDEX IF NOT EXISTS idx_frames_episode_t ON frames(episode_id, t);

-- ---------------------------------------------------------------------------
-- States: robot state time-series. Could later become Parquet per-episode if it grows.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS states (
    episode_id     TEXT NOT NULL,
    t              REAL NOT NULL,
    joints         TEXT,                            -- JSON: [q0..q6]
    ee_pose        TEXT,                            -- JSON: flattened 4x4
    force          TEXT,                            -- JSON: [fx, fy, fz, tx, ty, tz]
    gripper_width  REAL,
    FOREIGN KEY (episode_id) REFERENCES episodes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_states_episode_t ON states(episode_id, t);

-- ---------------------------------------------------------------------------
-- Decisions: every substrate routing decision (replay vs orchestrator vs policy).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS decisions (
    id                TEXT PRIMARY KEY,
    episode_id        TEXT,                         -- nullable: pre-episode decisions exist
    timestamp         REAL NOT NULL,
    skill_called      TEXT NOT NULL,
    params            TEXT,                         -- JSON
    relational_state  TEXT,                         -- JSON: the state vector
    candidates        TEXT,                         -- JSON: top-k retrieved
    decision          TEXT NOT NULL CHECK (decision IN ('replay', 'orchestrator', 'policy')),
    reasoning         TEXT,
    threshold_values  TEXT,                         -- JSON
    outcome           TEXT,
    FOREIGN KEY (episode_id) REFERENCES episodes(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_decisions_skill ON decisions(skill_called);
CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(timestamp);

-- ---------------------------------------------------------------------------
-- Events: the unified rollout record (schema v2).
--
-- Frames, states and decisions each answered one question about an episode, and
-- reassembling them meant guessing: matching a capture to a robot state by nearest
-- timestamp, and identifying a checkpoint by a substring of its filename. Both are
-- fragile enough that only 5 of 76 lifetime grasps yielded a usable training example
-- (lab notebook #146). An event says explicitly what happened, in what phase, anchored
-- to the exact state timestamp and the exact frames captured with it, so a reader
-- reconstructs the rollout instead of inferring it.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,
    episode_id  TEXT NOT NULL,
    seq         INTEGER NOT NULL,                   -- monotonic within episode; the ordering authority
    t           REAL NOT NULL,                      -- seconds since episode start
    phase       TEXT NOT NULL,                      -- approach | align | grasp | lift | verify | abstain | reset
    kind        TEXT NOT NULL,                      -- observation | motion | contact | closure | verdict | refusal
    label       TEXT,                               -- specific checkpoint name, e.g. "before_close"
    data        TEXT,                               -- JSON payload
    state_t     REAL,                               -- exact t of the anchoring states row, if any
    frame_ids   TEXT,                               -- JSON: ids of frames captured at this event
    FOREIGN KEY (episode_id) REFERENCES episodes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_events_episode_seq ON events(episode_id, seq);
CREATE INDEX IF NOT EXISTS idx_events_phase ON events(episode_id, phase);
CREATE INDEX IF NOT EXISTS idx_events_label ON events(episode_id, label);
