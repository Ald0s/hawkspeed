"""Media management. This module is responsible for handling all media on the server; incoming and outgoing."""
import re
import os
import shutil
import uuid
import time
import logging
import imghdr

from flask import request, url_for, send_from_directory
from flask_login import current_user
from werkzeug.utils import secure_filename
from functools import wraps
from datetime import datetime
from marshmallow import Schema, fields, EXCLUDE, post_load, ValidationError, validates

from . import db, config, models, error

LOG = logging.getLogger("hawkspeed.media")
LOG.setLevel( logging.DEBUG )


class MediaField(fields.Field):
    """A field that refers to an existing Media item on the server. When serialising, value is expected to be an instance of type Media, which will be serialised to a string that refers to the public
    resource URL for the given Media item. Temporary media items can't be serialised via media field, and attempting to do so will raise an exception. When deserialising, value is expected to be a
    string which refers to an existing Media item's UID."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def _serialize(self, value, attr, obj, **kwargs):
        """Serialise a specific Media item. This is essentially when an object has a profile image or other list of images; this is used to serialise that to a resource URL that can later be queried
        and drawn to a view. Value must be of type Media, or None if Nones are allowed."""
        if not value:
            # Return None.
            return None
        elif not isinstance(value, models.Media):
            LOG.error(f"Failed to serialise {value} as a MediaField, only Media model instances are allowed.")
            raise TypeError
        # Serialise to a string, which is the Media item's public resource.
        return _absolute_public_resource(value)

    def _deserialize(self, value, attr, data, **kwargs):
        """Deserialise is when a caller wishes to employ this Media item explicitly in a post of theirs, or as a profile/cover image etc. The specific permissions are not handled at all here, and should
        always be handled in the view model type that receives the deserialised media item. This function will deserialise a string to a Media item. If the Media item is temporary, it will be function will
        expect current User is set (to the same User that created Media item) or current environment is Test/Development."""
        if value is None:
            # Simply return none, nothing given.
            return None
        # Otherwise, value is a UID for a media item. Locate the corresponding Media item.
        media = find_media(
            media_uid = value)
        if not media:
            # If no Media item, raise a validation error regarding this issue.
            LOG.error(f"MediaField deserialise failed to find Media item with UID '{value}'")
            raise ValidationError("media-doesnt-exist")
        # Now, fail if current User is not authenticated OR current User is not equal to User that created Media item UNLESS we are in Test/Development mode.
        if (not current_user.is_authenticated or current_user != media.user) and (config.APP_ENV != "Test" and config.APP_ENV != "Development"):
            # Raise a ValidationError, this is not your media item.
            raise ValidationError("not-your-media")
        # Finally, ensure that if this media item is currently temporary, it is made permanent now.
        if media.is_temporary:
            LOG.debug(f"Successfully located existing media item; {media}. It is temporary and will be claimed by {media.user}")
            # Create a new public resource belonging to the media owner User. Instruct the system to delete the temporary file, currently in the media variable above, and set the new
            # media item's original filename to the temporary media item's original filename. Finally, the filename for the new resource should be given as the temp's filename, which
            # should be a UUID generated upon creation of the temp.
            new_media = create_public_resource(media,
                user = media.user)
            # Flush transaction.
            db.session.flush()
            # Return the new media item.
            return new_media
        # Finally, return the media item.
        return media


class InternalMediaField(fields.Field):
    """Deserialise an internal Media descriptor, which is essentially an object that points out a specific existing Media resource on disk. This will deserialise to a public, internal Media item. If the
    indicated Media item does not yet exist, it will be created. Otherwise, its existing instance will be returned. Serialising this media field will expect a Media item be given, that is internal. Its
    public resource will be returned as a string."""
    class InternalMediaSchema(Schema):
        class Meta:
            unknown = EXCLUDE
        filename            = fields.Str(required = True, allow_none = False)
        relative_directory  = fields.Str(required = False, allow_none = True, load_default = "")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def _serialize(self, value, attr, obj, **kwargs):
        """Serialise this media field to a string; public resource."""
        if not value:
            return None
        # If not a Media item, or a Media item that is not internal, raise a type error.
        if not isinstance(value, models.Media):
            LOG.error(f"Failed to serialise {value} as an internal Media item - this is not even a Media item!")
            raise TypeError
        elif not value.is_internal:
            LOG.error(f"Failed to serialise {value} as an internal Media item - this Media item is NOT internal!")
        # Otherwise, return public resource.
        return _absolute_public_resource(value)

    def _deserialize(self, value, attr, data, **kwargs):
        """Deserialise a Media descriptor field to a Media item. Value is expected to be a dictionary, which will successfully deserialise to InternalMediaSchema. The deserialised schema (as a dict) will
        then be utilised to locate and uplift the referenced Media item."""
        # Raise type error if value is not a dictionary.
        if not isinstance(value, dict):
            LOG.error(f"Failed to deserialise an internal media item from {value}, this is not a dictionary.")
            raise TypeError
        # Make a new internal media schema, and load value.
        internal_media_schema = self.InternalMediaSchema()
        internal_media_d = internal_media_schema.load(value)
        # From this dictionary, read both filename and relative source directory; both of these are mandatory so expect them both.
        src_filename = internal_media_d.get("filename")
        src_relative_directory = internal_media_d.get("relative_directory")
        # Now, attempt to find an existing internal Media item with the given filename, lowercased.
        existing_internal_media = find_media(
            original_filename = src_filename.lower(), is_internal = True)
        # If media item is found, return this one.
        if existing_internal_media:
            return existing_internal_media
        # Otherwise, we'll create a new internal resource.
        return create_internal_resource(src_filename, src_relative_directory)


def _absolute_public_resource(media, **kwargs):
    """"""
    try:
        if not media.is_public:
            """TODO: raise a proper exception."""
            raise NotImplementedError(f"Resource {media} requested is NOT a publicly available resource. Also this is not handled.")
        return url_for("frontend.getmedia",
            media_uid = media.uid, _external = True)
    except Exception as e:
        raise e
    

class UploadMediaResult():
    """A container for an uploaded media result, which essentially refers to a temporary media item."""
    class UploadMediaResponseSchema(Schema):
        """A schema for dumping an uploaded media result."""
        class Meta:
            unknown = EXCLUDE
        uid                     = fields.Str(allow_none = False)
        original_filename       = fields.Str(allow_none = False)
        created                 = fields.Int(allow_none = False)
        identity                = fields.Str(allow_none = False)

    @property
    def uid(self):
        """Return the Media items' UID."""
        return self._uid

    @property
    def original_filename(self):
        """Return the original filename, in lowercase, with extension."""
        return self._original_filename

    @property
    def created(self):
        """Return a timestamp, in seconds, when the Media item was created."""
        return self._created

    @property
    def identity(self):
        """Return the optional identity provided by request."""
        return self._identity

    def __init__(self, _temporary_media, _identity, **kwargs):
        self._uid = _temporary_media.uid
        self._original_filename = _temporary_media.original_filename
        self._created = _temporary_media.created
        self._identity = _identity

    def serialise(self, **kwargs):
        schema = self.UploadMediaResponseSchema(**kwargs)
        return schema.dump(self)


