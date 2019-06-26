# For more details, see
# http://docs.sqlalchemy.org/en/latest/orm/tutorial.html#declare-a-mapping
from anthill.framework.db import db
from anthill.framework.conf import settings
from anthill.framework.utils import timezone
from anthill.framework.utils.functional import cached_property
from anthill.framework.utils.geoip import GeoIP2
from anthill.framework.utils.asynchronous import as_future, thread_pool_exec as future_exec
from anthill.framework.utils.translation import translate_lazy as _
from anthill.platform.models import BaseApplication, BaseApplicationVersion
from anthill.platform.api.internal import InternalAPIMixin, RequestError
from anthill.platform.auth import RemoteUser
from anthill.platform.services import HeartbeatReport
from sqlalchemy_utils.types import URLType, ChoiceType, JSONType, IPAddressType
from sqlalchemy.ext.hybrid import hybrid_property
from geoalchemy2.elements import WKTElement
from geoalchemy2 import Geometry
from functools import partial, wraps
from typing import Union
import geoalchemy2.functions as func
import traceback
import enum
import json


class PartyError(Exception):
    pass


class PartySessionPermissionError(PartyError):
    pass


class PlayersLimitPerPartyExceeded(PartyError):
    pass


class PlayersLimitPerRoomExceeded(Exception):
    pass


class UserBannedError(Exception):
    pass


class Application(BaseApplication):
    __tablename__ = 'applications'


class ApplicationVersion(BaseApplicationVersion):
    __tablename__ = 'application_versions'

    rooms = db.relationship('Room', backref='app_version', lazy='dynamic')
    deployments = db.relationship('Deployment', backref='app_version', lazy='dynamic')


class Room(InternalAPIMixin, db.Model):
    __tablename__ = 'rooms'

    id = db.Column(db.Integer, primary_key=True)
    server_id = db.Column(db.Integer, db.ForeignKey('servers.id'))
    app_version_id = db.Column(db.Integer, db.ForeignKey('application_versions.id'))
    players = db.relationship('Player', backref='room', lazy='dynamic')
    settings = db.Column(JSONType, nullable=False, default={})
    max_players_count = db.Column(db.Integer, nullable=False, default=0)

    async def check_moderations(self):
        # TODO: get moderations from moderation servce
        if True:
            raise UserBannedError

    async def join(self, player):
        players = await future_exec(self.players.all)

        if len(players) >= self.max_players_count:
            raise PlayersLimitPerRoomExceeded
        await self.check_moderations()

        player.room_id = self.id
        await future_exec(self.players.append, player)

        # TODO: make other players to know about new player
        player_data1 = {}
        for p in players:
            await RemoteUser.send_message_by_user_id(
                p.id, message=json.dumps(player_data1), content_type='application/json')

        # TODO: send some info to the new player
        player_data2 = {}
        await RemoteUser.send_message_by_user_id(
            player.user_id, message=json.dumps(player_data2), content_type='application/json')

    # noinspection PyMethodMayBeStatic
    async def leave(self, player):
        await future_exec(player.delete)

    async def remove(self):
        await future_exec(Player.query.filter_by(room_id=self.id).delete)
        await future_exec(self.delete)

    @classmethod
    async def create_room(cls, **kwargs):
        return await future_exec(cls.create, **kwargs)

    async def terminate(self):
        # TODO: kill room process on controller
        await self.remove()

    @classmethod
    async def find(cls, **kwargs):
        # TODO: setup room filter
        rooms = await future_exec(cls.query.filter_by(**kwargs))
        return rooms

    async def instantiate(self):
        # TODO: start room process on controller
        pass

    async def spawn(self):
        result = await self.instantiate()
        return result


