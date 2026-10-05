from pathlib import Path

APP = Path("operations/dashboard/app.py").read_text(encoding="utf-8")
UI = Path("operations/dashboard/index.html").read_text(encoding="utf-8")
PRIVATE = Path("operations/connectivity/private_runtime.py").read_text(encoding="utf-8")


def test_owner_controlled_live_test_is_visible_on_open_trades_page() -> None:
    assert 'id="ownerTestEntrySection"' in UI
    assert 'id="ownerTestSymbol"' in UI
    assert '<option selected>LTCUSDT</option>' in UI
    assert 'id="ownerTestDirection"' in UI
    assert '10 USDT' in UI
    assert 'SL / TP' in UI
    assert '−0,5% / +0,5%' in UI
    assert 'Открыть контрольную сделку' in UI
    assert 'open:[strategyTradeControlSection,ownerTestEntrySection,openTradesSection]' in UI


def test_owner_controlled_live_test_requires_explicit_owner_confirmation() -> None:
    assert "type:'owner_test_entry'" in UI
    assert "confirmed:true" in UI
    assert 'if request.get("confirmed") is not True:' in APP
    assert '"контрольный реальный вход не подтверждён"' in APP


def test_owner_controlled_live_test_is_fixed_small_market_entry() -> None:
    assert 'stake != Decimal("10")' in APP
    assert 'leverage != 1' in APP
    assert 'stop_loss_pct != Decimal("0.5")' in APP
    assert 'take_profit_pct != Decimal("0.5")' in APP
    assert 'command_type = "entry"' in APP
    assert '"entry_offset_pct": "0"' in APP
    assert '"source": "owner_controlled_live_test"' in APP
    assert '"side": "Buy" if direction == "LONG" else "Sell"' in APP


def test_owner_controlled_live_test_requires_current_active_micro_live_symbol() -> None:
    assert "FROM control.live_arm_sessions" in APP
    assert "state='ACTIVE' AND symbol=%s" in APP
    assert "release_commit=%s" in APP
    assert "current active MICRO_LIVE cohort" in APP


def test_owner_controlled_live_test_does_not_forge_strategy_signal() -> None:
    block = APP.split('if kind == "owner_test_entry":', 1)[1].split(
        'elif kind == "trailing_stop":', 1
    )[0]
    assert "strategy_signals" not in block
    assert "entry_decisions" not in block
    assert "execution_requests" not in block


def test_market_entry_reanchor_accepts_not_modified_only_after_exchange_verification() -> None:
    block = PRIVATE.split('elif kind == "entry":', 1)[1].split(
        'else: raise RuntimeError("unknown command type")', 1
    )[0]
    assert "entry protection not-modified could not be verified" in block
    assert "entry protection not-modified verification mismatch" in block
    assert '"verifiedFromExchange": True' in block