def find_media(**kwargs) -> models.Media:
    """Attempt to locate and return a specific Media item identified by values provided in keyword arguments.
    
    Keyword arguments
    -----------------
    :media_uid: The UID for a specific Media item to locate.
    :is_internal: True if results should be filtered to only internal Media, False if results should be filtered to everything but internal. Default is None.
    :original_filename: Provide the original filename to filter against this column.
    
    Returns
    -------
    The Media item, if found."""
    try:
        media_uid = kwargs.get("media_uid", None)
        is_internal = kwargs.get("is_internal", None)
        original_filename = kwargs.get("original_filename", None)

        if not media_uid and not original_filename:
            raise Exception("No valid arguments passed to find_media!")
        # Build a Media query.
        media_q = db.session.query(models.Media)
        # If media UID given, attach that filter.
        if media_uid:
            media_q = media_q\
                .filter(models.Media.uid == media_uid)
        # If original filename given, attach that filter.
        if original_filename:
            media_q = media_q\
                .filter(models.Media.original_filename == original_filename)
        # If is internal is given (its not None) attach that filter.
        if is_internal != None:
            media_q = media_q\
                .filter(models.Media.internal == is_internal)
        # Return first result.
        return media_q.first()
    except Exception as e:
        raise e
    

def create_uploaded_media(user, form, uploaded_file, **kwargs) -> UploadMediaResult:
    """Receive, validate and save an uploaded media file from a User to the disk. This will be saved as a temporary media item to start with. On success, this function
    will return an instance of UploadMediaResult. On failure, an error will be raised.

    Arguments
    ---------
    :user: The User to whom the temporary media file should be attributed, and from whom claiming should be expected.
    :form: A form multi-dict.
    :uploaded_file: An instance of FileStorage referencing the incoming file.
    
    Returns
    -------
    An instance of UploadMediaResult on success."""
    try:
        # Optionally, get the requested identity for this resource. By default, we'll just use the file's name.
        identity = form.get("identity", secure_filename(uploaded_file.filename))
        # Create a temporary media resource from this.
        temporary_media = _receive_uploaded_file(uploaded_file,
            user = user)
        # Flush for UID to be created on temporary media.
        db.session.flush()
        # Now, on success, instantiate and return the result.
        return UploadMediaResult(temporary_media, identity)
    except Exception as e:
        raise e


