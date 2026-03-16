"""SQLAlchemy declarative base for all database models.

All ORM models inherit from this base to enable automatic
table metadata generation and session management.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all database models using SQLAlchemy 2.0 style."""
