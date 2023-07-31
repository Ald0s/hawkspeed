import os
import time
import json
import base64

from datetime import date, datetime, timedelta
from flask import url_for
from unittests.conftest import BaseWithDataCase

from app import db, config, factory, models, login_manager, tracks, error, viewmodel


class TestUserViewModel(BaseWithDataCase):
    def test_user_view_model_basics(self):
        """Test all functionality on the vehicle view model.
        Create a new User, provide them with a single vehicle.
        Create a user view model for that User from the perspective of the same User.
        Check that is you returns True."""
        # Create a new User.
        aldos = factory.create_user("alden@mail.com", "password",
            username = "alden", vehicle = "1994 Toyota Supra")
        db.session.flush()
        # Create a user view model for this User.
        user_view_model = viewmodel.UserViewModel(aldos, aldos)
        # Ensure is you is True.
        self.assertEqual(user_view_model.is_you, True)


class TestVehicleViewModel(BaseWithDataCase):
    def test_vehicle_view_model_basics(self):
        """Test all functionality on the vehicle view model.
        Create a new User, provide them with a single vehicle.
        Create a vehicle view model for that User's first vehicle."""
        # Create a new User.
        aldos = factory.create_user("alden@mail.com", "password",
            username = "alden", vehicle = "1994 Toyota Supra")
        db.session.flush()
        # Get that User's vehicles.
        all_vehicles = aldos.all_vehicles
        # Now, create a view model for the first one.
        vehicle_view_model = viewmodel.VehicleViewModel(aldos, all_vehicles[0])


class TestTrackViewModel(BaseWithDataCase):
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
        # First should have finishing place 1, second 2 etc.
        self.assertEqual(top_leaderboard_vml.items[0].finishing_place, 1)
        self.assertEqual(top_leaderboard_vml.items[1].finishing_place, 2)
        self.assertEqual(top_leaderboard_vml.items[2].finishing_place, 3)
        # Serialise the top leaderboard.
        leaderboard_d = top_leaderboard_vml.as_dict()
        leaderboard_l = leaderboard_d["items"]
        # Ensure the first item's finishing place is 1, second is 2, third is 3 etc.
        self.assertEqual(leaderboard_l[0]["finishing_place"], 1)
        self.assertEqual(leaderboard_l[1]["finishing_place"], 2)
        self.assertEqual(leaderboard_l[2]["finishing_place"], 3)