def _receive_uploaded_file(uploaded_file, **kwargs) -> models.Media:
    """Given a request form and instance of FileStorage, referencing an incoming file, save the result to the temporary media directory, under a temporary Media object, and return that object.
    All Media items must belong to a User, so ensure either current User is authenticated, or a User is provided via keyword arguments; even when testing.

    Arguments
    ---------
    :uploaded_file: An instance of FileStorage referencing the incoming file.

    Keyword arguments
    -----------------
    :user: The user to whom to assign ownership of the temporary media file. By default current_user will be used.

    Returns
    -------
    A Media object."""
    try:
        user = kwargs.get("user", current_user)

        # Ensure user is authenticated OR an actual user.
        if not user.is_authenticated:
            LOG.error(f"Failed to receive file; user is not authenticated!")
            raise ValueError
        # Get the file's name and secure it.
        original_filename = secure_filename(uploaded_file.filename)
        # Ensure filename is not empty or None
        if not original_filename:
            LOG.error(f"Failed to receive file; given file's name is not valid.")
            raise ValueError
        # Otherwise, get the file's extension, stripped of the dot.
        original_file_extension = os.path.splitext(original_filename)[1].strip(".").lower()
        # Ensure file type is acceptable.
        if not original_file_extension in config.ACCEPTABLE_MEDIA_TYPES:
            LOG.error(f"Failed to receive file of type {original_file_extension}, it is not acceptable!")
            raise ValueError
        # Generate a UID and create a new filename from that and the original extension.
        new_media_uid = uuid.uuid4()
        new_filename = f"{new_media_uid.hex.lower()}.{original_file_extension}"
        # Get our temporary media path for the resulting file.
        destination_absolute_directory = config.INSTANCE_TEMPORARY_MEDIA_PATH
        # Assemble an absolute destination path with file, and verify no file exists there already.
        absolute_destination_file = os.path.join(destination_absolute_directory, new_filename)
        if os.path.isfile(absolute_destination_file):
            """TODO: handle this exception properly."""
            raise NotImplementedError(f"Failed to create new temporary media from original filename {original_filename} toward new filename '{new_filename}', a file with this name already exists in temporary directory.")
        # Save the uploaded file to the temporary path above.
        uploaded_file.save(absolute_destination_file)
        # Now, create our actual Media item.
        new_media = models.Media(
            uid = new_media_uid)
        # Set all information for this new Media item.
        new_media.set_relative_directory("")
        new_media.set_filename(new_filename)
        new_media.set_original_filename(original_filename)
        new_media.set_file_type(original_file_extension)
        new_media.set_file_size(os.path.getsize(absolute_destination_file))
        new_media.set_is_public(False)
        new_media.set_is_internal(False)
        new_media.set_is_duplicate(False)
        new_media.set_is_temporary(True)
        new_media.set_user(user)
        # Add the media item to the session and return it.
        LOG.debug(f"Temporary Media item {new_media} successfully created!")
        db.session.add(new_media)
        return new_media
    except Exception as e:
        raise e
    

def delete_media(media, **kwargs):
    """"""
    try:
        raise NotImplementedError()
    except Exception as e:
        raise e


