FROM python:3.12-slim
WORKDIR /app
COPY ipa_source.py /app/
USER 1000:1000
EXPOSE 8080
CMD ["python", "/app/ipa_source.py", "--config", "/data/config.json", "serve"]
