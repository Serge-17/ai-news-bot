FROM python:3.10-slim

RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH

WORKDIR /app

COPY --chown=user . .

RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 7860

CMD ["python", "main.py"]
