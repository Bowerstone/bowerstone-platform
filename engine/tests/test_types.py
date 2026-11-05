"""Money and pages."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel, TypeAdapter, ValidationError

from bowerstone_engine.types import Money, allocate_money, page_of, round_money

MONEY = TypeAdapter(Money)


@pytest.mark.parametrize(
    ("text", "amount"),
    [
        ("10.50", Decimal("10.50")),
        ("0", Decimal("0")),
        ("-0.0001", Decimal("-0.0001")),
        ("999999999999999.9999", Decimal("999999999999999.9999")),
    ],
)
def test_money_is_read_from_a_string_exactly(text: str, amount: Decimal) -> None:
    assert MONEY.validate_json(f'"{text}"') == amount


@pytest.mark.parametrize("json", ["10.5", "10", "1e2", "true", "null"])
def test_money_refuses_anything_but_a_string(json: str) -> None:
    with pytest.raises(ValidationError):
        MONEY.validate_json(json)


@pytest.mark.parametrize(
    "text",
    [
        "1e3",
        "10.12345",
        "1234567890123456",
        "",
        " 10",
        "+10",
        "10.",
        ".5",
        "ten",
        "NaN",
    ],
)
def test_money_refuses_a_string_that_is_not_an_amount(text: str) -> None:
    with pytest.raises(ValidationError):
        MONEY.validate_json(f'"{text}"')


def test_money_is_sent_as_a_string_with_no_exponent() -> None:
    assert MONEY.dump_json(Decimal("10.5000")) == b'"10.5000"'
    assert MONEY.dump_json(Decimal("1E+3")) == b'"1000"'
    assert MONEY.json_schema()["type"] == "string"


def test_point_one_plus_point_two_is_exactly_point_three() -> None:
    assert MONEY.validate_json('"0.1"') + MONEY.validate_json('"0.2"') == Decimal("0.3")


@pytest.mark.parametrize(
    ("amount", "rounded"),
    [
        ("10.005", "10.01"),
        ("-10.005", "-10.01"),
        ("10.004", "10.00"),
        ("10.015", "10.02"),
        ("0.125", "0.13"),
        ("999999999999999.9950", "1000000000000000.00"),
    ],
)
def test_round_money_rounds_half_up_to_the_cent(amount: str, rounded: str) -> None:
    assert round_money(Decimal(amount)) == Decimal(rounded)


def test_allocate_money_puts_the_remainder_on_the_last_share() -> None:
    thirds = allocate_money(Decimal("100.00"), [Decimal(1)] * 3)
    assert thirds == [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")]

    weighted = allocate_money(Decimal("10.00"), [Decimal(1), Decimal(2)])
    assert weighted == [Decimal("3.33"), Decimal("6.67")]


@given(
    total=st.decimals(min_value=-(10**12), max_value=10**12, places=2),
    weights=st.lists(
        st.decimals(min_value=0, max_value=1000, places=2), min_size=1, max_size=20
    ).filter(lambda weights: sum(weights) > 0),
)
def test_allocated_shares_always_add_up_to_the_total(
    total: Decimal, weights: list[Decimal]
) -> None:
    shares = allocate_money(total, weights)
    assert sum(shares) == total
    assert len(shares) == len(weights)
    assert all(share == round_money(share) for share in shares)


@pytest.mark.parametrize("weights", [[], [Decimal(0)], [Decimal(1), Decimal(-1)]])
def test_allocate_money_refuses_weights_it_cannot_split_by(
    weights: list[Decimal],
) -> None:
    with pytest.raises(ValueError, match=r"share|weights"):
        allocate_money(Decimal("1.00"), weights)


class Item(BaseModel):
    id: int


@pytest.mark.parametrize(
    ("rows", "limit", "ids", "next_after"),
    [
        (4, 3, [1, 2, 3], "3"),
        (3, 3, [1, 2, 3], None),
        (2, 3, [1, 2], None),
        (0, 3, [], None),
    ],
)
def test_a_page_shows_whether_another_follows(
    rows: int, limit: int, ids: list[int], next_after: str | None
) -> None:
    page = page_of(
        [Item(id=i) for i in range(1, rows + 1)], limit, key=lambda item: str(item.id)
    )
    assert [item.id for item in page.items] == ids
    assert page.next_after == next_after
