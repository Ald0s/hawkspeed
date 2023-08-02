"""A module, designed to be used along with Google Maps Roads API through a background worker. Its purpose is to snap an incoming track to the nearest roads
prior to allowing players to access and race."""
import time
import requests
import urllib.parse
import pyproj
import shapely
import logging

from marshmallow import Schema, fields, EXCLUDE, post_load

from .. import db, config, models, tracks
# from . import celery

LOG = logging.getLogger("hawkspeed.tasks.roadsapi")
LOG.setLevel( logging.DEBUG )


class SnapToRoadsError(Exception):
    ERROR_API_DISABLED = "apidisabled"
    ERROR_INVALID_KEY = "invalidkey"
    ERROR_INVALID_ENVIRONMENT = "badenv"
    ERROR_SNAP_NOT_REQUIRED = "nosnap"
    ERROR_ALREADY_SNAPPED = "alreadysnapped"

    def __init__(self, code, **kwargs):
        self.code = code


class SnapToRoadsApiError(Exception):
    """An exception thrown when snap to roads api call fails."""
    @property
    def code(self):
        return self.google_api_error.error.code
    
    @property
    def status(self):
        return self.google_api_error.error.status
    
    @property
    def message(self):
        return self.google_api_error.error.message
    
    @property
    def details(self):
        return self.google_api_error.error.details
    
    def __init__(self, google_api_error, **kwargs):
        self.google_api_error = google_api_error


class LatitudeLongitudeLiteral():
    """A container for a loaded latitude longitude pair."""
    def __init__(self, **kwargs):
        self.latitude = kwargs.get("latitude")
        self.longitude = kwargs.get("longitude")


class LatitudeLongitudeLiteralSchema(Schema):
    """A schema for deserialising a single latitude longitude pair from a snapped point.
    
    More information here:
    https://developers.google.com/maps/documentation/roads/snap#LatitudeLongitudeLiteral"""
    class Meta:
        unknown = EXCLUDE
    # The latitude. Required.
    latitude                = fields.Float(
        as_string = True, required = True, allow_none = False, data_key = "latitude")
    # The longitude. Required.
    longitude               = fields.Float(
        as_string = True, required = True, allow_none = False, data_key = "longitude")
    
    @post_load
    def latitude_longitude_literal_post_load(self, data, **kwargs) -> LatitudeLongitudeLiteral:
        return LatitudeLongitudeLiteral(**data)
    

class SnappedPoint():
    """A container for a loaded snapped point."""
    def __init__(self, **kwargs):
        self.location = kwargs.get("location")
        self.place_id = kwargs.get("place_id")
        self.original_index = kwargs.get("original_index")


class SnappedPointSchema(Schema):
    """A schema for deserialising a single snapped point from within a snap to roads response.
    
    More information here:
    https://developers.google.com/maps/documentation/roads/snap#SnappedPoint"""
    class Meta:
        unknown = EXCLUDE
    # A latitude longitude literal containing the long/lat pair. Required.
    location                = fields.Nested(LatitudeLongitudeLiteralSchema,
        many = False, required = True, allow_none = False, data_key = "location")
    # A place Id for the road segment. Required.
    place_id                = fields.Str(
        required = True, allow_none = False, data_key = "placeId")
    # The original index for this point in the source list that was sent. Optional, though, we never use interpolate so it should always load.
    original_index          = fields.Int(
        required = False, allow_none = True, load_default = None, data_key = "originalIndex")

    @post_load
    def snapped_point_post_load(self, data, **kwargs) -> SnappedPoint:
        return SnappedPoint(**data)
    

class SnapToRoadsResponse():
    """A container for an entire response to a snap to roads call."""
    def __init__(self, **kwargs):
        self.snapped_points = kwargs.get("snapped_points")
        self.warning_message = kwargs.get("warning_message")


