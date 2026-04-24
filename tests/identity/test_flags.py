from src.identity.flags import IdentityFlags

def test_default_flags():
    flags = IdentityFlags()
    assert flags.parser_enabled is True
    assert flags.store_enabled is True
    assert flags.retriever_enabled is True
    assert flags.injector_enabled is False
    assert flags.gate_enabled is False
    assert flags.identity_system_killswitch is False

def test_killswitch_overrides_components():
    flags = IdentityFlags(identity_system_killswitch=True)
    assert flags.is_active("parser") is False
    assert flags.is_active("store") is False

def test_component_disabled_independently():
    flags = IdentityFlags(parser_enabled=False)
    assert flags.is_active("parser") is False
    assert flags.is_active("store") is True

def test_current_snapshot():
    flags = IdentityFlags()
    snap = flags.current()
    assert isinstance(snap, dict)
    assert "parser_enabled" in snap
    assert "identity_system_killswitch" in snap
