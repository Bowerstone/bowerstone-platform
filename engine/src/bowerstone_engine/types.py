"""Types the engine and products share: money and list pages.

Products may import this module. It is part of the engine's SDK.
"""

import re
from collections.abc import Callable, Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from pydantic import BaseModel, PlainSerializer, PlainValidator, WithJsonSchema

# Up to 19 digits, 4 of them after the point, as Postgres numeric(19, 4) stores.
MONEY_PATTERN = r"^-?\d{1,15}(\.\d{1,4})?$"
_MONEY = re.compile(MONEY_PATTERN)
CENT = Decimal("0.01")

MAX_PAGE_SIZE = 200


def _parse_money(value: object) -> Decimal:
    if isinstance(value, Decimal):
        value = format(value, "f")
    if not isinstance(value, str):
        # JSON numbers become floats, which cannot hold every amount exactly.
        raise ValueError('amounts must be sent as strings, such as "10.50"')
    if not _MONEY.fullmatch(value):
        raise ValueError(
            "an amount has up to 15 digits before the point and 4 after it, "
            'such as "1234.50"'
        )
    return Decimal(value)


def _format_money(value: Decimal) -> str:
    return format(value, "f")


Money = Annotated[
    Decimal,
    PlainValidator(_parse_money),
    PlainSerializer(_format_money, return_type=str),
    WithJsonSchema({"type": "string", "pattern": MONEY_PATTERN, "examples": ["10.50"]}),
]
"""An exact amount, sent and received as a string. Floats are refused."""


def round_money(amount: Decimal, places: Decimal = CENT) -> Decimal:
    """Rounds `amount` to the cent, half-up: a half rounds away from zero.

    10.005 becomes 10.01, and -10.005 becomes -10.01.
    """
    return amount.quantize(places, rounding=ROUND_HALF_UP)


def allocate_money(
    total: Decimal, weights: Sequence[Decimal], places: Decimal = CENT
) -> list[Decimal]:
    """Splits `total` in proportion to `weights`, each share rounded half-up.

    The last share takes the remainder, so the shares always add up to the
    total: 100.00 split three ways is 33.33, 33.33, and 33.34.
    """
    if not weights:
        raise ValueError("there must be at least one share")
    if any(weight < 0 for weight in weights) or sum(weights) == 0:
        raise ValueError(
            "the weights must be zero or more, and add up to more than zero"
        )
    total = round_money(total, places)
    whole = sum(weights)
    shares = [round_money(total * weight / whole, places) for weight in weights[:-1]]
    return [*shares, total - sum(shares, Decimal(0))]


class Page[T](BaseModel):
    """One page of a list. Pass `next_after` back as `after` to get the next page.

    `next_after` is empty on the last page.
    """

    items: list[T]
    next_after: str | None = None


def page_of[T](rows: Sequence[T], limit: int, key: Callable[[T], str]) -> Page[T]:
    """Makes a page from up to `limit + 1` rows, read in key order.

    Reading one row more than the page holds shows whether another page
    follows, without counting the whole list.
    """
    items = list(rows[:limit])
    more = len(rows) > limit
    return Page[T](items=items, next_after=key(items[-1]) if more and items else None)
