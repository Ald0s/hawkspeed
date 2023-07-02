import os
import time
import json
import base64

from datetime import date, datetime, timedelta
from flask import url_for
from unittests.conftest import BaseCase

from app import db, config, factory, models, login_manager, tracks, error, viewmodel


class TestTrackViewModel(BaseCase):
    def test_track_view_model_basics(self):
        """Import an example track; yarraboulevard."""
        # Create a new User.
        aldos = factory.create_user("alden@mail.com", "password",
            username = "alden", vehicle = "1994 Toyota Supra")
        db.session.flush()
        # Load the yarra boulevard test track.
        track = self.create_track_from_gpx(aldos, "yarra_boulevard.gpx")
        # Now, create a new viewmodel and serialise it.
        track_viewmodel = viewmodel.TrackViewModel(aldos, track)
        serialised_track_vm = track_viewmodel.serialise()
    
    def test_track_leaderboard(self):
        """Import an example track; yarra bouelvard and create three example Users.
        Create 4 TrackUserRace for this track, two attempts belonging to the first User, 1 attempt belonging to the second user and the rest belonging to
        the third User. The two attempts belonging to user1 should be in places 1st and 3rd, the 1 attempt by second should be in place 2nd and the balance
        should just be 4."""
        # Create 3 random users.
        user1 = self.get_random_user()
        user2 = self.get_random_user()
        user3 = self.get_random_user()
        # Import a test track.
        track = self.create_track_from_gpx(user1, "yarra_boulevard.gpx")
        # Now, create 5 track user races.
        # First place, user1; started 2020/10/15 22:45:01 finished 2020/10/15 22:48:26, this will occupy first spot.
        # Second place, user2; started 2020/10/16 22:55:01 finished 2020/10/16 23:02:26, this will occupy second spot.
        # Third place, user1; started 2020/10/16 23:05:01 finished 2020/10/16 23:13:26, this will occupy third spot.
        # Fourth place, user3; started 2020/10/16 23:20:01 finished 2020/10/16 23:36:26, this will occupy fourth spot.
        track_user_races = [
            self.make_finished_track_user_race(track, user1, 1602762301000, 1602762481000),
            self.make_finished_track_user_race(track, user2, 1602849301000, 1602849721000),
            self.make_finished_track_user_race(track, user1, 1602849901000, 1602850381000),
            self.make_finished_track_user_race(track, user3, 1602850801000, 1602851761000)
        ]
        db.session.flush()
        # Now, create a new track view model from user1 to the track.
        track_view_model = viewmodel.TrackViewModel(user1, track)
        # Get the top leaderboard.
        top_leaderboard_vml = track_view_model.top_leaderboard
        # Ensure there are three items in the leaderboard.
        self.assertEqual(top_leaderboard_vml.num_items, 3)
        # Get the top of the leaderboard.
        entry = top_leaderboard_vml.items[0]
        # Ensure the vehicle is 1994 Toyota Supra.
        self.assertEqual(entry.vehicle.title, "1994 Toyota Supra")

