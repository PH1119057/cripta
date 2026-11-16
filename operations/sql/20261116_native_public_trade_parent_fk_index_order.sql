-- #642: make the existing source-event UNIQUE index serve the parent FK lookup.
--
-- Final storage remains one five-column UNIQUE index. Only the key order changes:
-- parent native identity first, source_session_id last. This preserves the exact
-- uniqueness set and ON CONFLICT inference while allowing PostgreSQL to enforce
-- source_events -> native_public_trade_events without a full source table scan
-- during bounded parent cleanup.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '0';

DO $native_parent_index$
DECLARE
    constraint_name constant text :=
        'native_public_trade_source_ev_source_session_id_market_sour_key';
    replacement_name constant text :=
        'native_public_trade_source_events_parent_owner_ux';
    actual_columns text[];
    expected_old constant text[] := ARRAY[
        'source_session_id',
        'market_source_id',
        'instrument_id',
        'exchange_sequence',
        'exec_id'
    ];
    expected_new constant text[] := ARRAY[
        'market_source_id',
        'instrument_id',
        'exchange_sequence',
        'exec_id',
        'source_session_id'
    ];
BEGIN
    SELECT array_agg(a.attname ORDER BY k.ord)
      INTO actual_columns
      FROM pg_constraint c
      CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY
        AS k(attnum,ord)
      JOIN pg_attribute a
        ON a.attrelid=c.conrelid
       AND a.attnum=k.attnum
     WHERE c.conrelid=
           'analytics.native_public_trade_source_events'::regclass
       AND c.conname=constraint_name
       AND c.contype='u'
     GROUP BY c.oid;

    IF actual_columns = expected_new THEN
        RETURN;
    END IF;
    IF actual_columns IS DISTINCT FROM expected_old THEN
        RAISE EXCEPTION
            'NATIVE_SOURCE_EVENT_PARENT_UNIQUE_SHAPE_DRIFT actual=%',
            actual_columns;
    END IF;

    IF to_regclass('analytics.' || replacement_name) IS NOT NULL THEN
        RAISE EXCEPTION
            'NATIVE_SOURCE_EVENT_PARENT_REPLACEMENT_INDEX_ALREADY_EXISTS';
    END IF;

    EXECUTE format(
        'CREATE UNIQUE INDEX %I ON analytics.native_public_trade_source_events '
        || '(market_source_id,instrument_id,exchange_sequence,exec_id,'
        || 'source_session_id)',
        replacement_name
    );

    EXECUTE format(
        'ALTER TABLE analytics.native_public_trade_source_events '
        || 'DROP CONSTRAINT %I',
        constraint_name
    );
    EXECUTE format(
        'ALTER TABLE analytics.native_public_trade_source_events '
        || 'ADD CONSTRAINT %I UNIQUE USING INDEX %I',
        constraint_name,
        replacement_name
    );

    SELECT array_agg(a.attname ORDER BY k.ord)
      INTO actual_columns
      FROM pg_constraint c
      CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY
        AS k(attnum,ord)
      JOIN pg_attribute a
        ON a.attrelid=c.conrelid
       AND a.attnum=k.attnum
     WHERE c.conrelid=
           'analytics.native_public_trade_source_events'::regclass
       AND c.conname=constraint_name
       AND c.contype='u'
     GROUP BY c.oid;

    IF actual_columns IS DISTINCT FROM expected_new THEN
        RAISE EXCEPTION
            'NATIVE_SOURCE_EVENT_PARENT_UNIQUE_REORDER_FAILED actual=%',
            actual_columns;
    END IF;
END
$native_parent_index$;

DO $native_parent_fk_index_verify$
DECLARE
    fk_columns text[];
    unique_columns text[];
BEGIN
    SELECT array_agg(a.attname ORDER BY k.ord)
      INTO fk_columns
      FROM pg_constraint c
      CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY
        AS k(attnum,ord)
      JOIN pg_attribute a
        ON a.attrelid=c.conrelid
       AND a.attnum=k.attnum
     WHERE c.conrelid=
           'analytics.native_public_trade_source_events'::regclass
       AND c.confrelid=
           'analytics.native_public_trade_events'::regclass
       AND c.contype='f'
       AND c.convalidated
       AND NOT c.condeferrable
     GROUP BY c.oid;

    SELECT array_agg(a.attname ORDER BY k.ord)
      INTO unique_columns
      FROM pg_constraint c
      CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY
        AS k(attnum,ord)
      JOIN pg_attribute a
        ON a.attrelid=c.conrelid
       AND a.attnum=k.attnum
     WHERE c.conrelid=
           'analytics.native_public_trade_source_events'::regclass
       AND c.conname=
           'native_public_trade_source_ev_source_session_id_market_sour_key'
       AND c.contype='u'
     GROUP BY c.oid;

    IF fk_columns IS DISTINCT FROM ARRAY[
        'market_source_id','instrument_id','exchange_sequence','exec_id'
    ]::text[] THEN
        RAISE EXCEPTION
            'NATIVE_SOURCE_EVENT_PARENT_FK_SHAPE_DRIFT actual=%',
            fk_columns;
    END IF;
    IF unique_columns IS DISTINCT FROM ARRAY[
        'market_source_id','instrument_id','exchange_sequence','exec_id',
        'source_session_id'
    ]::text[] THEN
        RAISE EXCEPTION
            'NATIVE_SOURCE_EVENT_PARENT_UNIQUE_VERIFY_FAILED actual=%',
            unique_columns;
    END IF;
END
$native_parent_fk_index_verify$;

COMMIT;
