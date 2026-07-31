"""Infrastructure-level storage failures."""


class StorageUnavailableError(RuntimeError):
    """Raised when SQLite cannot be opened, migrated, or validated safely."""
