"""Optional server-side durable persistence adapters.

No adapter in this package is reachable from the browser.  They are deliberately
opt-in because a local research run must not claim durable external storage merely
because a module can be imported.
"""
from .supabase import (
    SupabaseHttpResponse,
    SupabaseLifecycleStore,
    SupabasePersistenceConfig,
    SupabasePersistenceError,
    UrllibSupabaseTransport,
)

__all__ = [
    "SupabaseHttpResponse",
    "SupabaseLifecycleStore",
    "SupabasePersistenceConfig",
    "SupabasePersistenceError",
    "UrllibSupabaseTransport",
]
