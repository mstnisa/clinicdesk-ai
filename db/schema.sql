-- ClinicDesk AI schema for Postgres / Supabase.
-- The agent creates these tables itself on start-up (SQLAlchemy); this file is
-- for running the schema by hand in the Supabase SQL editor.
--
-- Security model: only the agent service (server-side, service-role key or a
-- dedicated Postgres user) reads and writes these tables. Row Level Security is
-- enabled with NO public policies, so the anon/public Supabase key can read nothing.

CREATE TABLE answer_cache (
	key VARCHAR(64) NOT NULL, 
	answer TEXT, 
	knowledge_version VARCHAR(16), 
	hits INTEGER, 
	created_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (key)
);

ALTER TABLE answer_cache ENABLE ROW LEVEL SECURITY;

CREATE TABLE conversations (
	id SERIAL NOT NULL, 
	channel VARCHAR(20) NOT NULL, 
	sender_id VARCHAR(64) NOT NULL, 
	sender_masked VARCHAR(32), 
	contact_phone VARCHAR(32), 
	language VARCHAR(4), 
	status VARCHAR(16), 
	created_at TIMESTAMP WITH TIME ZONE, 
	updated_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_conversations_sender_id ON conversations (sender_id);
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;

CREATE TABLE llm_calls (
	id SERIAL NOT NULL, 
	conversation_id INTEGER, 
	purpose VARCHAR(16), 
	provider VARCHAR(16), 
	model VARCHAR(64), 
	input_tokens INTEGER, 
	output_tokens INTEGER, 
	cached_tokens INTEGER, 
	cost_usd FLOAT, 
	latency_ms INTEGER, 
	created_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_llm_calls_conversation_id ON llm_calls (conversation_id);
ALTER TABLE llm_calls ENABLE ROW LEVEL SECURITY;

CREATE TABLE outreach (
	id SERIAL NOT NULL, 
	company VARCHAR(160), 
	website VARCHAR(200), 
	city VARCHAR(60), 
	email VARCHAR(160), 
	icp_score INTEGER, 
	icp_reasons TEXT, 
	subject VARCHAR(200), 
	body TEXT, 
	status VARCHAR(20), 
	created_at TIMESTAMP WITH TIME ZONE, 
	updated_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id)
);

ALTER TABLE outreach ENABLE ROW LEVEL SECURITY;

CREATE TABLE security_events (
	id SERIAL NOT NULL, 
	conversation_id INTEGER, 
	kind VARCHAR(24), 
	detail TEXT, 
	created_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id)
);

ALTER TABLE security_events ENABLE ROW LEVEL SECURITY;

CREATE TABLE escalations (
	id SERIAL NOT NULL, 
	conversation_id INTEGER, 
	kind VARCHAR(20), 
	reason TEXT, 
	status VARCHAR(12), 
	created_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(conversation_id) REFERENCES conversations (id)
);

CREATE INDEX ix_escalations_conversation_id ON escalations (conversation_id);
ALTER TABLE escalations ENABLE ROW LEVEL SECURITY;

CREATE TABLE leads (
	id SERIAL NOT NULL, 
	conversation_id INTEGER, 
	name VARCHAR(120), 
	phone VARCHAR(32), 
	condition VARCHAR(200), 
	urgency VARCHAR(16), 
	score INTEGER, 
	stage VARCHAR(16), 
	source VARCHAR(20), 
	notes TEXT, 
	created_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(conversation_id) REFERENCES conversations (id)
);

CREATE INDEX ix_leads_conversation_id ON leads (conversation_id);
ALTER TABLE leads ENABLE ROW LEVEL SECURITY;

CREATE TABLE messages (
	id SERIAL NOT NULL, 
	conversation_id INTEGER, 
	role VARCHAR(12), 
	content TEXT, 
	route VARCHAR(24), 
	created_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(conversation_id) REFERENCES conversations (id)
);

CREATE INDEX ix_messages_conversation_id ON messages (conversation_id);
ALTER TABLE messages ENABLE ROW LEVEL SECURITY;

CREATE TABLE bookings (
	id SERIAL NOT NULL, 
	lead_id INTEGER, 
	conversation_id INTEGER, 
	name VARCHAR(120), 
	phone VARCHAR(32), 
	service VARCHAR(40), 
	slot_start VARCHAR(32), 
	status VARCHAR(16), 
	created_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(lead_id) REFERENCES leads (id), 
	FOREIGN KEY(conversation_id) REFERENCES conversations (id)
);

CREATE INDEX ix_bookings_conversation_id ON bookings (conversation_id);
ALTER TABLE bookings ENABLE ROW LEVEL SECURITY;

-- Useful views for reporting tools (Metabase, Looker Studio, Supabase dashboard)
CREATE VIEW daily_ai_cost AS
SELECT date_trunc('day', created_at) AS day, purpose, model,
       count(*) AS calls, sum(input_tokens) AS input_tokens, sum(cached_tokens) AS cached_tokens,
       sum(output_tokens) AS output_tokens, round(sum(cost_usd)::numeric, 4) AS cost_usd
FROM llm_calls GROUP BY 1, 2, 3 ORDER BY 1 DESC;

CREATE VIEW funnel AS
SELECT (SELECT count(*) FROM conversations) AS conversations,
       (SELECT count(*) FROM leads) AS leads,
       (SELECT count(*) FROM bookings WHERE status = 'confirmed') AS bookings,
       (SELECT count(*) FROM escalations WHERE kind = 'red_flag') AS red_flags;