def create_public_resource(media, **kwargs) -> models.Media:
    """Create and return a Media item given the source Media item. If the Media item is temporary, it will be copied to external media base path and set to public in status. If
    the Media item is currently public and not temporary, it will instead be duplicated.
    
    Arguments
    ---------
    :media: An instance of Media from which to produce another Media item.
    
    Keyword arguments
    -----------------
    :user: The User to ascribe to the new Media item. By default, the User associated with the existing Media item will be used. Either way, a User is required.
    :dest_absolute_directory: The directory to copy the resulting internal file to.
    
    Returns
    -------
    An instance of Media."""
    try:
        user = kwargs.get("user", None)
        dest_absolute_directory = kwargs.get("dest_absolute_directory", config.EXTERNAL_MEDIA_BASE_PATH)

        # Now, is both given User and User on Media None? Fail.
        if not user and not media.user:
            LOG.error(f"Failed to create a new Media item from {media}, there is no User provided.")
            raise ValueError
        # Now check to see if given Media is temporary.
        if media.is_temporary:
            # Save the current absolute path to temporary Media item.
            absolute_temporary_media_path = media.fully_qualified_path
            # Assemble a new absolute destination now, from the given destination and filename. Check its existence.
            dest_absolute_path = os.path.join(dest_absolute_directory, media.filename)
            # Copy absolute source path to absolute destination path.
            shutil.copyfile(absolute_temporary_media_path, dest_absolute_path)
            # Now, check that the destination file exists.
            if not os.path.isfile(dest_absolute_path):
                raise NotImplementedError(f"Failed to create new Media item from temporary Media, after copying, the new file does not exist.")
            # Update the Media item given to point to this new Media.
            media.set_is_public(True)
            media.set_is_internal(False)
            media.set_is_duplicate(False)
            media.set_is_temporary(False)
        else:
            raise NotImplementedError("Duplicating Media not yet supported.")
        return media
    except Exception as e:
        raise e


def create_internal_resource(src_filename, src_relative_directory, **kwargs) -> models.Media:
    """Create and return a Media item given the source information provided. Checking for existing Media items must be done prior to calling this function as this function will
    overwrite any existing files. A new filename will always be generated for the incoming Media item.

    Arguments
    ---------
    :src_filename: Filename of the source file.
    :src_relative_directory: A directory, relative to working, where the source file can be found.

    Keyword arguments
    -----------------
    :dest_absolute_directory: The directory to copy the resulting internal file to.
    
    Returns
    -------
    A Media. The created internal resource."""
    dest_absolute_directory = kwargs.get("dest_absolute_directory", config.EXTERNAL_MEDIA_BASE_PATH)

    # Ensure original filename is in lower case, then use it to get its extension.
    original_filename = src_filename.lower()
    original_file_extension = os.path.splitext(original_filename)[1].strip(".").lower()
    # Ensure file type is acceptable.
    if not original_file_extension in config.ACCEPTABLE_MEDIA_TYPES:
        LOG.error(f"Failed to create internal Media item with file of type {original_file_extension}, it is not acceptable!")
        raise ValueError
    # Ensure the source file exists.
    src_absolute_path = os.path.join(os.getcwd(), src_relative_directory, src_filename)
    if not os.path.isfile(src_absolute_path):
        LOG.error(f"Failed to create internal Media item, no source file: {src_absolute_path}")
        raise OSError(1, "no-source-item")
    # Generate a new UID to represent this resource, always. Filename is always the given destination filename.
    new_media_uid = uuid.uuid4()
    new_filename = f"{new_media_uid.hex.lower()}.{original_file_extension}"
    # Assemble a new absolute destination now, from the given destination and filename. Check its existence.
    dest_absolute_path = os.path.join(dest_absolute_directory, new_filename)
    # Copy absolute source path to absolute destination path.
    shutil.copyfile(src_absolute_path, dest_absolute_path)
    # Now, create our actual Media item.
    new_media = models.Media(
        uid = new_media_uid)
    # Set all information for this new Media item.
    new_media.set_relative_directory("")
    new_media.set_filename(new_filename)
    new_media.set_original_filename(original_filename)
    new_media.set_file_type(original_file_extension)
    new_media.set_file_size(os.path.getsize(dest_absolute_path))
    new_media.set_is_public(True)
    new_media.set_is_internal(True)
    new_media.set_is_duplicate(False)
    new_media.set_is_temporary(False)
    LOG.debug(f"Successfully created new public (internal) Media ({new_media}) from source '{src_absolute_path}' to destination '{dest_absolute_path}'!")
    db.session.add(new_media)
    return new_media