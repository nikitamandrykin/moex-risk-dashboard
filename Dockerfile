FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
COPY requirements-web.txt /app/requirements-web.txt
RUN pip install --no-cache-dir -r /app/requirements-web.txt
COPY . /app
EXPOSE 8080
CMD ["sh","-c","exec uvicorn web_app:app --host 0.0.0.0 --port ${PORT:-8080}"]
