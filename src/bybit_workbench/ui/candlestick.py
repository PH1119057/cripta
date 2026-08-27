from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import pyqtgraph as pg  # type: ignore[import-untyped]
from PySide6.QtCore import QLineF, QRectF
from PySide6.QtGui import QBrush, QColor, QPainter, QPen

from bybit_workbench.domain.models import Candle


class CandlestickItem(pg.GraphicsObject):  # type: ignore[misc]
    """Bybit-like time-based OHLC candles with solid bodies and thin wicks.

    The item paints directly instead of recording into QPicture. QPicture's integer
    bounding box is a poor fit for crypto prices around a few USDT and can make small
    candle bodies look like full-height bars after view transforms.
    """

    def __init__(self) -> None:
        super().__init__()
        self._candles: tuple[Candle, ...] = ()
        self._origin = 0.0
        self._body_width = 60.0
        self._bounds = QRectF()

    def set_candles(self, candles: Sequence[Candle]) -> None:
        self.prepareGeometryChange()
        self._candles = tuple(candles)
        if not self._candles:
            self._origin = 0.0
            self._body_width = 60.0
            self._bounds = QRectF()
            self.setPos(0.0, 0.0)
            self.update()
            return

        self._origin = self._candles[0].opened_at.timestamp()
        self._body_width = _body_width_seconds(self._candles)
        half_width = self._body_width / 2
        first_x = 0.0
        last_x = self._candles[-1].opened_at.timestamp() - self._origin
        low = min(float(candle.low) for candle in self._candles)
        high = max(float(candle.high) for candle in self._candles)
        height = max(high - low, 1e-9)
        self._bounds = QRectF(
            first_x - half_width,
            low,
            max(last_x - first_x + self._body_width, self._body_width),
            height,
        )
        self.setPos(self._origin, 0.0)
        self.update()

    def paint(self, painter: QPainter, *args: object) -> None:
        del args
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        half_width = self._body_width / 2
        for candle in self._candles:
            x = candle.opened_at.timestamp() - self._origin
            opened = float(candle.open)
            high = float(candle.high)
            low = float(candle.low)
            closed = float(candle.close)
            rising = closed >= opened
            color = QColor("#2ebd85" if rising else "#f6465d")
            pen = QPen(color, 1)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.drawLine(QLineF(x, low, x, high))

            painter.setBrush(QBrush(color))
            body_delta = closed - opened
            if abs(body_delta) <= max(abs(opened), abs(closed), 1.0) * 0.00002:
                painter.drawLine(
                    QLineF(x - half_width, opened, x + half_width, opened)
                )
                continue

            # Keep the signed open→close delta. This mirrors the canonical
            # pyqtgraph candlestick geometry and remains correct under the
            # ViewBox's inverted Qt Y transform.
            painter.drawRect(
                QRectF(
                    x - half_width,
                    opened,
                    self._body_width,
                    body_delta,
                )
            )

    def boundingRect(self) -> QRectF:
        return self._bounds


def candle_time_bounds(candles: Sequence[Candle]) -> tuple[float, float] | None:
    if not candles:
        return None
    width = _body_width_seconds(candles)
    return (
        candles[0].opened_at.timestamp() - width,
        candles[-1].opened_at.timestamp() + width,
    )


def _body_width_seconds(candles: Sequence[Candle]) -> float:
    if len(candles) >= 2:
        gaps = [
            (right.opened_at - left.opened_at).total_seconds()
            for left, right in zip(candles, candles[1:], strict=False)
            if right.opened_at > left.opened_at
        ]
        if gaps:
            return max(1.0, min(gaps) * 0.64)
    if candles:
        return max(1.0, _timeframe_seconds(candles[0].timeframe) * 0.64)
    return 60.0


def _timeframe_seconds(timeframe: str) -> float:
    value = timeframe.strip().upper()
    if value.isdigit():
        return float(int(value) * 60)
    return {
        "D": 86_400.0,
        "W": 604_800.0,
        "M": 2_592_000.0,
    }.get(value, 60.0)


def utc_timestamp(value: datetime) -> float:
    return value.timestamp()
