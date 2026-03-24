FROM python:3.12-slim

RUN pip install --no-cache-dir flask psycopg2-binary pyshp

WORKDIR /app
COPY app.py .

EXPOSE 5000
CMD ["python3", "app.py"]
