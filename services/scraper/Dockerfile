FROM python:3.12-slim

WORKDIR /app

# Install only what we need — no browser, no system deps beyond the basics.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["celery", "-A", "app.main", "worker", "--loglevel=info", "--beat"]
