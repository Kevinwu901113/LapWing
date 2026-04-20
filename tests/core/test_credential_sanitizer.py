import pytest
from src.core.credential_sanitizer import sanitize_env


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