class SnapToRoadsResponseSchema(Schema):
    """A schema for deserialising a response from the snap to roads API call.

    More information here:
    https://developers.google.com/maps/documentation/roads/snap#SnapToRoadsResponse"""
    class Meta:
        unknown = EXCLUDE
    # The list of snapped points. Not required.
    snapped_points          = fields.List(fields.Nested(SnappedPointSchema, many = False),
        required = False, load_default = None, data_key = "snappedPoints")
    # The warning message. Not required.
    warning_message         = fields.Str(
        required = False, load_default = None, data_key = "warningMessage")
    
    @post_load
    def snap_to_roads_response_post_load(self, data, **kwargs) -> SnapToRoadsResponse:
        return SnapToRoadsResponse(**data)


class HttpError():
    """A container for a loaded HTTP error, from Google.
    
    For more information and possible values:
    https://developers.google.com/maps/documentation/roads/errors#errors"""
    def __init__(self, **kwargs):
        self.code = kwargs.get("code")
        self.message = kwargs.get("message")
        self.status = kwargs.get("status")
        self.details = kwargs.get("details", None)


class HttpErrorSchema(Schema):
    """A schema for deserialising a HTTP error, from Google.
    
    For more information and possible values:
    https://developers.google.com/maps/documentation/roads/errors#errors
    
    There is an attribute called 'details' in here which is a list of dictionaries.
    TODO: implement this at some stage because it has been removed.
    details                 = fields.List(fields.Dict(keys = fields.Str(), values = fields.Str()),
        required = False, allow_none = True, load_default = None)"""
    class Meta:
        unknown = EXCLUDE
    # The HTTP status code. Required.
    code                    = fields.Int(
        required = True, allow_none = False, data_key = "code")
    # The message. Required.
    message                 = fields.Str(
        required = True, allow_none = False, data_key = "message")
    # The status code as text. Required.
    status                  = fields.Str(
        required = True, allow_none = False, data_key = "status")

    @post_load
    def http_error_post_load(self, data, **kwargs) -> HttpError:
        return HttpError(**data)


class GoogleApiError():
    """A container for a loaded Google API error."""
    def __init__(self, **kwargs):
        self.error = kwargs.get("error")


class GoogleApiErrorSchema(Schema):
    """A schema for deserialising a Google API error.
    
    For more information and possible values:
    https://developers.google.com/maps/documentation/roads/errors#errors"""
    class Meta:
        unknown = EXCLUDE
    # The HTTP error.
    error                   = fields.Nested(HttpErrorSchema, many = False, required = True, allow_none = False)
    
    @post_load
    def google_api_error_post_load(self, data, **kwargs) -> GoogleApiError:
        return GoogleApiError(**data)
    

def _snap_to_roads_api_call(full_url):
    """The default snap to roads API call that will be used if none other given. This function is ACTIVE, and will call out to Google Maps API, it
    will therefore only operate in LiveDevelopment or Production environments, attempting to call it otherwise will result in a NotImplementedError.
    
    Arguments
    ---------
    :full_url: The full URL to send a request to. Including scheme, domains and parameters.
    
    Returns
    -------
    An integer and a dict; the status code and response body respectively."""
    try:
        # Use requests to actually perform the request.
        response = requests.get(full_url)
        # If status code is 200, we will return the status code and the JSON function result.
        if response.status_code == 200:
            # Success!
            return 200, response.json()
        LOG.error(f"A snap to roads API call to {full_url} failed!\n\tStatus code: {response.status_code}")
        return response.status_code, response.json()
    except Exception as e:
        raise e
    