class Player(InternalAPIMixin, db.Model):
    __tablename__ = 'players'

    class Statuses(enum.Enum):
        NEW = 1
        JOINED = 2

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)
    room_id = db.Column(db.Integer, db.ForeignKey('rooms.id'))
    status = db.Column(db.Enum(Statuses), default=Statuses.NEW)
    ip_address = db.Column(IPAddressType)
    payload = db.Column(JSONType, nullable=False, default={})

    async def get_user(self) -> RemoteUser:
        data = await self.internal_request('login', 'get_user', user_id=self.user_id)
        return RemoteUser(**data)

    @cached_property
    def gis(self):
        if getattr(settings, 'GEOIP_PATH', None):
            return GeoIP2()

    def get_location(self):
        """Return a tuple of the (latitude, longitude) for the given ip address."""
        if self.ip_address is not None:
            return self.gis.lat_lon(self.ip_address)

    async def get_region(self):
        location = self.get_location()
        if location:
            loc = await GeoLocation.get_nearest(*location)
        else:
            loc = await GeoLocation.get_default()
        return loc.region

    @classmethod
    async def get_server(cls, region):
        server = await Server.get_optimal(region.id)
        return server


class GeoLocationRegion(db.Model):
    __tablename__ = 'geo_location_regions'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True)
    locations = db.relationship('GeoLocation', backref='region', lazy='dynamic')


class GeoLocation(db.Model):
    __tablename__ = 'geo_locations'

    id = db.Column(db.Integer, primary_key=True)
    point = db.Column(Geometry(geometry_type='POINT', srid=4326))
    region_id = db.Column(db.Integer, db.ForeignKey('geo_location_regions.id'))
    servers = db.relationship('Server', backref='geo_location', lazy='dynamic')
    default = db.Column(db.Boolean, nullable=False, default=False)

    @classmethod
    async def get_nearest(cls, lat, lon):
        """
        Find the nearest point to the input coordinates.
        Convert the input coordinates to a WKT point and query for nearest point.
        """
        pt = WKTElement('POINT({0} {1})'.format(lon, lat), srid=4326)
        return cls.query.order_by(cls.point.distance_box(pt)).first()

    @classmethod
    async def get_default(cls):
        return cls.query.filter_by(default=True).order_by(cls.id).first()

    @staticmethod
    async def from_point_to_xy(pt):
        """Extract x and y coordinates from a point geometry."""
        # noinspection PyUnresolvedReferences
        point_json = json.loads(db.session.scalar(func.ST_AsGeoJSON(pt.point)))
        return point_json['coordinates']


class Server(InternalAPIMixin, db.Model):
    __tablename__ = 'servers'

    STATUSES = (
        ('active', _('Active')),
        ('failed', _('Failed')),
        ('overload', _('Overload')),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True)
    location = db.Column(URLType, nullable=False, unique=True)
    geo_location_id = db.Column(db.Integer, db.ForeignKey('geo_locations.id'))
    last_heartbeat = db.Column(db.DateTime)
    status = db.Column(ChoiceType(STATUSES))
    last_failure_tb = db.Column(db.Text)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    rooms = db.relationship('Room', backref='server', lazy='dynamic')
    cpu_load = db.Column(db.Float, nullable=False, default=0.0)
    ram_usage = db.Column(db.Float, nullable=False, default=0.0)

    @hybrid_property
    def active(self):
        return self.enabled and self.status == 'active'

    @classmethod
    async def get_optimal(cls, region_id):
        # TODO:
        return cls.query.filter_by(active=True).first()

    @as_future
    def heartbeat(self, report: Union[HeartbeatReport, RequestError]):
        if isinstance(report, RequestError):
            self.status = 'failed'
            self.last_failure_tb = traceback.format_tb(report.__traceback__)
        elif isinstance(report, HeartbeatReport):
            self.last_heartbeat = timezone.now()
            self.cpu_load = report.cpu_load
            self.ram_usage = report.ram_usage
            self.status = 'overload' if report.server_is_overload() else 'active'
        else:
            raise ValueError('`report` argument should be either instance of'
                             'HeartbeatReport or RequestError')
        self.save()


