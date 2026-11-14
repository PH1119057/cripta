import argparse
import asyncio
import io
import json
import sys
import threading
import uuid
from collections.abc import Callable
from contextlib import redirect_stdout
from dataclasses import asdict, is_dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from queue import Empty, SimpleQueue
from typing import Any

from bybit_workbench.app.config import MAINNET_KZ_REST_URL, AppSettings
from bybit_workbench.app.endpoint_preferences import (
    MainnetEndpointPreference,
    normalize_mainnet_endpoint,
    persistent_mainnet_endpoint,
)
from bybit_workbench.app.state_machine import AppStateMachine
from bybit_workbench.app.windows_time import WindowsTimeSyncResult, resync_windows_time
from bybit_workbench.domain.intents import EnterIntent
from bybit_workbench.domain.models import Candle
from bybit_workbench.domain.types import AppMode, AppState, OrderType, PositionSide
from bybit_workbench.exchange.fake import FakeExchange
from bybit_workbench.historical import HistoricalEligibilityGate
from bybit_workbench.persistence import EventJournal, ReconciliationService, TradingJournal
from bybit_workbench.replay import ProtectionPlan, ReplayConfig, ReplayEngine
from bybit_workbench.risk import RiskContext, RiskEngine, RiskProfile
from bybit_workbench.strategies import ArmedStrategy, default_strategy_registry


async def headless_smoke(settings: AppSettings) -> int:
    journal = EventJournal(settings.database_path)
    audit = TradingJournal(settings.database_path)

    def record_transition(event: Any) -> None:
        journal.append(
            "app.state_transition",
            f"{event.previous.value} -> {event.current.value}",
            details={"reason": event.reason},
        )

    machine = AppStateMachine(record_transition)
    exchange = FakeExchange()
    run_id = f"smoke-run-{uuid.uuid4().hex}"
    intent_id = f"smoke-{uuid.uuid4().hex[:20]}"
    decision_id = f"decision-{uuid.uuid4().hex}"
    try:
        audit.save_settings_version(
            settings.mode.value,
            {
                "mode": settings.mode,
                "database_path": settings.database_path,
                "allow_live_trading": settings.allow_live_trading,
                "enable_testnet_execution": settings.enable_testnet_execution,
            },
        )
        machine.transition(AppState.SYNCING, "headless smoke started")
        await exchange.connect()
        candle = exchange.next_candle()
        machine.transition(AppState.READY, "fake exchange synchronized")
        journal.append(
            "market.candle_closed",
            f"{candle.symbol} closed at {candle.close}",
            details={"timeframe": candle.timeframe, "closed_at": candle.closed_at},
        )
        audit.start_strategy_run(
            run_id,
            strategy_id="manual_protected_trade",
            strategy_version="1.0",
            code_version=__version_for_audit(),
            mode=settings.mode.value,
            symbol=candle.symbol,
            parameters={"source": "headless_smoke", "timeframe": candle.timeframe},
        )
        audit.record_strategy_decision(
            decision_id,
            run_id,
            inputs={
                "symbol": candle.symbol,
                "timeframe": candle.timeframe,
                "closed_at": candle.closed_at,
                "close": candle.close,
            },
            decision={"action": "manual_enter", "reason": "offline smoke"},
            candle_at=candle.closed_at,
        )
        intent = EnterIntent(
            intent_id=intent_id,
            symbol=candle.symbol,
            direction=PositionSide.LONG,
            order_type=OrderType.MARKET,
            entry_price=candle.close,
            stop_price=candle.close - Decimal("1000"),
            leverage=Decimal("2"),
            reason="offline protected-path smoke",
        )
        audit.record_trade_intent(intent, run_id, decision_id=decision_id)
        profile = RiskProfile(
            max_risk_amount=Decimal("25"),
            max_risk_percent=Decimal("0.5"),
            max_position_notional=Decimal("1000"),
            max_leverage=Decimal("2"),
            max_daily_loss=Decimal("100"),
            max_consecutive_losses=3,
            max_open_positions=1,
            max_pending_entries=1,
            max_slippage_percent=Decimal("0.1"),
            estimated_fee_rate=Decimal("0.0006"),
            max_market_data_age_seconds=Decimal("5"),
            max_private_stream_age_seconds=Decimal("10"),
            allowed_symbols=frozenset({candle.symbol}),
            allowed_directions=frozenset({PositionSide.LONG}),
        )
        observed_at = datetime.now(UTC)
        risk_context = RiskContext(
            equity=Decimal("10000"),
            available_balance=Decimal("1000"),
            daily_realized_pnl=Decimal("0"),
            consecutive_losses=0,
            open_positions=0,
            pending_entries=0,
            market_data_at=observed_at,
            private_stream_at=observed_at,
            evaluated_at=observed_at,
        )
        rules = await exchange.instrument_rules(candle.symbol)
        decision = RiskEngine().evaluate_entry(intent, profile, risk_context, rules)
        audit.record_risk_decision(
            f"risk-{uuid.uuid4().hex}",
            intent.intent_id,
            decision,
        )
        journal.append(
            "risk.entry_decision",
            "approved" if decision.approved else "rejected",
            details={
                "intent_id": intent.intent_id,
                "checks": [
                    {"code": item.code, "passed": item.passed, "detail": item.detail}
                    for item in decision.checks
                ],
                "estimated_loss": decision.estimated_loss_at_stop,
            },
        )
        if not decision.approved or decision.normalized_order is None:
            machine.transition(AppState.ERROR, "offline risk gate rejected smoke intent")
            audit.finish_strategy_run(run_id, "REJECTED")
            return 1
        protection = ProtectionPlan(
            decision.normalized_stop or intent.stop_price,
            candle.close + Decimal("1000"),
        )
        machine.transition(AppState.ARMED, "manual smoke intent validated")
        machine.transition(AppState.RUNNING, "offline execution started")
        replay = ReplayEngine(
            candle.symbol,
            ReplayConfig(
                fee_rate=profile.estimated_fee_rate,
                slippage_percent=profile.max_slippage_percent,
                seed=0,
            ),
        )
        order = replay.submit_entry(decision.normalized_order, protection)
        audit.upsert_order(
            order,
            intent_id=intent.intent_id,
            event_id=f"replay-submit-{uuid.uuid4().hex}",
        )
        journal.append(
            "execution.order_submitted",
            order.status.value,
            details={
                "order_id": order.order_id,
                "client_order_id": order.request.client_order_id,
                "quantity": order.request.quantity,
            },
        )
        journal.append(
            "protection.level_requested",
            f"stop={protection.stop_price}",
            details={"status": "requested", "take_profit": protection.take_profit},
        )
        first_open = candle.closed_at
        entry_bar = Candle(
            symbol=candle.symbol,
            timeframe=candle.timeframe,
            opened_at=first_open,
            closed_at=first_open + timedelta(minutes=1),
            open=candle.close,
            high=candle.close + Decimal("100"),
            low=candle.close - Decimal("100"),
            close=candle.close + Decimal("50"),
            volume=Decimal("2"),
        )
        entry_fills = replay.on_candle(entry_bar)
        audit.upsert_order(
            order,
            intent_id=intent.intent_id,
            event_id=f"replay-entry-{uuid.uuid4().hex}",
        )
        for fill in entry_fills:
            audit.record_replay_fill(
                fill,
                order_id=order.order_id,
                symbol=candle.symbol,
            )
            journal.append(
                "execution.fill",
                fill.reason.value,
                details={"execution_id": fill.execution_id, "price": fill.price},
            )
        audit.record_position_snapshot(
            replay.position,
            source="replay",
            run_id=run_id,
            observed_at=entry_bar.closed_at,
        )
        audit.record_stop_update(
            run_id=run_id,
            intent_id=intent.intent_id,
            order_id=order.order_id,
            symbol=candle.symbol,
            status="confirmed",
            price=protection.stop_price,
            protected_quantity=replay.protected_quantity,
            reason="entry execution protected in replay",
            occurred_at=entry_bar.closed_at,
        )
        journal.append(
            "protection.level_confirmed",
            f"stop={protection.stop_price}",
            details={"status": "confirmed", "quantity": replay.protected_quantity},
        )
        audit.save_engine_snapshot(run_id, "replay", replay.snapshot())
        stored_snapshot = audit.load_current_engine_snapshot(run_id, "replay")
        if stored_snapshot is None:
            machine.transition(AppState.ERROR, "replay snapshot was not persisted")
            audit.finish_strategy_run(run_id, "ERROR")
            return 1
        replay = ReplayEngine.restore(json.loads(json.dumps(stored_snapshot)))
        exit_bar = Candle(
            symbol=candle.symbol,
            timeframe=candle.timeframe,
            opened_at=entry_bar.closed_at,
            closed_at=entry_bar.closed_at + timedelta(minutes=1),
            open=candle.close + Decimal("500"),
            high=candle.close + Decimal("1200"),
            low=candle.close,
            close=candle.close + Decimal("1100"),
            volume=Decimal("3"),
        )
        for fill in replay.on_candle(exit_bar):
            audit.record_replay_fill(
                fill,
                order_id=order.order_id,
                symbol=candle.symbol,
            )
            journal.append(
                "execution.fill",
                fill.reason.value,
                details={"execution_id": fill.execution_id, "price": fill.price},
            )
        trade = replay.completed_trades[-1]
        audit.record_position_snapshot(
            replay.position,
            source="replay",
            run_id=run_id,
            observed_at=exit_bar.closed_at,
        )
        audit.save_engine_snapshot(run_id, "replay", replay.snapshot())
        audit.finish_strategy_run(run_id, "COMPLETED", ended_at=exit_bar.closed_at)
        reconciliation = ReconciliationService(audit).run(
            f"reconcile-{uuid.uuid4().hex}",
            candle.symbol,
            replay.position,
            [],
            run_id=run_id,
            occurred_at=exit_bar.closed_at,
        )
        if not reconciliation.synchronized:
            machine.transition(AppState.ERROR, "offline reconciliation mismatch")
            return 1
        journal.append(
            "replay.trade_completed",
            trade.exit_reason.value,
            details={
                "gross_pnl": trade.gross_pnl,
                "fees": trade.fees,
                "funding": trade.funding,
                "net_pnl": trade.net_pnl,
            },
        )
        machine.transition(
            AppState.PAUSED,
            "offline replay trade completed; manual restart required",
        )
        print(
            f"mode={settings.mode.value} state={machine.state.value} "
            f"symbol={candle.symbol} close={candle.close} qty={order.request.quantity} "
            f"stop={protection.stop_price} exit={trade.exit_reason.value} "
            f"net_pnl={trade.net_pnl} audit=complete reconciled=true "
            f"live_allowed={settings.allow_live_trading}"
        )
        return 0
    finally:
        await exchange.disconnect()
        audit.close()
        journal.close()


