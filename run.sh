#!/bin/bash

# Set app environment as development. This will use an sqlite database.
export APP_ENV=Development

# Build the basics of this app.
pipenv run flask init-db
# Import all GPX routes.
pipenv run flask import-gpx-routes
# Finally, run the server.
pipenv run python run.py
