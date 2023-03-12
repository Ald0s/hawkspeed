import os
import time
import json
import base64

from sqlalchemy import func, asc
from datetime import date, datetime, timedelta
from flask import url_for
from tests.conftest import BaseCase

from app import db, config, factory, models, login_manager, world, tracks, races


class TestRaces(BaseCase):
    def test_races_basics(self):
        # Create a new User.
        aldos = factory.create_user("alden@mail.com", "password",
            username = "alden")
        # Create a track.
        track = tracks.create_track_from_gpx("example1.gpx")
        db.session.flush()
        # Ensure aldos does not have an ongoing race.
        self.assertIsNone(aldos.ongoing_race)
        # Create a new track user race for between this user and track.
        race = models.TrackUserRace(user = aldos, track = track)
        db.session.add(race)
        db.session.flush()
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
        finished = time.time()+20
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
