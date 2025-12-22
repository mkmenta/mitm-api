# "Man-in-the-Middle" debugging API

This repository implements a simple server using FastAPI that redirects all requests it receives to a configured endpoint, at the same time that it saves their content. The scope of this app is to be used for debugging failing requests from other apps.

It opens the following two endpoints:

- `/___configure`: used to configure the redirecting endpoint (shows a simple HTML).
- `/___view_last/{x}`: used to view the last request at index `x` properly formatted (assuming JSON).

Run the app with:

```
docker compose up
```
