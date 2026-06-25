-- ═══════════════════════════════════════════════════════════════
-- CivicPulse — Enhanced Supabase SQL Schema v2
-- Run this in Supabase > SQL Editor
-- ═══════════════════════════════════════════════════════════════

-- Users table
CREATE TABLE IF NOT EXISTS users (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name        TEXT NOT NULL,
  email       TEXT UNIQUE NOT NULL,
  password    TEXT NOT NULL,
  role        TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
  phone       TEXT DEFAULT '',
  is_active   BOOLEAN DEFAULT TRUE,
  created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Complaints table
CREATE TABLE IF NOT EXISTS complaints (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  ref_id       TEXT UNIQUE,
  user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title        TEXT NOT NULL,
  description  TEXT NOT NULL,
  location     TEXT NOT NULL,
  image_url    TEXT,
  category     TEXT DEFAULT 'Other',
  priority     TEXT DEFAULT 'Medium' CHECK (priority IN ('Low', 'Medium', 'High')),
  department   TEXT DEFAULT 'General Services',
  summary      TEXT,
  sentiment    TEXT DEFAULT 'Neutral',
  tags         TEXT DEFAULT '[]',
  status       TEXT DEFAULT 'Pending' CHECK (status IN ('Pending', 'In Progress', 'Resolved', 'Withdrawn')),
  is_duplicate BOOLEAN DEFAULT FALSE,
  duplicate_of UUID REFERENCES complaints(id),
  resolved_at  TIMESTAMPTZ,
  created_at   TIMESTAMPTZ DEFAULT NOW()
);

-- Complaint history/audit trail
CREATE TABLE IF NOT EXISTS complaint_history (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  complaint_id  UUID NOT NULL REFERENCES complaints(id) ON DELETE CASCADE,
  action        TEXT NOT NULL,
  old_value     TEXT DEFAULT '',
  new_value     TEXT DEFAULT '',
  changed_by    TEXT DEFAULT 'System',
  created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Comments / remarks
CREATE TABLE IF NOT EXISTS complaint_comments (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  complaint_id  UUID NOT NULL REFERENCES complaints(id) ON DELETE CASCADE,
  user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  content       TEXT NOT NULL,
  created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Notifications
CREATE TABLE IF NOT EXISTS notifications (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title         TEXT NOT NULL,
  message       TEXT NOT NULL,
  complaint_id  UUID REFERENCES complaints(id) ON DELETE SET NULL,
  is_read       BOOLEAN DEFAULT FALSE,
  created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Activity logs / audit trail
CREATE TABLE IF NOT EXISTS activity_logs (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  action        TEXT NOT NULL,
  entity_type   TEXT NOT NULL,
  entity_id     TEXT NOT NULL,
  user_id       UUID REFERENCES users(id) ON DELETE SET NULL,
  details       TEXT DEFAULT '',
  created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_complaints_user_id   ON complaints(user_id);
CREATE INDEX IF NOT EXISTS idx_complaints_status    ON complaints(status);
CREATE INDEX IF NOT EXISTS idx_complaints_category  ON complaints(category);
CREATE INDEX IF NOT EXISTS idx_complaints_priority  ON complaints(priority);
CREATE INDEX IF NOT EXISTS idx_complaints_created   ON complaints(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_history_complaint    ON complaint_history(complaint_id);
CREATE INDEX IF NOT EXISTS idx_comments_complaint   ON complaint_comments(complaint_id);
CREATE INDEX IF NOT EXISTS idx_notifications_user   ON notifications(user_id, is_read);
CREATE INDEX IF NOT EXISTS idx_activity_entity      ON activity_logs(entity_id);

-- Seed admin account (password: admin123)
INSERT INTO users (id, name, email, password, role)
VALUES (
  gen_random_uuid(), 'City Admin', 'admin@civic.gov',
  '240be518fabd2724ddb6f04eeb1da5967448d7e831c08c8fa822809f74c720a9', 'admin'
) ON CONFLICT (email) DO NOTHING;

-- ── Storage ────────────────────────────────────────────────────────
-- In Supabase Dashboard > Storage > New Bucket:
--   Name: complaint-images
--   Public: true
