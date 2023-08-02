import os
import time
import math
import json
import urllib.parse
import base64

from sqlalchemy import func
from datetime import date, datetime, timedelta
from unittests.conftest import BaseWithDataCase

from app import db, config, models, factory, error
from app.tasks import roadsapi


def _fake_snap_to_roads_api_call(full_url):
        """For our fake api call, we'll simply process the incoming URL, parse the parameters, then return all the points provided as a Google response."""
        # Parse the URL into a ParseResult.
        parse_result = urllib.parse.urlparse(full_url)
        # Now, parse qs on the query parameters of the result, to get a dictionary of all arguments decoded.
        query_arguments = urllib.parse.parse_qs(parse_result.query)
        # Now, get the path, which is a string, and split it by pipes. This is an array of coordinates joined by commas.
        coordinates_l = query_arguments["path"][0].split("|")
        # Split these coordinates by a comma each, and create dicts compatible with latitude longitude literals.
        latitude_longitude_literals_l = [dict(latitude = pt[0], longitude = pt[1]) for pt in [ptx.split(",") for ptx in coordinates_l]]
        # Now, map these to a snapped point containing each, with equivalent indicies.
        snapped_points_l = [dict(location = lll, originalIndex = idx, placeId = "PLACE") for idx, lll in enumerate(latitude_longitude_literals_l)]
        # Now, build a snap to roads response compatible dict from all this.
        snap_to_roads_response_d = dict(
            snappedPoints = snapped_points_l, warningMessage = None)
        # This is our response. We will now return this along with 200 OK.
        return 200, snap_to_roads_response_d


