import logging
from datetime import datetime, timezone
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.database import async_session_maker
from app.schemas import SyncStats
from app.services import sync_from_dump

logger = logging.getLogger("autocheck.scheduler")

# Глобальный экземпляр планировщика
scheduler = AsyncIOScheduler()

# Хранение статуса последнего запуска для мониторинга
last_sync_info: Optional[SyncStats] = None


async def scheduled_sync_job() -> None:
    """
    Фоновая периодическая задача синхронизации выгрузки 1С.
    Запускается по расписанию через APScheduler.
    """
    global last_sync_info
    logger.info(f"[{datetime.now(timezone.utc).isoformat()}] Запуск периодической синхронизации с 1С...")

    try:
        async with async_session_maker() as session:
            stats = await sync_from_dump(session, settings.dump_file_path)
            last_sync_info = stats
            if stats.status == "success":
                logger.info(
                    f"Периодическая синхронизация завершена успешно: "
                    f"обработано={stats.total_processed}, создано={stats.created}, обновлено={stats.updated}"
                )
            else:
                logger.warning(f"Синхронизация завершена со статусом '{stats.status}': {stats.message}")
    except Exception as e:
        logger.exception(f"Непредвиденная ошибка в фоновом задании синхронизации: {e}")
        last_sync_info = SyncStats(
            status="error",
            total_processed=0,
            created=0,
            updated=0,
            skipped_or_failed=0,
            message=f"Исключение планировщика: {str(e)}",
        )


def start_scheduler() -> None:
    """
    Инициализация и старт фонового планировщика APScheduler.
    """
    interval_minutes = settings.parse_interval_minutes
    
    # Добавляем интервальную задачу
    scheduler.add_job(
        scheduled_sync_job,
        trigger=IntervalTrigger(minutes=interval_minutes),
        id="1c_periodic_sync",
        name="Периодический парсинг выгрузки 1С",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(f"Планировщик APScheduler запущен (интервал проверки: {interval_minutes} мин.)")


def shutdown_scheduler() -> None:
    """
    Корректная остановка планировщика при выключении приложения.
    """
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Планировщик APScheduler успешно остановлен")


def get_last_sync_status() -> Optional[SyncStats]:
    """Возвращает результат последней синхронизации."""
    return last_sync_info
