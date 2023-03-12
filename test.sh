#!/bin/bash
# Set app environment to testing.
export APP_ENV=Test
# Run tests.
pipenv run python test.py