def gui_smoke(settings: AppSettings) -> int:
    if settings.mode is not AppMode.REPLAY:
        print("GUI SMOKE BLOCKED: Replay profile is required", file=sys.stderr)
        return 6
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("GUI SMOKE FAILED: PySide6 is not installed", file=sys.stderr)
        return 6

    from bybit_workbench.ui.main_window import create_main_window

    app = QApplication.instance() or QApplication([])
    exchange = FakeExchange()
    window = None
    try:
        asyncio.run(exchange.connect())
        machine = AppStateMachine()
        machine.transition(AppState.SYNCING, "packaged GUI smoke")
        machine.transition(AppState.READY, "packaged GUI smoke ready")
        window = create_main_window(settings, machine, exchange)
        window.show()
        app.processEvents()
        rendered = window.grab()
        checks = (
            (not rendered.isNull(), "window did not render"),
            (window.windowTitle() == "Bybit Strategy Workbench", "unexpected window title"),
            (not window.run_button.isEnabled(), "Run must be disabled in Replay smoke"),
            (
                not window.credentials_button.isEnabled(),
                "credentials control must be disabled in Replay smoke",
            ),
            (
                window.execution_badge.text() == "SHADOW · DISARMED",
                "execution badge is not fail-closed",
            ),
        )
        failures = [detail for passed, detail in checks if not passed]
        if failures:
            print("GUI SMOKE FAILED: " + "; ".join(failures), file=sys.stderr)
            return 6
        print("GUI SMOKE PASSED: replay window rendered; execution remains SHADOW · DISARMED")
        return 0
    finally:
        if window is not None:
            window.close()
            app.processEvents()
        asyncio.run(exchange.disconnect())


