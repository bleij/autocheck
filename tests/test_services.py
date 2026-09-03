import json
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models import Car
from app.schemas import CarCreate
from app.services import (
    extract_cars_from_raw_data,
    parse_file_content,
    upsert_cars,
    get_car_by_vin,
    get_cars,
)


@pytest.fixture
async def test_db_session():
    """Тестовая in-memory база данных SQLite."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


def test_map_1c_russian_keys():
    """Тест маппинга полей из 1С на русском языке."""
    raw_1c_car = {
        "VIN": "xta218070m1234567",
        "Марка": "Lada",
        "Модель": "Vesta",
        "Год": 2021,
        "Пробег": 50000,
        "Цена": 1200000,
        "Дефекты": ["скол на лобовом", "царапина на бампере"],
    }
    car = CarCreate.model_validate(raw_1c_car)
    assert car.vin == "XTA218070M1234567"
    assert car.mark == "Lada"
    assert car.model == "Vesta"
    assert car.year == 2021
    assert car.mileage == 50000
    assert car.price == 1200000.0
    assert "скол на лобовом" in car.defects


def test_parse_csv_file_content():
    """Тест парсинга CSV содержимого с точкой с запятой."""
    csv_data = (
        "VIN;Марка;Модель;Год;Пробег;Цена;Дефекты\n"
        "WBA11AA07MC789012;BMW;520d;2020;75000;3500000;Заводской окрас\n"
        "JTDKN36U601894523;Toyota;Camry;2021;45000;2900000;\n"
    ).encode("utf-8")

    cars = parse_file_content(csv_data, "export_1c.csv")
    assert len(cars) == 2
    assert cars[0].vin == "WBA11AA07MC789012"
    assert cars[0].mark == "BMW"
    assert cars[1].vin == "JTDKN36U601894523"
    assert cars[1].defects is None or cars[1].defects == ""


@pytest.mark.asyncio
async def test_upsert_insert_and_update(test_db_session: AsyncSession):
    """Тест корректности работы upsert: вставка новой записи и последующее обновление цены и пробега."""
    # 1. Первый импорт (создание)
    initial_cars = [
        CarCreate(
            vin="W1K2130041B345678",
            mark="Mercedes-Benz",
            model="E 200",
            year=2021,
            mileage=50000,
            price=4500000,
            defects="Без дефектов",
        )
    ]
    stats_1 = await upsert_cars(test_db_session, initial_cars)
    assert stats_1.created == 1
    assert stats_1.updated == 0

    car = await get_car_by_vin(test_db_session, "W1K2130041B345678")
    assert car is not None
    assert car.price == 4500000
    assert car.mileage == 50000

    # 2. Второй импорт того же VIN с измененной ценой и увеличенным пробегом
    updated_cars = [
        CarCreate(
            vin="W1K2130041B345678",
            mark="Mercedes-Benz",
            model="E 200",
            year=2021,
            mileage=52000,
            price=4300000,
            defects="Появился скол на капоте",
        )
    ]
    stats_2 = await upsert_cars(test_db_session, updated_cars)
    assert stats_2.created == 0
    assert stats_2.updated == 1

    # Проверяем, что в базе по-прежнему 1 запись, но данные обновились
    cars, total = await get_cars(test_db_session)
    assert total == 1
    car_updated = cars[0]
    assert car_updated.price == 4300000
    assert car_updated.mileage == 52000
    assert car_updated.defects == "Появился скол на капоте"


@pytest.mark.asyncio
async def test_timestamps_use_local_time(test_db_session: AsyncSession):
    """Проверка, что время updated_at генерируется в локальном часовом поясе (Asia/Almaty), а не в UTC."""
    from app.config import get_local_now

    now_almaty = get_local_now()
    cars = [
        CarCreate(
            vin="ALMATYTIME123456",
            mark="Toyota",
            model="Camry",
            year=2021,
            mileage=50000,
            price=15200000,
            defects="Заводской окрас",
        )
    ]
    await upsert_cars(test_db_session, cars)
    car = await get_car_by_vin(test_db_session, "ALMATYTIME123456")
    assert car is not None
    assert car.updated_at is not None

    # Разница между временем сохранения и текущим временем Алматы должна быть менее 5 секунд
    time_diff_seconds = abs((car.updated_at - now_almaty).total_seconds())
    assert time_diff_seconds < 5, f"Разница во времени {time_diff_seconds}с превышает допустимую (проблема с часовым поясом!)"

