-- ============================================================================
-- 001_verify.sql - Kiểm tra SAU khi chạy 001 (và 002 nếu đã chạy). CHỈ ĐỌC, không sửa dữ liệu.
-- Kết quả là MỘT bảng: cột status phải là OK. Dòng CHECK cần xem lại (xem ghi chú cuối file).
-- ============================================================================
WITH t(name) AS (VALUES ('locations'), ('narrations'), ('narration_cache'),
                        ('visitor_sessions'), ('analytics_events'), ('visitor_access_logs')),
checks(ord, check_name, actual, expected) AS (
  SELECT 1, 'so_bang_trong_6_bang', (SELECT count(*) FROM t JOIN pg_tables p ON p.schemaname = 'public' AND p.tablename = t.name), 6
  UNION ALL SELECT 2, 'rls_bat_tren_6_bang', (SELECT count(*) FROM t JOIN pg_tables p ON p.schemaname = 'public' AND p.tablename = t.name AND p.rowsecurity), 6
  UNION ALL SELECT 3, 'policy_service_role_all', (SELECT count(*) FROM pg_policies WHERE schemaname = 'public' AND policyname = 'service_role_all' AND tablename IN (SELECT name FROM t)), 6
  UNION ALL SELECT 4, 'quyen_bang_cua_anon_authenticated', (SELECT count(*) FROM information_schema.role_table_grants g
        WHERE g.table_schema = 'public' AND g.table_name IN (SELECT name FROM t) AND g.grantee IN ('anon', 'authenticated')), 0
  UNION ALL SELECT 5, 'anon_goi_duoc_ham_analytics', (SELECT count(*) FROM (VALUES
        ('public.analytics_summary(integer)'), ('public.analytics_cleanup(integer,integer)'),
        ('public.analytics_touch_session(text,text)'), ('public.purge_expired_narration_cache(interval)')) f(sig)
        WHERE has_function_privilege('anon', f.sig, 'EXECUTE') OR has_function_privilege('authenticated', f.sig, 'EXECUTE')), 0
  UNION ALL SELECT 6, 'bucket_narration_audio_private', (SELECT count(*) FROM storage.buckets WHERE id = 'narration-audio' AND public = false), 1
  UNION ALL SELECT 7, 'policy_storage_objects_cho_narration_audio', (SELECT count(*) FROM pg_policies
        WHERE schemaname = 'storage' AND tablename = 'objects'
          AND (COALESCE(qual, '') ILIKE '%narration-audio%' OR COALESCE(with_check, '') ILIKE '%narration-audio%')), 0
  UNION ALL SELECT 8, 'access_log_mo_coi_khong_co_session', (SELECT count(*) FROM visitor_access_logs l
        WHERE l.client_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM visitor_sessions s WHERE s.client_id = l.client_id)), 0
  -- Dòng cũ vi phạm ràng buộc NOT VALID (phải = 0 trước khi VALIDATE)
  UNION ALL SELECT 20, 'vi_pham_locations_name_trong', (SELECT count(*) FROM locations WHERE btrim(name) = ''), 0
  UNION ALL SELECT 21, 'vi_pham_locations_toa_do', (SELECT count(*) FROM locations
        WHERE latitude NOT BETWEEN -90 AND 90 OR longitude NOT BETWEEN -180 AND 180 OR (latitude IS NULL) <> (longitude IS NULL)), 0
  UNION ALL SELECT 22, 'vi_pham_locations_radius', (SELECT count(*) FROM locations WHERE radius <= 0), 0
  UNION ALL SELECT 23, 'vi_pham_locations_wikimedia_thieu_khoa', (SELECT count(*) FROM locations
        WHERE source = 'wikimedia' AND (source_lang IS NULL OR source_page_id IS NULL OR latitude IS NULL OR longitude IS NULL)), 0
  UNION ALL SELECT 24, 'vi_pham_narrations_attempts', (SELECT count(*) FROM narrations WHERE attempts < 0), 0
  UNION ALL SELECT 25, 'vi_pham_narrations_processing_thieu_lease', (SELECT count(*) FROM narrations
        WHERE status = 'processing' AND (lease_token IS NULL OR lease_until IS NULL)), 0
  UNION ALL SELECT 26, 'vi_pham_narrations_language', (SELECT count(*) FROM narrations WHERE language !~ '^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$'), 0
  UNION ALL SELECT 27, 'vi_pham_cache_audio_status', (SELECT count(*) FROM narration_cache WHERE audio_status NOT IN ('text_ready', 'audio_ready')), 0
  UNION ALL SELECT 28, 'vi_pham_cache_audio_ready_thieu_du_lieu', (SELECT count(*) FROM narration_cache
        WHERE audio_status = 'audio_ready' AND (audio_filename IS NULL OR voice IS NULL OR text_hash IS NULL)), 0
  UNION ALL SELECT 29, 'vi_pham_cache_language', (SELECT count(*) FROM narration_cache
        WHERE requested_lang !~ '^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$' OR lang !~ '^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$'), 0
  -- Thông tin: số ràng buộc đang ở trạng thái NOT VALID (về 0 sau khi VALIDATE)
  UNION ALL SELECT 40, 'so_rang_buoc_chua_validate (thong tin)', (SELECT count(*) FROM pg_constraint c
        JOIN pg_class r ON r.oid = c.conrelid JOIN pg_namespace n ON n.oid = r.relnamespace
        WHERE n.nspname = 'public' AND r.relname IN (SELECT name FROM t) AND NOT c.convalidated), -1
)
SELECT check_name, actual, expected,
       CASE WHEN expected = -1 THEN 'INFO' WHEN actual = expected THEN 'OK' ELSE 'CHECK' END AS status