class TestRoadsAPI(BaseWithDataCase):    
    def _get_test_track(self, user):
        # Import a test track.
        track = self.create_track_from_gpx(user, "yarra_boulevard.gpx",
            is_verified = True, is_snapped_to_roads = True)
        db.session.flush()
        return track

    def test_process_snapped_points(self):
        """Test the processing of snapped points.
        Create a new test User and import a test track. There should be 143 total points.
        Get the very first batch of points from the unsnapped collection; batches should be 100 points long.
        Transform these points into SnappedPoint containers - as can be expected from server.
        Pass these to be processed.
        Expect this operation to be successful.
        Expect 100 points in total to be snapped.
        Expect 43 points in total to be unsnapped.
        Expect this order to be 69% complete."""
        # Create a random User.
        user1 = self.get_random_user()
        db.session.flush()
        # Create a track for this User.
        track = self._get_test_track(user1)
        # Call the new order function for this track.
        new_order = roadsapi.new_order(track)
        db.session.add(new_order)
        db.session.flush()
        # Get a generator for batches.
        batch_it = roadsapi.iter_unsnapped_batches(new_order)
        # Get an unsnapped batch.
        batch_idx, unsnapped_points = next(batch_it)
        # Now, process this list of points into a list of SnappedPoint containers.
        snapped_points = [roadsapi.SnappedPoint(
            location = roadsapi.LatitudeLongitudeLiteral(latitude = up.geodetic_point.y, longitude = up.geodetic_point.x), place_id = "EXAMPLEPLACE", original_index = idx) for idx, up in enumerate(unsnapped_points)]
        # Now, pass these to be processed.
        new_order = roadsapi.process_snapped_points(new_order, unsnapped_points, snapped_points)
        # Expect the operation to pass.
        db.session.flush()
        # Expect 100 points have been snapped.
        self.assertEqual(new_order.num_points_snapped, 100)
        # Expect 43 points have yet to be snapped.
        self.assertEqual(new_order.num_points_unsnapped, 43)
        # Expect this to be 69% done.
        self.assertEqual(new_order.percent_snapped, 69)
        # Get the next unsnapped batch.
        batch_idx, unsnapped_points = next(batch_it)
        # Ensure batch idx is 1, ensure there's 43 points in unsnapped points.
        self.assertEqual(batch_idx, 1)
        self.assertEqual(len(unsnapped_points), 43)
    
    def test_iter_unsnapped_batches(self):
        """Test iterating unsnapped batches.
        Create an order for an example track. There should be 143 total points.
        Use currently configured NUM_POINTS_PER_SNAP_BATCH to determine the total number of unsnapped batches at the moment.
        Confirm that our calculated number of batches is equal to that returned by the order's property.
        Now, actually execute the iterate function, and ensure we get the correct batches of points."""
        # Create a random User.
        user1 = self.get_random_user()
        db.session.flush()
        # Create a track for this User.
        track = self._get_test_track(user1)
        # Call the new order function for this track.
        new_order = roadsapi.new_order(track)
        db.session.add(new_order)
        db.session.flush()
        # Determine the number of batches. Ceil total num points div num pts per batch.
        num_unsnapped_batches = math.ceil(new_order.static_num_points / config.NUM_POINTS_PER_SNAP_BATCH)
        # Ensure the num unsnapped is equal to that on the property.
        self.assertEqual(num_unsnapped_batches, new_order.num_unsnapped_batches)
        total_batches = list(roadsapi.iter_unsnapped_batches(new_order))
        # Ensure there's num_unsnapped_batches.
        self.assertEqual(len(total_batches), num_unsnapped_batches)
        # Ensure there's 100 points in the first batch, and 43 in the second.
        self.assertEqual(len(total_batches[0][1]), 100)
        self.assertEqual(len(total_batches[1][1]), 43)
    
    def test_get_order(self):
        """Test ability to get an existing order.
        Call the get order function with the track's ID.
        Ensure the result is not None, and ensure the result is attached to that track."""
        # Create a random User.
        user1 = self.get_random_user()
        db.session.flush()
        # Create a track for this User.
        track = self._get_test_track(user1)
        # Call the new order function for this track.
        new_order = roadsapi.new_order(track)
        db.session.add(new_order)
        db.session.flush()
        # Call the get order function.
        existing_order = roadsapi.get_order(track.id)
        # Ensure this is not None.
        self.assertIsNotNone(existing_order)
        # Ensure this order is attached to the track.
        self.assertEqual(existing_order.track, track)
    
    def test_send_snap_to_roads_request(self):
        """Test the new order function.
        Call the new order function, get back a new order."""
       # Create a random User.
        user1 = self.get_random_user()
        db.session.flush()
        # Create a track for this User.
        track = self._get_test_track(user1)
        # Call the new order function for this track.
        new_order = roadsapi.new_order(track)
        db.session.flush()
        # Get a next on iter unsnapped batches.
        batch_idx, unsnapped_points = next(roadsapi.iter_unsnapped_batches(new_order))
        # Now, transform all points in the batch, to their geodetic equivalents.
        geodetic_unsnapped_points = [track_point.geodetic_point for track_point in unsnapped_points]
        # Perform a snap to roads request, given our fake function above.
        snap_to_roads_response = roadsapi.send_snap_to_roads_request(0, geodetic_unsnapped_points,
            get_request_func = _fake_snap_to_roads_api_call)
        # Now, ensure the value we've received is valid. Ensure there's NUM_POINTS_PER_SNAP_BATCH total entries in snapped points.
        self.assertEqual(len(snap_to_roads_response.snapped_points), config.NUM_POINTS_PER_SNAP_BATCH)
        # Actual coordinates have not changed at all, so we can also ensure correctness by ensuring start and end coordinates are the same.
        self.assertEqual(geodetic_unsnapped_points[0].x, snap_to_roads_response.snapped_points[0].location.longitude)
        self.assertEqual(geodetic_unsnapped_points[0].y, snap_to_roads_response.snapped_points[0].location.latitude)
        self.assertEqual(geodetic_unsnapped_points[config.NUM_POINTS_PER_SNAP_BATCH-1].x, snap_to_roads_response.snapped_points[config.NUM_POINTS_PER_SNAP_BATCH-1].location.longitude)
        self.assertEqual(geodetic_unsnapped_points[config.NUM_POINTS_PER_SNAP_BATCH-1].y, snap_to_roads_response.snapped_points[config.NUM_POINTS_PER_SNAP_BATCH-1].location.latitude)

    def test_new_order(self):
        """Test the new order function.
        Call the new order function, get back a new order.
        Ensure there's 143 total static points in the order.
        Ensure 0 percent is snapped.
        Ensure not complete.
        Ensure there's 143 points in unsnapped track, and 0 points in snapped track."""
        # Create a random User.
        user1 = self.get_random_user()
        db.session.flush()
        # Create a track for this User.
        track = self._get_test_track(user1)
        # Call the new order function for this track.
        new_order = roadsapi.new_order(track)
        db.session.flush()
        # Ensure in total there's 143 points.
        self.assertEqual(new_order.static_num_points, 143)
        # Ensure 0 percent is snapped.
        self.assertEqual(new_order.percent_snapped, 0)
        # Ensure not complete.
        self.assertEqual(new_order.is_complete, False)
        # Ensure there's 143 points in unsnapped track and 0 in snapped.
        self.assertEqual(new_order.unsnapped_track.num_points, 143)
        self.assertEqual(new_order.snapped_track.num_points, 0)
    
    def test_attempt_live_api_in_dbg(self):
        """Test the roadsapi module's error receovery capability if a snap to roads request is performed while in test mode.
        Call the new order function, get back a new order."""
        # Set config to require snap to roads, use google maps and also set a dud key.
        config.USE_GOOGLE_MAPS_API = True
        config.REQUIRE_SNAP_TO_ROADS = True
        config.GOOGLE_MAPS_API_KEY = "ASDASDAJNDASJD"
        # Create a random User.
        user1 = self.get_random_user()
        db.session.flush()
        # Import a test track.
        track = self.create_track_from_gpx(user1, "yarra_boulevard.gpx",
            is_verified = True, is_snapped_to_roads = False)
        db.session.flush()
        # Ensure this track is not snapped to roads.
        self.assertEqual(track.is_snapped_to_roads, False)
        # Ensure we have 0 entries for all the following tables; SnapToRoadOrder, SnapToRoadTrack, SnapToRoadTrackPoint.
        self.assertEqual(db.session.query(func.count(models.SnapToRoadOrder.id)).scalar(), 0)
        self.assertEqual(db.session.query(func.count(models.SnapToRoadTrack.id)).scalar(), 0)
        self.assertEqual(db.session.query(func.count(models.SnapToRoadTrackPoint.id)).scalar(), 0)
        # Now, attempt to invoke roadsapi to snap this track to roads.
        snap_result = roadsapi.snap_to_road(track)
        # Ensure this was a failure.
        self.assertEqual(snap_result.is_successful, False)
        # Ensure we STILL have 0 entries for all the following tables; SnapToRoadOrder, SnapToRoadTrack, SnapToRoadTrackPoint.
        self.assertEqual(db.session.query(func.count(models.SnapToRoadOrder.id)).scalar(), 0)
        self.assertEqual(db.session.query(func.count(models.SnapToRoadTrack.id)).scalar(), 0)
        self.assertEqual(db.session.query(func.count(models.SnapToRoadTrackPoint.id)).scalar(), 0)

    def test_google_api_error(self):
        """Given a test Google API error that we can expect, ensure we can retrieve all data points from it.
        Load the error dictionary via the schema.
        With the result, ensure code, message and status all match.
        Ensure there's 1 detail."""
        test_api_error_d = {"error": {"code": 403, "message": "Requests from this Android client application <empty> are blocked.", "status": "PERMISSION_DENIED", "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_ANDROID_APP_BLOCKED", "domain": "googleapis.com", "metadata": {"consumer": "projects/XXXXXXXXXX", "service": "roads.googleapis.com"}}]}}
        # Load the error dict.
        google_api_error = roadsapi.GoogleApiErrorSchema().load(test_api_error_d)
        # Now, ensure the code matches.
        self.assertEqual(google_api_error.error.code, 403)
        # Ensure status catches.
        self.assertEqual(google_api_error.error.status, "PERMISSION_DENIED")
        # Ensure message matches.
        self.assertEqual(google_api_error.error.message, "Requests from this Android client application <empty> are blocked.")