FROM python:3.12-slim

# ncurses: python trae el modulo curses compilado, pero necesita la
# terminfo del sistema para resolver TERM=xterm-256color.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ncurses-base ncurses-term procps \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TERM=xterm-256color

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "src/main.py"]
