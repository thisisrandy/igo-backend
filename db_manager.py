from enum import Enum, auto
from constants import KEY_LEN
from game import ChatMessage, Color, Game
from typing import Dict, Optional, Tuple
from asyncinit import asyncinit
import asyncpg
from uuid import uuid4
from hashlib import sha256
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


@asyncinit
class DbManager:
    async def __init__(self, dsn: str = "postgres://randy@localhost/randy") -> None:
        """
        Interface to the postgres database store. Responsibilities include:

        - On start up, cleaning the player key table in case of reboot while
          managing any connections
        - Handling new game creation
        - Handling joining a connected player to an existing game
        - Subscribing to game and chat update channels and registering callbacks for
          each
        - Issuing game updates to the database and reporting success or failure
        - Issuing chat messages to the database
        - Unsubscribing from update channels and cleaning up
        """

        # TODO: we probably want to use a connection pool instead of a single
        # connection. look into best practices
        self._connection: asyncpg.connection.Connection = await asyncpg.connect(dsn)

        # machine-id is a reboot persistent unique identifier that should not be
        # shared externally. the following mimics sd_id128_get_machine_app_specific()
        with open("/etc/machine-id", "rb") as r:
            self._machine_id = sha256(r.readline().strip()).hexdigest()

        # if we get restarted while a client is connected to a game, the
        # database will still reflect that the client is connected and that we
        # are managing their connection. it is the responsibility of each game
        # server to clean up after itself on restart
        #
        # NOTE: this logic breaks down if a game server never restarts or is
        # replaced by another machine, meaning that a game key can become
        # orphaned without manual intervention. this could be commonplace in
        # certain environments, where some external janitorial watcher would
        # need to be present. for now, we are assuming that the worst that
        # happens to any game server is an unexpected restart
        await self._connection.execute(
            """
            UPDATE player_key
            SET connected = false, managed_by = null
            WHERE managed_by = $1;
            """,
            self._machine_id,
        )

    async def write_new_game(
        self,
        game: Game,
        player_color: Color = None,
    ) -> Tuple[bool, Optional[Dict[Color, str]]]:
        """
        Attempt to write `game` to the database as a new game. Return a tuple of
        success or failure (on key conflict) and a dictionary of Color: key
        pairs on success or None otherwise. Optionally specify `player_color` to
        start managing that color
        """

        key_w, key_b = [uuid4().hex[-KEY_LEN:] for _ in range(2)]
        keys = {Color.white: key_w, Color.black: key_b}

        try:
            await self._connection.execute(
                """
                CALL new_game($1, $2, $3, $4, $5)
                """,
                pickle.dumps(game),
                key_w,
                key_b,
                player_color.name,
                self._machine_id,
            )

        except Exception as e:
            logging.error(
                f"Encountered exception while attempting to write new game: {e}"
            )
            return False, None

        else:
            logging.info(f"Successfully wrote new game with keys {keys} to database")
            return True, keys

        # TODO: subscribe to game updates/chat if player color specified

    async def join_game(self, player_key: str) -> Optional[JoinResult]:
        """
        Attempt to join a game using `player_key` and return the result of the
        operation or None if an exception occurs
        """

        try:
            async with self._connection.transaction():
                res = await self._connection.fetchrow(
                    """
                    SELECT * from join_game($1, $2);
                    """,
                    player_key,
                    self._machine_id,
                )

        except Exception as e:
            logging.error(
                "Encountered exception while attempting to join game with key"
                f" {player_key}: {e}"
            )
            return None

        else:
            logging.info(f"Attempt to join game with key {player_key} returned '{res}'")
            return JoinResult[res]

        # TODO: subscribe to game updates/chat if player color specified

    async def _subscribe_to_game_status(self, player_key: str) -> None:
        """
        Subscribe to the game status channel corresponding to `player_key` and
        register a callback to receive game status updates. Should be called
        only after successfully creating or joining a game
        """

        pass

    async def _subcribe_to_chat_feed(self, game_id: int) -> None:
        """
        Subscribe to the chat feed channel corresponding to `game_id` and
        register a callback to receive chat feed updates. Should be called only
        after successfully creating or joining a game
        """

        pass

    async def write_game(self, player_key: str, game: Game) -> bool:
        """
        Attempt to write `game` and increment its version in the database.
        Return True on success and False on failure, i.e. when the write has
        been preempted from another source
        """

        version = game.version()
        log_text = f"game for player key {player_key} to version {version}"

        try:
            async with self._connection.transaction():
                res = await self._connection.fetchval(
                    """
                    SELECT * from write_game($1, $2, $3)
                    """,
                    player_key,
                    pickle.dumps(game),
                    version,
                )

            if res:
                logging.info(f"Successfully updated {log_text}")
            else:
                logging.info(f"Preempted attempting to update {log_text}")
            return res

        except Exception as e:
            logging.error(f"Encountered exception attempting to update {log_text}: {e}")
            return False

    async def write_chat(self, player_key: str, message: ChatMessage) -> bool:
        """
        Attempt to write `message` to the database. Return True on success and
        False otherwise (unspecified network or database failure)
        """

        try:
            async with self._connection.transaction():
                await self._connection.execute(
                    """
                    INSERT INTO chat (timestamp, color, message, game_id)
                    VALUES ($1, $2, $3, (
                        SELECT game_id
                        FROM player_key
                        WHERE key = $4
                    ));

                    SELECT pg_notify((SELECT CONCAT('chat_', $4)), '');
                    SELECT pg_notify((
                        SELECT CONCAT('chat_', opponent_key)
                        FROM player_key
                        WHERE key = $4
                    ), '');
                    """,
                    message.timestamp,
                    message.color.name,
                    message.message,
                    player_key,
                )

            logging.info(f"Successfully wrote chat message {message}")
            return True

        except Exception as e:
            logging.error(
                f"Encountered exception while attempting to write chat message {message}: {e}"
            )
            return False

    async def unsubscribe(self, player_key: str) -> bool:
        """
        Attempt to unsubscribe from channels associated with `player_key` and
        modify the row in the `player_key` table appropriately. Return True on
        success and False otherwise
        """

        try:
            async with self._connection.transaction():
                await self._connection.execute(
                    f"""
                    UPDATE player_key
                    SET connected = false, managed_by = null
                    WHERE key = $1;

                    UNLISTEN game_status_{player_key};
                    UNLISTEN chat_{player_key};
                    """,
                    player_key,
                )

            logging.info(f"Successfully unsubscribed player key {player_key}")
            return True

        except Exception as e:
            logging.error(
                f"Encountered exception while unsubscribing player key {player_key}: {e}"
            )
            return False
