ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE}
WORKDIR /app
COPY services /app/services
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 BENCH_BIND=0.0.0.0 BENCH_PORT=8090 BENCH_DB=/data/support.sqlite
RUN mkdir -p /data && chown 65532:65532 /data
USER 65532:65532
EXPOSE 8090
CMD ["python", "-m", "services.mock_service"]
