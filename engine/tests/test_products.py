"""Product manifests: their rules, and finding them through entry points."""

from dataclasses import replace
from importlib import metadata

import pytest

from bowerstone_engine.products import (
    Permission,
    ProductManifest,
    engine_version,
    load_products,
)
from bowerstone_example_notes.manifest import PRODUCT as NOTES
from bowerstone_example_tasks.manifest import PRODUCT as TASKS

ENGINE = "0.3.1"


def test_the_example_products_are_found_through_their_entry_points() -> None:
    products = load_products()

    assert products == {"notes": NOTES, "tasks": TASKS}
    assert TASKS.requires == ("notes",)


def test_the_engine_knows_its_own_version() -> None:
    assert engine_version() == metadata.version("bowerstone-engine")


def test_the_example_products_break_no_rule() -> None:
    assert NOTES.problems(engine_version()) == []
    assert TASKS.problems(engine_version()) == []


def manifest(**changes: object) -> ProductManifest:
    """The notes manifest, made to work with engine 0.3.1, with `changes` made."""
    base = replace(NOTES, engine_versions=">=0.3,<0.4")
    return replace(base, **changes)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "key",
    ["Notes", "n", "notes-app", "1notes", "a" * 32, ""],
)
def test_a_badly_formed_key_is_refused(key: str) -> None:
    assert manifest(key=key, permissions=()).problems(ENGINE) == [
        f"The product key {key!r} must be 2 to 31 characters: a lower-case letter, "
        "then lower-case letters, digits, or underscores."
    ]


@pytest.mark.parametrize("key", ["core", "public", "api", "products", "pg_notes"])
def test_a_reserved_key_is_refused(key: str) -> None:
    assert manifest(key=key, permissions=()).problems(ENGINE) == [
        f"The product key {key!r} is reserved."
    ]


def test_a_key_of_31_characters_is_allowed() -> None:
    assert manifest(key="a" * 31, permissions=()).problems(ENGINE) == []


@pytest.mark.parametrize(
    ("engine_versions", "allowed"),
    [
        (">=0.3,<0.4", True),
        ("==0.3.*", True),
        ("~=0.3.0", True),
        (">=0.1,<0.3", False),
        (">=0.4", False),
    ],
)
def test_the_engine_version_must_be_in_the_products_range(
    engine_versions: str, allowed: bool
) -> None:
    problems = manifest(engine_versions=engine_versions).problems(ENGINE)
    assert problems == (
        []
        if allowed
        else [
            f"The product notes works with engine versions {engine_versions}, "
            "and this engine is 0.3.1."
        ]
    )


def test_a_development_build_of_the_engine_counts_as_its_version() -> None:
    assert manifest().problems("0.3.2.dev1") == []


@pytest.mark.parametrize("field", ["engine_versions", "ui_versions"])
@pytest.mark.parametrize("value", ["", "0.3", "about 0.3"])
def test_a_range_that_is_not_a_range_is_refused(field: str, value: str) -> None:
    [problem] = manifest(**{field: value}).problems(ENGINE)
    assert "are not a version range" in problem


def test_a_version_that_is_not_a_version_is_refused() -> None:
    assert manifest(version="first").problems(ENGINE) == [
        "The product notes's version 'first' is not a version number."
    ]


def test_every_permission_starts_with_the_products_key() -> None:
    problems = manifest(
        permissions=(
            Permission("accounting.post", "Post entries", ("owner",)),
            Permission("notes", "Everything", ("owner",)),
            Permission("notes.View", "View", ("owner",)),
        )
    ).problems(ENGINE)

    assert problems == [
        f"The product notes's permission {key!r} must start with notes., "
        "as in notes.view."
        for key in ("accounting.post", "notes", "notes.View")
    ]


def test_permission_mistakes_are_refused() -> None:
    problems = manifest(
        permissions=(
            Permission("notes.view", "View notes", ("owner",)),
            Permission("notes.view", "View notes again", ("owner",)),
            Permission("notes.edit", "  ", ("Owner",)),
        )
    ).problems(ENGINE)

    assert problems == [
        "The product notes lists the permission notes.view more than once.",
        "The permission notes.edit has no description.",
        "The permission notes.edit names the role 'Owner', which is not a role name.",
    ]


def test_other_mistakes_are_refused() -> None:
    problems = manifest(
        name=" ", migrations_package="", requires=("notes", "tasks", "tasks")
    ).problems(ENGINE)

    assert problems == [
        "The product notes has no name.",
        "The product notes names no migrations package.",
        "The product notes needs itself.",
        "The product notes lists a product it needs twice.",
    ]
