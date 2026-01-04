# Summary of this project

This repository implements a simple server using FastAPI that redirects all requests it receives to a configured endpoint, at the same time that it saves their content. The scope of this app is to be used for debugging failing requests from other apps.

It opens the following two endpoints:

- `/___configure`: used to configure the redirecting endpoint (shows a simple HTML).
- `/___view_last/{x}`: used to view the last request at index `x` properly formatted (assuming JSON).

The project is intended to run in production using `docker compose`.

# Instructions for Coding Agents

- You have available some tests that you should always run at the end of your implementation using `.venv/bin/pytest -s tests`
- Note that you have a virtual environment in the project root's folder `.venv`