def send_snap_to_roads_request(batch_idx, unsnapped_points, **kwargs) -> SnapToRoadsResponse:
    """Given a batch index and a list of unsnapped shapely Points, request that Google Maps API snap all given points to closest road, then return
    the loaded response. Calling code may provide a custom function to be called with the full API call as a string; including parameters and the
    key, which should return an int and a dict; the HTTP status code and the full response object as a dict.

    If the request function fails, that is, returns a non success HTTP status code, this function will throw an instance of SnapToRoadsApiError in
    response, which will contain the loaded Google Maps API error.
    
    Arguments
    ---------
    :batch_idx: The index for the batch that is to be snapped.
    :unsnapped_points: A list of Shapely Point objects, in XY format, to snap to roads.

    Keyword arguments
    -----------------
    :snap_to_roads_base_url: A string, the base URL behind parameters. For example; https://roads.googleapis.com/v1/snapToRoads, by default, SNAP_TO_ROADS_BASE_URL is used.
    :get_request_func: A function that takes a string (the full URL) and returns an int and a dict; status code and response.
    :force_live_api: By default, using Google Maps API will fail if environment is not a Production-type. Pass True here to override. Default is False."""
    try:
        snap_to_roads_base_url = kwargs.get("snap_to_roads_base_url", config.SNAP_TO_ROADS_BASE_URL)
        get_request_func = kwargs.get("get_request_func", _snap_to_roads_api_call)
        force_live_api = kwargs.get("force_live_api", False)

        # Error out if we are not allowed to use live snap to roads API call.
        if get_request_func == _snap_to_roads_api_call and ((config.APP_ENV != "Production" and config.APP_ENV != "LiveDevelopment") and not force_live_api):
            LOG.error("Calling active snap to roads API function is not allowed in any of the test/debug environments.")
            raise SnapToRoadsError(SnapToRoadsError.ERROR_INVALID_ENVIRONMENT)
        # Encode params right here, join all points in YX (latitude,longitude) format with a comma separating, then join all points with a pipe.
        request_params_d = dict(
            path = "|".join( f"{str(latitude)},{str(longitude)}" for longitude, latitude in [point.coords[0] for point in unsnapped_points] ),
            key = config.GOOGLE_MAPS_API_KEY)
        encoded_params = urllib.parse.urlencode(request_params_d)
        # Construct the full URL.
        snap_to_roads_url = f"{snap_to_roads_base_url}{encoded_params}"
        LOG.debug(f"Performing snap to roads request for batch #{batch_idx+1} ...")
        # Now, call the request function with the full URL, and expect back a status code and a response dictionary.
        status_code, response_d = get_request_func(snap_to_roads_url)
        # If status code is 200, this is a successful attempt! Return a deserialised snapped response.
        if status_code == 200:
            # Return a deserialised snapped response.
            snap_to_roads_response_schema = SnapToRoadsResponseSchema()
            return snap_to_roads_response_schema.load(response_d)
        elif int(status_code / 100) == 4:
            # This request resulted in an API error. We will now raise a SnapToRoadsApiError exception.
            google_api_error_schema = GoogleApiErrorSchema()
            google_api_error = google_api_error_schema.load(response_d)
            LOG.warning(f"Attempting to request snapping batch #{batch_idx+1} to road failed due to an API error!")
            raise SnapToRoadsApiError(google_api_error)
        else:
            # No clue what's happening here.
            raise NotImplementedError(f"Failed to send a request to snap batch #{batch_idx+1} to road. An unhandled status code was received; {status_code}")
    except Exception as e:
        raise e
    

def get_order(track_id, **kwargs) -> models.SnapToRoadOrder:
    """Attempt to locate a snap to road order for the provided Track's ID.
    
    Arguments
    ---------
    :track_id: An ID with which to search for the order.
    
    Returns
    -------
    If exists, the snap to road order requested."""
    try:
        return db.session.query(models.SnapToRoadOrder)\
            .filter(models.SnapToRoadOrder.track_id == track_id)\
            .first()
    except Exception as e:
        raise e
    

