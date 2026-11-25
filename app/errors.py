"""The one error class the modules share (#844). A module raises `KeyError` (not found), `PermissionError` (not allowed),
`ValueError` (refused input) or `Conflict` (valid, but the stored state says no); `main._call` turns them into 404, 403, 422
and 409. One class, not one per module, so the wrapper has one thing to catch."""


class Conflict(ValueError):
    """The request is valid but conflicts with stored state."""