FROM checks ORDER BY ord;

-- ----------------------------------------------------------------------------
-- Dữ liệu hiện có (chạy riêng nếu muốn đối chiếu số dòng trước/sau migration):
-- SELECT 'locations' t, count(*) FROM locations UNION ALL SELECT 'narrations', count(*) FROM narrations
-- UNION ALL SELECT 'narration_cache', count(*) FROM narration_cache UNION ALL SELECT 'visitor_access_logs', count(*) FROM visitor_access_logs
-- UNION ALL SELECT 'visitor_sessions', count(*) FROM visitor_sessions UNION ALL SELECT 'analytics_events', count(*) FROM analytics_events;
--
-- Khi các dòng vi_pham_* đều = 0, có thể xác nhận ràng buộc (không khóa ghi lâu):
-- ALTER TABLE public.locations VALIDATE CONSTRAINT ck_locations_name_not_blank;
-- ALTER TABLE public.locations VALIDATE CONSTRAINT ck_locations_lat_range;
-- ALTER TABLE public.locations VALIDATE CONSTRAINT ck_locations_lon_range;
-- ALTER TABLE public.locations VALIDATE CONSTRAINT ck_locations_gps_pair;
-- ALTER TABLE public.locations VALIDATE CONSTRAINT ck_locations_radius_positive;
-- ALTER TABLE public.locations VALIDATE CONSTRAINT ck_locations_wikimedia_complete;
-- ALTER TABLE public.narrations VALIDATE CONSTRAINT ck_narrations_attempts_nonneg;
-- ALTER TABLE public.narrations VALIDATE CONSTRAINT ck_narrations_processing_lease;
-- ALTER TABLE public.narrations VALIDATE CONSTRAINT ck_narrations_language_fmt;
-- ALTER TABLE public.narration_cache VALIDATE CONSTRAINT ck_narration_cache_audio_status;
-- ALTER TABLE public.narration_cache VALIDATE CONSTRAINT ck_narration_cache_audio_complete;
-- ALTER TABLE public.narration_cache VALIDATE CONSTRAINT ck_narration_cache_lang_fmt;
