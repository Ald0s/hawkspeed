import logging
from datetime import date, datetime

from flask import request, send_from_directory, make_response
from flask_login import current_user

from .. import db, config, decorators, viewmodel
from . import frontend

LOG = logging.getLogger("hawkspeed.frontend.routes")
LOG.setLevel( logging.DEBUG )


@frontend.route(f"/{config.PUBLIC_RESOURCE_PATH}/<media_uid>", methods = [ "GET" ])
@decorators.get_media()
def getmedia(media, **kwargs):
    """Perform an attempt for the specified Media object. If we are in test or development environment, this function will utilise send from directory for images. Otherwise,
    Nginx's X-Accel-Redirect will be used. Currently, video streams are not supported. This route does not check for whether this Media item is public/internal, as this is
    done in the decorator responsible for finding the Media item."""
    try:
        # Now make a new Media view model for the desired resource.
        media_view_model = viewmodel.MediaViewModel(current_user, media)
        # Ensure the User is able to view the Media item.
        if not media_view_model.can_view:
            """TODO: implement this error."""
            raise NotImplementedError(f"{current_user} is not allowed to view Media item {media} but this is NOT implemented!")
        # Discern between Video and Image and handle appropriately.
        if media_view_model.is_video:
            """TODO: support videos."""
            raise NotImplementedError(f"Failed to query media {media}, it is a video and these are not currently supported.")
        elif media_view_model.is_image:
            # Get the mimetype.
            mimetype, content_encoding = media_view_model.mimetype
            if config.APP_ENV == "Production" or config.APP_ENV == "LiveDevelopment":
                # Now, if we are in a production level environment, serve this content with an X-Accel-Redirect header.
                accel_redirect_response = make_response()
                accel_redirect_response.headers["X-Accel-Redirect"] = media.fully_qualified_path
                accel_redirect_response.headers["Content-Type"] = mimetype
                if content_encoding:
                    # If content encoding returned, set that too.
                    accel_redirect_response.headers["Content-Encoding"] = content_encoding
                # Return this response.
                return accel_redirect_response
            else:
                # Otherwise, we'll use send from directory.
                return send_from_directory(media.directory, media.filename,
                    mimetype = mimetype)
        else:
            # This should never really happen.
            raise NotImplementedError(f"Failed to query media {media}, unrecognised Media type.")
    except Exception as e:
        raise e