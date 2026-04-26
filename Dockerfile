FROM python:3.10-slim

# HF Spaces требует non-root пользователя
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH

WORKDIR /app

COPY --chown=user . .

RUN pip install --no-cache-dir -r requirements.txt

# HF Spaces слушает порт 7860
EXPOSE 7860

CMD ["python", "main.py"]
