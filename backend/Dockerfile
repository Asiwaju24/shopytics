FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN DJANGO_SECRET_KEY=build-time-only python manage.py collectstatic --noinput

EXPOSE 8000
# Apply migrations, then serve. (For several replicas, run `migrate` as a separate job instead.)
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn ipace.wsgi:application --bind 0.0.0.0:8000 --workers 3 --access-logfile -"]