def new_order(track, **kwargs) -> models.SnapToRoadOrder:
    """Creates and returns a new snap to road order object with unsnapped and snapped geometries included. The object is returned but is not added to the
    session so calling code should manage this. This function does not check to ensure the given track is not already verified before creating a new order.
    
    Arguments
    ---------
    :track: An instance of Track for which to create a new snap to road order.
    
    Returns
    -------
    An instance of SnapToRoadOrder, not added to the session."""
    try:
        # Get the Track's entire path, as a linestring.
        track_path = track.path
        multi_linestring = track_path.multi_linestring
        """TODO: for now, there is only a single linestring in the multilinestring, since we only support single segment tracks."""
        linestring = multi_linestring.geoms[0]
        # Create a SnapToRoadOrder and set its Track instance, and static number of points to expect.
        new_order = models.SnapToRoadOrder()
        new_order.set_track(track)
        new_order.set_static_num_points(len(linestring.coords))
        # Create two new SnapToRoadTrack instances; one for snapped and one for unsnapped. Set the CRS to duplicate track path on both.
        unsnapped_track = models.SnapToRoadTrack()
        unsnapped_track.set_crs(track_path.crs)
        snapped_track = models.SnapToRoadTrack()
        snapped_track.set_crs(track_path.crs)
        # Populate the unsnapped track with all points in the line string above.
        for point_idx, point_t in enumerate(linestring.coords):
            # Construct a new Point.
            point = shapely.geometry.Point(point_t)
            # Now, construct a new track point. Set CRS to duplicate track path's CRS, and then set the actual point to that above.
            track_point = models.SnapToRoadTrackPoint()
            track_point.set_crs(track_path.crs)
            track_point.set_position(point)
            # Set the absolute index to the current coordinate's index.
            track_point.set_absolute_idx(point_idx)
            # Add this track point to the unsnapped track.
            unsnapped_track.add_point(track_point)
        # Set both snap to road tracks on the order.
        new_order.set_snapped_track(snapped_track)
        new_order.set_unsnapped_track(unsnapped_track)
        # Return the resulting object.
        return new_order
    except Exception as e:
        raise e
    

def iter_unsnapped_batches(order, **kwargs):
    """Iterate the required times for remaining unsnapped batches, and on each iteration, yield both the current batch index, and the list of snap to road track
    points that should be snapped. Keep repeating this until there are no more.
    
    Arguments
    ---------
    :order: An instance of SnapToRoadOrder, from which to iterate unsnapped batches."""
    try:
        # Get the unsnapped track points as a list right now, since the unsnapped track itself will have points recursively removed.
        all_unsnapped_track_points = order.unsnapped_track.track_points
        batch_idx = 0
        # Now, iterate unsnapped batches as required.
        for current_point_idx in range(0, len(all_unsnapped_track_points), config.NUM_POINTS_PER_SNAP_BATCH):
            # For each batch index, get a range of points from the geodetic linestring, NUM_POINTS_PER_SNAP_BATCH in length.
            unsnapped_track_points = all_unsnapped_track_points[current_point_idx:current_point_idx+config.NUM_POINTS_PER_SNAP_BATCH]
            # Yield both the batch index and the list of unsnapped track points.
            yield batch_idx, unsnapped_track_points
            batch_idx = batch_idx + 1
    except Exception as e:
        raise e


def process_snapped_points(order, unsnapped_track_points, snapped_points, **kwargs) -> models.SnapToRoadOrder:
    """Process the unsnapped track points against the given snapped points. There must be a one-to-one relationship between the two lists, when snapped
    points is sorted in ascending order by original index. As unsnapped points are processed, their snapped equivalents are added to the snapped track, but
    not deleted from unsnapped.
    
    Arguments
    ---------
    :order: An instance of SnapToRoadOrder.
    :unsnapped_track_points: A list of SnapToRoadTrackPoint, all of them unsnapped.
    :snapped_points: A list of SnappedPoint containers - received from Google Maps API.
    
    Returns
    -------
    The order, having been modified such that unsnapped points are now moved to snapped points."""
    try:
        # Verify there's the correct number of points among unsnapped and snapped.
        if len(unsnapped_track_points) != len(snapped_points):
            """TODO: handle this properly."""
            raise NotImplementedError(f"process_snapped_points could not continue due to a length mismatch. We should have snapped {len(unsnapped_track_points)} points, but instead only snapped {len(snapped_points)}")
        # Get both the snapped and unsnapped tracks.
        unsnapped_track = order.unsnapped_track
        snapped_track = order.snapped_track
        # Make a transformer from unsnapped track's geodetic CRS to unsnapped track's normal CRS. Essentially, an inverted geodetic transformer.
        transformer_ = pyproj.Transformer.from_crs(unsnapped_track.geodetic_crs_object, unsnapped_track.crs_object,
            always_xy = True)
        # Otherwise, we'll run a zip on the unsnapped track points, and the snapped points sorted by their original index; this index of course being relative only to the order of the unsnapped track points list
        # vs the list we've received in response from the server.
        for unsnapped_track_point, snapped_point_ in zip(unsnapped_track_points, sorted(snapped_points, key = lambda x: x.original_index)):
            # Add a new track point, representing the snapped point, to the snapped point track, with the same absolute index as the unsnapped point has.
            snapped_track_point = models.SnapToRoadTrackPoint()
            snapped_track_point.set_absolute_idx(unsnapped_track_point.absolute_idx)
            # Set CRS to match that of unsnapped track's.
            snapped_track_point.set_crs(unsnapped_track.crs)
            # Now, create a Shapely point in format XY from the latitude & longitude in snapped point. Transform this using the geodetic 
            geodetic_snapped_point = shapely.geometry.Point(snapped_point_.location.longitude, snapped_point_.location.latitude)
            # Transform this geodetic snapped point via transformer.
            snapped_point = shapely.ops.transform(transformer_.transform, geodetic_snapped_point)
            # Set this snapped point as the position on our new snapped track point.
            snapped_track_point.set_position(snapped_point)
            # Finally, add the snapped track point to our snapped track.
            snapped_track.add_point(snapped_track_point)
        # Successfully made it this far, return the order.
        LOG.debug(f"Successfully verified {len(snapped_points)} points, and moved from unsnapped to snapped.")
        return order
    except Exception as e:
        raise e


