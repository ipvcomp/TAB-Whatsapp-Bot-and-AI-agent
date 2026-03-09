FROM python:3.11

WORKDIR /code

COPY ./requirements.txt /code/requirements.txt

RUN pip install --no-cache-dir --upgrade -r /code/requirements.txt

ENV DEBUG="DEV"

COPY ./app /code/app
COPY env-prod .
COPY env-stage .
COPY main.py .

CMD ["gunicorn", "--bind=0.0.0.0:5000", "--reuse-port", "--workers=4", "--worker-class=uvicorn.workers.UvicornWorker", "--timeout=120", "--access-logfile=-", "--error-logfile=-", "main:app"]
