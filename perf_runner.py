"""
Replays the sample game concurrently from multiple processes to measure the
server's ability to scale
"""

from datetime import datetime, timedelta
from containers import (
    ActionResponseContainer,
    JoinGameResponseContainer,
    NewGameResponseContainer,
)
from typing import Dict, List
from messages import IncomingMessageType, OutgoingMessage, OutgoingMessageType
import pickle
from game import Action, Game, Color
from tornado.websocket import WebSocketClientConnection, websocket_connect
from tornado.options import define, options
import json
from constants import ACTION_TYPE, COORDS, KEY, TYPE, VS, COLOR, SIZE, KOMI
import asyncio
import multiprocessing as mp
import numpy as np


define("host", default="localhost", help="connect to the given host address", type=str)
define("port", default=8888, help="connect on the given port", type=int)
define(
    "num_processes",
    default=mp.cpu_count(),
    help="run in the given number of distinct processes",
    type=int,
)
define(
    "workers_per_process",
    default=10,
    help="spawn the given number of workers per process",
    type=int,
)

options.parse_command_line()

SERVER_URL = f"ws://{options.host}:{options.port}/websocket"

with open("sample_game.bin", "rb") as reader:
    sample_game: Game = pickle.load(reader)


def many_processes(num_processes: int, workers_per_process: int) -> List[List[float]]:
    with mp.Pool(num_processes) as pool:
        return pool.map(play_many, [workers_per_process for _ in range(num_processes)])


def play_many(num_workers: int) -> List[float]:
    """
    Spawn `num_workers` async tasks to play the sample game once each
    """

    async def tasks() -> List[float]:
        return await asyncio.gather(*[play_once() for _ in range(num_workers)])

    return asyncio.run(tasks())


async def play_once() -> float:
    """
    Play the sample game once through in a single thread and return the total
    time taken
    """

    start_time = datetime.now()

    # our first task is to open two connections, create a new game with one, and
    # join that game with the other

    black: WebSocketClientConnection = await websocket_connect(SERVER_URL)
    await black.write_message(
        json.dumps(
            {
                TYPE: IncomingMessageType.new_game.name,
                VS: "human",
                COLOR: Color.black.name,
                SIZE: sample_game.board.size,
                KOMI: sample_game.komi,
            }
        )
    )
    response: OutgoingMessage = OutgoingMessage.deserialize(await black.read_message())
    assert response.message_type is OutgoingMessageType.new_game_response
    data: NewGameResponseContainer = response.data
    assert data.success
    keys: Dict[Color, str] = data.keys

    white: WebSocketClientConnection = await websocket_connect(SERVER_URL)
    await white.write_message(
        json.dumps({TYPE: IncomingMessageType.join_game.name, KEY: keys[Color.white]})
    )
    response = OutgoingMessage.deserialize(await white.read_message())
    assert response.message_type is OutgoingMessageType.join_game_response
    data: JoinGameResponseContainer = response.data
    assert data.success

    # before proceeding, drain the message queues. we expect black to have four
    # (new game status messages + opp conn'd from after white joined), and white
    # to have three (join game status messages)

    for _ in range(4):
        await black.read_message()
    for _ in range(3):
        await white.read_message()

    # now that the game is set up, start a consumer task for each player and
    # wait for both to finish

    black_consumer = asyncio.create_task(
        play_consumer(black, Color.black, keys[Color.black])
    )
    white_consumer = asyncio.create_task(
        play_consumer(white, Color.white, keys[Color.white])
    )
    await black_consumer
    await white_consumer

    # finally, close the connections and return the total time taken

    black.close()
    white.close()

    return datetime.now() - start_time


async def play_consumer(
    player: WebSocketClientConnection, player_color: Color, key: str
) -> None:
    """
    Create a server message consumer that operates by reading the sample game
    action stack from beginning to end. If the next action is taken by this
    player's color, take it and then wait for/verify the response, and in either
    case, wait for/verify the type of the attendant status update before moving
    onto the next action in the stack. Returns when the game has been played
    through to the end
    """

    response: OutgoingMessage
    for i in range(len(sample_game.action_stack)):
        action: Action = sample_game.action_stack[i]
        if action.color is player_color:
            await player.write_message(
                json.dumps(
                    {
                        TYPE: IncomingMessageType.game_action.name,
                        KEY: key,
                        ACTION_TYPE: action.action_type.name,
                        COORDS: action.coords,
                    }
                )
            )
            response = OutgoingMessage.deserialize(await player.read_message())
            assert response.message_type is OutgoingMessageType.game_action_response
            data: ActionResponseContainer = response.data
            assert data.success

        response = OutgoingMessage.deserialize(await player.read_message())
        assert response.message_type is OutgoingMessageType.game_status


print(
    f"Starting run against {options.host}:{options.port} in {options.num_processes}"
    f" process(es) with {options.workers_per_process} worker(s) per process."
)
print("This may take some time... ", end="")

res: np.ndarray = np.vectorize(timedelta.total_seconds)(
    np.array(
        many_processes(options.num_processes, options.workers_per_process)
    ).flatten()
)
print("Finished!\n")
# + 2 is for create new game and join game
num_actions = len(sample_game.action_stack) + 2
num_plays = len(res)
print(f"Actions per play: {num_actions}")
print(f"Total plays: {num_plays}")
print(f"Total actions: {num_actions*num_plays}")
print(f"Min game time: {np.min(res):.04}s")
print(f"Max: {np.max(res):.04}s")
print(f"Std: {np.std(res):.04}s")
print(f"Mean: {np.mean(res):.04}s")
print(f"Median: {np.median(res):.04}s")
print(f"Mean action time: {np.mean(res)/num_actions:.04}s")
