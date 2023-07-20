"""A module for handling functionality relating to Vehicles."""
import logging
import os
import random
import hashlib

from datetime import datetime, date
from sqlalchemy import func, asc, desc, delete
from sqlalchemy.orm import with_expression
from marshmallow import fields, Schema, post_load, EXCLUDE

from .compat import insert
from . import db, config, models, error

LOG = logging.getLogger("hawkspeed.vehicles")
LOG.setLevel( logging.DEBUG )


def find_vehicle_for_user(user, vehicle_uid, **kwargs) -> models.UserVehicle:
    """Locate and return a Vehicle belonging to the given User, identified by the given Vehicle UID.
    
    Arguments
    ---------
    :user: The User to which the vehicle should belong.
    :vehicle_uid: The UID under which the Vehicle has been given.
    
    Returns
    -------
    The located vehicle."""
    try:
        # Setup a query for user vehicle, filter by User's ID and by Vehicle UID.
        user_vehicle_q = db.session.query(models.UserVehicle)\
            .filter(models.UserVehicle.uid == vehicle_uid)\
            .filter(models.UserVehicle.user_id == user.id)
        # Now, return the first result.
        return user_vehicle_q.first()
    except Exception as e:
        raise e
    

def create_vehicle(request_create_vehicle, **kwargs) -> models.UserVehicle:
    """Create and return a new User Vehicle. This function will not add the Vehicle to the session, unless of course, a User is provided. In which case
    Vehicle will be added to session as a biproduct of being added to the User.
    
    Arguments
    ---------
    :request_create_vehicle: An instance of RequestCreateVehicle from which the new Vehicle should be created.

    Keyword arguments
    -----------------
    :user: Optionally provide a User to immediately add that vehicle to.

    Returns
    -------
    The UserVehicle instance."""
    try:
        user = kwargs.get("user", None)
        
        # Create a new Vehicle model.
        vehicle = models.UserVehicle()
        # Set its text.
        vehicle.set_text(request_create_vehicle.text)
        # If User has been given, add the vehicle to that User.
        if user:
            user.add_vehicle(vehicle)
        # Return vehicle.
        return vehicle
    except Exception as e:
        raise e


class LoadedVehicleStock():
    """A container for a loaded vehicle stock model."""
    def __init__(self, **kwargs):
        self.vehicle_uid = kwargs.get("vehicle_uid")
        self.version = kwargs.get("version", None)
        self.badge = kwargs.get("badge", None)
        self.motor_type = kwargs.get("motor_type")
        self.displacement = kwargs.get("displacement")
        self.induction = kwargs.get("induction", None)
        self.fuel_type = kwargs.get("fuel_type", None)
        self.power = kwargs.get("power", None)
        self.elec_type = kwargs.get("elec_type", None)
        self.trans_type = kwargs.get("trans_type", None)
        self.num_gears = kwargs.get("num_gears", None)


class VehicleStockSchema(Schema):
    """A schema for loading or dumping a vehicle stock."""
    class Meta:
        unknown = EXCLUDE
    vehicle_uid             = fields.Str(allow_none = False, required = True)

    version                 = fields.Str(required = False, load_default = None)
    badge                   = fields.Str(required = False, load_default = None)

    motor_type              = fields.Str(allow_none = False, required = True)
    displacement            = fields.Int(required = False, load_default = None)
    induction               = fields.Str(required = False, load_default = None)
    fuel_type               = fields.Str(required = False, load_default = None)

    power                   = fields.Int(required = False, load_default = None)
    elec_type               = fields.Str(required = False, load_default = None)

    trans_type              = fields.Str(required = False, load_default = None)
    num_gears               = fields.Int(required = False, load_default = None)

    # As part of dump-only, we'll provide the make and model objects as well as the year.
    make                    = fields.Nested(lambda: MakeSchema(), many = False, dump_only = True)
    model                   = fields.Nested(lambda: ModelSchema(), many = False, dump_only = True)
    year_model_year_        = fields.Int(dump_only = True, data_key = "year")

    @post_load
    def vehicle_stock_post_load(self, data, **kwargs) -> LoadedVehicleStock:
        return LoadedVehicleStock(**data)


