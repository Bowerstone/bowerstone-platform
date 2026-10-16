"""Tests for bowerstone_engine.apps: the rules every app manifest must meet."""

import re
from dataclasses import replace
from typing import Any

import pytest

from bowerstone_engine.apps import AppManifest, Permission
from bowerstone_engine.errors import AppManifestError

VIEW = Permission("ledger.view", "View the ledger", ("owner", "staff", "read_only"))
A_MANIFEST = AppManifest(
    key="ledger",
    name="Ledger",
    version="0.1.0",
    migrations_package="bowerstone_ledger.migrations",
    requires=(),
    engine_versions=">=0.1,<0.2",
    ui_versions=">=0.1,<0.2",
    permissions=(VIEW,),
    data_deletable=False,
)


def refused(match: str, **changes: Any) -> None:
    """Assert that changing the valid manifest this way is refused.

    Args:
        match: Text the error message must contain.
        changes: The fields to change.

    Example:
        >>> refused("is not a valid app key", key="Ledger")
    """
    with pytest.raises(AppManifestError, match=re.escape(match)):
        replace(A_MANIFEST, **changes)


def test_a_valid_manifest_is_accepted() -> None:
    """The example manifest meets every rule."""
    assert A_MANIFEST.key == "ledger"


@pytest.mark.parametrize(
    "key",
    ["Ledger", "l", "1ledger", "ledger-x", "ledger x", "", "a" * 32, "léger", None],
)
def test_an_invalid_key_is_refused(key: object) -> None:
    """A key is 2 to 31 lower case letters, digits, and underscores, from a letter."""
    refused("is not a valid app key", key=key)


@pytest.mark.parametrize(
    "key", ["core", "public", "api", "apps", "pg_ledger", "pg_toast"]
)
def test_a_reserved_key_is_refused(key: str) -> None:
    """Keys the engine or PostgreSQL already use cannot name an app."""
    refused("is reserved", key=key)


@pytest.mark.parametrize("key", ["ab", "a" * 31, "invoicing_2"])
def test_keys_at_the_limits_are_accepted(key: str) -> None:
    """2 and 31 characters, and digits and underscores after the first letter, are fine."""
    assert replace(A_MANIFEST, key=key, permissions=()).key == key


def test_a_permission_of_another_app_is_refused() -> None:
    """Every permission starts with its app's key."""
    other = Permission("billing.view", "View billing", ("owner",))
    refused("must start with ledger.", permissions=(other,))


def test_a_permission_listed_twice_is_refused() -> None:
    """Each permission is declared once."""
    refused("listed twice", permissions=(VIEW, VIEW))


@pytest.mark.parametrize(
    ("key", "description", "roles", "match"),
    [
        ("ledger", "View", ("owner",), "is not a valid permission key"),
        ("ledger.", "View", ("owner",), "is not a valid permission key"),
        ("Ledger.view", "View", ("owner",), "is not a valid permission key"),
        ("ledger.View", "View", ("owner",), "is not a valid permission key"),
        ("ledger.view", "", ("owner",), "1 to 200 characters"),
        ("ledger.view", "x" * 201, ("owner",), "1 to 200 characters"),
        ("ledger.view", "View", ["owner"], "must be a tuple"),
        ("ledger.view", "View", ("Owner",), "is not a valid role key"),
        ("ledger.view", "View", ("owner", "owner"), "listed twice"),
    ],
)
def test_a_malformed_permission_is_refused(
    key: str, description: str, roles: Any, match: str
) -> None:
    """Keys, descriptions, and default roles are checked when a permission is made."""
    with pytest.raises(AppManifestError, match=re.escape(match)):
        Permission(key, description, roles)


@pytest.mark.parametrize(
    ("requires", "match"),
    [
        (("ledger",), "cannot require itself"),
        (("Billing",), "is not a valid app key"),
        (("billing", "billing"), "listed twice"),
        (["billing"], "must be a tuple"),
    ],
)
def test_a_bad_requires_list_is_refused(requires: Any, match: str) -> None:
    """requires is a tuple of other, distinct, valid app keys."""
    refused(match, requires=requires)


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"version": "one"}, "is not a valid version"),
        ({"version": None}, "is not a valid version"),
        ({"engine_versions": ">=x"}, "is not a valid version range"),
        ({"engine_versions": ""}, "must be a version range"),
        ({"ui_versions": "0.1"}, "is not a valid version range"),
        ({"name": ""}, "1 to 100 characters"),
        ({"name": "x" * 101}, "1 to 100 characters"),
        ({"migrations_package": "not a package"}, "is not a package name"),
        ({"migrations_package": "1ledger.migrations"}, "is not a package name"),
        ({"data_deletable": "no"}, "must be True or False"),
        ({"permissions": [VIEW]}, "must be a tuple"),
    ],
)
def test_other_malformed_fields_are_refused(
    changes: dict[str, Any], match: str
) -> None:
    """Versions, ranges, the name, the package, and the flags are all checked."""
    refused(match, **changes)


@pytest.mark.parametrize(
    ("engine_version", "supported"),
    [
        ("0.1.0", True),
        ("0.1.99", True),
        ("0.2.0", False),
        ("0.0.9", False),
        ("1.0.0", False),
    ],
)
def test_the_engine_range_is_read_as_pip_reads_it(
    engine_version: str, supported: bool
) -> None:
    """>=0.1,<0.2 takes every 0.1 release and nothing else."""
    assert A_MANIFEST.supports_engine(engine_version) is supported
