"""Pre-registration directory; deliberately separate from campaign subscriptions."""
from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, new_id, now_utc

class PreuserImport(Base):
    __tablename__ = 'preuser_imports'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    filename: Mapped[str] = mapped_column(String(255))
    file_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now_utc)
    rows: Mapped[int] = mapped_column(Integer)
    imported: Mapped[int] = mapped_column(Integer)
    duplicates: Mapped[int] = mapped_column(Integer)
    invalid: Mapped[int] = mapped_column(Integer)
    errors: Mapped[list] = mapped_column(JSON, default=list)

class Preuser(Base):
    __tablename__ = 'preusers'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    status: Mapped[str] = mapped_column(String(24), default='pre_registered', index=True)
    import_id: Mapped[str] = mapped_column(ForeignKey('preuser_imports.id'), index=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now_utc)
