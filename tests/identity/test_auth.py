from src.identity.auth import AuthContext, create_system_auth, create_kevin_auth, SCOPE_DEFINITIONS, DEFAULT_SCOPES_BY_ACTOR, AuthorizationError, check_scope

def test_kevin_has_all_scopes():
    auth = create_kevin_auth(session_id="s1")
    assert "identity.read" in auth.scopes
    assert "identity.erase" in auth.scopes
    assert "sensitive.restricted.explicit" in auth.scopes

def test_system_auth_has_limited_scopes():
    auth = create_system_auth()
    assert "identity.read" in auth.scopes
    assert "identity.erase" not in auth.scopes

def test_check_scope_raises_on_missing():
    auth = create_system_auth()
    import pytest
    with pytest.raises(AuthorizationError):
        check_scope(auth, "identity.erase")

def test_check_scope_passes_when_present():
    auth = create_kevin_auth(session_id="s1")
    check_scope(auth, "identity.read")  # should not raise
