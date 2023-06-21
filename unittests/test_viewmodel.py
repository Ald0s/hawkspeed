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
            username = "alden")
        db.session.flush()
        # Load the yarra boulevard test track.
        track_from_gpx = tracks.create_track_from_gpx("yarra_boulevard.gpx")
        db.session.flush()
        # Get the track and set its owner.
        track = track_from_gpx.track
        track.set_owner(aldos)
        db.session.flush()
        # Now, create a new viewmodel and serialise it.
        track_viewmodel = viewmodel.TrackViewModel(aldos, track)
        serialised_track_vm = track_viewmodel.serialise()