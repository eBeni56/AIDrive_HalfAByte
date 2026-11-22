FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py ./

# Listen on all interfaces inside the container; the host port is set in compose.yml / .env.
ENV HOST=0.0.0.0 \
    PORT=8765 \
    PYTHONUNBUFFERED=1
EXPOSE 8765
CMD ["python", "server.py"]