class LoadedYearModel():
    """A container for a loaded vehicle year model."""
    def __init__(self, **kwargs):
        self.make_uid = kwargs.get("make_uid")
        self.model_uid = kwargs.get("model_uid")
        self.year = kwargs.get("year")
        self.vehicles = kwargs.get("vehicles")


class YearModelSchema(Schema):
    """A schema for loading or dumping a vehicle year model."""
    class Meta:
        unknown = EXCLUDE
    make_uid                = fields.Str(allow_none = False, required = True)
    model_uid               = fields.Str(allow_none = False, required = True)
    year                    = fields.Int(allow_none = False, required = True)
    vehicles                = fields.List(fields.Nested(VehicleStockSchema, many = False), load_only = True)

    @post_load
    def year_model_post_load(self, data, **kwargs) -> LoadedYearModel:
        return LoadedYearModel(**data)


class LoadedModel():
    """A container for a loaded vehicle model."""
    def __init__(self, **kwargs):
        self.uid = kwargs.get("uid")
        self.make_uid = kwargs.get("make_uid")
        self.type_id = kwargs.get("type_id")
        self.name = kwargs.get("name")
        self.year_models = kwargs.get("year_models")


class ModelSchema(Schema):
    """A schema for loading or dumping."""
    class Meta:
        unknown = EXCLUDE
    uid                     = fields.Str(allow_none = False, required = True, data_key = "model_uid")
    make_uid                = fields.Str(allow_none = False, required = True)
    type_id                 = fields.Str(allow_none = False, required = True)
    name                    = fields.Str(allow_none = False, required = True, data_key = "model_name")
    year_models             = fields.Dict(keys = fields.Int(), values = fields.Nested(YearModelSchema, many = False), load_only = True)

    # Only when we dump model, we'll also include the model's type.
    type                    = fields.Nested(lambda: TypeSchema(), many = False, dump_only = True)

    @post_load
    def model_post_load(self, data, **kwargs) -> LoadedModel:
        return LoadedModel(**data)


class LoadedMake():
    """A container for a loaded vehicle make."""
    def __init__(self, **kwargs):
        self.uid = kwargs.get("uid")
        self.name = kwargs.get("name")
        self.models = kwargs.get("models")


class MakeSchema(Schema):
    """A schema for loading or dumping a vehicle make."""
    class Meta:
        unknown = EXCLUDE
    uid                     = fields.Str(allow_none = False, required = True, data_key = "make_uid")
    name                    = fields.Str(allow_none = False, required = True, data_key = "make_name")
    models                  = fields.Dict(keys = fields.Str(), values = fields.Nested(ModelSchema, many = False), load_only = True)

    @post_load
    def make_post_load(self, data, **kwargs) -> LoadedMake:
        return LoadedMake(**data)
    

class RequestCreateVehicle():
    """A container for a loaded request to create a new Vehicle."""
    def __init__(self, **kwargs):
        self.text = kwargs.get("text")


class RequestCreateVehicleSchema(Schema):
    """A schema for loading a request to create a new Vehicle."""
    class Meta:
        unknown = EXCLUDE
    text                    = fields.Str(required = True, allow_none = False)

    @post_load
    def request_create_vehicle_post_load(self, data, **kwargs) -> RequestCreateVehicle:
        return RequestCreateVehicle(**data)


class LoadedType():
    """A container for a loaded vehicle type."""
    def __init__(self, **kwargs):
        self.type_id = kwargs.get("type_id")
        self.name = kwargs.get("name")
        self.description = kwargs.get("description")


