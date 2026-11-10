-- PS05 - Conversations, messages, and structured conversation memory
--
-- Enables evidence-grounded research workspaces and conversational follow-ups.
-- A message references existing verification runs, claims, and evidence rows
-- rather than duplicating full text into transcripts.

CREATE TABLE IF NOT EXISTS tev.conversations (
    id                  text PRIMARY KEY,
    title               text NOT NULL,
    budget_mode         text NOT NULL DEFAULT 'balanced',
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    structured_memory   jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS tev.messages (
    id                  text PRIMARY KEY,
    conversation_id     text NOT NULL REFERENCES tev.conversations(id) ON DELETE CASCADE,
    role                text NOT NULL,
    content             text NOT NULL,
    run_id              text REFERENCES tev.verification_runs(id),
    citations           jsonb NOT NULL DEFAULT '[]'::jsonb,
    quality_metrics     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS messages_conv_idx
    ON tev.messages (conversation_id, created_at);
