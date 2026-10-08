"""Replace legacy Schedule categories with the confirmed five-category set.

Revision ID: 20261008_0002
Revises: 20260927_0001
"""

from typing import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision: str = "20261008_0002"
down_revision: str | None = "20260927_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEGACY_CATEGORIES = ("INVENTORY", "RETURN", "MEETING", "PERSONAL", "OTHER")
_NEW_CATEGORIES = ("INBOUND", "INVENTORY", "OUTBOUND", "RETURN", "OTHER")
_UNAPPROVED_LEGACY_CATEGORIES = ("PERSONAL", "PICKING")
_CONFIRMED_MEETING_IDS_OPTION = "confirmed_meeting_ids"


def _check_expression(categories: tuple[str, ...]) -> str:
    values = ", ".join(f"'{category}'" for category in categories)
    return f"category IN ({values})"


def _x_arguments() -> list[str]:
    command_options = getattr(op.get_context().config, "cmd_opts", None)
    return list(getattr(command_options, "x", None) or [])


def _confirmed_meeting_ids() -> set[str]:
    prefix = f"{_CONFIRMED_MEETING_IDS_OPTION}="
    values = [
        argument[len(prefix) :]
        for argument in _x_arguments()
        if argument.startswith(prefix)
    ]
    if len(values) > 1:
        raise RuntimeError(f"Alembic -x {prefix}<uuid,...> may be supplied only once")
    if not values or not values[0].strip():
        return set()
    try:
        return {
            UUID(identifier.strip()).hex
            for identifier in values[0].split(",")
            if identifier.strip()
        }
    except ValueError as exc:
        raise RuntimeError(
            f"Alembic -x {prefix}<uuid,...> contains an invalid UUID"
        ) from exc


def _schedule_categories() -> list[tuple[str, str]]:
    rows = op.get_bind().execute(sa.text("SELECT id, category FROM schedules")).all()
    return [(UUID(str(identifier)).hex, str(category)) for identifier, category in rows]


def _replace_category_check(categories: tuple[str, ...]) -> None:
    with op.batch_alter_table("schedules", recreate="always") as batch_op:
        batch_op.drop_constraint(op.f("ck_schedules_category"), type_="check")
        batch_op.create_check_constraint(
            op.f("ck_schedules_category"), _check_expression(categories)
        )


def upgrade() -> None:
    schedules = _schedule_categories()
    stored_categories = {category for _, category in schedules}
    recognized_categories = set(_LEGACY_CATEGORIES) | set(
        _UNAPPROVED_LEGACY_CATEGORIES
    )
    unexpected = stored_categories - recognized_categories
    if unexpected:
        raise RuntimeError(
            "Schedule category migration refused unexpected legacy category values"
        )
    unapproved = stored_categories & set(_UNAPPROVED_LEGACY_CATEGORIES)
    if unapproved:
        raise RuntimeError(
            "Schedule category migration refused unapproved legacy rows: "
            + ", ".join(sorted(unapproved))
        )

    meeting_ids = {
        identifier for identifier, category in schedules if category == "MEETING"
    }
    confirmed_meeting_ids = _confirmed_meeting_ids()
    if meeting_ids != confirmed_meeting_ids:
        raise RuntimeError(
            "Schedule category migration requires the exact existing MEETING IDs via "
            f"Alembic -x {_CONFIRMED_MEETING_IDS_OPTION}=<uuid,...>"
        )

    connection = op.get_bind()
    for identifier in confirmed_meeting_ids:
        result = connection.execute(
            sa.text(
                "UPDATE schedules SET category = 'OTHER' "
                "WHERE id = :identifier AND category = 'MEETING'"
            ),
            {"identifier": identifier},
        )
        if result.rowcount != 1:
            raise RuntimeError("Confirmed MEETING row changed during category migration")

    _replace_category_check(_NEW_CATEGORIES)


def downgrade() -> None:
    schedules = _schedule_categories()
    unexpected = {category for _, category in schedules} - set(_NEW_CATEGORIES)
    if unexpected:
        raise RuntimeError(
            "Schedule category downgrade refused unexpected category values"
        )
    new_only = {"INBOUND", "OUTBOUND"}
    if any(category in new_only for _, category in schedules):
        raise RuntimeError(
            "Schedule category downgrade refused INBOUND or OUTBOUND rows; "
            "restore the pre-upgrade backup"
        )
    _replace_category_check(_LEGACY_CATEGORIES)
