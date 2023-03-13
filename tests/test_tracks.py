import os
import time
import json
import base64

from datetime import date, datetime, timedelta
from flask import url_for
from tests.conftest import BaseCase

from app import db, config, factory, models, login_manager, tracks


class TestTracks(BaseCase):
    def test_loading_tracks(self):
        # Create a new User.
        aldos = factory.create_user("alden@mail.com", "password",
            username = "alden")
        db.session.flush()
        # Test that we can load a track from GPX.
        track_from_gpx = tracks.create_track_from_gpx("example1.gpx",
            intersection_check = False)
        """TODO: some verifies here."""
        # Test that we can load a track from JSON, that is already verified (no need to verify recorded attributes.)
        with open(os.path.join(os.getcwd(), config.IMPORTS_PATH, "json-routes", "example2.json"), "r") as f:
            example2_json = json.loads(f.read())
        track_from_json_2 = tracks.create_track_from_json(aldos, example2_json,
            is_verified = True, intersection_check = False)
        """TODO: some verifies here."""

    def test_ensure_intersecting_tracks_fail(self):
        """Create a new User.
        Create a track.
        Attempt to create a different track, but one that goes through an existing track.
        Ensure this fails with an exception; TrackPathIntersectsExistingTrack"""
        self.assertEqual(True, False)