def __version_for_audit() -> str:
    from bybit_workbench import __version__

    return __version__


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bybit Strategy Workbench")
    parser.add_argument("--headless", action="store_true", help="run offline smoke scenario")
    parser.add_argument(
        "--gui-smoke",
        action="store_true",
        help="construct, render, safety-check and close the Replay GUI",
    )
    parser.add_argument("--database", type=Path, help="override SQLite journal path")
    parser.add_argument(
        "--mainnet-acceptance",
        action="store_true",
        help="run the pass-7 Mainnet GET-only acceptance and write a redacted report",
    )
    parser.add_argument(
        "--acceptance-report",
        type=Path,
        default=Path("var/mainnet_acceptance.json"),
        help="redacted JSON output path for --mainnet-acceptance",
    )
    parser.add_argument("--inspect-history", type=Path, help="validate and inspect OHLCV CSV")
    parser.add_argument("--backtest", type=Path, help="run a research backtest from OHLCV CSV")
    parser.add_argument(
        "--rerun-report",
        type=Path,
        help="rerun a backtest manifest and require the same dataset fingerprint",
    )
    parser.add_argument(
        "--strategy",
        default="user_algorithm_1",
        help="registered strategy id for --backtest",
    )
    parser.add_argument(
        "--parameters",
        default="{}",
        help="strategy parameters as a JSON object; decimal values should be strings",
    )
    parser.add_argument("--mark-history", type=Path, help="aligned Mark Price OHLC CSV")
    parser.add_argument("--funding-history", type=Path, help="funding event CSV")
    parser.add_argument("--report-json", type=Path, help="write complete backtest report JSON")
    parser.add_argument("--trades-csv", type=Path, help="write completed trades CSV")
    parser.add_argument("--initial-equity", default="10000")
    parser.add_argument("--available-balance", default="10000")
    parser.add_argument("--maker-fee-rate", default="0.0002")
    parser.add_argument("--taker-fee-rate", default="0.00055")
    parser.add_argument("--slippage-percent", default="0.1")
    parser.add_argument(
        "--instrument-rules",
        help=(
            "exact InstrumentRules JSON snapshot for the selected symbol; required for BackTest"
        ),
    )
    parser.add_argument(
        "--eligibility",
        action="store_true",
        help=(
            "run production-equivalent temporal validation and persist the exact Micro-Live gate"
        ),
    )
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--walk-forward-training-bars", type=int)
    parser.add_argument("--walk-forward-test-bars", type=int)
    parser.add_argument(
        "--stress-suite",
        action="store_true",
        help="add predefined base/adverse execution stress runs to the report",
    )
    parser.add_argument("--sensitivity-parameter", help="one parameter to vary explicitly")
    parser.add_argument(
        "--sensitivity-values",
        help="JSON array of explicit neighboring values for --sensitivity-parameter",
    )
    parser.add_argument(
        "--strict-market-data",
        action="store_true",
        help="fail unless aligned Mark Price and funding series are explicitly complete",
    )
    parser.add_argument("--symbol", default="BTCUSDT", help="history symbol")
    parser.add_argument("--timeframe", default="1", help="history timeframe")
    parser.add_argument(
        "--allow-history-gaps",
        action="store_true",
        help="report gaps instead of rejecting the dataset",
    )
    return parser.parse_args(argv)


