import csv
import io
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Car
from app.schemas import CarCreate, SyncStats

logger = logging.getLogger("autocheck.services")


def extract_cars_from_raw_data(data: Any) -> List[Dict[str, Any]]:
    """
    Извлекает список словарей автомобилей из произвольной структуры JSON 1C.
    1С может присылать как плоский массив [ {...}, {...} ],
    так и объект с ключами: "cars", "items", "data", "Выгрузка", "Автомобили".
    """
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    elif isinstance(data, dict):
        for candidate_key in ["cars", "items", "data", "vehicles", "Выгрузка", "Автомобили", "СписокАвтомобилей"]:
            if candidate_key in data and isinstance(data[candidate_key], list):
                return [item for item in data[candidate_key] if isinstance(item, dict)]
        # Если это единичный объект автомобиля
        if "vin" in data or "VIN" in data or "Вин" in data:
            return [data]
    return []


def parse_csv_content(text: str) -> List[Dict[str, Any]]:
    """
    Парсит CSV выгрузку с автоматическим определением разделителя (, или ;).
    """
    # Определяем диалект и разделитель
    sample = text[:2048]
    delimiter = ";" if ";" in sample else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    items = []
    for row in reader:
        if any(row.values()):
            items.append(dict(row))
    return items


def parse_file_content(content_bytes: bytes, filename: str) -> List[CarCreate]:
    """
    Парсит содержимое байтов файла (JSON или CSV) и валидирует через Pydantic.
    """
    valid_cars: List[CarCreate] = []
    raw_items: List[Dict[str, Any]] = []

    filename_lower = filename.lower()
    
    # Попытка декодировать utf-8, с фоллбеком на cp1251 (популярно в 1C под Windows)
    text = ""
    for enc in ["utf-8", "utf-8-sig", "cp1251", "windows-1251"]:
        try:
            text = content_bytes.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    
    if not text:
        raise ValueError("Не удалось декодировать файл (поддерживаются UTF-8 и CP1251)")

    if filename_lower.endswith(".json"):
        parsed_json = json.loads(text)
        raw_items = extract_cars_from_raw_data(parsed_json)
    elif filename_lower.endswith(".csv"):
        raw_items = parse_csv_content(text)
    else:
        # Пытаемся сначала как JSON, если ошибка — как CSV
        try:
            parsed_json = json.loads(text)
            raw_items = extract_cars_from_raw_data(parsed_json)
        except json.JSONDecodeError:
            raw_items = parse_csv_content(text)

    for item in raw_items:
        try:
            validated = CarCreate.model_validate(item)
            valid_cars.append(validated)
        except Exception as e:
            logger.warning(f"Ошибка валидации записи автомобиля: {e}. Данные: {item}")

    return valid_cars