class Deployment(db.Model):
    __tablename__ = 'deployment'

    id = db.Column(db.Integer, primary_key=True)
    app_version_id = db.Column(db.Integer, db.ForeignKey('application_versions.id'))
    file = db.Column(db.FileType(upload_to='deployments'), nullable=False)


class Party(db.Model):
    __tablename__ = 'parties'

    class Statuses(enum.Enum):
        CREATED = 1
        STARTING = 2
        STARTED = 3

    id = db.Column(db.Integer, primary_key=True)
    max_members_count = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.Enum(Statuses), default=Statuses.CREATED)
    settings = db.Column(JSONType, nullable=False, default={})

    async def create_session(self, user_id: str, role=None, settings=None) -> 'PartySession':
        if self.members_count >= self.max_members_count:
            raise PlayersLimitPerPartyExceeded

        kwargs = {
            'user_id': user_id,
            'party_id': self.id,
            'role': role or PartySession.Roles.USER,
            'settings': settings or {},
        }
        session = await future_exec(PartySession.create, **kwargs)
        # TODO: all party members want to know that

        return session

    join_party = create_session

    @as_future
    def set_status(self, status):
        self.status = status
        self.save()

    async def start(self, member: 'PartySession'):
        await self.set_status(self.Statuses.STARTING)
        # TODO: start server by member
        await self.set_status(self.Statuses.STARTED)

    async def __start_server__(self, member: 'PartySession'):
        # TODO: spawn and join server
        pass

    async def __spawn_server__(self, member: 'PartySession'):
        # TODO: spawn server
        pass

    async def join_server(self, member: 'PartySession'):
        # TODO: join server
        # TODO: check if member can join
        pass

    async def close(self) -> None:
        pass

    @hybrid_property
    def members(self):
        return self.sessions

    @hybrid_property
    def members_count(self) -> int:
        return len(self.members.all)

    @classmethod
    async def create_party(cls, **kwargs) -> 'Party':
        return await future_exec(cls.create, **kwargs)


def _check_permission(perm: 'PartySession.Permissions'):
    def decorator(f):
        @wraps(f)
        async def wrapper(session, *args, **kwargs):
            if not session.has_permission(perm):
                raise PartySessionPermissionError
            return await f(session, *args, **kwargs)
        return wrapper
    return decorator


class PartySession(InternalAPIMixin, db.Model):
    __tablename__ = 'party_sessions'

    class Roles(enum.Enum):
        ADMIN = 1000
        USER = 0

    class Permissions(enum.Enum):
        ADMIN = 1000
        CAN_CLOSE = ADMIN
        CAN_START = 500
        USER = 0

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)
    party_id = db.Column(db.Integer, db.ForeignKey('parties.id'))
    party = db.relationship('Party', backref=db.backref('sessions', lazy='dynamic'))
    role = db.Column(db.Enum(Roles), default=Roles.USER)
    settings = db.Column(JSONType, nullable=False, default={})
    app_version_id = db.Column(db.Integer, db.ForeignKey('application_versions.id'))
    app_version = db.relationship(
        'ApplicationVersion', backref=db.backref('sessions', lazy='dynamic'))

    def has_permission(self, perm: Permissions) -> bool:
        return self.role.value >= perm.value

    @property
    def request_user(self):
        return partial(self.internal_request, 'login', 'get_user')

    async def get_user(self) -> RemoteUser:
        data = await self.request_user(user_id=self.user_id)
        return RemoteUser(**data)

    @hybrid_property
    def members(self):
        return self.party.members

    @_check_permission(Permissions.CAN_START)
    async def start_party(self) -> None:
        await self.party.start(self)

    async def join_server(self) -> None:
        await self.party.join_server(self)

    async def close(self, code=None, reason=None) -> None:
        # TODO: all party members want to know that
        await future_exec(self.delete)

    leave_party = close
