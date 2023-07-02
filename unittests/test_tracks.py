import os
import time
import json
import base64

from datetime import date, datetime, timedelta
from flask import url_for
from unittests.conftest import BaseCase

from app import db, config, factory, models, login_manager, tracks, error


class TestTracks(BaseCase):
    def test_loading_tracks(self):
        # Create a new User.
        aldos = factory.create_user("alden@mail.com", "password",
            username = "alden", vehicle = "1994 Toyota Supra")
        db.session.flush()
        # Test that we can load a track from GPX.
        track_from_gpx = tracks.create_track_from_gpx("example1.gpx",
            intersection_check = False)
        """TODO: some verifies here."""
        # Test that we can load a track from JSON, that is already verified (no need to verify recorded attributes.)
        with open(os.path.join(os.getcwd(), config.IMPORTS_PATH, "json-routes", "example2.json"), "r") as f:
            example2_json = json.loads(f.read())
        track_from_json_2 = tracks.create_track_from_json(example2_json,
            is_verified = True, intersection_check = False)
        """TODO: some verifies here."""
    
    def test_can_be_raced(self):
        """"""
        self.assertEqual(True, False)

    def test_ensure_interfering_tracks_fail(self):
        """Create a new User.
        Create a track.
        Attempt to create a different track, but one that has a start point within 10 meters of the first track.
        Ensure this attempt fails with TrackInspectionFailed."""
        # Create a new User.
        aldos = factory.create_user("alden@mail.com", "password",
            username = "alden", vehicle = "1994 Toyota Supra")
        db.session.flush()
        # Load the yarra boulevard test track.
        created_track = tracks.create_track_from_gpx("yarra_boulevard.gpx")
        # Ensure this was successful.
        self.assertIsNotNone(created_track)
        created_track.set_owner(aldos)
        db.session.flush()
        # Now, attempt to load the yarra boulevard track that is too close. Expect this raises a TrackInspectionFailed error.
        with self.assertRaises(tracks.TrackInspectionFailed) as tif:
            tracks.create_track_from_gpx("yarra_boulevard_too_close.gpx",
                relative_dir = config.TESTDATA_GPX_ROUTES_DIR)

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
            username = "alden", vehicle = "1994 Toyota Supra")
        emily = factory.create_user("emily@mail.com", "password",
            username = "emily", vehicle = "1994 Toyota Supra")
        # Create a track.
        created_track = tracks.create_track_from_gpx("yarra_boulevard.gpx")
        created_track.set_owner(aldos)
        track = created_track.track
        db.session.flush()
        # Now, for User1, step through the entire race yarra_boulevard_good_race_1.
        race_first = self.simulate_entire_race(aldos, track, os.path.join(os.getcwd(), config.IMPORTS_PATH, "races", "yarra_boulevard_good_race_1.gpx"))
        db.session.flush()
        # Refresh aldos.
        db.session.refresh(aldos)
        # Ensure aldos does not have an ongoing track.
        self.assertIsNone(aldos.ongoing_race)
        # Now for User2, step through the same race, but at 500 ms slower.
        race_second = self.simulate_entire_race(emily, track, os.path.join(os.getcwd(), config.IMPORTS_PATH, "races", "yarra_boulevard_good_race_1.gpx"),
            ms_adjustment = 500)
        db.session.flush()
        # Ensure aldos does not have an ongoing track.
        self.assertIsNone(aldos.ongoing_race)
        # Now for User1 again, step through the same race, but at 1000ms slower.
        race_third = self.simulate_entire_race(aldos, track, os.path.join(os.getcwd(), config.IMPORTS_PATH, "races", "yarra_boulevard_good_race_1.gpx"),
            ms_adjustment = 1000)
        db.session.flush()
        # Check there are 3 races logged in the database.
        self.assertEqual(db.session.query(models.TrackUserRace).count(), 3)
        # Get the entire leaderboard for the track.
        leaderboard = tracks.leaderboard_query_for(track).all()
        # Ensure race first is place #1, and is at the top of the leaderboard.
        self.assertEqual(leaderboard[0].uid, race_first.uid)
        self.assertEqual(leaderboard[0].finishing_place, 1)
        # Second is place #2 and in the middle.
        self.assertEqual(leaderboard[1].uid, race_second.uid)
        self.assertEqual(leaderboard[1].finishing_place, 2)
        # Third is place #3 and at the end.
        self.assertEqual(leaderboard[2].uid, race_third.uid)
        self.assertEqual(leaderboard[2].finishing_place, 3)

    def test_ratings(self):
        """Import a test GPX route.
        Create 11 Users.
        Create 6 positive votes toward the track, and 4 negative.
        Call the ratings function to receive back the dictionary.
        Ensure the above conditions.
        Get the very first User in the random Users list. Call get_user_vote and ensure the value returned is False.
        Get the very last User in the random Users list. Confirm the opposite.
        Call get_user_vote with aldos. Ensure None is returned."""
        # Create a new User.
        aldos = factory.create_user("alden@mail.com", "password",
            username = "alden", vehicle = "1994 Toyota Supra")
        # Create 10 more random Users.
        random_users = [factory.get_random_user() for x in range(10)]
        db.session.flush()
        # Test that we can load a track from GPX, and set its owner to aldos. 
        track_from_gpx = tracks.create_track_from_gpx("example1.gpx",
            intersection_check = False)
        track_from_gpx.set_owner(aldos)
        db.session.flush()
        # Get track.
        track = track_from_gpx.track
        # Now, for all 4 of the 10 Users, create negative TrackRatings, and positive TrackRatings for the balance.
        for user in random_users[:4]:
            db.session.add(models.TrackRating(track_id = track.id, user_id = user.id, rating = False))
        for user in random_users[4:]:
            db.session.add(models.TrackRating(track_id = track.id, user_id = user.id, rating = True))
        db.session.flush()
        # Now, call the ratings function.
        ratings = tracks.get_ratings_for(track)
        # Ensure there are 6 positive votes and 4 negative.
        self.assertEqual(ratings.num_positive_votes, 6)
        self.assertEqual(ratings.num_negative_votes, 4)
        # Get the very first User, and get their vote. Ensure value returned is False.
        self.assertEqual(tracks.get_user_rating(track, random_users[0]), False)
        # Get the very last User, confirm the opposite.
        self.assertEqual(tracks.get_user_rating(track, random_users[len(random_users)-1]), True)
        # Get the rating for aldos, ensure that is None.
        self.assertEqual(tracks.get_user_rating(track, aldos), None)