def parse_dump_file(file_path: Union[str, Path]) -> List[CarCreate]:
    """
    Считывает и парсит локальный файл выгрузки 1С по указанному пути.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Файл выгрузки 1С не найден: {path}")

    content_bytes = path.read_bytes()
    return parse_file_content(content_bytes, path.name)


async def upsert_cars(session: AsyncSession, cars_data: List[CarCreate]) -> SyncStats:
    """
    Пакетный upsert автомобилей в БД по первичному ключу VIN.
    
    - Если VIN уже есть в базе — обновляются поля mark, model, year, mileage, price, defects.
    - Если VIN отсутствует — создается новая запись.
    - Поддерживает любую базу данных (SQLite, PostgreSQL, MySQL) за счет кроссплатформенного алгоритма.
    """
    if not cars_data:
        return SyncStats(
            status="warning",
            total_processed=0,
            created=0,
            updated=0,
            skipped_or_failed=0,
            message="Файл не содержит валидных записей для сохранения",
        )

    # Устраняем возможные дубликаты VIN внутри одного файла выгрузки (берем последнее значение)
    unique_cars: Dict[str, CarCreate] = {}
    for car in cars_data:
        unique_cars[car.vin] = car

    all_vins = list(unique_cars.keys())
    created_count = 0
    updated_count = 0

    # Обрабатываем батчами по 500 записей во избежание переполнения параметров SQL-запроса
    batch_size = 500
    for i in range(0, len(all_vins), batch_size):
        batch_vins = all_vins[i:i + batch_size]
        
        # Получаем уже существующие записи для текущего батча
        stmt = select(Car).where(Car.vin.in_(batch_vins))
        result = await session.execute(stmt)
        existing_cars = {c.vin: c for c in result.scalars().all()}

        for vin in batch_vins:
            car_item = unique_cars[vin]
            if vin in existing_cars:
                # Обновление существующей записи
                existing = existing_cars[vin]
                existing.mark = car_item.mark
                existing.model = car_item.model
                existing.year = car_item.year
                existing.mileage = car_item.mileage
                existing.price = car_item.price
                existing.defects = car_item.defects
                updated_count += 1
            else:
                # Создание новой записи
                new_car = Car(
                    vin=car_item.vin,
                    mark=car_item.mark,
                    model=car_item.model,
                    year=car_item.year,
                    mileage=car_item.mileage,
                    price=car_item.price,
                    defects=car_item.defects,
                )
                session.add(new_car)
                created_count += 1

    await session.commit()
    logger.info(f"Синхронизация завершена: создано {created_count}, обновлено {updated_count}")

    return SyncStats(
        status="success",
        total_processed=len(cars_data),
        created=created_count,
        updated=updated_count,
        skipped_or_failed=0,
        message=f"Успешно обработано {len(cars_data)} записей (новых: {created_count}, обновлено: {updated_count})",
    )


async def sync_from_dump(session: AsyncSession, file_path: Optional[str] = None) -> SyncStats:
    """
    Выполняет полный цикл синхронизации: чтение файла выгрузки 1С и upsert в БД.
    """
    target_path = file_path or settings.dump_file_path
    try:
        cars_data = parse_dump_file(target_path)
        stats = await upsert_cars(session, cars_data)
        return stats
    except FileNotFoundError as e:
        logger.error(f"Ошибка синхронизации: {e}")
        return SyncStats(
            status="error",
            total_processed=0,
            created=0,
            updated=0,
            skipped_or_failed=0,
            message=str(e),
        )
    except Exception as e:
        logger.exception(f"Критическая ошибка при синхронизации выгрузки: {e}")
        return SyncStats(
            status="error",
            total_processed=0,
            created=0,
            updated=0,
            skipped_or_failed=0,
            message=f"Ошибка парсинга: {str(e)}",
        )


async def get_cars(
    session: AsyncSession,
    mark: Optional[str] = None,
    search: Optional[str] = None,
    min_year: Optional[int] = None,
    max_year: Optional[int] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    limit: int = 100,
    offset: int = 0,
) -> Tuple[List[Car], int]:
    """
    Получение списка автомобилей с фильтрацией, поиском и пагинацией.
    Возвращает (список_автомобилей, общее_количество).
    """
    query = select(Car)
    count_query = select(func.count(Car.vin))

    if mark:
        query = query.where(Car.mark.ilike(f"%{mark}%"))
        count_query = count_query.where(Car.mark.ilike(f"%{mark}%"))

    if search:
        search_pattern = f"%{search}%"
        search_filter = (
            Car.vin.ilike(search_pattern)
            | Car.mark.ilike(search_pattern)
            | Car.model.ilike(search_pattern)
            | Car.defects.ilike(search_pattern)
        )
        query = query.where(search_filter)
        count_query = count_query.where(search_filter)

    if min_year:
        query = query.where(Car.year >= min_year)
        count_query = count_query.where(Car.year >= min_year)

    if max_year:
        query = query.where(Car.year <= max_year)
        count_query = count_query.where(Car.year <= max_year)

    if min_price:
        query = query.where(Car.price >= min_price)
        count_query = count_query.where(Car.price >= min_price)

    if max_price:
        query = query.where(Car.price <= max_price)
        count_query = count_query.where(Car.price <= max_price)

    # Сортировка по умолчанию: сначала последние обновленные
    query = query.order_by(Car.updated_at.desc()).limit(limit).offset(offset)

    cars_result = await session.execute(query)
    count_result = await session.execute(count_query)

    cars = list(cars_result.scalars().all())
    total = count_result.scalar_one()

    return cars, total


async def get_car_by_vin(session: AsyncSession, vin: str) -> Optional[Car]:
    """Поиск автомобиля по точному VIN номеру."""
    normalized_vin = vin.strip().upper()
    query = select(Car).where(Car.vin == normalized_vin)
    result = await session.execute(query)
    return result.scalar_one_or_none()


async def get_distinct_marks(session: AsyncSession) -> List[str]:
    """Получение уникального списка марок для фильтров в интерфейсе."""
    query = select(Car.mark).distinct().order_by(Car.mark)
    result = await session.execute(query)
    return [mark for mark in result.scalars().all() if mark]
