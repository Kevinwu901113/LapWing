from .settings import LapwingSettings


def get_settings() -> LapwingSettings:
    from .settings import get_settings as _gs
    return _gs()


def reload_settings() -> LapwingSettings:
    from .settings import reload_settings as _rs
    return _rs()


__all__ = ["LapwingSettings", "get_settings", "reload_settings"]
