FROM python:3.9-slim

WORKDIR /app

# Copy และติดตั้ง dependencies
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy source code
COPY main.py .
COPY .env .

CMD ["python", "main.py"]