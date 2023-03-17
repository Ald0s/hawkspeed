"""A module for drawing various geometries to their GeoJSON equivalents."""
import geopandas
import geojson
import random
import pyproj
import shapely

from . import config


def race_progress_to_geojson(track_user_race, buffer_progress = True):
    # Get the track path.
    track_path = track_user_race.track.path
    # Get the track's multilinestring geometry.
    geodetic_multi_linestring = track_path.geodetic_multi_linestring
    # Now, draw the track path as a black multi line string.
    path_multi_linestring = geojson.MultiLineString([list(line_string.coords) for line_string in geodetic_multi_linestring.geoms])
    path_feature = geojson.Feature(
        properties = { "stroke": "#000000" },
        geometry = path_multi_linestring
    )
    if buffer_progress:
        # Draw the progress geometry as red slightly translucent polygon buffered by configured value.
        # But we need to buffer the transformed coordinates, then convert it back to a geodetic EPSG.
        progress_buffered_polygon = track_user_race.linestring\
            .buffer(config.NUM_METERS_BUFFER_PLAYER_PROGRESS, cap_style = shapely.geometry.CAP_STYLE.square)
        # Now, transform this to a geodetic equivalent. Begin by getting a geodetic transformer for this race.
        geodetic_transformer = track_user_race.geodetic_transformer
        # Now, use it to transform the progress buffered polygon.
        progress_buffered_polygon = shapely.ops.transform(geodetic_transformer.transform, progress_buffered_polygon)
        # We can now build the GeoJSON feature.
        progress_polygon = geojson.Polygon([list(progress_buffered_polygon.exterior.coords)])
        progress_feature = geojson.Feature(
            properties = {
                "stroke": "#ff0000",
                "stroke-width": 2,
                "stroke-opacity": 1,
                "fill": "#ff0000",
                "fill-opacity": 0.5
            },
            geometry = progress_polygon
        )
    else:
        # Get the progress' linestring.
        geodetic_linestring = track_user_race.geodetic_linestring
        # Draw the progress geometry as a red line string.
        progress_linestring = geojson.LineString(list(geodetic_linestring.coords))
        progress_feature = geojson.Feature(
            properties = { "stroke": "#ff0000" },
            geometry = progress_linestring
        )
    # Draw this feature collection, the progress feature overlay the path feature.
    feature_collection = geojson.FeatureCollection([path_feature, progress_feature])
    return geojson.dumps(feature_collection, indent = 4)


def points_to_geojson(points, texts):
    # Now, we will construct a Features list.
    features = []
    # Iterate a zip for the points.
    for point, text in zip(points, texts):
        # Create a point feature.
        geojson_point = geojson.Point((point[0], point[1],))
        polygon_feature = geojson.Feature(
            properties = {
                "name": text
            },
            geometry = geojson_point)
        features.append(polygon_feature)
    # Now a feature collection.
    feature_collection = geojson.FeatureCollection(features)
    # And dump return.
    return geojson.dumps(feature_collection, indent = 4)


def lines_to_geojson(lines):
    # Now, we will construct a Features list.
    features = []
    # Iterate a zip for the polys.
    for line in lines:
        # Create a line feature.
        geojson_line = geojson.LineString(list(line.coords))
        polygon_feature = geojson.Feature(
            properties = {
                "stroke": "#%06x" % random.randint(0, 0xFFFFFF)
            },
            geometry = geojson_line)
        features.append(polygon_feature)
    # Now a feature collection.
    feature_collection = geojson.FeatureCollection(features)
    # And dump return.
    return geojson.dumps(feature_collection, indent = 4)


def polygons_to_geojson_1(polygons, names, crs):
    zones_polygons = geopandas.GeoSeries(polygons, crs = crs)
    if crs != 4326:
        transformed_zones_polygons = zones_polygons.to_crs(4326)
    else:
        transformed_zones_polygons = zones_polygons
    # Now, we will construct a Features list.
    features = []
    # Iterate a zip for the polys.
    for polygon, name in zip(transformed_zones_polygons.geometry, names):
        # Create a polygon feature.
        geojson_polygon = geojson.Polygon([list(polygon.exterior.coords)])
        polygon_feature = geojson.Feature(
            properties = {
                "name": name
            },
            geometry = geojson_polygon
        )
        features.append(polygon_feature)
    # Now a feature collection.
    feature_collection = geojson.FeatureCollection(features)
    # And dump return.
    return geojson.dumps(feature_collection, indent = 4)


def polygons_to_geojson(polygons, crs, **kwargs):
    names = kwargs.get("names", None)

    zones_polygons = geopandas.GeoSeries(polygons, crs = crs)
    if crs != 4326:
        transformed_zones_polygons = zones_polygons.to_crs(4326)
    else:
        transformed_zones_polygons = zones_polygons
    # Now, we will construct a Features list.
    features = []
    # Iterate a zip for the polys.
    for polygon in transformed_zones_polygons.geometry:
        # Create a polygon feature.
        geojson_polygon = geojson.Polygon([list(polygon.exterior.coords)])
        polygon_feature = geojson.Feature(
            properties = {
                "stroke": "#ff0000",
                "stroke-width": 2,
                "stroke-opacity": 1,
                "fill": "#ff0000",
                "fill-opacity": 0.5
            },
            geometry = geojson_polygon)
        features.append(polygon_feature)
    # Now a feature collection.
    feature_collection = geojson.FeatureCollection(features)
    # And dump return.
    return geojson.dumps(feature_collection, indent = 4)


def multi_polygons_to_geojson(multi_polygons, crs, **kwargs):
    names = kwargs.get("names", None)

    # Now, we will construct a Features list.
    features = []
    # Iterate a zip for the polys.
    for multi_polygon in multi_polygons:
        for polygon in multi_polygon.geoms:
            # Create a polygon feature.
            geojson_polygon = geojson.Polygon([list(polygon.exterior.coords)])
            polygon_feature = geojson.Feature(
                properties = {
                    "stroke": "#ff0000",
                    "stroke-width": 2,
                    "stroke-opacity": 1,
                    "fill": "#ff0000",
                    "fill-opacity": 0.5
                },
                geometry = geojson_polygon)
            features.append(polygon_feature)
    # Now a feature collection.
    feature_collection = geojson.FeatureCollection(features)
    # And dump return.
    return geojson.dumps(feature_collection, indent = 4)
