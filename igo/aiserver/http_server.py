"""
Run a simple http server with a single `start` endpoint. This is the main
entrypoint, so a short discussion of architecture follows:

The game server makes three special allowances for AI players:
1. Prevents human players from using the AI's key via an additional secret AI key
   recorded in the database
2. Allows AI players to join games using their player + AI keys
3. Informs this server via the `start` endpoint when the player opposite the AI
   key joins the game

In every other respect, the AI server looks to the game server like a normal
user. It connects as a client via a websocket, receives game updates, takes
actions when allowed to do so, and disconnects when the game ends or it receives
`opponent_connected == False`.

In addition to interacting with the game server, the AI server also maintains a
DB where it stores a copy of any data used by play policies, written as a blob
on update. This data, if it exists, is also read in when the server receives the
`start` signal in case the corresponding human player disconnects and then
reconnects later to resume play.

Play policies (in `igo.aiserver.policy`) follow an `ABC` interface and are
therefore pluggable.
"""

import asyncio
from .websocket_client import Client
import tornado.web
import tornado.ioloop
from tornado.options import define, options
from secrets import token_urlsafe
import uvloop
import logging

# NOTE: tornado configures logging and provides some command line options by
# default.  See --help for details
define("port", default=1918, help="run on the given port", type=int)


class AIServer(tornado.web.RequestHandler):
    def get(self):
        """
        Sets and transmits the XSRF token in a cookie. See `post` for details
        """

        self.xsrf_token

    async def post(self):
        """
        Note that POST requests must have been preceeded by a GET in order to
        set the XSRF token on the server and consume it on the client as a
        cookie. POSTs thereafter must specify the token in their request as well
        as in their cookie. The security of this method relies on the fact that,
        due to the browser security policy, a cross-site attacker should not be
        able to access cookies from a different domain, and therefore should not
        be able to read the correct token from the cookie.

        The preferred way of transmitting this information is via headers,
        namely X-XsrfToken: token and Cookie: _xsrf=token.
        """

        try:
            player_key: str = self.get_argument("player_key")
            ai_secret: str = self.get_argument("ai_secret")
            asyncio.create_task((await Client(player_key, ai_secret)).start())
        except tornado.web.MissingArgumentError:
            err = "One or more required arguments was missing from the request"
            logging.warning(f"{err}. Ignoring")
            self.set_status(400, err)


class Application(tornado.web.Application):
    def __init__(self):
        handlers = [(r"/start", AIServer)]
        settings = dict(
            cookie_secret=token_urlsafe(),
            xsrf_cookies=True,
        )
        super().__init__(handlers, **settings)


def start_server():
    uvloop.install()
    options.parse_command_line()
    app = Application()
    app.listen(options.port)
    logging.info(f"Listening on port {options.port}")
    tornado.ioloop.IOLoop.current().start()
