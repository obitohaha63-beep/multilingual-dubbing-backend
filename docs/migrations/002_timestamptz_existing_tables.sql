-- ============================================================================
-- 002_timestamptz_existing_tables.sql - Đổi cột thời gian 3 bảng cũ sang TIMESTAMPTZ
-- CHỈ CHẠY SAU KHI Prompt 2 (sửa code) đã deploy: mã hiện tại so sánh datetime KHÔNG múi giờ
-- (content-service/main.py utcnow(), narration_store.py now()), nếu chạy sớm sẽ lỗi
-- "can't compare offset-naive and offset-aware datetimes".
-- Idempotent: cột đã là TIMESTAMPTZ sẽ được bỏ qua. Dữ liệu cũ được hiểu là UTC.
-- Khóa bảng ngắn khi ghi lại cột; nên chạy lúc ít truy cập.
-- ============================================================================
BEGIN;
SET LOCAL search_path = public;
SET LOCAL lock_timeout = '15s';

-- Chuyển cột thời gian của 3 bảng cũ sang TIMESTAMPTZ (dữ liệu cũ là UTC không múi giờ).
DO $$
DECLARE c record;
BEGIN
  FOR c IN
    SELECT i.table_name, i.column_name
    FROM information_schema.columns i
    WHERE i.table_schema = 'public' AND i.data_type = 'timestamp without time zone'
      AND (i.table_name, i.column_name) IN (
        ('locations', 'last_synced_at'),
        ('narrations', 'lease_until'), ('narrations', 'retry_at'),
        ('narrations', 'created_at'),  ('narrations', 'updated_at'),
        ('narration_cache', 'created_at'), ('narration_cache', 'updated_at'), ('narration_cache', 'expires_at'))
  LOOP
    EXECUTE format('ALTER TABLE public.%I ALTER COLUMN %I TYPE TIMESTAMPTZ USING %I AT TIME ZONE ''UTC''',
                   c.table_name, c.column_name, c.column_name);
  END LOOP;
END $$;

ALTER TABLE public.narrations      ALTER COLUMN created_at SET DEFAULT now();
ALTER TABLE public.narrations      ALTER COLUMN updated_at SET DEFAULT now();
ALTER TABLE public.narration_cache ALTER COLUMN created_at SET DEFAULT now();
ALTER TABLE public.narration_cache ALTER COLUMN updated_at SET DEFAULT now();

CREATE OR REPLACE FUNCTION public.purge_expired_narration_cache(p_grace interval DEFAULT interval '0')
RETURNS integer LANGUAGE plpgsql SET search_path = public, pg_catalog AS $$
DECLARE n integer;
BEGIN
  DELETE FROM narration_cache WHERE expires_at < now() - p_grace;
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END $$;
REVOKE ALL ON FUNCTION public.purge_expired_narration_cache(interval) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.purge_expired_narration_cache(interval) TO service_role;

COMMIT;
