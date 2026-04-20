FROM python:3.10-slim

# ARM Install
 #RUN apt-get update && apt-get install -y --no-install-recommends \
    # gcc \
    # build-essential \
    # && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY controller ./controller
COPY service ./service
EXPOSE 5500

CMD ["python", "app.py"]