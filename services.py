from anthill.platform.services import PlainService, MasterRole
from anthill.framework.utils.asynchronous import as_future
from anthill.framework.core.cache import caches


class Service(MasterRole, PlainService):
    """Anthill default service."""

    @as_future
    def storage(self):
        return caches['controllers']

    async def heartbeat_callback(self, controller, report):
        pass

    async def controllers_registry(self):
        # TODO: get all controllers from database
        pass
