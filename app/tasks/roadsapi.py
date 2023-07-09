"""A module, designed to be used along with Google Maps Roads API through a background worker. Its purpose is to snap an incoming track to the nearest roads
prior to allowing players to access and race."""
import requests
import urllib.parse
import pyproj
import shapely
import logging

from marshmallow import Schema, fields, EXCLUDE, post_load

from .. import db, config, models
# from . import celery

LOG = logging.getLogger("hawkspeed.tasks.roadsapi")
LOG.setLevel( logging.DEBUG )


class SnapToRoadsApiError(Exception):
    """An exception thrown when snap to roads api call fails."""
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


class GoogleApiError():
    """A container for a loaded Google API error."""
    def __init__(self, **kwargs):
        self.code = kwargs.get("code")
        self.message = kwargs.get("message")
        self.status = kwargs.get("status")


class GoogleApiErrorSchema(Schema):
    """A schema for deserialising a Google API error.
    
    For more information and possible values:
    https://developers.google.com/maps/documentation/roads/errors#errors"""
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
        if config.APP_ENV != "Production" and config.APP_ENV != "LiveDevelopment":
            raise NotImplementedError("Calling active snap to roads API function is not allowed in any of the test/debug environments.")
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
    :get_request_func: A function that takes a string (the full URL) and returns an int and a dict; status code and response."""
    try:
        snap_to_roads_base_url = kwargs.get("snap_to_roads_base_url", config.SNAP_TO_ROADS_BASE_URL)
        get_request_func = kwargs.get("get_request_func", _snap_to_roads_api_call)

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
        # Now, iterate unsnapped batches as required.
        for batch_idx in range(0, len(all_unsnapped_track_points), config.NUM_POINTS_PER_SNAP_BATCH):
            # For each batch index, get a range of points from the geodetic linestring, NUM_POINTS_PER_SNAP_BATCH in length.
            unsnapped_track_points = all_unsnapped_track_points[batch_idx:batch_idx+config.NUM_POINTS_PER_SNAP_BATCH]
            # Yield both the batch index and the list of unsnapped track points.
            yield batch_idx, unsnapped_track_points
    except Exception as e:
        raise e


def process_snapped_points(order, unsnapped_track_points, snapped_points, **kwargs) -> models.SnapToRoadOrder:
    """Process the unsnapped track points against the given snapped points. There must be a one-to-one relationship between the two lists, when snapped
    points is sorted in ascending order by original index.
    
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
            snapped_track_point.set_absolute_idx(unsnapped_track_point.absolute_index)
            # Set CRS to match that of unsnapped track's.
            snapped_track_point.set_crs(unsnapped_track.crs)
            # Now, create a Shapely point in format XY from the latitude & longitude in snapped point. Transform this using the geodetic 
            geodetic_snapped_point = shapely.geometry.Point(snapped_point_.longitude, snapped_point_.latitude)
            # Transform this geodetic snapped point via transformer.
            snapped_point = shapely.ops.transform(transformer_.transform, geodetic_snapped_point)
            # Set this snapped point as the position on our new snapped track point.
            snapped_track_point.set_position(snapped_point)
            # Finally, add the snapped track point to our snapped track.
            snapped_track.add_point(snapped_track_point)
            # Now, we'll delete the unsnapped track point from the unsnapped track and delete it.
            unsnapped_track.remove_point(unsnapped_track_point)
            db.session.delete(unsnapped_track_point)
        # Successfully made it this far, return the order.
        LOG.debug(f"Successfully verified {len(snapped_points)} points, and moved from unsnapped to snapped.")
        return order
    except Exception as e:
        raise e


def snap_to_road(track, **kwargs):
    """Given a Track instance, communicate with Google API to snap its geometry to the nearest road. This function will not execute if no maps API key is set,
    API functions are disabled or an API function fails due to billing issues at any point. This step will set the track as snapped to roads upon success, and
    will fail if we are configured to not use google maps or no google maps API key is given. This step will automatically set the track as snapped if we are
    configured to not require snapping to roads.
    
    Arguments
    ---------
    :track: The Track to snap to road."""
    try:
        if track.is_snapped_to_roads:
            LOG.warning(f"Attempted to pass a Track that is already verified to snap_to_road. This will not continue.")
            """TODO: track is verified, no need to continue this."""
            raise NotImplementedError()
        elif not config.REQUIRE_SNAP_TO_ROADS:
            LOG.debug(f"We are configured to not require snapping to roads for new race tracks. The track {track} will therefore be immediately set as snapped to roads.")
            raise NotImplementedError("SET IS SNAPPED TO ROADS TO TRUE SOMEWHERE")
        elif not config.USE_GOOGLE_MAPS_API:
            LOG.warning(f"Attempt to snap a Track to road will not continue; server is configured against using Google Maps API. We will therefore verify this track as it is.")
            """TODO: configured to not use google maps api."""
            raise NotImplementedError()
        elif not config.GOOGLE_MAPS_API_KEY:
            LOG.error(f"Failed to snap track {track} to road! We are configured to use Google Maps API, but there's no key set.")
            """TODO: configured to use google maps api, but no key configured. fail."""
            raise NotImplementedError()
        # Check for an existing snap to road order for this track, create one if it does not already exist.
        order = get_order(track.id)
        if not order:
            LOG.debug(f"No snap-to-roads order exists for given track {track}, creating one now...")
            order = new_order(track)
        else:
            LOG.debug(f"Continuing snap-to-roads for track {track}. We have snapped {order.percent_snapped}% to roads.")
        # Iterate to the number of batches to snap remaining for this order.
        for batch_idx, unsnapped_track_points in iter_unsnapped_batches(order):
            try:
                # For each batch of unsnapped points, send a snap to roads request. On success, this should return a snap to roads response.
                # At this point, we will map the unsnapped track points to a list of geodetic Shapely points.
                unsnapped_points = [track_point.geodetic_point for track_point in unsnapped_track_points]
                snap_to_roads_response = send_snap_to_roads_request(batch_idx, unsnapped_points)
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
                # We will commit after every successful processing of a batch.
                db.session.commit()
            except SnapToRoadsApiError as strae:
                """TODO: handle snap to roads API error case-by-case here."""
                raise NotImplementedError()
        """TODO: report results on the snapping."""
    except Exception as e:
        raise e