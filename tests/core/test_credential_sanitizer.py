import pytest
from src.core.credential_sanitizer import sanitize_env, redact_secrets, truncate_head_tail


class TestSanitizeEnv:
    def test_passes_safe_vars(self):
        env = {"PATH": "/usr/bin", "HOME": "/home/user", "LANG": "en_US.UTF-8"}
        result = sanitize_env(env)
        assert result == env

    def test_strips_api_keys(self):
        env = {
            "PATH": "/usr/bin",
            "LLM_API_KEY": "sk-secret",
            "TAVILY_API_KEY": "tvly-xxx",
            "NIM_API_KEY": "nvapi-xxx",
        }
        result = sanitize_env(env)
        assert "PATH" in result
        assert "LLM_API_KEY" not in result
        assert "TAVILY_API_KEY" not in result
        assert "NIM_API_KEY" not in result

    def test_strips_credential_patterns(self):
        env = {
            "PATH": "/usr/bin",
            "MY_PASSWORD": "hunter2",
            "DB_TOKEN": "tok-abc",
            "AWS_SECRET_ACCESS_KEY": "wJalrXUtnFEMI",
        }
        result = sanitize_env(env)
        assert "MY_PASSWORD" not in result
        assert "DB_TOKEN" not in result
        assert "AWS_SECRET_ACCESS_KEY" not in result

    def test_passes_python_vars(self):
        env = {
            "PATH": "/usr/bin",
            "PYTHONPATH": "/app",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        result = sanitize_env(env)
        assert "PYTHONPATH" in result
        assert "PYTHONDONTWRITEBYTECODE" in result

    def test_passes_tz(self):
        env = {"PATH": "/usr/bin", "TZ": "Asia/Shanghai"}
        result = sanitize_env(env)
        assert result["TZ"] == "Asia/Shanghai"

    def test_strips_proxy_vars_when_strict(self):
        env = {
            "PATH": "/usr/bin",
            "http_proxy": "http://proxy:8080",
            "HTTPS_PROXY": "http://proxy:8080",
        }
        result = sanitize_env(env, allow_network=False)
        assert "http_proxy" not in result
        assert "HTTPS_PROXY" not in result

    def test_passes_proxy_vars_when_network_allowed(self):
        env = {
            "PATH": "/usr/bin",
            "http_proxy": "http://proxy:8080",
        }
        result = sanitize_env(env, allow_network=True)
        assert "http_proxy" in result

    def test_empty_env(self):
        assert sanitize_env({}) == {}

    def test_none_env_returns_safe_default(self):
        result = sanitize_env(None)
        assert isinstance(result, dict)


class TestRedactSecrets:
    def test_redacts_github_pat(self):
        text = "token is ghp_ABCDEFghijklmnopqrstuvwxyz0123456789abcdef"
        result = redact_secrets(text)
        assert "ghp_" not in result
        assert "[REDACTED" in result

    def test_redacts_aws_key(self):
        text = "key=AKIAIOSFODNN7EXAMPLE"
        result = redact_secrets(text)
        assert "AKIAIOSFODNN7EXAMPLE" not in result

    def test_redacts_jwt(self):
        text = "auth: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        result = redact_secrets(text)
        assert "eyJhbGciOi" not in result

    def test_redacts_private_key_block(self):
        text = "key:\n-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA\n-----END RSA PRIVATE KEY-----\ndone"
        result = redact_secrets(text)
        assert "MIIEpAIBAAKCAQEA" not in result

    def test_redacts_generic_sk_prefix(self):
        text = "api_key = sk-proj-abcdef1234567890abcdef"
        result = redact_secrets(text)
        assert "sk-proj-" not in result

    def test_preserves_normal_text(self):
        text = "Hello world, this is a normal output\nwith line 2"
        assert redact_secrets(text) == text

    def test_empty_input(self):
        assert redact_secrets("") == ""

    def test_redacts_nvapi_key(self):
        text = "key=nvapi-abcdef1234567890abcdef"
        result = redact_secrets(text)
        assert "nvapi-" not in result


class TestTruncateHeadTail:
    def test_short_text_unchanged(self):
        text = "short output"
        assert truncate_head_tail(text, max_chars=1000) == text

    def test_long_text_truncated(self):
        head = "HEAD\n" * 100   # 500 chars
        middle = "M" * 5000
        tail = "\nTAIL" * 100  # 500 chars
        text = head + middle + tail
        result = truncate_head_tail(text, max_chars=2000)
        assert len(result) <= 2200  # allow small overhead for marker
        assert "HEAD" in result
        assert "TAIL" in result
        assert "truncated" in result.lower()

    def test_tail_bias(self):
        """Tail portion should be larger than head (results usually at end)."""
        lines = [f"line-{i:04d}" for i in range(1000)]
        text = "\n".join(lines)
        result = truncate_head_tail(text, max_chars=500)
        assert "line-0999" in result
        assert "line-0000" in result

    def test_empty_text(self):
        assert truncate_head_tail("", max_chars=100) == ""

    def test_exact_limit(self):
        text = "x" * 1000
        assert truncate_head_tail(text, max_chars=1000) == text
