#!/bin/bash

# Export production environment.
export APP_ENV=Production
# Build the basics of this app.
pipenv run flask init-db
# Import all basic race tracks.
pipenv run flask import-gpx-routes
# Finally, run the server via gunicorn, using our wsgi entry point and gunicorn config.
pipenv run gunicorn wsgi:application -c server/gunicorn.conf.py
