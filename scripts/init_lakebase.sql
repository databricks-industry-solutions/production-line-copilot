-- Lakebase Postgres schema for ProdLine CoPilot
-- Run once: psql -f scripts/init_lakebase.sql

CREATE SCHEMA IF NOT EXISTS ai_chatbot;

CREATE TABLE IF NOT EXISTS ai_chatbot.conversations (
    conversation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shift_id TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ai_chatbot.messages (
    message_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID REFERENCES ai_chatbot.conversations(conversation_id),
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ai_chatbot.sessions (
    session_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    shift_id TEXT NOT NULL,
    started_at TIMESTAMPTZ DEFAULT now(),
    last_active_at TIMESTAMPTZ DEFAULT now(),
    current_context JSONB DEFAULT '{}',
    session_summary TEXT
);

CREATE TABLE IF NOT EXISTS ai_chatbot.optimization_results (
    result_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES ai_chatbot.sessions(session_id),
    optimizer_type TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    parameters JSONB NOT NULL,
    result JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    expires_at TIMESTAMPTZ DEFAULT now() + INTERVAL '1 hour'
);

CREATE TABLE IF NOT EXISTS ai_chatbot.feedback (
    feedback_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id UUID REFERENCES ai_chatbot.messages(message_id),
    session_id UUID REFERENCES ai_chatbot.sessions(session_id),
    rating INT CHECK (rating BETWEEN 1 AND 5),
    was_actionable BOOLEAN,
    was_accurate BOOLEAN,
    operator_comment TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ai_chatbot.agent_learnings (
    learning_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_event_id TEXT,
    shift_id TEXT,
    category TEXT NOT NULL,
    situation TEXT NOT NULL,
    action_taken TEXT NOT NULL,
    outcome TEXT NOT NULL,
    learning TEXT NOT NULL,
    confidence FLOAT DEFAULT 0.5,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation ON ai_chatbot.messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_sessions_user_shift ON ai_chatbot.sessions(user_id, shift_id);
CREATE INDEX IF NOT EXISTS idx_opt_cache_lookup ON ai_chatbot.optimization_results(optimizer_type, input_hash);
CREATE INDEX IF NOT EXISTS idx_feedback_message ON ai_chatbot.feedback(message_id);

-- Grant permissions to App service principal (update role name as needed)
-- GRANT USAGE ON SCHEMA ai_chatbot TO "app_sp";
-- GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA ai_chatbot TO "app_sp";
