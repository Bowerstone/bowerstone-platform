"""Errors that become the API's error shape.

Every error response is {"rule": "...", "message": "..."}. The rule says which
rule was broken, for programs. The message says it in plain words, for people.
Input errors also list each field.

Products may import this module. It is part of the engine's SDK.
"""

from collections.abc import Mapping


class BowerstoneError(Exception):
    """An error the API reports in its error shape, with its own status."""

    status = 500
    rule = "unexpected"

    def __init__(self, message: str, *, rule: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if rule is not None:
            self.rule = rule


class RuleError(BowerstoneError):
    """A broken rule, such as an unbalanced entry. Status 422."""

    status = 422

    def __init__(self, rule: str, message: str) -> None:
        super().__init__(message, rule=rule)


class InputError(BowerstoneError):
    """Input that cannot be used, field by field. Status 422, rule "input"."""

    status = 422
    rule = "input"

    def __init__(self, fields: Mapping[str, str]) -> None:
        self.fields = dict(fields)
        super().__init__("Some of the input cannot be used.")


class NotFoundError(BowerstoneError):
    """A record that does not exist. Status 404, rule "not_found"."""

    status = 404
    rule = "not_found"


class PermissionDenied(BowerstoneError):
    """The user lacks the permission a route needs. Status 403, rule "permission"."""

    status = 403
    rule = "permission"
