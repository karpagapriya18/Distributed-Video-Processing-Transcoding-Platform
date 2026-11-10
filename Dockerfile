FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY common common
COPY services services
COPY migrations migrations
COPY alembic.ini pytest.ini ./
COPY tests tests
CMD ["python", "-m", "pytest", "-q"]
