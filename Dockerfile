FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY webhook/ ./webhook/

# Non-root. In imagepolicy mode the key is usually a hostPath file: it must be readable by
# this uid (scripts/gen-certs.sh chowns it to 10001). Secret mounts are readable by default.
USER 10001
EXPOSE 8443

# Certificates are expected at /certs/tls.crt and /certs/tls.key (see --cert-file/--key-file).
ENTRYPOINT ["python3", "-m", "webhook"]
CMD ["--mode", "imagepolicy"]
