FROM python:3.12-slim

# Only the co-pilot server (ai-server.py + tools.py). voice_client.py needs the microphone and the speakers,
# so it runs on the host (start.bat / start.sh start it).
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ai-server.py tools.py ./

# Listen on all interfaces inside the container; hub, Ollama and the host port are set in compose.yml / .env.
ENV COPILOT_HOST=0.0.0.0 \
    COPILOT_PORT=8766 \
    PYTHONUNBUFFERED=1
EXPOSE 8766
CMD ["python", "-u", "ai-server.py"]
