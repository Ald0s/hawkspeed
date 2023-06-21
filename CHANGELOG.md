# Changelog

## Version 0.00.02
* Added versioning with changelog,
* Added better installation instructions for server,
* Removed account schema in favour of view model,
* Added basic Gunicorn config for reverse proxy w/ nginx & gunicorn, with worker class eventlet for optimum socket IO,
* Started socket's error management system. Moved most server-only errors to their most applicable modules instead of error.py,
* Improved track import process,
* Granted track path its own view model,
* Added CRS to track path being delivered,
* Improved track API by returning track view model and track path view model in a single object,
* Changed models.py to adhere to sqlalchemy 2.0 format,
* Added track ratings,
* Added track comments,