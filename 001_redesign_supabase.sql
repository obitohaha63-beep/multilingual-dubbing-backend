-- ============================================================================
-- 001_redesign_supabase.sql  -  Thiết kế lại Supabase theo DFD mức 1 (6 bảng)
-- Chạy trong Supabase SQL Editor. IDEMPOTENT: chạy lại nhiều lần vẫn an toàn.
--
-- KHÔNG xóa bảng/cột/dữ liệu, KHÔNG đổi tên cột, KHÔNG đổi kiểu cột của locations,
-- narrations, narration_cache (mã Python hiện tại vẫn chạy nguyên vẹn sau migration này).
-- Thay đổi chính:
--   + Tạo visitor_sessions, analytics_events (TIMESTAMPTZ) và các hàm Dashboard/dọn dữ liệu.
--   + Thêm ràng buộc (NOT VALID), index, trigger bất biến phiên bản, narration_cache.narration_id.
--   + visitor_access_logs: liên kết visitor_sessions, created_at -> TIMESTAMPTZ.
--   + Bật RLS + thu quyền anon/authenticated cho cả 6 bảng (sửa thiếu REVOKE ở narrations).
--   + Ép bucket narration-audio ở chế độ private.
-- Cột thời gian của 3 bảng cũ chuyển TIMESTAMPTZ ở 002_timestamptz_existing_tables.sql,
-- CHỈ chạy sau khi Prompt 2 đã deploy mã tương thích.
-- Sau khi chạy: chạy 001_verify.sql để kiểm tra.
-- ============================================================================
BEGIN;

SET LOCAL search_path = public;
SET LOCAL lock_timeout = '15s';   -- không treo vô hạn nếu bảng đang bị khóa

-- Hàm tạm (chỉ tồn tại trong phiên này): thêm constraint nếu chưa có -> chạy lại nhiều lần vẫn an toàn.
CREATE FUNCTION pg_temp.ensure_constraint(p_table regclass, p_name text, p_definition text)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = p_name AND conrelid = p_table) THEN
    EXECUTE format('ALTER TABLE %s ADD CONSTRAINT %I %s', p_table, p_name, p_definition);
  END IF;
END $$;

-- Kiểm tra điều kiện trước khi chạy: 4 bảng hiện có phải tồn tại.
DO $$
DECLARE missing text;
BEGIN
  SELECT string_agg(t, ', ') INTO missing
  FROM unnest(ARRAY['locations', 'narrations', 'narration_cache', 'visitor_access_logs']) AS t
  WHERE to_regclass('public.' || t) IS NULL;
  IF missing IS NOT NULL THEN
    RAISE EXCEPTION 'Thiếu bảng: %. Dự án mới hãy chạy docs/supabase-schema.sql thay vì file migration này.', missing;
  END IF;
END $$;

-- ============================================================================
-- 1. D1 locations - bổ sung ràng buộc và index (không đổi cột, không xóa dữ liệu)
-- ============================================================================
ALTER TABLE public.locations ALTER COLUMN radius SET DEFAULT 80;
ALTER TABLE public.locations ALTER COLUMN is_ready SET DEFAULT false;

