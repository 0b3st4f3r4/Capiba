"""Tests for the shared HTTP helpers of the ingestion crawlers.

Responsibility: Validate fetch_page retry, backoff, rate limit
and error handling without real network access.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from capiba.ingestion._http import RATE_LIMIT_DELAY, fetch_page

MODULE = "capiba.ingestion._http"


def _response(status_code: int = 200, payload: Any = None) -> MagicMock:
    """Builds a mock HTTP response with the given status and JSON payload."""
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload if payload is not None else {"data": []}
    return response


def _http_error(status_code: int | None) -> requests.HTTPError:
    """Builds an HTTPError carrying a response with the given status code."""
    response = MagicMock() if status_code is not None else None
    if response is not None:
        response.status_code = status_code
    return requests.HTTPError("boom", response=response)


class TestFetchPage:
    """Tests for fetch_page retry and backoff behavior."""

    @patch(f"{MODULE}.time.sleep")
    @patch(f"{MODULE}.requests.get")
    def test_success_returns_json(self, mock_get: MagicMock, _: MagicMock) -> None:
        """Must return the parsed JSON on a successful response."""
        mock_get.return_value = _response(200, {"data": [{"id": 1}]})

        result = fetch_page("https://api.example.com", params={"pagina": 1})

        assert result == {"data": [{"id": 1}]}
        _, kwargs = mock_get.call_args
        assert kwargs["timeout"] == 90

    @patch(f"{MODULE}.time.sleep")
    @patch(f"{MODULE}.requests.get")
    def test_empty_status_returns_none(
        self, mock_get: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """Statuses in empty_statuses must return None without retrying."""
        mock_get.return_value = _response(204)

        result = fetch_page(
            "https://api.example.com",
            params={"pagina": 1},
            empty_statuses=(204,),
        )

        assert result is None
        mock_get.assert_called_once()
        mock_sleep.assert_not_called()

    @patch(f"{MODULE}.time.sleep")
    @patch(f"{MODULE}.requests.get")
    def test_rate_limit_retries_then_succeeds(
        self, mock_get: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """Must wait the base delay and retry after a rate limit response."""
        mock_get.side_effect = [
            _response(429),
            _response(200, {"data": [{"id": 2}]}),
        ]

        result = fetch_page(
            "https://api.example.com",
            params={"pagina": 1},
            rate_limit_status=429,
            delay=0.5,
        )

        assert result == {"data": [{"id": 2}]}
        assert mock_get.call_count == 2
        mock_sleep.assert_called_once_with(0.5)

    @patch(f"{MODULE}.time.sleep")
    @patch(f"{MODULE}.requests.get")
    def test_rate_limit_exhausted_returns_none(
        self, mock_get: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """Must return None when every attempt hits the rate limit."""
        mock_get.return_value = _response(429)

        result = fetch_page(
            "https://api.example.com",
            params={"pagina": 1},
            retries=2,
            rate_limit_status=429,
            delay=0.1,
        )

        assert result is None
        assert mock_get.call_count == 2
        assert mock_sleep.call_count == 2

    @patch(f"{MODULE}.time.sleep")
    @patch(f"{MODULE}.requests.get")
    def test_rate_limit_uses_longer_default_delay(
        self, mock_get: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """With the default delay, rate limit waits must use the long delay."""
        mock_get.side_effect = [
            _response(429),
            _response(200, {"data": []}),
        ]

        result = fetch_page(
            "https://api.example.com",
            params={"pagina": 1},
            rate_limit_status=429,
        )

        assert result == {"data": []}
        assert mock_get.call_count == 2
        mock_sleep.assert_called_once_with(RATE_LIMIT_DELAY)

    @patch(f"{MODULE}.time.sleep")
    @patch(f"{MODULE}.requests.get")
    def test_fatal_status_raises_immediately(
        self, mock_get: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """Fatal statuses must abort on the first attempt without retrying."""
        response = _response(400)
        response.raise_for_status.side_effect = _http_error(400)
        mock_get.return_value = response

        with pytest.raises(requests.HTTPError):
            fetch_page("https://api.example.com", params={"pagina": 1})

        mock_get.assert_called_once()
        mock_sleep.assert_not_called()

    @patch(f"{MODULE}.time.sleep")
    @patch(f"{MODULE}.requests.get")
    def test_transient_http_error_raises_after_retries(
        self, mock_get: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """Non-fatal HTTP errors must retry with backoff and re-raise."""
        response = _response(500)
        response.raise_for_status.side_effect = _http_error(500)
        mock_get.return_value = response

        with pytest.raises(requests.HTTPError):
            fetch_page(
                "https://api.example.com",
                params={"pagina": 1},
                retries=3,
                delay=0.1,
            )

        assert mock_get.call_count == 3
        # Exponential backoff: 0.1 then 0.2 (no sleep after the last attempt)
        assert [c.args[0] for c in mock_sleep.call_args_list] == [0.1, 0.2]

    @patch(f"{MODULE}.time.sleep")
    @patch(f"{MODULE}.requests.get")
    def test_http_error_without_response_is_transient(
        self, mock_get: MagicMock, _: MagicMock
    ) -> None:
        """An HTTPError without a response must be treated as transient."""
        response = _response(500)
        response.raise_for_status.side_effect = _http_error(None)
        mock_get.return_value = response

        with pytest.raises(requests.HTTPError):
            fetch_page(
                "https://api.example.com",
                params={"pagina": 1},
                retries=2,
                delay=0.1,
            )

        assert mock_get.call_count == 2

    @patch(f"{MODULE}.time.sleep")
    @patch(f"{MODULE}.requests.get")
    def test_network_error_raises_after_retries(
        self, mock_get: MagicMock, _: MagicMock
    ) -> None:
        """Network errors must retry and re-raise the last exception."""
        mock_get.side_effect = requests.ConnectionError("dns failure")

        with pytest.raises(requests.ConnectionError):
            fetch_page(
                "https://api.example.com",
                params={"pagina": 1},
                retries=2,
                delay=0.1,
            )

        assert mock_get.call_count == 2

    @patch(f"{MODULE}.time.sleep")
    @patch(f"{MODULE}.requests.get")
    def test_network_error_recovers_on_retry(
        self, mock_get: MagicMock, _: MagicMock
    ) -> None:
        """Must return the JSON when a later attempt succeeds."""
        mock_get.side_effect = [
            requests.Timeout("slow"),
            _response(200, {"data": []}),
        ]

        result = fetch_page(
            "https://api.example.com",
            params={"pagina": 1},
            retries=2,
            delay=0.1,
        )

        assert result == {"data": []}
        assert mock_get.call_count == 2

    @patch(f"{MODULE}.time.sleep")
    @patch(f"{MODULE}.requests.get")
    def test_retry_status_retries_then_succeeds(
        self, mock_get: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """Transient server errors must wait the long delay and retry."""
        mock_get.side_effect = [
            _response(504),
            _response(200, {"data": [{"id": 3}]}),
        ]

        result = fetch_page(
            "https://api.example.com",
            params={"pagina": 1},
            retry_statuses=(502, 503, 504),
        )

        assert result == {"data": [{"id": 3}]}
        assert mock_get.call_count == 2
        mock_sleep.assert_called_once_with(RATE_LIMIT_DELAY)

    @patch(f"{MODULE}.time.sleep")
    @patch(f"{MODULE}.requests.get")
    def test_retry_status_raises_after_exhaustion(
        self, mock_get: MagicMock, mock_sleep: MagicMock
    ) -> None:
        """A persistent transient error must raise, not return None."""
        mock_get.return_value = _response(504)

        with pytest.raises(requests.HTTPError):
            fetch_page(
                "https://api.example.com",
                params={"pagina": 1},
                retries=2,
                retry_statuses=(502, 503, 504),
            )

        assert mock_get.call_count == 2
        assert mock_sleep.call_count == 2
