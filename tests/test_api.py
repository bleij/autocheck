import json
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.main import app
from app.models import Car


@pytest.fixture
async def client():
    """Тестовый клиент FastAPI с изолированной in-memory базой данных."""
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    test_session_maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Сидируем тестовую запись
    async with test_session_maker() as session:
        car = Car(
            vin="TESTVIN1234567890",
            mark="BMW",
            model="M5",
            year=2022,
            mileage=15000,
            price=9500000.0,
            defects="В идеальном состоянии",
        )
        session.add(car)
        await session.commit()

    async def override_get_db():
        async with test_session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()


@pytest.mark.asyncio
async def test_get_status(client: AsyncClient):
    """Проверка эндпоинта статуса сервиса."""
    response = await client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


@pytest.mark.asyncio
async def test_get_cars_list(client: AsyncClient):
    """Проверка эндпоинта получения списка автомобилей."""
    response = await client.get("/api/cars")
    assert response.status_code == 200
    cars = response.json()
    assert len(cars) >= 1
    assert cars[0]["vin"] == "TESTVIN1234567890"
    assert cars[0]["mark"] == "BMW"


@pytest.mark.asyncio
async def test_get_single_car(client: AsyncClient):
    """Проверка получения конкретного авто по VIN."""
    response = await client.get("/api/cars/TESTVIN1234567890")
    assert response.status_code == 200
    car = response.json()
    assert car["vin"] == "TESTVIN1234567890"
    assert car["model"] == "M5"


@pytest.mark.asyncio
async def test_car_not_found(client: AsyncClient):
    """Проверка ошибки 404 при запросе несуществующего VIN."""
    response = await client.get("/api/cars/NONEXISTENTVIN123")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_html_index_page(client: AsyncClient):
    """Проверка доступности веб-страницы каталога."""
    response = await client.get("/")
    assert response.status_code == 200
    assert "Autocheck" in response.text
    assert "TESTVIN1234567890" in response.text


@pytest.mark.asyncio
async def test_sync_endpoint(client: AsyncClient):
    """Проверка эндпоинта ручной синхронизации /api/sync."""
    response = await client.post("/api/sync")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ("success", "warning")
    assert "total_processed" in data


@pytest.mark.asyncio
async def test_upload_json_file(client: AsyncClient):
    """Проверка эндпоинта ручной загрузки файла выгрузки /api/upload."""
    payload = [
        {
            "VIN": "UPLOADEDVIN123456",
            "Марка": "Audi",
            "Модель": "A6",
            "Год": 2021,
            "Пробег": 30000,
            "Цена": 4200000,
            "Дефекты": "Без дефектов",
        }
    ]
    json_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    files = {"file": ("manual_dump.json", json_bytes, "application/json")}
    response = await client.post("/api/upload", files=files)
    assert response.status_code == 200
    data = response.json()
    assert data["created"] == 1

    # Проверяем, что машина появилась в базе
    car_res = await client.get("/api/cars/UPLOADEDVIN123456")
    assert car_res.status_code == 200
    assert car_res.json()["mark"] == "Audi"


@pytest.mark.asyncio
async def test_empty_query_params_index_page(client: AsyncClient):
    """Проверка, что передача пустых строк в фильтры GET / не вызывает ошибку 422."""
    response = await client.get("/?search=&mark=&min_year=&max_year=&min_price=&max_price=")
    assert response.status_code == 200
    assert "Autocheck" in response.text


@pytest.mark.asyncio
async def test_empty_query_params_api(client: AsyncClient):
    """Проверка, что GET /api/cars корректно обрабатывает пустые строки параметров без 422."""
    response = await client.get("/api/cars?search=&mark=&min_year=&max_year=&min_price=&max_price=")
    assert response.status_code == 200
    cars = response.json()
    assert isinstance(cars, list)


