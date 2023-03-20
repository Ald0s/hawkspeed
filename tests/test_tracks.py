import os
import time
import json
import base64

from datetime import date, datetime, timedelta
from flask import url_for
from tests.conftest import BaseCase

from app import db, config, factory, models, login_manager, tracks, error


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

    def test_ensure_interfering_tracks_fail(self):
        """Create a new User.
        Create a track.
        Attempt to create a different track, but one that has a start point within 10 meters of the first track.
        Ensure this attempt fails with TrackInspectionFailed."""
        # Create a new User.
        aldos = factory.create_user("alden@mail.com", "password",
            username = "alden")
        db.session.flush()
        # Load the yarra boulevard test track.
        track_from_gpx = tracks.create_track_from_gpx("yarra_boulevard.gpx")
        # Ensure this was successful.
        self.assertIsNotNone(track_from_gpx)
        # Now, attempt to load the yarra boulevard track that is too close. Expect this raises a TrackInspectionFailed error.
        with self.assertRaises(error.TrackInspectionFailed) as tif:
            tracks.create_track_from_gpx("yarra_boulevard_too_close.gpx")

    def test_page_leaderboard(self):
        """Import a test GPX route.
        Create 2 Users.
        For User1 step through an entire race for the GPX route (successful attempt.)
        For User2 step through the same race but at 500 ms slower at each step.
        For User1 again, step through the same race but at 1000 ms slower at each step.
        Perform a query for the entire leaderboard from the given track.
        Expect the first track to come first, and have a finishing place of one.
        Expect the same for the next two being second and third place."""
        # Create two new Users.
        aldos = factory.create_user("alden@mail.com", "password",
            username = "alden")
        emily = factory.create_user("emily@mail.com", "password",
            username = "emily")
        # Create a track.
        track = tracks.create_track_from_gpx("yarra_boulevard.gpx")
        db.session.flush()
        # Now, for User1, step through the entire race yarra_boulevard_good_race_1.
        race_first = self.simulate_entire_race(aldos, track, os.path.join(os.getcwd(), config.IMPORTS_PATH, "races", "yarra_boulevard_good_race_1.gpx"))
        db.session.flush()
        # Now for User2, step through the same race, but at 500 ms slower.
        race_second = self.simulate_entire_race(emily, track, os.path.join(os.getcwd(), config.IMPORTS_PATH, "races", "yarra_boulevard_good_race_1.gpx"),
            ms_adjustment = 500)
        db.session.flush()
        # Now for User1 again, step through the same race, but at 1000ms slower.
        race_third = self.simulate_entire_race(aldos, track, os.path.join(os.getcwd(), config.IMPORTS_PATH, "races", "yarra_boulevard_good_race_1.gpx"),
            ms_adjustment = 1000)
        db.session.flush()
        # Check there are 3 races logged in the database.
        self.assertEqual(db.session.query(models.TrackUserRace).count(), 3)
        # Get the entire leaderboard for the track.
        leaderboard = tracks.page_leaderboard_for(track, 1).all()
        # Ensure race first is place #1, and is at the top of the leaderboard.
        self.assertEqual(leaderboard[0].uid, race_first.uid)
        self.assertEqual(leaderboard[0].finishing_place, 1)
        # Second is place #2 and in the middle.
        self.assertEqual(leaderboard[1].uid, race_second.uid)
        self.assertEqual(leaderboard[1].finishing_place, 2)
        # Third is place #3 and at the end.
        self.assertEqual(leaderboard[2].uid, race_third.uid)
        self.assertEqual(leaderboard[2].finishing_place, 3)
