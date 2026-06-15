import pytest
from components.llm import LLM
from config.models import LLMConfig


@pytest.fixture
def llm():
    return LLM(
        LLMConfig(
            base_url="http://default-llm:8000/v1",
            api_key="default-key",
            model="default-model",
            temperature=0.3,
        )
    )


class TestExtractLlmOverrides:
    def test_no_override_uses_defaults(self, llm):
        request = {
            "model": "openrag-my-partition",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
        }
        payload, base_url, headers = llm._extract_llm_overrides(request)

        assert payload["model"] == "default-model"
        assert payload["temperature"] == 0.3
        assert base_url == "http://default-llm:8000/v1"
        assert headers["Authorization"] == "Bearer default-key"

    def test_model_override_applied(self, llm):
        request = {
            "model": "openrag-my-partition",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
            "metadata": {"llm_override": {"model": "custom-model"}},
        }
        payload, base_url, headers = llm._extract_llm_overrides(request)

        assert payload["model"] == "custom-model"
        # Endpoint and credentials always come from server config.
        assert base_url == "http://default-llm:8000/v1"
        assert headers["Authorization"] == "Bearer default-key"

    def test_client_base_url_and_api_key_override_ignored(self, llm):
        # SSRF / key-exfiltration guard: a client-supplied base_url/api_key
        # must never be honored.
        request = {
            "model": "openrag-my-partition",
            "stream": False,
            "metadata": {
                "llm_override": {
                    "base_url": "http://169.254.169.254/latest/meta-data",
                    "api_key": "attacker-key",
                    "model": "custom-model",
                }
            },
        }
        payload, base_url, headers = llm._extract_llm_overrides(request)

        assert payload["model"] == "custom-model"
        assert base_url == "http://default-llm:8000/v1"
        assert headers["Authorization"] == "Bearer default-key"

    def test_request_params_forwarded_to_payload(self, llm):
        request = {
            "model": "openrag-my-partition",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
            "max_tokens": 2048,
            "temperature": 0.9,
        }
        payload, _, _ = llm._extract_llm_overrides(request)

        assert payload["stream"] is True
        assert payload["max_tokens"] == 2048
        assert payload["temperature"] == 0.9
        assert payload["messages"] == [{"role": "user", "content": "hello"}]

    def test_metadata_without_llm_override_uses_defaults(self, llm):
        request = {
            "model": "openrag-my-partition",
            "stream": False,
            "metadata": {"use_map_reduce": True},
        }
        payload, base_url, headers = llm._extract_llm_overrides(request)

        assert payload["model"] == "default-model"
        assert base_url == "http://default-llm:8000/v1"
        assert headers["Authorization"] == "Bearer default-key"

    def test_llm_override_popped_from_metadata(self, llm):
        metadata = {
            "use_map_reduce": False,
            "llm_override": {"model": "custom"},
        }
        request = {"model": "x", "stream": False, "metadata": metadata}
        llm._extract_llm_overrides(request)

        assert "llm_override" not in metadata
        assert "use_map_reduce" in metadata
