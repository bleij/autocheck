from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.config import get_local_now
from app.database import Base


class Car(Base):
    """
    Модель автомобиля из выгрузки 1С.
    
    Уникальный идентификатор — VIN (17 символов).
    Вся синхронизация и upsert выполняются по ключу VIN.
    """
    __tablename__ = "cars"

    vin: Mapped[str] = mapped_column(
        String(17),
        primary_key=True,
        index=True,
        comment="VIN номер автомобиля (уникальный 17-значный ключ)",
    )
    mark: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        comment="Марка автомобиля (бренд)",
    )
    model: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Модель автомобиля",
    )
    year: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Год выпуска",
    )
    mileage: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Пробег в километрах",
    )
    price: Mapped[float] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        comment="Стоимость в тенге ₸",
    )
    defects: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Описание дефектов или замечаний диагноста",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=get_local_now,
        comment="Дата добавления в базу данных (время Алматы)",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=get_local_now,
        onupdate=get_local_now,
        comment="Дата последнего обновления информации (время Алматы)",
    )

    def to_dict(self) -> dict:
        """Сериализация модели в словарь."""
        return {
            "vin": self.vin,
            "mark": self.mark,
            "model": self.model,
            "year": self.year,
            "mileage": self.mileage,
            "price": float(self.price),
            "defects": self.defects,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return f"<Car(vin='{self.vin}', mark='{self.mark}', model='{self.model}', price={self.price})>"
