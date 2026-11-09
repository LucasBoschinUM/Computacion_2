FROM python:3.13-slim

# Sin .pyc, y los logs salen al instante.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

WORKDIR /srv/tp2

# Primero las dependencias: si solo cambia el código, Docker reutiliza esta capa.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Forma "exec" (con corchetes): python queda como PID 1 y recibe SIGTERM.
# Con la forma shell, la señal le llegaría a sh y no a nuestro programa.
CMD ["python", "-m", "app.main"]
