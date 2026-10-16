FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY coldlink coldlink
COPY scenarios scenarios
ENV FALKORDB_HOST=falkordb
CMD ["sh", "-c", "python -m coldlink.generate && python -m coldlink.load && python -m coldlink.demo"]