-- Ràng buộc thêm bằng NOT VALID: áp dụng cho dòng MỚI/được sửa, KHÔNG làm migration
-- thất bại vì dữ liệu cũ. Sau khi chạy 001_verify.sql thấy 0 dòng vi phạm thì mới VALIDATE.
SELECT pg_temp.ensure_constraint('public.locations', 'ck_locations_name_not_blank',
  'CHECK (btrim(name) <> '''') NOT VALID');
SELECT pg_temp.ensure_constraint('public.locations', 'ck_locations_lat_range',
  'CHECK (latitude IS NULL OR latitude BETWEEN -90 AND 90) NOT VALID');
SELECT pg_temp.ensure_constraint('public.locations', 'ck_locations_lon_range',
  'CHECK (longitude IS NULL OR longitude BETWEEN -180 AND 180) NOT VALID');
SELECT pg_temp.ensure_constraint('public.locations', 'ck_locations_gps_pair',
  'CHECK ((latitude IS NULL) = (longitude IS NULL)) NOT VALID');
SELECT pg_temp.ensure_constraint('public.locations', 'ck_locations_radius_positive',
  'CHECK (radius IS NULL OR radius > 0) NOT VALID');
-- UNIQUE (source, source_lang, source_page_id) không chặn trùng khi một cột là NULL
-- (PostgreSQL coi NULL là khác nhau). Ràng buộc này buộc bài Wikimedia luôn đủ khóa.
SELECT pg_temp.ensure_constraint('public.locations', 'ck_locations_wikimedia_complete',
  'CHECK (source IS DISTINCT FROM ''wikimedia'' OR (source_lang IS NOT NULL AND source_page_id IS NOT NULL
          AND latitude IS NOT NULL AND longitude IS NOT NULL)) NOT VALID');

CREATE INDEX IF NOT EXISTS ix_locations_geo ON public.locations (latitude, longitude)
  WHERE latitude IS NOT NULL AND longitude IS NOT NULL;

-- ============================================================================
-- 2. D2 narrations - ràng buộc, index, bất biến phiên bản
-- ============================================================================
SELECT pg_temp.ensure_constraint('public.narrations', 'ck_narrations_attempts_nonneg',
  'CHECK (attempts >= 0) NOT VALID');
SELECT pg_temp.ensure_constraint('public.narrations', 'ck_narrations_processing_lease',
  'CHECK (status <> ''processing'' OR (lease_token IS NOT NULL AND lease_until IS NOT NULL)) NOT VALID');
SELECT pg_temp.ensure_constraint('public.narrations', 'ck_narrations_language_fmt',
  'CHECK (language ~ ''^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$'') NOT VALID');

-- Mỗi file MP3 thuộc đúng một bản thuyết minh.
CREATE UNIQUE INDEX IF NOT EXISTS ux_narrations_storage_path
  ON public.narrations (storage_path) WHERE storage_path IS NOT NULL;

-- Không ghi đè phiên bản cũ: khóa phiên bản luôn bất biến; văn bản đã "ready" không bị sửa.
CREATE OR REPLACE FUNCTION public.trg_narrations_protect_version()
RETURNS trigger LANGUAGE plpgsql SET search_path = public, pg_catalog AS $$
BEGIN
  IF NEW.location_id     IS DISTINCT FROM OLD.location_id
     OR NEW.language        IS DISTINCT FROM OLD.language
     OR NEW.voice           IS DISTINCT FROM OLD.voice
     OR NEW.content_version IS DISTINCT FROM OLD.content_version
     OR NEW.created_at      IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'narrations: (location_id, language, voice, content_version, created_at) là bất biến; hãy tạo phiên bản mới thay vì sửa'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  IF OLD.status = 'ready'
     AND (NEW.final_text   IS DISTINCT FROM OLD.final_text
          OR NEW.text_hash IS DISTINCT FROM OLD.text_hash
          OR NEW.source_text IS DISTINCT FROM OLD.source_text) THEN
    RAISE EXCEPTION 'narrations: bản đã ready không được ghi đè nội dung; hãy tạo content_version mới'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE TRIGGER trg_narrations_protect_version
  BEFORE UPDATE ON public.narrations
  FOR EACH ROW EXECUTE FUNCTION public.trg_narrations_protect_version();

-- ============================================================================
-- 3. D3 narration_cache - liên kết tùy chọn tới narrations, giữ nguyên mọi cột cũ
-- ============================================================================
ALTER TABLE public.narration_cache ADD COLUMN IF NOT EXISTS narration_id VARCHAR;

SELECT pg_temp.ensure_constraint('public.narration_cache', 'fk_narration_cache_narration',
  'FOREIGN KEY (narration_id) REFERENCES public.narrations (id) ON DELETE SET NULL ON UPDATE CASCADE');
SELECT pg_temp.ensure_constraint('public.narration_cache', 'ck_narration_cache_audio_status',
  'CHECK (audio_status IN (''text_ready'', ''audio_ready'')) NOT VALID');
SELECT pg_temp.ensure_constraint('public.narration_cache', 'ck_narration_cache_audio_complete',
  'CHECK (audio_status <> ''audio_ready'' OR (audio_filename IS NOT NULL AND voice IS NOT NULL AND text_hash IS NOT NULL)) NOT VALID');
SELECT pg_temp.ensure_constraint('public.narration_cache', 'ck_narration_cache_lang_fmt',
  'CHECK (requested_lang ~ ''^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$'' AND lang ~ ''^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$'') NOT VALID');

CREATE INDEX IF NOT EXISTS ix_narration_cache_narration_id
  ON public.narration_cache (narration_id) WHERE narration_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_narration_cache_expires_at ON public.narration_cache (expires_at);
CREATE INDEX IF NOT EXISTS ix_narration_cache_ready
  ON public.narration_cache (location_id, audio_status, expires_at);

-- ============================================================================
-- 4. D5 visitor_sessions - mỗi trình duyệt ẩn danh một dòng
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.visitor_sessions (
  client_id        VARCHAR(64)  NOT NULL,
  started_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
  last_seen_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
  heartbeat_count  INTEGER      NOT NULL DEFAULT 1,
  user_agent_short VARCHAR(100),
  CONSTRAINT pk_visitor_sessions PRIMARY KEY (client_id),
  CONSTRAINT ck_visitor_sessions_client_id CHECK (length(client_id) >= 1),
  CONSTRAINT ck_visitor_sessions_time CHECK (last_seen_at >= started_at),
  CONSTRAINT ck_visitor_sessions_heartbeats CHECK (heartbeat_count >= 0)
);
-- "Đang online" = last_seen_at trong cửa sổ 90 giây: tính khi truy vấn, không lưu cờ để tránh dữ liệu cũ.
CREATE INDEX IF NOT EXISTS ix_visitor_sessions_last_seen ON public.visitor_sessions (last_seen_at DESC);

-- ============================================================================
-- 5. D5 analytics_events - một bảng thống nhất cho mọi sự kiện
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.analytics_events (
  id           BIGINT GENERATED ALWAYS AS IDENTITY,
  event_type   VARCHAR(32)      NOT NULL,
  occurred_at  TIMESTAMPTZ      NOT NULL DEFAULT now(),
  client_id    VARCHAR(64),
  location_id  INTEGER,
  language     VARCHAR(10),
  path         VARCHAR(120),
  distance_m   DOUBLE PRECISION,
  duration_ms  DOUBLE PRECISION,
  is_fallback  BOOLEAN,
  cache_hit    BOOLEAN,
  reason       VARCHAR(150),
  service      VARCHAR(32),
  request_type VARCHAR(64),
  status_code  INTEGER,
  error_type   VARCHAR(64),
  message      VARCHAR(500),
  metadata     JSONB            NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT pk_analytics_events PRIMARY KEY (id),
  CONSTRAINT fk_analytics_events_session FOREIGN KEY (client_id)
    REFERENCES public.visitor_sessions (client_id) ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_analytics_events_location FOREIGN KEY (location_id)
    REFERENCES public.locations (id) ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT ck_analytics_events_type CHECK (event_type IN (
    'page_view', 'location_detected', 'location_activated',
    'narration_requested', 'narration_success', 'narration_failed', 'error')),
  CONSTRAINT ck_analytics_events_error_fields CHECK (
    event_type <> 'error' OR (service IS NOT NULL AND request_type IS NOT NULL AND message IS NOT NULL)),
  CONSTRAINT ck_analytics_events_page_view CHECK (
    event_type <> 'page_view' OR (client_id IS NOT NULL AND path IS NOT NULL)),
  CONSTRAINT ck_analytics_events_language CHECK (
    language IS NULL OR language ~ '^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$'),
  CONSTRAINT ck_analytics_events_numbers CHECK (
    (duration_ms IS NULL OR duration_ms >= 0) AND (distance_m IS NULL OR distance_m >= 0)),
  CONSTRAINT ck_analytics_events_status_code CHECK (
    status_code IS NULL OR status_code BETWEEN 100 AND 599),
  CONSTRAINT ck_analytics_events_metadata CHECK (
    jsonb_typeof(metadata) = 'object' AND pg_column_size(metadata) <= 4096)
);
CREATE INDEX IF NOT EXISTS ix_analytics_events_occurred_at ON public.analytics_events (occurred_at);
CREATE INDEX IF NOT EXISTS ix_analytics_events_type_time ON public.analytics_events (event_type, occurred_at DESC);
CREATE INDEX IF NOT EXISTS ix_analytics_events_location_time ON public.analytics_events (location_id, occurred_at DESC)
  WHERE location_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_analytics_events_client ON public.analytics_events (client_id)
  WHERE client_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_analytics_events_errors ON public.analytics_events (occurred_at DESC)
  WHERE event_type = 'error';

-- Làm sạch dữ liệu nhạy cảm NGAY TẠI DATABASE (lớp bảo vệ thứ hai sau Python).
CREATE OR REPLACE FUNCTION public.analytics_sanitize_text(p_text text)
RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE SET search_path = public, pg_catalog AS $$
  SELECT CASE WHEN p_text IS NULL THEN NULL ELSE
    regexp_replace(
    regexp_replace(
    regexp_replace(
    regexp_replace(
    regexp_replace(p_text,
      '(password|passwd|secret|token|authorization|cookie|session|api[_-]?key)[=:[:space:]]+[^[:space:],;&]+',
      '\1=[REDACTED]', 'gi'),
      'Bearer[[:space:]]+[A-Za-z0-9._~+/=-]+', 'Bearer [REDACTED]', 'gi'),
      '://[^/[:space:]:@]+:[^@[:space:]]+@', '://[REDACTED]@', 'g'),
      '\y(eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]*|sb_(secret|publishable)_[A-Za-z0-9_-]+|AIza[0-9A-Za-z_-]{20,})',
      '[KEY_REDACTED]', 'g'),
      '\y([0-9]{1,3}\.){3}[0-9]{1,3}\y', '[IP_REDACTED]', 'g')
  END
$$;

-- Chỉ xử lý khóa cấp 1 của metadata; Backend nên gửi metadata phẳng.
CREATE OR REPLACE FUNCTION public.analytics_scrub_metadata(p_meta jsonb)
RETURNS jsonb LANGUAGE sql IMMUTABLE PARALLEL SAFE SET search_path = public, pg_catalog AS $$
  SELECT COALESCE(jsonb_object_agg(k,
           CASE WHEN jsonb_typeof(v) = 'string' THEN to_jsonb(public.analytics_sanitize_text(v #>> '{}')) ELSE v END),
         '{}'::jsonb)
  FROM jsonb_each(p_meta) AS t(k, v)
  WHERE lower(k) !~ '(pass|secret|token|authori[sz]ation|cookie|api[_-]?key|session|e?mail|coord|^ip$|ip[_-]?addr|^lat(itude)?$|^(lng|lon|longitude)$)'
$$;

CREATE OR REPLACE FUNCTION public.trg_analytics_events_sanitize()
RETURNS trigger LANGUAGE plpgsql SET search_path = public, pg_catalog AS $$
BEGIN
  NEW.message := left(public.analytics_sanitize_text(NEW.message), 500);
  NEW.reason  := left(public.analytics_sanitize_text(NEW.reason), 150);
  NEW.path    := left(split_part(split_part(NEW.path, '?', 1), '#', 1), 120);  -- bỏ query string/fragment
  IF jsonb_typeof(NEW.metadata) = 'object' THEN
    NEW.metadata := public.analytics_scrub_metadata(NEW.metadata);
  END IF;  -- metadata không phải object sẽ bị ck_analytics_events_metadata từ chối
  RETURN NEW;
END $$;

CREATE OR REPLACE TRIGGER trg_analytics_events_sanitize
  BEFORE INSERT OR UPDATE ON public.analytics_events
  FOR EACH ROW EXECUTE FUNCTION public.trg_analytics_events_sanitize();

-- ============================================================================
-- 6. D9 visitor_access_logs - liên kết với visitor_sessions, giữ nguyên cách ghi cũ
-- ============================================================================
ALTER TABLE public.visitor_access_logs ALTER COLUMN client_id DROP NOT NULL;  -- cho phép ON DELETE SET NULL

DO $$
BEGIN
  IF (SELECT data_type FROM information_schema.columns
       WHERE table_schema = 'public' AND table_name = 'visitor_access_logs' AND column_name = 'created_at')
     = 'timestamp without time zone' THEN
    ALTER TABLE public.visitor_access_logs ALTER COLUMN created_at DROP DEFAULT;
    ALTER TABLE public.visitor_access_logs ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'UTC';
    ALTER TABLE public.visitor_access_logs ALTER COLUMN created_at SET DEFAULT now();
  END IF;
END $$;

-- Mã cũ chỉ INSERT vào visitor_access_logs: trigger tự tạo/cập nhật visitor_sessions để FK luôn thỏa.
CREATE OR REPLACE FUNCTION public.trg_visitor_access_logs_before_insert()
RETURNS trigger LANGUAGE plpgsql SET search_path = public, pg_catalog AS $$
BEGIN
  NEW.path := left(split_part(split_part(NEW.path, '?', 1), '#', 1), 120);
  IF NEW.client_id IS NOT NULL THEN
    INSERT INTO public.visitor_sessions (client_id, started_at, last_seen_at, heartbeat_count, user_agent_short)
    VALUES (NEW.client_id, NEW.created_at, NEW.created_at, 1, left(NEW.user_agent_short, 100))
    ON CONFLICT (client_id) DO UPDATE
      SET last_seen_at = GREATEST(public.visitor_sessions.last_seen_at, EXCLUDED.last_seen_at);
  END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE TRIGGER trg_visitor_access_logs_before_insert
  BEFORE INSERT ON public.visitor_access_logs
  FOR EACH ROW EXECUTE FUNCTION public.trg_visitor_access_logs_before_insert();

-- Dựng lại session từ log cũ (chỉ thêm, không sửa/xóa).
INSERT INTO public.visitor_sessions (client_id, started_at, last_seen_at, heartbeat_count, user_agent_short)
SELECT l.client_id, min(l.created_at), max(l.created_at), 0,
       left((array_agg(l.user_agent_short ORDER BY l.created_at DESC))[1], 100)
FROM public.visitor_access_logs l
WHERE l.client_id IS NOT NULL
GROUP BY l.client_id
ON CONFLICT (client_id) DO NOTHING;

SELECT pg_temp.ensure_constraint('public.visitor_access_logs', 'fk_visitor_access_logs_session',
  'FOREIGN KEY (client_id) REFERENCES public.visitor_sessions (client_id) ON DELETE SET NULL ON UPDATE CASCADE');

CREATE INDEX IF NOT EXISTS ix_visitor_access_created_client
  ON public.visitor_access_logs (created_at, client_id);

-- ============================================================================
-- 7. Hàm phục vụ Dashboard, ghi nhận phiên và dọn dữ liệu (gọi bằng service_role)
-- ============================================================================
CREATE OR REPLACE FUNCTION public.analytics_touch_session(p_client_id text, p_user_agent text DEFAULT NULL)
RETURNS void LANGUAGE plpgsql SET search_path = public, pg_catalog AS $$
BEGIN
  IF p_client_id IS NULL OR length(p_client_id) NOT BETWEEN 1 AND 64 THEN
    RETURN;
  END IF;
  INSERT INTO public.visitor_sessions (client_id, user_agent_short)
  VALUES (p_client_id, left(p_user_agent, 100))
  ON CONFLICT (client_id) DO UPDATE
    SET last_seen_at    = GREATEST(public.visitor_sessions.last_seen_at, now()),
        heartbeat_count = public.visitor_sessions.heartbeat_count + 1;
END $$;

CREATE OR REPLACE FUNCTION public.analytics_summary(p_online_window_seconds integer DEFAULT 90)
RETURNS jsonb LANGUAGE sql STABLE SET search_path = public, pg_catalog AS $$
  SELECT jsonb_build_object(
    'online_visitors',        (SELECT count(*) FROM visitor_sessions
                                WHERE last_seen_at >= now() - make_interval(secs => p_online_window_seconds)),
    'unique_visitors_24h',    (SELECT count(*) FROM visitor_sessions WHERE last_seen_at >= now() - interval '24 hours'),
    'unique_visitors_7d',     (SELECT count(*) FROM visitor_sessions WHERE last_seen_at >= now() - interval '7 days'),
    'page_views_24h',         (SELECT count(*) FROM visitor_access_logs WHERE created_at >= now() - interval '24 hours'),
    'page_views_total',       (SELECT count(*) FROM visitor_access_logs),
    'narration_requested_24h',(SELECT count(*) FROM analytics_events
                                WHERE event_type = 'narration_requested' AND occurred_at >= now() - interval '24 hours'),
    'tts_requests_24h',       (SELECT count(*) FROM analytics_events
                                WHERE event_type IN ('narration_success', 'narration_failed')
                                  AND occurred_at >= now() - interval '24 hours'),
    'tts_success_24h',        (SELECT count(*) FROM analytics_events
                                WHERE event_type = 'narration_success' AND occurred_at >= now() - interval '24 hours'),
    'tts_fail_24h',           (SELECT count(*) FROM analytics_events
                                WHERE event_type = 'narration_failed' AND occurred_at >= now() - interval '24 hours'),
    'tts_avg_duration_ms',    (SELECT round(avg(duration_ms)::numeric, 1) FROM analytics_events
                                WHERE event_type IN ('narration_success', 'narration_failed')
                                  AND duration_ms IS NOT NULL AND occurred_at >= now() - interval '24 hours'),
    'cache_hit_24h',          (SELECT count(*) FROM analytics_events
                                WHERE event_type = 'narration_success' AND cache_hit IS TRUE
                                  AND occurred_at >= now() - interval '24 hours'),
    'errors_24h',             (SELECT count(*) FROM analytics_events
                                WHERE event_type = 'error' AND occurred_at >= now() - interval '24 hours'),
    'language_distribution_7d', COALESCE((SELECT jsonb_object_agg(language, c) FROM (
                                SELECT language, count(*) AS c FROM analytics_events
                                 WHERE event_type = 'location_activated' AND language IS NOT NULL
                                   AND occurred_at >= now() - interval '7 days'
                                 GROUP BY language) s), '{}'::jsonb),
    'as_of',                  now()
  )
$$;

CREATE OR REPLACE FUNCTION public.analytics_activity_by_day(p_days integer DEFAULT 7, p_tz text DEFAULT 'UTC')
RETURNS TABLE (day date, visitors bigint, page_views bigint, detections bigint, activations bigint)
LANGUAGE sql STABLE SET search_path = public, pg_catalog AS $$
  WITH bounds AS (
    SELECT (now() AT TIME ZONE p_tz)::date AS today
  ), days AS (
    SELECT generate_series(b.today - (GREATEST(p_days, 1) - 1), b.today, interval '1 day')::date AS d FROM bounds b
  ), pv AS (
    SELECT (created_at AT TIME ZONE p_tz)::date AS d, count(DISTINCT client_id) AS visitors, count(*) AS views
    FROM visitor_access_logs
    WHERE created_at >= now() - make_interval(days => GREATEST(p_days, 1) + 1)
    GROUP BY 1
  ), ev AS (
    SELECT (occurred_at AT TIME ZONE p_tz)::date AS d,
           count(*) FILTER (WHERE event_type = 'location_detected')  AS detections,
           count(*) FILTER (WHERE event_type = 'location_activated') AS activations
    FROM analytics_events
    WHERE event_type IN ('location_detected', 'location_activated')
      AND occurred_at >= now() - make_interval(days => GREATEST(p_days, 1) + 1)
    GROUP BY 1
  )
  SELECT days.d, COALESCE(pv.visitors, 0), COALESCE(pv.views, 0),
         COALESCE(ev.detections, 0), COALESCE(ev.activations, 0)
  FROM days LEFT JOIN pv ON pv.d = days.d LEFT JOIN ev ON ev.d = days.d
  ORDER BY days.d
$$;

CREATE OR REPLACE FUNCTION public.analytics_top_locations(p_limit integer DEFAULT 10, p_days integer DEFAULT NULL)
RETURNS TABLE (location_id integer, location_name text, request_count bigint, last_requested_at timestamptz)
LANGUAGE sql STABLE SET search_path = public, pg_catalog AS $$
  SELECT e.location_id,
         COALESCE(NULLIF(btrim(l.name), ''), l.source_title, 'ID #' || e.location_id),
         count(*), max(e.occurred_at)
  FROM analytics_events e
  JOIN locations l ON l.id = e.location_id
  WHERE e.event_type = 'location_activated'
    AND (p_days IS NULL OR e.occurred_at >= now() - make_interval(days => p_days))
  GROUP BY e.location_id, l.name, l.source_title
  ORDER BY count(*) DESC, max(e.occurred_at) DESC
  LIMIT GREATEST(p_limit, 1)
$$;

CREATE OR REPLACE FUNCTION public.analytics_recent_errors(p_limit integer DEFAULT 25)
RETURNS TABLE (id bigint, service text, request_type text, status_code integer,
               error_type text, message text, occurred_at timestamptz)
LANGUAGE sql STABLE SET search_path = public, pg_catalog AS $$
  SELECT e.id, e.service::text, e.request_type::text, e.status_code, e.error_type::text, e.message::text, e.occurred_at
  FROM analytics_events e
  WHERE e.event_type = 'error'
  ORDER BY e.occurred_at DESC
  LIMIT LEAST(GREATEST(p_limit, 1), 200)
$$;

-- Chính sách giữ dữ liệu: analytics_events và visitor_sessions mặc định 30 ngày.
-- visitor_access_logs là nhật ký lâu dài nên mặc định KHÔNG bị xóa (p_access_log_days = NULL).
CREATE OR REPLACE FUNCTION public.analytics_cleanup(p_retention_days integer DEFAULT 30,
                                                    p_access_log_days integer DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SET search_path = public, pg_catalog AS $$
DECLARE
  n_events bigint; n_sessions bigint; n_access bigint := 0;
BEGIN
  IF p_retention_days IS NULL OR p_retention_days < 1 THEN
    RAISE EXCEPTION 'p_retention_days phải >= 1';
  END IF;
  IF p_access_log_days IS NOT NULL AND p_access_log_days < 1 THEN
    RAISE EXCEPTION 'p_access_log_days phải >= 1 hoặc NULL (giữ vô thời hạn)';
  END IF;

  DELETE FROM analytics_events WHERE occurred_at < now() - make_interval(days => p_retention_days);
  GET DIAGNOSTICS n_events = ROW_COUNT;

  DELETE FROM visitor_sessions WHERE last_seen_at < now() - make_interval(days => p_retention_days);
  GET DIAGNOSTICS n_sessions = ROW_COUNT;

  IF p_access_log_days IS NOT NULL THEN
    DELETE FROM visitor_access_logs WHERE created_at < now() - make_interval(days => p_access_log_days);
    GET DIAGNOSTICS n_access = ROW_COUNT;
  END IF;

  RETURN jsonb_build_object('analytics_events', n_events, 'visitor_sessions', n_sessions,
                            'visitor_access_logs', n_access, 'ran_at', now());
END $$;

-- Dọn narration_cache hết hạn (không đụng bảng narrations lưu lâu dài).
CREATE OR REPLACE FUNCTION public.purge_expired_narration_cache(p_grace interval DEFAULT interval '0')
RETURNS integer LANGUAGE plpgsql SET search_path = public, pg_catalog AS $$
DECLARE n integer;
BEGIN
  DELETE FROM narration_cache WHERE expires_at < (now() AT TIME ZONE 'UTC') - p_grace;
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END $$;
-- ============================================================================
-- 8. Bảo mật: RLS cho cả 6 bảng, chỉ service_role được thao tác
-- ============================================================================
DO $$
DECLARE t text; s text;
BEGIN
  FOREACH t IN ARRAY ARRAY['locations', 'narrations', 'narration_cache',
                           'visitor_sessions', 'analytics_events', 'visitor_access_logs'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('REVOKE ALL ON TABLE public.%I FROM anon, authenticated', t);
    EXECUTE format('GRANT ALL ON TABLE public.%I TO service_role', t);
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                    WHERE schemaname = 'public' AND tablename = t AND policyname = 'service_role_all') THEN
      EXECUTE format('CREATE POLICY service_role_all ON public.%I AS PERMISSIVE FOR ALL TO service_role USING (true) WITH CHECK (true)', t);
    END IF;
  END LOOP;

  -- Chỉ các sequence của 6 bảng này (không đụng bảng khác trong schema public).
  FOR s IN SELECT pg_get_serial_sequence(x.tbl, x.col)
           FROM (VALUES ('public.locations', 'id'), ('public.narration_cache', 'id'),
                        ('public.visitor_access_logs', 'id'), ('public.analytics_events', 'id')) AS x(tbl, col)
  LOOP
    IF s IS NOT NULL THEN
      EXECUTE format('REVOKE ALL ON SEQUENCE %s FROM anon, authenticated', s);
      EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE %s TO service_role', s);
    END IF;
  END LOOP;
END $$;

-- Hàm mới: bỏ quyền gọi từ API công khai (PostgREST /rpc), chỉ service_role được gọi.
REVOKE ALL ON FUNCTION
  public.analytics_touch_session(text, text),
  public.analytics_summary(integer),
  public.analytics_activity_by_day(integer, text),
  public.analytics_top_locations(integer, integer),
  public.analytics_recent_errors(integer),
  public.analytics_cleanup(integer, integer),
  public.purge_expired_narration_cache(interval),
  public.analytics_sanitize_text(text),
  public.analytics_scrub_metadata(jsonb),
  public.trg_narrations_protect_version(),
  public.trg_analytics_events_sanitize(),
  public.trg_visitor_access_logs_before_insert()
FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION
  public.analytics_touch_session(text, text),
  public.analytics_summary(integer),
  public.analytics_activity_by_day(integer, text),
  public.analytics_top_locations(integer, integer),
  public.analytics_recent_errors(integer),
  public.analytics_cleanup(integer, integer),
  public.purge_expired_narration_cache(interval)
TO service_role;

-- ============================================================================
-- 9. D4 Supabase Storage: bucket narration-audio luôn private
-- ============================================================================
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES ('narration-audio', 'narration-audio', false, 2000000, ARRAY['audio/mpeg'])
ON CONFLICT (id) DO UPDATE SET public = false;   -- chỉ ép private; không đổi giới hạn/mime hiện có
-- Không tạo policy cho storage.objects: chỉ Backend (secret key) upload và ký URL.

-- ============================================================================
-- 10. Chú thích trong database
-- ============================================================================
COMMENT ON TABLE public.locations IS 'D1: địa điểm, GPS, bán kính kích hoạt, nguồn Wikimedia.';
COMMENT ON TABLE public.narrations IS 'D2: bản thuyết minh lưu lâu dài theo (địa điểm, ngôn ngữ, giọng, phiên bản). MP3 nằm ở Storage.';
COMMENT ON TABLE public.narration_cache IS 'D3: cache API cũ theo (địa điểm, ngôn ngữ), có hạn expires_at. narration_id là liên kết tùy chọn tới narrations.';
COMMENT ON TABLE public.visitor_sessions IS 'D5: mỗi trình duyệt ẩn danh một dòng. Online = last_seen_at trong 90 giây gần nhất.';
COMMENT ON TABLE public.analytics_events IS 'D5: sự kiện thống nhất theo event_type. Giữ 30 ngày (analytics_cleanup). Không chứa IP/GPS/khóa bí mật.';
COMMENT ON TABLE public.visitor_access_logs IS 'D9: nhật ký lượt mở trang lâu dài; nguồn duy nhất cho số page view (không ghi trùng vào analytics_events).';

-- Tùy chọn: lịch dọn tự động bằng pg_cron (bật extension trong Dashboard > Database > Extensions trước).
-- SELECT cron.schedule('analytics-retention', '15 3 * * *', $$SELECT public.analytics_cleanup(30, NULL)$$);
-- SELECT cron.schedule('narration-cache-purge', '30 3 * * *', $$SELECT public.purge_expired_narration_cache(interval '1 day')$$);

COMMIT;