class TypeSchema(Schema):
    """A schema for loading or dumping a vehicle type."""
    class Meta:
        unknown = EXCLUDE
    type_id                 = fields.Str(allow_none = False, required = True)
    name                    = fields.Str(allow_none = False, required = True)
    description             = fields.Str(allow_none = False, required = True)

    @post_load
    def type_post_load(self, data, **kwargs) -> LoadedType:
        return LoadedType(**data)


class LoadedYear():
    """A container for a loaded vehicle year."""
    def __init__(self, **kwargs):
        self.year = kwargs.get("year")


class YearSchema(Schema):
    """A schema for loading or dumping a vehicle year."""
    class Meta:
        unknown = EXCLUDE
    year                    = fields.Int()

    @post_load
    def year_post_load(self, data, **kwargs) -> LoadedYear:
        return LoadedYear(**data)
    

class Master():
    def __init__(self, version, version_code, environment, types, years, **kwargs):
        self.version = version
        self.version_code = version_code
        self.environment = environment
        self.types = types
        self.years = years


class MasterSchema(Schema):
    """A schema for loading the master record from a vehicle data schema."""
    class Meta:
        unknown = EXCLUDE
    version                 = fields.Str()
    version_code            = fields.Int()
    environment             = fields.Str()
    types                   = fields.List(fields.Nested(TypeSchema, many = False))
    years                   = fields.List(fields.Int())

    @post_load
    def master_post_load(self, data, **kwargs) -> Master:
        return Master(**data)


class VehicleData():
    """A container for a read vehicles JSON file."""
    def __init__(self, master, makes, **kwargs):
        self.master = master
        self.makes = makes


class VehicleDataSchema(Schema):
    """A schema for loading a vehicle data JSON file."""
    class Meta:
        unknown = EXCLUDE
    master                  = fields.Nested(MasterSchema, many = False)
    makes                   = fields.Dict(keys = fields.Str(), values = fields.Nested(MakeSchema, many = False))

    @post_load
    def vehicle_data_post_load(self, data, **kwargs) -> VehicleData:
        return VehicleData(**data)
    

class UpdateVehicleDataResult():
    """A container for the results of updating the vehicle data."""
    def __init__(self, **kwargs):
        pass


