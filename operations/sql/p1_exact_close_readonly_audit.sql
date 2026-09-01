-- Read-only, coin-independent audit of exact position closure evidence.
-- It never links by nearest timestamp.  Each interval ends at the next owned
-- position fill for the same Bybit symbol/positionIdx inventory.
WITH owned AS (
    SELECT o.*,
           lead(fill_at) OVER (
               PARTITION BY symbol, position_idx ORDER BY fill_at, position_id
           ) AS next_fill_at
    FROM runtime.position_ownership o
), closing AS (
    SELECT o.position_id, o.trade_id, o.symbol, o.side, o.actual_avg_fill,
           o.actual_qty, o.fill_at, o.next_fill_at,
           e.exec_id, e.order_id, e.exec_qty::numeric AS exec_qty,
           e.exec_price::numeric AS exec_price, abs(e.exec_fee::numeric) AS exec_fee,
           e.exec_time_ms, e.payload_json::jsonb AS body
    FROM owned o
    LEFT JOIN runtime.executions e
      ON e.symbol=o.symbol
     AND e.exec_time_ms >= extract(epoch FROM o.fill_at)*1000
     AND (o.next_fill_at IS NULL
          OR e.exec_time_ms < extract(epoch FROM o.next_fill_at)*1000)
     AND e.side=CASE WHEN o.side='Buy' THEN 'Sell' ELSE 'Buy' END
     AND coalesce((e.payload_json::jsonb->>'closedSize')::numeric, 0)>0
), resolved AS (
    SELECT position_id, min(trade_id) AS trade_id, min(symbol) AS symbol,
           min(side) AS side, min(actual_avg_fill) AS actual_avg_fill,
           min(actual_qty) AS actual_qty, min(fill_at) AS fill_at,
           sum(exec_qty) FILTER (WHERE exec_id IS NOT NULL) AS actual_exit_qty,
           sum(exec_qty*exec_price) FILTER (WHERE exec_id IS NOT NULL)
             / nullif(sum(exec_qty) FILTER (WHERE exec_id IS NOT NULL),0)
             AS actual_exit_avg_fill,
           sum(exec_fee) FILTER (WHERE exec_id IS NOT NULL) AS exit_fee_actual,
           jsonb_agg(DISTINCT order_id) FILTER (WHERE order_id IS NOT NULL)
             AS exit_order_ids,
           jsonb_agg(exec_id ORDER BY exec_time_ms,exec_id)
             FILTER (WHERE exec_id IS NOT NULL) AS exit_execution_ids,
           max(exec_time_ms) FILTER (WHERE exec_id IS NOT NULL) AS closed_at_ms,
           (array_agg(body ORDER BY exec_time_ms DESC,exec_id DESC)
             FILTER (WHERE exec_id IS NOT NULL))[1] AS final_execution
    FROM closing
    GROUP BY position_id
)
SELECT position_id, trade_id, symbol, side, fill_at,
       CASE WHEN actual_exit_qty=actual_qty THEN 'EXACT'
            ELSE 'UNRESOLVED_EXACT_LINK' END AS link_status,
       actual_avg_fill, actual_qty, actual_exit_avg_fill, actual_exit_qty,
       CASE WHEN actual_exit_qty=actual_qty THEN
           (actual_exit_avg_fill/actual_avg_fill-1)*100*
             CASE WHEN side='Buy' THEN 1 ELSE -1 END
       END AS entry_to_exit_price_move_pct,
       exit_fee_actual, exit_order_ids, exit_execution_ids,
       to_timestamp(closed_at_ms/1000.0) AS closed_at,
       final_execution->>'createType' AS bybit_create_type,
       final_execution->>'stopOrderType' AS bybit_stop_order_type,
       final_execution->>'triggerPrice' AS exchange_trigger_price,
       final_execution->>'triggerBy' AS trigger_by
FROM resolved
ORDER BY fill_at DESC;
