BEGIN;

SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '15s';

INSERT INTO identity.venues(
    venue_id,venue_kind,display_name,status,metadata)
VALUES(
    'venue-bybit','CEX','Bybit','ACTIVE',
    '{"provider":"BYBIT","identity_revision":"R2"}'::jsonb)
ON CONFLICT(venue_id) DO NOTHING;

INSERT INTO identity.market_sources(
    market_source_id,venue_id,source_kind,provider_ref,environment,market_scope,
    status,provenance)
VALUES(
    'ms-bybit-mainnet-public-v1',
    'venue-bybit',
    'DIRECT_PUBLIC_COMPOSITE',
    'BYBIT_PUBLIC_V5',
    'MAINNET',
    'SPOT+LINEAR',
    'ACTIVE',
    '{"bootstrap":"current MAYAK direct Bybit public observation","identity_revision":"R2"}'::jsonb)
ON CONFLICT(market_source_id) DO NOTHING;

INSERT INTO identity.trading_accounts(
    trading_account_id,venue_id,external_account_ref,parent_trading_account_id,
    account_kind,account_type,environment,status,metadata)
VALUES(
    'ta-2122c81ecff14f3b85b102db605212f7',
    'venue-bybit',
    NULL,
    NULL,
    'MAIN',
    'UNIFIED',
    'MAINNET',
    'ACTIVE',
    '{"legacy_account_ref":"BYBIT:UNIFIED","bootstrap_basis":"single current mainnet private account","identity_revision":"R2"}'::jsonb)
ON CONFLICT(trading_account_id) DO NOTHING;

INSERT INTO identity.account_connectors(
    account_connector_id,trading_account_id,connector_kind,adapter_id,credential_ref,
    status,config_fingerprint,metadata)
VALUES(
    'ac-ed73de20410548d6841aeafa5f196f70',
    'ta-2122c81ecff14f3b85b102db605212f7',
    'CEX_PRIVATE_RUNTIME',
    'BYBIT_V5_PRIVATE',
    'systemd-credential:bybit-mainnet',
    'ACTIVE',
    NULL,
    '{"service":"cripta-private-runtime.service","identity_revision":"R2"}'::jsonb)
ON CONFLICT(account_connector_id) DO NOTHING;

COMMIT;
