# For more details, see
# http://docs.sqlalchemy.org/en/latest/orm/tutorial.html#declare-a-mapping
from anthill.framework.db import db
from anthill.framework.utils import timezone
from anthill.framework.utils.asynchronous import as_future, thread_pool_exec as future_exec
from anthill.framework.utils.translation import translate_lazy as _
from anthill.platform.models import BaseApplication, BaseApplicationVersion
from anthill.platform.api.internal import InternalAPIMixin, RequestError
from anthill.platform.auth import RemoteUser
from anthill.platform.services import HeartbeatReport
from sqlalchemy_utils.types import URLType, ChoiceType, JSONType
from sqlalchemy.ext.hybrid import hybrid_property
from geoalchemy2.elements import WKTElement
from geoalchemy2 import Geometry
from typing import Union
import geoalchemy2.functions as func
import traceback
import json


class PlayersLimitPerRoomExceeded(Exception):
    pass


class UserBannedError(Exception):
    pass


class Application(BaseApplication):
    __tablename__ = 'applications'


class ApplicationVersion(BaseApplicationVersion):
    __tablename__ = 'application_versions'

    rooms = db.relationship('Room', backref='app_version', lazy='dynamic')


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


class Player(InternalAPIMixin, db.Model):
    __tablename__ = 'players'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)
    room_id = db.Column(db.Integer, db.ForeignKey('rooms.id'))
    payload = db.Column(JSONType, nullable=False, default={})

    async def get_user(self) -> RemoteUser:
        data = await self.internal_request('login', 'get_user', user_id=self.user_id)
        return RemoteUser(**data)


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
    def get_nearest(cls, lat, lon):
        """
        Find the nearest point to the input coordinates.
        Convert the input coordinates to a WKT point and query for nearest point.
        """
        pt = WKTElement('POINT({0} {1})'.format(lon, lat), srid=4326)
        return cls.query.order_by(cls.point.distance_box(pt)).first()

    @staticmethod
    def from_point_to_xy(pt):
        """Extract x and y coordinates from a point geometry."""
        # noinspection PyUnresolvedReferences
        point_json = json.loads(db.session.scalar(func.ST_AsGeoJSON(pt.point)))
        return point_json['coordinates']


class Server(InternalAPIMixin, db.Model):
    __tablename__ = 'servers'

    STATUSES = [
        ('active', _('Active')),
        ('failed', _('Failed')),
        ('overload', _('Overload')),
    ]

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True)
    location = db.Column(URLType, nullable=False, unique=True)
    geo_location_id = db.Column(db.Integer, db.ForeignKey('geo_locations.id'))
    last_heartbeat = db.Column(db.DateTime)
    status = db.Column(ChoiceType(STATUSES))
    last_failure_tb = db.Column(db.Text)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    rooms = db.relationship('Room', backref='server', lazy='dynamic')
    system_load = db.Column(db.Float, nullable=False, default=0.0)
    ram_usage = db.Column(db.Float, nullable=False, default=0.0)

    @hybrid_property
    def active(self):
        return self.enabled and self.status == 'active'

    @as_future
    def heartbeat(self, report: Union[HeartbeatReport, RequestError]):
        if isinstance(report, RequestError):
            self.status = 'failed'
            self.last_failure_tb = traceback.format_tb(report.__traceback__)
        elif isinstance(report, HeartbeatReport):
            self.last_heartbeat = timezone.now()
            self.system_load = report.system_load
            self.ram_usage = report.ram_usage
            self.status = 'overload' if report.server_is_overload() else 'active'
        else:
            raise ValueError('`report` argument should be either instance of'
                             'HeartbeatReport or RequestError class')
        self.save()


class Deployment(db.Model):
    __tablename__ = 'deployment'

    id = db.Column(db.Integer, primary_key=True)


class Party(db.Model):
    __tablename__ = 'parties'

    id = db.Column(db.Integer, primary_key=True)
