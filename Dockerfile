FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN SECRET_KEY=build-only python manage.py collectstatic --noinput \
 && useradd -m app && chown -R app /app
USER app
EXPOSE 8000
# migrate, make sure the admin and (test mode) demo logins exist, then serve
CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py bootstrap && gunicorn config.wsgi --bind 0.0.0.0:${PORT:-8000} --workers 1 --threads 4 --timeout 60"]
