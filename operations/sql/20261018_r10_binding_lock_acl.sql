-- R10: row-lock read via narrow definer; runtime role keeps SELECT-only table ACL.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '15s';

CREATE OR REPLACE FUNCTION strategy_entry.lock_execution_binding_for_admission(p_binding_id text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
  IF p_binding_id IS NULL OR btrim(p_binding_id) = '' THEN
    RAISE EXCEPTION 'binding identity required' USING ERRCODE = '22023';
  END IF;
  PERFORM 1
    FROM strategy_entry.strategy_execution_bindings
   WHERE strategy_execution_binding_id = p_binding_id
   FOR SHARE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'binding not found' USING ERRCODE = 'P0002';
  END IF;
END;
$$;
REVOKE ALL ON FUNCTION strategy_entry.lock_execution_binding_for_admission(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION strategy_entry.lock_execution_binding_for_admission(text) TO cripta;
COMMIT;