class SnapToRoadResult():
    """A container for the result of snapping a track's path to roads via Google Maps API."""
    @property
    def is_successful(self):
        """Returns True if this snap to road order has been successful."""
        return self._successful
    
    @property
    def time_taken(self):
        """Returns the total time, in seconds, this snap to road order took to complete."""
        return self._time_taken
    
    @property
    def track(self):
        """Return the Track that has been snapped, or failed to be snapped."""
        return self._track
    
    def __init__(self, _track, **kwargs):
        self._track = _track
        self._error_exc = kwargs.get("exception", None)
        self._time_taken = None
        # If snap to road order is not None, we'll extract information from it, then delete it.
        _snap_to_road_order = kwargs.get("snap_to_road_order", None)
        if _snap_to_road_order:
            self._successful = True
            self._destroy_order(_snap_to_road_order)
        else:
            self._successful = False

    def _destroy_order(self, snap_to_road_order, **kwargs):
        # Calculate time taken.
        self._time_taken = time.time() - snap_to_road_order.created
        # Destroy the order.
        db.session.delete(snap_to_road_order)


def snap_to_road(track, **kwargs) -> SnapToRoadResult:
    """Given a Track instance, communicate with Google API to snap its geometry to the nearest road. This function will not execute if no maps API key is set,
    API functions are disabled or an API function fails due to billing issues at any point. This step will set the track as snapped to roads upon success, and
    will fail if we are configured to not use google maps or no google maps API key is given. This step will automatically set the track as snapped if we are
    configured to not require snapping to roads.
    
    Arguments
    ---------
    :track: The Track to snap to road.

    Keyword arguments
    -----------------
    :force_live_api: By default, using Google Maps API will fail if environment is not a Production-type. Pass True here to override. Default is False.
    
    Returns
    -------
    An instance of SnapToRoadResult, showing the result of this order."""
    try:
        force_live_api = kwargs.get("force_live_api", False)

        if track.is_snapped_to_roads:
            LOG.warning(f"Attempted to pass a Track that has already been snapped to roads. This will not continue.")
            raise SnapToRoadsError(SnapToRoadsError.ERROR_ALREADY_SNAPPED)
        elif not config.REQUIRE_SNAP_TO_ROADS:
            raise SnapToRoadsError(SnapToRoadsError.ERROR_SNAP_NOT_REQUIRED)
        elif not config.USE_GOOGLE_MAPS_API:
            LOG.warning(f"Attempt to snap a Track to road will not continue; server is configured against using Google Maps API. We will therefore verify this track as it is.")
            raise SnapToRoadsError(SnapToRoadsError.ERROR_API_DISABLED)
        elif not config.GOOGLE_MAPS_API_KEY:
            LOG.error(f"Failed to snap track {track} to road! We are configured to use Google Maps API, but there's no key set.")
            raise SnapToRoadsError(SnapToRoadsError.ERROR_INVALID_KEY)
        # Check for an existing snap to road order for this track, create one if it does not already exist.
        order = get_order(track.id)
        if not order:
            LOG.debug(f"No snap-to-roads order exists for given track {track}, creating one now...")
            order = new_order(track)
            # Remember to add to session, flush to persist.
            db.session.add(order)
            db.session.flush()
        else:
            LOG.debug(f"Continuing snap-to-roads for track {track}. We have snapped {order.percent_snapped}% to roads.")
        # Iterate to the number of batches to snap remaining for this order.
        for batch_idx, unsnapped_track_points in iter_unsnapped_batches(order):
            try:
                # For each batch of unsnapped points, send a snap to roads request. On success, this should return a snap to roads response.
                # At this point, we will map the unsnapped track points to a list of geodetic Shapely points.
                unsnapped_points = [track_point.geodetic_point for track_point in unsnapped_track_points]
                snap_to_roads_response = send_snap_to_roads_request(batch_idx, unsnapped_points,
                    force_live_api = force_live_api)
                # From the response, get the snapped points container and the warning message.
                snapped_points = snap_to_roads_response.snapped_points
                warning_message = snap_to_roads_response.warning_message
                # If warning message is not None, this is a failure case and we must handle this.
                if warning_message:
                    """TODO: Handle this warning message. If it communicates a failure due to billing or whatever, raise another exception that will result in
                    the use of the Google API being disabled."""
                    raise NotImplementedError("snap_to_road failed because warning message was not None, and we haven't handled this yet.")
                # Process the snapped points into database, receiving back the order.
                order = process_snapped_points(order, unsnapped_track_points, snapped_points)
                # We will flush after every successful processing of a batch.
                db.session.flush()
            except SnapToRoadsError as stna:
                # Failed to run snap to roads as the API is currently not allowed in this environment.
                db.session.delete(order)
                raise stna
            except SnapToRoadsApiError as strae:
                # API error occurred, there's no reason to every continue if this ever occurs.
                db.session.delete(order)
                raise strae
        # Calculate a new hash for this track's path, set that on the track's path.
        path_b2b_hash = tracks.calculate_track_path_hash(track.path)
        track.path.set_hash(path_b2b_hash)
        # Made it all the way through. We can now set this track to a snapped status.
        track.set_snapped_to_roads(True)
        # Return a result object here.
        return SnapToRoadResult(track, 
            snap_to_road_order = order)
    except SnapToRoadsApiError as strae:
        # A google maps API error has occurred. This is where we'll determine if this error should result in the disabling of access to maps.
        if strae.code == 400:
            """TODO: handle this."""
            raise NotImplementedError("SnapToRoads API error not correctly handled.")
        elif strae.code == 403:
            """TODO: handle this."""
            raise NotImplementedError("SnapToRoads API error not correctly handled.")
        elif strae.code == 404:
            """TODO: handle this."""
            raise NotImplementedError("SnapToRoads API error not correctly handled.")
        elif strae.code == 429:
            """TODO: handle this."""
            raise NotImplementedError("SnapToRoads API error not correctly handled.")
        else:
            raise NotImplementedError(f"Failed to process snap to roads API error! Unrecognised google maps API error: Code: {strae.code}, Status: {strae.status}, Message: {strae.message}")
    except SnapToRoadsError as stna:
        # Now, perform actions based on value of code.
        if stna.code == SnapToRoadsError.ERROR_SNAP_NOT_REQUIRED:
            # Snap to roads is not required, we'll simply therefore set this track to snapped.
            LOG.warning(f"We are configured to not require snapping to roads for new race tracks. The track {track} will therefore be immediately set as snapped to roads.")
            track.set_snapped_to_roads(True)
        # Return a result here.
        return SnapToRoadResult(track, 
            exception = stna)
    except Exception as e:
        # Re-raise any unknown exception.
        raise e
    finally:
        # Always commit on the way out.
        db.session.commit()