def update_vehicle_data(vehicle_data, **kwargs) -> UpdateVehicleDataResult:
    """Update vehicle data stored in the database given the loaded vehicle data. This function, on success, will return the update vehicle data result.
    This function will access the server configuration entity to check the current version of vehicle data, and will skip this procedure if we are up
    to date or attempting to load an old vehicle data.
    
    Arguments
    ---------
    :vehicle_data: An instance of VehicleData to upsert into database.

    Keyword arguments
    -----------------
    :server_configuration: The server configuration instance to use. By default, will be queried from database.
    
    Returns
    -------
    An instance of UpdateVehicleDataResult, containing the outcome of this process."""
    try:
        # Get the server configuration.
        server_configuration = kwargs.get("server_configuration", models.ServerConfiguration.get())

        # First, we will take the master record from the vehicle data object.
        master = vehicle_data.master
        # Now, check the version code in the loaded vehicle data.
        if server_configuration.has_vehicle_data and master.version_code <= server_configuration.vehicle_version_code:
            LOG.debug(f"Skipping updating vehicle data; no need. Current stored version is {server_configuration.vehicle_version} and incoming version is {master.version}")
            return UpdateVehicleDataResult()
        # From master, we'll upsert all vehicle types by merging them with the database.
        for read_type in master.types:
            vehicle_type = models.VehicleType(
                type_id = read_type.type_id, name = read_type.name, description = read_type.description)
            # Now, merge. This will essentially upsert.
            db.session.merge(vehicle_type)
        # Now, from master, we'll upsert all vehicle years by merging them too.
        for read_year in master.years:
            vehicle_year = models.VehicleYear(
                year_ = read_year)
            # Now, merge. This will essentially upsert.
            db.session.merge(vehicle_year)
        # Now, iterate all makes in the vehicle data object.
        for make_name, read_make in vehicle_data.makes.items():
            # Merge each make, too.
            vehicle_make = models.VehicleMake(
                uid = read_make.uid, name = make_name)
            db.session.merge(vehicle_make)
            # Within each read make, iterate all models. Merge these, too.
            for model_name, read_model in read_make.models.items():
                vehicle_model = models.VehicleModel(
                    uid = read_model.uid, make_uid = read_model.make_uid, type_id = read_model.type_id, name = model_name)
                db.session.merge(vehicle_model)
                # Within each model, iterate all year models. Merge these, too.
                for year, read_year_model in read_model.year_models.items():
                    vehicle_year_model = models.VehicleYearModel(
                        make_uid = read_year_model.make_uid, model_uid = read_year_model.model_uid, year_ = year)
                    db.session.merge(vehicle_year_model)
                    # Within each year model, iterate all vehicles. Merge these, too.
                    for read_vehicle in read_year_model.vehicles:
                        vehicle_stock = models.VehicleStock(vehicle_uid = read_vehicle.vehicle_uid)
                        # Set the vehicle stock's year model.
                        vehicle_stock.set_year_model(read_year_model.make_uid, read_year_model.model_uid, read_year_model.year)
                        # Now, set all information from the read vehicle.
                        vehicle_stock.set_version(read_vehicle.version)
                        vehicle_stock.set_badge(read_vehicle.badge)
                        vehicle_stock.set_motor_type(read_vehicle.motor_type)
                        vehicle_stock.set_displacement(read_vehicle.displacement)
                        vehicle_stock.set_induction_type(read_vehicle.version)
                        vehicle_stock.set_fuel_type(read_vehicle.version)
                        vehicle_stock.set_power(read_vehicle.version)
                        vehicle_stock.set_electric_motor_type(read_vehicle.version)
                        vehicle_stock.set_transmission_type(read_vehicle.version)
                        vehicle_stock.set_num_gears(read_vehicle.version)
                        db.session.merge(vehicle_stock)
        # Now, after updating, update the server configuration instance.
        server_configuration.set_vehicle_data_version(master.version, master.version_code)
        return UpdateVehicleDataResult()
    except Exception as e:
        raise e
    

def load_vehicle_data_from(filename, **kwargs) -> UpdateVehicleDataResult:
    """Load the most recent data from the given filename, optionally with a relative directory. By default, the file will be searched for in the
    imports directory. This function will raise an OSError if the target file doesn't exist. This function will flush but will not commit.
    
    Keyword arguments
    -----------------
    :relative_directory: The directory in which the source file should be located in. Default is empty.
    
    Returns
    -------
    An instance of UpdateVehicleDataResult."""
    try:
        relative_directory = kwargs.get("relative_directory", "")

        # Setup an absolute target file path for the given file.
        absolute_target_file = os.path.join(os.getcwd(), config.IMPORTS_PATH, relative_directory, filename)
        # Now, ensure the file exists, fail if not.
        if not os.path.isfile(absolute_target_file):
            raise OSError
        # Now, read the contents of this file.
        with open(absolute_target_file, "r", encoding = "utf-8") as f:
            target_file_contents = f.read()
        # Now, instantiate a master schema through which we will load the file contents.
        schema = VehicleDataSchema()
        loaded_vehicle_data = schema.loads(target_file_contents)
        # Pass the loaded vehicle data to the update function.
        return update_vehicle_data(loaded_vehicle_data)
    except Exception as e:
        raise e


