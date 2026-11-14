from __future__ import annotations

from dataclasses import dataclass

from .contracts import FeatureKind


@dataclass(frozen=True, slots=True)
class FeatureDefinition:
    feature_id: str
    group: str
    label_ru: str
    kind: FeatureKind
    description_ru: str
    allowed_values: tuple[str, ...] = ()
    unit: str | None = None


# V1 deliberately contains only stable, strategy-independent market dimensions.
# The architecture document contains the wider long-term vocabulary.
V1_FEATURES: tuple[FeatureDefinition, ...] = (
    FeatureDefinition("data.quality", "quality", "Качество данных", FeatureKind.STATUS,
                      "Общая пригодность данных Маяка для интерпретации.",
                      ("HIGH", "MEDIUM", "LOW", "INSUFFICIENT")),
    FeatureDefinition("market.direction", "price", "Направление рынка", FeatureKind.CATEGORICAL,
                      "Преобладающее направление рынка.",
                      ("STRONG_UP", "UP", "NEUTRAL", "DOWN", "STRONG_DOWN")),
    FeatureDefinition("market.direction_strength", "price", "Сила направления", FeatureKind.CATEGORICAL,
                      "Насколько выражено направленное движение.",
                      ("NONE", "WEAK", "MODERATE", "STRONG", "EXTREME")),
    FeatureDefinition("market.direction_persistence", "price", "Устойчивость направления", FeatureKind.CATEGORICAL,
                      "Насколько долго направление сохраняется.",
                      ("VERY_LOW", "LOW", "MEDIUM", "HIGH", "VERY_HIGH")),
    FeatureDefinition("market.move_maturity", "price", "Зрелость движения", FeatureKind.CATEGORICAL,
                      "Стадия текущего направленного движения.",
                      ("EMERGING", "DEVELOPING", "MATURE", "LATE", "EXHAUSTING")),
    FeatureDefinition("market.volatility", "regime", "Волатильность", FeatureKind.CATEGORICAL,
                      "Текущая волатильность относительно собственной нормы рынка.",
                      ("VERY_LOW", "LOW", "NORMAL", "HIGH", "EXTREME")),
    FeatureDefinition("market.volatility_trend", "regime", "Изменение волатильности", FeatureKind.CATEGORICAL,
                      "Ускоряется или затухает волатильность.",
                      ("FALLING_FAST", "FALLING", "STABLE", "RISING", "RISING_FAST")),
    FeatureDefinition("market.breadth", "breadth", "Ширина рынка", FeatureKind.CATEGORICAL,
                      "Насколько широко направление распространено между активами.",
                      ("STRONGLY_BULLISH", "BULLISH", "BALANCED", "BEARISH", "STRONGLY_BEARISH")),
    FeatureDefinition("market.synchronization", "breadth", "Синхронность направления", FeatureKind.CATEGORICAL,
                      "Насколько одновременно активы движутся в одном направлении.",
                      ("LOW", "NORMAL", "HIGH", "EXTREME")),
    FeatureDefinition("market.range_state", "structure", "Состояние боковика", FeatureKind.CATEGORICAL,
                      "Фаза общерыночного диапазона.",
                      ("NONE", "FORMING", "ESTABLISHED", "MATURE", "BREAKING")),
    FeatureDefinition("market.breakout_environment", "structure", "Среда для пробоя", FeatureKind.CATEGORICAL,
                      "Насколько среда благоприятна для продолжения выходов из диапазона.",
                      ("HOSTILE", "WEAK", "NEUTRAL", "FAVORABLE", "STRONG")),
    FeatureDefinition("money.pressure", "money", "Денежное давление", FeatureKind.CATEGORICAL,
                      "Преобладающая сторона реально исполненного денежного потока.",
                      ("STRONG_BUY", "BUY", "BALANCED", "SELL", "STRONG_SELL")),
    FeatureDefinition("money.pressure_acceleration", "money", "Ускорение денежного давления", FeatureKind.CATEGORICAL,
                      "Усиливается или ослабевает преобладающий денежный поток.",
                      ("REVERSING", "WEAKENING", "STABLE", "STRENGTHENING", "SURGING")),
    FeatureDefinition("money.spot_pressure", "money", "Давление спота", FeatureKind.CATEGORICAL,
                      "Сторона исполненного денежного потока на спотовом рынке.",
                      ("STRONG_BUY", "BUY", "BALANCED", "SELL", "STRONG_SELL")),
    FeatureDefinition("money.derivatives_pressure", "money", "Давление срочного рынка", FeatureKind.CATEGORICAL,
                      "Сторона исполненного денежного потока на срочном рынке.",
                      ("STRONG_BUY", "BUY", "BALANCED", "SELL", "STRONG_SELL")),
    FeatureDefinition("money.spot_derivatives_alignment", "money", "Согласие спота и срочного рынка", FeatureKind.CATEGORICAL,
                      "Насколько одинаково ведут себя спот и срочный рынок.",
                      ("STRONGLY_ALIGNED", "ALIGNED", "MIXED", "DIVERGING", "STRONGLY_DIVERGING")),
    FeatureDefinition("positioning.oi_regime", "positioning", "Режим открытого интереса", FeatureKind.CATEGORICAL,
                      "Расширяется или сокращается объём открытых контрактов.",
                      ("STRONG_CONTRACTION", "CONTRACTING", "STABLE", "EXPANDING", "STRONG_EXPANSION")),
    FeatureDefinition("positioning.price_oi_state", "positioning", "Цена и открытый интерес", FeatureKind.CATEGORICAL,
                      "Каноническое сочетание направления цены и открытого интереса.",
                      ("PRICE_UP_OI_UP", "PRICE_UP_OI_DOWN", "PRICE_DOWN_OI_UP", "PRICE_DOWN_OI_DOWN", "MIXED")),
    FeatureDefinition("liquidity.quality", "liquidity", "Качество ликвидности", FeatureKind.CATEGORICAL,
                      "Глубина доступной ликвидности относительно нормы.",
                      ("POOR", "THIN", "NORMAL", "DEEP", "VERY_DEEP")),
    FeatureDefinition("liquidity.trend", "liquidity", "Изменение ликвидности", FeatureKind.CATEGORICAL,
                      "Исчезает или восстанавливается доступная ликвидность.",
                      ("WITHDRAWING_FAST", "WITHDRAWING", "STABLE", "REPLENISHING", "REPLENISHING_FAST")),
    FeatureDefinition("liquidity.absorption", "liquidity", "Поглощение", FeatureKind.CATEGORICAL,
                      "Наличие поглощения агрессивного потока противоположной стороной.",
                      ("NONE", "WEAK_BUY", "BUY", "STRONG_BUY", "WEAK_SELL", "SELL", "STRONG_SELL")),
    FeatureDefinition("liquidation.intensity", "liquidations", "Интенсивность ликвидаций", FeatureKind.CATEGORICAL,
                      "Масштаб принудительных ликвидаций относительно нормы.",
                      ("NONE", "LOW", "NORMAL", "HIGH", "EXTREME")),
    FeatureDefinition("liquidation.acceleration", "liquidations", "Ускорение ликвидаций", FeatureKind.CATEGORICAL,
                      "Ускоряется или затухает принудительный поток ликвидаций.",
                      ("FALLING_FAST", "FALLING", "STABLE", "RISING", "SURGING")),
    FeatureDefinition("liquidation.breadth", "liquidations", "Ширина ликвидаций", FeatureKind.CATEGORICAL,
                      "Насколько широко ликвидации распространяются между активами.",
                      ("LOCAL", "LIMITED", "BROAD", "MARKET_WIDE", "SYSTEMIC")),
    FeatureDefinition("liquidation.phase", "liquidations", "Фаза ликвидационного процесса", FeatureKind.CATEGORICAL,
                      "Причинная фаза: накопление напряжения, каскад, истощение или восстановление.",
                      ("NONE", "TENSION_BUILDING", "CASCADE", "EXHAUSTION", "RECOVERY", "UNCERTAIN")),
    FeatureDefinition("market.exhaustion", "regime", "Истощение движения", FeatureKind.CATEGORICAL,
                      "Насколько выражены признаки потери эффективности текущего движения.",
                      ("NONE", "WEAK", "FORMING", "STRONG", "CONFIRMED")),
    FeatureDefinition("market.continuation_environment", "environment", "Среда продолжения", FeatureKind.CATEGORICAL,
                      "Насколько среда поддерживает продолжение текущего движения.",
                      ("HOSTILE", "WEAK", "NEUTRAL", "FAVORABLE", "STRONG")),
    FeatureDefinition("market.bounce_environment", "environment", "Среда отскока", FeatureKind.CATEGORICAL,
                      "Насколько сформирована среда для отскока после сильного движения.",
                      ("HOSTILE", "EARLY", "FORMING", "FAVORABLE", "STRONG")),
    FeatureDefinition("market.chaos", "regime", "Хаотичность", FeatureKind.CATEGORICAL,
                      "Частота противоречивых движений и нестабильность краткосрочного режима.",
                      ("LOW", "NORMAL", "HIGH", "EXTREME")),
    FeatureDefinition("market.transition_speed", "regime", "Скорость смены режима", FeatureKind.CATEGORICAL,
                      "Насколько быстро рынок переходит между режимами.",
                      ("STABLE", "SLOW_CHANGE", "TRANSITIONING", "FAST_CHANGE", "VIOLENT_CHANGE")),
    FeatureDefinition("market.regime_stability", "regime", "Стабильность режима", FeatureKind.CATEGORICAL,
                      "Насколько устойчив текущий режим.",
                      ("VERY_LOW", "LOW", "NORMAL", "HIGH", "VERY_HIGH")),
    FeatureDefinition("btc.state", "anchors", "Состояние BTC", FeatureKind.CATEGORICAL,
                      "Направление и сила опорного актива BTC.",
                      ("STRONG_UP", "UP", "NEUTRAL", "DOWN", "STRONG_DOWN")),
    FeatureDefinition("eth.state", "anchors", "Состояние ETH", FeatureKind.CATEGORICAL,
                      "Направление и сила опорного актива ETH.",
                      ("STRONG_UP", "UP", "NEUTRAL", "DOWN", "STRONG_DOWN")),
    FeatureDefinition("market.timeframe_alignment", "regime", "Согласие временных горизонтов", FeatureKind.CATEGORICAL,
                      "Насколько 1/5/15/60-минутные картины согласованы.",
                      ("CONFLICTING", "MIXED", "PARTIAL", "ALIGNED", "STRONGLY_ALIGNED")),
    FeatureDefinition("event.context", "events", "Событийный фон", FeatureKind.CATEGORICAL,
                      "Положение относительно объективно известного внешнего события.",
                      ("NONE", "UPCOMING", "IMMINENT", "ACTIVE", "POST_EVENT")),
    FeatureDefinition("event.importance", "events", "Значимость события", FeatureKind.CATEGORICAL,
                      "Оценка заранее определённой значимости внешнего события.",
                      ("LOW", "MEDIUM", "HIGH", "EXTREME")),
)


V1_FEATURE_INDEX = {item.feature_id: item for item in V1_FEATURES}


def definition(feature_id: str) -> FeatureDefinition:
    try:
        return V1_FEATURE_INDEX[feature_id]
    except KeyError as exc:
        raise LookupError(f"unknown dispatcher feature: {feature_id}") from exc
