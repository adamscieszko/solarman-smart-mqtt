FROM python:3.13-slim-bookworm

LABEL maintainer="Adam Ścieszko <adam.scieszko@gmail.com>"
LABEL org.opencontainers.image.authors="Adam Ścieszko <adam.scieszko@gmail.com>"
LABEL org.opencontainers.image.description="Solarman Smart MQTT published API data of supported PV systems to MQTT."
LABEL org.opencontainers.image.source="https://github.com/adamscieszko/solarman-smart-mqtt"
LABEL org.opencontainers.image.title="Solarman Smart MQTT"
LABEL org.opencontainers.image.url="https://github.com/adamscieszko/solarman-smart-mqtt/pkgs/container/solarman-smart-mqtt"

COPY . /opt/app-root/src/
WORKDIR /opt/app-root/src

RUN python3 -m venv /opt/venv && \
    . /opt/venv/bin/activate && \
    pip install --upgrade pip && \
    pip install -r requirements.txt

ENV PATH=/opt/venv/bin:$PATH

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD []
