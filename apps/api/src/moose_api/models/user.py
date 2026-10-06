from datetime import datetime

from sqlalchemy import Integer, Text, func
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from moose_api.models.base import Base


class User(Base):
    """User account model for manager authentication and authorization.

    Links Yahoo GUID to local user account with role-based access control.
    Roles include manager (default) and commissioner with elevated privileges.

    Last login tracking supports security monitoring and inactive user detection.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    yahoo_guid: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default="'manager'")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    last_login: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