def _search_vehicles(**kwargs):
    """Called by high level code when a user wishes to search for vehicles; makes, types, models, years, vehicle stocks. Inputs are a cascading set
    of arguments, each depending on the existence of the last. These must be provided each time, in sequential order, as a search is assembled.
    Results are to be filtered according to keyword arguments, and ordered by the searching_user's interests toward the found vehicles.

    Should no arguments be valid, types will be returned instead.

    Keyword arguments
    -----------------
    :make_uid: The UID of a vehicle make. The return value will be VehicleTypes.
    :type_id: The type of vehicle to search for. The return value will be VehicleModels.
    :model_uid: The UID of a vehicle model within the vehicle make. The return value will be VehicleYears.
    :year: The year of a vehicle within the vehicle model. The return value will be VehicleStocks.

    Raises
    ------
    ValueError - cascade has been broken.

    Returns
    -------
    A query, filtered to match search criteria."""
    try:
        # Extract our search filtering criteria.
        make_uid = kwargs.get("make_uid", None)
        type_id = kwargs.get("type_id", None)
        model_uid = kwargs.get("model_uid", None)
        year = kwargs.get("year", None)
        # An empty variable for the target schema type for dumping.
        SerialiseCls = None

        # Now, construct and return our query.
        result_query = None
        if not make_uid and not type_id and not model_uid and not year:
            # We have been given no arguments at all. Return a query for all makes.
            LOG.debug(f"Querying all vehicle makes!")
            result_query = db.session.query(models.VehicleMake)
            SerialiseCls = MakeSchema
        elif not type_id and not model_uid and not year:
            # We have been given just a vehicle make. Return a query for types for that make.
            LOG.debug(f"Querying vehicle types within make {make_uid}!")
            result_query = db.session.query(models.VehicleType)\
                .join(models.VehicleModel, models.VehicleModel.type_id == models.VehicleType.type_id)\
                .filter(models.VehicleModel.make_uid == make_uid)
            SerialiseCls = TypeSchema
        elif not model_uid and not year:
            # We have been given a make UID and a type ID. Return a query for models for that make and type ID.
            LOG.debug(f"Querying vehicle models within make {make_uid} and type ID {type_id}!")
            result_query = db.session.query(models.VehicleModel)\
                .filter(models.VehicleModel.make_uid == make_uid)\
                .filter(models.VehicleModel.type_id == type_id)
            SerialiseCls = ModelSchema
        elif not year:
            # We have been given a make UID, type ID and a model UID. Return a query for years for that make, type ID and model.
            LOG.debug(f"Querying vehicle years within make {make_uid}, type ID {type_id} and model {model_uid}!")
            """TODO: join vehicle model here"""
            result_query = db.session.query(models.VehicleYear)\
                .join(models.VehicleYearModel, models.VehicleYearModel.year_ == models.VehicleYear.year_)\
                .filter(models.VehicleYearModel.make_uid == make_uid)\
                .filter(models.VehicleYearModel.model_uid == model_uid)
            SerialiseCls = YearSchema
        else:
            # We have been given ALL arguments. Return a query for vehicle stocks for that type ID, make, model and year.
            LOG.debug(f"Querying vehicle stocks within make {make_uid}, type ID {type_id}, model {model_uid} and year {year}!")
            result_query = db.session.query(models.VehicleStock)\
                .filter(models.VehicleStock.year_model_make_uid == make_uid)\
                .filter(models.VehicleStock.year_model_model_uid == model_uid)\
                .filter(models.VehicleStock.year_model_year_ == year)
            SerialiseCls = VehicleStockSchema
        return result_query, SerialiseCls
    except Exception as e:
        raise e


def search_vehicles(**kwargs):
    """View _search_vehicles for the full explanation of this function. This is an abstraction of that function that is focused only on returning a query, assembled to
    target the next desired vehicle artifact determined by the values given in keyword arguments."""
    # Result of calling search vehicles is the query, and the required schema for the target entity.
    search_vehicles_q, SerialiseCls = _search_vehicles(**kwargs)
    # We will just return the query.
    return search_vehicles_q


def search_vehicles_with_schema(**kwargs):
    """View _search_vehicles for the full explanation of this function. This is an abstraction of that function that will return not just the query, but also the schema
    determined to be appropriate for serialising the result objects."""
    # Result of calling search vehicles is the query, and the required schema for the target entity.
    search_vehicles_q, SerialiseCls = _search_vehicles(**kwargs)
    # We will return both.
    return search_vehicles_q, SerialiseCls