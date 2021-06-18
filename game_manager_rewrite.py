"""
Temporary file to hold rewrite of game_manager. Once fleshed out, overwrite
game_manager and remove this file
"""

from game_manager import NewGameResponseContainer
from constants import COLOR, KOMI, SIZE
import logging
from chat import ChatThread
from dataclasses import dataclass
from game import Color, Game
from typing import Callable, Coroutine, Dict, Optional
from tornado.websocket import WebSocketHandler
from messages import (
    IncomingMessage,
    IncomingMessageType,
    JsonifyableBase,
    OutgoingMessageType,
    send_outgoing_message,
)
import asyncinit
from db_manager import DbManager


@dataclass
class ClientData:
    """
    ClientData is a container for all of the various data that a single client
    is concerned with.

    Attributes:

        key: str - the client's player key

        game: Game - the current game

        chat_thread: ChatThread - the chat thread associated with the current game

        opponent_connected: bool - whether or not the client's opponent in the
        current game is connected to a game server
    """

    key: str
    game: Game
    chat_thread: ChatThread
    opponent_connected: bool


@dataclass
class OpponentConnectedContainer(JsonifyableBase):
    """
    Simple container for opponent's connectedness indicator which implements jsonifyable
    """

    opponent_connected: bool

    def jsonifyable(self) -> Dict:
        return {"opponentConnected": self.opponent_connected}


@asyncinit
class GameStore:
    """
    GameStore is the guts of the in-memory storage and management of games. It
    maps connected clients, identified by their web socket handler, one-to-one
    to all of the data they are concerned with, routes messages, and issues
    responses on the client socket. Although it is possible and indeed likely
    that two clients playing the same game will be connected to the same game
    server, no attempt is made to share data between them above the database
    level.
    """

    async def __init__(self, store_dsn: str) -> None:
        self._clients: Dict[WebSocketHandler, ClientData] = {}
        self._keys: Dict[str, WebSocketHandler] = {}
        self._db_manager: DbManager = await DbManager(
            self._get_game_updater,
            self._get_chat_updater,
            self._get_opponent_connected_updater,
            store_dsn,
        )

    async def new_game(self, msg: IncomingMessage) -> None:
        """
        Create and write out a new game and then respond appropriately
        """

        if msg.websocket_handler in self._clients:
            old_key = self._clients[msg.websocket_handler].key
            logging.info(f"Client requesting new game already subscribed to {old_key}")
            await self.unsubscribe(msg.websocket_handler)

        game = Game(msg.data[SIZE], msg.data[KOMI])
        requested_color = Color[msg.data[COLOR]]
        keys: Dict[Color, str] = await self._db_manager.write_new_game(
            game, requested_color
        )
        client_key = keys[requested_color]
        self._clients[msg.websocket_handler] = ClientData(
            client_key, game, ChatThread()
        )
        self._keys[client_key] = msg.websocket_handler

        # TODO: If msg.data[VS] is "computer", set up computer as second player

        await send_outgoing_message(
            OutgoingMessageType.new_game_response,
            NewGameResponseContainer(
                True,
                (
                    f"Successfully created new game. Make sure to give the"
                    f" {requested_color.inverse().name} player key"
                    f" ({keys[requested_color.inverse()]}) to your opponent so that"
                    f" they can join the game. Your key is {keys[requested_color]}."
                    f" Make sure to write it down in case you want to pause the game"
                    f" and resume it later, or if you want to view it once complete"
                ),
                keys,
                requested_color,
            ),
            msg.websocket_handler,
        )

    def _get_game_updater(self) -> Callable[[str, Game], Coroutine]:
        """
        Return a function which takes a player key string and a Game object,
        updates the in-memory store, and alerts the client of the change. May be
        readily used to generate a subscription callback
        """

        async def callback(player_key: str, game: Game) -> None:
            client = self._updater_callback_preamble(player_key)
            if client:
                self._clients[client].game = game
                logging.info(f"Successfully updated game for player key {player_key}")

                await send_outgoing_message(
                    OutgoingMessageType.game_status, game, client
                )

        return callback

    def _get_chat_updater(self) -> Callable[[str, ChatThread], Coroutine]:
        """
        Return a function which takes a player key string and a ChatThread
        object, updates the in-memory store, and alerts the client of the
        change. Maybe be readily used to generate a subscription callback
        """

        async def callback(player_key: str, thread: ChatThread) -> None:
            client = self._updater_callback_preamble(player_key)
            if client:
                self._clients[client].chat_thread.extend(thread)
                logging.info(
                    f"Successfully updated chat thread for player key {player_key}"
                )

                await send_outgoing_message(OutgoingMessageType.chat, thread, client)

        return callback

    def _get_opponent_connected_updater(self) -> Callable[[str, bool], Coroutine]:
        """
        Return a function which takes a player key string and a bool, updates
        the in-memory store, and alerts the client of the change. Maybe be
        readily used to generate a subscription callback
        """

        async def callback(player_key: str, opponent_connected: bool) -> None:
            client = self._updater_callback_preamble(player_key)
            if client:
                self._clients[client].opponent_connected = opponent_connected
                logging.info(
                    "Successfully updated opponent connected status to"
                    f" {opponent_connected} for player key {player_key}"
                )

                await send_outgoing_message(
                    OutgoingMessageType.opponent_connected,
                    OpponentConnectedContainer(opponent_connected),
                    client,
                )

        return callback

    def _updater_callback_preamble(self, player_key: str) -> Optional[WebSocketHandler]:
        """
        All updater callbacks begin by doing the same couple things. Rather than
        copy-pasting, call this preamble instead
        """

        if player_key not in self._keys:
            logging.warn(f"Player key {player_key} is not being managed by this store")
            return None

        return self._keys[player_key]

    async def join_game(self, msg: IncomingMessage) -> None:
        pass

    async def route_message(self, msg: IncomingMessage) -> None:
        pass

    async def unsubscribe(self, socket: WebSocketHandler) -> None:
        pass


class GameManager:
    """
    GameManager is the simplified Game API to the connection_manager module.
    Its only responsibilites are routing messages to the underlying store

    Attributes:

        store: GameStore - the game store
    """

    async def __init__(self, store_dsn: str) -> None:
        """
        Arguments:

            store_dsn: str - the data source name url of the store database
        """

        self.store: GameStore = await GameStore(store_dsn)

    async def unsubscribe(self, socket: WebSocketHandler) -> None:
        """Unsubscribe the socket from its key if it is subscribed, otherwise
        do nothing"""

        await self.store.unsubscribe(socket)

    async def route_message(self, msg: IncomingMessage) -> None:
        """Route the message to the correct method on the underlying store"""

        if msg.message_type is IncomingMessageType.new_game:
            await self.store.new_game(msg)
        elif msg.message_type is IncomingMessageType.join_game:
            await self.store.join_game(msg)
        elif msg.message_type in (
            IncomingMessageType.game_action,
            IncomingMessageType.chat_message,
        ):
            await self.store.route_message(msg)
        else:
            raise TypeError(
                f"Unknown incoming message type {msg.message_type} encountered"
            )