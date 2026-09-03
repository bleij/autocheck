# Базовый легковесный образ Python 3.11
FROM python:3.11-slim

# Отключаем буферизацию вывода и генерацию .pyc файлов
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Устанавливаем рабочую директорию
WORKDIR /app

# Устанавливаем системные зависимости для сборки (при необходимости)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Копируем requirements и устанавливаем Python-пакеты с кэшированием pip
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Копируем исходный код проекта
COPY . .

# Создаем директории для базы данных и выгрузок, если их нет
RUN mkdir -p /app/data /app/db

# Открываем порт сервиса
EXPOSE 8000

# Команда запуска FastAPI через uvicorn с горячей перезагрузкой
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
