from datetime import datetime

from sqlalchemy import Boolean, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from moose_api.models.base import Base


class CommissionerNotification(Base):
    """Notifications for commissioner-level events and alerts.

    Stores system-generated messages for commissioners including job deferrals,
    errors, and important events. Metadata JSONB field allows structured context
    for different notification types.

    Read status tracking ensures commissioners can acknowledge notifications.
    """

    __tablename__ = "commissioner_notification"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, server_default="{}")
    is_read: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