def inspect_history(args: argparse.Namespace) -> int:
    from bybit_workbench.historical import inspect_continuity, load_candles_csv

    dataset = load_candles_csv(
        args.inspect_history,
        symbol=args.symbol.strip().upper(),
        timeframe=args.timeframe.strip(),
        require_contiguous=not args.allow_history_gaps,
    )
    quality = inspect_continuity(dataset)
    print(
        json.dumps(
            {
                "symbol": dataset.symbol,
                "timeframe": dataset.timeframe,
                "candles": len(dataset.candles),
                "started_at": dataset.started_at.isoformat(),
                "ended_at": dataset.ended_at.isoformat(),
                "fingerprint": dataset.fingerprint,
                "contiguous": quality.is_contiguous,
                "gaps": [
                    {
                        "previous_closed_at": gap.previous_closed_at.isoformat(),
                        "next_opened_at": gap.next_opened_at.isoformat(),
                        "duration_seconds": gap.duration_seconds,
                    }
                    for gap in quality.gaps
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _parse_instrument_rules(raw: str | None, symbol: str) -> Any:
    from bybit_workbench.domain import InstrumentRules

    if raw is None or not raw.strip():
        raise ValueError(
            "BackTest requires --instrument-rules from a real read-only Bybit instrument snapshot"
        )
    try:
        payload = json.loads(raw, parse_float=Decimal)
    except json.JSONDecodeError as exc:
        raise ValueError(f"instrument rules are not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("instrument rules must be a JSON object")
    required = (
        "symbol",
        "tick_size",
        "qty_step",
        "min_order_qty",
        "min_notional",
        "max_order_qty",
    )
    missing = [name for name in required if name not in payload]
    if missing:
        raise ValueError("instrument rules missing fields: " + ", ".join(missing))
    rules = InstrumentRules(
        str(payload["symbol"]).strip().upper(),
        Decimal(str(payload["tick_size"])),
        Decimal(str(payload["qty_step"])),
        Decimal(str(payload["min_order_qty"])),
        Decimal(str(payload["min_notional"])),
        Decimal(str(payload["max_order_qty"])),
        (
            None
            if payload.get("max_market_order_qty") in (None, "")
            else Decimal(str(payload["max_market_order_qty"]))
        ),
    )
    if rules.symbol != symbol:
        raise ValueError(
            f"instrument rules symbol mismatch: expected={symbol} actual={rules.symbol}"
        )
    return rules


async def backtest_history(args: argparse.Namespace) -> int:
    from bybit_workbench.historical import (
        HistoricalAcceptancePolicy,
        HistoricalMarketData,
        HistoricalRunConfig,
        StressScenario,
        build_backtest_manifest,
        chronological_split,
        evaluate_stress_scenarios,
        evaluate_temporal_validation,
        evaluate_walk_forward,
        load_candles_csv,
        load_funding_csv,
        parameters_fingerprint,
        run_strategy,
        walk_forward_splits,
        write_backtest_json,
        write_trades_csv,
    )
    from bybit_workbench.strategies import default_strategy_registry

    symbol = args.symbol.strip().upper()
    timeframe = args.timeframe.strip()
    instrument_rules = _parse_instrument_rules(args.instrument_rules, symbol)
    trade = load_candles_csv(args.backtest, symbol=symbol, timeframe=timeframe)
    mark = (
        None
        if args.mark_history is None
        else load_candles_csv(args.mark_history, symbol=symbol, timeframe=timeframe)
    )
    funding = (
        ()
        if args.funding_history is None
        else load_funding_csv(args.funding_history, symbol=symbol)
    )
    market = HistoricalMarketData(
        trade,
        () if mark is None else mark.candles,
        funding,
        mark_price_complete=mark is not None,
        funding_complete=bool(funding),
    )
    if args.strict_market_data and not market.quality.production_equivalent:
        raise ValueError(
            "strict market data requires contiguous Trade OHLCV, aligned Mark Price, "
            "and a non-empty explicit funding series"
        )
    try:
        supplied = json.loads(args.parameters, parse_float=Decimal)
    except json.JSONDecodeError as exc:
        raise ValueError(f"parameters are not valid JSON: {exc}") from exc
    if not isinstance(supplied, dict):
        raise ValueError("parameters must be a JSON object")
    registry = default_strategy_registry()
    strategy = registry.create(args.strategy)
    profile = RiskProfile(
        max_risk_amount=Decimal("100"),
        max_risk_percent=Decimal("1"),
        max_position_notional=Decimal("100000"),
        max_leverage=Decimal("10"),
        max_daily_loss=Decimal("1000"),
        max_consecutive_losses=10,
        max_open_positions=1,
        max_pending_entries=1,
        max_slippage_percent=Decimal(str(args.slippage_percent)),
        estimated_fee_rate=Decimal(str(args.taker_fee_rate)),
        max_market_data_age_seconds=Decimal("1"),
        max_private_stream_age_seconds=Decimal("1"),
        allowed_symbols=frozenset({symbol}),
        allowed_directions=frozenset({PositionSide.LONG, PositionSide.SHORT}),
    )
    config = HistoricalRunConfig(
        initial_equity=Decimal(str(args.initial_equity)),
        available_balance=Decimal(str(args.available_balance)),
        risk_profile=profile,
        instrument_rules=instrument_rules,
        replay=ReplayConfig(
            fee_rate=Decimal("0"),
            maker_fee_rate=Decimal(str(args.maker_fee_rate)),
            taker_fee_rate=Decimal(str(args.taker_fee_rate)),
            slippage_percent=Decimal(str(args.slippage_percent)),
            seed=args.seed,
        ),
    )
    result = await run_strategy(
        strategy,
        trade,
        parameters=supplied,
        config=config,
        market_data=market,
    )
    rerun_options = {
        "walk_forward_training_bars": args.walk_forward_training_bars,
        "walk_forward_test_bars": args.walk_forward_test_bars,
        "stress_suite": bool(args.stress_suite),
        "sensitivity_parameter": args.sensitivity_parameter,
        "sensitivity_values": (
            None if args.sensitivity_values is None else json.loads(args.sensitivity_values)
        ),
        "strict_market_data": bool(args.strict_market_data),
        "eligibility": bool(args.eligibility),
    }
    manifest = build_backtest_manifest(
        result,
        config,
        dataset_path=args.backtest,
        mark_path=args.mark_history,
        funding_path=args.funding_history,
        code_version=__version_for_audit(),
        rerun=rerun_options,
    )
    tested_fingerprints = [parameters_fingerprint(result.parameters)]
    if (args.walk_forward_training_bars is None) != (args.walk_forward_test_bars is None):
        raise ValueError("both walk-forward training and test bar counts are required")
    if args.walk_forward_training_bars is not None:
        folds = walk_forward_splits(
            trade,
            training_bars=args.walk_forward_training_bars,
            test_bars=args.walk_forward_test_bars,
        )
        fold_results = await evaluate_walk_forward(
            lambda: registry.create(args.strategy),
            folds,
            parameters=supplied,
            config=config,
            market_data=market,
        )
        manifest["walk_forward"] = [
            {
                "fold": item.index,
                "train": _compact_run(item.train),
                "test": _compact_run(item.test),
            }
            for item in fold_results
        ]
    if args.stress_suite:
        stress_results = await evaluate_stress_scenarios(
            lambda: registry.create(args.strategy),
            trade,
            parameters=supplied,
            config=config,
            market_data=market,
            scenarios=(
                StressScenario(
                    "base",
                    config.replay.effective_taker_fee_rate,
                    config.replay.slippage_percent,
                ),
                StressScenario(
                    "adverse-cost-delay-gaps",
                    config.replay.effective_taker_fee_rate * Decimal("2"),
                    config.replay.slippage_percent * Decimal("2"),
                    execution_delay_bars=1,
                    gap_every_n_bars=10,
                ),
            ),
        )
        manifest["stress"] = [
            {
                "scenario": item.scenario.name,
                "assumptions": {
                    "fee_rate": item.scenario.fee_rate,
                    "slippage_percent": item.scenario.slippage_percent,
                    "execution_delay_bars": item.scenario.execution_delay_bars,
                    "gap_every_n_bars": item.scenario.gap_every_n_bars,
                },
                "result": _compact_run(item.run),
            }
            for item in stress_results
        ]
    if (args.sensitivity_parameter is None) != (args.sensitivity_values is None):
        raise ValueError("sensitivity parameter and values must be supplied together")
    if args.sensitivity_parameter is not None:
        values = json.loads(args.sensitivity_values, parse_float=Decimal)
        if not isinstance(values, list) or not values:
            raise ValueError("sensitivity values must be a non-empty JSON array")
        sensitivity: list[dict[str, object]] = []
        for value in values:
            varied = dict(supplied)
            varied[args.sensitivity_parameter] = value
            sensitivity_result = await run_strategy(
                registry.create(args.strategy),
                trade,
                parameters=varied,
                config=config,
                market_data=market,
            )
            fingerprint = parameters_fingerprint(sensitivity_result.parameters)
            tested_fingerprints.append(fingerprint)
            sensitivity.append(
                {
                    "parameter": args.sensitivity_parameter,
                    "value": value,
                    "parameters_fingerprint": fingerprint,
                    "result": _compact_run(sensitivity_result),
                }
            )
        manifest["sensitivity"] = sensitivity
    eligibility_result = None
    if args.eligibility:
        if not market.quality.production_equivalent:
            raise ValueError(
                "Micro-Live eligibility requires aligned Mark Price and explicit funding history"
            )
        parts = chronological_split(trade, minimum_partition_bars=2)
        calibration = type(trade)(parts.train.candles + parts.validation.candles)
        policy = HistoricalAcceptancePolicy(
            minimum_out_of_sample_trades=20,
            maximum_out_of_sample_drawdown=config.initial_equity * Decimal("0.20"),
            maximum_ambiguous_fraction=Decimal("0.05"),
            require_positive_out_of_sample_pnl=True,
            require_production_data=True,
        )
        journal = TradingJournal(
            args.database or AppSettings.from_environment().database_path
        )
        try:
            eligibility_result = await evaluate_temporal_validation(
                lambda: registry.create(args.strategy),
                train_dataset=calibration,
                out_of_sample_dataset=parts.test,
                parameters=supplied,
                config=config,
                policy=policy,
                code_version=__version_for_audit(),
                report_store=journal,
                train_market_data=market.slice_for(calibration),
                out_of_sample_market_data=market.slice_for(parts.test),
            )
        finally:
            journal.close()
        report = eligibility_result.report
        manifest["eligibility"] = {
            "report_id": eligibility_result.report_id,
            "eligible_for_micro_live": report.eligible_for_micro_live,
            "production_equivalent": report.production_equivalent,
            "dataset_fingerprint": report.dataset_fingerprint,
            "instrument_rules_fingerprint": report.instrument_rules_fingerprint,
            "parameters_fingerprint": report.parameters_fingerprint,
            "checks": report.checks,
            "train_period": report.train_period,
            "out_of_sample_period": report.out_of_sample_period,
        }
        manifest["badge"] = (
            "Eligible for Micro-Live gate"
            if report.eligible_for_micro_live
            else "Research only · Micro-Live blocked"
        )
    manifest["tested_parameter_fingerprints"] = tested_fingerprints
    manifest = _json_compatible(manifest)
    if args.report_json is not None:
        write_backtest_json(args.report_json, manifest)
    if args.trades_csv is not None:
        write_trades_csv(args.trades_csv, result)
    print(
        json.dumps(
            {
                "badge": manifest["badge"],
                "strategy": manifest["strategy"],
                "dataset": manifest["dataset"],
                "execution": manifest["execution"],
                "capital": manifest["capital"],
                "counts": manifest["counts"],
                "metrics": manifest["metrics"],
                "eligibility": manifest.get("eligibility"),
                "report_json": None if args.report_json is None else str(args.report_json),
                "trades_csv": None if args.trades_csv is None else str(args.trades_csv),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _compact_run(result: Any) -> dict[str, Any]:
    from dataclasses import asdict

    return {
        "dataset_fingerprint": result.dataset_fingerprint,
        "parameters": dict(result.parameters),
        "metrics": asdict(result.metrics),
        "trades": len(result.trades),
        "forced_flatten": result.forced_flatten,
        "production_equivalent": result.data_quality.production_equivalent,
    }


def _json_compatible(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _json_compatible(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_compatible(nested) for key, nested in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_compatible(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


async def rerun_backtest_report(args: argparse.Namespace) -> int:
    from tempfile import TemporaryDirectory

    manifest = json.loads(args.rerun_report.read_text(encoding="utf-8"))
    schema = manifest.get("schema_version")
    if schema != "backtest-report-v2":
        raise ValueError(
            "report predates pass 4 exact bindings; create a new backtest-report-v2 first"
        )
    dataset = manifest["dataset"]
    strategy = manifest["strategy"]
    execution = manifest["execution"]
    capital = manifest["capital"]
    instrument = dict(manifest["instrument_rules"])
    instrument.pop("fingerprint", None)
    rerun = manifest.get("rerun") or {}
    command = [
        "--backtest",
        dataset["trade_path"],
        "--strategy",
        strategy["id"],
        "--symbol",
        dataset["symbol"],
        "--timeframe",
        dataset["timeframe"],
        "--parameters",
        json.dumps(strategy["parameters"], ensure_ascii=False),
        "--instrument-rules",
        json.dumps(instrument, ensure_ascii=False),
        "--initial-equity",
        capital["initial_equity"],
        "--available-balance",
        capital["available_balance"],
        "--maker-fee-rate",
        execution["maker_fee_rate"],
        "--taker-fee-rate",
        execution["taker_fee_rate"],
        "--slippage-percent",
        execution["slippage_percent"],
        "--seed",
        str(execution["seed"]),
    ]
    if dataset.get("mark_path"):
        command.extend(("--mark-history", dataset["mark_path"]))
    if dataset.get("funding_path"):
        command.extend(("--funding-history", dataset["funding_path"]))
    if rerun.get("strict_market_data"):
        command.append("--strict-market-data")
    if rerun.get("walk_forward_training_bars") is not None:
        command.extend(
            (
                "--walk-forward-training-bars",
                str(rerun["walk_forward_training_bars"]),
                "--walk-forward-test-bars",
                str(rerun["walk_forward_test_bars"]),
            )
        )
    if rerun.get("stress_suite"):
        command.append("--stress-suite")
    if rerun.get("sensitivity_parameter") is not None:
        command.extend(
            (
                "--sensitivity-parameter",
                str(rerun["sensitivity_parameter"]),
                "--sensitivity-values",
                json.dumps(rerun["sensitivity_values"], ensure_ascii=False),
            )
        )
    with TemporaryDirectory(prefix="bybit-workbench-rerun-") as temporary:
        temp_report = Path(temporary) / "rerun.json"
        command.extend(("--report-json", str(temp_report)))
        if rerun.get("eligibility"):
            command.extend(("--eligibility", "--database", str(Path(temporary) / "gate.sqlite3")))
        rerun_args = parse_args(command)
        output = io.StringIO()
        with redirect_stdout(output):
            await backtest_history(rerun_args)
        reproduced = json.loads(temp_report.read_text(encoding="utf-8"))

    comparisons = {
        "dataset_fingerprint": (
            dataset["fingerprint"], reproduced["dataset"]["fingerprint"]
        ),
        "parameters_fingerprint": (
            strategy["parameters_fingerprint"],
            reproduced["strategy"]["parameters_fingerprint"],
        ),
        "instrument_rules_fingerprint": (
            manifest["instrument_rules"]["fingerprint"],
            reproduced["instrument_rules"]["fingerprint"],
        ),
        "code_version": (manifest["code_version"], reproduced["code_version"]),
        "execution_mode": (execution["mode"], reproduced["execution"]["mode"]),
        "price_trigger": (
            execution["price_trigger"], reproduced["execution"]["price_trigger"]
        ),
        "tested_parameter_fingerprints": (
            manifest.get("tested_parameter_fingerprints", []),
            reproduced.get("tested_parameter_fingerprints", []),
        ),
    }
    mismatches = {
        name: {"expected": expected, "actual": actual}
        for name, (expected, actual) in comparisons.items()
        if expected != actual
    }
    if mismatches:
        raise ValueError(
            "backtest rerun is not reproducible: "
            + json.dumps(mismatches, ensure_ascii=False, sort_keys=True)
        )
    if args.report_json is not None:
        args.report_json.write_text(
            json.dumps(reproduced, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    summary = json.loads(output.getvalue())
    summary["reproduced_from"] = str(args.rerun_report)
    summary["exact_binding_match"] = True
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


async def run_mainnet_acceptance(args: argparse.Namespace, settings: AppSettings) -> int:
    from bybit_workbench.app.credentials import WindowsCredentialStore
    from bybit_workbench.exchange.bybit.acceptance import write_acceptance_report
    from bybit_workbench.exchange.bybit.connection import create_mainnet_acceptance_runner

    if settings.mode is not AppMode.LIVE:
        raise ValueError("Mainnet acceptance requires BYBIT_WORKBENCH_PROFILE=live")
    store = WindowsCredentialStore()
    credentials = store.load(
        AppMode.LIVE,
        name=settings.credential_profile_name,
    )
    if credentials is None:
        raise RuntimeError(
            "Mainnet credentials are not stored in Windows Credential Manager for profile "
            f"{settings.credential_profile_name!r}; save them through the GUI first"
        )
    runner = create_mainnet_acceptance_runner(settings, credentials)
    report = await runner.run(args.symbol.strip().upper())
    report_path, sha_path = write_acceptance_report(report, args.acceptance_report)
    failing = [check.code for check in report.checks if check.blocking and not check.passed]
    print(
        json.dumps(
            {
                "schema": report.schema,
                "workbench_version": report.workbench_version,
                "endpoint": report.endpoint,
                "symbol": report.symbol,
                "micro_live_ready": report.micro_live_ready,
                "blocking_checks": failing,
                "report": str(report_path),
                "report_sha256_file": str(sha_path),
                "secret_material_included": False,
                "network_mutations_sent": 0,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0

def _apply_mainnet_startup_defaults(
    settings: AppSettings,
    endpoint_preference: MainnetEndpointPreference,
) -> AppSettings:
    """Prefer the Kazakhstan Mainnet stack on normal LIVE desktop startup.

    An explicit BYBIT_WORKBENCH_REST_URL remains an operator override. Without
    one, every LIVE launch resets the persisted UI endpoint to api.bybit.kz so
    an earlier temporary .com selection cannot silently survive a restart.
    """

    if settings.mode is not AppMode.LIVE or settings.rest_url_override is not None:
        return settings
    endpoint_preference.save(MAINNET_KZ_REST_URL)
    return replace(
        settings,
        rest_url_override=MAINNET_KZ_REST_URL,
        public_ws_url_override=None,
        private_ws_url_override=None,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = AppSettings.from_environment()
    if args.database is not None:
        settings = replace(settings, database_path=args.database)
    endpoint_preference = persistent_mainnet_endpoint(
        settings.database_path.parent / "mainnet_endpoint.json"
    )
    settings = _apply_mainnet_startup_defaults(settings, endpoint_preference)
    try:
        settings.validate_startup()
    except PermissionError as exc:
        print(f"STARTUP BLOCKED: {exc}", file=sys.stderr)
        return 3
    if args.inspect_history is not None:
        try:
            return inspect_history(args)
        except Exception as exc:
            print(f"HISTORY INVALID: {exc}", file=sys.stderr)
            return 4
    if args.backtest is not None:
        try:
            return asyncio.run(backtest_history(args))
        except Exception as exc:
            print(f"BACKTEST FAILED: {exc}", file=sys.stderr)
            return 5
    if args.rerun_report is not None:
        try:
            return asyncio.run(rerun_backtest_report(args))
        except Exception as exc:
            print(f"BACKTEST RERUN FAILED: {exc}", file=sys.stderr)
            return 5
    if args.mainnet_acceptance:
        try:
            if settings.mode is AppMode.LIVE:
                acceptance_clock_sync = resync_windows_time()
                status = "succeeded" if acceptance_clock_sync.succeeded else "failed"
                print(
                    "Windows clock preflight "
                    f"{status}: {acceptance_clock_sync.detail}"
                )
                if not acceptance_clock_sync.succeeded:
                    raise RuntimeError(
                        "Windows clock preflight failed; Mainnet GET-only acceptance "
                        "was not started"
                    )
            return asyncio.run(run_mainnet_acceptance(args, settings))
        except Exception as exc:
            print(f"MAINNET ACCEPTANCE FAILED: {exc}", file=sys.stderr)
            return 7
    if args.headless:
        return asyncio.run(headless_smoke(settings))
    if args.gui_smoke:
        return gui_smoke(settings)

    startup_clock_sync: WindowsTimeSyncResult | None = None
    if settings.mode is AppMode.LIVE:
        startup_clock_sync = resync_windows_time()

    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("PySide6 is not installed; use --headless or install project dependencies.")
        return 2
    from bybit_workbench.exchange.bybit.connection import (
        create_mainnet_access_diagnostics_runner,
        set_mainnet_symbol_leverage,
    )
    from bybit_workbench.ui.main_window import create_main_window
    from bybit_workbench.ui.mainnet_execution_runtime import MainnetExecutionRuntime
    from bybit_workbench.ui.manual_workflow import (
        ManualTradeWorkflow,
        PreparedManualTrade,
    )
    from bybit_workbench.ui.read_only_runtime import ReadOnlyRuntime
    from bybit_workbench.ui.testnet_execution_runtime import TestnetExecutionRuntime
    from bybit_workbench.ui.view_model import UserFacingError, WorkbenchViewModel

    app = QApplication(sys.argv)
    machine = AppStateMachine()
    model = WorkbenchViewModel(settings.mode)
    startup_clock_ready = startup_clock_sync is None or startup_clock_sync.succeeded
    if startup_clock_sync is not None:
        status = "succeeded" if startup_clock_sync.succeeded else "failed"
        model.append_system_log(
            f"Windows clock startup resync {status}: {startup_clock_sync.detail}"
        )
        if not startup_clock_sync.succeeded:
            model.set_error(
                UserFacingError(
                    "Не удалось запустить/синхронизировать службу времени Windows.",
                    "Автоподключение к Bybit остановлено до исправления часов.",
                    (
                        "Проверьте, что Workbench запущен от администратора; "
                        "затем нажмите Подключить повторно."
                    ),
                )
            )
    exchange: FakeExchange | None = None
    runtime: ReadOnlyRuntime | None = None
    execution_runtime: TestnetExecutionRuntime | MainnetExecutionRuntime | None = None
    workflow = ManualTradeWorkflow(machine, model)
    ui_journal = TradingJournal(settings.database_path)
    strategy_registry = default_strategy_registry()
    historical_gate = HistoricalEligibilityGate(ui_journal)

    def mainnet_armed_strategy_provider(prepared: PreparedManualTrade) -> ArmedStrategy:
        del prepared
        registration = strategy_registry.get("manual_protected_trade")
        parameters = registration.resolve_parameters(None)
        decision = historical_gate.require(registration, parameters, None)
        return ArmedStrategy(
            registration.metadata.strategy_id,
            registration.metadata.version,
            parameters,
            decision,
            registration.requires_historical_validation,
        )

    if settings.mode is AppMode.REPLAY:
        exchange = FakeExchange()
        asyncio.run(exchange.connect())
        machine.transition(AppState.SYNCING, "Replay feed connecting")
        machine.transition(AppState.READY, "Replay feed ready")
    else:
        runtime = ReadOnlyRuntime(settings, machine)
        if settings.testnet_execution_allowed:
            execution_runtime = TestnetExecutionRuntime(
                settings,
                machine,
                private_snapshot_provider=runtime.latest_private_observation,
            )
        elif settings.mode is AppMode.LIVE:
            execution_runtime = MainnetExecutionRuntime(
                settings,
                machine,
                context_provider=runtime.latest_mainnet_context,
                private_snapshot_provider=runtime.latest_private_observation,
                armed_strategy_provider=mainnet_armed_strategy_provider,
            )
    def current_mainnet_endpoint() -> str:
        selected_settings = runtime.settings if runtime is not None else settings
        endpoint = selected_settings.endpoint_profile.rest_url
        return endpoint or ""

    def set_mainnet_endpoint(endpoint: str) -> str:
        if settings.mode is not AppMode.LIVE or runtime is None:
            raise RuntimeError("Mainnet endpoint can only be changed in LIVE mode")
        normalized = normalize_mainnet_endpoint(endpoint)
        if runtime.running:
            raise RuntimeError("Отключите read-only перед сменой endpoint")
        if isinstance(execution_runtime, MainnetExecutionRuntime) and execution_runtime.running:
            raise RuntimeError("Остановите активную Mainnet-операцию перед сменой endpoint")
        current = runtime.settings
        updated = replace(
            current,
            rest_url_override=normalized,
            public_ws_url_override=None,
            private_ws_url_override=None,
        )
        updated.validate_startup()
        runtime.reconfigure(updated)
        if isinstance(execution_runtime, MainnetExecutionRuntime):
            execution_runtime.reconfigure(updated)
        endpoint_preference.save(normalized)
        model.append_system_log(f"Mainnet endpoint changed by operator: {normalized}")
        return normalized

    def set_mainnet_leverage(symbol: str, leverage: str) -> str:
        if settings.mode is not AppMode.LIVE or runtime is None:
            raise RuntimeError("плечо можно менять из интерфейса только в LIVE режиме")
        if isinstance(execution_runtime, MainnetExecutionRuntime):
            status = execution_runtime.status
            if execution_runtime.running or status.phase.value not in {"DISARMED", "BLOCKED"}:
                raise RuntimeError(
                    "сначала завершите или сбросьте активный Mainnet execution workflow"
                )
        state = model.state
        selected_symbol = symbol.strip().upper()
        if state.symbol == selected_symbol and state.account_leverage == Decimal(leverage):
            return leverage
        selected_settings = runtime.settings
        credentials = runtime.credential_store.load(
            AppMode.LIVE,
            name=selected_settings.credential_profile_name,
        )
        if credentials is None:
            raise RuntimeError("профиль Mainnet API-ключей не сохранён")
        applied = set_mainnet_symbol_leverage(
            selected_settings,
            credentials,
            selected_symbol,
            leverage,
        )
        return applied

    backtest_messages: SimpleQueue[str] = SimpleQueue()
    access_diagnostic_messages: SimpleQueue[str] = SimpleQueue()
    access_diagnostic_running = threading.Event()

    def start_access_diagnostics(request: dict[str, str]) -> None:
        if settings.mode is not AppMode.LIVE or runtime is None:
            access_diagnostic_messages.put(
                "Диагностика доступа доступна только в LIVE/Mainnet режиме."
            )
            return
        if access_diagnostic_running.is_set():
            access_diagnostic_messages.put("Диагностика уже выполняется…")
            return
        access_diagnostic_running.set()

        def worker() -> None:
            try:
                selected_settings = runtime.settings
                credentials = runtime.credential_store.load(
                    AppMode.LIVE,
                    name=selected_settings.credential_profile_name,
                )
                if credentials is None:
                    raise RuntimeError("профиль Mainnet API-ключей не сохранён")
                runner = create_mainnet_access_diagnostics_runner(
                    selected_settings,
                    credentials,
                )
                report = runner.run(
                    symbol=request["symbol"],
                    side=request["side"],
                    quantity=request["quantity"],
                    price=request["price"],
                )
                access_diagnostic_messages.put("\n".join(report.lines))
            except Exception as exc:
                access_diagnostic_messages.put(
                    "Диагностика завершилась ошибкой: "
                    + (str(exc).strip() or exc.__class__.__name__)
                )
            finally:
                access_diagnostic_running.clear()

        threading.Thread(
            target=worker,
            name="mainnet-access-diagnostics",
            daemon=True,
        ).start()

    def load_access_diagnostics() -> tuple[str, ...]:
        lines: list[str] = []
        while True:
            try:
                lines.append(access_diagnostic_messages.get_nowait())
            except Empty:
                return tuple(lines)

    def save_risk_profile(payload: dict[str, object]) -> None:
        journal = TradingJournal(settings.database_path)
        try:
            journal.save_settings_version(
                f"risk:{settings.mode.value}:{payload['profile_name']}",
                payload,
            )
        finally:
            journal.close()

    def pump_runtimes() -> None:
        if runtime is not None:
            runtime.drain_into(model)
        if execution_runtime is not None:
            execution_runtime.drain_into(model)

    def execution_command_lines() -> tuple[str, ...]:
        return tuple(
            f"{command.updated_at.isoformat()} · {command.kind.value} · "
            f"{command.status.value} · {command.symbol} · {command.command_id}"
            for command in ui_journal.recent_execution_commands()
        )

    def start_ui_backtest(request: dict[str, str]) -> None:
        state = model.state
        if state.instrument is None:
            backtest_messages.put(
                "BackTest заблокирован: сначала выполните read-only подключение, "
                "чтобы получить реальные InstrumentRules выбранного символа."
            )
            return
        if state.instrument.symbol != request["symbol"]:
            backtest_messages.put(
                "BackTest заблокирован: InstrumentRules в UI относятся к "
                f"{state.instrument.symbol}, а выбран {request['symbol']}."
            )
            return
        maker = state.maker_fee_rate
        taker = state.taker_fee_rate
        eligibility = request.get("eligibility") == "true"
        if eligibility and (maker is None or taker is None):
            backtest_messages.put(
                "Eligibility заблокирован: read-only snapshot не содержит maker/taker fee rates."
            )
            return
        command = [
            "--backtest",
            request["trade_path"],
            "--strategy",
            request["strategy_id"],
            "--symbol",
            request["symbol"],
            "--timeframe",
            request["timeframe"],
            "--parameters",
            request["parameters"],
            "--instrument-rules",
            json.dumps(_json_compatible(asdict(state.instrument)), ensure_ascii=False),
            "--report-json",
            request["report_path"],
            "--database",
            str(settings.database_path),
        ]
        if state.equity is not None:
            command.extend(("--initial-equity", str(state.equity)))
        if state.available_balance is not None:
            command.extend(("--available-balance", str(state.available_balance)))
        if maker is not None:
            command.extend(("--maker-fee-rate", str(maker)))
        if taker is not None:
            command.extend(("--taker-fee-rate", str(taker)))
        if request["mark_path"]:
            command.extend(("--mark-history", request["mark_path"]))
        if request["funding_path"]:
            command.extend(("--funding-history", request["funding_path"]))
        if eligibility:
            command.extend(("--eligibility", "--strict-market-data"))
        parsed = parse_args(command)

        def worker() -> None:
            output = io.StringIO()
            try:
                with redirect_stdout(output):
                    asyncio.run(backtest_history(parsed))
            except Exception as exc:
                backtest_messages.put(f"BackTest завершился ошибкой: {exc}")
                return
            summary = json.loads(output.getvalue())
            gate = summary.get("eligibility")
            gate_text = (
                "research-only"
                if gate is None
                else (
                    f"eligibility={gate['eligible_for_micro_live']} "
                    f"report_id={gate['report_id']}"
                )
            )
            backtest_messages.put(
                f"{summary['badge']} · "
                f"trades={summary['counts']['trades']} · "
                f"net_pnl={summary['metrics']['net_pnl']} · "
                f"production_equivalent="
                f"{summary['dataset']['quality']['production_equivalent']} · "
                f"{gate_text} · report={request['report_path']}"
            )

        threading.Thread(target=worker, name="backtest-worker", daemon=True).start()

    def load_ui_backtest_results() -> tuple[str, ...]:
        lines: list[str] = []
        while True:
            try:
                lines.append(backtest_messages.get_nowait())
            except Empty:
                return tuple(lines)

    prepare_execution = (
        execution_runtime.prepare
        if isinstance(execution_runtime, MainnetExecutionRuntime)
        else None
    )
    arm_execution = (
        execution_runtime.arm
        if isinstance(execution_runtime, MainnetExecutionRuntime)
        else None
    )
    invalidate_execution = (
        execution_runtime.invalidate
        if isinstance(execution_runtime, MainnetExecutionRuntime)
        else None
    )
    submit_execution: Callable[[PreparedManualTrade], None] | None
    if isinstance(execution_runtime, TestnetExecutionRuntime):
        selected_testnet_runtime = execution_runtime

        def submit_testnet(prepared: PreparedManualTrade) -> None:
            selected_testnet_runtime.submit(prepared, model.health_snapshot())

        submit_execution = submit_testnet
    elif isinstance(execution_runtime, MainnetExecutionRuntime):
        submit_execution = execution_runtime.submit
    else:
        submit_execution = None

    window = create_main_window(
        settings,
        machine,
        exchange,
        view_model=model,
        connect_read_only=None if runtime is None else runtime.start,
        disconnect_read_only=None if runtime is None else runtime.request_stop,
        get_mainnet_endpoint=(
            current_mainnet_endpoint if settings.mode is AppMode.LIVE else None
        ),
        set_mainnet_endpoint=(
            set_mainnet_endpoint if settings.mode is AppMode.LIVE else None
        ),
        set_mainnet_leverage=(
            set_mainnet_leverage if settings.mode is AppMode.LIVE else None
        ),
        switch_read_only_market=None if runtime is None else runtime.switch_market,
        pump_read_only=pump_runtimes,
        start_access_diagnostics=(
            start_access_diagnostics if settings.mode is AppMode.LIVE else None
        ),
        load_access_diagnostics=(
            load_access_diagnostics if settings.mode is AppMode.LIVE else None
        ),
        manual_workflow=workflow,
        prepare_execution=prepare_execution,
        arm_execution=arm_execution,
        invalidate_execution=invalidate_execution,
        submit_manual_trade=submit_execution,
        stop_strategy=(None if execution_runtime is None else execution_runtime.request_stop),
        cancel_entries=(
            None
            if execution_runtime is None
            else execution_runtime.request_cancel_entries_for_symbol
        ),
        cancel_non_protective=(
            None
            if execution_runtime is None
            else execution_runtime.request_cancel_non_protective_for_symbol
        ),
        flatten_position=(
            None if execution_runtime is None else execution_runtime.request_flatten_for_symbol
        ),
        emergency_strategy=(
            None
            if execution_runtime is None
            else lambda: execution_runtime.request_emergency_for_symbol(model.state.symbol)
        ),
        save_risk_profile=save_risk_profile,
        load_execution_commands=execution_command_lines,
        start_backtest=start_ui_backtest,
        load_backtest_results=load_ui_backtest_results,
        auto_connect_read_only=(
            settings.mode is AppMode.LIVE and runtime is not None and startup_clock_ready
        ),
    )
    window.show()
    result = app.exec()
    if runtime is not None:
        runtime.stop()
    if execution_runtime is not None:
        execution_runtime.stop()
    ui_journal.close()
    if exchange is not None:
        asyncio.run(exchange.disconnect())
    return result


if __name__ == "__main__":
    raise SystemExit(main())
