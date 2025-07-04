FROM python:3.12.11-slim-bookworm

WORKDIR /pydentity

ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

COPY pyproject.toml ./

RUN pip install poetry
RUN poetry install

COPY app ./app
COPY config.py main.py ./

CMD ["gunicorn", "main:app"]