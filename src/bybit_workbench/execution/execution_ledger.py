from bybit_workbench.domain.models import Execution


class ExecutionLedger:
    def __init__(self) -> None:
        self._by_id: dict[str, Execution] = {}

    def record(self, execution: Execution) -> bool:
        existing = self._by_id.get(execution.execution_id)
        if existing is not None:
            if existing != execution:
                raise ValueError("execution id was reused with different data")
            return False
        self._by_id[execution.execution_id] = execution
        return True

    @property
    def executions(self) -> tuple[Execution, ...]:
        return tuple(self._by_id.values())
