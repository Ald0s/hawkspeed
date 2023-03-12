import os
import time
import json
import base64

from datetime import date, datetime, timedelta
from flask import url_for
from tests.conftest import BaseCase

from app import db, config, factory, models, login_manager, tracks


class TestTracks(BaseCase):
    def test_load_gpx(self):
        tracks.create_track_from_gpx("example1.gpx")

    def test_load_json(self):
        # Create a new User.
        aldos = factory.create_user("alden@mail.com", "password",
            username = "alden")
        db.session.flush()
        # Read the example dictionary.
        with open(os.path.join(os.getcwd(), config.IMPORTS_PATH, "json-routes", "example1.json"), "r") as f:
            j = json.loads(f.read())
        t = tracks.create_track_from_json(aldos, j)
