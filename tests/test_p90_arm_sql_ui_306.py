from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "operations/sql/20261029_p90l90_real_account_arm_control.sql").read_text()
HTML = (ROOT / "operations/dashboard/index.html").read_text()


def _arm_sql():
    return SQL.split( 
        "CREATE OR REPLACE FUNCTION control.arm_p90l90_real_v3_account_scope(", 1 
    )[1].split(
        "CREATE OR REPLACE FUNCTION control.disarm_p90l90_real_v3_account_scopes(", 1
    )[0]


def test_account_arm_preserves_serializable_identity_without_broad_update_grant():
    sql = _arm_sql()
    assert "SECURITY DEFINER" in sql
    assert "transaction_isolation') <> 'serializable'" in sql
    assert "FOR UPDATE OF b,a;" in sql
    assert "FOR UPDATE OF b,a,ta;" not in sql
    assert "FOR SHARE;" not in sql
    assert "ta.status='ACTIVE' AND ta.environment='MAINNET'" in sql
    assert "ta.venue_id='venue-bybit' AND ta.account_type='UNIFIED'" in sql
    assert "c.status='ACTIVE' AND c.retired_at IS NULL" in sql
    assert "s.release_commit=p_release_commit" in sql
    assert "s.state='ACTIVE'" in sql
    assert "INSERT INTO control.account_live_arm_sessions(" in sql
    assert "GRANT UPDATE ON identity.trading_accounts" not in SQL
    assert "GRANT UPDATE ON identity.account_connectors" not in SQL


def test_p90_failure_display_is_copyable_persistent_read_only():
    assert 'id="p90ArmResultPanel"' in HTML
    assert 'id="p90ArmResultText"' in HTML
    assert 'id="p90ArmCopyResult"' in HTML
    assert "sessionStorage.setItem(p90ArmHistoryKey" in HTML
    assert "recordP90ArmResult('error',e.message,requestId)" in HTML
    assert "navigator.clipboard.writeText(text)" in HTML
    assert "try{await changeP90RealV3Arm(true)}catch(e){alert(e.message)}" not in HTML
    assert "try{await changeP90RealV3Arm(false)}catch(e){alert(e.message)}" not in HTML