@pytest.mark.asyncio
async def test_invalid_int_query_param_still_returns_422(client: AsyncClient):
    """Проверка, что действительно невалидный ввод (не число) возвращает 422."""
    response = await client.get("/api/cars?min_year=notanumber")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_dumps_endpoint(client: AsyncClient):
    """Проверка эндпоинта /api/dumps со списком файлов."""
    response = await client.get("/api/dumps")
    assert response.status_code == 200
    dumps = response.json()
    assert isinstance(dumps, list)
    dump_names = [d["name"] for d in dumps]
    assert "1c_dump_base.json" in dump_names
    assert "1c_dump_update.json" in dump_names
    assert "1c_dump_partner.csv" in dump_names


@pytest.mark.asyncio
async def test_sync_preset_base_and_update_upsert(client: AsyncClient):
    """
    Проверка комплексного сценария Upsert:
    1. Импорт базового файла 1c_dump_base.json (10 авто)
    2. Импорт файла обновления 1c_dump_update.json (5 обновлено, 2 создано)
    """
    # 1. Базовый импорт
    res_base = await client.post("/api/sync?file_name=1c_dump_base.json")
    assert res_base.status_code == 200
    data_base = res_base.json()
    assert data_base["status"] == "success"
    assert data_base["created"] == 10

    # Проверяем начальную цену Camry (15.2M ₸)
    camry_res = await client.get("/api/cars/JTDKN36U601894523")
    assert camry_res.status_code == 200
    assert camry_res.json()["price"] == 15200000.0

    # 2. Обновление (Upsert)
    res_update = await client.post("/api/sync?file_name=1c_dump_update.json")
    assert res_update.status_code == 200
    data_update = res_update.json()
    assert data_update["status"] == "success"
    assert data_update["updated"] == 5
    assert data_update["created"] == 2

    # Проверяем, что цена Camry снизилась до 14 400 000 ₸
    camry_updated = await client.get("/api/cars/JTDKN36U601894523")
    assert camry_updated.json()["price"] == 14400000.0
    assert "скидка недели" in camry_updated.json()["defects"].lower()

    # Проверяем, что появилась новая машина (Audi Q5)
    audi = await client.get("/api/cars/WAUZZZFY1M2456789")
    assert audi.status_code == 200
    assert audi.json()["mark"] == "Audi"


@pytest.mark.asyncio
async def test_sync_preset_partner_csv(client: AsyncClient):
    """Проверка синхронизации партнерского CSV файла."""
    res_csv = await client.post("/api/sync?file_name=1c_dump_partner.csv")
    assert res_csv.status_code == 200
    data_csv = res_csv.json()
    assert data_csv["status"] == "success"
    assert data_csv["created"] == 5

    # Проверяем наличие BMW X5 с ценой 29.5M ₸
    bmw_res = await client.get("/api/cars/WBAJU2106K9871234")
    assert bmw_res.status_code == 200
    assert bmw_res.json()["mark"] == "BMW"
    assert bmw_res.json()["price"] == 29500000.0


@pytest.mark.asyncio
async def test_reset_database_endpoint(client: AsyncClient):
    """Проверка эндпоинта сброса базы данных POST /api/reset."""
    # 1. Загружаем CSV (добавляем машины)
    await client.post("/api/sync?file_name=1c_dump_partner.csv")
    cars_before_res = await client.get("/api/cars?limit=100")
    assert len(cars_before_res.json()) >= 6

    # 2. Вызываем сброс базы данных
    reset_res = await client.post("/api/reset")
    assert reset_res.status_code == 200
    data = reset_res.json()
    assert data["status"] == "success"
    assert data["created"] == 10

    # 3. Проверяем, что в базе ровно 10 базовых автомобилей
    cars_after_res = await client.get("/api/cars?limit=100")
    cars_after = cars_after_res.json()
    assert len(cars_after) == 10
    vins = [c["vin"] for c in cars_after]
    assert "JTDKN36U601894523" in vins  # Camry
    assert "WBAJU2106K9871234" not in vins  # BMW X5 из CSV удален при сбросе




