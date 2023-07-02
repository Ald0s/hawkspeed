import os
import time
import json
import base64

from shapely import geometry

from sqlalchemy import func, asc
from datetime import date, datetime, timedelta
from flask import url_for
from unittests.conftest import BaseCase, PlayerRaceGPXSimulator

from app import db, config, factory, models, login_manager, world, tracks, races, draw, error


class TestRaces(BaseCase):
    def test_races_basics(self):
        # Create a new User.
        aldos = factory.create_user("alden@mail.com", "password",
            username = "alden", vehicle = "1994 Toyota Supra")
        db.session.flush()
        vehicle = aldos.vehicles.first()
        # Create a track.
        track = self.create_track_from_gpx(aldos, "example1.gpx")
        db.session.flush()
        # Ensure aldos does not have an ongoing race.
        self.assertIsNone(aldos.ongoing_race)
        # Create a new track user race for between this user and track.
        race = models.TrackUserRace(
            started = 1678508080000)
        race.set_track_and_user(track, aldos)
        race.set_vehicle(vehicle)
        # Set the CRS for this geometry.
        race.set_crs(config.WORLD_CONFIGURATION_CRS)
        db.session.add(race)
        db.session.flush()
        # Refresh aldos.
        db.session.refresh(aldos)
        # Ensure aldos now has an ongoing race.
        self.assertIsNotNone(aldos.ongoing_race)
        # Check that, via instance property, race is ongoing.
        self.assertEqual(race.is_ongoing, True)
        # Check that, via expression property, race is ongoing.
        race_ = db.session.query(models.TrackUserRace)\
            .filter(models.TrackUserRace.user_id == aldos.id)\
            .filter(models.TrackUserRace.track_id == track.id)\
            .filter(models.TrackUserRace.is_ongoing == True)\
            .first()
        self.assertIsNotNone(race_)
        # Now, set the race finished.
        finished = (time.time()*1000)+20000
        race.set_finished(finished)
        db.session.flush()
        # Ensure it is no longer ongoing.
        self.assertEqual(race.is_ongoing, False)
        race_ = db.session.query(models.TrackUserRace)\
            .filter(models.TrackUserRace.user_id == aldos.id)\
            .filter(models.TrackUserRace.track_id == track.id)\
            .filter(models.TrackUserRace.is_ongoing == False)\
            .first()
        self.assertIsNotNone(race_)

    def test_race_track_progress(self):
        # Create a new User.
        aldos = factory.create_user("alden@mail.com", "password",
            username = "alden", vehicle = "1994 Toyota Supra")
        db.session.flush()
        vehicle = aldos.vehicles.first()
        # Create a track.
        track = self.create_track_from_gpx(aldos, "example1.gpx")
        db.session.flush()
        # Start a new race for this User and this track.
        # Create a new track user race for between this user and track.
        race = models.TrackUserRace(
            started = 1678508080000)
        race.set_track_and_user(track, aldos)
        race.set_vehicle(vehicle)
        # Set the CRS for this geometry.
        race.set_crs(config.WORLD_CONFIGURATION_CRS)
        db.session.add(race)
        db.session.flush()
        # Ensure this race has no progress.
        self.assertEqual(race.has_progress, False)
        # Ensure the line string geometry is None.
        self.assertIsNone(race.linestring)
        # Now, create two UserLocations, which represent the first two points in the track. We'll use the first two locations in example1 exactly.
        user_locations = [
            world.prepare_user_location(dict(latitude = -37.843652, longitude = 145.03001, logged_at = 1678508081000, speed = 70.0, rotation = 180.0)),
            world.prepare_user_location(dict(latitude = -37.84354, longitude = 145.029053, logged_at = 1678508082000, speed = 70.0, rotation = 180.0))
        ]
        # Associate all with the User, so they are all granted a User ID.
        for x in user_locations:
            aldos.add_location(x)
        db.session.flush()
        # Once we've prepared these locations, we'll add them to the race ongoing.
        race.add_location(user_locations[0])
        db.session.flush()
        # Ensure there is still no progress.
        self.assertEqual(race.has_progress, False)
        race.add_location(user_locations[1])
        db.session.flush()
        # Now, ensure we have progress.
        self.assertEqual(race.has_progress, True)

    def test_race_track_bad_shortcut_1(self):
        # Create a new User.
        aldos = factory.create_user("alden@mail.com", "password",
            username = "alden", vehicle = "1994 Toyota Supra")
        db.session.flush()
        vehicle = aldos.vehicles.first()
        # Create a track.
        track = self.create_track_from_gpx(aldos, "yarra_boulevard.gpx")
        db.session.flush()
        # Simulate a race where the Player takes an unauthorised shortcut.
        race = self.simulate_entire_race(aldos, track, os.path.join(os.getcwd(), config.IMPORTS_PATH, "races", "yarra_boulevard_dq_bad_shortcut.gpx"))
        db.session.flush()
        # Ensure thsi race has been disqualified.
        self.assertEqual(race.is_disqualified, True)

    def test_race_track_bad_shortcut_2(self):
        # Create a new User.
        aldos = factory.create_user("alden@mail.com", "password",
            username = "alden", vehicle = "1994 Toyota Supra")
        db.session.flush()
        vehicle = aldos.vehicles.first()
        # Create a track.
        track = self.create_track_from_gpx(aldos, "yarra_boulevard.gpx")
        db.session.flush()
        # Simulate a race where the Player takes an unauthorised shortcut.
        race = self.simulate_entire_race(aldos, track, os.path.join(os.getcwd(), config.IMPORTS_PATH, "races", "yarra_boulevard_dq_bad_shortcut_2_notdone.gpx"))
        db.session.flush()
        # Ensure thsi race has been disqualified.
        self.assertEqual(race.is_disqualified, True)

    def test_race_good_race(self):
        # Create a new User.
        aldos = factory.create_user("alden@mail.com", "password",
            username = "alden", vehicle = "1994 Toyota Supra")
        db.session.flush()
        vehicle = aldos.vehicles.first()
        # Create a track.
        track = self.create_track_from_gpx(aldos, "yarra_boulevard.gpx")
        db.session.flush()
        # Now, for User1, step through the entire race yarra_boulevard_good_race_1.
        race = self.simulate_entire_race(aldos, track, os.path.join(os.getcwd(), config.IMPORTS_PATH, "races", "yarra_boulevard_good_race_1.gpx"))
        db.session.flush()
        # Ensure the race is now finished.
        self.assertEqual(race.is_finished, True)
