"""Schedule category shared by persistence and API schemas."""

from enum import Enum


class Category(str, Enum):
    INBOUND = "INBOUND"
    INVENTORY = "INVENTORY"
    OUTBOUND = "OUTBOUND"
    RETURN = "RETURN"
    OTHER = "OTHER"
