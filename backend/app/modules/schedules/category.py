"""Schedule category shared by persistence and API schemas."""

from enum import Enum


class Category(str, Enum):
    INVENTORY = "INVENTORY"
    RETURN = "RETURN"
    MEETING = "MEETING"
    PERSONAL = "PERSONAL"
    OTHER = "OTHER"
