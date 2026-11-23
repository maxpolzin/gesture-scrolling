FROM python:3.12-slim
# opencv/mediapipe need a few shared libs
RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 libgl1 libegl1 libgles2 v4l-utils \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . \
    && python -c "from gesture_scrolling.pipeline import model_path; model_path()"
ENTRYPOINT ["gesture-scrolling"]
