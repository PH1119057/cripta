from unittest.mock import patch

import pytest

import operations.connectivity.universal_entry_consumer as consumer


def test_armed_consumer_requires_explicit_connector_before_database_connection():
    with (
        patch.object(consumer, "ENTRY_COMMAND_SOURCE", "UNIVERSAL_ENTRY"),
        patch.object(consumer, "CONSUMER_ARM", "ENABLED"),
        patch.object(consumer, "TRADING_ACCOUNT_ID", "ta-1"),
        patch.object(consumer, "ACCOUNT_CONNECTOR_ID", ""),
        patch.object(consumer.psycopg, "connect") as connect,
    ):
        with pytest.raises(SystemExit, match="CRIPTA_ACCOUNT_CONNECTOR_ID is required"):
            consumer.main()
        connect.assert_not_called()


def test_disabled_consumer_does_not_require_connector_or_database():
    with (
        patch.object(consumer, "CONSUMER_ARM", "DISABLED"),
        patch.object(consumer, "ACCOUNT_CONNECTOR_ID", ""),
        patch.object(consumer.psycopg, "connect") as connect,
    ):
        assert consumer.main() == 0
        connect.assert_not_called()
