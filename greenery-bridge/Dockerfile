ARG BUILD_FROM=ghcr.io/home-assistant/base:latest
FROM ${BUILD_FROM}

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

COPY requirements.txt /tmp/requirements.txt

# A virtualenv sidesteps PEP 668 ("externally managed environment"), which
# blocks pip on current Alpine regardless of flags.
#
# The build toolchain goes in as a virtual package and comes out in the same
# layer. Most of these ship musllinux wheels, but aiohttp on aarch64 is exactly
# the case where a missing wheel becomes a compile - and a build that fails on
# the user's HA Green costs far more than three extra minutes here.
RUN apk add --no-cache python3 \
    && apk add --no-cache --virtual .build-deps \
        py3-pip gcc musl-dev python3-dev libffi-dev make \
    && python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt \
    && apk del .build-deps \
    && rm -rf /root/.cache /tmp/requirements.txt

COPY farm_bridge.py /app/farm_bridge.py
COPY run.sh /run.sh
RUN chmod a+x /run.sh

WORKDIR /app
CMD [ "/run.sh" ]
