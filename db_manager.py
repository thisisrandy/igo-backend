import string
from collections import defaultdict
from enum import Enum, auto
from constants import KEY_LEN
from game import Color, Game
from chat import ChatMessage, ChatThread
from typing import Callable, Coroutine, DefaultDict, Dict, List, Tuple
from asyncinit import asyncinit
import asyncpg
from uuid import uuid4
from hashlib import sha256
import asyncio
import pickle
import logging

# TODO: handle database restarts.
# https://github.com/MagicStack/asyncpg/issues/421 seems to indicate that
# listeners aren't automatically reconnected


class JoinResult(Enum):
    """
    dne - the player key requested does not exist
    in_use - someone was already connected to the requested player key
    success - successfully joined using the requested player key
    """

    dne = auto()
    in_use = auto()
    success = auto()


class _UpdateType(Enum):
    game_status = auto()
    chat = auto()
    opponent_connected = auto()


ALPHANUM_CHARS = "".join(str(x) for x in range(10)) + string.ascii_letters


def alphanum_uuid(desired_length: int = KEY_LEN) -> str:
    """
    Produce a alpha-numeric uuid (each char in [0-9a-zA-Z], i.e. base 62) of
    `desired_length` using uuid4 under the hood. As uuid4 is 128 bits long,
    possible values range between 0 and 2**128 = 7N42dgm5tFLK9N8MT7fHC8 in base
    62, so `desired_length` must be <= 22 (and at least 1).

    In the context of game ids, choosing a large base means that we can have
    short, easy to type keys while still maintaining a practically zero
    collision rate. 62**10, for example, is nearly 1 quintillion, far beyond any
    practical number of games played at which we would see collisions, whereas
    if we used hexadecimal and the same key length, 16**10 is only slightly over
    1 trillion, meaning we would start seeing regular collisions after less than
    1 billion games. Obviously this is a hobby project and will never be played
    at that scale, but a real-world game could be, so this is a legitimate
    concern, unless we wanted to do something about handling collisions
    (currently, we assume it never happens and simply blow up if it does)
    """

    assert isinstance(desired_length, int) and 0 < desired_length <= 22

    base_10 = uuid4().int
    res = ""
    while base_10 and desired_length:
        res += ALPHANUM_CHARS[base_10 % 62]
        desired_length -= 1
        base_10 //= 62
    # the fact that this is backwards is immaterial in the context of generating
    # a unique id, so we choose not to reverse it
    return res


