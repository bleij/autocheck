from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import re
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CarBase(BaseModel):
    """Базовые поля автомобиля."""
    vin: str = Field(..., min_length=11, max_length=17, description="VIN номер автомобиля")
    mark: str = Field(..., min_length=1, max_length=100, description="Марка автомобиля")
    model: str = Field(..., min_length=1, max_length=100, description="Модель автомобиля")
    year: int = Field(..., ge=1900, le=2100, description="Год выпуска")
    mileage: int = Field(..., ge=0, description="Пробег в км")
    price: float = Field(..., ge=0, description="Цена в рублях")
    defects: Optional[str] = Field(None, description="Список или описание дефектов")

    @field_validator("vin")
    @classmethod
    def normalize_vin(cls, v: str) -> str:
        """Нормализация VIN: удаление пробелов и перевод в верхний регистр."""
        cleaned = re.sub(r"\s+", "", v.strip().upper())
        if len(cleaned) < 11 or len(cleaned) > 17:
            raise ValueError("VIN должен содержать от 11 до 17 символов")
        return cleaned


class CarCreate(CarBase):
    """Схема для валидации входных данных из 1С."""

    @model_validator(mode="before")
    @classmethod
    def map_1c_fields(cls, data: Any) -> Any:
        """
        Маппинг типичных названий полей из выгрузок 1С (как на русском, так и на английском).
        """
        if not isinstance(data, dict):
            return data

        # Словарь синонимов полей из 1С
        synonyms = {
            "vin": ["vin", "VIN", "Вин", "ВИН", "ИдентификационныйНомер"],
            "mark": ["mark", "brand", "Марка", "Бренд", "Производитель"],
            "model": ["model", "Модель"],
            "year": ["year", "Год", "ГодВыпуска", "Год_выпуска"],
            "mileage": ["mileage", "Пробег", "Одометр"],
            "price": ["price", "Цена", "Стоимость", "ЦенаПродажи"],
            "defects": ["defects", "Дефекты", "Замечания", "Состояние", "Повреждения"],
        }

        normalized: Dict[str, Any] = {}
        for target_key, aliases in synonyms.items():
            for alias in aliases:
                if alias in data and data[alias] is not None:
                    val = data[alias]
                    # Обработка дефектов, если они пришли списком
                    if target_key == "defects" and isinstance(val, (list, tuple)):
                        val = "; ".join(str(item) for item in val)
                    normalized[target_key] = val
                    break

        # Дополняем остальными ключами, если они есть
        for k, v in data.items():
            if k not in normalized and k in cls.model_fields:
                normalized[k] = v

        return normalized


class CarResponse(CarBase):
    """Схема ответа API с автомобилем."""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class SyncStats(BaseModel):
    """Статистика выполнения синхронизации с 1С."""
    status: str = Field(..., description="Статус операции (success/warning/error)")
    total_processed: int = Field(0, description="Всего обработано записей")
    created: int = Field(0, description="Добавлено новых автомобилей")
    updated: int = Field(0, description="Обновлено существующих автомобилей")
    skipped_or_failed: int = Field(0, description="Пропущено из-за ошибок")
    message: str = Field("", description="Поясняющее сообщение")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def empty_str_to_none(v: Any) -> Any:
    """
    Преобразует пустые строки или строки из пробелов в None.
    Предотвращает ошибки валидации 422 при отправке пустых полей формы (например, min_year="").
    """
    if isinstance(v, str) and not v.strip():
        return None
    return v


from typing import Annotated
from pydantic import BeforeValidator

CleanStr = Annotated[Optional[str], BeforeValidator(empty_str_to_none)]
CleanInt = Annotated[Optional[int], BeforeValidator(empty_str_to_none)]
CleanFloat = Annotated[Optional[float], BeforeValidator(empty_str_to_none)]


class CarFilterParams(BaseModel):
    """Параметры фильтрации и пагинации с автоматической очисткой пустых строк."""
    mark: CleanStr = Field(None, description="Фильтр по марке")
    search: CleanStr = Field(None, description="Поиск по VIN, марке, модели или дефектам")
    min_year: CleanInt = Field(None, ge=1900, le=2100, description="Минимальный год выпуска")
    max_year: CleanInt = Field(None, ge=1900, le=2100, description="Максимальный год выпуска")
    min_price: CleanFloat = Field(None, ge=0, description="Минимальная стоимость")
    max_price: CleanFloat = Field(None, ge=0, description="Максимальная стоимость")
    limit: int = Field(50, ge=1, le=500, description="Количество элементов")
    offset: int = Field(0, ge=0, description="Смещение пагинации")

