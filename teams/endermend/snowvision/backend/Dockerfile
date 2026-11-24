FROM python:3.12-slim AS dependencies
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM dependencies AS local-model
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.8.0+cpu
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torchvision==0.23.0+cpu
COPY . .

FROM dependencies AS server
COPY . .
CMD ["uvicorn","snowvision.api.app:app","--host","0.0.0.0","--port","8000"]
