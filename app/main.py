import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, List, Optional

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session_maker, get_db, init_db
from app.models import Car
from app.scheduler import get_last_sync_status, shutdown_scheduler, start_scheduler
from app.schemas import CarFilterParams, CarResponse, SyncStats
from app.services import (
    clear_and_reset_database,
    get_car_by_vin,
    get_cars,
    get_distinct_marks,
    parse_file_content,
    sync_from_dump,
    upsert_cars,
)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("autocheck")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Управление жизненным циклом приложения:
    1. Инициализация таблиц базы данных.
    2. Первичная синхронизация из файла выгрузки 1С (при наличии).
    3. Запуск планировщика APScheduler.
    4. Остановка планировщика при завершении работы.
    """
    logger.info("Запуск сервиса Autocheck...")
    await init_db()
    logger.info("База данных инициализирована.")

    # Выполняем первичный импорт из дампа, если он существует
    if Path(settings.dump_file_path).exists():
        logger.info(f"Обнаружен файл выгрузки 1С ({settings.dump_file_path}), запуск начального импорта...")
        async with async_session_maker() as session:
            initial_sync = await sync_from_dump(session, settings.dump_file_path)
            logger.info(f"Результат начального импорта: {initial_sync.message}")

    # Запускаем периодический планировщик
    start_scheduler()

    yield

    # Остановка сервиса
    logger.info("Остановка сервиса Autocheck...")
    shutdown_scheduler()


app = FastAPI(
    title=settings.app_name,
    description="Микросервис интеграции и парсинга выгрузок автомобилей из 1С с поддержкой upsert",
    version="1.0.0",
    lifespan=lifespan,
)

# Настройка шаблонов Jinja2
templates_dir = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))


# ==========================================
# Веб-интерфейс (HTML)
# ==========================================

@app.get("/", response_class=HTMLResponse, summary="Главная страница каталога")
async def index_view(
    request: Request,
    params: Annotated[CarFilterParams, Query()],
    db: AsyncSession = Depends(get_db),
):
    """Отображение HTML страницы со списком автомобилей и статистикой."""
    cars, total_count = await get_cars(
        session=db,
        mark=params.mark,
        search=params.search,
        min_year=params.min_year,
        max_year=params.max_year,
        min_price=params.min_price,
        max_price=params.max_price,
        limit=params.limit if params.limit != 50 else 200,
        offset=params.offset,
    )
    all_marks = await get_distinct_marks(db)
    last_sync = get_last_sync_status()

    # Поиск доступных файлов выгрузки в папке data/
    data_dir = Path("data")
    available_dumps = []
    if data_dir.exists():
        for p in sorted(data_dir.iterdir()):
            if p.is_file() and p.suffix.lower() in (".json", ".csv"):
                available_dumps.append(p.name)

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "cars": cars,
            "total_count": total_count,
            "marks": all_marks,
            "available_dumps": available_dumps,
            "selected_mark": params.mark,
            "search_query": params.search,
            "selected_min_year": params.min_year,
            "selected_max_year": params.max_year,
            "selected_min_price": params.min_price,
            "selected_max_price": params.max_price,
            "parse_interval": settings.parse_interval_minutes,
            "last_sync": last_sync,
        },
    )


# ==========================================
# REST API эндпоинты
# ==========================================

@app.get("/api/dumps", summary="Список доступных файлов выгрузки")
async def list_dumps_endpoint():
    """Возвращает список доступных файлов выгрузок из директории data/."""
    data_dir = Path("data")
    files = []
    if data_dir.exists():
        for p in sorted(data_dir.iterdir()):
            if p.is_file() and p.suffix.lower() in (".json", ".csv"):
                files.append({
                    "name": p.name,
                    "size_bytes": p.stat().st_size,
                    "format": p.suffix.lower().replace(".", "").upper(),
                })
    return files


@app.get("/api/cars", response_model=List[CarResponse], summary="Получить список автомобилей")
async def list_cars_endpoint(
    params: Annotated[CarFilterParams, Query()],
    db: AsyncSession = Depends(get_db),
):
    """Возвращает список автомобилей с возможностью фильтрации и пагинации."""
    cars, _ = await get_cars(
        session=db,
        mark=params.mark,
        search=params.search,
        min_year=params.min_year,
        max_year=params.max_year,
        min_price=params.min_price,
        max_price=params.max_price,
        limit=params.limit,
        offset=params.offset,
    )
    return cars


@app.get("/api/cars/{vin}", response_model=CarResponse, summary="Получить автомобиль по VIN")
async def get_car_endpoint(
    vin: str,
    db: AsyncSession = Depends(get_db),
):
    """Возвращает детальную информацию об автомобиле по VIN номеру."""
    car = await get_car_by_vin(db, vin)
    if not car:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Автомобиль с VIN '{vin}' не найден в базе данных",
        )
    return car


@app.post("/api/sync", response_model=SyncStats, summary="Запустить синхронизацию")
async def sync_now_endpoint(
    file_name: Optional[str] = Query(None, description="Имя файла выгрузки из директории data (например, 1c_dump_base.json)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Принудительный запуск чтения файла выгрузки 1С и обновления базы данных методом upsert.
    Если передан параметр file_name, синхронизируется указанный файл из папки data/.
    """
    target_path = settings.dump_file_path
    if file_name:
        clean_name = Path(file_name).name
        custom_path = Path("data") / clean_name
        if not custom_path.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Файл выгрузки '{clean_name}' не найден в директории data/",
            )
        target_path = str(custom_path)

    stats = await sync_from_dump(db, target_path)
    return stats


@app.post("/api/upload", response_model=SyncStats, summary="Загрузить файл выгрузки 1С вручную")
async def upload_dump_file_endpoint(
    file: UploadFile = File(..., description="Файл выгрузки (JSON или CSV)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Эндпоинт для прямой загрузки файла выгрузки 1C через браузер или API.
    Файл валидируется и немедленно импортируется методом upsert.
    """
    try:
        content = await file.read()
        cars_data = parse_file_content(content, file.filename or "dump.json")
        stats = await upsert_cars(db, cars_data)
        return stats
    except Exception as e:
        logger.error(f"Ошибка при обработке загруженного файла: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Не удалось обработать файл: {str(e)}",
        )


@app.post("/api/reset", response_model=SyncStats, summary="Сбросить базу и загрузить 10 базовых авто")
@app.delete("/api/cars", response_model=SyncStats, summary="Очистить базу и восстановить базовый набор")
async def reset_database_endpoint(
    db: AsyncSession = Depends(get_db),
):
    """
    Полностью очищает базу данных и повторно инициализирует её базовой выгрузкой 1c_dump_base.json (10 авто в тенге ₸).
    """
    stats = await clear_and_reset_database(db, "data/1c_dump_base.json")
    return stats


@app.get("/api/status", summary="Статус микросервиса и планировщика")
async def status_endpoint():
    """Проверка статуса работоспособности сервиса и последней синхронизации."""
    last_sync = get_last_sync_status()
    return {
        "status": "healthy",
        "service": settings.app_name,
        "database": settings.database_url.split("://")[0],
        "dump_file": settings.dump_file_path,
        "scheduler_interval_minutes": settings.parse_interval_minutes,
        "last_sync": last_sync.model_dump() if last_sync else None,
    }
