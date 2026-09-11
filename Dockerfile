ARG PYTHON_BASE=python:3.11-slim@sha256:1042b61448fef4ba92d16a8c7eb4996d027568ce64792a7877fd88511e0af7c6

FROM ${PYTHON_BASE} AS ffmpeg-builder

ARG FFMPEG_VERSION=9.0.1
ARG FFMPEG_SHA256=cf38e0e28c7e5605942c4a77755349b0145804a397af37eb1fb4c77cb237f635
ARG FFMPEG_SIGNING_FINGERPRINT=FCF986EA15E6E293A5644F10B4322F04D67658D8

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        gnupg \
        libcodec2-dev \
        libgsm1-dev \
        libmp3lame-dev \
        libopus-dev \
        libspeex-dev \
        nasm \
        pkg-config \
        xz-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp/ffmpeg-release

RUN curl --fail --location --silent --show-error \
        "https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz" \
        --output "ffmpeg-${FFMPEG_VERSION}.tar.xz" \
    && curl --fail --location --silent --show-error \
        "https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz.asc" \
        --output "ffmpeg-${FFMPEG_VERSION}.tar.xz.asc" \
    && curl --fail --location --silent --show-error \
        "https://ffmpeg.org/ffmpeg-devel.asc" \
        --output ffmpeg-devel.asc \
    && echo "${FFMPEG_SHA256}  ffmpeg-${FFMPEG_VERSION}.tar.xz" | sha256sum --check --strict \
    && gpg --batch --import ffmpeg-devel.asc \
    && test "$(gpg --batch --with-colons --fingerprint \
        'FFmpeg release signing key <ffmpeg-devel@ffmpeg.org>' \
        | awk -F: '$1 == "fpr" { print $10; exit }')" = "${FFMPEG_SIGNING_FINGERPRINT}" \
    && gpg --batch --verify \
        "ffmpeg-${FFMPEG_VERSION}.tar.xz.asc" \
        "ffmpeg-${FFMPEG_VERSION}.tar.xz" \
    && tar --extract --file "ffmpeg-${FFMPEG_VERSION}.tar.xz"

WORKDIR /tmp/ffmpeg-release/ffmpeg-9.0.1

RUN ./configure \
        --prefix=/opt/ffmpeg \
        --disable-debug \
        --disable-doc \
        --disable-ffplay \
        --enable-gpl \
        --enable-libcodec2 \
        --enable-libgsm \
        --enable-libmp3lame \
        --enable-libopus \
        --enable-libspeex \
    && make -j"$(nproc)" \
    && make install

FROM ${PYTHON_BASE}

ARG FFMPEG_VERSION=9.0.1
ARG FFMPEG_SHA256=cf38e0e28c7e5605942c4a77755349b0145804a397af37eb1fb4c77cb237f635

LABEL org.opencontainers.image.title="APMA V5 MVP" \
      org.opencontainers.image.version="${FFMPEG_VERSION}" \
      org.opencontainers.image.source="https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz" \
      apma.ffmpeg.sha256="${FFMPEG_SHA256}"

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DRY_RUN=true
ENV PATH="/opt/ffmpeg/bin:${PATH}"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        libcodec2-1.2 \
        libgsm1 \
        libmp3lame0 \
        libopus0 \
        libspeex1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ffmpeg-builder /opt/ffmpeg/bin /opt/ffmpeg/bin

RUN ffmpeg -version | grep --fixed-strings "ffmpeg version ${FFMPEG_VERSION}" \
    && ffprobe -version | grep --fixed-strings "ffprobe version ${FFMPEG_VERSION}"

# Copy minimal dependency spec and install (no heavy deps in Task 2)
COPY requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir -r /app/requirements.txt

# Task 2: dry-run verification only. No service code is included in this image.
# The default command prints a verification message, Python version, and DRY_RUN value, then exits.
CMD ["python", "-c", "import os, sys; print('APMA V5 dry-run verification'); print('Python', sys.version); print('DRY_RUN=' + os.environ.get('DRY_RUN','')); sys.exit(0)"]
