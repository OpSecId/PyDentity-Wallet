# Deployment Guide


## Web Server

### Gunicorn
```
GUNICORN_CMD_ARGS="--bind=0.0.0.0:5000 --workers=8 --log-level=info"

```