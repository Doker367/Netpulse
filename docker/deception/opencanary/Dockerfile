# OpenCanary — imagen local para la red señuelo NetPulse
FROM python:3.11-slim

RUN pip install --no-cache-dir opencanary

COPY opencanary.conf /etc/opencanaryd/opencanary.conf
RUN mkdir -p /var/log/opencanary

EXPOSE 21 22 23 80 445 3306 3389

# --dev: corre en primer plano (sin daemonizar) para Docker
CMD ["opencanaryd", "--dev"]
