FROM python:3.12.2

RUN apt-get update && \
    apt-get install -y --no-install-recommends mediainfo libmediainfo0v5 libmediainfo-dev ca-certificates && \
    ldconfig && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /DreamxBotz

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip --root-user-action=ignore && \
    pip install --no-cache-dir -r requirements.txt --root-user-action=ignore

COPY . .

# give freed memory (images) back to the OS instead of keeping it
ENV TZ=UTC
ENV MALLOC_ARENA_MAX=2 MALLOC_TRIM_THRESHOLD_=262144 MALLOC_MMAP_THRESHOLD_=262144

CMD ["python3", "bot.py"]