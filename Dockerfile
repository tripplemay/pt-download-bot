FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/ ./bot/

# 数据目录
RUN mkdir -p /app/data

CMD ["python", "-m", "bot.main"]
