from anthill.framework.utils.asynchronous import as_future, thread_pool_exec as future_exec
from anthill.framework.auth.handlers import UserHandlerMixin
from anthill.platform.handlers.jsonrpc import JsonRPCSessionHandler, jsonrpc_method
from game_master.models import Party


class BasePartySessionHandler(UserHandlerMixin, JsonRPCSessionHandler):
    def __init__(self, application, request, **kwargs):
        super().__init__(self, application, request, **kwargs)
        self.session = None

    def check_origin(self, origin):
        return True

    async def prepare(self):
        await super().prepare()

    async def open(self, *args, **kwargs):
        await super().open(*args, **kwargs)
        party = await Party.create_party()  # TODO:
        self.session = await party.create_session()  # TODO:

    async def close(self, code=None, reason=None):
        await self.session.close(code, reason)
        await super().close(code, reason)

    @jsonrpc_method()
    async def update_party(self):
        pass

    @jsonrpc_method()
    async def close_party(self):
        pass

    @jsonrpc_method()
    async def join_party(self):
        pass

    @jsonrpc_method()
    async def leave_party(self):
        pass

    @jsonrpc_method()
    async def start_game(self):
        pass

    @jsonrpc_method()
    async def send_message(self, payload):
        pass


class PartySessionHandler(BasePartySessionHandler):
    pass


class PartiesSearchHandler(BasePartySessionHandler):
    pass


class CreatePartySessionHandler(BasePartySessionHandler):
    pass
