-- Chạy trong Supabase SQL Editor trước khi khởi động Content Service.

-- Dùng timestamp UTC không timezone để tương thích model hiện tại.

BEGIN;

SET LOCAL search_path = public;


CREATE TABLE IF NOT EXISTS locations (
	id SERIAL NOT NULL, 
	name VARCHAR NOT NULL, 
	description_vi VARCHAR NOT NULL, 
	target_languages VARCHAR NOT NULL, 
	description_source VARCHAR, 
	latitude FLOAT, 
	longitude FLOAT, 
	radius FLOAT, 
	source_lang VARCHAR, 
	source VARCHAR, 
	source_title VARCHAR, 
	source_page_id INTEGER, 
	source_url VARCHAR, 
	last_synced_at TIMESTAMP WITHOUT TIME ZONE, 
	is_ready BOOLEAN, 
	PRIMARY KEY (id), 
	CONSTRAINT ux_locations_source UNIQUE (source, source_lang, source_page_id)
)

;


CREATE TABLE IF NOT EXISTS narration_cache (
	id SERIAL NOT NULL, 
	location_id INTEGER NOT NULL, 
	requested_lang VARCHAR NOT NULL, 
	lang VARCHAR NOT NULL, 
	text TEXT NOT NULL, 
	source_url VARCHAR, 
	target_title VARCHAR, 
	is_fallback BOOLEAN NOT NULL, 
	reason VARCHAR, 
	voice VARCHAR, 
	text_hash VARCHAR, 
	audio_filename VARCHAR, 
	audio_status VARCHAR NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ux_narration_location_lang UNIQUE (location_id, requested_lang), 
	FOREIGN KEY(location_id) REFERENCES locations (id) ON DELETE CASCADE
)

;


CREATE TABLE IF NOT EXISTS narrations (
	id VARCHAR NOT NULL, 
	location_id INTEGER NOT NULL, 
	language VARCHAR NOT NULL, 
	voice VARCHAR NOT NULL, 
	content_version VARCHAR NOT NULL, 
	location_snapshot TEXT NOT NULL, 
	source_text TEXT, 
	source_url TEXT, 
	final_text TEXT, 
	text_hash VARCHAR, 
	audio_filename VARCHAR, 
	storage_path VARCHAR, 
	status VARCHAR NOT NULL, 
	attempts INTEGER NOT NULL, 
	lease_token VARCHAR, 
	lease_until TIMESTAMP WITHOUT TIME ZONE, 
	retry_at TIMESTAMP WITHOUT TIME ZONE, 
	error_code VARCHAR, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ux_narrations_version UNIQUE (location_id, language, voice, content_version), 
	FOREIGN KEY(location_id) REFERENCES locations (id) ON DELETE RESTRICT
)

;

CREATE INDEX IF NOT EXISTS ix_narrations_status ON public.narrations (status, lease_until);

CREATE INDEX IF NOT EXISTS ix_narrations_location ON public.narrations (location_id, status);

DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_narrations_status' AND conrelid = 'public.narrations'::regclass) THEN ALTER TABLE public.narrations ADD CONSTRAINT ck_narrations_status CHECK (status IN ('pending', 'processing', 'ready', 'failed')); END IF; END $$;

DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_narrations_ready' AND conrelid = 'public.narrations'::regclass) THEN ALTER TABLE public.narrations ADD CONSTRAINT ck_narrations_ready CHECK (status <> 'ready' OR (final_text IS NOT NULL AND text_hash IS NOT NULL AND storage_path IS NOT NULL)); END IF; END $$;

ALTER TABLE public.locations ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.locations FROM anon, authenticated;

ALTER TABLE public.narration_cache ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.narration_cache FROM anon, authenticated;

ALTER TABLE public.narrations ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.narrations FROM anon, authenticated;

INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types) VALUES ('narration-audio', 'narration-audio', false, 2000000, ARRAY['audio/mpeg']) ON CONFLICT (id) DO NOTHING;

-- Không tạo policy public cho bucket. Backend secret key là bên duy nhất upload/ký URL.

COMMIT;