@asyncinit
class DbManager:
    __slots__ = (
        "_connection",
        "_machine_id",
        "_update_queue",
        "_listening_channels",
        "_game_status_callback",
        "_chat_callback",
        "_opponent_connected_callback",
    )

    async def __init__(
        self,
        game_status_callback: Callable[[str, Game], Coroutine],
        chat_callback: Callable[[str, ChatThread], Coroutine],
        opponent_connected_callback: Callable[[str, bool], Coroutine],
        dsn: str = "postgres://randy@localhost/randy",
        do_setup: bool = False,
    ) -> None:
        """
        Interface to the postgres database store. Responsibilities include:

        - On start up, cleaning the player key table in case of reboot while
          managing any connections
        - Handling new game creation
        - Handling joining a connected player to an existing game
        - Subscribing to and registering callbacks for various update channels
        - Issuing game updates to the database and reporting success or failure
        - Issuing chat messages to the database
        - Unsubscribing from update channels and cleaning up

        :param Callable[[str, Game], Coroutine] game_status_callback: an async
        callback to be invoked when a notification is received on a game status
        channel. must take a player key string and a Game object as arguments

        :param Callable[[str, ChatThread], Coroutine] chat_callback: an async
        callback to be invoked when a notification is received on a chat
        channel. must take a player key string and a ChatThread object as
        arguments

        :param Callable[[str, bool], Coroutine] opponent_connected_callback: an
        async callback to be invoked when a notification is received on an
        opponent connected channel. must take a player key string and a bool
        indicator of connectedness as arguments

        :param str dsn: data source name url

        :param bool do_setup: if True, run all table/index/function/etc.
        creation scripts as if using a fresh database. useful for testing
        """

        self._game_status_callback = game_status_callback
        self._chat_callback = chat_callback
        self._opponent_connected_callback = opponent_connected_callback

        # TODO: we probably want to use a connection pool instead of a single
        # connection. look into best practices
        self._connection: asyncpg.connection.Connection = await asyncpg.connect(dsn)

        # { player_key: [(channel, callback), ...], ...}
        # populate whenever adding listeners and lookup/delete record when
        # unlistening
        #
        # TODO: if multiple listener connections are used, the connection
        # associated with each channel will also need to be stored. this will
        # also be useful for handling reconnect logic (db restart, etc.)
        self._listening_channels: DefaultDict[
            str, List[Tuple[str, Callable]]
        ] = defaultdict(list)

        # machine-id is a reboot persistent unique identifier that should not be
        # shared externally. the following mimics sd_id128_get_machine_app_specific()
        with open("/etc/machine-id", "rb") as r:
            self._machine_id = sha256(r.readline().strip()).hexdigest()

        if do_setup:
            try:
                # NOTE: tables must be executed first. otherwise, it would be
                # sufficient to list the directory and execute each file
                for fn in ("tables", "indices", "views", "procedures", "functions"):
                    with open(f"./sql/{fn}.sql", "r") as r:
                        sql = r.read()
                    await self._connection.execute(sql)

            except Exception as e:
                raise Exception("Failed to run db setup scripts") from e

        # if we get restarted while a client is connected to a game, the
        # database will still reflect that we are managing their connection. it
        # is the responsibility of each game server to clean up after itself on
        # restart
        #
        # NOTE: this logic breaks down if a game server never restarts or is
        # replaced by another machine, meaning that a game key can become
        # orphaned without manual intervention. this could be commonplace in
        # certain environments, where some external janitorial watcher would
        # need to be present. for now, we are assuming that the worst that
        # happens to any game server is an unexpected restart
        try:
            await self._connection.execute(
                """
                CALL do_cleanup($1);
                """,
                self._machine_id,
            )

        except Exception as e:
            raise Exception("Failed to execute restart database cleanup") from e

        # set up the notifications queue and consumer
        self._update_queue = asyncio.Queue()
        asyncio.create_task(self._update_consumer())

    async def write_new_game(
        self,
        game: Game,
        player_color: Color = None,
    ) -> Dict[Color, str]:
        """
        Attempt to write `game` to the database as a new game. Return a
        dictionary of Color: key pairs on success or raise an Exception
        otherwise. Optionally specify `player_color` to start managing that
        color
        """

        key_w, key_b = [alphanum_uuid() for _ in range(2)]
        keys = {Color.white: key_w, Color.black: key_b}

        try:
            async with self._connection.transaction():
                await self._connection.execute(
                    """
                    CALL new_game($1, $2, $3, $4, $5);
                    """,
                    pickle.dumps(game),
                    key_w,
                    key_b,
                    player_color.name if player_color else None,
                    self._machine_id,
                )
                if player_color:
                    await self._subscribe_to_updates(keys[player_color])

        except Exception as e:
            raise Exception("Failed to write new game") from e

        else:
            logging.info(f"Successfully wrote new game with keys {keys} to database")
            return keys

    async def join_game(
        self, player_key: str
    ) -> Tuple[JoinResult, Optional[Dict[Color, str]]]:
        """
        Attempt to join a game using `player_key` and return the result of the
        operation or raise an Exception otherwise. If the result is
        `JoinResult.success`, also return a dictionary of { Color: player key,
        ... } for the joined game. Note that a successful call to this method
        should always be followed by `trigger_update_all`. They are separated in
        order to allow the caller to set up any necessary state to allow update
        callbacks to succeed
        """

        try:
            async with self._connection.transaction():
                res, key_w, key_b = await self._connection.fetchrow(
                    """
                    SELECT * from join_game($1, $2);
                    """,
                    player_key,
                    self._machine_id,
                )
                res = JoinResult[res]
                if res is JoinResult.success:
                    keys = {Color.white: key_w, Color.black: key_b}
                    await self._subscribe_to_updates(player_key)
                else:
                    keys = None

        except Exception as e:
            raise Exception(f"Failed to join game with key {player_key}") from e

        else:
            logging.info(
                f"Attempt to join game with key {player_key} returned '{res.name}'"
            )
            return res, keys

    async def trigger_update_all(self, player_key: str) -> None:
        try:
            async with self._connection.transaction():
                await self._connection.execute(
                    """
                    CALL trigger_update_all($1);
                    """,
                    player_key,
                )

        except Exception as e:
            raise Exception(
                f"Failed to trigger update all for player key {player_key}"
            ) from e

    async def _subscribe_to_updates(self, player_key: str) -> None:
        """
        Subscribe to the update channels corresponding to `player_key` and
        register callbacks to receive updates. Should be called only after
        successfully creating or joining a game
        """

        def listener_callback(update_type: _UpdateType):
            return lambda _, _1, _2, payload: self._update_queue.put_nowait(
                (update_type, player_key, payload)
            )

        try:
            for update_type in _UpdateType:
                channel = f"{update_type.name}_{player_key}"
                partial_callback = listener_callback(update_type)
                await self._connection.add_listener(channel, partial_callback)
                self._listening_channels[player_key].append((channel, partial_callback))

        except Exception as e:
            raise Exception(
                f"Failed to subscribe to status updates for player key {player_key}"
            ) from e

        else:
            logging.info(f"Successfully subscribed to status updates for {player_key}")

    async def _update_consumer(self) -> None:
        """
        The top-level consumer for queued updates. Routes to type-specific
        consumers
        """

        while True:
            update_type: _UpdateType
            player_key: str
            payload: str
            update_type, player_key, payload = await self._update_queue.get()

            if update_type is _UpdateType.game_status:
                await self._game_status_consumer(player_key)
            elif update_type is _UpdateType.chat:
                await self._chat_consumer(player_key)
            elif update_type is _UpdateType.opponent_connected:
                await self._opponent_connected_consumer(player_key, payload)
            else:
                logging.error(f"Found unknown update type {update_type} in queue")

            # NOTE: as we aren't attempting to join the queue in the current
            # design, this call doesn't really do anything useful. that said,
            # it's good practice, because it future-proofs us should we have a
            # reason to join the queue later on
            self._update_queue.task_done()

    async def _game_status_consumer(self, player_key: str) -> None:
        # TODO: stub. need to go to db for latest version and invoke callback
        # with the result
        print(f"In game status consumer with key {player_key}")

    async def _chat_consumer(self, player_key: str) -> None:
        # TODO: stub. need to go to db for updates since last known id and
        # invoke callback with the result
        print(f"In chat consumer with key {player_key}")

    async def _opponent_connected_consumer(self, player_key: str, payload: str) -> None:
        # TODO: stub. if payload is not empty, use it, otherwise go to db for
        # status, and then invoke callback with the result
        print(
            f"In opponent connected consumer with key {player_key} and payload {payload}"
        )

    async def write_game(self, player_key: str, game: Game) -> bool:
        """
        Attempt to write `game` and increment its version in the database.
        Return True on success and False on failure, i.e. when the write has
        been preempted from another source, or raise an Exception otherwise
        """

        version = game.version()
        log_text = f"game for player key {player_key} to version {version}"

        try:
            async with self._connection.transaction():
                res = await self._connection.fetchval(
                    """
                    SELECT * from write_game($1, $2, $3);
                    """,
                    player_key,
                    pickle.dumps(game),
                    version,
                )

        except Exception as e:
            raise Exception(f"Failed to update {log_text}") from e

        else:
            if res:
                logging.info(f"Successfully updated {log_text}")
            else:
                logging.info(f"Preempted attempting to update {log_text}")
            return res

    async def write_chat(self, player_key: str, message: ChatMessage) -> bool:
        """
        Attempt to write `message` to the database. Return True on success,
        False if a game associated with `player_key` could not be found, and
        raise an Exception otherwise
        """

        try:
            async with self._connection.transaction():
                res = await self._connection.fetchval(
                    """
                    SELECT * FROM write_chat($1, $2, $3);
                    """,
                    message.timestamp,
                    message.message,
                    player_key,
                )

        except Exception as e:
            raise Exception(f"Failed to write chat message '{message}'") from e

        else:
            if res:
                logging.info(f"Successfully wrote chat message {message}")
            else:
                logging.warn(
                    f"When attempting to write chat message {message} from player key"
                    f" {player_key}, a game associated with that key could not be found"
                )
            return res

    async def unsubscribe(self, player_key: str) -> bool:
        """
        Attempt to unsubscribe from channels associated with `player_key` and
        modify the row in the `player_key` table appropriately. Return True on
        success, False if the database shows that this server is not managing
        `player_key`, and raise an Exception otherwise
        """

        try:
            async with self._connection.transaction():
                res = await self._connection.fetchval(
                    """
                    SELECT * FROM unsubscribe($1, $2);
                    """,
                    player_key,
                    self._machine_id,
                )
                if res:
                    for channel, callback in self._listening_channels[player_key]:
                        await self._connection.remove_listener(channel, callback)
                    del self._listening_channels[player_key]

        except Exception as e:
            raise Exception(
                f"Failed to unsubscribe from player key {player_key}"
            ) from e

        else:
            if res:
                logging.info(f"Successfully unsubscribed player key {player_key}")
            else:
                logging.warn(
                    f"When unsubscribing from player key {player_key}, no record was"
                    " found of a connected player managed by this game server"
                )
            return res
