from __future__ import annotations

import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]


def _install_azure_stubs() -> None:
    azure_module = types.ModuleType("azure")
    functions_module = types.ModuleType("azure.functions")
    cosmos_module = types.ModuleType("azure.cosmos")
    identity_module = types.ModuleType("azure.identity")
    core_module = types.ModuleType("azure.core")
    credentials_module = types.ModuleType("azure.core.credentials")
    eventgrid_module = types.ModuleType("azure.eventgrid")
    monitor_module = types.ModuleType("azure.monitor")
    opentelemetry_module = types.ModuleType("azure.monitor.opentelemetry")

    class _CosmosHttpResponseError(Exception):
        pass

    class _CosmosClient:
        @classmethod
        def from_connection_string(cls, connection_string: str):
            raise AssertionError("CosmosClient.from_connection_string should be mocked in tests.")

    class _DefaultAzureCredential:
        def get_token(self, scope: str):
            return types.SimpleNamespace(token="stub-token")

    class _AzureKeyCredential:
        def __init__(self, key: str) -> None:
            self.key = key

    class _EventGridEvent:
        def __init__(self, **kwargs) -> None:
            self.__dict__.update(kwargs)

    class _EventGridPublisherClient:
        def __init__(self, endpoint: str, credential: object) -> None:
            self.endpoint = endpoint
            self.credential = credential

        def send(self, events) -> None:
            self.sent_events = list(events)

    class _StubEventGridEvent:
        def __init__(
            self,
            *,
            id: str = "evt-1",
            event_type: str = "GracePeriodExpired",
            subject: str = "/vaults/vault-1",
            topic: str = "topic-1",
            data: object | None = None,
        ) -> None:
            self.id = id
            self.event_type = event_type
            self.subject = subject
            self.topic = topic
            self._data = {} if data is None else data

        def get_json(self):
            if isinstance(self._data, Exception):
                raise self._data
            return self._data

    class _StubTimerRequest:
        def __init__(self, past_due: bool = False) -> None:
            self.past_due = past_due

    functions_module.EventGridEvent = _StubEventGridEvent
    functions_module.TimerRequest = _StubTimerRequest
    cosmos_module.CosmosClient = _CosmosClient
    cosmos_module.exceptions = types.SimpleNamespace(
        CosmosHttpResponseError=_CosmosHttpResponseError
    )
    identity_module.DefaultAzureCredential = _DefaultAzureCredential
    credentials_module.AzureKeyCredential = _AzureKeyCredential
    eventgrid_module.EventGridEvent = _EventGridEvent
    eventgrid_module.EventGridPublisherClient = _EventGridPublisherClient
    opentelemetry_module.configure_azure_monitor = lambda **kwargs: None
    monitor_module.opentelemetry = opentelemetry_module
    core_module.credentials = credentials_module
    azure_module.functions = functions_module
    azure_module.cosmos = cosmos_module
    azure_module.identity = identity_module
    azure_module.core = core_module
    azure_module.eventgrid = eventgrid_module
    azure_module.monitor = monitor_module

    sys.modules["azure"] = azure_module
    sys.modules["azure.functions"] = functions_module
    sys.modules["azure.cosmos"] = cosmos_module
    sys.modules["azure.identity"] = identity_module
    sys.modules["azure.core"] = core_module
    sys.modules["azure.core.credentials"] = credentials_module
    sys.modules["azure.eventgrid"] = eventgrid_module
    sys.modules["azure.monitor"] = monitor_module
    sys.modules["azure.monitor.opentelemetry"] = opentelemetry_module


def _load_module(module_name: str, relative_path: str):
    _install_azure_stubs()
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class FakeVaultContainer:
    def __init__(self, items: list[dict] | None = None) -> None:
        self.items = list(items or [])
        self.query_calls: list[dict] = []
        self.replace_calls: list[dict] = []

    def query_items(self, **kwargs):
        self.query_calls.append(kwargs)
        return list(self.items)

    def replace_item(self, *, item, body):
        self.replace_calls.append({"item": item, "body": body})
        return body


class StartDeliveryJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load_module(
            "test_start_delivery_job_module",
            "functions/start_delivery_job/__init__.py",
        )

    def test_extract_vault_id_supports_payload_and_subject(self) -> None:
        self.assertEqual(
            self.module._extract_vault_id({"vault_id": "vault-a"}, "/vaults/vault-b"),
            "vault-a",
        )
        self.assertEqual(
            self.module._extract_vault_id({}, "/subscriptions/demo/vaults/vault-b"),
            "vault-b",
        )
        self.assertIsNone(self.module._extract_vault_id({}, "/subscriptions/demo"))

    def test_append_or_replace_env_updates_existing_and_preserves_other_values(self) -> None:
        container_definition = {
            "env": [
                {"name": "KEEP", "value": "1"},
                {"name": "VAULT_ID", "value": "old"},
            ]
        }

        self.module._append_or_replace_env(container_definition, "VAULT_ID", "vault-123")
        self.module._append_or_replace_env(container_definition, "DELIVERY_EVENT_ID", "evt-123")

        self.assertEqual(
            container_definition["env"],
            [
                {"name": "KEEP", "value": "1"},
                {"name": "VAULT_ID", "value": "vault-123"},
                {"name": "DELIVERY_EVENT_ID", "value": "evt-123"},
            ],
        )

    def test_start_delivery_job_builds_container_start_payload(self) -> None:
        calls: list[tuple[str, str, dict | None]] = []

        def _fake_management_request(method: str, url: str, payload: dict | None = None):
            calls.append((method, url, payload))
            if method == "GET":
                return {
                    "properties": {
                        "template": {
                            "containers": [
                                {
                                    "name": "worker",
                                    "env": [{"name": "KEEP", "value": "1"}],
                                }
                            ],
                            "initContainers": [{"name": "setup"}],
                        }
                    }
                }
            return {"name": "execution-42"}

        with patch.dict(
            os.environ,
            {
                "AZURE_SUBSCRIPTION_ID": "sub-123",
                "CONTAINER_APPS_RESOURCE_GROUP": "rg-demo",
                "CONTAINER_APPS_JOB_NAME": "job-demo",
                "CONTAINER_APPS_API_VERSION": "2024-03-01",
            },
            clear=False,
        ):
            with patch.object(
                self.module,
                "_management_request",
                side_effect=_fake_management_request,
            ):
                execution_name = self.module._start_delivery_job("vault-123", "evt-123")

        self.assertEqual(execution_name, "execution-42")
        self.assertEqual(calls[0][0], "GET")
        self.assertEqual(calls[1][0], "POST")
        post_payload = calls[1][2]
        self.assertIsNotNone(post_payload)
        self.assertEqual(post_payload["initContainers"], [{"name": "setup"}])
        self.assertEqual(
            post_payload["containers"][0]["env"],
            [
                {"name": "KEEP", "value": "1"},
                {"name": "VAULT_ID", "value": "vault-123"},
                {"name": "DELIVERY_EVENT_ID", "value": "evt-123"},
            ],
        )

    def test_main_persists_delivery_error_when_job_start_fails(self) -> None:
        event = self.module.func.EventGridEvent(
            id="evt-500",
            event_type="GracePeriodExpired",
            subject="/vaults/vault-123",
            data={"vault_id": "vault-123"},
        )
        update_calls: list[dict] = []

        def _fake_update_vault(vault_document: dict, update_data: dict) -> dict:
            update_calls.append(update_data)
            merged = dict(vault_document)
            merged.update(update_data)
            return merged

        with patch.object(self.module, "_get_vault", return_value={"id": "vault-123", "status": "active"}):
            with patch.object(self.module, "_update_vault", side_effect=_fake_update_vault):
                with patch.object(self.module, "_upsert_delivery"):
                    with patch.object(
                        self.module,
                        "_start_delivery_job",
                        side_effect=RuntimeError("job start failed"),
                    ):
                        self.module.main(event)

        self.assertEqual(update_calls[0]["status"], "delivery_initiated")
        self.assertEqual(update_calls[1]["delivery_error"], "job start failed")


class ProcessEventsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load_module(
            "test_process_events_module",
            "functions/process_events/__init__.py",
        )

    def test_update_vault_status_requires_user_id_partition_key(self) -> None:
        container = FakeVaultContainer([{"id": "vault-123", "status": "active"}])

        with patch.object(self.module, "_get_vaults_container", return_value=container):
            with self.assertRaisesRegex(ValueError, "missing user_id partition key"):
                self.module._update_vault_status_to_delivery_initiated(
                    vault_id="vault-123",
                    event_id="evt-1",
                    event_type="GracePeriodExpired",
                )

    def test_main_processes_supported_event_and_notifies(self) -> None:
        event = self.module.func.EventGridEvent(
            id="evt-1",
            event_type="GracePeriodExpired",
            subject="/vaults/vault-123",
            data={"vault_id": "vault-123"},
        )
        updated_vault = {
            "id": "vault-123",
            "status": "delivery_initiated",
            "recipients": ["alice@example.com"],
        }

        with patch.object(
            self.module,
            "_update_vault_status_to_delivery_initiated",
            return_value=updated_vault,
        ) as update_mock:
            with patch.object(self.module, "_send_mocked_email_notification") as notify_mock:
                self.module.main(event)

        update_mock.assert_called_once_with(
            vault_id="vault-123",
            event_id="evt-1",
            event_type="GracePeriodExpired",
        )
        notify_mock.assert_called_once_with(updated_vault)

    def test_main_skips_unsupported_event_type(self) -> None:
        event = self.module.func.EventGridEvent(
            id="evt-2",
            event_type="SomeOtherEvent",
            subject="/vaults/vault-123",
            data={"vault_id": "vault-123"},
        )

        with patch.object(self.module, "_update_vault_status_to_delivery_initiated") as update_mock:
            self.module.main(event)

        update_mock.assert_not_called()


class CheckGracePeriodsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _load_module(
            "test_check_grace_periods_module",
            "functions/check_grace_periods/__init__.py",
        )

    def test_parse_iso_datetime_supports_z_suffix_and_naive_values(self) -> None:
        parsed_z = self.module._parse_iso_datetime("2026-05-08T12:00:00Z")
        parsed_naive = self.module._parse_iso_datetime("2026-05-08T12:00:00")

        self.assertIsNotNone(parsed_z)
        self.assertIsNotNone(parsed_naive)
        self.assertEqual(parsed_z.utcoffset().total_seconds(), 0)
        self.assertEqual(parsed_naive.utcoffset().total_seconds(), 0)

    def test_publish_expiration_event_sends_expected_payload(self) -> None:
        sent_events: list[object] = []

        class FakeEventGridClient:
            def send(self, events) -> None:
                sent_events.extend(events)

        expires_at = self.module.datetime(2026, 5, 8, 12, 0, tzinfo=self.module.timezone.utc)
        detected_at = self.module.datetime(2026, 5, 8, 12, 5, tzinfo=self.module.timezone.utc)
        vault_document = {
            "id": "vault-123",
            "user_id": "user-123",
            "grace_period_days": 7,
            "activation_requests": [{"id": "req-1"}, {"id": "req-2"}],
            "grace_period_started_at": "2026-05-01T12:00:00+00:00",
        }

        with patch.object(self.module, "_get_event_grid_client", return_value=FakeEventGridClient()):
            self.module._publish_expiration_event(vault_document, expires_at, detected_at)

        self.assertEqual(len(sent_events), 1)
        event = sent_events[0]
        self.assertEqual(event.subject, "/vaults/vault-123")
        self.assertEqual(event.event_type, "GracePeriodExpired")
        self.assertEqual(event.data["activation_request_count"], 2)
        self.assertEqual(event.data["user_id"], "user-123")

    def test_main_only_publishes_expired_vaults(self) -> None:
        published_ids: list[str] = []
        marked_ids: list[str] = []

        expired_vault = {
            "id": "vault-expired",
            "grace_period_expires_at": "2000-01-01T00:00:00Z",
            "grace_period_days": 7,
            "activation_requests": [],
        }
        future_vault = {
            "id": "vault-future",
            "grace_period_expires_at": "2999-01-01T00:00:00Z",
            "grace_period_days": 7,
            "activation_requests": [],
        }
        invalid_vault = {
            "id": "vault-invalid",
            "grace_period_expires_at": "not-a-date",
            "grace_period_days": 7,
            "activation_requests": [],
        }

        def _fake_publish(vault_document: dict, expires_at, detected_at) -> None:
            published_ids.append(vault_document["id"])

        def _fake_mark(vault_document: dict, published_at) -> None:
            marked_ids.append(vault_document["id"])

        with patch.object(
            self.module,
            "_query_expired_grace_period_vaults",
            return_value=[expired_vault, future_vault, invalid_vault],
        ):
            with patch.object(self.module, "_publish_expiration_event", side_effect=_fake_publish):
                with patch.object(self.module, "_mark_event_published", side_effect=_fake_mark):
                    self.module.main(self.module.func.TimerRequest(past_due=False))

        self.assertEqual(published_ids, ["vault-expired"])
        self.assertEqual(marked_ids, ["vault-expired"])


if __name__ == "__main__":
    unittest.main()
