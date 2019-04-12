from anthill.platform.apps import BaseAnthillApplication
from geoalchemy2.types import Geometry, Geography
from marshmallow_sqlalchemy import convert
from marshmallow import fields
import logging

logger = logging.getLogger('anthill.application')


BaseModelConverter = BaseAnthillApplication.ModelConverter


class AnthillApplication(BaseAnthillApplication):
    """Anthill default application."""

    class ModelConverter(BaseModelConverter):
        """Anthill model converter for marshmallow model schema."""
        SQLA_TYPE_MAPPING = BaseModelConverter.SQLA_TYPE_MAPPING.copy()
        SQLA_TYPE_MAPPING.update({
            Geometry: fields.Str,
            Geography: fields.Str,
        })
