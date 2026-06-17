"""Normalize JSON values that may be singular objects or lists."""


def as_list(value, *, label: str = "value") -> list[dict]:
    """Return a list of dicts from a single object or a list of objects."""
    if value is None:
        return []
    if isinstance(value, dict):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        raise TypeError(
            f"{label} must be an object or list of objects, got {type(value).__name__}"
        )

    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise TypeError(
                f"{label}[{index}] must be an object, got {type(item).__name__}"
            )

    return items
